"""
Email Service - Send email notifications using Resend API.

This service handles sending email notifications for:
- Task assignments
- Task due reminders
- Task overdue alerts
- @mentions in comments
"""
import html
import os
import logging
from typing import List, Optional
from datetime import date

logger = logging.getLogger(__name__)


def _mask_email(email: Optional[str]) -> str:
    """Mask a recipient email for logging (``j***@example.com``).

    Removes clear-text PII from logs (CodeQL ``py/clear-text-logging-sensitive-data``)
    while keeping enough signal for delivery debugging. Mirrors ``auth._mask_email`` —
    intentionally duplicated to avoid importing the auth module (and its import-time
    side effects) here; consolidate into a shared util when one exists.
    """
    if not email or "@" not in email:
        return "<unknown>"
    local, _, domain = email.partition("@")
    if not local:
        return f"***@{domain}"
    return f"{local[0]}***@{domain}"


# Resend configuration
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "notifications@odin-scf.app")
APP_URL = os.getenv("APP_URL", "http://localhost:5173")
# Marketing website URL for signup (website-first provisioning)
MARKETING_WEBSITE_URL = os.getenv("MARKETING_WEBSITE_URL", "https://scfcontrolsplatform.com")

# Check if Resend is configured
RESEND_ENABLED = bool(RESEND_API_KEY)

# Log email service configuration on startup
print("=" * 60)
print("📧 EMAIL SERVICE CONFIGURATION")
print("=" * 60)
print(f"   RESEND_API_KEY: {'SET' if RESEND_API_KEY else 'NOT SET'}")
print(f"   RESEND_FROM_EMAIL: {RESEND_FROM_EMAIL}")
print(f"   APP_URL: {APP_URL}")
print(f"   RESEND_ENABLED: {RESEND_ENABLED}")
print("=" * 60)

if RESEND_ENABLED:
    try:
        import resend
        resend.api_key = RESEND_API_KEY
        logger.info("✅ Resend email service initialized successfully")
        print("✅ Email service is ENABLED and ready to send emails")
    except ImportError:
        logger.error("❌ Resend package not installed. Run: pip install resend")
        print("❌ Resend package not installed!")
        RESEND_ENABLED = False
else:
    logger.warning("⚠️  RESEND_API_KEY not set - email notifications disabled")
    print("⚠️  Email service is DISABLED (RESEND_API_KEY not set)")
    print("   To enable: Add RESEND_API_KEY to your .env file")


async def send_assignment_notification_email(
    to_email: str,
    to_name: str,
    assignable_type: str,
    assignable_id: str,
    assigned_by_name: str
):
    """Send email when user is assigned to a control or evidence."""
    if not RESEND_ENABLED:
        logger.debug("Email notifications disabled - skipping assignment email")
        return None

    try:
        subject = f"You've been assigned to a {assignable_type}"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1976d2;">New Assignment</h2>
            <p>Hi {to_name},</p>
            <p><strong>{assigned_by_name}</strong> has assigned you to a {assignable_type} in CG SCF.</p>
            <p style="margin: 20px 0;">
                <a href="{APP_URL}"
                   style="background-color: #1976d2; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    View in CG SCF
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This is an automated notification from CG SCF Explorer.
            </p>
        </body>
        </html>
        """

        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "tags": [
                {"name": "type", "value": "assignment"},
                {"name": "assignable_type", "value": assignable_type}
            ]
        }

        email = resend.Emails.send(params)
        logger.info(f"✅ Assignment email sent to {_mask_email(to_email)}: {email['id']}")
        return email['id']

    except Exception as e:
        logger.error(f"❌ Failed to send assignment email: {e}")
        return None


async def send_task_due_notification_email(
    to_email: str,
    to_name: str,
    evidence_id: str,
    due_date: date,
    days_until_due: int
):
    """Send email when evidence collection task is due soon."""
    if not RESEND_ENABLED:
        logger.debug("Email notifications disabled - skipping task due email")
        return None

    try:
        if days_until_due == 0:
            subject = f"Evidence Collection Due Today: {evidence_id}"
            urgency = "today"
        elif days_until_due == 1:
            subject = f"Evidence Collection Due Tomorrow: {evidence_id}"
            urgency = "tomorrow"
        else:
            subject = f"Evidence Collection Due in {days_until_due} Days: {evidence_id}"
            urgency = f"in {days_until_due} days"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #f57c00;">Evidence Collection Reminder</h2>
            <p>Hi {to_name},</p>
            <p>This is a reminder that evidence collection is due <strong>{urgency}</strong>:</p>
            <div style="background-color: #fff3e0; padding: 15px; border-radius: 4px; margin: 20px 0;">
                <p style="margin: 0; font-size: 16px;"><strong>Evidence ID:</strong> {evidence_id}</p>
                <p style="margin: 5px 0 0 0; font-size: 14px; color: #666;">
                    <strong>Due Date:</strong> {due_date.strftime('%B %d, %Y')}
                </p>
            </div>
            <p style="margin: 20px 0;">
                <a href="{APP_URL}"
                   style="background-color: #f57c00; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    View Task Details
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This is an automated reminder from CG SCF Explorer.
            </p>
        </body>
        </html>
        """

        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "tags": [
                {"name": "type", "value": "task_due"},
                {"name": "evidence_id", "value": evidence_id}
            ]
        }

        email = resend.Emails.send(params)
        logger.info(f"✅ Task due email sent to {_mask_email(to_email)}: {email['id']}")
        return email['id']

    except Exception as e:
        logger.error(f"❌ Failed to send task due email: {e}")
        return None


