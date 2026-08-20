"""Whole-feature E2E for the SCF catalog upgrade (WP6, plan §4.7).

One coherent journey over the real service code — only the database session is
faked (the repo's established unit-test boundary; see test_catalog_apply.py /
test_reconciliation_apply.py):

    platform stage (catalog_diff.stage_catalog_diff on a synthetic 2026.2-era
    workbook) → superseded pairings set → platform apply
    (catalog_apply.apply_catalog_run) → org preview
    (reconciliation_service.build_preview, branches a–e) → org actions confirm
    (incl. the first-run framework confirmation) → org apply
    (apply_reconciliation_run) → scope re-materialisation + notification
    verified → platform revert refusal while the org is pinned
    (RevertBlockedError) → org rollback (rollback_reconciliation_run) →
    snapshot restore verified (byte-compare modulo timestamps) → platform
    revert now allowed → re-preview + re-apply reaches the same end state.

The FakeSession merges the two established fakes: WP1b's transactional
semantics (rollback restores pre-transaction row images, so guard refusals can
be asserted as no-ops) and WP2c's statement dispatch (advisory locks, the
bulk-scope raw catalog query, the (scf_id, selected) column read, the
scoped_controls bulk UPDATE, and object deletes), plus the two notification
queries (OrganizationMember.user_id column select and the User lookup).

Fixture identifiers use a letter after the hyphen (``GOV-A1``) — opaque to the
code under test.
"""
from __future__ import annotations

import copy
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

# CI runs pytest from the repo root where backend/pytest.ini's
# asyncio_mode=auto is not picked up; mark explicitly (repo convention).
pytestmark = pytest.mark.asyncio
from sqlalchemy import Delete, Select
from sqlalchemy.sql.dml import Update
from sqlalchemy.sql.elements import TextClause

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from catalog_models import (  # noqa: E402
    CapabilityTheme,
    CapabilityThemeMapping,
    SCFCatalogAssessmentObjective,
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
)
from models import (  # noqa: E402
    CatalogImportRun,
    CDMMapping,
    ControlAssessmentComposite,
    EngagementControlScope,
    EvidenceCollectionTask,
    EvidenceTracking,
    Notification,
    OrganizationCatalogState,
    OrganizationFrameworkSelection,
    OrganizationMember,
    OrganizationReconciliationRun,
    ScopedControl,
    User,
)
from schemas_catalog_upgrade import (  # noqa: E402
    CatalogEntityType,
    PlannedAction,
    PlannedActionType,
)
from services import catalog_apply as ca  # noqa: E402
from services import catalog_diff as cd  # noqa: E402
from services import reconciliation_service as rs  # noqa: E402
from services.notifications import (  # noqa: E402
    create_catalog_reconciliation_notifications,
)
from test_scf_extractor import AICPA_SLUG, GDPR_SLUG, build_workbook  # noqa: E402

CONTROLS = CatalogEntityType.CONTROLS
EVIDENCE = CatalogEntityType.EVIDENCE


# ---------------------------------------------------------------------------
# FakeSession — WP1b's transactional fake + WP2c's statement dispatch
# ---------------------------------------------------------------------------

TABLES = (
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
    SCFCatalogAssessmentObjective,
    CapabilityTheme,
    CapabilityThemeMapping,
    CatalogImportRun,
    OrganizationCatalogState,
    OrganizationFrameworkSelection,
    OrganizationReconciliationRun,
    ScopedControl,
    EvidenceTracking,
    EngagementControlScope,
    CDMMapping,
    EvidenceCollectionTask,
    ControlAssessmentComposite,
    OrganizationMember,
    User,
    Notification,
)


def _row_attrs(obj) -> dict:
    return {
        k: copy.deepcopy(v) for k, v in vars(obj).items() if not k.startswith("_")
    }


