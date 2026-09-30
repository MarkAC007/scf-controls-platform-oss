"""Tests for services/scoping_service.py (catalog upgrade WP2a).

The bulk-scope/bulk-unscope endpoint bodies were extracted into
``bulk_scope_frameworks`` / ``bulk_unscope_frameworks`` so per-org catalog
reconciliation can re-materialise scope through the same code path. These
tests prove:

- the pre-extraction endpoint behaviour is unchanged: three-way partition
  (new / needs_update / already_scoped), idempotent re-run, overlap-protected
  unscope, counts and messages;
- the catalog query now excludes deprecated controls (``status = 'active'``);
- organization_framework_selections rows are written on scope
  (source='bulk_scope', active=True), deactivated on unscope, and
  reactivated on re-scope.

Pure-logic unit tests over a scripted fake async session in the style of the
other backend tests (no real Postgres): each ``execute`` pops the next result
in order; persisted rows are captured via ``add``. Control ids are opaque
strings to the service, so neutral placeholders are used.
"""
from __future__ import annotations

import os
import sys
from uuid import uuid4
from types import SimpleNamespace

import pytest  # noqa: F401

# CI runs pytest from the repo root where backend/pytest.ini's
# asyncio_mode=auto is not picked up; mark explicitly (repo convention).
pytestmark = pytest.mark.asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scoping_service import (  # noqa: E402
    bulk_scope_frameworks,
    bulk_unscope_frameworks,
    effective_evidence_ids,
    framework_scope_summary,
    orphan_impact,
    preview_framework_change,
    required_controls_by_evidence,
    set_individual_scope_override,
)
from api.scoped_controls import reset_all_scope  # noqa: E402
from models import OrganizationFrameworkSelection, ScopedControl  # noqa: E402
import catalog_models  # noqa: E402,F401 — registers mappers referenced by models.System

ORG_ID = uuid4()
USER_ID = uuid4()


class _Result:
    """Minimal stand-in for a SQLAlchemy Result: fetchall() and scalars().all()."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return self._rows


class _FakeAsyncSession:
    """Scripted async session: each execute() pops the next result in order."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.statements = []
        self.added = []
        self.commits = 0

    async def execute(self, stmt, params=None):
        self.statements.append((stmt, params))
        if not self._scripted:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(self._scripted.pop(0))


    async def refresh(self, _row):
        return None
    def add(self, row):
        self.added.append(row)

    async def commit(self):
        self.commits += 1


def _selection(framework_id, active=True, source="bulk_scope"):
    return OrganizationFrameworkSelection(
        organization_id=ORG_ID,
        framework_id=framework_id,
        source=source,
        active=active,
    )


def _added_controls(db):
    return [r for r in db.added if isinstance(r, ScopedControl)]


def _added_selections(db):
    return [r for r in db.added if isinstance(r, OrganizationFrameworkSelection)]


# ---------------------------------------------------------------------------
# bulk_scope_frameworks
# ---------------------------------------------------------------------------

