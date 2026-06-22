"""
Webhook endpoints for external service integrations.

This module handles webhooks from:
- Stripe: Subscription lifecycle events (created, updated, deleted, payment_failed)

SECURITY:
- Stripe webhooks verify signature using STRIPE_WEBHOOK_SECRET
- All webhooks are processed idempotently (duplicate events handled gracefully)
"""
import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from database import get_db
from models import User as DBUser, UserSubscription, SubscriptionTier
from rate_limiting import rate_limit_stripe_webhook
from schemas import StripeWebhookResponse
from services.subscription import get_tier_limits

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/webhooks",
    tags=["webhooks"]
)

# Stripe configuration
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Price ID to tier mapping
# These should match your Stripe product price IDs
PRICE_TIER_MAP = {
    # Monthly prices
    "price_professional_monthly": SubscriptionTier.PROFESSIONAL.value,
    "price_professional_yearly": SubscriptionTier.PROFESSIONAL.value,
    "price_enterprise_monthly": SubscriptionTier.ENTERPRISE.value,
    "price_enterprise_yearly": SubscriptionTier.ENTERPRISE.value,
}


def get_tier_from_price_id(price_id: str) -> str:
    """
    Map Stripe price ID to subscription tier.

    Args:
        price_id: Stripe price ID from subscription

    Returns:
        Subscription tier string ('free', 'professional', 'enterprise')
    """
    return PRICE_TIER_MAP.get(price_id, SubscriptionTier.FREE.value)


async def process_subscription_created(
    event_data: dict,
    db: AsyncSession
) -> dict:
    """
    Handle customer.subscription.created event.

    Creates or updates UserSubscription based on Stripe subscription data.
    """
    subscription = event_data.get("object", {})
    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")

    # Get price ID from subscription items
    items = subscription.get("items", {}).get("data", [])
    price_id = items[0].get("price", {}).get("id") if items else None
    tier = get_tier_from_price_id(price_id) if price_id else SubscriptionTier.FREE.value

    # Find user by Stripe customer ID
    # First check if we have a user with this customer_id already
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.stripe_customer_id == customer_id
        )
    )
    user_subscription = result.scalar_one_or_none()

    if user_subscription:
        # Update existing subscription
        tier_limits = get_tier_limits(tier)
        user_subscription.tier = tier
        user_subscription.max_organisations = tier_limits["max_organisations"]
        user_subscription.max_team_members = tier_limits["max_team_members"]
        user_subscription.stripe_subscription_id = subscription_id
        user_subscription.is_active = True
        await db.commit()

        logger.info(
            f"Updated subscription for customer {customer_id}: "
            f"tier={tier}, subscription_id={subscription_id}"
        )
        return {"status": "updated", "tier": tier}

    # No existing subscription - log for manual investigation
    # We can't auto-create because we need the user_id mapping
    logger.warning(
        f"Received subscription.created for unknown customer {customer_id}. "
        f"User may need manual subscription setup."
    )
    return {"status": "skipped", "reason": "unknown_customer"}


async def process_subscription_updated(
    event_data: dict,
    db: AsyncSession
) -> dict:
    """
    Handle customer.subscription.updated event.

    Updates tier and limits when subscription plan changes.
    Handles both upgrades and downgrades.
    """
    subscription = event_data.get("object", {})
    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")
    subscription_status = subscription.get("status")

    # Get price ID from subscription items
    items = subscription.get("items", {}).get("data", [])
    price_id = items[0].get("price", {}).get("id") if items else None
    tier = get_tier_from_price_id(price_id) if price_id else SubscriptionTier.FREE.value

    # Find subscription by Stripe subscription ID
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.stripe_subscription_id == subscription_id
        )
    )
    user_subscription = result.scalar_one_or_none()

    if not user_subscription:
        # Try finding by customer ID
        result = await db.execute(
            select(UserSubscription).where(
                UserSubscription.stripe_customer_id == customer_id
            )
        )
        user_subscription = result.scalar_one_or_none()

    if not user_subscription:
        logger.warning(
            f"Received subscription.updated for unknown subscription {subscription_id} "
            f"(customer: {customer_id})"
        )
        return {"status": "skipped", "reason": "unknown_subscription"}

    # Update subscription
    tier_limits = get_tier_limits(tier)
    old_tier = user_subscription.tier

    user_subscription.tier = tier
    user_subscription.max_organisations = tier_limits["max_organisations"]
    user_subscription.max_team_members = tier_limits["max_team_members"]
    user_subscription.stripe_subscription_id = subscription_id

    # Handle subscription status
    if subscription_status in ("active", "trialing"):
        user_subscription.is_active = True
    elif subscription_status in ("canceled", "unpaid", "incomplete_expired"):
        user_subscription.is_active = False

    await db.commit()

    action = "upgraded" if tier != old_tier and tier != SubscriptionTier.FREE.value else "updated"
    logger.info(
        f"Subscription {action} for customer {customer_id}: "
        f"{old_tier} -> {tier}, status={subscription_status}"
    )
    return {"status": action, "old_tier": old_tier, "new_tier": tier}


