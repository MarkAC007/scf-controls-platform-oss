"""The evidence-assessment engine over HTTP: the setting and the two reads.

- ``evidence_assessment_engine`` round-trips through the organisation
  settings GET/PATCH and is refused (422) for any value outside the three,
- ``GET .../evidence-assessment/engine`` reports the engine, whether a
  TypeSafe key is present (presence only — never the value), the Jev model,
  the cutoff and the shadow statistics,
- ``GET .../assessment/shadow`` returns the latest shadow row or 404.

Endpoint functions are called directly with a mocked AsyncSession, the idiom
used by ``test_assessment_review_api.py``. No database.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from schemas import (  # noqa: E402
    OrganizationSettingsResponse,
    OrganizationSettingsUpdate,
    ShadowVerdictResponse,
)
from services import jev_assessment as ja  # noqa: E402
from services.assessment_prompts import PROMPT_VERSION  # noqa: E402


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def membership(org_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.db_id = str(uuid4())
    m.role = "admin"
    return m


def _db(*results):
    """An AsyncSession whose successive ``execute`` calls yield *results*."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    execute_results = []
    for value in results:
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        r.scalar = MagicMock(return_value=value)
        r.mappings.return_value.first = MagicMock(return_value=value)
        execute_results.append(r)
    db.execute = AsyncMock(side_effect=execute_results)
    return db


# ---------------------------------------------------------------------------
# The setting
# ---------------------------------------------------------------------------

class TestSettingSchema:
    @pytest.mark.parametrize("engine", ja.ENGINES)
    def test_accepts_the_three_engines(self, engine):
        assert OrganizationSettingsUpdate(evidence_assessment_engine=engine).evidence_assessment_engine == engine

    @pytest.mark.parametrize("bad", ["gpt", "JEV", "", "shadow", 1])
    def test_refuses_anything_else(self, bad):
        with pytest.raises(ValidationError):
            OrganizationSettingsUpdate(evidence_assessment_engine=bad)

    def test_response_defaults_to_llm(self):
        assert OrganizationSettingsResponse().evidence_assessment_engine == "llm"

    def test_update_is_partial(self):
        assert "evidence_assessment_engine" not in OrganizationSettingsUpdate(industry="x").model_dump(exclude_unset=True)


class TestSettingsEndpoint:
    @pytest.mark.asyncio
    async def test_get_reports_the_engine(self, org_id, membership):
        from api.organizations import get_organization_settings

        org = SimpleNamespace(id=org_id, name="Acme", settings={ja.ENGINE_SETTING_KEY: "jev_shadow"})
        out = await get_organization_settings(org_id=org_id, membership=membership, db=_db(org))
        assert out.evidence_assessment_engine == "jev_shadow"

    @pytest.mark.asyncio
    async def test_get_defaults_when_unset_or_junk(self, org_id, membership):
        from api.organizations import get_organization_settings

        for settings in ({}, {ja.ENGINE_SETTING_KEY: "gpt"}):
            org = SimpleNamespace(id=org_id, name="Acme", settings=settings)
            out = await get_organization_settings(org_id=org_id, membership=membership, db=_db(org))
            assert out.evidence_assessment_engine == "llm"

    @pytest.mark.asyncio
    async def test_patch_merges_the_engine_and_keeps_other_keys(self, org_id, membership):
        from api.organizations import update_organization_settings

        org = SimpleNamespace(id=org_id, name="Acme", settings={"industry": "Biotech", "owner_teams": ["a"]})
        db = _db(org)
        with patch("api.organizations.log_entity_changes", new=AsyncMock()) as audit, \
                patch("api.organizations.detect_action_source", return_value="ui"), \
                patch("api.organizations.get_request_id", return_value=None):
            out = await update_organization_settings(
                org_id=org_id,
                settings_data=OrganizationSettingsUpdate(evidence_assessment_engine="jev"),
                request=MagicMock(),
                membership=membership,
                db=db,
            )
        assert out.evidence_assessment_engine == "jev"
        assert org.settings == {"industry": "Biotech", "owner_teams": ["a"], ja.ENGINE_SETTING_KEY: "jev"}
        assert out.industry == "Biotech"
        # The change is in the audit trail like every other settings change.
        audit.assert_awaited_once()
        assert audit.call_args.kwargs["new_values"]["settings"][ja.ENGINE_SETTING_KEY] == "jev"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_patch_without_the_field_leaves_it_alone(self, org_id, membership):
        from api.organizations import update_organization_settings

        org = SimpleNamespace(id=org_id, name="Acme", settings={ja.ENGINE_SETTING_KEY: "jev_shadow"})
        with patch("api.organizations.log_entity_changes", new=AsyncMock()), \
                patch("api.organizations.detect_action_source", return_value="ui"), \
                patch("api.organizations.get_request_id", return_value=None):
            out = await update_organization_settings(
                org_id=org_id,
                settings_data=OrganizationSettingsUpdate(industry="Biotech"),
                request=MagicMock(),
                membership=membership,
                db=_db(org),
            )
        assert out.evidence_assessment_engine == "jev_shadow"
        assert org.settings[ja.ENGINE_SETTING_KEY] == "jev_shadow"