class TestBulkScope:
    async def test_three_way_partition(self):
        """New controls inserted, deselected ones flipped, selected ones skipped."""
        db = _FakeAsyncSession([
            [("ctl-new",), ("ctl-flip",), ("ctl-kept",)],   # catalog query
            [("ctl-flip", False), ("ctl-kept", True)],       # existing scoped controls
            [],                                              # UPDATE needs_update
            [],                                              # selections select
        ])

        result = await bulk_scope_frameworks(
            db, ORG_ID, ["iso_27001_2022"], user_id=USER_ID
        )

        assert (result.added, result.updated, result.skipped) == (1, 1, 1)
        assert result.total == 3
        assert result.frameworks_processed == ["iso_27001_2022"]
        assert "Added 1 new controls" in result.message
        assert "updated 1 existing controls" in result.message
        assert "(1 already in scope)" in result.message

        controls = _added_controls(db)
        assert len(controls) == 1
        new = controls[0]
        assert new.scf_id == "ctl-new"
        assert new.organization_id == ORG_ID
        assert new.selected is True
        assert new.implementation_status == "not_started"
        assert new.selection_reason == "Bulk scoped from: iso_27001_2022"
        assert db.commits == 1

    async def test_catalog_query_filters_active_status(self):
        """The extracted catalog query must exclude deprecated controls."""
        db = _FakeAsyncSession([
            [],  # catalog query — empty, early return
        ])

        await bulk_scope_frameworks(db, ORG_ID, ["iso_27001_2022"])

        catalog_stmt = str(db.statements[0][0])
        assert "status = 'active'" in catalog_stmt

    async def test_no_controls_found_is_a_no_op(self):
        """Empty catalog match: zero counts, nothing written, no commit."""
        db = _FakeAsyncSession([
            [],  # catalog query
        ])

        result = await bulk_scope_frameworks(db, ORG_ID, ["made_up_fw"])

        assert (result.added, result.updated, result.skipped, result.total) == (0, 0, 0, 0)
        assert result.message == "No controls found for frameworks: made_up_fw"
        assert db.added == []
        assert db.commits == 0

    async def test_idempotent_rerun_skips_everything(self):
        """Re-running with all controls selected and selection active changes nothing."""
        db = _FakeAsyncSession([
            [("ctl-a",), ("ctl-b",)],                       # catalog query
            [("ctl-a", True), ("ctl-b", True)],             # all already selected
            [_selection("iso_27001_2022", active=True)],    # active selection exists
        ])

        result = await bulk_scope_frameworks(
            db, ORG_ID, ["iso_27001_2022"], user_id=USER_ID
        )

        assert (result.added, result.updated, result.skipped) == (0, 0, 2)
        assert result.message == "All 2 controls from iso_27001_2022 already in scope"
        assert db.added == []
        assert db.commits == 0  # nothing changed → no commit

    async def test_custom_selection_reason_is_used(self):
        db = _FakeAsyncSession([
            [("ctl-new",)],
            [],
            [],
        ])

        await bulk_scope_frameworks(
            db, ORG_ID, ["soc2"], user_id=USER_ID,
            selection_reason="Required by SOC 2 certification",
        )

        assert _added_controls(db)[0].selection_reason == "Required by SOC 2 certification"

    async def test_writes_framework_selection_on_scope(self):
        """A fresh bulk-scope inserts an active bulk_scope selection per framework."""
        db = _FakeAsyncSession([
            [("ctl-new",)],  # catalog
            [],              # nothing scoped yet
            [],              # no selections yet
        ])

        await bulk_scope_frameworks(db, ORG_ID, ["iso_27001_2022"], user_id=USER_ID)

        selections = _added_selections(db)
        assert len(selections) == 1
        sel = selections[0]
        assert sel.organization_id == ORG_ID
        assert sel.framework_id == "iso_27001_2022"
        assert sel.source == "bulk_scope"
        assert sel.active is True
        assert sel.selected_by == USER_ID
        assert db.commits == 1

    async def test_writes_one_selection_per_requested_framework(self):
        db = _FakeAsyncSession([
            [("ctl-a",), ("ctl-b",)],
            [],
            [],
        ])

        await bulk_scope_frameworks(
            db, ORG_ID, ["iso_27001_2022", "soc2"], user_id=USER_ID
        )

        assert {s.framework_id for s in _added_selections(db)} == {"iso_27001_2022", "soc2"}

    async def test_reactivates_inactive_selection_on_rescope(self):
        """Re-scoping a previously unscoped framework flips the row back to active."""
        inactive = _selection("iso_27001_2022", active=False)
        db = _FakeAsyncSession([
            [("ctl-flip",)],         # catalog
            [("ctl-flip", False)],   # control was deselected by the earlier unscope
            [],                      # UPDATE needs_update
            [inactive],              # existing (inactive) selection row
        ])

        result = await bulk_scope_frameworks(
            db, ORG_ID, ["iso_27001_2022"], user_id=USER_ID
        )

        assert result.updated == 1
        assert inactive.active is True
        assert inactive.source == "bulk_scope"
        assert inactive.selected_by == USER_ID
        assert _added_selections(db) == []  # reactivated, not duplicated
        assert db.commits == 1

    async def test_selection_only_change_still_commits(self):
        """All controls already in scope but the selection row is new → commit."""
        db = _FakeAsyncSession([
            [("ctl-kept",)],
            [("ctl-kept", True)],  # already selected (e.g. via overlapping framework)
            [],                    # no selection row for this framework yet
        ])

        result = await bulk_scope_frameworks(db, ORG_ID, ["soc2"], user_id=USER_ID)

        assert (result.added, result.updated, result.skipped) == (0, 0, 1)
        assert len(_added_selections(db)) == 1
        assert db.commits == 1

    async def test_commit_false_never_commits(self):
        """Caller-managed transactions (reconciliation apply) suppress the commit."""
        db = _FakeAsyncSession([
            [("ctl-new",)],
            [],
            [],
        ])

        await bulk_scope_frameworks(
            db, ORG_ID, ["iso_27001_2022"], user_id=USER_ID, commit=False
        )

        assert _added_controls(db) and _added_selections(db)
        assert db.commits == 0


