"""Unit tests for ENABLE_COMPOSITE_KSI flag + precedence (M3 PR 3, #575).

Covers ISC-18..19 of the M3 design spec:

* ``ENABLE_COMPOSITE_KSI`` defaults ``false``.
* Precedence chain: composite > window > per-file.
* The two flags (``ENABLE_COMPOSITE_KSI`` and ``ENABLE_WINDOW_ASSESSMENT_KSI``)
  are independent; composite-on-window-off skips straight to per-file as the
  fallback inside the composite-aware SQL.
* Flag-off behaviour is byte-identical to today (no-op-on-merge guarantee).
* SQL response shape continues to expose evidence_quality /
  evidence_quality_band / evidence_quality_warning via the same row columns
  the existing window/per-file paths produce.

Tests are pure-function + scripted-SQL — no DB. Mirrors the lightweight style
of ``test_composite_service.py`` and ``test_control_composites_api.py``.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import capability_themes  # noqa: E402
from api.capability_themes import (  # noqa: E402
    _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL,
    _EVIDENCE_METRICS_SQL,
    _EVIDENCE_METRICS_WINDOW_AWARE_SQL,
    _composite_ksi_enabled,
    _fetch_evidence_metrics_per_theme,
    _window_ksi_enabled,
)


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Fakes — scripted async session that captures the SQL it received.
# ---------------------------------------------------------------------------


class _FakeResult:
    """sqlalchemy-esque result that returns whatever rows it was handed."""

    def __init__(self, rows: list[Any]):
        self._rows = rows

    def all(self) -> list[Any]:
        return list(self._rows)


class _CapturingSession:
    """Async session that records the SQL passed to ``execute``.

    Returns rows from ``rows_for_sql`` keyed on Python ``id(sql_text)`` so we
    can assert which exact ``text(...)`` constant was selected by the
    dispatcher. Defaults to an empty list otherwise.
    """

    def __init__(self, rows_for_sql: dict[int, list[Any]] | None = None):
        self.rows_for_sql = rows_for_sql or {}
        self.last_sql = None
        self.last_params = None

    async def execute(self, sql, params=None):  # noqa: D401 — sqlalchemy shape
        self.last_sql = sql
        self.last_params = params
        rows = self.rows_for_sql.get(id(sql), [])
        return _FakeResult(rows)


def _row(theme_code: str = "BCD", **overrides) -> SimpleNamespace:
    """Build a fake metrics row matching the SELECT shape of all 3 SQL variants."""
    base = dict(
        theme_code=theme_code,
        controls_with_evidence=1,
        total_evidence_files=1,
        sufficient_count=1,
        partial_count=0,
        insufficient_count=0,
        insufficient_sample_count=0,
        pending_count=0,
        unassessed_count=0,
        avg_relevance_score=80.0,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# Flag default — guarantees no behaviour change on merge.
# ---------------------------------------------------------------------------


class TestFlagDefault:
    def test_composite_flag_default_off(self, monkeypatch):
        monkeypatch.delenv("ENABLE_COMPOSITE_KSI", raising=False)
        assert _composite_ksi_enabled() is False

    def test_composite_flag_explicit_false(self, monkeypatch):
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "false")
        assert _composite_ksi_enabled() is False

    def test_composite_flag_true(self, monkeypatch):
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        assert _composite_ksi_enabled() is True

    def test_composite_flag_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "TRUE")
        assert _composite_ksi_enabled() is True

    def test_composite_flag_independent_of_window_flag(self, monkeypatch):
        # Composite on, window off — must still report composite=on.
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "false")
        assert _composite_ksi_enabled() is True
        assert _window_ksi_enabled() is False


# ---------------------------------------------------------------------------
# Precedence — which SQL the dispatcher selects under each flag combination.
# ---------------------------------------------------------------------------


class TestSqlSelectionPrecedence:
    """Tests the four precedence combinations called out in M3 spec §5.

    Asserts the exact ``text(...)`` SQL constant the dispatcher chose by
    object identity — guarantees we picked the right CTE structure, not just
    one that happens to return rows.
    """

    @pytest.mark.asyncio
    async def test_a_composite_present_flag_on_uses_composite_sql(self, monkeypatch):
        """Composite + flag on → composite-aware SQL chosen (case A)."""
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "false")
        session = _CapturingSession({
            id(_EVIDENCE_METRICS_COMPOSITE_AWARE_SQL): [_row()],
        })
        result = await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL
        assert "BCD" in result

    @pytest.mark.asyncio
    async def test_b_composite_missing_window_flag_on_uses_window_sql(self, monkeypatch):
        """No composite row + window flag on → window-aware SQL (case B).

        From the dispatcher's perspective, "no composite row" is indistinguishable
        from "composite flag off" — both fall to the window tier. We model the
        flag-off case here; the SQL itself contains the row-level fallback for
        controls without composites when the composite flag is on.
        """
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "false")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "true")
        session = _CapturingSession({
            id(_EVIDENCE_METRICS_WINDOW_AWARE_SQL): [_row()],
        })
        await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_WINDOW_AWARE_SQL

    @pytest.mark.asyncio
    async def test_c_both_flags_off_uses_legacy_per_file_sql(self, monkeypatch):
        """Both flags off → legacy per-file SQL (case C, default behaviour)."""
        monkeypatch.delenv("ENABLE_COMPOSITE_KSI", raising=False)
        monkeypatch.delenv("ENABLE_WINDOW_ASSESSMENT_KSI", raising=False)
        session = _CapturingSession({
            id(_EVIDENCE_METRICS_SQL): [_row()],
        })
        await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_SQL

    @pytest.mark.asyncio
    async def test_d_composite_flag_on_no_composite_row_falls_through(self, monkeypatch):
        """Composite flag on but no composite row exists for any control.

        Inside the composite-aware SQL, the fallback CTE handles "no composite
        for this scf_id" by joining through to per-file/per-window. The
        dispatcher still selects composite-aware SQL — fallthrough lives
        inside the SQL, not in Python.
        """
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "false")
        # Empty rows simulate "no composite_status rows match in SQL" — the
        # caller still gets back an empty per-theme dict.
        session = _CapturingSession({})
        result = await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL
        assert result == {}

    @pytest.mark.asyncio
    async def test_composite_on_window_on_uses_composite_with_window_fallback(self, monkeypatch):
        """Both flags on → composite-aware-with-window-fallback SQL.

        Confirms the composite tier sits ABOVE the window tier, and that the
        composite-aware SQL embeds a window fallback when window flag is on.
        """
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "true")
        session = _CapturingSession({
            id(_EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL): [_row()],
        })
        await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL


# ---------------------------------------------------------------------------
# No-op-on-merge guarantee.
# ---------------------------------------------------------------------------


class TestNoOpOnMerge:
    """Asserts ENABLE_COMPOSITE_KSI=false produces identical SQL to today.

    "Today" = whatever the legacy + window-aware paths used pre-PR 3. We
    re-run the dispatcher with the composite flag off (and either window
    flag value) and assert the SQL constant selected matches the value
    that the pre-PR 3 dispatcher would have returned.
    """

    @pytest.mark.asyncio
    async def test_composite_off_window_off_matches_legacy(self, monkeypatch):
        monkeypatch.delenv("ENABLE_COMPOSITE_KSI", raising=False)
        monkeypatch.delenv("ENABLE_WINDOW_ASSESSMENT_KSI", raising=False)
        session = _CapturingSession()
        await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_SQL

    @pytest.mark.asyncio
    async def test_composite_off_window_on_matches_pre_pr3_window_path(self, monkeypatch):
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "false")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "true")
        session = _CapturingSession()
        await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_sql is _EVIDENCE_METRICS_WINDOW_AWARE_SQL

    @pytest.mark.asyncio
    async def test_org_id_passed_to_sql(self, monkeypatch):
        """Both pre-PR 3 paths bind ``org_id`` as a string. Composite SQL must too."""
        monkeypatch.setenv("ENABLE_COMPOSITE_KSI", "true")
        monkeypatch.setenv("ENABLE_WINDOW_ASSESSMENT_KSI", "false")
        session = _CapturingSession()
        await _fetch_evidence_metrics_per_theme(session, ORG_ID)
        assert session.last_params == {"org_id": str(ORG_ID)}


# ---------------------------------------------------------------------------
# Response shape — confirms axis bundle still produces evidence_quality fields.
# ---------------------------------------------------------------------------


class TestResponseShape:
    """When composite is the source, ``_compute_axis_bundle`` produces the
    same evidence_quality / evidence_quality_band / evidence_quality_warning
    fields as the window/per-file paths."""

    def test_composite_row_produces_eq_fields(self):
        from schemas import CapabilityThemePosture

        # A composite-sourced row mirroring the SQL output: 1 sufficient, score 90.
        row = _row(
            controls_with_evidence=1,
            total_evidence_files=1,
            sufficient_count=1,
            partial_count=0,
            insufficient_count=0,
            insufficient_sample_count=0,
            unassessed_count=0,
            avg_relevance_score=90.0,
        )
        posture = CapabilityThemePosture(
            implemented=1, monitored=0, ready_for_review=0, in_progress=0,
            not_started=0, at_risk=0, not_applicable=0, deferred=0,
        )
        bundle = capability_themes._compute_axis_bundle(
            posture=posture, scoped=1, maturity_score=None, evidence_row=row,
        )
        # The contract: these three fields are present and non-None for a
        # populated composite row.
        assert "evidence_quality" in bundle
        assert "evidence_quality_band" in bundle
        assert "evidence_quality_warning" in bundle
        assert bundle["evidence_quality"] is not None
        assert bundle["evidence_quality_band"] is not None

    def test_composite_insufficient_sample_routes_through_eq_formula(self):
        """insufficient_sample propagates from composite into EQ as half-credit
        (matches M1a behaviour for window-level insufficient_sample)."""
        from schemas import CapabilityThemePosture

        row = _row(
            controls_with_evidence=1,
            total_evidence_files=1,
            sufficient_count=0,
            partial_count=0,
            insufficient_count=0,
            insufficient_sample_count=1,
            unassessed_count=0,
            avg_relevance_score=100.0,
        )
        posture = CapabilityThemePosture(
            implemented=1, monitored=0, ready_for_review=0, in_progress=0,
            not_started=0, at_risk=0, not_applicable=0, deferred=0,
        )
        bundle = capability_themes._compute_axis_bundle(
            posture=posture, scoped=1, maturity_score=None, evidence_row=row,
        )
        # 1 insufficient_sample / 1 total = 0.5 quality × 1.0 relevance = 0.5
        assert bundle["evidence_quality"] == pytest.approx(0.5)
