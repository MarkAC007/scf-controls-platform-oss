"""Tests for backend/services/reconciliation_service.py (WP2b, plan §4.3, §4.7).

Repo unit-test pattern (no live database — DB access faked at the session
boundary, as in test_catalog_apply.py). The service deliberately selects whole
model rows and filters in Python, so the FakeSession here returns tables
wholesale and never interprets WHERE clauses.

Covered (WP2b acceptance list):
- skip-version diff union: later-changes-win, add-then-deprecate collapse,
  deprecate-then-resurrect collapse, stacked-run fixtures (two+ applied runs);
- eligibility: behind / up-to-date / empty ledger, first-reconciliation flag,
  stale-preview detection;
- preview branches (a)–(e): additions vs active framework selections,
  deprecated impacts with migrate/retain defaults per the frozen PlannedAction
  contract, changed-in-scope with composite-driven reassessment flags, the
  report-only orphan report, and the first-run framework confirmation;
- preview run persistence (previewed status, planned-action defaults,
  diff_summary counts, staleness anchor) and previewed-run supersession vs
  in-flight-run conflict;
- actions PUT validation: exact key coverage, migrate successor rules,
  retire_only justification, first-run confirmed_framework_ids requirement and
  its selection-row effects, run-state and not-found errors;
- org changelog assembly from applied runs + ledger diff details, pagination.

Fixture identifiers use a letter after the hyphen (``GOV-A1``) — opaque to the
code under test.
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Select

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import SCFCatalogControl, SCFCatalogEvidence  # noqa: E402
from models import (  # noqa: E402
    CatalogImportRun,
    ControlAssessmentComposite,
    EvidenceTracking,
    OrganizationCatalogState,
    OrganizationFrameworkSelection,
    OrganizationReconciliationRun,
    ScopedControl,
)
from schemas_catalog_upgrade import (  # noqa: E402
    AddedEntity,
    CatalogEntityType,
    ChangedEntity,
    DeprecatedEntity,
    DiffDetail,
    EntityDiff,
    FieldChange,
    PlannedAction,
    PlannedActionType,
    ResurrectedEntity,
)
from services import reconciliation_service as rs  # noqa: E402


# ---------------------------------------------------------------------------
# FakeSession (tables wholesale; the service filters in Python)
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
    ControlAssessmentComposite,
)


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, rows_by_model=None):
        self.tables = {model: [] for model in TABLES}
        for model, rows in (rows_by_model or {}).items():
            self.tables[model] = list(rows)
        self._pending = []
        self.commits = 0

    def add(self, obj):
        self._pending.append(obj)

    async def flush(self):
        for obj in self._pending:
            for model in TABLES:
                if isinstance(obj, model):
                    self.tables[model].append(obj)
                    break
            else:
                raise AssertionError(f"add() of unknown row type: {type(obj)}")
        self._pending = []

    async def commit(self):
        await self.flush()
        self.commits += 1

    async def rollback(self):
        self._pending = []

    async def execute(self, stmt, params=None):
        assert isinstance(stmt, Select), f"unhandled statement: {stmt!r}"
        await self.flush()
        entity = stmt.column_descriptions[0]["entity"]
        return _FakeResult(self.tables[entity])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORG = uuid.uuid4()
OTHER_ORG = uuid.uuid4()
USER = uuid.uuid4()

T0 = datetime(2026, 1, 1)
T1 = datetime(2026, 2, 1)
T2 = datetime(2026, 3, 1)

V1, V2, V3 = "2026.1", "2026.2", "2026.3"

CONTROLS = CatalogEntityType.CONTROLS
EVIDENCE = CatalogEntityType.EVIDENCE


def _catalog_control(scf_id, name=None, status="active", superseded_by=None):
    return SimpleNamespace(
        scf_id=scf_id,
        control_name=name or f"Control {scf_id}",
        status=status,
        superseded_by=superseded_by,
    )


def _catalog_evidence(evidence_id, status="active", superseded_by=None):
    return SimpleNamespace(
        evidence_id=evidence_id, status=status, superseded_by=superseded_by
    )


def _scoped(scf_id, selected=True, impl="in_progress", maturity=None, org=ORG):
    return SimpleNamespace(
        organization_id=org,
        scf_id=scf_id,
        selected=selected,
        implementation_status=impl,
        maturity_level=maturity,
    )


def _tracking(evidence_id, is_tracked=True, org=ORG):
    return SimpleNamespace(
        organization_id=org, evidence_id=evidence_id, is_tracked=is_tracked
    )


def _composite(scf_id, org=ORG):
    return SimpleNamespace(organization_id=org, scf_id=scf_id)


def _selection(framework_id, source="bulk_scope", active=True, org=ORG):
    return SimpleNamespace(
        organization_id=org,
        framework_id=framework_id,
        source=source,
        active=active,
        selected_by=None,
        selected_at=None,
    )


def _org_state(version, last_reconciled_at=T0, org=ORG):
    return SimpleNamespace(
        organization_id=org,
        reconciled_catalog_version=version,
        last_reconciled_at=last_reconciled_at,
        last_reconciliation_run_id=None,
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


def _org_run(
    status="previewed",
    from_version=V1,
    to_version=V2,
    import_run_id=None,
    planned_actions=None,
    actions_log=None,
    created_at=T1,
    completed_at=None,
    org=ORG,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=org,
        from_version=from_version,
        to_version=to_version,
        catalog_import_run_id=import_run_id or uuid.uuid4(),
        status=status,
        diff_summary=None,
        planned_actions=planned_actions or [],
        org_snapshot=None,
        actions_log=actions_log if actions_log is not None else [
            {"event": "previewed", "first_reconciliation": False}
        ],
        created_at=created_at,
        updated_at=created_at,
        completed_at=completed_at,
    )


def _controls_detail(from_version, to_version, **kwargs):
    return DiffDetail(
        from_version=from_version,
        to_version=to_version,
        entities={CONTROLS: EntityDiff(**kwargs)},
    )


def _loader(details_by_run_id):
    def load(run):
        return details_by_run_id[run.id]

    return load


# ---------------------------------------------------------------------------
# Skip-version diff union (pure function)
# ---------------------------------------------------------------------------


def test_union_later_change_wins():
    d1 = _controls_detail(V1, V2, changed=[
        ChangedEntity(key="GOV-A1", fields={"control_name": FieldChange(old="First", new="Second")}),
    ])
    d2 = _controls_detail(V2, V3, changed=[
        ChangedEntity(key="GOV-A1", fields={"control_name": FieldChange(old="Second", new="Third")}),
    ])
    union = rs.union_diff_details([d1, d2])
    diff = union.entities[CONTROLS]
    assert union.from_version == V1 and union.to_version == V3
    assert len(diff.changed) == 1
    fc = diff.changed[0].fields["control_name"]
    assert fc.old == "First" and fc.new == "Third"


def test_union_add_then_deprecate_collapses_to_deprecated():
    d1 = _controls_detail(V1, V2, added=[
        AddedEntity(key="GOV-B1", data={"control_name": "Shortlived"}),
    ])
    d2 = _controls_detail(V2, V3, deprecated=[
        DeprecatedEntity(key="GOV-B1", superseded_by="GOV-C1"),
    ])
    diff = rs.union_diff_details([d1, d2]).entities[CONTROLS]
    assert not diff.added
    assert [d.key for d in diff.deprecated] == ["GOV-B1"]
    assert diff.deprecated[0].superseded_by == "GOV-C1"


def test_union_deprecate_then_resurrect_collapses():
    # Round trip with a surviving field delta -> net changed.
    d1 = _controls_detail(V1, V2, deprecated=[DeprecatedEntity(key="GOV-A1")])
    d2 = _controls_detail(V2, V3, resurrected=[
        ResurrectedEntity(key="GOV-A1", fields={"control_name": FieldChange(old="Old", new="New")}),
    ])
    diff = rs.union_diff_details([d1, d2]).entities[CONTROLS]
    assert not diff.deprecated and not diff.resurrected
    assert [c.key for c in diff.changed] == ["GOV-A1"]

    # Clean round trip -> net unchanged.
    d2_clean = _controls_detail(V2, V3, resurrected=[ResurrectedEntity(key="GOV-A1")])
    diff = rs.union_diff_details([d1, d2_clean]).entities[CONTROLS]
    assert not diff.deprecated and not diff.resurrected and not diff.changed
    assert diff.unchanged == ["GOV-A1"]


def test_union_added_then_changed_stays_added_with_latest_data():
    d1 = _controls_detail(V1, V2, added=[
        AddedEntity(key="GOV-B1", data={"control_name": "Initial", "scf_domain": "Governance"}),
    ])
    d2 = _controls_detail(V2, V3, changed=[
        ChangedEntity(key="GOV-B1", fields={"control_name": FieldChange(old="Initial", new="Renamed")}),
    ])
    diff = rs.union_diff_details([d1, d2]).entities[CONTROLS]
    assert not diff.changed
    assert [a.key for a in diff.added] == ["GOV-B1"]
    assert diff.added[0].data["control_name"] == "Renamed"
    assert diff.added[0].data["scf_domain"] == "Governance"


def test_union_change_then_deprecate_is_deprecated():
    d1 = _controls_detail(V1, V2, changed=[
        ChangedEntity(key="GOV-A1", fields={"control_name": FieldChange(old="A", new="B")}),
    ])
    d2 = _controls_detail(V2, V3, deprecated=[DeprecatedEntity(key="GOV-A1")])
    diff = rs.union_diff_details([d1, d2]).entities[CONTROLS]
    assert not diff.changed
    assert [d.key for d in diff.deprecated] == ["GOV-A1"]


def test_union_unchanged_bookkeeping():
    d1 = _controls_detail(V1, V2, unchanged=["GOV-A1", "GOV-A2"], changed=[
        ChangedEntity(key="GOV-A3", fields={"control_name": FieldChange(old="X", new="Y")}),
    ])
    d2 = _controls_detail(V2, V3, unchanged=["GOV-A1", "GOV-A3"], changed=[
        ChangedEntity(key="GOV-A2", fields={"control_name": FieldChange(old="P", new="Q")}),
    ])
    diff = rs.union_diff_details([d1, d2]).entities[CONTROLS]
    assert sorted(c.key for c in diff.changed) == ["GOV-A2", "GOV-A3"]
    assert diff.unchanged == ["GOV-A1"]


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_eligibility_org_behind_is_eligible():
    session = FakeSession({
        CatalogImportRun: [_import_run(V1, V2, T1)],
        OrganizationCatalogState: [_org_state(V1)],
    })
    info = await rs.check_eligibility(session, ORG)
    assert info.eligible is True
    assert info.platform_catalog_version == V2
    assert info.reconciled_catalog_version == V1
    assert info.first_reconciliation is False


@pytest.mark.asyncio
async def test_eligibility_up_to_date_and_empty_ledger():
    session = FakeSession({
        CatalogImportRun: [_import_run(V1, V2, T1)],
        OrganizationCatalogState: [_org_state(V2)],
    })
    info = await rs.check_eligibility(session, ORG)
    assert info.eligible is False

    empty = FakeSession({OrganizationCatalogState: [_org_state(V1)]})
    info = await rs.check_eligibility(empty, ORG)
    assert info.eligible is False
    assert info.platform_catalog_version is None


@pytest.mark.asyncio
async def test_eligibility_first_reconciliation_flag():
    ledger = [_import_run(V1, V2, T1)]
    # No state row at all.
    info = await rs.check_eligibility(FakeSession({CatalogImportRun: ledger}), ORG)
    assert info.first_reconciliation is True
    # Backfilled state row that has never actually reconciled.
    info = await rs.check_eligibility(
        FakeSession({
            CatalogImportRun: ledger,
            OrganizationCatalogState: [_org_state(V1, last_reconciled_at=None)],
        }),
        ORG,
    )
    assert info.first_reconciliation is True


@pytest.mark.asyncio
async def test_eligibility_stale_preview_detection():
    old_run = _import_run(V1, V2, T1)
    new_run = _import_run(V2, V3, T2)
    stale = _org_run(status="previewed", import_run_id=old_run.id)
    session = FakeSession({
        CatalogImportRun: [old_run, new_run],
        OrganizationCatalogState: [_org_state(V1)],
        OrganizationReconciliationRun: [stale],
    })
    info = await rs.check_eligibility(session, ORG)
    assert info.stale_preview is True
    assert info.active_run is stale

    fresh = FakeSession({
        CatalogImportRun: [old_run, new_run],
        OrganizationCatalogState: [_org_state(V1)],
        OrganizationReconciliationRun: [
            _org_run(status="previewed", import_run_id=new_run.id)
        ],
    })
    info = await rs.check_eligibility(fresh, ORG)
    assert info.stale_preview is False


# ---------------------------------------------------------------------------
# Preview — shared fixture builder
# ---------------------------------------------------------------------------


def _preview_world(first_run=False):
    """One applied platform run V1->V2 with all five branch triggers."""
    run = _import_run(V1, V2, T1)
    detail = DiffDetail(
        from_version=V1,
        to_version=V2,
        entities={
            CONTROLS: EntityDiff(
                added=[
                    AddedEntity(key="GOV-F1", name="In scope add",
                                data={"framework_mappings": {"fw_alpha": ["ref"]}}),
                    AddedEntity(key="GOV-G1", name="Out of scope add",
                                data={"framework_mappings": {"fw_gamma": ["ref"]}}),
                ],
                changed=[
                    ChangedEntity(key="GOV-A1", name="Changed sel",
                                  fields={"control_name": FieldChange(old="A", new="B")}),
                    ChangedEntity(key="GOV-A2", name="Changed unsel",
                                  fields={"control_name": FieldChange(old="C", new="D")}),
                ],
                deprecated=[
                    DeprecatedEntity(key="GOV-C1", name="Dep with successor"),
                    DeprecatedEntity(key="GOV-D1", name="Dep no successor"),
                    DeprecatedEntity(key="GOV-E1", name="Dep no org data"),
                ],
            ),
            EVIDENCE: EntityDiff(
                deprecated=[DeprecatedEntity(key="erl-a1", name="Dep evidence")],
            ),
        },
    )
    state_rows = [] if first_run else [_org_state(V1)]
    session = FakeSession({
        CatalogImportRun: [run],
        OrganizationCatalogState: state_rows,
        OrganizationFrameworkSelection: [
            _selection("fw_alpha", source="backfill" if first_run else "bulk_scope"),
            _selection("fw_beta", source="backfill", active=first_run),
        ],
        ScopedControl: [
            _scoped("GOV-A1", selected=True),
            _scoped("GOV-A2", selected=False),
            _scoped("GOV-C1", selected=True, impl="implemented"),
            _scoped("GOV-D1", selected=True),
            _scoped("OLD-Z9", selected=True),  # orphan: not in catalog
            _scoped("GOV-A1", org=OTHER_ORG),  # other org's row must be ignored
        ],
        EvidenceTracking: [_tracking("erl-a1"), _tracking("erl-z9", is_tracked=False)],
        ControlAssessmentComposite: [_composite("GOV-A1")],
        SCFCatalogControl: [
            _catalog_control("GOV-A1"),
            _catalog_control("GOV-A2"),
            _catalog_control("GOV-B1"),  # active successor target
            _catalog_control("GOV-C1", status="deprecated", superseded_by="GOV-B1"),
            _catalog_control("GOV-D1", status="deprecated"),
            _catalog_control("GOV-E1", status="deprecated"),
            _catalog_control("GOV-F1"),
            _catalog_control("GOV-G1"),
        ],
        SCFCatalogEvidence: [
            _catalog_evidence("erl-a1", status="deprecated"),
            _catalog_evidence("erl-z9"),
        ],
    })
    return session, run, {run.id: detail}


@pytest.mark.asyncio
async def test_preview_branch_a_additions():
    session, _, details = _preview_world()
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    assert [i.scf_id for i in result.additions.in_scope] == ["GOV-F1"]
    assert result.additions.in_scope[0].frameworks == ["fw_alpha"]
    assert result.additions.out_of_scope_count == 1


@pytest.mark.asyncio
async def test_preview_branch_b_deprecated_defaults():
    session, _, details = _preview_world()
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    by_key = {i.key: i for i in result.deprecated_impacts}
    # Org has no data on the third deprecated control -> count-only, not listed.
    assert set(by_key) == {"GOV-C1", "GOV-D1", "erl-a1"}

    # Successor resolved from the live catalog row (platform pairing) -> migrate.
    migrate = by_key["GOV-C1"]
    assert migrate.superseded_by == "GOV-B1"
    assert migrate.suggested_action == PlannedActionType.MIGRATE
    assert migrate.planned_action.successor_scf_id == "GOV-B1"
    assert migrate.data_summary["implementation_status"] == "implemented"

    # No successor anywhere -> retain default (safe mid-engagement).
    retain = by_key["GOV-D1"]
    assert retain.suggested_action == PlannedActionType.RETAIN
    assert retain.planned_action.successor_scf_id is None

    evidence = by_key["erl-a1"]
    assert evidence.entity == EVIDENCE
    assert evidence.data_summary == {"tracking_rows": 1, "is_tracked": True}


@pytest.mark.asyncio
async def test_preview_branch_c_changed_in_scope():
    session, _, details = _preview_world()
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    # GOV-A2 is scoped but selected=False -> excluded.
    assert [c.scf_id for c in result.changed_in_scope] == ["GOV-A1"]
    item = result.changed_in_scope[0]
    assert item.reassessment_recommended is True  # composite exists
    assert item.fields["control_name"].new == "B"


@pytest.mark.asyncio
async def test_preview_branch_d_orphans_report_only():
    session, _, details = _preview_world()
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    orphan_keys = {(o.source_table, o.key) for o in result.orphans.items}
    assert orphan_keys == {("scoped_controls", "OLD-Z9")}
    assert result.orphans.count == 1
    # Report-only: the preview run was still created.
    assert result.run.status == "previewed"


@pytest.mark.asyncio
async def test_preview_branch_e_framework_confirmation():
    session, _, details = _preview_world(first_run=True)
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    fc = result.framework_confirmation
    assert fc.required is True
    assert [(s.framework_id, s.active) for s in fc.selections] == [
        ("fw_alpha", True), ("fw_beta", True),
    ]

    session, _, details = _preview_world(first_run=False)
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    assert result.framework_confirmation.required is False


@pytest.mark.asyncio
async def test_preview_persists_run():
    session, platform_run, details = _preview_world()
    result = await rs.build_preview(
        session, ORG, user_id=USER, detail_loader=_loader(details)
    )
    stored = session.tables[OrganizationReconciliationRun]
    assert stored == [result.run]
    run = result.run
    assert run.status == "previewed"
    assert run.organization_id == ORG
    assert run.from_version == V1 and run.to_version == V2
    assert run.catalog_import_run_id == platform_run.id  # staleness anchor
    assert session.commits == 1

    actions = [PlannedAction.model_validate(a) for a in run.planned_actions]
    assert {(a.entity, a.key) for a in actions} == {
        (CONTROLS, "GOV-C1"), (CONTROLS, "GOV-D1"), (EVIDENCE, "erl-a1"),
    }
    counts = run.diff_summary["entities"]["controls"]
    assert counts["added"] == 2 and counts["changed"] == 2 and counts["deprecated"] == 3
    assert run.actions_log[0]["event"] == "previewed"
    assert run.actions_log[0]["by"] == str(USER)


@pytest.mark.asyncio
async def test_preview_supersedes_previewed_but_conflicts_with_applying():
    session, platform_run, details = _preview_world()
    stale = _org_run(status="previewed", import_run_id=uuid.uuid4())
    session.tables[OrganizationReconciliationRun] = [stale]
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    assert stale.status == "cancelled"
    assert result.run.status == "previewed"

    session2, _, details2 = _preview_world()
    applying = _org_run(status="applying", import_run_id=platform_run.id)
    session2.tables[OrganizationReconciliationRun] = [applying]
    with pytest.raises(rs.ActiveRunConflictError):
        await rs.build_preview(session2, ORG, detail_loader=_loader(details2))
    assert applying.status == "applying"


@pytest.mark.asyncio
async def test_preview_refuses_ineligible_org():
    session, _, details = _preview_world()
    session.tables[OrganizationCatalogState] = [_org_state(V2)]
    with pytest.raises(rs.NotEligibleError):
        await rs.build_preview(session, ORG, detail_loader=_loader(details))

    empty = FakeSession()
    with pytest.raises(rs.NotEligibleError):
        await rs.build_preview(empty, ORG, detail_loader=_loader({}))


@pytest.mark.asyncio
async def test_preview_skip_version_unions_stacked_runs():
    run1 = _import_run(V1, V2, T1)
    run2 = _import_run(V2, V3, T2)
    details = {
        run1.id: _controls_detail(V1, V2, added=[
            AddedEntity(key="GOV-F1", data={"framework_mappings": {"fw_alpha": ["r"]}}),
        ]),
        run2.id: _controls_detail(V2, V3, changed=[
            ChangedEntity(key="GOV-F1",
                          fields={"control_name": FieldChange(old="X", new="Y")}),
        ], deprecated=[DeprecatedEntity(key="GOV-A1")]),
    }
    session = FakeSession({
        CatalogImportRun: [run1, run2],
        OrganizationCatalogState: [_org_state(V1)],
        OrganizationFrameworkSelection: [_selection("fw_alpha")],
        ScopedControl: [_scoped("GOV-A1")],
        SCFCatalogControl: [_catalog_control("GOV-A1", status="deprecated"),
                            _catalog_control("GOV-F1")],
    })
    result = await rs.build_preview(session, ORG, detail_loader=_loader(details))
    run = result.run
    # Two runs behind: union spans both, anchored to the latest.
    assert run.from_version == V1 and run.to_version == V3
    assert run.catalog_import_run_id == run2.id
    # Added-then-changed collapsed into a single addition with the latest name.
    assert [i.scf_id for i in result.additions.in_scope] == ["GOV-F1"]
    assert [i.key for i in result.deprecated_impacts] == ["GOV-A1"]

    # Org already at V2 only unions the later run.
    session2 = FakeSession({
        CatalogImportRun: [run1, run2],
        OrganizationCatalogState: [_org_state(V2)],
        OrganizationFrameworkSelection: [_selection("fw_alpha")],
        ScopedControl: [_scoped("GOV-A1")],
        SCFCatalogControl: [_catalog_control("GOV-A1", status="deprecated"),
                            _catalog_control("GOV-F1")],
    })
    result2 = await rs.build_preview(session2, ORG, detail_loader=_loader(details))
    assert result2.run.from_version == V2 and result2.run.to_version == V3
    assert result2.additions.in_scope == []  # GOV-F1 added before V2, not in window


# ---------------------------------------------------------------------------
# Actions PUT
# ---------------------------------------------------------------------------


def _actions_world(first_run=False, planned=None):
    run = _org_run(
        status="previewed",
        planned_actions=planned if planned is not None else [
            {"key": "GOV-C1", "entity": "controls", "action": "migrate",
             "justification": None, "successor_scf_id": "GOV-B1"},
            {"key": "GOV-D1", "entity": "controls", "action": "retain",
             "justification": None, "successor_scf_id": None},
        ],
        actions_log=[{"event": "previewed", "first_reconciliation": first_run}],
    )
    session = FakeSession({
        OrganizationReconciliationRun: [run],
        SCFCatalogControl: [
            _catalog_control("GOV-B1"),
            _catalog_control("GOV-X1", status="deprecated"),
        ],
        OrganizationFrameworkSelection: [
            _selection("fw_alpha", source="backfill"),
            _selection("fw_beta", source="backfill"),
        ],
    })
    return session, run


def _action(key, action, successor=None, justification=None):
    return PlannedAction(
        key=key, entity=CONTROLS, action=action,
        successor_scf_id=successor, justification=justification,
    )


@pytest.mark.asyncio
async def test_actions_put_replaces_and_logs():
    session, run = _actions_world()
    updated = await rs.update_planned_actions(
        session, ORG, run.id,
        [
            _action("GOV-C1", PlannedActionType.MIGRATE, successor="GOV-B1"),
            _action("GOV-D1", PlannedActionType.RETIRE_ONLY,
                    justification="No longer applicable"),
        ],
        user_id=USER,
    )
    actions = [PlannedAction.model_validate(a) for a in updated.planned_actions]
    assert {a.key: a.action for a in actions} == {
        "GOV-C1": PlannedActionType.MIGRATE,
        "GOV-D1": PlannedActionType.RETIRE_ONLY,
    }
    assert updated.actions_log[-1]["event"] == "actions_updated"
    assert session.commits == 1


@pytest.mark.asyncio
async def test_actions_put_rejects_unknown_missing_duplicate_keys():
    session, run = _actions_world()
    with pytest.raises(rs.ActionValidationError) as exc:
        await rs.update_planned_actions(
            session, ORG, run.id,
            [
                _action("GOV-C1", PlannedActionType.RETAIN),
                _action("GOV-C1", PlannedActionType.RETAIN),
                _action("GOV-Z9", PlannedActionType.RETAIN),
            ],
        )
    joined = "; ".join(exc.value.errors)
    assert "duplicate" in joined
    assert "GOV-Z9" in joined
    assert "missing action for controls GOV-D1" in joined
    assert session.commits == 0


@pytest.mark.asyncio
async def test_actions_put_migrate_successor_rules():
    session, run = _actions_world()
    base = [_action("GOV-D1", PlannedActionType.RETAIN)]
    # No successor at all.
    with pytest.raises(rs.ActionValidationError, match="requires successor"):
        await rs.update_planned_actions(
            session, ORG, run.id, base + [_action("GOV-C1", PlannedActionType.MIGRATE)]
        )
    # Successor missing from the catalog.
    with pytest.raises(rs.ActionValidationError, match="does not exist"):
        await rs.update_planned_actions(
            session, ORG, run.id,
            base + [_action("GOV-C1", PlannedActionType.MIGRATE, successor="GOV-Z9")],
        )
    # Successor present but deprecated.
    with pytest.raises(rs.ActionValidationError, match="not active"):
        await rs.update_planned_actions(
            session, ORG, run.id,
            base + [_action("GOV-C1", PlannedActionType.MIGRATE, successor="GOV-X1")],
        )


@pytest.mark.asyncio
async def test_actions_put_retire_only_requires_justification():
    session, run = _actions_world()
    with pytest.raises(rs.ActionValidationError, match="justification"):
        await rs.update_planned_actions(
            session, ORG, run.id,
            [
                _action("GOV-C1", PlannedActionType.MIGRATE, successor="GOV-B1"),
                _action("GOV-D1", PlannedActionType.RETIRE_ONLY, justification="  "),
            ],
        )


@pytest.mark.asyncio
async def test_actions_put_first_run_framework_confirmation():
    session, run = _actions_world(first_run=True)
    good = [
        _action("GOV-C1", PlannedActionType.MIGRATE, successor="GOV-B1"),
        _action("GOV-D1", PlannedActionType.RETAIN),
    ]
    # Without the confirmed list the first-run edit is refused.
    with pytest.raises(rs.ActionValidationError, match="confirmed_framework_ids"):
        await rs.update_planned_actions(session, ORG, run.id, good)

    # Unknown framework ids are refused.
    with pytest.raises(rs.ActionValidationError, match="fw_gamma"):
        await rs.update_planned_actions(
            session, ORG, run.id, good, confirmed_framework_ids=["fw_gamma"]
        )

    updated = await rs.update_planned_actions(
        session, ORG, run.id, good,
        confirmed_framework_ids=["fw_alpha"], user_id=USER,
    )
    selections = {
        s.framework_id: s for s in session.tables[OrganizationFrameworkSelection]
    }
    assert selections["fw_alpha"].active is True
    assert selections["fw_alpha"].source == "reconciliation"
    assert selections["fw_beta"].active is False  # unconfirmed backfill deactivated
    assert rs.frameworks_confirmed(updated) == ["fw_alpha"]

    # A follow-up edit without the list now passes (confirmation recorded).
    await rs.update_planned_actions(session, ORG, run.id, good)


@pytest.mark.asyncio
async def test_actions_put_run_state_and_not_found():
    session, run = _actions_world()
    with pytest.raises(rs.RunNotFoundError):
        await rs.update_planned_actions(session, ORG, uuid.uuid4(), [])
    # A run belonging to another org is not visible.
    with pytest.raises(rs.RunNotFoundError):
        await rs.update_planned_actions(session, OTHER_ORG, run.id, [])
    run.status = "applied"
    with pytest.raises(rs.RunStateError):
        await rs.update_planned_actions(session, ORG, run.id, [])


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_changelog_assembles_applied_window():
    run1 = _import_run(V1, V2, T1)
    run2 = _import_run(V2, V3, T2)
    details = {
        run1.id: _controls_detail(V1, V2, added=[AddedEntity(key="GOV-F1", name="New")]),
        run2.id: _controls_detail(
            V2, V3,
            changed=[ChangedEntity(
                key="GOV-A1",
                fields={"control_name": FieldChange(old="A", new="B")},
            )],
            deprecated=[DeprecatedEntity(key="GOV-C1", superseded_by="GOV-B1")],
        ),
    }
    org_run = _org_run(
        status="applied", from_version=V1, to_version=V3,
        import_run_id=run2.id, completed_at=T2,
    )
    session = FakeSession({
        CatalogImportRun: [run1, run2],
        OrganizationReconciliationRun: [org_run],
    })
    entries, total = await rs.assemble_changelog(
        session, ORG, detail_loader=_loader(details)
    )
    assert total == 3
    # Newest catalog version first within the reconciliation.
    assert [(e.version, e.key) for e in entries] == [
        (V3, "GOV-A1"), (V3, "GOV-C1"), (V2, "GOV-F1"),
    ]
    assert all(e.applied_at == T2 for e in entries)
    assert entries[0].summary == "fields changed: control_name"
    assert "superseded by GOV-B1" in entries[1].summary

    paged, total = await rs.assemble_changelog(
        session, ORG, limit=1, offset=1, detail_loader=_loader(details)
    )
    assert total == 3
    assert [e.key for e in paged] == ["GOV-C1"]


@pytest.mark.asyncio
async def test_changelog_empty_before_first_apply():
    run1 = _import_run(V1, V2, T1)
    session = FakeSession({
        CatalogImportRun: [run1],
        OrganizationReconciliationRun: [_org_run(status="previewed")],
    })
    entries, total = await rs.assemble_changelog(
        session, ORG, detail_loader=_loader({})
    )
    assert entries == [] and total == 0
