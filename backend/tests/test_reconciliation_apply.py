"""Tests for the WP2c side of backend/services/reconciliation_service.py
(apply + rollback + cancel, plan §4.3, §4.7).

Repo unit-test pattern (no live database — DB access faked at the session
boundary, as in test_reconciliation_preview.py / test_catalog_apply.py). The
FakeSession here additionally understands the statements the apply path runs
through ``scoping_service.bulk_scope_frameworks``: the raw-text catalog query,
the (scf_id, selected) column select, and the bulk UPDATE — plus the two
advisory-lock text statements and object deletes.

Covered (WP2c acceptance list):
- THE ROUND-TRIP PROPERTY TEST: apply then rollback leaves every touched row
  identical modulo timestamps (byte-compare of row dicts);
- apply semantics: migrate copies assessment state onto the successor and
  demotes the old row, retire_only demotes with justification, retain leaves
  the row untouched, scope re-materialisation adds newly-active in-framework
  controls, evidence migrate is copy-and-demote, org state + snapshot + run
  ledger updates;
- never-DELETE during apply;
- CASCADE guard: engagement- or CDM-referenced run-created scoped rows (and
  task-referenced evidence rows) are demoted on rollback, never deleted;
- stale-preview refusal; frameworks-not-confirmed refusal on the first run;
- concurrency: second-active-run conflict (the service mirror of the
  catupg005 partial unique) and the advisory-lock interleave (shared catalog
  key + exclusive org key, first statements, before any read);
- migrate successor re-validation at apply time;
- rollback guards: latest-applied-only, snapshot required, live preview
  superseded, in-flight run conflict;
- cancel: previewed only.

Fixture identifiers use a letter after the hyphen (``GOV-A1``) — opaque to
the code under test.
"""
from __future__ import annotations

import copy
import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Select
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import TextClause

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import SCFCatalogControl, SCFCatalogEvidence  # noqa: E402
from models import (  # noqa: E402
    CatalogImportRun,
    CDMMapping,
    EngagementControlScope,
    EvidenceCollectionTask,
    EvidenceTracking,
    OrganizationCatalogState,
    OrganizationFrameworkSelection,
    OrganizationReconciliationRun,
    ScopedControl,
)
from services import catalog_apply as ca  # noqa: E402
from services import reconciliation_service as rs  # noqa: E402


# ---------------------------------------------------------------------------
# FakeSession (WP2b's wholesale-table fake + the bulk-scope statement shapes)
# ---------------------------------------------------------------------------

