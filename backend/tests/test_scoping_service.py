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

import pytest  # noqa: F401

# CI runs pytest from the repo root where backend/pytest.ini's
# asyncio_mode=auto is not picked up; mark explicitly (repo convention).
pytestmark = pytest.mark.asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scoping_service import (  # noqa: E402
    bulk_scope_frameworks,
    bulk_unscope_frameworks,
)
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

def _unscope_script(catalog_rows, in_scope, reasons, selections,
                    with_update=True):
    """Scripted results in the service's execute order."""
    script = [
        catalog_rows,                      # catalog query (scf_id, framework_mappings)
        [(s,) for s in in_scope],          # in-scope scf_ids
        [(r,) for r in reasons],           # distinct selection_reasons
    ]
    if with_update:
        script.append([])                  # UPDATE selected=False
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
            reasons=["Bulk scoped from: iso_27001_2022, iso_27017_2015"],
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
            reasons=["Bulk scoped from: iso_27017_2015"],
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
            reasons=["Bulk scoped from: iso_27001_2022, iso_27017_2015"],
            selections=[active_sel],
            with_update=False,  # nothing removable → no UPDATE issued
        ))

        result = await bulk_unscope_frameworks(db, ORG_ID, ["iso_27017_2015"])

        assert result.removed == 0
        assert result.protected == 1
        assert "shared with other in-scope frameworks" in result.message
        assert active_sel.active is False
        assert db.commits == 1  # selection deactivation alone still commits