class _FakeResult:
    def __init__(self, rows):
        self._rows = list(rows)

    def scalars(self):
        # Real .scalars() takes the first column; unwrap column-select tuples.
        return _FakeResult(
            [row[0] if isinstance(row, tuple) else row for row in self._rows]
        )

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        assert len(self._rows) <= 1, f"expected at most one row, got {self._rows}"
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeSession:
    """In-memory tables + Postgres-like transaction semantics.

    ``rollback()`` restores the row images captured at the last commit —
    guard refusals inside a service transaction can be asserted as strict
    no-ops. Entity SELECTs return tables wholesale (the services filter in
    Python); the bulk-scope text/column/UPDATE statements are emulated the
    way test_reconciliation_apply.py established.
    """

    def __init__(self, rows_by_model=None):
        self.tables = {model: [] for model in TABLES}
        for model, rows in (rows_by_model or {}).items():
            self.tables[model] = list(rows)
        self._pending = []
        self.commits = 0
        self.rollbacks = 0
        self.deleted = []
        self.deleted_tables = set()
        self.events = []
        self._theme_id_seq = 1000
        self._snapshot = None
        self._take_snapshot()

    # -- transaction bookkeeping ------------------------------------------
    def _take_snapshot(self):
        self._snapshot = {
            model: [(obj, _row_attrs(obj)) for obj in rows]
            for model, rows in self.tables.items()
        }

    async def commit(self):
        await self.flush()
        self.commits += 1
        self.events.append("commit")
        self._take_snapshot()

    async def rollback(self):
        self._pending = []
        self.rollbacks += 1
        self.events.append("rollback")
        for model, snap in self._snapshot.items():
            self.tables[model] = [obj for obj, _ in snap]
            for obj, attrs in snap:
                for key in [k for k in vars(obj) if not k.startswith("_")]:
                    if key not in attrs:
                        try:
                            delattr(obj, key)
                        except AttributeError:
                            pass
                for key, value in attrs.items():
                    setattr(obj, key, copy.deepcopy(value))

    # -- statement dispatch -----------------------------------------------
    def add(self, obj):
        self._pending.append(obj)

    async def flush(self):
        for obj in self._pending:
            for model in TABLES:
                if isinstance(obj, model):
                    if model is CapabilityTheme and getattr(obj, "id", None) is None:
                        self._theme_id_seq += 1
                        obj.id = self._theme_id_seq
                    elif getattr(obj, "id", None) is None and hasattr(type(obj), "id"):
                        obj.id = uuid.uuid4()
                    self.tables[model].append(obj)
                    break
            else:
                raise AssertionError(f"add() of unknown row type: {type(obj)}")
        self._pending = []

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
                if params and "cls" in params:
                    assert params["cls"] == rs.ORG_RECONCILIATION_LOCK_CLASS
                    self.events.append(("lock_org", params["key"]))
                    return _FakeResult([])
                assert params == {"key": ca.CATALOG_LOCK_KEY}
                self.events.append("lock_catalog_exclusive")
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
        if isinstance(stmt, Delete):
            table_name = stmt.table.name
            self.events.append(("delete", table_name))
            self.deleted_tables.add(table_name)
            for model in TABLES:
                if model.__tablename__ == table_name:
                    self.tables[model] = []
            return _FakeResult([])
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
            # Column select ((scf_id, selected) read, OrganizationMember.user_id).
            keys = [d["name"] for d in descriptions]
            self.events.append(("select_cols", entity.__tablename__))
            return _FakeResult([tuple(getattr(r, k) for k in keys) for r in rows])
        raise AssertionError(f"unhandled statement: {stmt!r}")


# ---------------------------------------------------------------------------
# World fixtures — live 2026.1 catalog matching the synthetic workbook
# ---------------------------------------------------------------------------

ORG = uuid.uuid4()
ADMIN = uuid.uuid4()   # org admin who receives the notification
ACTOR = uuid.uuid4()   # platform admin driving the console (excluded)

T0 = datetime(2026, 1, 1)
V1, V2 = "2026.1", "2026.2"

DOMAIN_NAME = "Cybersecurity & Data Protection Governance"

# 2026.2-era workbook rows (columns per test_scf_extractor.controls_headers).
# GOV-A1: description CHANGED vs live; GOV-A2: identical to live (unchanged);
# GOV-A3/A4/A5: new. GOV-A4's name is a deliberate near-match for the retiring
# GOV-R1 so the superseded_by suggestion scorer fires. Live-only GOV-C1/D1/R1
# become deprecations. 5 workbook controls vs 5 live actives keeps the
# control-count-drop sanity gate quiet.
WORKBOOK_CONTROL_ROWS = [
    (
        DOMAIN_NAME,
        "Cybersecurity & Data Protection Governance Program",
        "GOV-A1",
        "Mechanisms exist to facilitate the implementation of an enterprise-wide "
        "governance program.",
        "Annual",
        "E-GOV-A1, E-GOV-A2",
        "Does the organization facilitate a governance program?",
        10,
        "Process",
        "Govern",
        "Practices are non-existent.",
        "CC1.1\nCC1.2",
        "Art 32",
    ),
    (
        DOMAIN_NAME,
        "Publishing Cybersecurity & Data Protection Documentation",
        "GOV-A2",
        "Mechanisms exist to establish and publish documentation.",
        "Annual",
        "E-GOV-A3",
        None,
        8,
        "Process",
        "Govern",
        None,
        "CC5.3",
        None,
    ),
    (
        DOMAIN_NAME,
        "Continuous Governance Monitoring",
        "GOV-A3",
        "Mechanisms exist to continuously monitor the governance program.",
        "Annual",
        None,
        None,
        7,
        "Process",
        "Govern",
        None,
        "CC2.1",
        None,
    ),
    (
        DOMAIN_NAME,
        "Governance Status Reports",
        "GOV-A4",
        "Mechanisms exist to report governance status.",
        "Annual",
        None,
        None,
        5,
        "Process",
        "Govern",
        None,
        None,
        None,
    ),
    (
        DOMAIN_NAME,
        "Governance Tooling Inventory",
        "GOV-A5",
        "Mechanisms exist to inventory governance tooling.",
        "Annual",
        None,
        None,
        5,
        "Process",
        "Govern",
        None,
        None,
        None,
    ),
]


