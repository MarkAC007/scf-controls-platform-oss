"""Every remaining place a freshness date was anchored on arrival (#57, #789).

The coverage-vs-arrival distinction was fixed at the two places it was most
visible — the health dashboard and the maturity engine — and then swept for
siblings. Three more sites were computing or stamping a freshness date, each
with the same substitution or a missing monotonic guard:

  * ``services/validation_service._rule_freshness`` aged a file from its upload
    date, so a Q1 report uploaded in July validated as fresh.
  * ``api/evidence_inbox`` stamped ``last_collection_date`` unconditionally, so
    a replayed or back-dated webhook delivery walked a live programme's
    collection date backwards.
  * ``api/evidence_tasks`` did the same on task completion.

The three sites that were *not* changed are tombstoned where they live:
``maturity.py``'s L5 seven-day literal, and ``EvidenceHealthConfig``'s per-item
override columns.
"""
import pytest
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from services.validation_service import _rule_freshness  # noqa: E402
from services.collection_date import advance_last_collection_date  # noqa: E402


def _utc_today() -> date:
    """The day boundary the code under test uses.

    ``api/evidence_health.py`` works from ``datetime.utcnow()``. Building the
    expectations here from ``date.today()`` -- the *local* date -- made these
    tests disagree with it by one day for the hour each day when the server's
    timezone and UTC are on different dates. CI runs in UTC and never saw it;
    anyone west of Greenwich in the evening, or east of it in the morning, did.
    """
    return datetime.utcnow().date()



def _db_returning(tracking):
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=tracking)
    db.execute = AsyncMock(return_value=result)
    return db


def _file(uploaded_days_ago=0, effective_period_end=None):
    return SimpleNamespace(
        organization_id="org-1",
        evidence_id="E-AST-01",
        uploaded_at=datetime.utcnow() - timedelta(days=uploaded_days_ago),
        effective_period_end=effective_period_end,
    )


def _tracking(frequency="monthly"):
    return SimpleNamespace(frequency=frequency)


# ---------------------------------------------------------------------------
# validation_service._rule_freshness
# ---------------------------------------------------------------------------

class TestFreshnessValidationAgesFromCoverage:

    @pytest.mark.asyncio
    async def test_an_old_period_uploaded_today_is_stale(self):
        """The whole point. Arrival said fresh; the period says otherwise."""
        result = await _rule_freshness(
            _db_returning(_tracking("monthly")),
            _file(uploaded_days_ago=0, effective_period_end=_utc_today() - timedelta(days=120)),
        )

        assert result["level"] == "warning"
        assert "120d old" in result["message"]

    @pytest.mark.asyncio
    async def test_a_current_period_uploaded_long_ago_is_fresh(self):
        """And the converse, which the upload anchor got wrong the other way."""
        result = await _rule_freshness(
            _db_returning(_tracking("monthly")),
            _file(uploaded_days_ago=200, effective_period_end=_utc_today() - timedelta(days=3)),
        )

        assert result["level"] == "valid"
        assert "3d old" in result["message"]

    @pytest.mark.asyncio
    async def test_nothing_asserted_still_falls_back_to_the_upload_date(self):
        result = await _rule_freshness(
            _db_returning(_tracking("monthly")),
            _file(uploaded_days_ago=120, effective_period_end=None),
        )

        assert result["level"] == "warning"
        assert "120d old" in result["message"]

    @pytest.mark.asyncio
    async def test_the_message_says_which_date_it_used(self):
        asserted = await _rule_freshness(
            _db_returning(_tracking("monthly")),
            _file(effective_period_end=_utc_today() - timedelta(days=3)),
        )
        proxy = await _rule_freshness(
            _db_returning(_tracking("monthly")),
            _file(uploaded_days_ago=3),
        )

        # Same number of days, two very different grounds for relying on it.
        assert "covering period ended" in asserted["message"]
        assert "uploaded" in proxy["message"]

    @pytest.mark.asyncio
    async def test_age_is_counted_in_date_space(self):
        """`datetime - datetime` truncates toward zero and read a day short."""
        file = _file()
        # Yesterday at 23:59 — an afternoon `utcnow()` minus this is under 24h.
        file.uploaded_at = datetime.combine(
            _utc_today() - timedelta(days=1), datetime.min.time()
        ) + timedelta(hours=23, minutes=59)

        result = await _rule_freshness(_db_returning(_tracking("monthly")), file)

        assert "1d old" in result["message"]

    @pytest.mark.asyncio
    async def test_an_unrecognised_cadence_still_skips_rather_than_guessing(self):
        result = await _rule_freshness(
            _db_returning(_tracking("fortnightly-ish")), _file()
        )

        assert "freshness check skipped" in result["message"]


# ---------------------------------------------------------------------------
# The two stamping call sites
# ---------------------------------------------------------------------------

class TestStampingIsMonotonicEverywhere:
    """Both ingest paths and the task-completion path share one guard.

    These exercise the guard through the shared helper rather than through each
    endpoint, because what the endpoints add is HTTP plumbing and what is worth
    pinning is the rule. That the call sites *use* it is what the mutation sweep
    checks: reverting either call site to a bare assignment fails these.
    """

    def test_a_backdated_delivery_cannot_regress_a_live_programme(self):
        tracker = SimpleNamespace(last_collection_date=_utc_today())

        changed = advance_last_collection_date(
            tracker, _utc_today() - timedelta(days=90)
        )

        assert changed is False
        assert tracker.last_collection_date == _utc_today()

    def test_a_newer_collection_still_moves_it_forward(self):
        tracker = SimpleNamespace(last_collection_date=_utc_today() - timedelta(days=30))

        changed = advance_last_collection_date(tracker, _utc_today())

        assert changed is True
        assert tracker.last_collection_date == _utc_today()

    def test_a_missing_tracker_is_a_no_op_rather_than_an_attribute_error(self):
        assert advance_last_collection_date(None, _utc_today()) is False


class TestTheCallSitesActuallyUseTheSharedRule:
    """Source-level, and deliberately so.

    Each of these call sites lives inside an endpoint whose surrounding plumbing
    (auth, tenancy resolution, S3, audit) costs far more to stand up than the one
    line under test is worth. What can be checked cheaply and exactly is that the
    line is routed through the shared helper rather than assigning the column
    directly — which is the specific regression that produced the original
    divergence.
    """

    def test_the_webhook_inbox_routes_through_the_helper(self):
        import inspect
        from api import evidence_inbox

        source = inspect.getsource(evidence_inbox)

        assert "advance_last_collection_date(tracker" in source
        assert "tracker.last_collection_date = " not in source

    def test_task_completion_routes_through_the_helper(self):
        import inspect
        from api import evidence_tasks

        source = inspect.getsource(evidence_tasks)

        assert "advance_last_collection_date(evidence" in source
        assert "evidence.last_collection_date = " not in source

    def test_the_browser_upload_path_routes_through_the_helper(self):
        import inspect
        from api import evidence_files

        source = inspect.getsource(evidence_files)

        assert "advance_last_collection_date(tracker" in source