async def send_task_overdue_notification_email(
    to_email: str,
    to_name: str,
    evidence_id: str,
    due_date: date,
    days_overdue: int
):
    """Send email when evidence collection task is overdue."""
    if not RESEND_ENABLED:
        logger.debug("Email notifications disabled - skipping overdue email")
        return None

    try:
        subject = f"⚠️ OVERDUE: Evidence Collection {evidence_id}"

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #d32f2f;">⚠️ Overdue Evidence Collection</h2>
            <p>Hi {to_name},</p>
            <p>The following evidence collection task is <strong style="color: #d32f2f;">overdue by {days_overdue} day(s)</strong>:</p>
            <div style="background-color: #ffebee; padding: 15px; border-radius: 4px; margin: 20px 0; border-left: 4px solid #d32f2f;">
                <p style="margin: 0; font-size: 16px;"><strong>Evidence ID:</strong> {evidence_id}</p>
                <p style="margin: 5px 0 0 0; font-size: 14px; color: #666;">
                    <strong>Was Due:</strong> {due_date.strftime('%B %d, %Y')}
                </p>
                <p style="margin: 5px 0 0 0; font-size: 14px; color: #d32f2f;">
                    <strong>Overdue:</strong> {days_overdue} day(s)
                </p>
            </div>
            <p>Please complete this task as soon as possible to maintain compliance.</p>
            <p style="margin: 20px 0;">
                <a href="{APP_URL}"
                   style="background-color: #d32f2f; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    Complete Task Now
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This is an automated alert from CG SCF Explorer.
            </p>
        </body>
        </html>
        """

        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "tags": [
                {"name": "type", "value": "task_overdue"},
                {"name": "evidence_id", "value": evidence_id},
                {"name": "days_overdue", "value": str(days_overdue)}
            ]
        }

        email = resend.Emails.send(params)
        logger.info(f"✅ Overdue email sent to {_mask_email(to_email)}: {email['id']}")
        return email['id']

    except Exception as e:
        logger.error(f"❌ Failed to send overdue email: {e}")
        return None


async def send_mention_notification_email(
    to_email: str,
    to_name: str,
    commenter_name: str,
    commentable_type: str,
    comment_preview: str
):
    """Send email when user is @mentioned in a comment."""
    if not RESEND_ENABLED:
        logger.debug("Email notifications disabled - skipping mention email")
        return None

    try:
        subject = f"{commenter_name} mentioned you in a comment"

        # Truncate comment preview
        preview = comment_preview[:200] + "..." if len(comment_preview) > 200 else comment_preview

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1976d2;">You Were Mentioned</h2>
            <p>Hi {to_name},</p>
            <p><strong>{commenter_name}</strong> mentioned you in a comment on a {commentable_type}:</p>
            <div style="background-color: #f5f5f5; padding: 15px; border-radius: 4px; margin: 20px 0;
                        border-left: 4px solid #1976d2; font-style: italic;">
                "{preview}"
            </div>
            <p style="margin: 20px 0;">
                <a href="{APP_URL}"
                   style="background-color: #1976d2; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    View Comment
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                This is an automated notification from CG SCF Explorer.
            </p>
        </body>
        </html>
        """

        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "tags": [
                {"name": "type", "value": "mention"},
                {"name": "commentable_type", "value": commentable_type}
            ]
        }

        email = resend.Emails.send(params)
        logger.info(f"✅ Mention email sent to {_mask_email(to_email)}: {email['id']}")
        return email['id']

    except Exception as e:
        logger.error(f"❌ Failed to send mention email: {e}")
        return None


