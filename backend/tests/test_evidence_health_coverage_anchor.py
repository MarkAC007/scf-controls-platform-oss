"""Evidence health measured from coverage, not from arrival (#789 audit lane).

The dashboard and the upcoming-deadlines endpoint both anchored on
``EvidenceFile.uploaded_at``. That is when a file turned up, which is not what
anybody wants to know: a quarterly access review is exported and uploaded a few
days *after* the quarter it describes, so the traffic light ran a reporting gap
behind reality, every cycle, always in the flattering direction. Worse, uploading
last year's report during an audit turned a red item green.

Now that a preparer can assert what a file covers (#786), both endpoints anchor
on ``COALESCE(effective_period_end, uploaded_at::date)`` — the newest date the
evidence *claims*, falling back to the upload proxy only where nothing was
claimed. These tests pin that, and pin the disclosure that says which was used.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from fastapi import Response  # noqa: E402

from tests.test_upcoming_evidence_no_sentinel import (  # noqa: E402
    FakeSession,
    _request,
    _utc_today,
    file_row,
    tracking,
)


async def call_dashboard(db):
    from api.evidence_health import get_evidence_health

    return await get_evidence_health(
        request=_request(),
        response=Response(),
        org_id=uuid4(),
        membership=MagicMock(),
        db=db,
    )


async def call_upcoming(db, days=14):
    from api.evidence_health import get_upcoming_evidence

    return await get_upcoming_evidence(
        request=_request(),
        response=Response(),
        org_id=uuid4(),
        days=days,
        membership=MagicMock(),
        db=db,
    )


def dashboard_session(tracked, files):
    """Rowsets in the order `get_evidence_health` consumes them.

    tracked → health config (none) → files → AI assessments (none) → catalog.
    """
    return FakeSession(tracked, [], files, [], [])


def item(result, evidence_id):
    return next(i for i in result.items if i.evidence_id == evidence_id)


class TestTheDashboardMeasuresCoverage:

    @pytest.mark.asyncio
    async def test_an_old_document_uploaded_today_does_not_turn_the_light_green(self):
        """The headline defect. Uploading last year's report is not collecting.

        Monthly cadence, threshold 35 days. The file arrived an hour ago but
        asserts it covers through the end of 2024 — well over 1.5x the
        threshold, so red.
        """
        db = dashboard_session(
            [tracking("E-AST-01", frequency="monthly")],
            [file_row("E-AST-01", datetime.utcnow(), coverage_through=date(2024, 12, 31))],
        )
        health = await call_dashboard(db)
        row = item(health, "E-AST-01")
        assert row.status == "red"
        assert row.staleness_basis == "asserted_period"
        assert row.coverage_through == date(2024, 12, 31)

    @pytest.mark.asyncio
    async def test_a_late_upload_of_current_evidence_is_judged_on_what_it_covers(self):
        """The mirror image, and the one that penalises honest preparers.

        Quarterly evidence covering the period that ended a week ago, uploaded
        today. It is green either way here — what matters is that the days are
        counted from the period end, not from the upload.
        """
        covered_through = _utc_today() - timedelta(days=7)
        db = dashboard_session(
            [tracking("E-AST-01", frequency="quarterly")],
            [file_row("E-AST-01", datetime.utcnow(), coverage_through=covered_through)],
        )
        row = item(await call_dashboard(db), "E-AST-01")
        assert row.days_since_coverage == 7
        assert row.status == "green"

    @pytest.mark.asyncio
    async def test_nothing_asserted_still_uses_the_upload_proxy_and_says_so(self):
        """No behaviour change for evidence nobody has asserted anything about."""
        uploaded = datetime.utcnow() - timedelta(days=3)
        db = dashboard_session(
            [tracking("E-AST-01", frequency="monthly")],
            # coverage_through defaults to the upload date, mirroring the SQL COALESCE
            [file_row("E-AST-01", uploaded)],
        )
        row = item(await call_dashboard(db), "E-AST-01")
        assert row.staleness_basis == "upload_date"
        assert row.days_since_coverage == 3
        assert row.status == "green"

    @pytest.mark.asyncio
    async def test_the_upload_date_is_still_reported_alongside_the_coverage_date(self):
        """A reader who disagrees with the ruling needs both numbers."""
        uploaded = datetime.utcnow()
        db = dashboard_session(
            [tracking("E-AST-01", frequency="monthly")],
            [file_row("E-AST-01", uploaded, coverage_through=date(2024, 12, 31))],
        )
        row = item(await call_dashboard(db), "E-AST-01")
        assert row.last_file_uploaded_at == uploaded
        assert row.days_since_upload == 0
        assert row.days_since_coverage > 0

    @pytest.mark.asyncio
    async def test_an_item_with_no_files_at_all_is_unknown_not_red(self):
        db = dashboard_session([tracking("E-AST-01", frequency="monthly")], [])
        row = item(await call_dashboard(db), "E-AST-01")
        assert row.status == "unknown"
        assert row.coverage_through is None
        assert row.days_since_coverage is None


class TestUpcomingDeadlinesCountFromCoverage:

    @pytest.mark.asyncio
    async def test_the_next_due_date_runs_from_the_period_end_not_the_upload(self):
        """Monthly cadence, threshold 35 days.

        The period ended 30 days ago; the file was uploaded today. Due in 5
        days. Anchored on the upload it would have said 35, and the deadline
        would drift a little later every single cycle.
        """
        db = FakeSession(
            [tracking("E-AST-01", frequency="monthly")],
            [file_row(
                "E-AST-01",
                datetime.utcnow(),
                coverage_through=_utc_today() - timedelta(days=30),
            )],
            [],
        )
        result = await call_upcoming(db, days=14)
        row = next(i for i in result["items"] if i["evidence_id"] == "E-AST-01")
        assert row["days_until_due"] == 5

    @pytest.mark.asyncio
    async def test_evidence_covering_an_old_period_is_already_overdue(self):
        db = FakeSession(
            [tracking("E-AST-01", frequency="monthly")],
            [file_row("E-AST-01", datetime.utcnow(), coverage_through=date(2024, 1, 31))],
            [],
        )
        result = await call_upcoming(db, days=14)
        row = next(i for i in result["items"] if i["evidence_id"] == "E-AST-01")
        assert row["days_until_due"] < 0

    @pytest.mark.asyncio
    async def test_never_collected_still_has_no_due_date(self):
        """The #788 sentinel fix must survive the re-anchoring."""
        db = FakeSession([tracking("E-AST-01")], [], [])
        result = await call_upcoming(db, days=14)
        row = next(i for i in result["items"] if i["evidence_id"] == "E-AST-01")
        assert row["days_until_due"] is None


