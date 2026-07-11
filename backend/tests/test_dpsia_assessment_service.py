"""Unit tests for services.dpsia_assessment (unified vendor_assessments).

Covers assessment-type vocabulary mapping (API <-> engine <-> storage),
trigger behaviour (row creation + task dispatch) and the legacy-shaped
result dictionary used by the deprecated /dpsia/* aliases.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import dpsia_assessment as svc  # noqa: E402


class TestTypeMappings:
    def test_api_to_engine(self):
        assert svc.API_TO_ENGINE_TYPE == {
            "initial": "new",
            "annual": "annual-review",
            "adhoc": "adhoc",
        }

    def test_legacy_to_api(self):
        assert svc.LEGACY_TO_API_TYPE == {
            "new": "initial",
            "annual-review": "annual",
            "adhoc": "adhoc",
        }

    def test_storage_to_legacy_covers_old_platform_values(self):
        # periodic/triggered were the old auto-created platform values
        assert svc.API_TO_LEGACY_TYPE["periodic"] == "annual-review"
        assert svc.API_TO_LEGACY_TYPE["triggered"] == "adhoc"


def _fake_db(vendor=None, running=None, org_name="Client Org"):
    """AsyncMock db scripted for trigger_assessment's three queries."""
    db = AsyncMock()
    vendor_result = MagicMock()
    vendor_result.scalar_one_or_none.return_value = vendor
    running_result = MagicMock()
    running_result.first.return_value = running
    org_result = MagicMock()
    org_result.scalar_one_or_none.return_value = org_name
    db.execute = AsyncMock(side_effect=[vendor_result, running_result, org_result])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _vendor():
    return SimpleNamespace(
        id=uuid4(), name="Acme", description="CRM", website="https://acme.example",
    )


class TestTrigger:
    @pytest.mark.asyncio
    async def test_trigger_creates_pending_row_and_dispatches(self):
        vendor = _vendor()
        db = _fake_db(vendor=vendor)
        org_id = str(uuid4())

        with patch("celery_app.celery_app") as celery:
            result = await svc.trigger_assessment(
                db=db,
                vendor_id=str(vendor.id),
                organization_id=org_id,
                services_used="CRM hosting",
                assessment_type="annual",
                data_role="Controller",
            )

        # Unified row created with pending status + storage vocabulary
        row = db.add.call_args.args[0]
        assert row.status == "pending"
        assert row.assessment_type == "annual"
        assert row.job_id.startswith("dpsia-")
        assert row.services_used == "CRM hosting"
        assert row.client_name == "Client Org"  # defaulted from org name
        assert row.assessment_date == date.today()

        # Task dispatched with the ENGINE vocabulary
        kwargs = celery.send_task.call_args.kwargs["kwargs"]
        assert kwargs["assessment_type"] == "annual-review"
        assert kwargs["job_id"] == row.job_id

        assert result["assessment_id"] == str(row.id)
        assert result["job_id"] == row.job_id
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_trigger_accepts_legacy_vocabulary(self):
        vendor = _vendor()
        db = _fake_db(vendor=vendor)
        with patch("celery_app.celery_app") as celery:
            await svc.trigger_assessment(
                db=db,
                vendor_id=str(vendor.id),
                organization_id=str(uuid4()),
                services_used="X",
                assessment_type="new",  # legacy DPSIA value
            )
        row = db.add.call_args.args[0]
        assert row.assessment_type == "initial"  # normalised for storage
        kwargs = celery.send_task.call_args.kwargs["kwargs"]
        assert kwargs["assessment_type"] == "new"  # engine vocabulary

    @pytest.mark.asyncio
    async def test_trigger_rejects_unknown_type(self):
        db = _fake_db(vendor=_vendor())
        with pytest.raises(ValueError, match="Invalid assessment_type"):
            await svc.trigger_assessment(
                db=db, vendor_id="v", organization_id="o",
                services_used="X", assessment_type="bogus",
            )

    @pytest.mark.asyncio
    async def test_trigger_conflicts_when_running(self):
        db = _fake_db(vendor=_vendor(), running=object())
        with pytest.raises(ValueError, match="already in progress"):
            await svc.trigger_assessment(
                db=db, vendor_id="v", organization_id="o", services_used="X",
            )

    @pytest.mark.asyncio
    async def test_trigger_missing_vendor(self):
        db = _fake_db(vendor=None)
        with pytest.raises(ValueError, match="not found"):
            await svc.trigger_assessment(
                db=db, vendor_id="v", organization_id="o", services_used="X",
            )


class TestRowToDict:
    def test_legacy_shape_from_unified_row(self):
        row_id = uuid4()
        vendor_id = uuid4()
        row = SimpleNamespace(
            id=row_id,
            vendor_id=vendor_id,
            job_id="dpsia-xyz",
            status="completed",
            assessment_type="initial",
            data_role="Processor",
            rag_status="GREEN",
            recommendation="APPROVED",
            final_risk_score=4,
            risk_level="low",
            executive_summary="Fine",
            report_markdown="# OK",
            report_json={"a": 1},
            research_sources=["s"],
            processing_time_ms=1000,
            error_message=None,
            started_at=datetime(2026, 7, 1, 10, 0, 0),
            completed_at=datetime(2026, 7, 1, 10, 5, 0),
            created_at=datetime(2026, 7, 1, 9, 59, 0),
        )
        d = svc._row_to_dict(row)
        assert d["job_id"] == "dpsia-xyz"
        assert d["assessment_type"] == "new"  # storage -> legacy vocabulary
        assert d["risk_score"] == 4           # final_risk_score surfaced as risk_score
        assert d["linked_assessment_id"] == str(row_id)  # self-reference
        assert d["linked_report_id"] is None
        assert d["report_filename"] is None
        assert d["assessment_id"] == str(row_id)
        assert d["completed_at"] == "2026-07-01T10:05:00"
