"""Round-trip tests for GET /database/backup and POST /database/restore.

These two endpoints had zero coverage despite being the most destructive pair
in the codebase, and both were broken:

* restore returned HTTP 500 unconditionally — ``DELETE FROM users`` cannot
  succeed while ``audit_log.changed_by_user_id`` is NOT NULL behind an
  ``ON DELETE SET NULL`` FK (fixed by migration ``auditlognull01``);
* backup exported zero users whenever ``organization_members`` was empty, so a
  restore could never satisfy its own foreign keys;
* restore wiped *every* tenant with unfiltered ``DELETE FROM`` and committed
  before inserting, so a partial-scope backup destroyed everything absent
  from it, irrecoverably.

The assertions that matter here are therefore about blast radius, not about
happy-path row counts. ``test_restore_leaves_other_organisation_untouched`` is
the one that would have caught the original defect, and
``test_restore_rejects_inaccessible_organisation`` is the one that stops the
request body from choosing which tenant gets destroyed.

A real PostgreSQL is required — the behaviour under test is cascade and
constraint behaviour, which a mocked session cannot express. Set
``TEST_RESTORE_DATABASE_URL`` to an async DSN pointing at a *throwaway*
database; the module creates and drops the whole schema. Without it the
module skips, matching ``test_vendor_assessment_migration.py``.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DSN = os.getenv("TEST_RESTORE_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DSN,
    reason="TEST_RESTORE_DATABASE_URL not set — needs a throwaway PostgreSQL database",
)

if DSN:  # imports touch settings that are pointless to load when skipping
    import main  # noqa: E402
    from auth import User, require_platform_admin  # noqa: E402
    from database import get_db  # noqa: E402
    from models import (  # noqa: E402
        Base,
        EvidenceTracking,
        Organization,
        OrganizationMember,
        ScopedControl,
        User as UserModel,
    )

TARGET_ORG_ID = uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001")
OTHER_ORG_ID = uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002")
ADMIN_USER_ID = uuid.UUID("cccccccc-0000-0000-0000-000000000003")


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """Hand the database back empty; the app's lifespan runs Alembic to build it.

    ``init_db`` defaults to ``DB_INIT_MODE=alembic``, and Alembic's DDL has no
    ``checkfirst`` — so the schema must not pre-exist. Dropping the whole
    public schema (rather than ``Base.metadata.drop_all``) also clears
    ``alembic_version``, without which the migrations would be skipped as
    already-applied and the tables would never appear.
    """
    # NullPool: TestClient runs the app in its own event loop while these
    # fixtures and the assertion sessions run in pytest-asyncio's. A pooled
    # asyncpg connection reused across two loops raises "attached to a
    # different loop", so nothing may be pooled here.
    engine = create_async_engine(DSN, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"No PostgreSQL reachable for restore tests: {exc}")
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def seeded(client_raw, db_engine):
    """Two organisations, both visible to the admin, each with one control.

    Two orgs is the whole point: one is the restore target, the other exists
    purely so that a test can prove it was *not* touched. Depends on
    ``client_raw`` because the app's startup is what created the schema.
    """
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add_all([
            Organization(id=TARGET_ORG_ID, name="Target Org", slug="target-org"),
            Organization(id=OTHER_ORG_ID, name="Other Org", slug="other-org"),
            UserModel(
                id=ADMIN_USER_ID,
                google_sub="admin-sub",
                email="admin@example.test",
                display_name="Admin",
            ),
        ])
        await session.flush()
        session.add_all([
            OrganizationMember(id=uuid.uuid4(), organization_id=TARGET_ORG_ID,
                               user_id=ADMIN_USER_ID, role="admin"),
            OrganizationMember(id=uuid.uuid4(), organization_id=OTHER_ORG_ID,
                               user_id=ADMIN_USER_ID, role="admin"),
            ScopedControl(id=uuid.uuid4(), organization_id=TARGET_ORG_ID,
                          scf_id="TARGET-01", selected=True,
                          implementation_status="not_started"),
            ScopedControl(id=uuid.uuid4(), organization_id=OTHER_ORG_ID,
                          scf_id="OTHER-01", selected=True,
                          implementation_status="implemented"),
            EvidenceTracking(id=uuid.uuid4(), organization_id=OTHER_ORG_ID,
                             evidence_id="E-OTHER-1", is_tracked=True),
        ])
        await session.commit()
    return session_factory


@pytest.fixture(scope="function")
def client_raw(db_engine):
    """Starts the app so its lifespan builds the schema via Alembic.

    ``require_platform_admin`` is overridden rather than faked at the HTTP
    layer so the endpoint's own ``get_accessible_org_ids`` call runs for real
    against the seeded memberships — that call is what the authorisation test
    depends on.
    """
    async def _get_db():
        # Built per request, inside the app's own event loop, for the same
        # reason db_engine uses NullPool: an engine created in pytest's loop
        # cannot serve connections in TestClient's.
        engine = create_async_engine(DSN, poolclass=NullPool)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()

    def _admin():
        return User(
            user_id="admin-sub",
            email="admin@example.test",
            # auth.py:1322 does UUID(user.db_id) — it must be the string form.
            db_id=str(ADMIN_USER_ID),
            auth_method="oidc",
        )

    main.app.dependency_overrides[get_db] = _get_db
    main.app.dependency_overrides[require_platform_admin] = _admin
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def client(client_raw, seeded):
    """The client every test uses — schema built, rows seeded."""
    return client_raw


def _take_backup(client) -> dict:
    resp = client.get("/api/database/backup")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _scope_to_target_only(backup: dict) -> dict:
    """Narrow a backup so it names only TARGET_ORG.

    The seeded admin can see both orgs, so an unmodified backup covers both
    and cannot demonstrate scoping. Trimming it here is exactly the real-world
    case that used to be catastrophic: restoring a partial-scope file.
    """
    target = str(TARGET_ORG_ID)
    scoped = {"metadata": dict(backup["metadata"]), "data": {}}
    scoped["metadata"]["accessible_organizations"] = [target]
    for table, rows in backup["data"].items():
        if table == "users":
            scoped["data"][table] = rows
            continue
        kept = []
        for row in rows:
            # The organizations table keys on `id`; every other table carries
            # `organization_id`. Filtering the wrong key here would leave both
            # orgs in scope and make the scoping assertion vacuous.
            org = row.get("id") if table == "organizations" else row.get("organization_id")
            if org is None or org == target:
                kept.append(row)
        scoped["data"][table] = kept
    scoped["metadata"]["table_counts"] = {
        t: len(r) for t, r in scoped["data"].items()
    }
    return scoped


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------

def test_backup_returns_versioned_envelope(client):
    backup = _take_backup(client)
    assert backup["metadata"]["version"] == "1.1"
    assert "table_counts" in backup["metadata"]
    assert "data" in backup


def test_backup_counts_match_row_counts(client):
    """table_counts must describe data — the two drifting is how the users
    bug stayed invisible."""
    backup = _take_backup(client)
    for table, count in backup["metadata"]["table_counts"].items():
        assert count == len(backup["data"].get(table, [])), table


def test_backup_includes_members_of_accessible_orgs(client):
    """The original defect: users derived only from organization_members, so
    an empty membership table produced a backup with zero users and a restore
    that could not satisfy its own FKs."""
    backup = _take_backup(client)
    ids = {row["id"] for row in backup["data"].get("users", [])}
    assert str(ADMIN_USER_ID) in ids


# --------------------------------------------------------------------------
# Preview
# --------------------------------------------------------------------------

def test_preview_does_not_mutate(client):
    backup = _take_backup(client)
    before = _take_backup(client)["metadata"]["table_counts"]

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": False})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "preview"

    after = _take_backup(client)["metadata"]["table_counts"]
    assert after == before


def test_preview_reports_deletion_impact(client):
    """A preview that only says what will be written cannot warn about what
    will be removed — which is the half that loses data."""
    backup = _scope_to_target_only(_take_backup(client))
    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": False})
    body = resp.json()
    assert "would_delete_counts" in body
    assert "target_organizations" in body
    assert body["target_organizations"] == [str(TARGET_ORG_ID)]


# --------------------------------------------------------------------------
# Restore — blast radius
# --------------------------------------------------------------------------

def test_restore_succeeds(client):
    backup = _take_backup(client)
    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success"


@pytest.mark.asyncio
async def test_restore_leaves_other_organisation_untouched(client, seeded):
    """THE regression test. A backup scoped to one org used to wipe every org.

    Restoring a TARGET-only file must leave OTHER's control and evidence rows
    exactly as they were.
    """
    backup = _scope_to_target_only(_take_backup(client))

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 200, resp.text

    async with seeded() as session:
        controls = await session.execute(text(
            "SELECT scf_id, implementation_status FROM scoped_controls "
            "WHERE organization_id = :org"), {"org": str(OTHER_ORG_ID)})
        rows = controls.fetchall()
        assert [(r[0], r[1]) for r in rows] == [("OTHER-01", "implemented")]

        evidence = await session.execute(text(
            "SELECT count(*) FROM evidence_tracking WHERE organization_id = :org"),
            {"org": str(OTHER_ORG_ID)})
        assert evidence.scalar() == 1

        org = await session.execute(text(
            "SELECT count(*) FROM organizations WHERE id = :org"),
            {"org": str(OTHER_ORG_ID)})
        assert org.scalar() == 1


@pytest.mark.asyncio
async def test_restore_preserves_platform_admin_flag(client, seeded):
    """A backup must never be able to change who is a platform admin.

    Restore is itself platform-admin gated, so clearing the flag during a
    restore would lock the operator out with their own request; setting it
    from a hand-edited payload would be privilege escalation.
    """
    async with seeded() as session:
        await session.execute(text(
            "UPDATE users SET is_platform_admin = true WHERE id = :uid"),
            {"uid": str(ADMIN_USER_ID)})
        await session.commit()

    backup = _take_backup(client)
    for row in backup["data"]["users"]:
        row["is_platform_admin"] = False  # hostile payload

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 200, resp.text

    async with seeded() as session:
        result = await session.execute(text(
            "SELECT is_platform_admin FROM users WHERE id = :uid"),
            {"uid": str(ADMIN_USER_ID)})
        assert result.scalar() is True


def test_restore_rejects_inaccessible_organisation(client):
    """Scoping the wipe is only safe if the caller cannot choose the scope."""
    backup = _take_backup(client)
    stranger = str(uuid.uuid4())
    backup["metadata"]["accessible_organizations"] = [stranger]
    backup["data"]["organizations"] = [{
        "id": stranger,
        "name": "Not Mine",
        "slug": "not-mine",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }]

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 403, resp.text


def test_restore_rejects_unsupported_version(client):
    backup = _take_backup(client)
    backup["metadata"]["version"] = "9.9"
    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 400


# --------------------------------------------------------------------------
# Restore — atomicity
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_restore_rolls_back(client, seeded):
    """The original code committed the wipe before inserting, so any later
    failure left an empty database with no way back. One transaction means a
    failure is a no-op."""
    backup = _take_backup(client)
    before_controls = len(backup["data"]["scoped_controls"])

    # Measured, not hardcoded: the app's single-tenant bootstrap creates an
    # organisation of its own at startup, so the seeded count is not the total.
    async with seeded() as session:
        before_orgs = (await session.execute(
            text("SELECT count(*) FROM organizations"))).scalar()

    # In-scope but unwritable: a valid target organisation with a value the
    # check constraint rejects. Out-of-scope rows would not do — those are
    # filtered and reported rather than raised (see the skipped_rows test).
    backup["data"]["scoped_controls"].append({
        "id": str(uuid.uuid4()),
        "organization_id": str(TARGET_ORG_ID),
        "scf_id": "POISON-01",
        "selected": True,
        "implementation_status": "not_a_real_status",
    })

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code >= 400, "poisoned payload should not succeed"

    async with seeded() as session:
        result = await session.execute(text("SELECT count(*) FROM scoped_controls"))
        assert result.scalar() == before_controls

        orgs = await session.execute(text("SELECT count(*) FROM organizations"))
        assert orgs.scalar() == before_orgs


@pytest.mark.asyncio
async def test_out_of_scope_rows_are_dropped_and_reported(client, seeded):
    """A row naming an organisation outside the authorised scope must not be
    written — and the drop must be counted, not swallowed. Silent skipping is
    the same failure shape as the payload-length counts it replaced."""
    backup = _take_backup(client)
    stranger_org = str(uuid.uuid4())
    backup["data"]["scoped_controls"].append({
        "id": str(uuid.uuid4()),
        "organization_id": stranger_org,
        "scf_id": "SMUGGLED-01",
        "selected": True,
    })

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["skipped_rows"].get("scoped_controls") == 1

    async with seeded() as session:
        result = await session.execute(text(
            "SELECT count(*) FROM scoped_controls WHERE scf_id = 'SMUGGLED-01'"))
        assert result.scalar() == 0


@pytest.mark.asyncio
async def test_restore_round_trip_restores_mutated_value(client, seeded):
    """Backup, change something, restore, and the change is undone."""
    backup = _take_backup(client)

    async with seeded() as session:
        await session.execute(text(
            "UPDATE scoped_controls SET implementation_status = 'in_progress' "
            "WHERE organization_id = :org"), {"org": str(TARGET_ORG_ID)})
        await session.commit()

    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    assert resp.status_code == 200, resp.text

    async with seeded() as session:
        result = await session.execute(text(
            "SELECT implementation_status FROM scoped_controls "
            "WHERE organization_id = :org"), {"org": str(TARGET_ORG_ID)})
        assert result.scalar() == "not_started"


@pytest.mark.asyncio
async def test_restore_counts_come_from_the_database(client, seeded):
    """restored_counts used to be len(payload), which reports full success
    even when nothing was written — the mechanism that kept every other
    failure in this endpoint silent."""
    backup = _take_backup(client)
    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})
    body = resp.json()
    counts = body.get("upserted_counts") or body["restored_counts"]

    async with seeded() as session:
        actual = await session.execute(text("SELECT count(*) FROM scoped_controls"))
        assert counts["scoped_controls"] == actual.scalar()


# ---------------------------------------------------------------------------
# Platform-admin scoping.
#
# `auth.get_accessible_org_ids` answers "which orgs is this user a member of".
# Platform admin is a cross-organisation role that deliberately carries no
# memberships, so asking that question of a platform admin returns [] — which
# on the live dev stack produced HTTP 200 and a backup file containing nothing,
# and would have 403'd every restore they attempted. The seeded admin above is
# a *member* of both orgs, so none of the tests before this point exercise the
# platform-admin branch at all.
# ---------------------------------------------------------------------------

def _use_principal(**kwargs):
    """Swap the authenticated principal for a single test."""
    main.app.dependency_overrides[require_platform_admin] = lambda: User(**kwargs)


async def _make_platform_admin_without_memberships(seeded):
    async with seeded() as session:
        await session.execute(text(
            "UPDATE users SET is_platform_admin = true WHERE id = :uid"),
            {"uid": str(ADMIN_USER_ID)})
        await session.execute(text(
            "DELETE FROM organization_members WHERE user_id = :uid"),
            {"uid": str(ADMIN_USER_ID)})
        await session.commit()


@pytest.mark.asyncio
async def test_platform_admin_without_memberships_backs_up_every_org(client, seeded):
    """The live defect: a platform admin holds no memberships, so a
    membership-scoped backup handed back an empty file and called it success."""
    await _make_platform_admin_without_memberships(seeded)
    _use_principal(user_id="admin-sub", email="admin@example.test",
                   db_id=str(ADMIN_USER_ID), auth_method="oidc")

    backup = _take_backup(client)
    orgs = set(backup["metadata"]["accessible_organizations"])

    # Superset, not equality: the app's single-tenant bootstrap creates its own
    # organisation during lifespan startup, and a platform admin legitimately
    # sees that one too. The contract under test is "every org", not "these two".
    assert orgs >= {str(TARGET_ORG_ID), str(OTHER_ORG_ID)}, \
        "platform admin must see every organisation, not just their memberships"
    assert sum(backup["metadata"]["table_counts"].values()) > 0, \
        "a backup reporting zero records is the failure this test exists to catch"
    assert backup["data"]["scoped_controls"], "control rows must be exported"


@pytest.mark.asyncio
async def test_platform_admin_may_restore_into_org_it_does_not_belong_to(client, seeded):
    """Consequence of the above: the authorisation gate must not reject the
    very role the endpoint is restricted to."""
    await _make_platform_admin_without_memberships(seeded)
    _use_principal(user_id="admin-sub", email="admin@example.test",
                   db_id=str(ADMIN_USER_ID), auth_method="oidc")

    backup = _scope_to_target_only(_take_backup(client))
    resp = client.post("/api/database/restore",
                       json={"backup_data": backup, "confirm_clear": True})

    assert resp.status_code == 200, resp.text
    assert resp.json()["target_organizations"] == [str(TARGET_ORG_ID)]


@pytest.mark.asyncio
async def test_backup_refuses_rather_than_returning_an_empty_file(client, seeded):
    """An empty file the operator believes is a backup is worse than an error,
    because the loss is only discovered when they try to restore from it."""
    async with seeded() as session:
        await session.execute(text(
            "DELETE FROM organization_members WHERE user_id = :uid"),
            {"uid": str(ADMIN_USER_ID)})
        await session.commit()
    # Not a platform admin, and now a member of nothing: scope is genuinely empty.
    _use_principal(user_id="nobody-sub", email="nobody@example.test",
                   db_id=str(ADMIN_USER_ID), auth_method="oidc")

    resp = client.get("/api/database/backup")

    assert resp.status_code == 409, resp.text
    assert "no organisations are in scope" in resp.json()["detail"]