TABLES = (
    SCFCatalogControl,
    SCFCatalogEvidence,
    CatalogImportRun,
    OrganizationCatalogState,
    OrganizationFrameworkSelection,
    OrganizationReconciliationRun,
    ScopedControl,
    EvidenceTracking,
    EngagementControlScope,
    CDMMapping,
    EvidenceCollectionTask,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, rows_by_model=None):
        self.tables = {model: [] for model in TABLES}
        for model, rows in (rows_by_model or {}).items():
            self.tables[model] = list(rows)
        self._pending = []
        self.commits = 0
        self.rollbacks = 0
        self.deleted = []
        self.events = []  # lock/select/update/delete markers, in order

    def add(self, obj):
        self._pending.append(obj)

    async def flush(self):
        for obj in self._pending:
            for model in TABLES:
                if isinstance(obj, model):
                    # Python-side Column default fires at INSERT in production.
                    if getattr(obj, "id", None) is None and hasattr(type(obj), "id"):
                        obj.id = uuid.uuid4()
                    self.tables[model].append(obj)
                    break
            else:
                raise AssertionError(f"add() of unknown row type: {type(obj)}")
        self._pending = []

    async def commit(self):
        await self.flush()
        self.commits += 1
        self.events.append("commit")

    async def rollback(self):
        self._pending = []
        self.rollbacks += 1
        self.events.append("rollback")

    async def delete(self, obj):
        self.deleted.append(obj)
        self.events.append(("delete_row", type(obj).__name__))
        for model in TABLES:
            if isinstance(obj, model):
                if obj in self.tables[model]:
                    self.tables[model].remove(obj)
                return
        raise AssertionError(f"delete() of unknown row type: {type(obj)}")

    async def execute(self, stmt, params=None):
        if isinstance(stmt, TextClause):
            sql = str(stmt)
            if "pg_advisory_xact_lock_shared" in sql:
                assert params == {"key": ca.CATALOG_LOCK_KEY}
                self.events.append("lock_shared_catalog")
                return _FakeResult([])
            if "pg_advisory_xact_lock(" in sql:
                assert params["cls"] == rs.ORG_RECONCILIATION_LOCK_CLASS
                self.events.append(("lock_org", params["key"]))
                return _FakeResult([])
            if "FROM scf_catalog_controls" in sql:
                # bulk_scope's catalog query: frameworks match + active-only.
                frameworks = [
                    v for k, v in (params or {}).items() if k.startswith("fw_")
                ]
                rows = [
                    (c.scf_id,)
                    for c in self.tables[SCFCatalogControl]
                    if getattr(c, "status", "active") == "active"
                    and any(
                        fw in (getattr(c, "framework_mappings", None) or {})
                        for fw in frameworks
                    )
                ]
                self.events.append(("select_text", "scf_catalog_controls"))
                return _FakeResult(rows)
            raise AssertionError(f"unhandled text statement: {sql}")
        if isinstance(stmt, Update):
            assert stmt.table.name == "scoped_controls"
            compiled = stmt.compile().params
            in_ids = set()
            org = None
            for key, value in compiled.items():
                if key.startswith("scf_id") and isinstance(value, (list, set, tuple)):
                    in_ids = set(value)
                elif key.startswith("organization_id"):
                    org = value
            for row in self.tables[ScopedControl]:
                if row.scf_id in in_ids and (org is None or row.organization_id == org):
                    if "selected" in compiled:
                        row.selected = compiled["selected"]
                    if "selection_reason" in compiled:
                        row.selection_reason = compiled["selection_reason"]
            self.events.append(("update", "scoped_controls"))
            return _FakeResult([])
        if isinstance(stmt, Select):
            await self.flush()
            descriptions = stmt.column_descriptions
            entity = descriptions[0]["entity"]
            rows = list(self.tables[entity])
            if len(descriptions) == 1 and descriptions[0]["name"] == entity.__name__:
                self.events.append(("select", entity.__tablename__))
                return _FakeResult(rows)
            # Column select (bulk_scope's (scf_id, selected) read).
            keys = [d["name"] for d in descriptions]
            self.events.append(("select_cols", entity.__tablename__))
            return _FakeResult(
                [tuple(getattr(r, k) for k in keys) for r in rows]
            )
        raise AssertionError(f"unhandled statement: {stmt!r}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORG = uuid.uuid4()
USER = uuid.uuid4()

T0 = datetime(2026, 1, 1)
T1 = datetime(2026, 2, 1)
T2 = datetime(2026, 3, 1)

V1, V2, V3 = "2026.1", "2026.2", "2026.3"


def _catalog_control(scf_id, status="active", frameworks=None, superseded_by=None):
    return SimpleNamespace(
        scf_id=scf_id,
        control_name=f"Control {scf_id}",
        status=status,
        superseded_by=superseded_by,
        framework_mappings={fw: ["ref"] for fw in (frameworks or [])},
    )


def _catalog_evidence(evidence_id, status="active"):
    return SimpleNamespace(
        evidence_id=evidence_id, status=status, superseded_by=None
    )


def _scoped(scf_id, selected=True, impl="in_progress", maturity=None, **over):
    attrs = dict(
        id=uuid.uuid4(),
        organization_id=ORG,
        scf_id=scf_id,
        selected=selected,
        selection_reason=None,
        out_of_scope_justification=None,
        implementation_status=impl,
        priority=None,
        owner=None,
        assigned_to=None,
        maturity_level=maturity,
        target_date=None,
        completion_date=None,
        implementation_notes=None,
        related_documentation=None,
        custom_fields=None,
        assigned_user_id=None,
        owner_user_id=None,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _tracking(evidence_id, is_tracked=True, **over):
    attrs = dict(
        id=uuid.uuid4(),
        organization_id=ORG,
        evidence_id=evidence_id,
        is_tracked=is_tracked,
        method_of_collection="export script",
        collecting_system="tooling",
        owner=None,
        frequency="monthly",
        comments=None,
        maturity_level="L2",
        assigned_user_id=None,
        owner_user_id=None,
        next_collection_date=None,
        last_collection_date=None,
        system_id=None,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _org_state(version):
    return SimpleNamespace(
        organization_id=ORG,
        reconciled_catalog_version=version,
        last_reconciled_at=T0,
        last_reconciliation_run_id=None,
        created_at=T0,
        updated_at=T0,
    )


def _import_run(from_version, to_version, completed_at, status="applied"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        from_version=from_version,
        to_version=to_version,
        status=status,
        completed_at=completed_at,
        created_at=completed_at,
        diff_detail_object_key=None,
    )


DEFAULT_ACTIONS = [
    {"key": "GOV-C1", "entity": "controls", "action": "migrate",
     "justification": None, "successor_scf_id": "GOV-B1"},
    {"key": "GOV-D1", "entity": "controls", "action": "retire_only",
     "justification": "No longer applicable", "successor_scf_id": None},
    {"key": "GOV-R1", "entity": "controls", "action": "retain",
     "justification": None, "successor_scf_id": None},
    {"key": "erl-a1", "entity": "evidence", "action": "migrate",
     "justification": None, "successor_scf_id": "erl-b1"},
]


def _org_run(
    import_run,
    planned_actions=None,
    status="previewed",
    first=False,
    confirmed=False,
    from_version=V1,
    to_version=V2,
    completed_at=None,
    org_snapshot=None,
    created_at=T1,
):
    log = [{"event": "previewed", "first_reconciliation": first}]
    if confirmed:
        log.append({"event": "frameworks_confirmed", "framework_ids": ["fw_alpha"]})
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=ORG,
        from_version=from_version,
        to_version=to_version,
        catalog_import_run_id=import_run.id,
        status=status,
        diff_summary=None,
        planned_actions=(
            planned_actions if planned_actions is not None else list(DEFAULT_ACTIONS)
        ),
        org_snapshot=org_snapshot,
        actions_log=log,
        created_at=created_at,
        updated_at=created_at,
        completed_at=completed_at,
    )


def _world(first_run=False, confirmed=False, planned=None):
    """One applied platform run V1->V2 and a previewed org run against it.

    Single-org fixture ONLY: the FakeSession ignores WHERE clauses, so the
    bulk-scope statements would see other orgs' rows.
    """
    platform_run = _import_run(V1, V2, T1)
    run = _org_run(platform_run, planned, first=first_run, confirmed=confirmed)
    session = FakeSession({
        CatalogImportRun: [platform_run],
        OrganizationCatalogState: [_org_state(V1)],
        OrganizationFrameworkSelection: [
            SimpleNamespace(
                organization_id=ORG, framework_id="fw_alpha",
                source="bulk_scope", active=True,
                selected_by=None, selected_at=None,
            ),
        ],
        OrganizationReconciliationRun: [run],
        ScopedControl: [
            _scoped("GOV-A1"),  # active, already selected — untouched
            _scoped("GOV-C1", impl="implemented", maturity="managed"),  # migrate
            _scoped("GOV-D1"),  # retire_only
            _scoped("GOV-R1"),  # retain
        ],
        EvidenceTracking: [_tracking("erl-a1")],
        SCFCatalogControl: [
            _catalog_control("GOV-A1", frameworks=["fw_alpha"]),
            _catalog_control("GOV-B1", frameworks=["fw_alpha"]),  # successor
            _catalog_control("GOV-C1", status="deprecated", superseded_by="GOV-B1"),
            _catalog_control("GOV-D1", status="deprecated"),
            _catalog_control("GOV-R1", status="deprecated"),
            _catalog_control("GOV-F1", frameworks=["fw_alpha"]),  # new -> scope add
        ],
        SCFCatalogEvidence: [
            _catalog_evidence("erl-a1", status="deprecated"),
            _catalog_evidence("erl-b1"),
        ],
    })
    return session, run, platform_run


def _scoped_by_id(session):
    return {row.scf_id: row for row in session.tables[ScopedControl]}


def _tracking_by_id(session):
    return {row.evidence_id: row for row in session.tables[EvidenceTracking]}


# Byte-compare helper for the round-trip property test. Timestamps are the
# documented exception ("identical modulo timestamps", plan §4.7).
_TIMESTAMP_FIELDS = {"created_at", "updated_at", "last_reconciled_at", "selected_at"}

_IMAGED_TABLES = (
    (ScopedControl, "scf_id"),
    (EvidenceTracking, "evidence_id"),
    (OrganizationCatalogState, "organization_id"),
    (OrganizationFrameworkSelection, "framework_id"),
)


def _table_images(session):
    images = {}
    for model, sort_key in _IMAGED_TABLES:
        rows = sorted(session.tables[model], key=lambda r: str(getattr(r, sort_key)))
        images[model.__name__] = [
            {
                k: copy.deepcopy(v)
                for k, v in vars(r).items()
                if not k.startswith("_") and k not in _TIMESTAMP_FIELDS
            }
            for r in rows
        ]
    return images


# ---------------------------------------------------------------------------
# Apply — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_executes_actions_and_rematerialises_scope():
    session, run, _ = _world()
    report = await rs.apply_reconciliation_run(session, ORG, run.id, user_id=USER)

    scoped = _scoped_by_id(session)

    # migrate: successor created with the org's assessment state, old demoted.
    successor = scoped["GOV-B1"]
    assert successor.selected is True
    assert successor.implementation_status == "implemented"
    assert successor.maturity_level == "managed"
    assert "Migrated from GOV-C1" in successor.selection_reason
    old = scoped["GOV-C1"]
    assert old.selected is False
    assert "migrated to GOV-B1" in old.out_of_scope_justification

    # retire_only: demoted with the admin's justification; row NOT deleted.
    retired = scoped["GOV-D1"]
    assert retired.selected is False
    assert retired.out_of_scope_justification == "No longer applicable"

    # retain: untouched.
    assert scoped["GOV-R1"].selected is True
    assert scoped["GOV-R1"].out_of_scope_justification is None

    # scope re-materialisation: the newly-active in-framework control landed.
    assert scoped["GOV-F1"].selected is True
    assert report.scope_added == 1

    # evidence migrate: copy-and-demote.
    tracking = _tracking_by_id(session)
    assert tracking["erl-a1"].is_tracked is False
    assert tracking["erl-b1"].is_tracked is True
    assert tracking["erl-b1"].method_of_collection == "export script"

    # org state advanced and anchored to the run.
    state = session.tables[OrganizationCatalogState][0]
    assert state.reconciled_catalog_version == V2
    assert state.last_reconciliation_run_id == run.id

    # run ledger: applied, snapshot stored, log appended.
    assert run.status == "applied"
    assert run.completed_at is not None
    assert run.actions_log[-1]["event"] == "applied"
    assert run.org_snapshot is not None
    assert report.action == "applied"
    assert report.migrated == 2  # GOV-C1 + erl-a1
    assert report.retired == 1
    assert report.retained == 1
    assert session.commits == 1


@pytest.mark.asyncio
async def test_apply_snapshot_covers_exactly_the_touched_rows():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)

    snapshot = run.org_snapshot
    by_key = {
        (r["table"], tuple(sorted(r["primary_key"].items()))): r
        for r in snapshot["rows"]
    }
    keys = {
        (r["table"], r["primary_key"].get("scf_id") or r["primary_key"].get("evidence_id"))
        for r in snapshot["rows"]
    }
    # Touched: migrate pair, retired, scope add, evidence pair, org state.
    assert keys == {
        ("scoped_controls", "GOV-B1"),
        ("scoped_controls", "GOV-C1"),
        ("scoped_controls", "GOV-D1"),
        ("scoped_controls", "GOV-F1"),
        ("evidence_tracking", "erl-a1"),
        ("evidence_tracking", "erl-b1"),
        ("organization_catalog_state", None),
    }
    # Untouched rows (GOV-A1, GOV-R1) are NOT in the snapshot.
    assert len(snapshot["rows"]) == 7

    def row_for(table, key_field, key):
        return next(
            r["row"] for r in snapshot["rows"]
            if r["table"] == table and r["primary_key"].get(key_field) == key
        )

    # Run-created rows carry the empty pre-image marker.
    assert row_for("scoped_controls", "scf_id", "GOV-B1") == {}
    assert row_for("scoped_controls", "scf_id", "GOV-F1") == {}
    assert row_for("evidence_tracking", "evidence_id", "erl-b1") == {}
    # Pre-imaged rows hold the pre-apply values.
    assert row_for("scoped_controls", "scf_id", "GOV-C1")["selected"] is True
    assert row_for("evidence_tracking", "evidence_id", "erl-a1")["is_tracked"] is True
    assert row_for("scoped_controls", "scf_id", "GOV-C1")["implementation_status"] == "implemented"
    assert by_key  # silence lint on the helper dict


@pytest.mark.asyncio
async def test_apply_never_deletes_rows():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)
    assert session.deleted == []
    assert not any(
        isinstance(e, tuple) and e[0] == "delete_row" for e in session.events
    )


