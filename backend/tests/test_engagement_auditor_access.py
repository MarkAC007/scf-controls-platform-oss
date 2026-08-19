"""Isolation tests for engagement-scoped auditor access — the security spine.

Increment 3 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

resolve_engagement_access must:
  * admit a normal org member,
  * admit an external auditor ONLY for the engagement they hold an active grant to,
  * refuse (404, never 403) a non-member with no/other/revoked grant, and
  * refuse (404) when the engagement is not under the requested org,
so an auditor can never reach another engagement or leak engagement existence.

Uses a scripted fake async session and a monkeypatched verify_org_membership, in
the no-real-Postgres style of the other backend tests.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.audit_engagements as ae  # noqa: E402
from api.audit_engagements import resolve_engagement_access  # noqa: E402


ORG_ID = uuid4()
ENGAGEMENT_ID = uuid4()


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """Returns scripted values from successive execute().scalar_one_or_none()."""

    def __init__(self, scripted):
        self._scripted = list(scripted)

    async def execute(self, *_args, **_kwargs):
        if not self._scripted:
            raise AssertionError("FakeSession: ran out of scripted results")
        return _ScalarResult(self._scripted.pop(0))


def _user():
    return SimpleNamespace(db_id=str(uuid4()), email="auditor@example.com")


@pytest.fixture
def deny_membership(monkeypatch):
    """Make verify_org_membership behave as 'not an org member'."""
    async def _deny(*_args, **_kwargs):
        raise HTTPException(status_code=403, detail="not a member")
    monkeypatch.setattr(ae, "verify_org_membership", _deny)


@pytest.fixture
def allow_membership(monkeypatch):
    async def _allow(*_args, **_kwargs):
        return SimpleNamespace(role="viewer")
    monkeypatch.setattr(ae, "verify_org_membership", _allow)


@pytest.mark.asyncio
async def test_org_member_gets_access_as_member_not_auditor(allow_membership):
    session = _FakeSession([ENGAGEMENT_ID])  # engagement exists
    access = await resolve_engagement_access(_user(), ORG_ID, ENGAGEMENT_ID, session)
    assert access.is_auditor is False
    assert access.role == "viewer"


@pytest.mark.asyncio
async def test_auditor_with_active_grant_is_admitted(deny_membership):
    # engagement exists, then an active grant row is found
    session = _FakeSession([ENGAGEMENT_ID, uuid4()])
    access = await resolve_engagement_access(_user(), ORG_ID, ENGAGEMENT_ID, session)
    assert access.is_auditor is True
    assert access.role == "auditor"
    assert access.engagement_id == ENGAGEMENT_ID


@pytest.mark.asyncio
async def test_non_member_without_grant_is_refused_with_404(deny_membership):
    session = _FakeSession([ENGAGEMENT_ID, None])  # exists, but no grant
    with pytest.raises(HTTPException) as exc:
        await resolve_engagement_access(_user(), ORG_ID, ENGAGEMENT_ID, session)
    assert exc.value.status_code == 404  # never 403 — no existence leak


@pytest.mark.asyncio
async def test_revoked_or_other_engagement_grant_does_not_admit(deny_membership):
    # The grant query filters to status=active AND this engagement, so a revoked
    # grant (or one for a different engagement) simply returns no row -> None.
    session = _FakeSession([ENGAGEMENT_ID, None])
    with pytest.raises(HTTPException) as exc:
        await resolve_engagement_access(_user(), ORG_ID, ENGAGEMENT_ID, session)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_engagement_not_under_org_is_404_before_membership_check(monkeypatch):
    # If membership were consulted it would raise; assert we 404 on existence first.
    async def _boom(*_a, **_k):
        raise AssertionError("membership must not be checked when engagement is absent")
    monkeypatch.setattr(ae, "verify_org_membership", _boom)

    session = _FakeSession([None])  # engagement not found under this org
    with pytest.raises(HTTPException) as exc:
        await resolve_engagement_access(_user(), ORG_ID, ENGAGEMENT_ID, session)
    assert exc.value.status_code == 404