# ---------------------------------------------------------------------------
# bulk_unscope_frameworks
# ---------------------------------------------------------------------------

def _unscope_script(catalog_rows, in_scope, active_frameworks, selections,
                    with_update=True, orphan_script=None, untrack_script=None):
    """Scripted results in the service's execute order.

    ``orphan_script`` is the evidence-impact probe that runs whenever something
    is removable: [in-scope (scf_id, evidence_requests) rows, then — only if
    that leaves some evidence unrequired — tracked (id, evidence_id) rows, then
    — only if any are tracked — the open-task count]. The default is a probe
    that finds no evidence_requests at all, so it stops after one query.
    ``untrack_script`` is the pair of UPDATEs issued for action="untrack".
    """
    script = [
        catalog_rows,                      # catalog query (scf_id, framework_mappings)
        [(s,) for s in in_scope],          # in-scope scf_ids
        [(framework_id,) for framework_id in active_frameworks],
    ]
    if with_update:
        script.extend(orphan_script if orphan_script is not None else [[]])
        script.append([])                  # UPDATE selected=False
        script.extend(untrack_script or [])
    script.append(selections)              # active selections select
    return script


class TestBulkUnscope:
    async def test_removes_unprotected_and_protects_overlap(self):
        """Controls shared with another explicitly-scoped framework are protected."""
        db = _FakeAsyncSession(_unscope_script(
            catalog_rows=[
                ("ctl-shared", {"iso_27017_2015": ["5.1"], "iso_27001_2022": ["5.1"]}),
                ("ctl-solo", {"iso_27017_2015": ["5.2"]}),
                ("ctl-out", {"iso_27017_2015": ["5.3"]}),
            ],
            in_scope=["ctl-shared", "ctl-solo"],  # ctl-out already out of scope
            active_frameworks=["iso_27001_2022", "iso_27017_2015"],
            selections=[_selection("iso_27017_2015", active=True)],
        ))

        result = await bulk_unscope_frameworks(db, ORG_ID, ["iso_27017_2015"])

        assert result.removed == 1                 # ctl-solo
        assert result.protected == 1               # ctl-shared (iso_27001_2022 overlap)
        assert result.already_out_of_scope == 1    # ctl-out
        assert result.total == 3
        assert result.protected_by == {"iso_27001_2022": 1}
        assert "Removed 1 controls from iso_27017_2015" in result.message
        assert db.commits == 1

    async def test_no_catalog_match_is_a_no_op(self):
        db = _FakeAsyncSession([
            [],  # catalog query
        ])

        result = await bulk_unscope_frameworks(db, ORG_ID, ["made_up_fw"])

        assert (result.removed, result.protected, result.already_out_of_scope, result.total) == (0, 0, 0, 0)
        assert result.message == "No controls found for frameworks: made_up_fw"
        assert db.commits == 0

    async def test_deactivates_framework_selection_on_unscope(self):
        active_sel = _selection("iso_27017_2015", active=True)
        db = _FakeAsyncSession(_unscope_script(
            catalog_rows=[("ctl-solo", {"iso_27017_2015": ["5.2"]})],
            in_scope=["ctl-solo"],
            active_frameworks=["iso_27017_2015"],
            selections=[active_sel],
        ))

        result = await bulk_unscope_frameworks(db, ORG_ID, ["iso_27017_2015"])

        assert result.removed == 1
        assert active_sel.active is False
        assert db.commits == 1

    async def test_all_protected_still_deactivates_selection(self):
        """Even when every control is overlap-protected, the framework selection is withdrawn."""
        active_sel = _selection("iso_27017_2015", active=True)
        db = _FakeAsyncSession(_unscope_script(
            catalog_rows=[
                ("ctl-shared", {"iso_27017_2015": ["5.1"], "iso_27001_2022": ["5.1"]}),
            ],
            in_scope=["ctl-shared"],
            active_frameworks=["iso_27001_2022", "iso_27017_2015"],
            selections=[active_sel],
            with_update=False,  # nothing removable → no UPDATE issued
        ))

        result = await bulk_unscope_frameworks(db, ORG_ID, ["iso_27017_2015"])

        assert result.removed == 0
        assert result.protected == 1
        assert "shared with other in-scope frameworks" in result.message
        assert active_sel.active is False
        assert db.commits == 1  # selection deactivation alone still commits


