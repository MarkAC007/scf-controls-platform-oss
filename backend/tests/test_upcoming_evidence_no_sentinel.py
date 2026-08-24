"""
Unit tests for ``GET /organizations/{org_id}/evidence-health/upcoming`` (#788).

The endpoint used to describe never-collected evidence with a magic number:
``days_until_due = -999``. The dashboard had no idea it was a sentinel and
rendered it as "Overdue (999d)" — a fabricated fact shown to the user with the
same confidence as a real one.

These tests pin the replacement: ``None`` for "there is no such number", the
item still surfaced, still flagged overdue, and sorted ahead of everything with
a real deadline.

Mock-based — no database (mirrors tests/test_frequency_health_api.py).
"""
import pytest
from unittest.mock import MagicMock
from uuid import uuid4
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from fastapi import Response  # noqa: E402
from starlette.requests import Request  # noqa: E402


def _utc_today() -> date:
    """The day boundary the code under test uses.

    ``api/evidence_health.py`` works from ``datetime.utcnow()``. Building the
    expectations here from ``date.today()`` -- the *local* date -- made these
    tests disagree with it by one day for the hour each day when the server's
    timezone and UTC are on different dates. CI runs in UTC and never saw it;
    anyone west of Greenwich in the evening, or east of it in the morning, did.
    """
    return datetime.utcnow().date()