class TestTheQueryActuallyAsksForCoverage:
    """What the SQL asks the database for.

    Every other test in this file feeds the endpoint hand-built rows through a
    fake session, which is fast and readable and completely blind to the query
    itself: a mutation replacing the COALESCE with a bare `uploaded_at` left all
    of them green. There is no Postgres in CI to run the real thing against, so
    what is asserted here is the compiled expression — narrower than a
    behavioural test and honest about being so, but it does pin the one thing
    the fakes cannot see, which is that the query asks for the asserted period
    first and the upload date only as a fallback.
    """

    def test_coverage_prefers_the_asserted_period_over_the_upload_date(self):
        from api.evidence_health import latest_coverage_expr

        sql = str(latest_coverage_expr().compile(compile_kwargs={"literal_binds": True}))

        assert "coalesce" in sql.lower()
        assert "effective_period_end" in sql
        assert "uploaded_at" in sql
        # Order matters: COALESCE returns its first non-null argument, so an
        # asserted period end has to come first or it never wins.
        assert sql.lower().index("effective_period_end") < sql.lower().index("uploaded_at")

    def test_coverage_is_aggregated_to_the_newest_one(self):
        from api.evidence_health import latest_coverage_expr

        sql = str(latest_coverage_expr().compile()).lower()

        # A record with five files is as fresh as its newest coverage, not its
        # oldest and not an arbitrary one.
        assert sql.startswith("max(")

    def test_the_basis_flag_asks_whether_anything_was_asserted(self):
        from api.evidence_health import any_asserted_expr

        sql = str(any_asserted_expr().compile()).lower()

        assert "effective_period_end" in sql
        assert "not null" in sql

    def test_both_endpoints_anchor_on_the_same_expression(self):
        """The divergence this PR closed (#57): two screens, two anchors."""
        import inspect
        from api import evidence_health

        source = inspect.getsource(evidence_health)

        # One definition, two call sites — not two hand-written COALESCEs that
        # can drift apart again.
        assert source.count("latest_coverage_expr()") == 3  # def + 2 uses
        assert source.count("sa_func.coalesce(") == 1
