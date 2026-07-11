"""Unit tests for tasks_vendor_assessment — unified persistence.

Uses a recording fake sync session (no database) to verify that:
- completion writes the report onto the unified vendor_assessments row,
- CIA control + action item children are created against the assessment,
- the vendor's risk score is updated with provenance and a next review date
  of completed_at + 12 months,
- failures mark the row failed with an error message,
- the full task flow wires engine output through persistence + cache.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from unittest.mock import MagicMock, patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tasks_vendor_assessment as tva  # noqa: E402


ASSESSMENT_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
VENDOR_ID = "11111111-2222-3333-4444-555555555555"
JOB_ID = "dpsia-test00000001"


# ---------------------------------------------------------------------------
# Recording fake session
# ---------------------------------------------------------------------------

class FakeSession:
    """Records (sql, params) per execute; first execute returns RETURNING id."""

    def __init__(self, returning_id=ASSESSMENT_ID):
        self.calls = []  # list of (sql_text, params)
        self.committed = False
        self.rolled_back = False
        self._returning_id = returning_id

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.calls.append((sql, params or {}))
        result = MagicMock()
        if "RETURNING id" in sql:
            result.fetchone.return_value = (self._returning_id,) if self._returning_id else None
        else:
            result.fetchone.return_value = None
        return result

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


def _sample_result():
    return {
        "rag_status": "AMBER",
        "recommendation": "CONDITIONAL",
        "risk_score": 12,
        "risk_level": "High",
        "executive_summary": "Exec summary",
        "report_markdown": "# Report",
        "report_json": {
            "inherentRiskScore": 16,
            "inherentRiskLevel": "High",
            "controlEffectivenessPercent": 60,
            "confidentialityControls": [
                {"control": "Encryption at Rest", "rating": "Strong", "implementation": "AES-256"},
                {"control": "Access Control", "rating": "Adequate", "implementation": "RBAC"},
            ],
            "integrityControls": [
                {"control": "Change Management", "rating": "Weak", "implementation": "Ad hoc"},
            ],
            "availabilityControls": [],
            "mandatoryActions": [
                {"action": "Obtain SOC 2", "priority": "High", "owner": "Vendor", "dueDate": "2026-12-31"},
            ],
        },
        "research_sources": ["https://example.com"],
        "processing_time_ms": 42000,
    }


# ---------------------------------------------------------------------------
# _add_months
# ---------------------------------------------------------------------------

class TestAddMonths:
    def test_plain_12_months(self):
        assert tva._add_months(datetime(2026, 3, 15), 12) == date(2027, 3, 15)

    def test_leap_day_clamps(self):
        assert tva._add_months(datetime(2024, 2, 29), 12) == date(2025, 2, 28)

    def test_end_of_month_clamps(self):
        assert tva._add_months(datetime(2026, 1, 31), 1) == date(2026, 2, 28)

    def test_december_rollover(self):
        assert tva._add_months(datetime(2026, 12, 5), 12) == date(2027, 12, 5)


# ---------------------------------------------------------------------------
# _persist_completed
# ---------------------------------------------------------------------------

class TestPersistCompleted:
    def _run(self, monkeypatch, result=None, session=None):
        session = session or FakeSession()
        monkeypatch.setattr(tva, "_get_sync_session", lambda: session)
        completed_at = datetime(2026, 7, 11, 12, 0, 0)
        assessment_id = tva._persist_completed(
            job_id=JOB_ID,
            vendor_id=VENDOR_ID,
            result=result or _sample_result(),
            completed_at=completed_at,
        )
        return assessment_id, session, completed_at

    def test_updates_unified_row_by_job_id(self, monkeypatch):
        assessment_id, session, _ = self._run(monkeypatch)
        assert assessment_id == ASSESSMENT_ID
        sql, params = session.calls[0]
        assert "UPDATE vendor_assessments" in sql
        assert "WHERE job_id = :job_id" in sql
        assert params["job_id"] == JOB_ID
        assert params["rag_status"] == "AMBER"
        assert params["recommendation"] == "CONDITIONAL"
        assert params["risk_score"] == 12
        assert params["risk_level"] == "high"  # lowercased
        assert params["report_markdown"] == "# Report"
        assert params["inherent_risk_score"] == 16
        assert params["inherent_risk_level"] == "high"
        assert params["control_effectiveness_pct"] == 60
        # CIA pillar averages: conf (5+3)/2 = 4, integ 1, avail None
        assert params["conf_score"] == 4
        assert params["integ_score"] == 1
        assert params["avail_score"] is None
        assert session.committed

    def test_next_assessment_date_is_completed_plus_12_months(self, monkeypatch):
        _, session, completed_at = self._run(monkeypatch)
        _, params = session.calls[0]
        assert params["next_assessment_date"] == date(2027, 7, 11)
        assert params["completed_at"] == completed_at
        assert params["assessment_date"] == completed_at.date()

    def test_creates_cia_controls_and_action_items(self, monkeypatch):
        _, session, _ = self._run(monkeypatch)
        cia_inserts = [c for c in session.calls if "INSERT INTO vendor_cia_controls" in c[0]]
        action_inserts = [c for c in session.calls if "INSERT INTO vendor_action_items" in c[0]]
        assert len(cia_inserts) == 3
        assert len(action_inserts) == 1
        pillars = [p["pillar"] for _, p in cia_inserts]
        assert pillars == ["confidentiality", "confidentiality", "integrity"]
        assert cia_inserts[0][1]["score"] == 5  # Strong
        assert cia_inserts[1][1]["score"] == 3  # Adequate
        assert action_inserts[0][1]["priority"] == "high"
        assert action_inserts[0][1]["due_date"] == date(2026, 12, 31)
        # Children link to the assessment, not a report
        assert cia_inserts[0][1]["assessment_id"] == ASSESSMENT_ID
        assert action_inserts[0][1]["assessment_id"] == ASSESSMENT_ID

    def test_supersedes_prior_open_auto_action_items(self, monkeypatch):
        _, session, _ = self._run(monkeypatch)
        supersedes = [
            c for c in session.calls
            if "UPDATE vendor_action_items" in c[0] and "'cancelled'" in c[0]
        ]
        assert len(supersedes) == 1
        sql, params = supersedes[0]
        assert "auto_generated = true" in sql
        assert "status = 'open'" in sql
        assert "assessment_id != :assessment_id" in sql
        assert params == {"vendor_id": VENDOR_ID, "assessment_id": ASSESSMENT_ID}
        # Supersede runs before the new items are inserted
        first_insert = next(
            i for i, c in enumerate(session.calls)
            if "INSERT INTO vendor_action_items" in c[0]
        )
        assert session.calls.index(supersedes[0]) < first_insert

    def test_vendor_risk_updated_with_provenance(self, monkeypatch):
        _, session, completed_at = self._run(monkeypatch)
        vendor_updates = [c for c in session.calls if "UPDATE vendors" in c[0]]
        assert len(vendor_updates) == 1
        sql, params = vendor_updates[0]
        assert "risk_score_source" in sql
        assert params["risk_score"] == 12
        assert params["risk_level"] == "high"
        assert params["assessment_id"] == ASSESSMENT_ID
        assert params["scored_at"] == completed_at
        assert params["next_review_date"] == date(2027, 7, 11)

    def test_vendor_not_updated_without_risk_score(self, monkeypatch):
        result = _sample_result()
        result["risk_score"] = None
        _, session, _ = self._run(monkeypatch, result=result)
        assert not any("UPDATE vendors" in c[0] for c in session.calls)

    def test_missing_row_returns_none(self, monkeypatch):
        session = FakeSession(returning_id=None)
        assessment_id, session, _ = self._run(monkeypatch, session=session)
        assert assessment_id is None
        assert not session.committed


# ---------------------------------------------------------------------------
# _update_assessment_status
# ---------------------------------------------------------------------------

class TestUpdateStatus:
    def test_failed_status_with_error(self, monkeypatch):
        session = FakeSession()
        monkeypatch.setattr(tva, "_get_sync_session", lambda: session)
        now = datetime(2026, 7, 11, 12, 0, 0)
        tva._update_assessment_status(JOB_ID, "failed", error_message="boom", completed_at=now)
        sql, params = session.calls[0]
        assert "UPDATE vendor_assessments" in sql
        assert params["status"] == "failed"
        assert params["error_message"] == "boom"
        assert params["completed_at"] == now
        assert session.committed


# ---------------------------------------------------------------------------
# Full task flow (engine mocked)
# ---------------------------------------------------------------------------

class TestRunVendorAssessmentTask:
    def _kwargs(self):
        return dict(
            vendor_id=VENDOR_ID,
            organization_id="99999999-8888-7777-6666-555555555555",
            job_id=JOB_ID,
            vendor_name="Acme Corp",
            vendor_description="CRM provider",
            vendor_website="https://acme.example",
            services_used="CRM hosting",
            assessment_type="new",
            data_role="Processor",
            client_name="Client A",
            additional_context="",
        )

    def test_success_flow(self, monkeypatch):
        statuses = []
        monkeypatch.setattr(
            tva, "_update_assessment_status",
            lambda job_id, status, **kw: statuses.append((job_id, status, kw)),
        )
        monkeypatch.setattr(tva, "_gather_research_context", lambda vid: "context")
        persist = MagicMock(return_value=ASSESSMENT_ID)
        monkeypatch.setattr(tva, "_persist_completed", persist)
        cache = MagicMock()
        monkeypatch.setattr(tva, "_cache_results", cache)
        monkeypatch.delenv("DPSIA_SERVICE_URL", raising=False)

        engine_result = _sample_result()
        with patch("services.vendor_assessment_engine.run_assessment", return_value=engine_result) as engine:
            outcome = tva.run_vendor_assessment.apply(kwargs=self._kwargs()).result

        assert outcome["status"] == "completed"
        assert outcome["assessment_id"] == ASSESSMENT_ID
        assert outcome["job_id"] == JOB_ID
        # running transition happened first
        assert statuses[0][1] == "running"
        assert "started_at" in statuses[0][2]
        # engine called with engine vocabulary + research context
        assert engine.call_args.kwargs["assessment_type"] == "new"
        assert engine.call_args.kwargs["research_context"] == "context"
        # persistence received the engine result
        assert persist.call_args.kwargs["job_id"] == JOB_ID
        assert persist.call_args.kwargs["vendor_id"] == VENDOR_ID
        # cache payload keeps legacy shape + assessment id
        payload = cache.call_args.args[2]
        assert payload["assessment_id"] == ASSESSMENT_ID
        assert payload["linked_assessment_id"] == ASSESSMENT_ID
        assert payload["linked_report_id"] is None
        assert payload["status"] == "completed"

    def test_failure_marks_row_failed(self, monkeypatch):
        statuses = []
        monkeypatch.setattr(
            tva, "_update_assessment_status",
            lambda job_id, status, **kw: statuses.append((job_id, status, kw)),
        )
        monkeypatch.setattr(tva, "_gather_research_context", lambda vid: "")
        monkeypatch.delenv("DPSIA_SERVICE_URL", raising=False)

        with patch("services.vendor_assessment_engine.run_assessment", side_effect=RuntimeError("engine down")):
            outcome = tva.run_vendor_assessment.apply(kwargs=self._kwargs()).result

        assert outcome["status"] == "failed"
        assert "engine down" in outcome["error"]
        assert statuses[-1][1] == "failed"
        assert statuses[-1][2]["error_message"].startswith("engine down")
        assert "completed_at" in statuses[-1][2]

    def test_external_service_path(self, monkeypatch):
        """When DPSIA_SERVICE_URL is set, the HTTP response maps into the same
        unified persistence (no engine call)."""
        statuses = []
        monkeypatch.setattr(
            tva, "_update_assessment_status",
            lambda job_id, status, **kw: statuses.append((job_id, status, kw)),
        )
        persist = MagicMock(return_value=ASSESSMENT_ID)
        monkeypatch.setattr(tva, "_persist_completed", persist)
        monkeypatch.setattr(tva, "_cache_results", MagicMock())
        monkeypatch.setenv("DPSIA_SERVICE_URL", "http://dpsia.example")

        http_response = MagicMock()
        http_response.json.return_value = {
            "ragStatus": "GREEN",
            "recommendation": "APPROVED",
            "riskScore": 4,
            "riskLevel": "Low",
            "executiveSummary": "Fine",
            "reportMarkdown": "# OK",
            "reportJson": {"ragStatus": "GREEN"},
            "researchSources": [],
            "processingTimeMs": 1000,
            "reportDocxBase64": "ignored",
        }
        http_response.raise_for_status.return_value = None

        with patch("tasks_vendor_assessment.httpx.post", return_value=http_response) as post, \
             patch("services.vendor_assessment_engine.run_assessment") as engine:
            outcome = tva.run_vendor_assessment.apply(kwargs=self._kwargs()).result

        assert outcome["status"] == "completed"
        engine.assert_not_called()
        assert post.call_args.args[0] == "http://dpsia.example/assess"
        result = persist.call_args.kwargs["result"]
        assert result["rag_status"] == "GREEN"
        assert result["risk_score"] == 4
        assert result["report_json"] == {"ragStatus": "GREEN"}
