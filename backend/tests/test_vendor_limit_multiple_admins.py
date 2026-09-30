"""check_vendor_limit must tolerate an organisation with more than one admin.

Regression for the production 500 on POST /organizations/{org}/vendors:
the admin lookup used scalar_one_or_none(), which raises MultipleResultsFound
as soon as a second admin is a member of the organisation.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import MultipleResultsFound

import catalog_models  # noqa: F401  — registers SystemCatalogTemplate so mappers configure
from models import SubscriptionTier
from services.vendor import VENDOR_TIER_LIMITS, check_vendor_limit


def _result(*, scalars_rows=None, scalar=None, one_or_none=None):
    """Build a fake SQLAlchemy Result exposing the accessors the service uses."""
    result = MagicMock()
    rows = list(scalars_rows or [])
    scalars = MagicMock()
    scalars.first.return_value = rows[0] if rows else None
    scalars.all.return_value = rows
    result.scalars.return_value = scalars
    result.scalar.return_value = scalar
    if one_or_none is MultipleResultsFound:
        result.scalar_one_or_none.side_effect = MultipleResultsFound()
    else:
        result.scalar_one_or_none.return_value = one_or_none
    return result


def _admin(joined_order: int):
    member = MagicMock()
    member.user_id = uuid.uuid4()
    member.joined_order = joined_order
    return member


@pytest.mark.asyncio
async def test_two_admins_do_not_raise_and_limit_is_enforced():
    org_id = uuid.uuid4()
    first_admin, second_admin = _admin(1), _admin(2)

    subscription = MagicMock()
    subscription.tier = SubscriptionTier.FREE.value
    free_limit = VENDOR_TIER_LIMITS[SubscriptionTier.FREE.value]

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        # admin lookup: ordered, limited to one row — but the DB still holds two admins
        _result(scalars_rows=[first_admin, second_admin], one_or_none=MultipleResultsFound),
        # subscription lookup for the chosen admin
        _result(one_or_none=subscription),
        # current vendor count, one below the free-tier ceiling
        _result(scalar=free_limit - 1),
    ])

    with patch("services.single_tenant.is_single_tenant_active", return_value=False):
        allowed = await check_vendor_limit(org_id, db)

    assert allowed is True

    # The subscription lookup must be keyed on the longest-standing admin.
    sub_stmt = db.execute.await_args_list[1].args[0]
    assert first_admin.user_id in sub_stmt.compile().params.values()
    assert second_admin.user_id not in sub_stmt.compile().params.values()


@pytest.mark.asyncio
async def test_two_admins_at_limit_is_denied():
    org_id = uuid.uuid4()
    subscription = MagicMock()
    subscription.tier = SubscriptionTier.FREE.value
    free_limit = VENDOR_TIER_LIMITS[SubscriptionTier.FREE.value]

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[
        _result(scalars_rows=[_admin(1), _admin(2)], one_or_none=MultipleResultsFound),
        _result(one_or_none=subscription),
        _result(scalar=free_limit),
    ])

    with patch("services.single_tenant.is_single_tenant_active", return_value=False):
        assert await check_vendor_limit(org_id, db) is False


@pytest.mark.asyncio
async def test_no_admin_still_denies():
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(scalars_rows=[])])

    with patch("services.single_tenant.is_single_tenant_active", return_value=False):
        assert await check_vendor_limit(uuid.uuid4(), db) is False
