"""Tests for backend/services/catalog_apply.py (WP1b, plan §4.2.4-7, §4.7).

Repo unit-test pattern (no live database — DB access faked at the session
boundary, as in test_catalog_diff.py). The FakeSession here additionally
emulates TRANSACTION semantics: every mutation lands on in-memory rows, and
``rollback()`` restores the pre-transaction images exactly as Postgres would
discard an uncommitted transaction — which is what makes the injected
mid-apply-failure test able to assert the catalog is byte-identical.

Covered (WP1b acceptance list):
- transactionality: injected mid-apply failure -> catalog byte-identical;
- required_artifact_types (+extracted_at) and created_at preservation by
  construction, including a poisoned AddedEntity payload;
- idempotent re-apply (same staged diff twice = same end state mod timestamps);
- updated_at rotation on touched rows only (the api/catalog.py ETag basis);
- revert restores stored old values; revert blocker guard (org pinned to
  to_version -> typed refusal listing blockers); latest-applied-only guard;
- never-DELETE: the only deleted table is capability_theme_mappings;
- advisory lock taken first, on the documented shared key;
- theme upsert by theme_code + in-transaction mapping recompute;
- superseded-pairing write + validation failure rollback;
- the ledger version-authority helper;
- the two named stale module caches actually exist.

Fixture identifiers use a letter after the hyphen (``GOV-A1``) — opaque to the
code under test.
"""
from __future__ import annotations

import copy
import json
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import Delete, Select
from sqlalchemy.sql.elements import TextClause

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import (  # noqa: E402
    CapabilityTheme,
    CapabilityThemeMapping,
    SCFCatalogAssessmentObjective,
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
)
from models import CatalogImportRun, OrganizationCatalogState  # noqa: E402
from schemas_catalog_upgrade import (  # noqa: E402
    AddedEntity,
    CatalogEntityType,
    ChangedEntity,
    DeprecatedEntity,
    DiffDetail,
    EntityDiff,
    FieldChange,
    ResurrectedEntity,
)
from services import catalog_apply as ca  # noqa: E402
from services.catalog_diff import CONTROL_COMPARED_FIELDS  # noqa: E402


# ---------------------------------------------------------------------------
# Transactional FakeSession
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
)

ENTITY_TABLE_NAMES = {
    "scf_catalog_controls",
    "scf_catalog_domains",
    "scf_catalog_evidence",
    "scf_catalog_assessment_objectives",
}


def _row_attrs(obj) -> dict:
    return {
        k: copy.deepcopy(v) for k, v in vars(obj).items() if not k.startswith("_")
    }


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


class FakeSession:
    """In-memory tables + Postgres-like transaction semantics.

    - select(Model) returns every row of that table (WHERE ignored; the
      service maps by natural key itself);
    - delete(Model) empties the table and records the table name;
    - text() statements record advisory-lock acquisition;
    - rollback() restores the row images captured at transaction start,
      commit() makes them the new baseline.
    """

    def __init__(self, rows_by_model=None):
        self.tables = {model: [] for model in TABLES}
        for model, rows in (rows_by_model or {}).items():
            self.tables[model] = list(rows)
        self.events = []  # "lock" / ("delete", table) / "commit" / "rollback"
        self.deleted_tables = set()
        self.commits = 0
        self._pending = []
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
        self.events.append("rollback")
        self._pending = []
        for model, snap in self._snapshot.items():
            self.tables[model] = [obj for obj, _ in snap]
            for obj, attrs in snap:
                current = [k for k in vars(obj) if not k.startswith("_")]
                for key in current:
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
                    self.tables[model].append(obj)
                    break
            else:
                raise AssertionError(f"add() of unknown row type: {type(obj)}")
        self._pending = []

    async def execute(self, stmt, params=None):
        if isinstance(stmt, TextClause):
            assert "pg_advisory_xact_lock" in str(stmt)
            assert params == {"key": ca.CATALOG_LOCK_KEY}
            self.events.append("lock")
            return _FakeResult([])
        if isinstance(stmt, Delete):
            table_name = stmt.table.name
            self.events.append(("delete", table_name))
            self.deleted_tables.add(table_name)
            for model in TABLES:
                if model.__tablename__ == table_name:
                    self.tables[model] = []
            return _FakeResult([])
        if isinstance(stmt, Select):
            await self.flush()  # autoflush semantics
            entity = stmt.column_descriptions[0]["entity"]
            self.events.append(("select", entity.__tablename__))
            return _FakeResult(self.tables[entity])
        raise AssertionError(f"unhandled statement: {stmt!r}")


