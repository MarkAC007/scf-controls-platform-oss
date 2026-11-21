# SPDX-License-Identifier: AGPL-3.0-only
"""A risk owner must be someone who may be given work in the organisation (#1271).

The risk-assessment writes stored whatever ``owner_user_id`` the body carried,
and the response then returned that user's name and email: an editor of one
organisation could resolve a user UUID of another to an identity. These tests
pin that every write path asks ``assert_user_in_org`` before anything is
stored, and that an owner the risk already has is not asked about again.

The first half needs no database. The second half runs the real endpoints on
a rolled-back transaction where a Postgres is reachable.
"""
import uuid as _uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from api import custom_risks as custom_api
from api import risk_assessments as risk_api
from auth import OrgMembership
from models import AuditLog, Organization, OrganizationMember, RiskAssessment, User
from schemas import CustomRiskDefinitionCreate, RiskAssessmentCreate, RiskAssessmentUpdate

from test_journey_service import requires_postgres
from test_risk_acceptance import db, no_commit, _request  # noqa: F401  (fixtures)

# CI runs pytest from the repository root, where backend/pytest.ini (asyncio_mode = auto) is not read.
pytestmark = pytest.mark.asyncio

ORG = _uuid.uuid4()
MEMBER = _uuid.uuid4()
OUTSIDER = _uuid.uuid4()


# ---------------------------------------------------------------------------
# No database
# ---------------------------------------------------------------------------


@pytest.fixture
def members_only(monkeypatch):
    """``assert_user_in_org`` knows one member; records who it was asked about."""
    asked = []

    async def fake(user_id, org_id, db):
        asked.append((user_id, org_id))
        if user_id != MEMBER:
            raise HTTPException(status_code=404, detail="Assigned user not found in this organisation")

    monkeypatch.setattr(risk_api, "assert_user_in_org", fake)
    return asked


async def test_an_owner_outside_the_organisation_is_refused(members_only):
    with pytest.raises(HTTPException) as exc:
        await risk_api.check_owner(None, ORG, {"owner_user_id": OUTSIDER})
    assert exc.value.status_code == 404
    assert members_only == [(OUTSIDER, ORG)]


async def test_a_member_may_own_a_risk(members_only):
    await risk_api.check_owner(None, ORG, {"owner_user_id": MEMBER})
    assert members_only == [(MEMBER, ORG)]


@pytest.mark.parametrize("data", [{}, {"owner_user_id": None}, {"notes": "no owner in the body"}])
async def test_no_owner_or_a_cleared_owner_is_not_checked(members_only, data):
    await risk_api.check_owner(None, ORG, data, current=OUTSIDER)
    assert members_only == []


async def test_the_owner_a_risk_already_has_is_not_asked_about_again(members_only):
    # Someone who has since left: sending them back unchanged must not block the save.
    await risk_api.check_owner(None, ORG, {"owner_user_id": OUTSIDER}, current=OUTSIDER)
    assert members_only == []


class _Session:
    """Answers the handler's first query with ``found``; nothing may be written."""

    def __init__(self, found):
        self.found = found
        self.added = []

    async def execute(self, _query):
        return SimpleNamespace(scalar_one_or_none=lambda: self.found)

    def add(self, row):
        self.added.append(row)

    async def flush(self):  # pragma: no cover - reaching it is the failure
        raise AssertionError("the write went ahead")

    commit = flush


def _editor():
    return OrgMembership(user=SimpleNamespace(db_id=str(MEMBER), email="e@example.invalid"), organization_id=ORG, role="editor")


def _existing():
    return RiskAssessment(id=_uuid.uuid4(), organization_id=ORG, risk_code="R-ORG-1", owner_user_id=None, notes="before")


@pytest.mark.parametrize("via", ["patch", "post-update", "post-create"])
async def test_each_write_path_refuses_before_storing_anything(members_only, via):
    existing = None if via == "post-create" else _existing()
    session = _Session(existing)
    with pytest.raises(HTTPException) as exc:
        if via == "patch":
            await risk_api.update_risk_assessment(
                ORG, "R-ORG-1", RiskAssessmentUpdate(owner_user_id=OUTSIDER, notes="after"),
                _request(), _editor(), session,
            )
        else:
            await risk_api.create_or_update_risk_assessment(
                ORG, RiskAssessmentCreate(risk_code="R-ORG-1", owner_user_id=OUTSIDER, notes="after"),
                _request(), _editor(), session,
            )
    assert exc.value.status_code == 404
    assert session.added == []
    if existing is not None:
        assert existing.owner_user_id is None and existing.notes == "before"


# ---------------------------------------------------------------------------
# Database-backed: the real endpoints on a rolled-back transaction
# ---------------------------------------------------------------------------


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"own-{tag}", slug=f"own-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db, org, role="editor"):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"own-{tag}", email=f"own-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role=role))
    await db.flush()
    return user


def _membership(org, user):
    return OrgMembership(user=SimpleNamespace(db_id=str(user.id), email=user.email), organization_id=org.id, role="editor")


@requires_postgres
class TestAgainstTheDatabase:
    async def _risk(self, db, org, user):
        out = await custom_api.create_custom_risk(
            org.id, CustomRiskDefinitionCreate(title="Legacy VPN", description="Below the standard"),
            _request(), _membership(org, user), db,
        )
        return out.risk_code

    @pytest.mark.parametrize("via", ["patch", "post"])
    async def test_another_organisations_user_is_refused_and_nothing_changes(self, db, no_commit, via):
        ours, theirs = await _org(db), await _org(db)
        editor, outsider = await _user(db, ours), await _user(db, theirs)
        code = await self._risk(db, ours, editor)
        with pytest.raises(HTTPException) as exc:
            if via == "patch":
                await risk_api.update_risk_assessment(
                    ours.id, code, RiskAssessmentUpdate(owner_user_id=outsider.id),
                    _request(), _membership(ours, editor), db,
                )
            else:
                await risk_api.create_or_update_risk_assessment(
                    ours.id, RiskAssessmentCreate(risk_code=code, owner_user_id=outsider.id),
                    _request(), _membership(ours, editor), db,
                )
        assert exc.value.status_code == 404
        assert outsider.email not in str(exc.value.detail)
        row = await db.scalar(select(RiskAssessment).where(
            RiskAssessment.organization_id == ours.id, RiskAssessment.risk_code == code))
        assert row.owner_user_id is None
        audited = (await db.execute(select(AuditLog.id).where(
            AuditLog.entity_id == row.id, AuditLog.field_name == "owner_user_id"))).all()
        assert audited == []

    async def test_a_member_is_accepted_and_returned(self, db, no_commit):
        org = await _org(db)
        editor, colleague = await _user(db, org), await _user(db, org, "viewer")
        code = await self._risk(db, org, editor)
        out = await risk_api.update_risk_assessment(
            org.id, code, RiskAssessmentUpdate(owner_user_id=colleague.id),
            _request(), _membership(org, editor), db,
        )
        assert out["owner_user_id"] == colleague.id