# ---------------------------------------------------------------------------
# Structured precedence and server-authoritative framework views (#1049)
# ---------------------------------------------------------------------------

class TestStructuredScopePrecedence:
    async def test_framework_materialisation_preserves_explicit_exclusion(self):
        db = _FakeAsyncSession([
            [("ctl-excluded",)],
            [("ctl-excluded", True)],  # CASE folds explicit exclusion into effective selected
            [],
        ])
        result = await bulk_scope_frameworks(db, ORG_ID, ["iso_27001_2022"])
        assert (result.added, result.updated, result.skipped) == (0, 0, 1)
        assert _added_controls(db) == []
        assert not [stmt for stmt, _ in db.statements if "UPDATE" in str(stmt)]

    async def test_framework_removal_preserves_individual_inclusion(self):
        active = _selection("iso_27001_2022", active=True)
        db = _FakeAsyncSession([
            [("ctl-included", {"iso_27001_2022": ["A.1"]})],
            [("ctl-included", "include")],
            [("iso_27001_2022",)],
            [active],
        ])
        result = await bulk_unscope_frameworks(db, ORG_ID, ["iso_27001_2022"])
        assert result.removed == 0
        assert result.protected == 1
        assert result.protected_by == {"individual_inclusion": 1}
        assert active.active is False

    async def test_exclusion_requires_and_persists_a_rationale(self):
        control = ScopedControl(organization_id=ORG_ID, scf_id="ctl-one", selected=True)
        catalog = SimpleNamespace(
            scf_id="ctl-one", framework_mappings={"iso_27001_2022": ["A.1"]}
        )
        db = _FakeAsyncSession([[control], [catalog]])
        with pytest.raises(ValueError, match="rationale"):
            await set_individual_scope_override(
                db, ORG_ID, "ctl-one", "exclude", "  ", USER_ID, commit=False
            )

        db = _FakeAsyncSession([[control], [catalog]])
        updated = await set_individual_scope_override(
            db, ORG_ID, "ctl-one", "exclude", "Compensating control", USER_ID,
            commit=False,
        )
        assert updated.selected is False
        assert updated.scope_override == "exclude"
        assert updated.scope_override_reason == "Compensating control"
        assert updated.out_of_scope_justification == "Compensating control"
        assert updated.scope_override_set_by == USER_ID


