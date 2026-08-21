"""
Regression tests for GET /api/users/me under static master-key auth.

Before the fix this branch returned ``created_at: None``, which UserResponse
rejects (``created_at: datetime`` is required) — every API-key session got a 500,
so the web app could not read a profile and hid all platform-admin surfaces even
though the API itself was granting access.

The branch now reports ``is_platform_admin`` from ``is_single_tenant_active()``,
matching how ``auth.require_org_role`` and ``auth.get_accessible_org_ids`` already
gate the same principal. Fail-closed: multi-tenant reports False.
"""
from datetime import datetime
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from api import users as users_api
from schemas import UserResponse
from services.service_account import SERVICE_ACCOUNT_EMAIL


class _FakeResult:
    """Stands in for the object SQLAlchemy's execute() returns."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeDB:
    def __init__(self, value=None):
        self._value = value

    async def execute(self, _stmt):
        return _FakeResult(self._value)


def _api_key_user():
    user = MagicMock()
    user.auth_method = "api_key"
    user.email = SERVICE_ACCOUNT_EMAIL
    user.name = "API User"
    user.db_id = None
    return user


async def _call(monkeypatch, *, single_tenant, db_user=None):
    monkeypatch.setattr(users_api, "is_single_tenant_active", lambda: single_tenant)
    monkeypatch.setattr(users_api, "get_service_account_id", lambda: None)
    return await users_api.get_current_user(
        current_user=_api_key_user(), db=_FakeDB(db_user)
    )


@pytest.mark.asyncio
async def test_api_key_profile_is_platform_admin_in_single_tenant(monkeypatch):
    payload = await _call(monkeypatch, single_tenant=True)
    assert payload["is_platform_admin"] is True


@pytest.mark.asyncio
async def test_api_key_profile_not_admin_when_multi_tenant(monkeypatch):
    """Fail-closed: the platform-admin grant is tied to the single-tenant guard."""
    payload = await _call(monkeypatch, single_tenant=False)
    assert payload["is_platform_admin"] is False


@pytest.mark.asyncio
async def test_api_key_profile_has_real_created_at(monkeypatch):
    """The None that caused the 500 must never come back."""
    payload = await _call(monkeypatch, single_tenant=True)
    assert isinstance(payload["created_at"], datetime)


@pytest.mark.asyncio
async def test_api_key_profile_validates_against_schema(monkeypatch):
    """The whole point: the payload must satisfy the declared response model."""
    payload = await _call(monkeypatch, single_tenant=True)
    model = UserResponse.model_validate(payload)
    assert model.is_platform_admin is True
    assert model.subscription is None


@pytest.mark.asyncio
async def test_api_key_profile_uses_service_account_row_when_present(monkeypatch):
    """When the seeded service-account row exists, prefer its real identity."""
    sa_id = UUID("11111111-2222-3333-4444-555555555555")
    created = datetime(2026, 1, 2, 3, 4, 5)
    db_user = MagicMock()
    db_user.id = sa_id
    db_user.google_sub = "static-api-key-service-account"
    db_user.created_at = created
    db_user.last_login_at = None

    monkeypatch.setattr(users_api, "is_single_tenant_active", lambda: True)
    monkeypatch.setattr(users_api, "get_service_account_id", lambda: str(sa_id))
    payload = await users_api.get_current_user(
        current_user=_api_key_user(), db=_FakeDB(db_user)
    )

    assert payload["id"] == sa_id
    assert payload["created_at"] == created