# ---------------------------------------------------------------------------
# THE ROUND-TRIP PROPERTY TEST (plan §4.7 flagship acceptance criterion)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip_apply_then_rollback_restores_every_row():
    session, run, _ = _world()
    before = _table_images(session)

    await rs.apply_reconciliation_run(session, ORG, run.id, user_id=USER)
    assert _table_images(session) != before  # the apply really did something

    await rs.rollback_reconciliation_run(session, ORG, run.id, user_id=USER)
    after = _table_images(session)

    # Byte-compare every row dict, modulo timestamps.
    assert after == before
    assert run.status == "rolled_back"
    assert run.actions_log[-1]["event"] == "rolled_back"


@pytest.mark.asyncio
async def test_round_trip_with_no_active_frameworks_still_round_trips():
    # No scope re-materialisation branch: actions only.
    session, run, _ = _world()
    session.tables[OrganizationFrameworkSelection][0].active = False
    before = _table_images(session)

    report = await rs.apply_reconciliation_run(session, ORG, run.id)
    assert report.scope_added == 0
    scoped = _scoped_by_id(session)
    assert "GOV-F1" not in scoped  # nothing re-materialised

    await rs.rollback_reconciliation_run(session, ORG, run.id)
    assert _table_images(session) == before


# ---------------------------------------------------------------------------
# CASCADE guard (plan §4.8: delete only if unreferenced — absolute rule)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_demotes_engagement_referenced_created_rows():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)

    successor = _scoped_by_id(session)["GOV-B1"]
    created_tracking = _tracking_by_id(session)["erl-b1"]
    # An engagement materialised scope against the run-created successor, and
    # a collection task landed on the run-created tracking row.
    session.tables[EngagementControlScope].append(
        SimpleNamespace(
            id=uuid.uuid4(), engagement_id=uuid.uuid4(),
            scf_id="GOV-B1", scoped_control_id=successor.id,
        )
    )
    session.tables[EvidenceCollectionTask].append(
        SimpleNamespace(id=uuid.uuid4(), evidence_tracking_id=created_tracking.id)
    )

    report = await rs.rollback_reconciliation_run(session, ORG, run.id)

    scoped = _scoped_by_id(session)
    # Referenced successor row survives, demoted — the engagement CASCADE is
    # never triggered.
    assert "GOV-B1" in scoped
    assert scoped["GOV-B1"].selected is False
    assert "retained" in scoped["GOV-B1"].out_of_scope_justification
    assert successor not in session.deleted
    # Referenced tracking row survives, demoted.
    tracking = _tracking_by_id(session)
    assert "erl-b1" in tracking
    assert tracking["erl-b1"].is_tracked is False
    # The unreferenced run-created row (GOV-F1) WAS deleted.
    assert "GOV-F1" not in scoped
    assert report.demoted == 2
    # Everything pre-imaged still restored.
    assert scoped["GOV-C1"].selected is True


