"""Tests for the reworked ``_materialise_scope`` helper.

Increment 1 of the framework-native audit-engagement design
(docs/superpowers/specs/2026-08-19-audit-engagements-design.md).

The engagement scope snapshot changed from "only selected controls" to the
**complete framework-mapped set, tagged** in_scope / excluded / not_tracked,
each carrying the frameworks that pulled it in and (for exclusions) the org's
frozen out-of-scope justification.

These are pure-logic unit tests over the helper, using a scripted fake async
session in the style of the other backend tests (no real Postgres): the first
``execute`` returns the catalog query (scf_id, framework_mappings); the second
returns the org's scoped controls (id, scf_id, selected, out_of_scope_justification).
The rows the helper would persist are captured via ``add_all``.
"""
from __future__ import annotations

import os
import sys
from uuid import UUID, uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.audit_engagements import _materialise_scope  # noqa: E402
from models import ScopeStatus  # noqa: E402


class _Result:
    """Minimal stand-in for a SQLAlchemy Result exposing fetchall()."""

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeAsyncSession:
    """Scripted async session: each execute() pops the next result in order."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.added = []

    async def execute(self, _stmt, _params=None):
        if not self._scripted:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(self._scripted.pop(0))

    def add_all(self, rows):
        self.added.extend(rows)


ENG_ID = uuid4()
ORG_ID = uuid4()
SC_SELECTED = uuid4()
SC_EXCLUDED = uuid4()


def _run(session, frameworks):
    import asyncio

    return asyncio.get_event_loop().run_until_complete(
        _materialise_scope(
            db=session, engagement_id=ENG_ID, org_id=ORG_ID, frameworks=frameworks
        )
    )


@pytest.mark.asyncio
async def test_materialise_tags_in_scope_excluded_and_not_tracked():
    """Every framework-mapped control is captured with the right scope_status."""
    catalog_rows = [
        ("GOV-01", {"iso_27001_2022": ["5.1"], "soc2": ["CC1.1"]}),
        ("GOV-02", {"iso_27001_2022": ["5.2"]}),
        ("GOV-03", {"iso_27001_2022": ["5.3"]}),  # org never tracked this one
    ]
    scoped_rows = [
        (SC_SELECTED, "GOV-01", True, None),
        (SC_EXCLUDED, "GOV-02", False, "Handled by the parent group's certified ISMS."),
    ]
    session = _FakeAsyncSession([catalog_rows, scoped_rows])

    count = await _materialise_scope(
        db=session, engagement_id=ENG_ID, org_id=ORG_ID, frameworks=["iso_27001_2022"]
    )

    assert count == 3
    by_scf = {r.scf_id: r for r in session.added}

    assert by_scf["GOV-01"].scope_status == ScopeStatus.IN_SCOPE.value
    assert by_scf["GOV-01"].scoped_control_id == SC_SELECTED
    assert by_scf["GOV-01"].out_of_scope_justification is None

    assert by_scf["GOV-02"].scope_status == ScopeStatus.EXCLUDED.value
    assert by_scf["GOV-02"].scoped_control_id == SC_EXCLUDED
    assert by_scf["GOV-02"].out_of_scope_justification == (
        "Handled by the parent group's certified ISMS."
    )

    assert by_scf["GOV-03"].scope_status == ScopeStatus.NOT_TRACKED.value
    assert by_scf["GOV-03"].scoped_control_id is None
    assert by_scf["GOV-03"].out_of_scope_justification is None

    # All rows are pinned to the engagement.
    assert all(r.engagement_id == ENG_ID for r in session.added)


@pytest.mark.asyncio
async def test_source_frameworks_records_only_matching_engagement_frameworks():
    """source_frameworks is the intersection of the engagement frameworks and the
    control's own mappings — not every framework the control happens to map to."""
    catalog_rows = [
        # GOV-01 maps to three frameworks, but the engagement only covers two.
        ("GOV-01", {"iso_27001_2022": ["5.1"], "soc2": ["CC1.1"], "pci_dss_4_0_1": ["12.1"]}),
    ]
    scoped_rows = [(SC_SELECTED, "GOV-01", True, None)]
    session = _FakeAsyncSession([catalog_rows, scoped_rows])

    await _materialise_scope(
        db=session,
        engagement_id=ENG_ID,
        org_id=ORG_ID,
        frameworks=["iso_27001_2022", "soc2"],
    )

    row = session.added[0]
    assert sorted(row.source_frameworks) == ["iso_27001_2022", "soc2"]


@pytest.mark.asyncio
async def test_no_frameworks_returns_zero_without_querying():
    session = _FakeAsyncSession([])  # no scripted results — execute must not be called
    count = await _materialise_scope(
        db=session, engagement_id=ENG_ID, org_id=ORG_ID, frameworks=[]
    )
    assert count == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_no_catalog_matches_returns_zero():
    session = _FakeAsyncSession([[]])  # catalog query returns nothing
    count = await _materialise_scope(
        db=session, engagement_id=ENG_ID, org_id=ORG_ID, frameworks=["iso_27001_2022"]
    )
    assert count == 0
    assert session.added == []