# ---------------------------------------------------------------------------
# GET .../evidence-assessment/engine
# ---------------------------------------------------------------------------

def _stats_row(**overrides):
    row = {
        "compared_verdicts": 4, "failed_verdicts": 1,
        "objectives_compared": 40, "objectives_agreed": 34,
        "confident_objectives": 30, "confident_agreed": 29,
        "status_agreed": 3, "status_compared": 4,
        "mean_latency_ms": Decimal("120.5"), "total_cost_cents": Decimal("0.0136"),
        "last_compared_at": datetime(2026, 9, 29, 6, 0, 0),
    }
    row.update(overrides)
    return row


class TestEngineStatus:
    @pytest.mark.asyncio
    async def test_reports_engine_key_presence_model_cutoff_and_stats(self, org_id, membership, monkeypatch):
        from api.evidence_assessment import get_assessment_engine_status

        monkeypatch.setenv(ja.CONFIDENCE_CUTOFF_ENV, "0.9")
        db = _db({ja.ENGINE_SETTING_KEY: "jev_shadow"}, _stats_row())
        with patch("api.evidence_assessment.integration_enabled", return_value=True) as enabled:
            out = await get_assessment_engine_status(org_id=org_id, membership=membership, db=db)

        enabled.assert_called_once_with("TYPESAFE_API_KEY")
        assert out.engine == "jev_shadow"
        assert out.typesafe_key_configured is True
        assert out.jev_model_id == "jev-1.13.0"
        assert out.confidence_cutoff == pytest.approx(0.9)
        s = out.shadow_stats
        assert s.compared_verdicts == 4 and s.failed_verdicts == 1
        assert s.objectives_compared == 40 and s.objectives_agreed == 34
        assert s.agreement_rate == 0.85
        assert s.confident_objectives == 30 and s.confident_agreed == 29
        assert s.confident_agreement_rate == pytest.approx(0.9667)
        assert s.status_agreement_rate == 0.75
        assert s.mean_latency_ms == pytest.approx(120.5)
        assert s.total_cost_cents == pytest.approx(0.0136)
        # Naive UTC in the database is said to be UTC on the wire.
        assert s.last_compared_at.tzinfo is not None
        assert s.last_compared_at.isoformat() == "2026-09-29T06:00:00+00:00"

    @pytest.mark.asyncio
    async def test_no_shadow_rows_is_none_not_zero_percent(self, org_id, membership):
        from api.evidence_assessment import get_assessment_engine_status

        empty = _stats_row(
            compared_verdicts=0, failed_verdicts=0, objectives_compared=0, objectives_agreed=0,
            confident_objectives=0, confident_agreed=0, status_agreed=0, status_compared=0,
            mean_latency_ms=None, total_cost_cents=None, last_compared_at=None,
        )
        db = _db({}, empty)
        with patch("api.evidence_assessment.integration_enabled", return_value=False):
            out = await get_assessment_engine_status(org_id=org_id, membership=membership, db=db)
        assert out.engine == "llm"
        assert out.typesafe_key_configured is False
        s = out.shadow_stats
        assert s.compared_verdicts == 0
        assert s.agreement_rate is None
        assert s.confident_agreement_rate is None
        assert s.status_agreement_rate is None
        assert s.last_compared_at is None

    @pytest.mark.asyncio
    async def test_unknown_org_is_404(self, org_id, membership):
        from api.evidence_assessment import get_assessment_engine_status

        with pytest.raises(HTTPException) as info:
            await get_assessment_engine_status(org_id=org_id, membership=membership, db=_db(None))
        assert info.value.status_code == 404

    def test_response_never_carries_the_key(self):
        from schemas import AssessmentEngineStatusResponse

        fields = set(AssessmentEngineStatusResponse.model_fields)
        assert "typesafe_key_configured" in fields
        assert not any("key" in f and f != "typesafe_key_configured" for f in fields)


