# SPDX-License-Identifier: AGPL-3.0-only
"""External auditors: invited across the email-domain rule, confined to the
engagements an admin assigns them.

Two halves:

* The domain rule itself (no database). Nothing pinned it before this file.
* The auditor list, grants, sign-in activation, disable and expiry, against a
  real Postgres. These skip -- never pass -- without a ``postgresql``
  ``DATABASE_URL``. Run them against a throwaway database, never the dev one::

    docker run --rm --network cg-scf-network -v $PWD/backend:/app -w /app \\
      -e DATABASE_URL=postgresql+asyncpg://...throwaway... <backend image> \\
      python -m pytest tests/test_external_auditor_invite.py -v
"""
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace
from datetime import date, timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402
import catalog_models  # noqa: E402,F401  (mapper configuration needs both modules)
from services.domain_validation import validate_invite_domain  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")
needs_db = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
)


# --------------------------------------------------------------------------
# The domain rule for membership invites (unchanged, now pinned)
# --------------------------------------------------------------------------

class TestMembershipInvitesKeepTheDomainRule:
    def test_a_different_domain_is_refused(self):
        ok, message = validate_invite_domain("admin@corp.example", "lead@audit-firm.example")
        assert ok is False
        assert "same email domain" in message and "corp.example" in message

    def test_the_same_domain_is_allowed(self):
        assert validate_invite_domain("admin@corp.example", "new.hire@corp.example") == (True, "")

    def test_an_inviter_on_public_mail_cannot_invite(self):
        ok, message = validate_invite_domain("someone@gmail.com", "someone.else@gmail.com")
        assert ok is False
        assert "corporate email domain" in message

    @pytest.mark.asyncio
    async def test_a_membership_invite_cannot_carry_the_auditor_role(self):
        from services.org_invite import create_invite

        with pytest.raises(ValueError, match="Invalid role 'auditor'"):
            await create_invite(
                org_id=uuid.uuid4(), inviter_user_id=uuid.uuid4(),
                inviter_email="admin@corp.example", email="lead@audit-firm.example",
                role="auditor", message=None, db=None,
            )


class TestGrantPayload:
    def test_exactly_one_target(self):
        from pydantic import ValidationError
        from schemas import EngagementAuditorCreate

        with pytest.raises(ValidationError):
            EngagementAuditorCreate()
        with pytest.raises(ValidationError):
            EngagementAuditorCreate(user_id=uuid.uuid4(), auditor_id=uuid.uuid4())
        assert EngagementAuditorCreate(auditor_id=uuid.uuid4()).kind == "external"

    def test_a_roster_auditor_is_never_internal(self):
        from pydantic import ValidationError
        from schemas import EngagementAuditorCreate

        with pytest.raises(ValidationError):
            EngagementAuditorCreate(auditor_id=uuid.uuid4(), kind="internal")


# --------------------------------------------------------------------------
# Database-backed
# --------------------------------------------------------------------------

AUDITORS = "/api/organizations/{org}/auditors"
GRANTS = "/api/organizations/{org}/engagements/{eng}/auditors"
MEMBERS = "/api/organizations/{org}/members"


@pytest.fixture
async def engine():
    eng = create_async_engine(DATABASE_URL)
    try:
        async with eng.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await eng.dispose()
        pytest.skip(f"database not reachable: {exc}")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        try:
            yield s
        finally:
            await s.rollback()


class _Estate:
    pass


@pytest.fixture
async def estate(session):
    from models import AuditEngagement, Organization, OrganizationMember, User

    tag = uuid.uuid4().hex[:10]
    e = _Estate()
    e.tag = tag
    e.domain = f"corp-{tag}.example"
    org = Organization(name=f"aud-{tag}", slug=f"aud-{tag}")
    session.add(org)
    await session.flush()

    async def _user(key, domain):
        user = User(email=f"{key}@{domain}", google_sub=f"aud-{key}-{tag}", oidc_issuer="https://idp.test")
        session.add(user)
        await session.flush()
        return user

    e.admin = await _user("admin", e.domain)
    e.viewer = await _user("viewer", e.domain)
    session.add_all([
        OrganizationMember(organization_id=org.id, user_id=e.admin.id, role="admin"),
        OrganizationMember(organization_id=org.id, user_id=e.viewer.id, role="viewer"),
    ])
    eng = AuditEngagement(organization_id=org.id, name=f"ISO audit {tag}", frameworks=[])
    ended = AuditEngagement(
        organization_id=org.id, name=f"Ended {tag}", frameworks=[],
        end_date=date.today() - timedelta(days=1),
    )
    session.add_all([eng, ended])
    await session.commit()
    e.org_id, e.eng_id, e.ended_id = org.id, eng.id, ended.id
    e.auditor_email = f"lead.auditor@audit-firm-{tag}.example"
    try:
        yield e
    finally:
        await session.rollback()
        await session.execute(sa.delete(Organization).where(Organization.id == e.org_id))
        await session.execute(sa.delete(User).where(User.email.like(f"%{tag}%")))
        await session.commit()