@pytest.mark.asyncio
async def test_rollback_demotes_cdm_referenced_created_rows():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)

    successor = _scoped_by_id(session)["GOV-B1"]
    session.tables[CDMMapping].append(
        SimpleNamespace(
            id=uuid.uuid4(), organization_id=ORG, scoped_control_id=successor.id
        )
    )
    await rs.rollback_reconciliation_run(session, ORG, run.id)
    scoped = _scoped_by_id(session)
    assert "GOV-B1" in scoped and scoped["GOV-B1"].selected is False


# ---------------------------------------------------------------------------
# Apply guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_refuses_stale_preview():
    session, run, _ = _world()
    # A newer platform run applied after the preview was anchored.
    session.tables[CatalogImportRun].append(_import_run(V2, V3, T2))
    with pytest.raises(rs.StalePreviewError):
        await rs.apply_reconciliation_run(session, ORG, run.id)
    assert run.status == "previewed"
    assert session.commits == 0
    assert _scoped_by_id(session)["GOV-C1"].selected is True  # untouched


@pytest.mark.asyncio
async def test_apply_refuses_unconfirmed_frameworks_on_first_run():
    session, run, _ = _world(first_run=True, confirmed=False)
    with pytest.raises(rs.FrameworksNotConfirmedError):
        await rs.apply_reconciliation_run(session, ORG, run.id)
    assert session.commits == 0

    # With the confirmation recorded on the run, the same world applies.
    session2, run2, _ = _world(first_run=True, confirmed=True)
    report = await rs.apply_reconciliation_run(session2, ORG, run2.id)
    assert report.action == "applied"