# ---------------------------------------------------------------------------
# GET .../assessment/shadow
# ---------------------------------------------------------------------------

def _shadow_row(org_id, file_id, **overrides):
    fields = {
        "id": uuid4(), "assessment_id": uuid4(), "version_id": uuid4(),
        "evidence_file_id": file_id, "organization_id": org_id, "evidence_id": "ERL-001",
        "engine": "jev", "model_id": "jev-1.13.0", "question_set_version": ja.QUESTION_SET_VERSION,
        "status": "partial", "relevance_score": Decimal("75.00"),
        "ao_findings": [
            {"ao_id": "AO0001", "suggested_designation": "appears_satisfied", "confidence": 0.95,
             "probabilities": {"appears_satisfied": 0.95}},
            {"ao_id": "AO0002", "suggested_designation": "gap_identified", "confidence": 0.6,
             "probabilities": {"gap_identified": 0.6}},
        ],
        "gap_count": 1, "cannot_assess_count": 0, "low_confidence_count": 1,
        "confidence_cutoff": Decimal("0.850"),
        "comparison": {
            "compared": 2, "agreed": 1, "agreement_rate": 0.5,
            "confident_total": 1, "confident_agreed": 1, "confident_agreement_rate": 1.0,
            "llm_status": "sufficient", "jev_status": "partial", "status_agrees": False,
            "disagreements": [{"ao_id": "AO0002", "llm": "appears_satisfied", "jev": "gap_identified", "confidence": 0.6}],
        },
        "state_truncated": False, "input_token_count": 800, "output_token_count": 0,
        "cost_cents": Decimal("0.0034"), "processing_time_ms": 95, "error": None,
        "created_at": datetime(2026, 9, 29, 6, 0, 0),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class TestShadowVerdictRead:
    @pytest.mark.asyncio
    async def test_latest_row_is_returned_in_the_contract_shape(self, org_id, membership):
        from api.evidence_assessment import get_shadow_verdict

        file_id = uuid4()
        row = _shadow_row(org_id, file_id)
        out = await get_shadow_verdict(
            org_id=org_id, evidence_id="ERL-001", file_id=file_id, membership=membership, db=_db(row),
        )
        assert out.status == "partial"
        assert out.relevance_score == 75.0
        assert out.confidence_cutoff == 0.85
        assert [f.ao_id for f in out.ao_findings] == ["AO0001", "AO0002"]
        assert out.ao_findings[1].confidence == 0.6
        assert out.comparison.compared == 2
        assert out.comparison.status_agrees is False
        assert out.comparison.disagreements[0].jev == "gap_identified"
        assert out.cost_cents == pytest.approx(0.0034)
        assert out.created_at.isoformat() == "2026-09-29T06:00:00+00:00"
        assert out.error is None

    @pytest.mark.asyncio
    async def test_failed_shadow_is_an_error_row_not_a_404(self, org_id, membership):
        from api.evidence_assessment import get_shadow_verdict

        file_id = uuid4()
        row = _shadow_row(
            org_id, file_id, status=None, ao_findings=[], comparison=None,
            error="JevCallError: Jev answered HTTP 500", relevance_score=None, confidence_cutoff=None,
        )
        out = await get_shadow_verdict(
            org_id=org_id, evidence_id="ERL-001", file_id=file_id, membership=membership, db=_db(row),
        )
        assert out.status is None
        assert out.comparison is None
        assert out.ao_findings == []
        assert "HTTP 500" in out.error

    @pytest.mark.asyncio
    async def test_no_shadow_is_404(self, org_id, membership):
        from api.evidence_assessment import get_shadow_verdict

        with pytest.raises(HTTPException) as info:
            await get_shadow_verdict(
                org_id=org_id, evidence_id="ERL-001", file_id=uuid4(), membership=membership, db=_db(None),
            )
        assert info.value.status_code == 404

    def test_from_row_tolerates_malformed_jsonb(self, org_id):
        row = _shadow_row(org_id, uuid4(), ao_findings=["not a dict"], comparison="garbage")
        out = ShadowVerdictResponse.from_row(row)
        assert out.ao_findings == []
        assert out.comparison is None


# ---------------------------------------------------------------------------
# The trigger endpoint's cache gate follows the chosen engine
# ---------------------------------------------------------------------------

def _cached_llm_assessment(file_sha):
    a = MagicMock()
    a.status = "sufficient"
    a.prompt_hash = "p" * 64
    a.control_context_hash = "c" * 64
    a.assessed_file_sha256 = file_sha
    a.prompt_version = PROMPT_VERSION
    return a


class TestTriggerGateFollowsTheEngine:
    """The endpoint's gate and the worker's gate must agree, or an engine
    switch answers with the other engine's verdict here and never queues."""

    async def _trigger(self, org_id, membership, engine):
        from api.evidence_assessment import trigger_assessment
        from schemas import EvidenceAssessmentRequest

        file_sha = "f" * 64
        evidence_file = MagicMock()
        evidence_file.computed_sha256 = file_sha
        evidence_file.sha256_hash = file_sha
        assessment = _cached_llm_assessment(file_sha)
        context = MagicMock()
        context.context_hash = "c" * 64
        db = _db(evidence_file, assessment, {ja.ENGINE_SETTING_KEY: engine})

        with patch("api.evidence_assessment.assemble_control_context", AsyncMock(return_value=context)), \
             patch("api.evidence_assessment.assess_evidence_task") as task, \
             patch("api.evidence_assessment.EvidenceAssessmentResponse.from_assessment", return_value="body"):
            await trigger_assessment(
                org_id=org_id, evidence_id="ERL-001", file_id=uuid4(),
                body=EvidenceAssessmentRequest(), response=MagicMock(),
                membership=membership, db=db,
            )
        return task.delay, assessment

    @pytest.mark.asyncio
    async def test_llm_engine_reuses_a_valid_llm_verdict(self, org_id, membership):
        delay, assessment = await self._trigger(org_id, membership, "llm")
        delay.assert_not_called()
        assert assessment.status == "sufficient"

    @pytest.mark.asyncio
    async def test_jev_engine_does_not_reuse_an_llm_verdict(self, org_id, membership):
        # Same stored verdict, organisation now on Jev: the gate misses and the
        # file is queued for a fresh assessment on the chosen engine.
        delay, assessment = await self._trigger(org_id, membership, "jev")
        delay.assert_called_once()
        assert assessment.status == "pending"