@pytest.fixture
async def app_engine():
    import database

    await database.engine.dispose()
    try:
        yield database.engine
    finally:
        await database.engine.dispose()


class _Api:
    def __init__(self, client, current):
        self.client = client
        self._current = current

    def as_(self, user) -> None:
        self._current["user"] = auth_mod.User(
            user_id=f"stub-{user.id}", email=user.email, auth_method="google", db_id=str(user.id),
        )


@pytest.fixture
async def api(app_engine, session, estate, monkeypatch):
    import main
    from api import org_auditors as api_mod
    from database import get_db
    from fastapi import HTTPException
    from services import keycloak_admin

    current = {"user": None}
    sent = []

    async def _authenticate(credentials, db):
        if current["user"] is None:
            raise HTTPException(status_code=401, detail="no caller chosen")
        return current["user"]

    async def _db():
        yield session

    async def _send(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(auth_mod, "_authenticate", _authenticate)
    monkeypatch.setattr(keycloak_admin, "is_enabled", lambda: False)
    monkeypatch.setattr(api_mod, "send_auditor_invitation_email", _send)
    main.app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://auditors",
            headers={"Authorization": "Bearer stub"},
        ) as client:
            a = _Api(client, current)
            a.sent = sent
            yield a
    finally:
        main.app.dependency_overrides.pop(get_db, None)


async def _invite(api, estate, **extra):
    api.as_(estate.admin)
    return await api.client.post(
        AUDITORS.format(org=estate.org_id),
        json={"email": estate.auditor_email, "display_name": "Lead Auditor", "firm": "Audit Firm", **extra},
    )


async def _access(session, estate, user_id, eng_id=None):
    """resolve_engagement_access for this user, or the HTTP status it raised."""
    from api.audit_engagements import resolve_engagement_access
    from fastapi import HTTPException

    user = auth_mod.User(user_id="stub", email="x@y.example", auth_method="google", db_id=str(user_id))
    try:
        return await resolve_engagement_access(user, estate.org_id, eng_id or estate.eng_id, session)
    except HTTPException as exc:
        return exc.status_code


@needs_db
@pytest.mark.asyncio
class TestInvite:
    async def test_an_outside_domain_auditor_is_invited_without_becoming_a_member(self, api, session, estate):
        from models import AuditLog, OrganizationMember, User

        response = await _invite(api, estate)
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["status"] == "invited"
        assert body["email"] == estate.auditor_email
        assert body["idp_temporary_password"] is None  # Keycloak off in this test

        user = (await session.execute(sa.select(User).where(User.email == estate.auditor_email))).scalar_one()
        assert user.google_sub == f"pending:{estate.auditor_email}"
        assert str(user.id) == body["user_id"]
        members = (await session.execute(
            sa.select(sa.func.count()).select_from(OrganizationMember)
            .where(OrganizationMember.organization_id == estate.org_id)
        )).scalar()
        assert members == 2  # admin + viewer: the auditor takes no seat

        trail = (await session.execute(
            sa.select(AuditLog.field_name, AuditLog.new_value)
            .where(AuditLog.organization_id == estate.org_id, AuditLog.entity_type == "organization_auditor")
        )).all()
        assert ("domain_rule", f"exempt: external auditor (audit-firm-{estate.tag}.example)") in trail
        assert len(api.sent) == 1 and api.sent[0]["to_email"] == estate.auditor_email

    async def test_public_mail_is_refused(self, api, estate):
        estate.auditor_email = f"auditor{estate.tag}@gmail.com"
        response = await _invite(api, estate)
        assert response.status_code == 400
        assert "Public email" in response.json()["detail"]

    async def test_a_viewer_cannot_invite(self, api, estate):
        api.as_(estate.viewer)
        response = await api.client.post(AUDITORS.format(org=estate.org_id), json={"email": estate.auditor_email})
        assert response.status_code == 403

    async def test_a_member_is_pointed_at_internal_auditing(self, api, estate):
        estate.auditor_email = estate.viewer.email
        response = await _invite(api, estate)
        assert response.status_code == 400
        assert "internal auditor" in response.json()["detail"]

    async def test_the_same_auditor_twice_is_refused(self, api, estate):
        assert (await _invite(api, estate)).status_code == 201
        response = await _invite(api, estate)
        assert response.status_code == 400
        assert "already on" in response.json()["detail"]

    async def test_a_viewer_can_list_the_auditors(self, api, estate):
        await _invite(api, estate)
        api.as_(estate.viewer)
        response = await api.client.get(AUDITORS.format(org=estate.org_id))
        assert response.status_code == 200
        assert [a["email"] for a in response.json()] == [estate.auditor_email]