async def send_daily_digest_email(
    to_email: str,
    to_name: str,
    notifications: List[dict]
):
    """Send daily digest of notifications."""
    if not RESEND_ENABLED:
        logger.debug("Email notifications disabled - skipping digest email")
        return None

    try:
        subject = f"Daily Digest - {len(notifications)} Notifications"

        # Build notification list HTML
        notification_items = ""
        for notif in notifications:
            notification_items += f"""
            <li style="margin-bottom: 15px; padding-bottom: 15px; border-bottom: 1px solid #eee;">
                <strong style="color: #1976d2;">{notif.get('type', 'Notification').replace('_', ' ').title()}</strong><br/>
                <span style="color: #333;">{notif.get('message', 'No message')}</span><br/>
                <span style="color: #999; font-size: 12px;">
                    {notif.get('created_at', 'Unknown time')}
                </span>
            </li>
            """

        html = f"""
        <html>
        <body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <h2 style="color: #1976d2;">Your Daily Digest</h2>
            <p>Hi {to_name},</p>
            <p>Here's a summary of your notifications from today:</p>
            <ul style="list-style: none; padding: 0;">
                {notification_items}
            </ul>
            <p style="margin: 20px 0;">
                <a href="{APP_URL}"
                   style="background-color: #1976d2; color: white; padding: 12px 24px;
                          text-decoration: none; border-radius: 4px; display: inline-block;">
                    View All in CG SCF
                </a>
            </p>
            <p style="color: #666; font-size: 14px;">
                You're receiving this because you have daily digest notifications enabled.<br/>
                Update your preferences in CG SCF settings.
            </p>
        </body>
        </html>
        """

        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html,
            "tags": [
                {"name": "type", "value": "daily_digest"},
                {"name": "notification_count", "value": str(len(notifications))}
            ]
        }

        email = resend.Emails.send(params)
        logger.info(f"✅ Daily digest sent to {_mask_email(to_email)}: {email['id']}")
        return email['id']

    except Exception as e:
        logger.error(f"❌ Failed to send daily digest: {e}")
        return None


async def send_batch_emails(emails: List[dict]):
    """Send multiple emails in a single batch (up to 100)."""
    if not RESEND_ENABLED:
        logger.debug("Email notifications disabled - skipping batch emails")
        return None

    try:
        result = resend.Batch.send(emails)

        if "data" in result:
            logger.info(f"✅ Batch sent: {len(result['data'])} emails")

        if "errors" in result and result["errors"]:
            for error in result["errors"]:
                logger.error(f"❌ Email {error['index']} failed: {error['message']}")

        return result

    except Exception as e:
        logger.error(f"❌ Failed to send batch emails: {e}")
        return None