@pytest.mark.asyncio
async def test_apply_refuses_second_active_run():
    # Service-level mirror of the catupg005 partial unique: a second active
    # run for the org is a conflict (the API maps this to 409).
    session, run, platform_run = _world()
    session.tables[OrganizationReconciliationRun].append(
        _org_run(platform_run, status="applying")
    )
    with pytest.raises(rs.ActiveRunConflictError):
        await rs.apply_reconciliation_run(session, ORG, run.id)
    assert session.commits == 0


@pytest.mark.asyncio
async def test_apply_refuses_wrong_run_state_and_version_mismatch():
    session, run, _ = _world()
    with pytest.raises(rs.RunNotFoundError):
        await rs.check_apply_preflight(session, ORG, uuid.uuid4())
    with pytest.raises(rs.RunStateError, match="expected_to_version"):
        await rs.check_apply_preflight(
            session, ORG, run.id, expected_to_version=V3
        )
    # Happy preflight returns the run.
    assert await rs.check_apply_preflight(
        session, ORG, run.id, expected_to_version=V2
    ) is run

    run.status = "applied"
    with pytest.raises(rs.RunStateError):
        await rs.apply_reconciliation_run(session, ORG, run.id)


@pytest.mark.asyncio
async def test_apply_revalidates_migrate_successor():
    session, run, _ = _world()
    # The successor was deprecated after the actions PUT validated it.
    next(
        c for c in session.tables[SCFCatalogControl] if c.scf_id == "GOV-B1"
    ).status = "deprecated"
    with pytest.raises(rs.ActionValidationError, match="not active"):
        await rs.apply_reconciliation_run(session, ORG, run.id)
    assert session.commits == 0
    assert _scoped_by_id(session)["GOV-C1"].selected is True  # nothing mutated


