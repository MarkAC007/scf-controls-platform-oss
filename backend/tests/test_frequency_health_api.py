"""
Unit tests for the Frequency Health API endpoint (M4 PR 2, #574).

Covers ``GET /organizations/{org_id}/evidence/frequency-health``:
- Response shape matches ISC-18.
- ``items`` only contains misaligned rows (ISC-19).
- ``low_confidence_count`` populated separately for awareness.
- ETag header present on 200 responses.
- 304 returned when ``If-None-Match`` matches.

Mock-based — frequency_health_service.compute_for_org is patched so we
exercise the API wrapper, not the compute layer (covered separately in
``test_frequency_health_service.py``).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def membership(org_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = uuid4()
    m.user.db_id = str(uuid4())
    m.role = "viewer"
    return m


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_observation(
    *,
    evidence_id,
    declared,
    suggested,
    cadence_days,
    confidence,
    file_count,
    misaligned,
    reason,
):
    from services.frequency_health_service import FrequencyObservation

    return FrequencyObservation(
        evidence_id=evidence_id,
        declared_frequency=declared,
        observed_cadence_days=cadence_days,
        suggested_frequency=suggested,
        confidence=confidence,
        file_count=file_count,
        misaligned=misaligned,
        reason=reason,
    )


def _make_report(items, *, computed_at=None):
    from services.frequency_health_service import FrequencyHealthReport

    misaligned = sum(1 for i in items if i.misaligned)
    low_conf = sum(
        1 for i in items if i.confidence == "low" and not i.misaligned
    )
    return FrequencyHealthReport(
        items=items,
        misaligned_count=misaligned,
        low_confidence_count=low_conf,
        total_evidence_ids_evaluated=len(items),
        computed_at=computed_at or datetime(2026, 5, 9, 13, 0, 0),
    )


# ---------------------------------------------------------------------------
# Response shape (ISC-18)
# ---------------------------------------------------------------------------

class TestFrequencyHealthResponseShape:

    @pytest.mark.asyncio
    async def test_response_carries_required_fields(
        self, membership, mock_db, org_id,
    ):
        from api.evidence_health import get_frequency_health
        from fastapi import Response

        report = _make_report([
            _make_observation(
                evidence_id="E-BCM-11",
                declared="monthly",
                suggested="daily",
                cadence_days=1.02,
                confidence="high",
                file_count=22,
                misaligned=True,
                reason="misaligned",
            ),
        ])

        with patch(
            "api.evidence_health.frequency_health_service.compute_for_org",
            new_callable=AsyncMock,
            return_value=report,
        ):
            response = Response()
            result = await get_frequency_health(
                request=MagicMock(),
                response=response,
                org_id=org_id,
                if_none_match=None,
                membership=membership,
                db=mock_db,
            )

        assert result.organization_id == org_id
        assert result.evaluation_window_days == 90
        assert result.total_evidence_ids_evaluated == 1
        assert result.misaligned_count == 1
        assert result.low_confidence_count == 0
        assert len(result.items) == 1
        assert result.items[0].evidence_id == "E-BCM-11"
        assert result.items[0].suggested_frequency == "daily"
        assert "ETag" in response.headers


# ---------------------------------------------------------------------------
# Filtering (ISC-19)
# ---------------------------------------------------------------------------

class TestFrequencyHealthFiltering:

    @pytest.mark.asyncio
    async def test_items_only_contains_misaligned_rows(
        self, membership, mock_db, org_id,
    ):
        from api.evidence_health import get_frequency_health
        from fastapi import Response

        report = _make_report([
            _make_observation(
                evidence_id="E-BCM-11",
                declared="monthly",
                suggested="daily",
                cadence_days=1.0,
                confidence="high",
                file_count=22,
                misaligned=True,
                reason="misaligned",
            ),
            _make_observation(
                evidence_id="E-AL-1",
                declared="daily",
                suggested="daily",
                cadence_days=1.05,
                confidence="high",
                file_count=30,
                misaligned=False,
                reason="aligned",
            ),
            _make_observation(
                evidence_id="E-LOW-1",
                declared="weekly",
                suggested=None,
                cadence_days=None,
                confidence="low",
                file_count=2,
                misaligned=False,
                reason="aligned",
            ),
        ])

        with patch(
            "api.evidence_health.frequency_health_service.compute_for_org",
            new_callable=AsyncMock,
            return_value=report,
        ):
            response = Response()
            result = await get_frequency_health(
                request=MagicMock(),
                response=response,
                org_id=org_id,
                if_none_match=None,
                membership=membership,
                db=mock_db,
            )

        ids_in_items = {i.evidence_id for i in result.items}
        assert ids_in_items == {"E-BCM-11"}
        assert result.total_evidence_ids_evaluated == 3
        assert result.misaligned_count == 1
        assert result.low_confidence_count == 1


# ---------------------------------------------------------------------------
# ETag + 304 (ISC-20)
# ---------------------------------------------------------------------------

class TestFrequencyHealthETag:

    @pytest.mark.asyncio
    async def test_etag_set_on_200(self, membership, mock_db, org_id):
        from api.evidence_health import get_frequency_health
        from fastapi import Response

        report = _make_report([])

        with patch(
            "api.evidence_health.frequency_health_service.compute_for_org",
            new_callable=AsyncMock,
            return_value=report,
        ):
            response = Response()
            await get_frequency_health(
                request=MagicMock(),
                response=response,
                org_id=org_id,
                if_none_match=None,
                membership=membership,
                db=mock_db,
            )

        etag = response.headers.get("ETag")
        assert etag is not None
        assert etag.startswith('W/"')
        # 64 hex chars + W/"" wrapping = 4 + 64 = 68
        assert len(etag) == 4 + 64

    @pytest.mark.asyncio
    async def test_revalidation_returns_304(
        self, membership, mock_db, org_id,
    ):
        from api.evidence_health import get_frequency_health
        from fastapi import Response

        report = _make_report([
            _make_observation(
                evidence_id="E-BCM-11",
                declared="monthly",
                suggested="daily",
                cadence_days=1.0,
                confidence="high",
                file_count=10,
                misaligned=True,
                reason="misaligned",
            ),
        ])

        # First call — capture ETag.
        with patch(
            "api.evidence_health.frequency_health_service.compute_for_org",
            new_callable=AsyncMock,
            return_value=report,
        ):
            first_response = Response()
            await get_frequency_health(
                request=MagicMock(),
                response=first_response,
                org_id=org_id,
                if_none_match=None,
                membership=membership,
                db=mock_db,
            )
            first_etag = first_response.headers["ETag"]

            # Second call with If-None-Match should return 304 with same ETag.
            second_response = Response()
            result = await get_frequency_health(
                request=MagicMock(),
                response=second_response,
                org_id=org_id,
                if_none_match=first_etag,
                membership=membership,
                db=mock_db,
            )

        # 304 path returns a Response directly (not the Pydantic model).
        from fastapi import Response as FastAPIResponse
        assert isinstance(result, FastAPIResponse)
        assert result.status_code == 304
        assert result.headers.get("ETag") == first_etag

    @pytest.mark.asyncio
    async def test_etag_changes_when_data_changes(
        self, membership, mock_db, org_id,
    ):
        from api.evidence_health import get_frequency_health
        from fastapi import Response

        # Same computed_at bucket, different misaligned set → ETag differs.
        report_a = _make_report([
            _make_observation(
                evidence_id="E-A",
                declared="daily",
                suggested="weekly",
                cadence_days=7.0,
                confidence="high",
                file_count=12,
                misaligned=True,
                reason="misaligned",
            ),
        ], computed_at=datetime(2026, 5, 9, 13, 0, 0))

        report_b = _make_report([
            _make_observation(
                evidence_id="E-A",
                declared="daily",
                suggested="weekly",
                cadence_days=7.0,
                confidence="high",
                file_count=12,
                misaligned=True,
                reason="misaligned",
            ),
            _make_observation(
                evidence_id="E-B",
                declared="weekly",
                suggested="monthly",
                cadence_days=30.0,
                confidence="medium",
                file_count=4,
                misaligned=True,
                reason="misaligned",
            ),
        ], computed_at=datetime(2026, 5, 9, 13, 0, 0))

        with patch(
            "api.evidence_health.frequency_health_service.compute_for_org",
            new_callable=AsyncMock,
            return_value=report_a,
        ):
            r_a = Response()
            await get_frequency_health(
                request=MagicMock(),
                response=r_a,
                org_id=org_id,
                if_none_match=None,
                membership=membership,
                db=mock_db,
            )
        with patch(
            "api.evidence_health.frequency_health_service.compute_for_org",
            new_callable=AsyncMock,
            return_value=report_b,
        ):
            r_b = Response()
            await get_frequency_health(
                request=MagicMock(),
                response=r_b,
                org_id=org_id,
                if_none_match=None,
                membership=membership,
                db=mock_db,
            )

        assert r_a.headers["ETag"] != r_b.headers["ETag"]