async def send_invitation_email(
    to_email: str,
    organization_name: str,
    inviter_name: str,
    invite_token: str,
    custom_message: Optional[str] = None,
    invite_type: str = "consultant",
):
    """
    Send an invitation email to join the organization.

    Args:
        to_email: Email address to send invitation to
        organization_name: Name of the organization inviting the user
        inviter_name: Name of the person sending the invitation
        invite_token: The secure token for accepting the invitation
        custom_message: Optional custom message from the inviter

    Returns:
        Email ID if successful, None if email service is disabled or failed
    """
    logger.info(f"📧 INVITATION EMAIL REQUEST")
    logger.info(f"   To: {_mask_email(to_email)}")
    logger.info(f"   Organization: {organization_name}")
    logger.info(f"   Invited by: {inviter_name}")
    logger.info(f"   Invite token: {invite_token[:8]}...")
    logger.info(f"   Custom message: {'Yes' if custom_message else 'No'}")
    logger.info(f"   RESEND_ENABLED: {RESEND_ENABLED}")
    logger.info(f"   RESEND_API_KEY set: {'Yes' if RESEND_API_KEY else 'No'}")
    logger.info(f"   RESEND_FROM_EMAIL: {RESEND_FROM_EMAIL}")
    logger.info(f"   APP_URL: {APP_URL}")

    if not RESEND_ENABLED:
        logger.warning(f"⚠️  EMAIL SERVICE DISABLED - Invitation to {_mask_email(to_email)} will NOT be sent")
        logger.warning(f"   To enable: Set RESEND_API_KEY environment variable")
        return None

    try:
        subject = f"You're invited to join {html.escape(organization_name)} on CG SCF"

        # Build custom message section if provided (TD-09: sanitise user input)
        custom_section = ""
        if custom_message:
            safe_message = html.escape(custom_message)
            safe_inviter = html.escape(inviter_name)
            custom_section = f"""
            <div style="background-color: #f8f9fa; padding: 16px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #1976d2;">
                <p style="margin: 0; font-style: italic; color: #555;">"{safe_message}"</p>
                <p style="margin: 8px 0 0 0; font-size: 13px; color: #888;">— {safe_inviter}</p>
            </div>
            """

        # Sanitise all user-provided values for HTML context
        safe_inviter_name = html.escape(inviter_name)
        safe_org_name = html.escape(organization_name)

        html_body = f"""
        <html>
        <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
            <div style="text-align: center; margin-bottom: 30px;">
                <h1 style="color: #1976d2; margin: 0; font-size: 24px;">Welcome to SCF Controls Platform</h1>
            </div>

            <p style="font-size: 16px; line-height: 1.6;">Hi there,</p>

            <p style="font-size: 16px; line-height: 1.6;">
                <strong>{safe_inviter_name}</strong> has invited you to join <strong>{safe_org_name}</strong>
                on SCF Controls Platform — a platform for managing your organisation's Secure Controls Framework.
            </p>

            {custom_section}

            <p style="font-size: 16px; line-height: 1.6;">
                Click the button below to view and accept your invitation.
                You'll be able to sign in with Google if you don't have an account yet.
            </p>

            <div style="text-align: center; margin: 32px 0;">
                <a href="{APP_URL}/?invite={invite_token}&invite_type={invite_type}"
                   style="display: inline-block; background-color: #1976d2; color: white; padding: 14px 32px;
                          text-decoration: none; border-radius: 8px; font-weight: 500; font-size: 16px;">
                    Accept Invitation
                </a>
            </div>

            <p style="font-size: 14px; color: #666; line-height: 1.6;">
                Or copy this link into your browser:
                <br>
                <a href="{APP_URL}/?invite={invite_token}&invite_type={invite_type}" style="color: #1976d2;">{APP_URL}/?invite={invite_token}&invite_type={invite_type}</a>
            </p>

            <hr style="border: none; border-top: 1px solid #e5e7eb; margin: 30px 0;">

            <p style="font-size: 13px; color: #888; line-height: 1.5;">
                This invitation was sent by {safe_inviter_name} from {safe_org_name}.
                If you weren't expecting this email, you can safely ignore it.
            </p>
        </body>
        </html>
        """

        # Sanitize organization name for tags (only ASCII letters, numbers, underscores, dashes)
        import re
        sanitized_org_name = re.sub(r'[^a-zA-Z0-9_-]', '_', organization_name)[:50]

        params = {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
            "tags": [
                {"name": "type", "value": "invitation"},
                {"name": "organization", "value": sanitized_org_name}
            ]
        }

        logger.info(f"📤 Sending invitation email via Resend API...")
        logger.info(f"   From: {RESEND_FROM_EMAIL}")
        logger.info(f"   To: {_mask_email(to_email)}")
        logger.info(f"   Subject: {subject}")

        email = resend.Emails.send(params)

        logger.info(f"✅ INVITATION EMAIL SENT SUCCESSFULLY!")
        logger.info(f"   Email ID: {email.get('id', 'unknown')}")
        logger.info(f"   Recipient: {_mask_email(to_email)}")
        return email['id']

    except Exception as e:
        logger.error(f"❌ FAILED TO SEND INVITATION EMAIL")
        logger.error(f"   Recipient: {_mask_email(to_email)}")
        logger.error(f"   Error type: {type(e).__name__}")
        logger.error(f"   Error message: {str(e)}")
        import traceback
        logger.error(f"   Traceback: {traceback.format_exc()}")
        return None


# Test function
async def test_email_service():
    """Test the email service configuration."""
    if not RESEND_ENABLED:
        print("❌ Email service not configured")
        print("   Set RESEND_API_KEY environment variable")
        return False

    try:
        # Send test email
        params = {
            "from": RESEND_FROM_EMAIL,
            "to": ["test@example.com"],  # Change to your email for testing
            "subject": "CG SCF - Email Service Test",
            "html": "<h1>Email service is working!</h1><p>This is a test email from CG SCF.</p>"
        }

        email = resend.Emails.send(params)
        print(f"✅ Test email sent successfully: {email['id']}")
        return True

    except Exception as e:
        print(f"❌ Test email failed: {e}")
        return False


if __name__ == "__main__":
    """Test the email service"""
    import asyncio
    asyncio.run(test_email_service())
