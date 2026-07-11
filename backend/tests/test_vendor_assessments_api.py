"""API contract tests for the unified vendor assessment endpoints (Phase 3)
and the deprecated /dpsia/* aliases.

Uses FastAPI TestClient with a scripted fake AsyncSession (dependency
override on get_db) and monkeypatched auth internals, so no database or
Redis is required. Service-layer functions that the routes delegate to
(trigger/get_latest/...) are patched at the api.vendors namespace.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import auth as auth_module  # noqa: E402
from auth import OrgMembership  # noqa: E402
from database import get_db  # noqa: E402
from api import vendors as vendors_api  # noqa: E402


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
VENDOR_ID = UUID("00000000-0000-0000-0000-000000000002")
ASSESSMENT_ID = UUID("00000000-0000-0000-0000-000000000003")
AUTH = {"Authorization": "Bearer test-key"}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _vendor_row(**overrides) -> SimpleNamespace:
    base = dict(
        id=VENDOR_ID,
        organization_id=ORG_ID,
        name="Acme Corp",
        description=None,
        website=None,
        category=None,
        status="active",
        criticality="low",
        contact_name=None,
        contact_email=None,
        contact_phone=None,
        contract_start_date=None,
        contract_end_date=None,
        contract_value=None,
        risk_score=None,
        risk_level=None,
        risk_score_source=None,
        risk_scored_at=None,
        next_review_date=None,
        data_classification=None,
        created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 1, 1),
        created_by_user_id=None,
        updated_by_user_id=None,
        created_by=None,
        updated_by=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _assessment_row(**overrides) -> SimpleNamespace:
    base = dict(
        id=ASSESSMENT_ID,
        vendor_id=VENDOR_ID,
        assessment_type="initial",
        assessment_date=date(2026, 7, 1),
        status="completed",
        confidentiality_score=4,
        integrity_score=3,
        availability_score=5,
        breach_score=None,
        certification_score=None,
        cve_score=None,
        regulatory_score=None,
        data_handling_score=None,
        likelihood=None,
        impact=None,
        final_risk_score=12,
        risk_level="high",
        ai_analysis="Exec summary",
        findings="Exec summary",
        risk_rating="high",
        next_assessment_date=date(2027, 7, 1),
        assessor_user_id=None,
        inherent_risk_score=16,
        inherent_risk_level="high",
        control_effectiveness_pct=60,
        job_id="dpsia-abc123def456",
        started_at=datetime(2026, 7, 1, 10, 0, 0),
        completed_at=datetime(2026, 7, 1, 10, 5, 0),
        error_message=None,
        triggered_by_user_id=None,
        data_role="Processor",
        services_used="CRM hosting",
        client_name="Client A",
        additional_context=None,
        rag_status="AMBER",
        recommendation="CONDITIONAL",
        executive_summary="Exec summary",
        report_markdown="# Report",
        report_json={"ragStatus": "AMBER"},
        research_sources=["https://example.com"],
        processing_time_ms=42000,
        created_at=datetime(2026, 7, 1, 9, 59, 0),
        updated_at=datetime(2026, 7, 1, 10, 5, 0),
        created_by_user_id=None,
        updated_by_user_id=None,
        created_by=None,
        updated_by=None,
        assessor=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None


class _FakeAsyncSession:
    """Scripted async session — pops the next pre-arranged result per execute()."""

    def __init__(self, responses: List[List[Any]]):
        self._responses = list(responses)

    async def execute(self, _stmt) -> _Result:
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(list(self._responses.pop(0)))

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass

    def add(self, _obj):
        pass


@pytest.fixture
def client_factory(monkeypatch):
    """(responses, role='editor') -> TestClient with auth+db faked out."""
    app = main.app

    def _build(responses: List[List[Any]], role: str = "editor") -> TestClient:
        session = _FakeAsyncSession(responses)

        async def _override_db():
            yield session

        async def _fake_require_auth(credentials, db):
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return user

        async def _fake_verify_org_membership(org_id, user, db, min_role="viewer"):
            return OrgMembership(user=user, organization_id=org_id, role=role, is_consultant=False)

        monkeypatch.setattr(auth_module, "require_auth", _fake_require_auth)
        monkeypatch.setattr(auth_module, "verify_org_membership", _fake_verify_org_membership)
        app.dependency_overrides[get_db] = _override_db
        return TestClient(app)

    yield _build
    app.dependency_overrides.pop(get_db, None)


BASE = f"/api/organizations/{ORG_ID}/vendors/{VENDOR_ID}"


# ---------------------------------------------------------------------------
# POST /assessments — unified AI assessment trigger
# ---------------------------------------------------------------------------

class TestTriggerAssessment:
    def test_trigger_returns_202_with_ids(self, client_factory, monkeypatch):
        trigger = AsyncMock(return_value={
            "assessment_id": str(ASSESSMENT_ID),
            "job_id": "dpsia-abc123def456",
            "vendor_id": str(VENDOR_ID),
            "status": "pending",
        })
        monkeypatch.setattr(vendors_api, "trigger_ai_assessment", trigger)
        client = client_factory([[_vendor_row()]])

        resp = client.post(
            f"{BASE}/assessments",
            json={"assessment_type": "initial", "services_used": "CRM hosting"},
            headers=AUTH,
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["assessment_id"] == str(ASSESSMENT_ID)
        assert body["job_id"] == "dpsia-abc123def456"
        assert body["status"] == "pending"
        # assessment_type mapped for the engine happens in the service layer;
        # the route passes the API value through
        assert trigger.call_args.kwargs["assessment_type"] == "initial"
        assert trigger.call_args.kwargs["data_role"] == "Processor"

    @pytest.mark.parametrize("atype", ["annual", "adhoc"])
    def test_trigger_accepts_all_types(self, client_factory, monkeypatch, atype):
        trigger = AsyncMock(return_value={
            "assessment_id": str(ASSESSMENT_ID), "job_id": "j",
            "vendor_id": str(VENDOR_ID), "status": "pending",
        })
        monkeypatch.setattr(vendors_api, "trigger_ai_assessment", trigger)
        client = client_factory([[_vendor_row()]])
        resp = client.post(
            f"{BASE}/assessments",
            json={"assessment_type": atype, "services_used": "X"},
            headers=AUTH,
        )
        assert resp.status_code == 202

    def test_trigger_rejects_invalid_type(self, client_factory):
        client = client_factory([[_vendor_row()]])
        resp = client.post(
            f"{BASE}/assessments",
            json={"assessment_type": "new", "services_used": "X"},  # legacy value not allowed here
            headers=AUTH,
        )
        assert resp.status_code == 422

    def test_trigger_conflict_returns_400(self, client_factory, monkeypatch):
        trigger = AsyncMock(side_effect=ValueError("An AI assessment is already in progress for this vendor"))
        monkeypatch.setattr(vendors_api, "trigger_ai_assessment", trigger)
        client = client_factory([[_vendor_row()]])
        resp = client.post(
            f"{BASE}/assessments",
            json={"services_used": "X"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "already in progress" in resp.json()["detail"]

    def test_trigger_unknown_vendor_404(self, client_factory):
        client = client_factory([[]])  # vendor lookup returns nothing
        resp = client.post(
            f"{BASE}/assessments",
            json={"services_used": "X"},
            headers=AUTH,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET list / latest / single / status
# ---------------------------------------------------------------------------

class TestReadAssessments:
    def test_list_returns_unified_fields(self, client_factory):
        client = client_factory([[_vendor_row()], [_assessment_row()]])
        resp = client.get(f"{BASE}/assessments", headers=AUTH)
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        item = items[0]
        assert item["job_id"] == "dpsia-abc123def456"
        assert item["rag_status"] == "AMBER"
        assert item["recommendation"] == "CONDITIONAL"
        assert item["final_risk_score"] == 12
        assert item["report_markdown"] == "# Report"
        assert item["report_json"] == {"ragStatus": "AMBER"}
        assert item["research_sources"] == ["https://example.com"]
        assert item["executive_summary"] == "Exec summary"

    def test_latest_returns_completed_assessment(self, client_factory):
        client = client_factory([[_vendor_row()], [_assessment_row()]])
        resp = client.get(f"{BASE}/assessments/latest", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()["id"] == str(ASSESSMENT_ID)
        assert resp.json()["status"] == "completed"

    def test_latest_404_when_none(self, client_factory):
        client = client_factory([[_vendor_row()], []])
        resp = client.get(f"{BASE}/assessments/latest", headers=AUTH)
        assert resp.status_code == 404

    def test_get_single_assessment(self, client_factory):
        client = client_factory([[_vendor_row()], [_assessment_row()]])
        resp = client.get(f"{BASE}/assessments/{ASSESSMENT_ID}", headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(ASSESSMENT_ID)
        assert body["services_used"] == "CRM hosting"
        assert body["data_role"] == "Processor"
        assert body["processing_time_ms"] == 42000

    def test_get_single_404(self, client_factory):
        client = client_factory([[_vendor_row()], []])
        resp = client.get(f"{BASE}/assessments/{uuid4()}", headers=AUTH)
        assert resp.status_code == 404

    def test_status_endpoint_shape(self, client_factory):
        client = client_factory([[_vendor_row()], [_assessment_row(status="running", completed_at=None)]])
        resp = client.get(f"{BASE}/assessments/{ASSESSMENT_ID}/status", headers=AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body == {
            "assessment_id": str(ASSESSMENT_ID),
            "job_id": "dpsia-abc123def456",
            "vendor_id": str(VENDOR_ID),
            "status": "running",
            "started_at": "2026-07-01T10:00:00",
            "completed_at": None,
            "created_at": "2026-07-01T09:59:00",
            "error_message": None,
        }


# ---------------------------------------------------------------------------
# Deprecated /dpsia/* aliases
# ---------------------------------------------------------------------------

class TestDpsiaAliases:
    def test_post_dpsia_alias_deprecation_header_and_legacy_shape(self, client_factory, monkeypatch):
        trigger = AsyncMock(return_value={
            "assessment_id": str(ASSESSMENT_ID),
            "job_id": "dpsia-abc123def456",
            "vendor_id": str(VENDOR_ID),
            "status": "pending",
        })
        monkeypatch.setattr(vendors_api, "trigger_ai_assessment", trigger)
        client = client_factory([[_vendor_row()]])
        resp = client.post(
            f"{BASE}/dpsia",
            json={"assessment_type": "new", "services_used": "CRM hosting"},
            headers=AUTH,
        )
        assert resp.status_code == 202
        assert resp.headers.get("Deprecation") == "true"
        # Legacy response shape preserved (no assessment_id leaked)
        assert resp.json() == {
            "job_id": "dpsia-abc123def456",
            "vendor_id": str(VENDOR_ID),
            "status": "pending",
        }
        # Legacy vocabulary passed through; service normalises it
        assert trigger.call_args.kwargs["assessment_type"] == "new"

    def test_get_dpsia_latest_alias(self, client_factory, monkeypatch):
        legacy = {
            "job_id": "dpsia-abc123def456",
            "vendor_id": str(VENDOR_ID),
            "status": "completed",
            "assessment_type": "new",
            "data_role": "Processor",
            "rag_status": "AMBER",
            "recommendation": "CONDITIONAL",
            "risk_score": 12,
            "risk_level": "high",
            "executive_summary": "Exec summary",
            "report_markdown": "# Report",
            "report_json": {"ragStatus": "AMBER"},
            "report_filename": None,
            "research_sources": ["https://example.com"],
            "linked_assessment_id": str(ASSESSMENT_ID),
            "linked_report_id": None,
            "processing_time_ms": 42000,
            "error_message": None,
            "started_at": None,
            "completed_at": "2026-07-01T10:05:00",
            "created_at": None,
        }
        monkeypatch.setattr(vendors_api, "get_ai_latest", AsyncMock(return_value=legacy))
        client = client_factory([[_vendor_row()]])
        resp = client.get(f"{BASE}/dpsia/latest", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == "true"
        body = resp.json()
        assert body["risk_score"] == 12
        assert body["linked_assessment_id"] == str(ASSESSMENT_ID)

    def test_get_dpsia_status_alias(self, client_factory, monkeypatch):
        monkeypatch.setattr(vendors_api, "get_ai_status", AsyncMock(return_value={
            "job_id": "dpsia-abc123def456",
            "vendor_id": str(VENDOR_ID),
            "status": "running",
            "started_at": None,
            "completed_at": None,
            "created_at": None,
            "error_message": None,
        }))
        client = client_factory([[_vendor_row()]])
        resp = client.get(f"{BASE}/dpsia/dpsia-abc123def456/status", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers.get("Deprecation") == "true"
        assert resp.json()["status"] == "running"

    def test_get_dpsia_results_alias(self, client_factory, monkeypatch):
        monkeypatch.setattr(vendors_api, "get_ai_results", AsyncMock(return_value=None))
        client = client_factory([[_vendor_row()]])
        resp = client.get(f"{BASE}/dpsia/dpsia-unknown", headers=AUTH)
        assert resp.status_code == 404
        assert resp.headers.get("Deprecation") == "true"

    def test_get_dpsia_active_alias_404_carries_header(self, client_factory, monkeypatch):
        monkeypatch.setattr(vendors_api, "get_ai_active", AsyncMock(return_value=None))
        client = client_factory([[_vendor_row()]])
        resp = client.get(f"{BASE}/dpsia/active", headers=AUTH)
        assert resp.status_code == 404
        assert resp.headers.get("Deprecation") == "true"

    def test_docx_route_returns_410_gone(self, client_factory):
        client = client_factory([[_vendor_row()]])
        resp = client.get(f"{BASE}/dpsia/dpsia-abc123def456/docx", headers=AUTH)
        assert resp.status_code == 410
        assert resp.headers.get("Deprecation") == "true"
        detail = resp.json()["detail"]
        assert "markdown" in detail["message"]
        assert "report_markdown" in detail["hint"]


# ---------------------------------------------------------------------------
# Vendor list/detail: review_status + risk_provenance
# ---------------------------------------------------------------------------

class TestVendorReviewStatus:
    def _get_vendor(self, client_factory, vendor):
        client = client_factory([[vendor]])
        resp = client.get(f"/api/organizations/{ORG_ID}/vendors/{VENDOR_ID}", headers=AUTH)
        assert resp.status_code == 200
        return resp.json()

    def test_never_assessed_is_null(self, client_factory):
        body = self._get_vendor(client_factory, _vendor_row())
        assert body["review_status"] is None
        assert body["risk_provenance"] is None

    def test_ok_when_far_away(self, client_factory):
        body = self._get_vendor(
            client_factory,
            _vendor_row(next_review_date=date.today() + timedelta(days=200)),
        )
        assert body["review_status"] == "ok"

    def test_due_soon_at_30_days(self, client_factory):
        body = self._get_vendor(
            client_factory,
            _vendor_row(next_review_date=date.today() + timedelta(days=30)),
        )
        assert body["review_status"] == "due_soon"

    def test_due_soon_today(self, client_factory):
        body = self._get_vendor(
            client_factory,
            _vendor_row(next_review_date=date.today()),
        )
        assert body["review_status"] == "due_soon"

    def test_overdue_yesterday(self, client_factory):
        body = self._get_vendor(
            client_factory,
            _vendor_row(next_review_date=date.today() - timedelta(days=1)),
        )
        assert body["review_status"] == "overdue"

    def test_risk_provenance_present(self, client_factory):
        scored_at = datetime(2026, 7, 1, 10, 5, 0)
        body = self._get_vendor(
            client_factory,
            _vendor_row(
                risk_score=12,
                risk_level="high",
                risk_score_source=ASSESSMENT_ID,
                risk_scored_at=scored_at,
                next_review_date=date(2027, 7, 1),
            ),
        )
        assert body["risk_provenance"] == {
            "assessment_id": str(ASSESSMENT_ID),
            "scored_at": "2026-07-01T10:05:00",
        }

    def test_vendor_list_includes_review_fields(self, client_factory):
        vendor = _vendor_row(next_review_date=date.today() - timedelta(days=10))
        client = client_factory([[vendor]])
        resp = client.get(f"/api/organizations/{ORG_ID}/vendors", headers=AUTH)
        assert resp.status_code == 200
        assert resp.json()[0]["review_status"] == "overdue"