# ---------------------------------------------------------------------------
# Row / diff fixtures
# ---------------------------------------------------------------------------

T0 = datetime(2026, 1, 1, 0, 0, 0)
FROM_V = "2026.1"
TO_V = "2026.2"


def _control_row(scf_id, name, status="active", **over):
    attrs = {f: None for f in CONTROL_COMPARED_FIELDS}
    attrs.update(
        scf_id=scf_id,
        scf_domain="Governance",
        control_name=name,
        control_description=f"Mechanisms exist for {name}.",
        evidence_requests=[],
        framework_mappings={},
        risk_codes=[],
        threat_codes=[],
        status=status,
        retired_in_version=None,
        superseded_by=None,
        required_artifact_types=[{"type": "policy", "mandatory": True}],
        required_artifact_types_extracted_at=T0,
        catalog_version=FROM_V,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _domain_row(identifier, **over):
    attrs = dict(
        identifier=identifier,
        order=1,
        name="Governance",
        principle="Execute a program.",
        principle_intent="Intent.",
        status="active",
        retired_in_version=None,
        superseded_by=None,
        catalog_version=FROM_V,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _theme_row(theme_id, code, **over):
    attrs = dict(
        id=theme_id,
        theme_code=code,
        name=f"Theme {code}",
        description="Theme.",
        ksi_reference=None,
        display_order=0,
        icon=None,
        catalog_version=FROM_V,
        created_at=T0,
        updated_at=T0,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


def _run(status="staged", to_version=TO_V, from_version=FROM_V, pairings=None, **over):
    attrs = dict(
        id=uuid.uuid4(),
        from_version=from_version,
        to_version=to_version,
        status=status,
        superseded_pairings=pairings or [],
        created_at=T0,
        updated_at=T0,
        completed_at=None,
        workbook_object_key=None,
        diff_detail_object_key=None,
        diff_summary=None,
        sanity_report=None,
    )
    attrs.update(over)
    return SimpleNamespace(**attrs)


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


def _detail(entities, from_version=FROM_V, to_version=TO_V) -> DiffDetail:
    return DiffDetail(from_version=from_version, to_version=to_version, entities=entities)


def _controls_diff(**kwargs) -> DiffDetail:
    return _detail({CatalogEntityType.CONTROLS: EntityDiff(**kwargs)})


def _state(session: FakeSession) -> str:
    """Byte-comparable image of the four catalog entity tables."""
    image = {}
    for model, key_attr in (
        (SCFCatalogControl, "scf_id"),
        (SCFCatalogDomain, "identifier"),
        (SCFCatalogEvidence, "evidence_id"),
        (SCFCatalogAssessmentObjective, "ao_id"),
    ):
        image[model.__tablename__] = sorted(
            (_row_attrs(row) for row in session.tables[model]),
            key=lambda attrs: attrs[key_attr],
        )
    return json.dumps(image, sort_keys=True, default=str)


def _state_no_timestamps(session: FakeSession) -> str:
    raw = json.loads(_state(session))
    for rows in raw.values():
        for attrs in rows:
            attrs.pop("updated_at", None)
    return json.dumps(raw, sort_keys=True)


def _session(controls=(), domains=(), themes=(), mappings=(), runs=(), org_states=()):
    return FakeSession(
        {
            SCFCatalogControl: list(controls),
            SCFCatalogDomain: list(domains),
            CapabilityTheme: list(themes),
            CapabilityThemeMapping: list(mappings),
            CatalogImportRun: list(runs),
            OrganizationCatalogState: list(org_states),
        }
    )


# ---------------------------------------------------------------------------
# Apply — happy path + lock + counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_all_change_classes():
    kept = _control_row("GOV-A1", "Kept")
    changed = _control_row("GOV-A2", "Old Name")
    retired = _control_row("GOV-A3", "Retiring")
    zombie = _control_row("GOV-A4", "Zombie", status="deprecated",
                          retired_in_version="2025.4", superseded_by="GOV-A2")
    session = _session(controls=[kept, changed, retired, zombie])
    run = _run()
    detail = _controls_diff(
        added=[AddedEntity(key="GOV-A5", name="Brand New",
                           data={"scf_domain": "Governance", "control_name": "Brand New",
                                 "control_description": "Mechanisms exist.",
                                 "framework_mappings": {"nist_800_53_r5": ["AC-2"]}})],
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name")})],
        deprecated=[DeprecatedEntity(key="GOV-A3")],
        resurrected=[ResurrectedEntity(key="GOV-A4",
                                       fields={"control_name": FieldChange(old="Zombie", new="Revived")})],
        unchanged=["GOV-A1"],
    )

    report = await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    assert session.events[0] == "lock"
    assert session.commits == 1

    by_id = {c.scf_id: c for c in session.tables[SCFCatalogControl]}
    assert set(by_id) == {"GOV-A1", "GOV-A2", "GOV-A3", "GOV-A4", "GOV-A5"}

    assert by_id["GOV-A2"].control_name == "New Name"
    assert by_id["GOV-A2"].catalog_version == TO_V

    assert by_id["GOV-A3"].status == "deprecated"
    assert by_id["GOV-A3"].retired_in_version == TO_V

    assert by_id["GOV-A4"].status == "active"
    assert by_id["GOV-A4"].retired_in_version is None
    assert by_id["GOV-A4"].superseded_by is None
    assert by_id["GOV-A4"].control_name == "Revived"

    added = by_id["GOV-A5"]
    assert isinstance(added, SCFCatalogControl)
    assert added.status == "active"
    assert added.control_name == "Brand New"
    assert added.catalog_version == TO_V

    # Untouched row: nothing moved, version NOT restamped (touched rows only).
    assert by_id["GOV-A1"].catalog_version == FROM_V
    assert by_id["GOV-A1"].updated_at == T0

    assert run.status == "applied"
    assert run.completed_at is not None

    counts = report.entities["controls"]
    assert (counts.added, counts.changed, counts.deprecated, counts.resurrected) == (1, 1, 1, 1)
    assert list(report.stale_module_caches) == list(ca.STALE_MODULE_CACHES)


@pytest.mark.asyncio
async def test_apply_rejects_run_version_mismatch():
    session = _session()
    run = _run(to_version="2026.3")
    with pytest.raises(ca.ApplyConsistencyError):
        await ca.apply_catalog_run(session, run, _controls_diff(), themes_json=THEMES_JSON)
    assert session.commits == 0


# ---------------------------------------------------------------------------
# Transactionality (plan §4.7: injected mid-apply failure -> byte-identical)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_injected_midapply_failure_leaves_catalog_byte_identical(monkeypatch):
    """Force a failure AFTER the entity upserts have mutated rows; rollback
    must leave every table byte-identical and nothing committed."""
    changed = _control_row("GOV-A2", "Old Name")
    retired = _control_row("GOV-A3", "Retiring")
    session = _session(controls=[changed, retired],
                       themes=[_theme_row(1, "IAM")])
    run = _run()
    before = _state(session)

    async def boom(*args, **kwargs):
        raise RuntimeError("injected mid-apply failure")

    monkeypatch.setattr(ca, "_upsert_themes", boom)

    detail = _controls_diff(
        added=[AddedEntity(key="GOV-A5", data={"control_name": "New"})],
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name")})],
        deprecated=[DeprecatedEntity(key="GOV-A3")],
    )
    with pytest.raises(RuntimeError, match="injected mid-apply failure"):
        await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    assert session.commits == 0
    assert _state(session) == before
    assert run.status == "staged"  # run row rolled back too


@pytest.mark.asyncio
async def test_stale_diff_failure_rolls_back():
    """A changed key missing from the live catalog (stale staged diff) aborts
    the whole apply — earlier same-transaction mutations are rolled back."""
    domain = _domain_row("GOV")
    session = _session(domains=[domain])
    run = _run()
    before = _state(session)
    detail = _detail({
        # Domains are processed first and DO mutate...
        CatalogEntityType.DOMAINS: EntityDiff(
            changed=[ChangedEntity(key="GOV",
                                   fields={"name": FieldChange(old="Governance", new="Gov 2")})]
        ),
        # ...then the stale controls entry blows up.
        CatalogEntityType.CONTROLS: EntityDiff(
            changed=[ChangedEntity(key="GOV-A9",
                                   fields={"control_name": FieldChange(old="x", new="y")})]
        ),
    })
    with pytest.raises(ca.ApplyConsistencyError, match="GOV-A9"):
        await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    assert session.commits == 0
    assert _state(session) == before


# ---------------------------------------------------------------------------
# Preservation by construction (required_artifact_types + created_at)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_required_artifact_types_and_created_at_preserved():
    changed = _control_row("GOV-A2", "Old Name")
    original_artifacts = copy.deepcopy(changed.required_artifact_types)
    session = _session(controls=[changed])
    run = _run()
    detail = _controls_diff(
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name")})],
        # Poisoned payload: a workbook row must never be able to write the
        # excluded columns — the allowlist filters them out.
        added=[AddedEntity(key="GOV-A6", data={
            "control_name": "Poisoned",
            "required_artifact_types": [{"type": "evil"}],
            "required_artifact_types_extracted_at": "2026-08-20T00:00:00",
            "created_at": "1999-01-01T00:00:00",
            "catalog_version": "9999.9",
            "status": "deprecated",
        })],
    )
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    assert changed.required_artifact_types == original_artifacts
    assert changed.required_artifact_types_extracted_at == T0
    assert changed.created_at == T0

    added = next(c for c in session.tables[SCFCatalogControl]
                 if getattr(c, "scf_id", None) == "GOV-A6")
    assert getattr(added, "required_artifact_types", None) in (None, [])
    assert getattr(added, "required_artifact_types_extracted_at", None) is None
    assert added.status == "active"          # lifecycle owned by apply, not payload
    assert added.catalog_version == TO_V     # not the poisoned "9999.9"


# ---------------------------------------------------------------------------
# Idempotent re-apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_reapply_same_end_state():
    controls = [_control_row("GOV-A2", "Old Name"), _control_row("GOV-A3", "Retiring")]
    session = _session(controls=controls, themes=[_theme_row(1, "IAM")])
    run = _run()
    detail = _controls_diff(
        added=[AddedEntity(key="GOV-A5", data={"control_name": "Brand New",
                                               "framework_mappings": {"nist_800_53_r5": ["AC-1"]}})],
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name")})],
        deprecated=[DeprecatedEntity(key="GOV-A3")],
    )
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    first = _state_no_timestamps(session)
    first_mappings = [(m.theme_id, m.scf_id, m.relevance)
                      for m in session.tables[CapabilityThemeMapping]]

    run.status = "staged"
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    assert _state_no_timestamps(session) == first
    assert [(m.theme_id, m.scf_id, m.relevance)
            for m in session.tables[CapabilityThemeMapping]] == first_mappings
    # No duplicate row was inserted for the already-present added key.
    assert sum(1 for c in session.tables[SCFCatalogControl]
               if getattr(c, "scf_id", None) == "GOV-A5") == 1


# ---------------------------------------------------------------------------
# updated_at rotation (ETag basis)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_updated_at_rotates_on_touched_rows_only():
    touched = _control_row("GOV-A2", "Old Name")
    untouched = _control_row("GOV-A1", "Kept")
    session = _session(controls=[touched, untouched])
    run = _run()
    detail = _controls_diff(
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name")})],
        unchanged=["GOV-A1"],
    )
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    # max(updated_at) moves => the api/catalog.py count+max ETag rotates.
    assert touched.updated_at > T0
    assert untouched.updated_at == T0


# ---------------------------------------------------------------------------
# Never DELETE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_and_revert_never_delete_entity_rows():
    retired = _control_row("GOV-A3", "Retiring")
    session = _session(controls=[retired],
                       mappings=[SimpleNamespace(theme_id=1, scf_id="GOV-A3",
                                                 relevance="primary")])
    run = _run()
    detail = _controls_diff(
        added=[AddedEntity(key="GOV-A5", data={"control_name": "New"})],
        deprecated=[DeprecatedEntity(key="GOV-A3")],
    )
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    await ca.revert_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    # The ONLY table ever deleted from is the derived mapping table.
    assert session.deleted_tables == {"capability_theme_mappings"}
    assert not (session.deleted_tables & ENTITY_TABLE_NAMES)
    # Both rows still exist after apply + revert.
    ids = {getattr(c, "scf_id", None) for c in session.tables[SCFCatalogControl]}
    assert ids == {"GOV-A3", "GOV-A5"}


# ---------------------------------------------------------------------------
# Themes + mapping recompute
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_theme_upsert_and_mapping_recompute():
    ctrl = _control_row("GOV-A2", "Old Name",
                        framework_mappings={"nist_800_53_r5": ["AC-1", "AC-2"]})
    stale_theme = _theme_row(7, "IAM", name="Stale Name")
    stale_mapping = SimpleNamespace(theme_id=99, scf_id="GONE-A1", relevance="primary")
    session = _session(controls=[ctrl], themes=[stale_theme], mappings=[stale_mapping])
    run = _run()
    detail = _controls_diff(
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name")})],
    )
    report = await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    # Upserted by theme_code: same row object updated in place, no duplicate.
    assert stale_theme.name == "Identity & Access Management"
    assert len(session.tables[CapabilityTheme]) == 1

    # Mappings rebuilt from the post-apply controls: the stale row is gone and
    # AC-1/AC-2 collapse to one (theme, control) pair.
    mappings = [(m.theme_id, m.scf_id, m.relevance)
                for m in session.tables[CapabilityThemeMapping]]
    assert mappings == [(7, "GOV-A2", "primary")]
    assert report.themes_upserted == 1
    assert report.mappings_recomputed == 1


# ---------------------------------------------------------------------------
# Superseded pairings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pairings_written_onto_deprecated_rows():
    retired = _control_row("GOV-A3", "Retiring")
    successor = _control_row("GOV-A2", "Successor")
    session = _session(controls=[retired, successor])
    run = _run(pairings=[{"deprecated_scf_id": "GOV-A3", "superseded_by": "GOV-A2"}])
    detail = _controls_diff(deprecated=[DeprecatedEntity(key="GOV-A3")])
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    assert retired.superseded_by == "GOV-A2"
    assert retired.status == "deprecated"


@pytest.mark.asyncio
async def test_invalid_pairing_successor_rolls_back_apply():
    retired = _control_row("GOV-A3", "Retiring")
    session = _session(controls=[retired])
    run = _run(pairings=[{"deprecated_scf_id": "GOV-A3", "superseded_by": "GOV-A9"}])
    before = _state(session)
    detail = _controls_diff(deprecated=[DeprecatedEntity(key="GOV-A3")])
    with pytest.raises(ca.PairingValidationError) as exc:
        await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    assert "GOV-A3->GOV-A9" in exc.value.invalid
    assert session.commits == 0
    assert _state(session) == before


# ---------------------------------------------------------------------------
# Revert
# ---------------------------------------------------------------------------


def _applied_fixture():
    """A catalog with an applied run, built by actually running apply()."""
    kept = _control_row("GOV-A1", "Kept")
    changed = _control_row("GOV-A2", "Old Name",
                           framework_mappings={"nist_800_53_r5": ["AC-1"]})
    retired = _control_row("GOV-A3", "Retiring")
    zombie = _control_row("GOV-A4", "Zombie", status="deprecated",
                          retired_in_version="2025.4")
    session = _session(controls=[kept, changed, retired, zombie],
                       themes=[_theme_row(1, "IAM")])
    run = _run()
    session.tables[CatalogImportRun].append(run)
    detail = _controls_diff(
        added=[AddedEntity(key="GOV-A5", data={"control_name": "Brand New"})],
        changed=[ChangedEntity(key="GOV-A2",
                               fields={"control_name": FieldChange(old="Old Name", new="New Name"),
                                       "framework_mappings": FieldChange(
                                           old={"nist_800_53_r5": ["AC-1"]},
                                           new={"nist_800_53_r5": ["AC-1", "AC-9"]})})],
        deprecated=[DeprecatedEntity(key="GOV-A3")],
        resurrected=[ResurrectedEntity(key="GOV-A4",
                                       fields={"control_name": FieldChange(old="Zombie", new="Revived")})],
        unchanged=["GOV-A1"],
    )
    return session, run, detail


@pytest.mark.asyncio
async def test_revert_restores_old_values():
    session, run, detail = _applied_fixture()
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    report = await ca.revert_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    by_id = {getattr(c, "scf_id", None): c for c in session.tables[SCFCatalogControl]}

    # Changed fields restored from stored OLD values — including the
    # framework_mappings column via the controls entity (the revert authority).
    assert by_id["GOV-A2"].control_name == "Old Name"
    assert by_id["GOV-A2"].framework_mappings == {"nist_800_53_r5": ["AC-1"]}
    assert by_id["GOV-A2"].catalog_version == FROM_V

    # Run-deprecated -> re-activated.
    assert by_id["GOV-A3"].status == "active"
    assert by_id["GOV-A3"].retired_in_version is None

    # Run-added -> deprecated, never deleted.
    assert by_id["GOV-A5"].status == "deprecated"
    assert by_id["GOV-A5"].retired_in_version == TO_V

    # Run-resurrected -> re-deprecated with old field values.
    assert by_id["GOV-A4"].status == "deprecated"
    assert by_id["GOV-A4"].control_name == "Zombie"

    assert run.status == "reverted"
    assert report.action == "reverted"
    assert session.events[0] == "lock"


@pytest.mark.asyncio
async def test_revert_blocked_while_org_pinned_to_to_version():
    session, run, detail = _applied_fixture()
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    org_id = uuid.uuid4()
    session.tables[OrganizationCatalogState].extend([
        SimpleNamespace(organization_id=org_id, reconciled_catalog_version=TO_V),
        SimpleNamespace(organization_id=uuid.uuid4(), reconciled_catalog_version=FROM_V),
    ])
    before = _state(session)

    with pytest.raises(ca.RevertBlockedError) as exc:
        await ca.revert_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    assert exc.value.blockers == [str(org_id)]  # typed error lists the blockers
    assert exc.value.to_version == TO_V
    assert run.status == "applied"  # run untouched
    assert _state(session) == before


@pytest.mark.asyncio
async def test_revert_refused_unless_latest_applied():
    session, run, detail = _applied_fixture()
    await ca.apply_catalog_run(session, run, detail, themes_json=THEMES_JSON)

    newer = _run(status="applied", from_version=TO_V, to_version="2026.3",
                 completed_at=run.completed_at + timedelta(hours=1))
    session.tables[CatalogImportRun].append(newer)
    await session.commit()  # baseline: newer run is part of the ledger

    with pytest.raises(ca.RevertNotLatestError):
        await ca.revert_catalog_run(session, run, detail, themes_json=THEMES_JSON)
    assert run.status == "applied"


@pytest.mark.asyncio
async def test_revert_refused_for_non_applied_run():
    session, run, detail = _applied_fixture()
    with pytest.raises(ca.RevertNotLatestError):
        await ca.revert_catalog_run(session, run, detail, themes_json=THEMES_JSON)


# ---------------------------------------------------------------------------
# Version authority helper (plan §4.2.5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_current_version_is_latest_applied_runs_to_version():
    r1 = _run(status="applied", to_version="2026.1", completed_at=T0)
    r2 = _run(status="applied", to_version=TO_V,
              completed_at=T0 + timedelta(days=1))
    r3 = _run(status="staged", to_version="2026.3")
    r4 = _run(status="reverted", to_version="2026.4",
              completed_at=T0 + timedelta(days=2))
    session = _session(runs=[r1, r2, r3, r4])

    # The fake ignores WHERE/ORDER BY, so emulate them through the same
    # selection rule the query encodes: applied only, newest completed first.
    class OrderedFake(FakeSession):
        async def execute(self, stmt, params=None):
            result = await super().execute(stmt, params)
            if isinstance(stmt, Select) and stmt.column_descriptions[0]["entity"] is CatalogImportRun:
                rows = [r for r in result.all() if r.status == "applied"]
                rows.sort(key=lambda r: r.completed_at, reverse=True)
                return _FakeResult(rows[:1])
            return result

    ordered = OrderedFake()
    ordered.tables = session.tables
    assert await ca.get_current_catalog_version(ordered) == TO_V

    empty = _session()
    assert await ca.get_current_catalog_version(empty) is None


# ---------------------------------------------------------------------------
# Stale module caches — the two names in the apply report must exist
# ---------------------------------------------------------------------------


def test_named_stale_module_caches_exist():
    import services.validation_service as validation_service

    assert hasattr(validation_service, "_collection_interfaces")
    assert "services.validation_service._collection_interfaces" in ca.STALE_MODULE_CACHES

    import api.capabilities as capabilities

    loader = getattr(capabilities, "_load_collection_interfaces")
    assert hasattr(loader, "cache_clear")  # functools.lru_cache wrapper
    assert "api.capabilities._load_collection_interfaces" in ca.STALE_MODULE_CACHES


def test_trust_portal_purge_patterns_match_cache_key_shapes():
    from cache import make_cache_key
    import fnmatch

    short_key = make_cache_key("acme-org", prefix="trust_portal")
    long_key = make_cache_key("x" * 300, prefix="trust_portal")
    assert any(fnmatch.fnmatch(short_key, p) for p in ca.TRUST_PORTAL_CACHE_PATTERNS)
    assert any(fnmatch.fnmatch(long_key, p) for p in ca.TRUST_PORTAL_CACHE_PATTERNS)