def _ctl(scf_id, name, **over):
    """Full-column live catalog control row (diff + apply both touch it)."""
    attrs = {f: None for f in cd.CONTROL_COMPARED_FIELDS}
    attrs.update(
        scf_id=scf_id,
        scf_domain=DOMAIN_NAME,
        control_name=name,
        control_description=f"Mechanisms exist for {name}.",
        pptdf_people=False,
        pptdf_process=True,
        pptdf_technology=False,
        pptdf_data=False,
        pptdf_facility=False,
        evidence_requests=[],
        framework_mappings={},
        scrm_tier1_strategic=False,
        scrm_tier2_operational=False,
        scrm_tier3_tactical=False,
        risk_codes=[],
        threat_codes=[],
        status="active",
        retired_in_version=None,
        superseded_by=None,
        required_artifact_types=[{"type": "policy", "mandatory": True}],
        required_artifact_types_extracted_at=T0,
        catalog_version=V1,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _live_controls():
    return [
        _ctl(
            "GOV-A1",
            "Cybersecurity & Data Protection Governance Program",
            control_description=(
                "Mechanisms exist to facilitate the implementation of a "
                "governance program."
            ),
            control_question="Does the organization facilitate a governance program?",
            validation_cadence="Annual",
            control_weighting=10,
            nist_csf_function="Govern",
            evidence_requests=["E-GOV-A1", "E-GOV-A2"],
            framework_mappings={
                AICPA_SLUG: ["CC1.1", "CC1.2"],
                GDPR_SLUG: ["Art 32"],
            },
            cmm_level_0="Practices are non-existent.",
        ),
        _ctl(
            "GOV-A2",
            "Publishing Cybersecurity & Data Protection Documentation",
            control_description=(
                "Mechanisms exist to establish and publish documentation."
            ),
            validation_cadence="Annual",
            control_weighting=8,
            nist_csf_function="Govern",
            evidence_requests=["E-GOV-A3"],
            framework_mappings={AICPA_SLUG: ["CC5.3"]},
        ),
        _ctl("GOV-C1", "Governance Steering Committee"),
        _ctl("GOV-D1", "Legacy Governance Metrics"),
        _ctl("GOV-R1", "Governance Status Reporting"),
    ]


def _domain(identifier="GOV"):
    return SimpleNamespace(
        identifier=identifier,
        order=1,
        name=DOMAIN_NAME,
        principle="Execute a documented, risk-based program.",
        principle_intent="Organizations specify the development of a program.",
        status="active",
        retired_in_version=None,
        superseded_by=None,
        catalog_version=V1,
        created_at=T0,
        updated_at=T0,
    )


def _evd(evidence_id, title, **over):
    attrs = dict(
        evidence_id=evidence_id,
        area_of_focus="Governance",
        artifact_title=title,
        artifact_description=None,
        control_mappings=[],
        status="active",
        retired_in_version=None,
        superseded_by=None,
        catalog_version=V1,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _live_evidence():
    return [
        _evd(
            "E-GOV-A1",
            "Cybersecurity Program Charter",
            artifact_description="Charter for the cybersecurity program.",
            control_mappings=["GOV-A1", "GOV-A2"],
        ),
        _evd(
            "E-GOV-A2",
            "Steering Committee Minutes",
            artifact_description="Minutes evidencing oversight.",
            control_mappings=["GOV-A1"],
        ),
        _evd("E-GOV-A9", "Legacy Governance Report"),
    ]


def _ao(ao_id="GOV-A1.1", scf_id="GOV-A1"):
    attrs = {f: None for f in cd.AO_COMPARED_FIELDS}
    attrs.update(
        ao_id=ao_id,
        scf_id=scf_id,
        objective_text="the organization facilitates a governance program.",
        pptdf_people=False,
        pptdf_process=True,
        pptdf_technology=False,
        pptdf_data=False,
        pptdf_facility=False,
        ao_origins="SCF",
        assessment_rigor=3,
        assessment_procedure="Examine the program charter.",
        expected_results="A charter exists and is approved.",
        status="active",
        retired_in_version=None,
        superseded_by=None,
        catalog_version=V1,
        created_at=T0,
        updated_at=T0,
    )
    return SimpleNamespace(**attrs)


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


def _selection(framework_id, source="backfill", active=True):
    return SimpleNamespace(
        organization_id=ORG,
        framework_id=framework_id,
        source=source,
        active=active,
        selected_by=None,
        selected_at=None,
    )


THEMES_JSON = {
    "themes": [
        {
            "theme_code": "IAM",
            "name": "Identity & Access Management",
            "description": "IAM theme.",
            "ksi_reference": "KSI-IAM",
            "display_order": 1,
        }
    ],
    "nist_family_mappings": {"AC": {"primary": "IAM", "supporting": []}},
}

_TIMESTAMP_FIELDS = {
    "created_at",
    "updated_at",
    "last_reconciled_at",
    "selected_at",
    "completed_at",
    "required_artifact_types_extracted_at",
}

_ORG_TABLES = (
    (ScopedControl, "scf_id"),
    (EvidenceTracking, "evidence_id"),
    (OrganizationCatalogState, "organization_id"),
)


def _org_images(session, exclude=frozenset()):
    """Byte-comparable images of the org tables, modulo timestamps (+extras)."""
    skip = _TIMESTAMP_FIELDS | set(exclude)
    images = {}
    for model, sort_key in _ORG_TABLES:
        rows = sorted(session.tables[model], key=lambda r: str(getattr(r, sort_key)))
        images[model.__name__] = [
            {k: v for k, v in _row_attrs(r).items() if k not in skip} for r in rows
        ]
    return images


def _catalog_images(session, exclude=frozenset()):
    skip = _TIMESTAMP_FIELDS | {"catalog_version"} | set(exclude)
    images = {}
    for model, key in (
        (SCFCatalogControl, "scf_id"),
        (SCFCatalogDomain, "identifier"),
        (SCFCatalogEvidence, "evidence_id"),
        (SCFCatalogAssessmentObjective, "ao_id"),
    ):
        rows = sorted(session.tables[model], key=lambda r: getattr(r, key))
        images[model.__tablename__] = [
            {k: v for k, v in _row_attrs(r).items() if k not in skip} for r in rows
        ]
    return images


def _scoped_by_id(session):
    return {r.scf_id: r for r in session.tables[ScopedControl]}


def _tracking_by_id(session):
    return {r.evidence_id: r for r in session.tables[EvidenceTracking]}


def _controls_by_id(session):
    return {c.scf_id: c for c in session.tables[SCFCatalogControl]}


# ---------------------------------------------------------------------------
# Journey driver — each test replays the §4.7 pipeline up to its checkpoint
# ---------------------------------------------------------------------------


class Journey:
    def __init__(self, tmp_path):
        self.workbook = build_workbook(
            tmp_path / "scf-e2e.xlsx",
            version=V2,
            era="focal_documents",
            control_rows=WORKBOOK_CONTROL_ROWS,
        )
        self.platform_run = SimpleNamespace(
            id=uuid.uuid4(),
            from_version=V1,
            to_version=V2,
            status="staging",
            superseded_pairings=[],
            diff_summary=None,
            sanity_report=None,
            workbook_object_key=None,
            diff_detail_object_key=None,
            created_at=T0,
            updated_at=T0,
            completed_at=None,
        )
        self.session = FakeSession({
            SCFCatalogControl: _live_controls(),
            SCFCatalogDomain: [_domain()],
            SCFCatalogEvidence: _live_evidence(),
            SCFCatalogAssessmentObjective: [_ao()],
            CatalogImportRun: [self.platform_run],
            # First-reconciliation org: NO organization_catalog_state row,
            # heuristic backfill framework selections awaiting confirmation.
            OrganizationFrameworkSelection: [
                _selection(AICPA_SLUG),
                _selection(GDPR_SLUG),
            ],
            ScopedControl: [
                _scoped("GOV-A1"),
                _scoped("GOV-C1", impl="implemented", maturity="managed"),
                _scoped("GOV-D1"),
                _scoped("GOV-R1"),
                _scoped("OLD-Z9"),  # orphan: no catalog row at all
            ],
            EvidenceTracking: [_tracking("E-GOV-A9")],
            ControlAssessmentComposite: [
                SimpleNamespace(organization_id=ORG, scf_id="GOV-A1")
            ],
            OrganizationMember: [
                SimpleNamespace(organization_id=ORG, user_id=ADMIN, role="admin")
            ],
            User: [
                SimpleNamespace(
                    id=ADMIN,
                    email="admin@example.test",
                    display_name="Org Admin",
                    email_notifications_enabled=False,
                    notification_frequency="daily",
                )
            ],
        })
        self.staged = None
        self.detail = None
        self.org_run = None
        self.org_apply_report = None
        self.pre_org_apply_images = None

    def detail_loader(self, run):
        assert run.id == self.platform_run.id
        return self.detail

    async def stage(self):
        self.staged = await cd.stage_catalog_diff(self.session, self.workbook, V1)
        assert self.staged.sanity_report.passed, self.staged.sanity_report
        self.detail = self.staged.diff_detail
        # What the WP1c endpoint / WP1b stage task records on the run.
        run = self.platform_run
        run.status = "staged"
        run.diff_summary = self.staged.diff_summary.model_dump(mode="json")
        run.sanity_report = self.staged.sanity_report.model_dump(mode="json")
        await self.session.commit()
        return self.staged

    def set_pairings(self):
        # The PUT .../pairings admin step: GOV-C1 retires in favour of GOV-A2.
        self.platform_run.superseded_pairings = [
            {"deprecated_scf_id": "GOV-C1", "superseded_by": "GOV-A2"}
        ]

    async def platform_apply(self):
        await self.stage()
        self.set_pairings()
        return await ca.apply_catalog_run(
            self.session, self.platform_run, self.detail, themes_json=THEMES_JSON
        )

    async def preview(self):
        result = await rs.build_preview(
            self.session, ORG, user_id=ACTOR, detail_loader=self.detail_loader
        )
        self.org_run = result.run
        return result

    def planned_actions(self):
        return [
            PlannedAction(key="GOV-C1", entity=CONTROLS,
                          action=PlannedActionType.MIGRATE,
                          successor_scf_id="GOV-A2"),
            PlannedAction(key="GOV-D1", entity=CONTROLS,
                          action=PlannedActionType.RETIRE_ONLY,
                          justification="Covered by internal procedure"),
            PlannedAction(key="GOV-R1", entity=CONTROLS,
                          action=PlannedActionType.RETAIN),
            PlannedAction(key="E-GOV-A9", entity=EVIDENCE,
                          action=PlannedActionType.MIGRATE,
                          successor_scf_id="E-GOV-A1"),
        ]

    async def confirm_actions(self):
        return await rs.update_planned_actions(
            self.session,
            ORG,
            self.org_run.id,
            self.planned_actions(),
            confirmed_framework_ids=[AICPA_SLUG],
            user_id=ACTOR,
        )

    async def org_apply(self):
        self.pre_org_apply_images = _org_images(self.session)
        self.org_apply_report = await rs.apply_reconciliation_run(
            self.session, ORG, self.org_run.id, user_id=ACTOR
        )
        return self.org_apply_report

    async def to_org_applied(self):
        await self.platform_apply()
        await self.preview()
        await self.confirm_actions()
        return await self.org_apply()

    async def org_rollback(self):
        return await rs.rollback_reconciliation_run(
            self.session, ORG, self.org_run.id, user_id=ACTOR
        )


@pytest.fixture
def journey(tmp_path):
    return Journey(tmp_path)


# ---------------------------------------------------------------------------
# Platform stage → pairings → apply
# ---------------------------------------------------------------------------


async def test_stage_classifies_the_synthetic_workbook(journey):
    staged = await journey.stage()

    assert staged.to_version == V2
    assert staged.forced is False
    controls = staged.diff_detail.entities[CONTROLS]
    assert [a.key for a in controls.added] == ["GOV-A3", "GOV-A4", "GOV-A5"]
    assert [c.key for c in controls.changed] == ["GOV-A1"]
    assert controls.changed[0].fields.keys() == {"control_description"}
    assert controls.changed[0].fields["control_description"].old == (
        "Mechanisms exist to facilitate the implementation of a governance program."
    )
    assert [d.key for d in controls.deprecated] == ["GOV-C1", "GOV-D1", "GOV-R1"]
    assert "GOV-A2" in controls.unchanged

    # The display-only successor suggestions: GOV-R1's near-namesake GOV-A4.
    by_key = {d.key: d for d in controls.deprecated}
    assert [s.scf_id for s in by_key["GOV-R1"].suggestions] == ["GOV-A4"]

    evidence = staged.diff_detail.entities[EVIDENCE]
    assert [d.key for d in evidence.deprecated] == ["E-GOV-A9"]
    assert sorted(evidence.unchanged) == ["E-GOV-A1", "E-GOV-A2"]

    domains = staged.diff_detail.entities[CatalogEntityType.DOMAINS]
    assert domains.unchanged == ["GOV"]
    aos = staged.diff_detail.entities[CatalogEntityType.ASSESSMENT_OBJECTIVES]
    assert aos.unchanged == ["GOV-A1.1"]

    assert journey.platform_run.diff_summary["entities"]["controls"]["added"] == 3


async def test_platform_apply_lands_all_change_classes_and_pairings(journey):
    report = await journey.platform_apply()

    controls = _controls_by_id(journey.session)
    assert controls["GOV-A1"].control_description.startswith(
        "Mechanisms exist to facilitate the implementation of an enterprise-wide"
    )
    assert controls["GOV-A1"].catalog_version == V2
    assert controls["GOV-A3"].status == "active"
    assert controls["GOV-A3"].framework_mappings == {AICPA_SLUG: ["CC2.1"]}
    for key in ("GOV-C1", "GOV-D1", "GOV-R1"):
        assert controls[key].status == "deprecated"
        assert controls[key].retired_in_version == V2
    # The pairing written at apply — the org preview's migrate default reads it.
    assert controls["GOV-C1"].superseded_by == "GOV-A2"
    assert controls["GOV-D1"].superseded_by is None

    evidence = {e.evidence_id: e for e in journey.session.tables[SCFCatalogEvidence]}
    assert evidence["E-GOV-A9"].status == "deprecated"

    assert journey.platform_run.status == "applied"
    assert journey.platform_run.completed_at is not None
    counts = report.entities["controls"]
    assert (counts.added, counts.changed, counts.deprecated) == (3, 1, 3)


# ---------------------------------------------------------------------------
# Org preview — branches (a)–(e) against the applied platform run
# ---------------------------------------------------------------------------


async def test_org_preview_exercises_all_branches(journey):
    await journey.platform_apply()
    result = await journey.preview()

    # (a) additions vs active framework selections: only GOV-A3 maps to the
    # org's frameworks; GOV-A4/A5 are count-only.
    assert [i.scf_id for i in result.additions.in_scope] == ["GOV-A3"]
    assert result.additions.in_scope[0].frameworks == [AICPA_SLUG]
    assert result.additions.out_of_scope_count == 2

    # (b) deprecated impacts limited to org data at stake; migrate default
    # flows from the platform pairing, retain default without a successor.
    impacts = {i.key: i for i in result.deprecated_impacts}
    assert set(impacts) == {"GOV-C1", "GOV-D1", "GOV-R1", "E-GOV-A9"}
    assert impacts["GOV-C1"].superseded_by == "GOV-A2"
    assert impacts["GOV-C1"].suggested_action == PlannedActionType.MIGRATE
    assert impacts["GOV-C1"].planned_action.successor_scf_id == "GOV-A2"
    assert impacts["GOV-C1"].data_summary["implementation_status"] == "implemented"
    assert impacts["GOV-D1"].suggested_action == PlannedActionType.RETAIN
    assert impacts["GOV-R1"].suggested_action == PlannedActionType.RETAIN
    assert impacts["E-GOV-A9"].entity == EVIDENCE
    assert impacts["E-GOV-A9"].data_summary == {
        "tracking_rows": 1, "is_tracked": True,
    }

    # (c) changed ∩ selected, composite-driven reassessment flag.
    assert [c.scf_id for c in result.changed_in_scope] == ["GOV-A1"]
    assert result.changed_in_scope[0].reassessment_recommended is True

    # (d) orphan report — report-only, run still created.
    assert {(o.source_table, o.key) for o in result.orphans.items} == {
        ("scoped_controls", "OLD-Z9")
    }

    # (e) first reconciliation: framework confirmation demanded, both
    # heuristic backfill rows listed.
    assert result.framework_confirmation.required is True
    assert sorted(s.framework_id for s in result.framework_confirmation.selections) \
        == sorted([AICPA_SLUG, GDPR_SLUG])

    run = result.run
    assert run.status == "previewed"
    assert run.from_version == V1 and run.to_version == V2
    assert run.catalog_import_run_id == journey.platform_run.id


async def test_actions_confirm_records_frameworks_and_actions(journey):
    await journey.platform_apply()
    await journey.preview()

    # The §4.3 gate: apply refused until the first-run confirmation lands.
    with pytest.raises(rs.FrameworksNotConfirmedError):
        await rs.apply_reconciliation_run(journey.session, ORG, journey.org_run.id)

    updated = await journey.confirm_actions()

    actions = {a["key"]: a for a in updated.planned_actions}
    assert actions["GOV-C1"]["action"] == "migrate"
    assert actions["E-GOV-A9"]["successor_scf_id"] == "E-GOV-A1"
    assert rs.frameworks_confirmed(updated) == [AICPA_SLUG]

    selections = {
        s.framework_id: s
        for s in journey.session.tables[OrganizationFrameworkSelection]
    }
    assert selections[AICPA_SLUG].active is True
    assert selections[AICPA_SLUG].source == "reconciliation"
    assert selections[GDPR_SLUG].active is False  # unconfirmed backfill dropped


# ---------------------------------------------------------------------------
# Org apply — scope re-materialisation + notification
# ---------------------------------------------------------------------------


async def test_org_apply_executes_actions_and_rematerialises_scope(journey):
    report = await journey.to_org_applied()

    scoped = _scoped_by_id(journey.session)
    # migrate: successor carries the org's assessment state, old row demoted.
    assert scoped["GOV-A2"].selected is True
    assert scoped["GOV-A2"].implementation_status == "implemented"
    assert scoped["GOV-A2"].maturity_level == "managed"
    assert scoped["GOV-C1"].selected is False
    assert "GOV-A2" in scoped["GOV-C1"].out_of_scope_justification
    # retire_only: demoted with the admin's justification.
    assert scoped["GOV-D1"].selected is False
    assert scoped["GOV-D1"].out_of_scope_justification == "Covered by internal procedure"
    # retain: untouched (renders badged downstream).
    assert scoped["GOV-R1"].selected is True
    # scope re-materialisation added the new in-framework control — and ONLY it.
    assert scoped["GOV-A3"].selected is True
    assert "GOV-A4" not in scoped and "GOV-A5" not in scoped
    assert report.scope_added == 1

    tracking = _tracking_by_id(journey.session)
    assert tracking["E-GOV-A9"].is_tracked is False
    assert tracking["E-GOV-A1"].is_tracked is True
    assert tracking["E-GOV-A1"].method_of_collection == "export script"

    state = journey.session.tables[OrganizationCatalogState][0]
    assert state.reconciled_catalog_version == V2
    assert state.last_reconciliation_run_id == journey.org_run.id
    assert journey.org_run.status == "applied"
    assert journey.org_run.org_snapshot is not None
    assert journey.session.deleted == []  # apply never deletes org rows


async def test_org_apply_notifies_org_admins(journey):
    report = await journey.to_org_applied()

    # The org.reconcile_apply task's post-commit call, verbatim.
    created = await create_catalog_reconciliation_notifications(
        journey.session,
        ORG,
        journey.org_run.id,
        event="applied",
        from_version=report.from_version,
        to_version=report.to_version,
        actor_user_id=ACTOR,
    )
    assert created == 1
    notification = journey.session.tables[Notification][0]
    assert notification.user_id == ADMIN
    assert notification.reference_type == "catalog"
    assert notification.reference_id == journey.org_run.id
    assert notification.type == "catalog_reconciliation_applied"
    assert V2 in notification.message


# ---------------------------------------------------------------------------
# Platform revert refusal while the org is pinned to to_version
# ---------------------------------------------------------------------------


async def test_platform_revert_blocked_while_org_reconciled(journey):
    await journey.to_org_applied()
    before_catalog = _catalog_images(journey.session, exclude=())

    with pytest.raises(ca.RevertBlockedError) as exc:
        await ca.revert_catalog_run(
            journey.session, journey.platform_run, journey.detail,
            themes_json=THEMES_JSON,
        )

    assert exc.value.blockers == [str(ORG)]
    assert exc.value.to_version == V2
    assert journey.platform_run.status == "applied"  # run untouched
    assert _catalog_images(journey.session, exclude=()) == before_catalog


# ---------------------------------------------------------------------------
# Org rollback — snapshot restore is the authority
# ---------------------------------------------------------------------------


async def test_org_rollback_restores_snapshot_byte_identical(journey):
    await journey.to_org_applied()
    report = await journey.org_rollback()

    # Byte-compare of every org row dict, modulo timestamps (plan §4.7).
    assert _org_images(journey.session) == journey.pre_org_apply_images
    assert journey.org_run.status == "rolled_back"
    assert journey.org_run.actions_log[-1]["event"] == "rolled_back"
    assert report.action == "rolled_back"

    scoped = _scoped_by_id(journey.session)
    # Run-created rows are gone (unreferenced ⇒ deleted), pre-images restored.
    assert "GOV-A2" not in scoped and "GOV-A3" not in scoped
    assert scoped["GOV-C1"].selected is True
    assert scoped["GOV-C1"].implementation_status == "implemented"
    assert scoped["GOV-D1"].selected is True
    tracking = _tracking_by_id(journey.session)
    assert "E-GOV-A1" not in tracking
    assert tracking["E-GOV-A9"].is_tracked is True
    # The state row this apply created was removed again.
    assert journey.session.tables[OrganizationCatalogState] == []


async def test_org_rollback_demotes_engagement_referenced_created_rows(journey):
    await journey.to_org_applied()
    successor = _scoped_by_id(journey.session)["GOV-A2"]
    journey.session.tables[EngagementControlScope].append(
        SimpleNamespace(
            id=uuid.uuid4(),
            engagement_id=uuid.uuid4(),
            scf_id="GOV-A2",
            scoped_control_id=successor.id,
        )
    )

    report = await journey.org_rollback()

    scoped = _scoped_by_id(journey.session)
    # The engagement-referenced successor survives, demoted — CASCADE never fires.
    assert scoped["GOV-A2"].selected is False
    assert successor not in journey.session.deleted
    assert report.demoted == 1
    assert "GOV-A3" not in scoped  # unreferenced run-created row still deleted


# ---------------------------------------------------------------------------
# Platform revert after the rollback + re-preview / re-apply
# ---------------------------------------------------------------------------


async def test_platform_revert_succeeds_after_org_rollback(journey):
    await journey.to_org_applied()
    await journey.org_rollback()

    report = await ca.revert_catalog_run(
        journey.session, journey.platform_run, journey.detail,
        themes_json=THEMES_JSON,
    )

    assert report.action == "reverted"
    assert journey.platform_run.status == "reverted"
    controls = _controls_by_id(journey.session)
    # Changed fields restored from stored old values.
    assert controls["GOV-A1"].control_description == (
        "Mechanisms exist to facilitate the implementation of a governance program."
    )
    # Run-deprecated rows re-activated, pairing cleared with them.
    for key in ("GOV-C1", "GOV-D1", "GOV-R1"):
        assert controls[key].status == "active"
        assert controls[key].retired_in_version is None
    assert controls["GOV-C1"].superseded_by is None
    # Run-added rows deprecated, never deleted.
    for key in ("GOV-A3", "GOV-A4", "GOV-A5"):
        assert controls[key].status == "deprecated"
        assert controls[key].retired_in_version == V2
    evidence = {e.evidence_id: e for e in journey.session.tables[SCFCatalogEvidence]}
    assert evidence["E-GOV-A9"].status == "active"


async def test_repreview_and_reapply_after_rollback_reaches_same_state(journey):
    await journey.to_org_applied()
    end_state_first = _org_images(
        journey.session, exclude={"id", "last_reconciliation_run_id"}
    )
    first_run_id = journey.org_run.id

    await journey.org_rollback()

    # Re-preview: the platform run is still applied, the org is back on V1
    # (its state row is gone ⇒ first reconciliation again), so the journey
    # repeats — and must land on the identical end state.
    result = await journey.preview()
    assert result.run.id != first_run_id
    assert result.run.catalog_import_run_id == journey.platform_run.id
    assert result.eligibility.first_reconciliation is True
    assert [i.scf_id for i in result.additions.in_scope] == ["GOV-A3"]

    await journey.confirm_actions()
    report = await journey.org_apply()

    assert report.action == "applied"
    assert report.scope_added == 1
    state = journey.session.tables[OrganizationCatalogState][0]
    assert state.reconciled_catalog_version == V2
    end_state_second = _org_images(
        journey.session, exclude={"id", "last_reconciliation_run_id"}
    )
    assert end_state_second == end_state_first


# ---------------------------------------------------------------------------
# Cross-cutting invariants the journey must hold
# ---------------------------------------------------------------------------


async def test_lock_ordering_across_platform_and_org_operations(journey):
    await journey.stage()
    journey.set_pairings()
    journey.session.events.clear()
    await ca.apply_catalog_run(
        journey.session, journey.platform_run, journey.detail,
        themes_json=THEMES_JSON,
    )
    assert journey.session.events[0] == "lock_catalog_exclusive"

    await journey.preview()
    await journey.confirm_actions()
    journey.session.events.clear()
    await journey.org_apply()
    # Shared catalog key then exclusive org key, before any read (one lock
    # space with the platform apply — the interleave guarantee).
    assert journey.session.events[0] == "lock_shared_catalog"
    assert journey.session.events[1] == ("lock_org", rs.org_lock_key(ORG))
    assert rs.CATALOG_LOCK_KEY == ca.CATALOG_LOCK_KEY


async def test_stale_preview_refused_and_recoverable_via_repreview(journey):
    await journey.platform_apply()
    await journey.preview()
    await journey.confirm_actions()

    # A newer platform run applies after the preview was anchored. The first
    # run's completed_at is the real apply wall clock, so this one must
    # complete relative to it, not to the fixture epoch.
    later = journey.platform_run.completed_at + timedelta(hours=1)
    newer = SimpleNamespace(
        id=uuid.uuid4(),
        from_version=V2,
        to_version="2026.3",
        status="applied",
        superseded_pairings=[],
        diff_summary=None,
        sanity_report=None,
        workbook_object_key=None,
        diff_detail_object_key=None,
        created_at=later,
        updated_at=later,
        completed_at=later,
    )
    journey.session.tables[CatalogImportRun].append(newer)
    await journey.session.commit()

    with pytest.raises(rs.StalePreviewError):
        await rs.apply_reconciliation_run(journey.session, ORG, journey.org_run.id)
    assert journey.org_run.status == "previewed"
    assert _scoped_by_id(journey.session)["GOV-C1"].selected is True  # untouched