# ---------------------------------------------------------------------------
# Advisory-lock interleave (test_catalog_apply.py's lock-test approach)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_takes_both_locks_first():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)
    # Shared catalog key then exclusive org key, before ANY read/write.
    assert session.events[0] == "lock_shared_catalog"
    assert session.events[1] == ("lock_org", rs.org_lock_key(ORG))
    first_nonlock = session.events[2]
    assert first_nonlock[0] in ("select", "select_cols", "select_text")


@pytest.mark.asyncio
async def test_rollback_takes_both_locks_first():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)
    session.events.clear()
    await rs.rollback_reconciliation_run(session, ORG, run.id)
    assert session.events[0] == "lock_shared_catalog"
    assert session.events[1] == ("lock_org", rs.org_lock_key(ORG))


def test_org_lock_key_is_positive_int32_and_stable():
    key = rs.org_lock_key(ORG)
    assert 0 <= key <= 0x7FFF_FFFF
    assert key == rs.org_lock_key(ORG)
    # The shared key is catalog_apply's, verbatim — one lock space.
    assert rs.CATALOG_LOCK_KEY == ca.CATALOG_LOCK_KEY


# ---------------------------------------------------------------------------
# Rollback guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rollback_only_latest_applied_run():
    platform_run = _import_run(V1, V2, T1)
    older = _org_run(
        platform_run, status="applied", completed_at=T1,
        org_snapshot={"captured_at": T1.isoformat(), "rows": []},
    )
    newer = _org_run(
        platform_run, status="applied", from_version=V2, to_version=V3,
        completed_at=T2, created_at=T2,
        org_snapshot={"captured_at": T2.isoformat(), "rows": []},
    )
    session = FakeSession({
        CatalogImportRun: [platform_run],
        OrganizationReconciliationRun: [older, newer],
    })
    with pytest.raises(rs.RollbackNotLatestError):
        await rs.rollback_reconciliation_run(session, ORG, older.id)
    # The latest one is allowed.
    report = await rs.rollback_reconciliation_run(session, ORG, newer.id)
    assert report.action == "rolled_back"