class TestFrameworkScopeViews:
    async def test_remove_preview_returns_exact_overlap_and_exception_sets(self):
        catalog = [
            SimpleNamespace(scf_id="leave", framework_mappings={"fw": ["1"]}),
            SimpleNamespace(scf_id="shared", framework_mappings={"fw": ["2"], "other": ["2"]}),
            SimpleNamespace(scf_id="included", framework_mappings={"fw": ["3"]}),
            SimpleNamespace(scf_id="excluded", framework_mappings={"fw": ["4"]}),
            SimpleNamespace(scf_id="already-out", framework_mappings={"fw": ["5"]}),
        ]
        scoped = [
            ScopedControl(organization_id=ORG_ID, scf_id="leave", selected=True),
            ScopedControl(organization_id=ORG_ID, scf_id="shared", selected=True),
            ScopedControl(organization_id=ORG_ID, scf_id="included", selected=True, scope_override="include"),
            ScopedControl(
                organization_id=ORG_ID, scf_id="excluded", selected=False,
                scope_override="exclude", scope_override_reason="Exception",
            ),
        ]
        db = _FakeAsyncSession([
            catalog, scoped, [("fw",), ("other",)],
            [("leave", []), ("shared", []), ("included", [])],  # in-scope evidence_requests: none
        ])
        preview = await preview_framework_change(db, ORG_ID, ["fw"], "remove")
        assert preview["controls_leaving_scope"] == ["leave"]
        assert preview["orphaned_evidence"] == []
        assert preview["open_tasks_affected"] == 0
        assert preview["shared_with_active_frameworks"] == ["shared"]
        assert preview["individual_inclusions"] == ["included"]
        assert preview["explicitly_excluded"] == ["excluded"]
        assert preview["already_covered"] == ["already-out"]

    async def test_add_preview_reports_real_overlap_not_structural_zeros(self):
        """The add path used to sort every control into new/already_covered and `continue`,
        so shared_with_active_frameworks and individual_inclusions were always empty on an
        add no matter how much the incoming framework overlapped the existing scope."""
        catalog = [
            SimpleNamespace(scf_id="brand-new", framework_mappings={"fw": ["1"]}),
            SimpleNamespace(scf_id="shared", framework_mappings={"fw": ["2"], "other": ["2"]}),
            SimpleNamespace(scf_id="included", framework_mappings={"fw": ["3"]}),
            SimpleNamespace(scf_id="excluded", framework_mappings={"fw": ["4"]}),
            SimpleNamespace(scf_id="orphan", framework_mappings={"fw": ["5"]}),
        ]
        scoped = [
            ScopedControl(organization_id=ORG_ID, scf_id="shared", selected=True),
            ScopedControl(
                organization_id=ORG_ID, scf_id="included", selected=True,
                scope_override="include",
            ),
            ScopedControl(
                organization_id=ORG_ID, scf_id="excluded", selected=False,
                scope_override="exclude", scope_override_reason="Exception",
            ),
            ScopedControl(organization_id=ORG_ID, scf_id="orphan", selected=True),
        ]
        db = _FakeAsyncSession([catalog, scoped, [("other",)]])
        preview = await preview_framework_change(db, ORG_ID, ["fw"], "add")

        assert preview["new_controls"] == ["brand-new"]
        # "other" is active and also maps this control, so the overlap is real and reported.
        assert preview["shared_with_active_frameworks"] == ["shared"]
        assert preview["individual_inclusions"] == ["included"]
        assert preview["explicitly_excluded"] == ["excluded"]
        # In scope, but nothing active justifies it independently of the framework being added.
        assert preview["already_covered"] == ["orphan"]
        # An add can never take a control out of scope, so it never orphans evidence
        # and never issues the evidence-impact probe.
        assert preview["controls_leaving_scope"] == []
        assert preview["orphaned_evidence"] == []
        assert preview["open_tasks_affected"] == 0
        assert len(db.statements) == 3

    async def test_summary_marks_partial_and_filters_internal_mappings(self):
        catalog = [
            SimpleNamespace(
                scf_id="covered",
                framework_mappings={"iso_27001_2022": ["A.1"], "risk_catalog": ["R.1"]},
            ),
            SimpleNamespace(
                scf_id="excluded",
                framework_mappings={"iso_27001_2022": ["A.2"], "threat_catalog": ["T.1"]},
            ),
        ]
        scoped = [
            ScopedControl(organization_id=ORG_ID, scf_id="covered", selected=True),
            ScopedControl(
                organization_id=ORG_ID, scf_id="excluded", selected=False,
                scope_override="exclude", scope_override_reason="Exception",
            ),
        ]
        selection = _selection("iso_27001_2022", active=True)
        db = _FakeAsyncSession([catalog, scoped, [selection]])
        summary = await framework_scope_summary(db, ORG_ID)
        assert summary["selected_count"] == 1
        assert [row["id"] for row in summary["frameworks"]] == ["iso_27001_2022"]
        row = summary["frameworks"][0]
        assert row["partial"] is True
        assert row["mapped_control_count"] == 2
        assert row["in_scope_count"] == 1
        assert row["missing_count"] == 1
        assert row["expected_additions"] == 0
        assert row["status_counts"] == {
            "implemented": 0, "in_progress": 0, "at_risk": 0, "not_started": 1,
        }

    async def test_summary_buckets_in_scope_controls_by_implementation_status(self):
        statuses = {
            "a": "implemented", "b": "monitored", "c": "in_progress",
            "d": "at_risk", "e": "deferred", "f": None,
        }
        catalog = [
            SimpleNamespace(scf_id=scf_id, framework_mappings={"fw": ["1"]})
            for scf_id in [*statuses, "not-scoped"]
        ]
        scoped = [
            ScopedControl(
                organization_id=ORG_ID, scf_id=scf_id, selected=True,
                implementation_status=status,
            )
            for scf_id, status in statuses.items()
        ]
        db = _FakeAsyncSession([catalog, scoped, []])
        row = (await framework_scope_summary(db, ORG_ID))["frameworks"][0]
        # Only in-scope controls are counted; the out-of-scope one is the gap.
        assert row["status_counts"] == {
            "implemented": 2, "in_progress": 1, "at_risk": 1, "not_started": 2,
        }
        assert row["mapped_control_count"] - row["in_scope_count"] == 1


