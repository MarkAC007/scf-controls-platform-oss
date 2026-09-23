"""Audit rows name the record that changed, against a real Postgres.

Every successful mutation gets a request-level baseline row from
``AuditMiddleware``: an all-zero ``entity_id``, no field, no values. That row
is a deliberate safety net and these tests assert it survives. What they add is
the other half — that journey attestation, the engagement-query lifecycle and
auditor access grants each also write field-level rows carrying the real
``entity_id`` and real before/after values, so a reader can tell *what* changed
and not merely that something did.

Skips — never passes — when no database resolves. The DSN comes from the
application's own resolver, not ``os.environ``: under the file-secrets overlay
``DATABASE_URL`` is deliberately blank and the password lives in
``/run/secrets/DB_PASSWORD``, so reading the environment alone would skip this
whole module while reporting success. Run it through compose so that wiring
comes along::

    docker compose -p scf-controls-platform \
      -f docker-compose.yml -f docker-compose.secrets.yml \
      -f docker-compose.override.yml -f docker-compose.dev.yml \
      -f docker-compose.dev-local.yml \
      run --rm --no-deps -v <your-worktree>/backend:/app -w /app \
      backend python -m pytest tests/test_audit_row_identity.py -v

Fixture design follows tests/test_change_cursor.py: ``auth._authenticate`` is
stubbed rather than ``require_auth``, because the middleware only writes when
the real ``require_auth`` has set ``request.state.user``.
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402
import main  # noqa: E402
from database import get_db  # noqa: E402
from db_url import get_database_url  # noqa: E402
from models import (  # noqa: E402
    AuditEngagement,
    AuditEngagementStatus,
    AuditLog,
    EngagementAuditor,
    EngagementAuditorStatus,
    JourneyStage,
    JourneyStageState,
    OrgJourney,
    Organization,
    OrganizationMember,
    User,
)

# Empty only when nothing resolves at all — an explicit DATABASE_URL, or a
# password from the secrets file, both produce a DSN here.
DATABASE_URL = get_database_url("")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="no database resolved — SKIPPED, not passed",
)

asyncio_test = pytest.mark.asyncio

ZERO_UUID = uuid.UUID("00000000-0000-0000-0000-000000000000")

ATTEST_PATH = "/api/organizations/{org_id}/journey/stages/{stage_id}/attest"
QUERIES_PATH = "/api/organizations/{org_id}/engagements/{engagement_id}/queries"
QUERY_PATH = QUERIES_PATH + "/{query_id}"
RESPONSES_PATH = QUERY_PATH + "/responses"
AUDITORS_PATH = "/api/organizations/{org_id}/engagements/{engagement_id}/auditors"
AUDITOR_PATH = AUDITORS_PATH + "/{auditor_id}"


def value(raw):
    """Audit values are JSON-encoded; ``None`` stays ``None``."""
    return None if raw is None else json.loads(raw)


def field(rows, name):
    """The single row for *name*, or a readable failure naming what is there."""
    matching = [r for r in rows if r.field_name == name]
    assert len(matching) == 1, (
        f"expected exactly one {name!r} row, found {len(matching)} "
        f"among {sorted(r.field_name for r in rows)}"
    )
    return matching[0]


# ---------------------------------------------------------------------------
# Journey attestation
# ---------------------------------------------------------------------------

@asyncio_test
class TestAnAttestationNamesTheStage:
    async def test_the_row_carries_the_real_stage_id_not_the_zero_sentinel(
        self, api, audit, estate
    ):
        await api.attest(estate.first_stage)
        rows = await audit.detail_rows(entity_type="journey_stage")
        assert rows, "the attestation wrote no field-level audit rows"
        assert {r.entity_id for r in rows} >= {estate.first_stage}
        assert ZERO_UUID not in {r.entity_id for r in rows}

    async def test_the_row_labels_the_stage_by_key(self, api, audit, estate):
        await api.attest(estate.first_stage)
        rows = await audit.detail_rows(entity_id=estate.first_stage)
        assert {r.scf_id for r in rows} == {estate.first_stage_key}

    async def test_the_state_change_records_where_the_stage_came_from(
        self, api, audit, estate
    ):
        await api.attest(estate.first_stage)
        rows = await audit.detail_rows(entity_id=estate.first_stage)
        state = field(rows, "state")
        assert state.action == "update", "the stage existed before it was attested"
        assert value(state.old_value) == JourneyStageState.ACTIVE.value
        assert value(state.new_value) == JourneyStageState.PASSED.value
        assert state.changed_by_user_id == estate.admin

    async def test_a_conditional_pass_records_its_target_date(self, api, audit, estate):
        await api.attest(estate.first_stage, conditional=True, target_date="2099-01-01")
        rows = await audit.detail_rows(entity_id=estate.first_stage)
        assert value(field(rows, "state").new_value) == JourneyStageState.PASSED_CONDITIONAL.value
        target = field(rows, "target_date")
        assert value(target.old_value) is None
        assert value(target.new_value) == "2099-01-01"

    async def test_two_attestations_are_told_apart(self, api, audit, estate):
        await api.attest(estate.first_stage)
        await api.attest(estate.second_stage)
        rows = await audit.detail_rows(entity_type="journey_stage")
        attested = {r.entity_id for r in rows if r.field_name == "attested_at"}
        assert attested == {estate.first_stage, estate.second_stage}
        labels = {r.entity_id: r.scf_id for r in rows}
        assert labels[estate.first_stage] != labels[estate.second_stage]

    async def test_the_stage_the_attestation_unlocks_gets_its_own_record(
        self, api, audit, estate
    ):
        await api.attest(estate.first_stage)
        rows = await audit.detail_rows(entity_id=estate.second_stage)
        state = field(rows, "state")
        assert value(state.old_value) == JourneyStageState.LOCKED.value
        assert value(state.new_value) == JourneyStageState.ACTIVE.value

    async def test_a_key_too_long_for_the_label_column_still_attests(
        self, api, audit, estate
    ):
        """The label is a convenience; the entity id is the identity.

        ``audit_log.scf_id`` is far shorter than a stage key may be, so an
        over-long key is dropped rather than half-written or allowed to fail
        the write — and the attestation itself must be unaffected.
        """
        response = await api.attest(estate.long_key_stage)
        assert response.status_code == 200, response.text
        rows = await audit.detail_rows(entity_id=estate.long_key_stage)
        assert rows, "the attestation wrote no rows"
        assert {r.scf_id for r in rows} == {None}


# ---------------------------------------------------------------------------
# Engagement queries
# ---------------------------------------------------------------------------

@asyncio_test
class TestTheQueryLifecycleIsLegible:
    async def test_raising_a_query_names_it(self, api, audit, estate):
        query_id = await api.raise_query()
        rows = await audit.detail_rows(entity_id=query_id)
        assert rows and all(r.action == "create" for r in rows)
        assert {r.entity_type for r in rows} == {"engagement_query"}
        assert value(field(rows, "status").new_value) == "open"

    async def test_a_response_records_the_advance_to_answered(self, api, audit, estate):
        query_id = await api.raise_query()
        await api.respond(query_id)
        rows = await audit.detail_rows(entity_id=query_id, action="update")
        status = field(rows, "status")
        assert (value(status.old_value), value(status.new_value)) == ("open", "answered")

    async def test_close_and_reopen_are_opposite_in_both_columns(
        self, api, audit, estate
    ):
        query_id = await api.raise_query()
        await api.respond(query_id)

        # Each transition is read as the rows it alone added: responding has
        # already written a status row, so "the status row" is ambiguous.
        close_rows = await audit.rows_added_by(
            lambda: api.set_status(query_id, "closed"), entity_id=query_id
        )
        close_status = field(close_rows, "status")
        close_closed_at = field(close_rows, "closed_at")
        assert (value(close_status.old_value), value(close_status.new_value)) == (
            "answered", "closed",
        )
        assert value(close_closed_at.old_value) is None
        assert value(close_closed_at.new_value) is not None

        reopen_rows = await audit.rows_added_by(
            lambda: api.set_status(query_id, "open"), entity_id=query_id
        )
        reopen_status = field(reopen_rows, "status")
        reopen_closed_at = field(reopen_rows, "closed_at")
        assert (value(reopen_status.old_value), value(reopen_status.new_value)) == (
            "closed", "open",
        )
        assert value(reopen_closed_at.old_value) is not None
        assert value(reopen_closed_at.new_value) is None

        # The regression guard: on the unfixed code both transitions produced
        # identical rows, so a reader could not tell them apart.
        def triple(row):
            return (row.field_name, row.old_value, row.new_value)

        assert triple(close_status) != triple(reopen_status)
        assert triple(close_closed_at) != triple(reopen_closed_at)

    async def test_every_row_carries_the_control_the_query_is_about(
        self, api, audit, estate
    ):
        query_id = await api.raise_query()
        await api.respond(query_id)
        await api.set_status(query_id, "closed")
        rows = await audit.detail_rows(entity_id=query_id)
        assert {r.scf_id for r in rows} == {estate.query_scf_id}
        assert {r.changed_by_user_id for r in rows} == {estate.admin}


# ---------------------------------------------------------------------------
# Auditor access — the access-control siblings
# ---------------------------------------------------------------------------

@asyncio_test
class TestAuditorAccessIsIdentifiable:
    async def test_granting_access_names_the_grant_and_the_auditor(
        self, api, audit, session, estate
    ):
        auditor_id = await api.grant_auditor()
        rows = await audit.detail_rows(entity_type="engagement_auditor")
        assert rows, "granting engagement access wrote no field-level audit rows"
        assert {r.entity_id for r in rows} == {auditor_id}
        assert ZERO_UUID not in {r.entity_id for r in rows}
        assert value(field(rows, "user_id").new_value) == str(estate.auditor)
        assert value(field(rows, "status").new_value) == EngagementAuditorStatus.ACTIVE.value
        assert {r.changed_by_user_id for r in rows} == {estate.admin}

    async def test_revoking_access_records_the_withdrawal(self, api, audit, estate):
        auditor_id = await api.grant_auditor()
        rows = await audit.rows_added_by(
            lambda: api.revoke_auditor(auditor_id), entity_id=auditor_id
        )
        assert rows, "revoking engagement access wrote no field-level audit rows"
        status = field(rows, "status")
        assert (value(status.old_value), value(status.new_value)) == (
            EngagementAuditorStatus.ACTIVE.value,
            EngagementAuditorStatus.REVOKED.value,
        )
        assert value(field(rows, "revoked_at").new_value) is not None


# ---------------------------------------------------------------------------
# The baseline row is a safety net, not a casualty
# ---------------------------------------------------------------------------

@asyncio_test
class TestTheBaselineRowSurvives:
    async def test_an_attestation_still_writes_the_request_level_row(
        self, api, audit, estate
    ):
        await api.attest(estate.first_stage)
        baseline = await audit.baseline_rows()
        assert baseline, (
            "the request-level baseline row is gone — the detail rows are "
            "meant to accompany it, not replace it"
        )

    async def test_baseline_and_detail_rows_correlate_on_one_request_id(
        self, api, audit, estate
    ):
        await api.attest(estate.first_stage)
        baseline = await audit.baseline_rows()
        detail = await audit.detail_rows(entity_type="journey_stage")
        assert detail and baseline
        assert {r.request_id for r in detail} == {baseline[-1].request_id}
        assert baseline[-1].request_id is not None

    async def test_a_query_transition_keeps_its_baseline_row_too(
        self, api, audit, estate
    ):
        query_id = await api.raise_query()
        await api.set_status(query_id, "closed")
        baseline = await audit.baseline_rows()
        assert len(baseline) >= 2, "one baseline row per mutation is the contract"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

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
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


@pytest.fixture
async def estate(session):
    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"aud-{tag}", slug=f"aud-{tag}")
    session.add(org)
    await session.flush()

    async def _user(key):
        user = User(email=f"aud-{key}-{tag}@example.invalid", google_sub=f"aud-{key}-{tag}")
        session.add(user)
        await session.flush()
        return user

    admin = await _user("admin")
    auditor = await _user("auditor")
    session.add(OrganizationMember(organization_id=org.id, user_id=admin.id, role="admin"))

    journey = OrgJourney(organization_id=org.id, name=f"path-{tag}")
    session.add(journey)
    await session.flush()

    # A key comfortably within audit_log.scf_id, one to follow it, and one
    # deliberately longer than that column so the label path is exercised.
    first_key = "opening-stage"
    second_key = "following-stage"
    long_key = "a-stage-key-longer-than-the-audit-label-column-can-hold"
    stages = [
        JourneyStage(journey_id=journey.id, ordinal=1, key=first_key,
                     title="Opening", state=JourneyStageState.ACTIVE.value),
        JourneyStage(journey_id=journey.id, ordinal=2, key=second_key,
                     title="Following", state=JourneyStageState.LOCKED.value),
        JourneyStage(journey_id=journey.id, ordinal=3, key=long_key,
                     title="Long", state=JourneyStageState.ACTIVE.value),
    ]
    session.add_all(stages)

    engagement = AuditEngagement(
        organization_id=org.id, created_by_user_id=admin.id,
        name=f"eng-{tag}", frameworks=["scf"],
        status=AuditEngagementStatus.DRAFT.value,
    )
    session.add(engagement)
    await session.flush()
    await session.commit()

    built = _Estate(
        org_id=org.id,
        admin=admin.id,
        auditor=auditor.id,
        journey_id=journey.id,
        first_stage=stages[0].id,
        first_stage_key=first_key,
        second_stage=stages[1].id,
        long_key_stage=stages[2].id,
        engagement_id=engagement.id,
        query_scf_id=f"Q-{tag[:6]}",
    )
    user_ids = [admin.id, auditor.id]
    try:
        yield built
    finally:
        # Ids come from `built`, not the ORM objects: rollback expires them and
        # a lazy reload here raises MissingGreenlet in the sync finaliser.
        await session.rollback()
        await session.execute(sa.delete(Organization).where(Organization.id == built.org_id))
        await session.execute(sa.delete(User).where(User.id.in_(user_ids)))
        await session.commit()


class _Audit:
    """Reads audit rows back after the request has returned and committed."""

    def __init__(self, session, org_id):
        self._session = session
        self._org_id = org_id

    async def _rows(self, *clauses):
        return (await self._session.execute(
            sa.select(AuditLog)
            .where(AuditLog.organization_id == self._org_id, *clauses)
            .order_by(AuditLog.changed_at, AuditLog.id)
        )).scalars().all()

    async def detail_rows(self, entity_type=None, entity_id=None, action=None):
        clauses = [AuditLog.entity_id != ZERO_UUID]
        if entity_type is not None:
            clauses.append(AuditLog.entity_type == entity_type)
        if entity_id is not None:
            clauses.append(AuditLog.entity_id == entity_id)
        if action is not None:
            clauses.append(AuditLog.action == action)
        return await self._rows(*clauses)

    async def baseline_rows(self):
        return await self._rows(AuditLog.entity_id == ZERO_UUID)

    async def rows_added_by(self, action, **filters):
        """The rows *action* alone wrote, by id difference either side of it."""
        before = {r.id for r in await self.detail_rows(**filters)}
        await action()
        return [r for r in await self.detail_rows(**filters) if r.id not in before]


@pytest.fixture
async def audit(session, estate):
    return _Audit(session, estate.org_id)


class _Api:
    def __init__(self, client, current, estate):
        self.client = client
        self._current = current
        self._estate = estate

    def as_(self, key: str) -> None:
        user_id = {"admin": self._estate.admin, "auditor": self._estate.auditor}[key]
        self._current["user"] = auth_mod.User(
            user_id=f"stub-{key}", email=f"{key}@example.invalid",
            auth_method="google", db_id=str(user_id),
        )

    async def attest(self, stage_id, conditional=False, target_date=None):
        self.as_("admin")
        body = {"conditional": conditional}
        if target_date is not None:
            body["target_date"] = target_date
        return await self.client.post(
            ATTEST_PATH.format(org_id=self._estate.org_id, stage_id=stage_id),
            json=body,
        )

    async def raise_query(self):
        self.as_("admin")
        response = await self.client.post(
            QUERIES_PATH.format(org_id=self._estate.org_id,
                                engagement_id=self._estate.engagement_id),
            json={"scf_id": self._estate.query_scf_id,
                  "title": "A question", "body": "About this control."},
        )
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["id"])

    async def respond(self, query_id):
        self.as_("admin")
        response = await self.client.post(
            RESPONSES_PATH.format(org_id=self._estate.org_id,
                                  engagement_id=self._estate.engagement_id,
                                  query_id=query_id),
            json={"content": "An answer."},
        )
        assert response.status_code == 201, response.text
        return response

    async def set_status(self, query_id, status):
        self.as_("admin")
        response = await self.client.patch(
            QUERY_PATH.format(org_id=self._estate.org_id,
                              engagement_id=self._estate.engagement_id,
                              query_id=query_id),
            json={"status": status},
        )
        assert response.status_code == 200, response.text
        return response

    async def grant_auditor(self):
        self.as_("admin")
        response = await self.client.post(
            AUDITORS_PATH.format(org_id=self._estate.org_id,
                                 engagement_id=self._estate.engagement_id),
            json={"user_id": str(self._estate.auditor)},
        )
        assert response.status_code == 201, response.text
        return uuid.UUID(response.json()["id"])

    async def revoke_auditor(self, auditor_id):
        self.as_("admin")
        response = await self.client.delete(
            AUDITOR_PATH.format(org_id=self._estate.org_id,
                                engagement_id=self._estate.engagement_id,
                                auditor_id=auditor_id),
        )
        assert response.status_code == 204, response.text
        return response


@pytest.fixture
async def app_engine():
    """Give the middleware's module-level engine this test's event loop.

    ``AuditMiddleware`` writes through ``database.AsyncSessionLocal``, whose
    engine pools connections bound to whichever loop first used them. Each test
    runs on a fresh loop, so without disposing the pool either side the
    middleware picks up a connection from a dead loop, swallows the error, and
    the test reads an empty audit table as a missing row.
    """
    import database

    await database.engine.dispose()
    try:
        yield database.engine
    finally:
        await database.engine.dispose()


@pytest.fixture
async def api(app_engine, session, estate, monkeypatch):
    """The real app, the real ``require_auth``, a stubbed token check."""
    from fastapi import HTTPException

    current = {"user": None}

    async def _authenticate(credentials, db):
        if current["user"] is None:
            raise HTTPException(status_code=401, detail="no caller chosen")
        return current["user"]

    async def _db():
        yield session

    monkeypatch.setattr(auth_mod, "_authenticate", _authenticate)
    main.app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://audit",
            headers={"Authorization": "Bearer stub"},
        ) as client:
            yield _Api(client, current, estate)
    finally:
        main.app.dependency_overrides.pop(get_db, None)