@pytest.mark.asyncio
async def test_rollback_requires_snapshot():
    platform_run = _import_run(V1, V2, T1)
    run = _org_run(platform_run, status="applied", completed_at=T1, org_snapshot=None)
    session = FakeSession({
        CatalogImportRun: [platform_run],
        OrganizationReconciliationRun: [run],
    })
    with pytest.raises(rs.SnapshotUnavailableError):
        await rs.rollback_reconciliation_run(session, ORG, run.id)
    assert run.status == "applied"


@pytest.mark.asyncio
async def test_rollback_supersedes_preview_but_refuses_inflight():
    session, run, platform_run = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)

    live_preview = _org_run(platform_run, status="previewed", created_at=T2)
    session.tables[OrganizationReconciliationRun].append(live_preview)
    await rs.rollback_reconciliation_run(session, ORG, run.id)
    assert live_preview.status == "cancelled"  # invalidated by the rollback

    session2, run2, platform_run2 = _world()
    await rs.apply_reconciliation_run(session2, ORG, run2.id)
    session2.tables[OrganizationReconciliationRun].append(
        _org_run(platform_run2, status="applying", created_at=T2)
    )
    with pytest.raises(rs.ActiveRunConflictError):
        await rs.rollback_reconciliation_run(session2, ORG, run2.id)


@pytest.mark.asyncio
async def test_rollback_refuses_wrong_state():
    session, run, _ = _world()
    with pytest.raises(rs.RunStateError):
        await rs.rollback_reconciliation_run(session, ORG, run.id)  # previewed
    with pytest.raises(rs.RunNotFoundError):
        await rs.check_rollback_preflight(session, ORG, uuid.uuid4())


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_previewed_run():
    session, run, _ = _world()
    cancelled = await rs.cancel_reconciliation_run(
        session, ORG, run.id, user_id=USER
    )
    assert cancelled is run
    assert run.status == "cancelled"
    assert run.actions_log[-1]["event"] == "cancelled"
    assert run.actions_log[-1]["by"] == str(USER)
    assert session.commits == 1


@pytest.mark.asyncio
async def test_cancel_refuses_non_previewed_run():
    session, run, _ = _world()
    await rs.apply_reconciliation_run(session, ORG, run.id)
    with pytest.raises(rs.RunStateError):
        await rs.cancel_reconciliation_run(session, ORG, run.id)
    with pytest.raises(rs.RunNotFoundError):
        await rs.cancel_reconciliation_run(session, ORG, uuid.uuid4())