class _ResetSession:
    def __init__(self):
        self.statements = []
        self.commits = 0

    async def scalar(self, stmt):
        self.statements.append(stmt)
        return 3

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result([])

    async def commit(self):
        self.commits += 1


async def test_reset_clears_effective_scope_overrides_and_framework_selections():
    db = _ResetSession()
    membership = SimpleNamespace(user=SimpleNamespace(db_id=str(USER_ID)))

    result = await reset_all_scope(ORG_ID, membership=membership, db=db)

    assert result.removed == 3
    assert db.commits == 1
    statements = [str(stmt) for stmt in db.statements]
    scoped_update = next(stmt for stmt in statements if stmt.startswith("UPDATE scoped_controls"))
    framework_update = next(
        stmt for stmt in statements if stmt.startswith("UPDATE organization_framework_selections")
    )
    assert "scope_override" in scoped_update
    assert "scope_override_reason" in scoped_update
    assert "scoped_controls.organization_id" in scoped_update
    assert "active" in framework_update
    assert "organization_framework_selections.organization_id" in framework_update


# ---------------------------------------------------------------------------
# Scope change → evidence impact
# ---------------------------------------------------------------------------

def _sql(db, index):
    return str(db.statements[index][0])


class TestEffectiveEvidence:
    ROWS = [
        ("AST-01", ["E-AST-01", "E-AST-02"]),
        ("AST-02", ["E-AST-02", "E-IAC-01"]),
        ("GOV-01", None),                  # a catalog row with no evidence_requests
        ("GOV-02", "not-a-list"),          # defensive: bad JSON shape reads as nothing
    ]

    async def test_union_of_in_scope_evidence_requests(self):
        db = _FakeAsyncSession([list(self.ROWS)])
        assert await effective_evidence_ids(db, ORG_ID) == {"E-AST-01", "E-AST-02", "E-IAC-01"}
        sql = _sql(db, 0)
        assert "scoped_controls" in sql and "scf_catalog_controls" in sql
        assert "selected" in sql
        assert len(db.statements) == 1

    async def test_inverse_view_lists_requiring_controls_sorted(self):
        db = _FakeAsyncSession([list(self.ROWS)])
        assert await required_controls_by_evidence(db, ORG_ID) == {
            "E-AST-01": ["AST-01"],
            "E-AST-02": ["AST-01", "AST-02"],
            "E-IAC-01": ["AST-02"],
        }
        assert len(db.statements) == 1


class TestRescopeStamp:
    """Re-scoping keeps status/maturity but stamps scope_restored_at so the
    UI can flag the old assessment as stale (see models.assessment_is_stale)."""

    async def test_bulk_rescope_update_stamps_scope_restored_at(self):
        db = _FakeAsyncSession([
            [("ctl-flip",)],                 # catalog query
            [("ctl-flip", False)],           # existing, deselected
            [],                              # UPDATE
            [],                              # selections select
        ])
        await bulk_scope_frameworks(db, ORG_ID, ["iso_27001_2022"], user_id=USER_ID)
        update_stmt, _ = db.statements[2]
        params = update_stmt.compile().params
        assert params.get("selected") is True
        assert params.get("scope_restored_at") is not None

    async def test_include_override_from_out_of_scope_stamps(self):
        control = ScopedControl(organization_id=ORG_ID, scf_id="ctl-one", selected=False)
        catalog = SimpleNamespace(scf_id="ctl-one", framework_mappings={"fw": ["1"]})
        db = _FakeAsyncSession([[control], [catalog]])
        updated = await set_individual_scope_override(
            db, ORG_ID, "ctl-one", "include", None, USER_ID, commit=False
        )
        assert updated.selected is True
        assert updated.scope_restored_at is not None

    async def test_include_override_when_already_in_scope_does_not_stamp(self):
        control = ScopedControl(organization_id=ORG_ID, scf_id="ctl-one", selected=True)
        catalog = SimpleNamespace(scf_id="ctl-one", framework_mappings={"fw": ["1"]})
        db = _FakeAsyncSession([[control], [catalog]])
        updated = await set_individual_scope_override(
            db, ORG_ID, "ctl-one", "include", None, USER_ID, commit=False
        )
        assert updated.selected is True
        assert updated.scope_restored_at is None

    async def test_exclude_never_stamps(self):
        control = ScopedControl(organization_id=ORG_ID, scf_id="ctl-one", selected=True)
        catalog = SimpleNamespace(scf_id="ctl-one", framework_mappings={"fw": ["1"]})
        db = _FakeAsyncSession([[control], [catalog]])
        updated = await set_individual_scope_override(
            db, ORG_ID, "ctl-one", "exclude", "reason", USER_ID, commit=False
        )
        assert updated.selected is False
        assert updated.scope_restored_at is None