async def process_subscription_deleted(
    event_data: dict,
    db: AsyncSession
) -> dict:
    """
    Handle customer.subscription.deleted event.

    Downgrades user to free tier when subscription is cancelled.
    """
    subscription = event_data.get("object", {})
    customer_id = subscription.get("customer")
    subscription_id = subscription.get("id")

    # Find subscription by Stripe subscription ID
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.stripe_subscription_id == subscription_id
        )
    )
    user_subscription = result.scalar_one_or_none()

    if not user_subscription:
        logger.warning(
            f"Received subscription.deleted for unknown subscription {subscription_id}"
        )
        return {"status": "skipped", "reason": "unknown_subscription"}

    # Downgrade to free tier
    free_limits = get_tier_limits(SubscriptionTier.FREE.value)
    old_tier = user_subscription.tier

    user_subscription.tier = SubscriptionTier.FREE.value
    user_subscription.max_organisations = free_limits["max_organisations"]
    user_subscription.max_team_members = free_limits["max_team_members"]
    user_subscription.is_active = True  # Free tier is always "active"
    user_subscription.stripe_subscription_id = None  # Clear subscription ID

    await db.commit()

    logger.info(
        f"Subscription deleted for customer {customer_id}: "
        f"downgraded from {old_tier} to free"
    )
    return {"status": "downgraded", "old_tier": old_tier, "new_tier": "free"}


async def process_payment_failed(
    event_data: dict,
    db: AsyncSession
) -> dict:
    """
    Handle invoice.payment_failed event.

    Marks subscription as at-risk but doesn't immediately downgrade.
    The user should receive a notification to update payment method.
    """
    invoice = event_data.get("object", {})
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")

    if not subscription_id:
        # Not a subscription-related payment
        return {"status": "skipped", "reason": "not_subscription_payment"}

    # Find subscription
    result = await db.execute(
        select(UserSubscription).where(
            UserSubscription.stripe_subscription_id == subscription_id
        )
    )
    user_subscription = result.scalar_one_or_none()

    if not user_subscription:
        logger.warning(
            f"Payment failed for unknown subscription {subscription_id}"
        )
        return {"status": "skipped", "reason": "unknown_subscription"}

    # Log the payment failure - actual status change comes from subscription.updated
    logger.warning(
        f"Payment failed for customer {customer_id}, subscription {subscription_id}. "
        f"User tier: {user_subscription.tier}"
    )

    # TODO: Trigger notification to user about payment failure
    # This could be done via email, in-app notification, etc.

    return {"status": "logged", "tier": user_subscription.tier}


@router.post("/stripe", response_model=StripeWebhookResponse)
@rate_limit_stripe_webhook
async def stripe_webhook(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    """
    Handle Stripe webhook events.

    Supported events:
    - customer.subscription.created: New subscription created
    - customer.subscription.updated: Subscription plan changed
    - customer.subscription.deleted: Subscription cancelled
    - invoice.payment_failed: Payment failed (at-risk notification)

    Security:
    - Verifies Stripe webhook signature when STRIPE_WEBHOOK_SECRET is configured
    - Processes events idempotently (safe to receive duplicates)
    """
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    # Parse event (with mandatory signature verification)
    try:
        if not STRIPE_WEBHOOK_SECRET:
            logger.error("STRIPE_WEBHOOK_SECRET not configured — rejecting webhook")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook verification not configured"
            )
        if not sig_header:
            logger.error("Missing stripe-signature header — rejecting webhook")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing signature header"
            )
        import stripe
        # Council Review: tightened from default 300s (5 min) to 180s (3 min)
        # to reduce replay attack window per security checklist requirement.
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET,
            tolerance=180  # 3 minutes (Council Review: tightened from 5 min default)
        )
    except ValueError as e:
        logger.error(f"Invalid webhook payload: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload"
        )
    except Exception as e:
        logger.error(f"Webhook signature verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature"
        )

    event_type = event.get("type", "unknown")
    event_data = event.get("data", {})

    logger.info(f"Received Stripe webhook: {event_type}")

    # Route to appropriate handler
    result = {"processed": False, "message": "Event type not handled"}

    try:
        if event_type == "customer.subscription.created":
            result = await process_subscription_created(event_data, db)
            result["processed"] = True

        elif event_type == "customer.subscription.updated":
            result = await process_subscription_updated(event_data, db)
            result["processed"] = True

        elif event_type == "customer.subscription.deleted":
            result = await process_subscription_deleted(event_data, db)
            result["processed"] = True

        elif event_type == "invoice.payment_failed":
            result = await process_payment_failed(event_data, db)
            result["processed"] = True

        else:
            logger.debug(f"Ignoring unhandled event type: {event_type}")

    except Exception as e:
        logger.error(f"Error processing webhook {event_type}: {e}")
        # Return 200 to avoid Stripe retries for processing errors
        # The error is logged for investigation
        result = {"processed": False, "message": str(e)}

    return StripeWebhookResponse(
        received=True,
        event_type=event_type,
        processed=result.get("processed", False),
        message=result.get("message") or result.get("status")
    )