def _request() -> Request:
    """A real Request: the endpoint's rate limiter rejects a mock."""
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/organizations/x/evidence-health/upcoming",
        "headers": [],
        "query_string": b"",
        "client": ("127.0.0.1", 1234),
    })


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    """Replays one scripted rowset through the accessors the endpoint uses."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalars(self):
        rows = self._rows

        class _Scalars:
            def all(self_inner):
                return list(rows)

        return _Scalars()


class FakeSession:
    """Async session stub replaying results in call order."""

    def __init__(self, *rowsets):
        self._rowsets = list(rowsets)
        self.calls = 0

    async def execute(self, statement, params=None):
        self.calls += 1
        rows = self._rowsets.pop(0) if self._rowsets else []
        return _Result(rows)


def tracking(evidence_id, frequency="monthly"):
    t = MagicMock()
    t.evidence_id = evidence_id
    t.frequency = frequency
    t.collecting_system = "Manual"
    t.evidence_name = f"Name for {evidence_id}"
    return t


def file_row(evidence_id, uploaded_at, count=3, coverage_through=None):
    """One row of the latest-files subquery.

    ``latest_coverage`` mirrors the SQL ``COALESCE(effective_period_end,
    uploaded_at::date)``: with nothing asserted it IS the upload date, which is
    what these tests assume unless they say otherwise.
    """
    any_asserted = 1 if coverage_through is not None else 0
    if coverage_through is None and uploaded_at is not None:
        coverage_through = uploaded_at.date()
    return SimpleNamespace(
        evidence_id=evidence_id,
        latest_upload=uploaded_at,
        latest_coverage=coverage_through,
        any_asserted=any_asserted,
        file_count=count,
    )


async def call_upcoming(db, days=14):
    from api.evidence_health import get_upcoming_evidence

    membership = MagicMock()
    return await get_upcoming_evidence(
        request=_request(),
        response=Response(),
        org_id=uuid4(),
        days=days,
        membership=membership,
        db=db,
    )


# ---------------------------------------------------------------------------
# The sentinel is gone
# ---------------------------------------------------------------------------

class TestNoSentinelReachesTheClient:

    @pytest.mark.asyncio
    async def test_never_collected_has_no_days_until_due(self):
        """No upload → the number does not exist, so it is None."""
        db = FakeSession([tracking("E-AST-01")], [], [])

        result = await call_upcoming(db)

        assert len(result["items"]) == 1
        item = result["items"][0]
        assert item["days_until_due"] is None
        assert item["next_due"] is None
        assert item["last_uploaded_at"] is None

    @pytest.mark.asyncio
    async def test_never_collected_is_still_overdue(self):
        """Absent is not "fine": nothing ever collected is still a gap."""
        db = FakeSession([tracking("E-AST-01")], [], [])

        item = (await call_upcoming(db))["items"][0]

        assert item["is_overdue"] is True

    @pytest.mark.asyncio
    async def test_never_collected_is_included_regardless_of_window(self):
        """A 1-day window still surfaces it — there is no date to fall outside."""
        db = FakeSession([tracking("E-AST-01")], [], [])

        result = await call_upcoming(db, days=1)

        assert [i["evidence_id"] for i in result["items"]] == ["E-AST-01"]

    @pytest.mark.asyncio
    async def test_module_source_contains_no_999_sentinel(self):
        """The literal itself is gone, not merely unreachable."""
        import inspect
        import api.evidence_health as mod

        source = inspect.getsource(mod)
        # Strip comments: the fix is explained in prose that names the old value.
        code = "\n".join(
            line.split("#")[0] for line in source.splitlines()
        )
        assert "-999" not in code


# ---------------------------------------------------------------------------
# Dated items are unaffected
# ---------------------------------------------------------------------------

class TestDatedItemsKeepTheirNumbers:

    @pytest.mark.asyncio
    async def test_overdue_item_reports_negative_days(self):
        now = datetime.utcnow()
        db = FakeSession(
            [tracking("E-AST-02", frequency="monthly")],
            [file_row("E-AST-02", now - timedelta(days=45))],
            [],
        )

        item = (await call_upcoming(db))["items"][0]

        assert item["days_until_due"] is not None
        assert item["days_until_due"] < 0
        assert item["is_overdue"] is True

    @pytest.mark.asyncio
    async def test_due_soon_item_is_not_overdue(self):
        now = datetime.utcnow()
        db = FakeSession(
            [tracking("E-AST-03", frequency="monthly")],
            [file_row("E-AST-03", now - timedelta(days=25))],
            [],
        )

        item = (await call_upcoming(db))["items"][0]

        assert item["days_until_due"] >= 0
        assert item["is_overdue"] is False

    @pytest.mark.asyncio
    async def test_item_outside_the_window_is_excluded(self):
        """A fresh monthly upload is ~30 days out — beyond a 7-day window."""
        now = datetime.utcnow()
        db = FakeSession(
            [tracking("E-AST-04", frequency="monthly")],
            [file_row("E-AST-04", now)],
            [],
        )

        result = await call_upcoming(db, days=7)

        assert result["items"] == []
        assert result["total"] == 0


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------

class TestSortOrder:

    @pytest.mark.asyncio
    async def test_never_collected_sorts_before_dated_items(self):
        """Not by accident of being a small number — by an explicit key."""
        now = datetime.utcnow()
        db = FakeSession(
            [
                tracking("E-DATED-OVERDUE", frequency="monthly"),
                tracking("E-NEVER", frequency="monthly"),
                tracking("E-DATED-SOON", frequency="monthly"),
            ],
            [
                file_row("E-DATED-OVERDUE", now - timedelta(days=40)),
                file_row("E-DATED-SOON", now - timedelta(days=28)),
            ],
            [],
        )

        ids = [i["evidence_id"] for i in (await call_upcoming(db))["items"]]

        assert ids == ["E-NEVER", "E-DATED-OVERDUE", "E-DATED-SOON"]

    @pytest.mark.asyncio
    async def test_dated_items_remain_ascending(self):
        now = datetime.utcnow()
        db = FakeSession(
            [
                tracking("E-LATER", frequency="monthly"),
                tracking("E-EARLIER", frequency="monthly"),
            ],
            [
                # Monthly threshold is 35 days, and the window here is 14, so
                # both of these have to be due within a fortnight to be in the
                # result at all. 20 days ago put E-LATER 15 days out — it only
                # ever appeared because the day count truncated toward zero
                # after midnight (#789 audit lane). 25 puts it genuinely inside.
                file_row("E-LATER", now - timedelta(days=25)),
                file_row("E-EARLIER", now - timedelta(days=35)),
            ],
            [],
        )

        items = (await call_upcoming(db))["items"]
        days = [i["days_until_due"] for i in items]

        assert days == sorted(days)
        assert [i["evidence_id"] for i in items] == ["E-EARLIER", "E-LATER"]

    @pytest.mark.asyncio
    async def test_multiple_never_collected_do_not_crash_the_sort(self):
        """`None` in the sort key is the whole risk of this change."""
        db = FakeSession(
            [tracking("E-N1"), tracking("E-N2"), tracking("E-N3")], [], []
        )

        items = (await call_upcoming(db))["items"]

        assert len(items) == 3
        assert all(i["days_until_due"] is None for i in items)


class TestDayCountsAreCountedInDateSpace:
    """``days_until_due`` is a number of days, not a truncated duration.

    The endpoint computes a due *date* and subtracts today's *date*. It used to
    subtract a mid-afternoon ``datetime.utcnow()`` from midnight on the due day,
    and ``timedelta.days`` truncates toward zero: at 14:00 the answer came back
    one day short, every day, for every item. The visible consequence was
    evidence due today reporting as already overdue from one minute past
    midnight onwards.

    These assert exact numbers rather than an ordering, because an ordering
    survives a uniform off-by-one and this bug was a uniform off-by-one.
    """

    @pytest.mark.asyncio
    async def test_evidence_due_today_is_not_yet_overdue(self):
        # Monthly cadence is a 35-day threshold, so coverage ending 35 days ago
        # is due exactly today.
        db = FakeSession(
            [tracking("E-DUE-TODAY", frequency="monthly")],
            [file_row(
                "E-DUE-TODAY",
                datetime.utcnow() - timedelta(days=35),
                coverage_through=_utc_today() - timedelta(days=35),
            )],
            [],
        )

        item = (await call_upcoming(db))["items"][0]

        assert item["days_until_due"] == 0
        assert item["is_overdue"] is False

    @pytest.mark.asyncio
    async def test_a_deadline_ten_days_out_reports_ten_days(self):
        db = FakeSession(
            [tracking("E-TEN", frequency="monthly")],
            [file_row(
                "E-TEN",
                datetime.utcnow() - timedelta(days=25),
                coverage_through=_utc_today() - timedelta(days=25),
            )],
            [],
        )

        item = (await call_upcoming(db))["items"][0]

        assert item["days_until_due"] == 10

    @pytest.mark.asyncio
    async def test_a_deadline_a_day_past_reports_minus_one(self):
        db = FakeSession(
            [tracking("E-PAST", frequency="monthly")],
            [file_row(
                "E-PAST",
                datetime.utcnow() - timedelta(days=36),
                coverage_through=_utc_today() - timedelta(days=36),
            )],
            [],
        )

        item = (await call_upcoming(db))["items"][0]

        assert item["days_until_due"] == -1
        assert item["is_overdue"] is True