class TestOrphanImpact:
    async def test_nothing_leaving_asks_nothing(self):
        db = _FakeAsyncSession([])
        impact = await orphan_impact(db, ORG_ID, set())
        assert (impact.evidence_ids, impact.tracking_ids, impact.open_tasks) == ([], [], 0)
        assert db.statements == []

    async def test_only_evidence_losing_its_last_requirer_is_reported(self):
        """E-SHARED is also required by a control that stays; E-SOLO is not.
        E-UNTRACKED is orphaned but nobody is collecting it, so nothing to report."""
        solo_id, = [uuid4()]
        db = _FakeAsyncSession([
            [("leave", ["E-SOLO", "E-SHARED", "E-UNTRACKED"]), ("stay", ["E-SHARED"])],
            [(solo_id, "E-SOLO")],         # tracked rows among the newly-unrequired
            [4],                           # open tasks on them
        ])
        impact = await orphan_impact(db, ORG_ID, {"leave"})
        assert impact.evidence_ids == ["E-SOLO"]
        assert impact.tracking_ids == [solo_id]
        assert impact.open_tasks == 4
        # The tracked query asked only about the newly-unrequired ids.
        tracked_stmt, _ = db.statements[1]
        bound = {str(v) for vals in tracked_stmt.compile().params.values()
                 for v in (vals if isinstance(vals, (list, tuple)) else [vals])}
        assert "E-SOLO" in bound and "E-UNTRACKED" in bound
        assert "E-SHARED" not in bound
        assert "wont_do" in _sql(db, 2) or "status" in _sql(db, 2)

    async def test_no_tracked_rows_skips_the_task_count(self):
        db = _FakeAsyncSession([
            [("leave", ["E-SOLO"])],
            [],                            # nothing tracked
        ])
        impact = await orphan_impact(db, ORG_ID, {"leave"})
        assert impact.evidence_ids == [] and impact.open_tasks == 0
        assert len(db.statements) == 2