@needs_db
@pytest.mark.asyncio
class TestAssignmentAndSignIn:
    async def test_a_grant_waits_until_first_sign_in_then_opens_one_engagement(self, api, session, estate):
        from models import OrganizationAuditor

        auditor = (await _invite(api, estate)).json()
        api.as_(estate.admin)
        response = await api.client.post(
            GRANTS.format(org=estate.org_id, eng=estate.eng_id), json={"auditor_id": auditor["id"]},
        )
        assert response.status_code == 201, response.text
        assert response.json()["status"] == "invited"
        user_id = uuid.UUID(auditor["user_id"])
        assert await _access(session, estate, user_id) == 404  # not signed in yet

        # First sign-in through the real provisioning path: the placeholder is
        # adopted and the roster entry plus the grant turn on.
        signed_in = await auth_mod._persist_oidc_user(
            session, sub=f"kc-{estate.tag}", email=estate.auditor_email,
            display_name="Lead Auditor", issuer="https://idp.test", email_verified=True,
        )
        assert signed_in.id == user_id
        session.expire_all()
        roster = (await session.execute(
            sa.select(OrganizationAuditor).where(OrganizationAuditor.id == uuid.UUID(auditor["id"]))
        )).scalar_one()
        assert roster.status == "active"

        access = await _access(session, estate, user_id)
        assert access.is_auditor is True and access.role == "auditor"
        assert await _access(session, estate, user_id, eng_id=estate.ended_id) == 404  # not assigned

        api.as_(SimpleNamespace(id=user_id, email=estate.auditor_email))
        assert (await api.client.get(MEMBERS.format(org=estate.org_id))).status_code == 403
        assert (await api.client.get(AUDITORS.format(org=estate.org_id))).status_code == 403

    async def test_an_unverified_email_claim_does_not_adopt_the_invite(self, api, session, estate):
        from fastapi import HTTPException

        await _invite(api, estate)
        with pytest.raises(HTTPException) as exc:
            await auth_mod._persist_oidc_user(
                session, sub=f"kc-{estate.tag}", email=estate.auditor_email,
                display_name=None, issuer="https://idp.test", email_verified=False,
            )
        assert exc.value.status_code == 403

    async def test_a_member_cannot_be_granted_as_external(self, api, estate):
        api.as_(estate.admin)
        response = await api.client.post(
            GRANTS.format(org=estate.org_id, eng=estate.eng_id),
            json={"user_id": str(estate.viewer.id), "kind": "external"},
        )
        assert response.status_code == 422
        assert "internal auditor" in response.json()["detail"]

    async def test_another_orgs_auditor_cannot_be_assigned(self, api, estate):
        api.as_(estate.admin)
        response = await api.client.post(
            GRANTS.format(org=estate.org_id, eng=estate.eng_id), json={"auditor_id": str(uuid.uuid4())},
        )
        assert response.status_code == 404


@needs_db
@pytest.mark.asyncio
class TestEndingAccess:
    async def _signed_in_with_grant(self, api, session, estate, eng_id):
        auditor = (await _invite(api, estate)).json()
        await auth_mod._persist_oidc_user(
            session, sub=f"kc-{estate.tag}", email=estate.auditor_email,
            display_name=None, issuer="https://idp.test", email_verified=True,
        )
        api.as_(estate.admin)
        response = await api.client.post(
            GRANTS.format(org=estate.org_id, eng=eng_id), json={"auditor_id": auditor["id"]},
        )
        assert response.status_code == 201 and response.json()["status"] == "active", response.text
        return auditor

    async def test_disabling_revokes_every_grant_and_blocks_reassignment(self, api, session, estate):
        auditor = await self._signed_in_with_grant(api, session, estate, estate.eng_id)
        user_id = uuid.UUID(auditor["user_id"])
        assert (await _access(session, estate, user_id)).is_auditor is True

        response = await api.client.patch(
            f"{AUDITORS.format(org=estate.org_id)}/{auditor['id']}", json={"status": "disabled"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "disabled"
        session.expire_all()
        assert await _access(session, estate, user_id) == 404

        response = await api.client.post(
            GRANTS.format(org=estate.org_id, eng=estate.eng_id), json={"auditor_id": auditor["id"]},
        )
        assert response.status_code == 422

    async def test_a_viewer_cannot_disable(self, api, session, estate):
        auditor = (await _invite(api, estate)).json()
        api.as_(estate.viewer)
        response = await api.client.patch(
            f"{AUDITORS.format(org=estate.org_id)}/{auditor['id']}", json={"status": "disabled"},
        )
        assert response.status_code == 403

    async def test_access_ends_the_day_after_the_engagement(self, api, session, estate):
        from models import EngagementAuditor
        from services.org_auditors import revoke_expired_grants

        auditor = await self._signed_in_with_grant(api, session, estate, estate.ended_id)
        await api.client.post(
            GRANTS.format(org=estate.org_id, eng=estate.eng_id), json={"auditor_id": auditor["id"]},
        )
        await revoke_expired_grants(session)
        session.expire_all()
        rows = dict((await session.execute(
            sa.select(EngagementAuditor.engagement_id, EngagementAuditor.status)
            .where(EngagementAuditor.user_id == uuid.UUID(auditor["user_id"]))
        )).all())
        assert rows[estate.ended_id] == "revoked"
        assert rows[estate.eng_id] == "active"  # no end date: untouched