class TestUnscopeOrphanEvidence:
    def _script(self, *, untrack):
        solo_id = uuid4()
        orphan = [
            [("ctl-solo", ["E-SOLO"])],    # in-scope evidence_requests
            [(solo_id, "E-SOLO")],         # tracked
            [3],                           # open tasks
        ]
        return solo_id, _unscope_script(
            catalog_rows=[("ctl-solo", {"iso_27017_2015": ["5.2"]})],
            in_scope=["ctl-solo"],
            active_frameworks=["iso_27017_2015"],
            selections=[_selection("iso_27017_2015", active=True)],
            orphan_script=orphan,
            untrack_script=[[], []] if untrack else None,
        )

    async def test_keep_reports_orphans_and_touches_nothing(self):
        _, script = self._script(untrack=False)
        db = _FakeAsyncSession(script)

        result = await bulk_unscope_frameworks(db, ORG_ID, ["iso_27017_2015"])

        assert result.removed == 1
        assert result.orphaned_evidence == ["E-SOLO"]
        assert result.open_tasks_affected == 3
        assert result.orphan_evidence_action == "keep"
        assert (result.untracked_evidence, result.tasks_closed) == (0, 0)
        assert "no longer required by any in-scope control (3 open tasks)" in result.message
        updates = [str(stmt) for stmt, _ in db.statements if "UPDATE" in str(stmt)]
        assert len(updates) == 1 and "scoped_controls" in updates[0]
        assert db.commits == 1

    async def test_untrack_flips_tracking_and_closes_open_tasks(self):
        solo_id, script = self._script(untrack=True)
        db = _FakeAsyncSession(script)

        result = await bulk_unscope_frameworks(
            db, ORG_ID, ["iso_27017_2015"], orphan_evidence_action="untrack"
        )

        assert result.orphaned_evidence == ["E-SOLO"]
        assert result.untracked_evidence == 1
        assert result.tasks_closed == 3
        assert "Untracked 1 evidence items" in result.message
        updates = [(str(stmt), stmt) for stmt, _ in db.statements if "UPDATE" in str(stmt)]
        assert [u[0].split()[1] for u in updates] == [
            "scoped_controls", "evidence_tracking", "evidence_collection_tasks",
        ]
        tracking_update = updates[1][1].compile()
        assert tracking_update.params.get("is_tracked") is False
        task_update = updates[2][1].compile()
        assert task_update.params.get("status") == "wont_do"
        assert "iso_27017_2015" in task_update.params.get("completion_notes", "")
        assert "wont_do" in str(updates[2][1]) or "NOT IN" in str(updates[2][1]).upper()
        assert db.commits == 1

    async def test_untrack_with_no_orphans_issues_no_evidence_updates(self):
        db = _FakeAsyncSession(_unscope_script(
            catalog_rows=[("ctl-solo", {"iso_27017_2015": ["5.2"]})],
            in_scope=["ctl-solo"],
            active_frameworks=["iso_27017_2015"],
            selections=[_selection("iso_27017_2015", active=True)],
        ))
        result = await bulk_unscope_frameworks(
            db, ORG_ID, ["iso_27017_2015"], orphan_evidence_action="untrack"
        )
        assert result.orphaned_evidence == []
        assert (result.untracked_evidence, result.tasks_closed) == (0, 0)
        updates = [str(stmt) for stmt, _ in db.statements if "UPDATE" in str(stmt)]
        assert len(updates) == 1

    async def test_unknown_action_is_rejected_before_any_query(self):
        db = _FakeAsyncSession([])
        with pytest.raises(ValueError, match="orphan_evidence_action"):
            await bulk_unscope_frameworks(db, ORG_ID, ["fw"], orphan_evidence_action="delete")
        assert db.statements == []


class TestPreviewOrphanEvidence:
    async def test_remove_preview_lists_orphaned_evidence_and_open_tasks(self):
        catalog = [
            SimpleNamespace(scf_id="leave", framework_mappings={"fw": ["1"]}),
            SimpleNamespace(scf_id="stay", framework_mappings={"other": ["2"]}),
        ]
        scoped = [
            ScopedControl(organization_id=ORG_ID, scf_id="leave", selected=True),
            ScopedControl(organization_id=ORG_ID, scf_id="stay", selected=True),
        ]
        db = _FakeAsyncSession([
            catalog, scoped, [("fw",), ("other",)],
            [("leave", ["E-SOLO", "E-SHARED"]), ("stay", ["E-SHARED"])],
            [(uuid4(), "E-SOLO")],
            [2],
        ])
        preview = await preview_framework_change(db, ORG_ID, ["fw"], "remove")
        assert preview["controls_leaving_scope"] == ["leave"]
        assert preview["orphaned_evidence"] == ["E-SOLO"]
        assert preview["open_tasks_affected"] == 2
        assert db.commits == 0
@pytest.mark.parametrize(
    "framework_id, family",
    [
        ("usa_federal_sox_2002", "us_federal"),
        ("us_fedramp_r5_low", "us_federal"),
        ("usa_texas_cdpa_2025", "us_state"),
        ("cis_csc_8_1_ig1", "industry"),
        ("owasp_top_10_2025", "industry"),
        ("emea_uk_cyber_essentials_3_3", "emea"),
        ("iso_27001_2022", "international"),
        ("scf_dpmp_2026", "other"),
    ],
)
def test_framework_family_groups_2026_catalogue_ids(framework_id, family):
    # 2026 catalogue ids are ``usa_*``; the ``us_*`` prefixes alone sent 77 US
    # frameworks and CIS into "Other".
    from services.scoping_service import _framework_family

    assert _framework_family(framework_id) == family
