"""Human confirmation of an AI evidence assessment (#881 WS3, seam 3).

The product rule these tests exist to hold: an AI verdict is a *suggestion*
until a person says otherwise. Everything here is about the moment that
changes — who is allowed to say it, what they have to say when they disagree,
what the platform records, and what it refuses to record twice.

Endpoint functions are called directly with a mocked AsyncSession, the idiom
used by ``test_evidence_files_api.py``. No database.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.assurance_policy import AssurancePolicy  # noqa: E402


EVIDENCE_KEY = "evidence_one"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def file_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = user_id
    m.user.db_id = str(user_id)
    m.user.email = "reviewer@example.com"
    m.role = "editor"
    return m


@pytest.fixture
def request_obj():
    """A Request stand-in — only the audit helpers read it, and they are patched."""
    return MagicMock()


AO_FINDINGS = [
    {
        "ao_id": "objective_alpha",
        "suggested_designation": "appears_satisfied",
        "rationale": "The policy states the requirement.",
        "suggestion": "",
    },
    {
        "ao_id": "objective_bravo",
        "suggested_designation": "gap_identified",
        "rationale": "No review cadence is stated.",
        "suggestion": "Add an annual review clause.",
    },
    {
        "ao_id": "objective_charlie",
        "suggested_designation": "appears_satisfied",
        "rationale": "Ownership is named.",
        "suggestion": "",
    },
]


def make_assessment(org_id, file_id, *, status="partial", version_id=None, review_decision=None):
    a = MagicMock()
    a.id = uuid4()
    a.organization_id = org_id
    a.evidence_file_id = file_id
    a.evidence_id = EVIDENCE_KEY
    a.status = status
    a.relevance_score = 72.0
    a.findings = []
    a.summary = "Covers most of it."
    a.ao_findings = [dict(f) for f in AO_FINDINGS]
    a.gap_count = 1
    a.cannot_assess_count = 0
    a.current_version_id = version_id or uuid4()
    a.version_number = 2
    a.review_decision = review_decision
    a.reviewed_by_user_id = None
    a.reviewed_at = None
    a.unassessable_reason = None
    a.assessment_source = "on_demand"
    a.created_at = datetime.utcnow()
    a.assessed_at = datetime.utcnow()
    a.truncated = False
    # Declared explicitly because EvidenceAssessmentResponse validates the whole
    # row: an attribute left to MagicMock's autocreation arrives as a MagicMock
    # and fails validation for reasons that have nothing to do with the test.
    a.evidence_effective_date = None
    a.effective_date_source = None
    a.age_exceeds_12_months = None
    a.requested_by_user_id = None
    a.model_id = "claude-sonnet-4-6"
    a.prompt_hash = "0" * 64
    a.prompt_version = "2.0.0"
    a.control_context_hash = "1" * 64
    a.framework_version = "2026.1"
    a.input_token_count = 1200
    a.output_token_count = 900
    a.cost_cents = None
    a.processing_time_ms = 4100
    return a


def make_version(assessment, *, review_decision=None):
    v = MagicMock()
    v.id = assessment.current_version_id
    v.assessment_id = assessment.id
    v.organization_id = assessment.organization_id
    v.evidence_file_id = assessment.evidence_file_id
    v.evidence_id = assessment.evidence_id
    v.version_number = assessment.version_number
    v.schema_version = 2
    v.status = assessment.status
    v.ao_findings = [dict(f) for f in AO_FINDINGS]
    v.gap_count = assessment.gap_count
    v.cannot_assess_count = assessment.cannot_assess_count
    v.review_decision = review_decision
    v.review_reason = None
    v.reviewed_by_user_id = None
    v.reviewed_at = None
    v.ao_overrides = None
    v.created_at = datetime.utcnow()
    return v


def make_db(*results):
    """An AsyncSession whose successive ``execute`` calls return *results*.

    Each entry is the value ``scalar_one_or_none()`` should hand back.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    execute_results = []
    for value in results:
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        r.scalar = MagicMock(return_value=value)
        execute_results.append(r)
    db.execute = AsyncMock(side_effect=execute_results)
    return db


OPEN_POLICY = AssurancePolicy(
    require_evidence_attestation=False,
    require_reviewer_independence=False,
)
INDEPENDENT_POLICY = AssurancePolicy(
    require_evidence_attestation=False,
    require_reviewer_independence=True,
)


def review_body(**kwargs):
    from schemas import EvidenceAssessmentReviewRequest

    return EvidenceAssessmentReviewRequest(**kwargs)


async def call_review(db, membership, org_id, file_id, body, request_obj):
    from api.evidence_assessment import review_assessment

    return await review_assessment(
        org_id=org_id,
        evidence_id=EVIDENCE_KEY,
        file_id=file_id,
        body=body,
        request=request_obj,
        membership=membership,
        db=db,
    )


# ---------------------------------------------------------------------------
# Confirm
# ---------------------------------------------------------------------------

class TestConfirm:
    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_confirm_records_reviewer_on_row_and_version(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj, user_id
    ):
        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id)
        version = make_version(assessment)
        db = make_db(assessment, version)

        result = await call_review(
            db, membership, org_id, file_id,
            review_body(decision="confirmed"), request_obj,
        )

        assert version.review_decision == "confirmed"
        assert version.reviewed_by_user_id == user_id
        assert version.reviewed_at is not None
        # Denormalized onto the row the queue and the KSI SQL read.
        assert assessment.review_decision == "confirmed"
        assert assessment.reviewed_by_user_id == user_id
        assert result.review_decision == "confirmed"

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_confirm_does_not_change_the_verdict(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        """Confirming means "the AI was right" — it must not move any number."""
        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id, status="partial")
        version = make_version(assessment)
        db = make_db(assessment, version)

        await call_review(
            db, membership, org_id, file_id,
            review_body(decision="confirmed"), request_obj,
        )

        assert assessment.status == "partial"
        assert assessment.gap_count == 1
        assert assessment.ao_findings == AO_FINDINGS
        assert version.ao_overrides is None

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_audit_entry_written(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id)
        db = make_db(assessment, make_version(assessment))

        await call_review(
            db, membership, org_id, file_id,
            review_body(decision="confirmed"), request_obj,
        )

        assert mock_audit.await_count == 1
        kwargs = mock_audit.await_args.kwargs
        assert kwargs["entity_type"] == "evidence_assessment"
        assert kwargs["entity_id"] == assessment.id


# ---------------------------------------------------------------------------
# Override
# ---------------------------------------------------------------------------

class TestOverride:
    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_override_recomputes_status_and_counts(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        """Clearing the only gap turns partial into sufficient."""
        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id, status="partial")
        version = make_version(assessment)
        db = make_db(assessment, version)

        await call_review(
            db, membership, org_id, file_id,
            review_body(
                decision="overridden",
                reason="The cadence is in the appendix the model did not reach.",
                ao_overrides=[{
                    "ao_id": "objective_bravo",
                    "human_designation": "appears_satisfied",
                    "note": "Appendix B, clause 4.",
                }],
            ),
            request_obj,
        )

        assert assessment.status == "sufficient"
        assert assessment.gap_count == 0
        assert assessment.cannot_assess_count == 0

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_override_leaves_the_version_row_holding_the_ai_original(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        """The frozen row is what the AI said. Disagreeing with it is not editing it."""
        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id, status="partial")
        version = make_version(assessment)
        db = make_db(assessment, version)

        await call_review(
            db, membership, org_id, file_id,
            review_body(
                decision="overridden",
                reason="Appendix covers it.",
                ao_overrides=[{"ao_id": "objective_bravo", "human_designation": "appears_satisfied"}],
            ),
            request_obj,
        )

        assert version.status == "partial"
        assert version.gap_count == 1
        assert version.ao_findings == AO_FINDINGS
        # ai_designation is snapshotted server-side, never taken from the client.
        assert version.ao_overrides == [{
            "ao_id": "objective_bravo",
            "ai_designation": "gap_identified",
            "human_designation": "appears_satisfied",
            "note": "",
        }]

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_override_applies_human_designations_to_the_current_row(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id, status="partial")
        db = make_db(assessment, make_version(assessment))

        await call_review(
            db, membership, org_id, file_id,
            review_body(
                decision="overridden",
                reason="Two of these do not apply to a single-tenant deployment.",
                ao_overrides=[
                    {"ao_id": "objective_alpha", "human_designation": "not_applicable"},
                    {"ao_id": "objective_charlie", "human_designation": "not_applicable"},
                ],
            ),
            request_obj,
        )

        by_id = {f["ao_id"]: f for f in assessment.ao_findings}
        assert by_id["objective_alpha"]["suggested_designation"] == "not_applicable"
        assert by_id["objective_charlie"]["suggested_designation"] == "not_applicable"
        # Untouched objectives keep the AI's answer.
        assert by_id["objective_bravo"]["suggested_designation"] == "gap_identified"
        # Only a gap left, nothing satisfied → insufficient.
        assert assessment.status == "insufficient"

    def test_override_without_a_reason_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            review_body(
                decision="overridden",
                ao_overrides=[{"ao_id": "objective_bravo", "human_designation": "appears_satisfied"}],
            )

    def test_override_with_a_blank_reason_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            review_body(
                decision="overridden",
                reason="   ",
                ao_overrides=[{"ao_id": "objective_bravo", "human_designation": "appears_satisfied"}],
            )

    def test_override_without_any_ao_overrides_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            review_body(decision="overridden", reason="I disagree.")

    def test_bad_human_designation_is_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            review_body(
                decision="overridden",
                reason="x",
                ao_overrides=[{"ao_id": "objective_bravo", "human_designation": "compliant"}],
            )

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_unknown_ao_id_is_422(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        from fastapi import HTTPException

        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id)
        db = make_db(assessment, make_version(assessment))

        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(
                    decision="overridden",
                    reason="Wrong objective.",
                    ao_overrides=[{"ao_id": "not_an_objective", "human_designation": "appears_satisfied"}],
                ),
                request_obj,
            )
        assert exc.value.status_code == 422
        assert "not_an_objective" in str(exc.value.detail)

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_duplicate_ao_id_is_422(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        from fastapi import HTTPException

        mock_policy.return_value = OPEN_POLICY
        assessment = make_assessment(org_id, file_id)
        db = make_db(assessment, make_version(assessment))

        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(
                    decision="overridden",
                    reason="Twice.",
                    ao_overrides=[
                        {"ao_id": "objective_bravo", "human_designation": "appears_satisfied"},
                        {"ao_id": "objective_bravo", "human_designation": "not_applicable"},
                    ],
                ),
                request_obj,
            )
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

class TestRefusals:
    @pytest.mark.asyncio
    async def test_missing_assessment_is_404(self, membership, org_id, file_id, request_obj):
        from fastapi import HTTPException

        db = make_db(None)
        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(decision="confirmed"), request_obj,
            )
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "processing", "error"])
    async def test_non_terminal_status_is_409(
        self, status, membership, org_id, file_id, request_obj
    ):
        from fastapi import HTTPException

        assessment = make_assessment(org_id, file_id, status=status)
        db = make_db(assessment)
        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(decision="confirmed"), request_obj,
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_second_decision_on_the_same_version_is_409(
        self, membership, org_id, file_id, request_obj
    ):
        from fastapi import HTTPException

        assessment = make_assessment(org_id, file_id, review_decision="confirmed")
        version = make_version(assessment, review_decision="confirmed")
        db = make_db(assessment, version)

        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(decision="confirmed"), request_obj,
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_terminal_status_with_no_frozen_version_is_409(
        self, membership, org_id, file_id, request_obj
    ):
        from fastapi import HTTPException

        assessment = make_assessment(org_id, file_id)
        assessment.current_version_id = None
        db = make_db(assessment)

        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(decision="confirmed"), request_obj,
            )
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_sole_uploader_cannot_confirm_when_independence_required(
        self, mock_policy, membership, org_id, file_id, request_obj, user_id
    ):
        from fastapi import HTTPException

        mock_policy.return_value = INDEPENDENT_POLICY
        assessment = make_assessment(org_id, file_id)
        version = make_version(assessment)
        # Third execute resolves the uploader — the reviewer, here.
        db = make_db(assessment, version, user_id)

        with pytest.raises(HTTPException) as exc:
            await call_review(
                db, membership, org_id, file_id,
                review_body(decision="confirmed"), request_obj,
            )
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    @patch("api.evidence_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_someone_else_uploaded_so_review_is_allowed(
        self, mock_policy, mock_audit, membership, org_id, file_id, request_obj
    ):
        mock_policy.return_value = INDEPENDENT_POLICY
        assessment = make_assessment(org_id, file_id)
        version = make_version(assessment)
        db = make_db(assessment, version, uuid4())

        await call_review(
            db, membership, org_id, file_id,
            review_body(decision="confirmed"), request_obj,
        )
        assert assessment.review_decision == "confirmed"


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

class TestReviewQueueQuery:
    """The queue query is built by a pure function so its shape is testable."""

    def test_awaiting_filter_selects_undecided_terminal_rows(self, org_id):
        from api.evidence_assessment import build_review_queue_query

        sql = str(build_review_queue_query(org_id, "awaiting", 50, 0))
        assert "review_decision IS NULL" in sql
        assert "is_deleted" in sql

    def test_reviewed_filter_selects_decided_rows(self, org_id):
        from api.evidence_assessment import build_review_queue_query

        sql = str(build_review_queue_query(org_id, "reviewed", 50, 0))
        assert "review_decision IS NOT NULL" in sql

    def test_all_filter_applies_no_decision_predicate(self, org_id):
        from api.evidence_assessment import build_review_queue_query

        sql = str(build_review_queue_query(org_id, "all", 50, 0))
        assert "review_decision IS NULL" not in sql
        assert "review_decision IS NOT NULL" not in sql

    def test_severity_ordering(self, org_id):
        """Most gaps first, then most unreadable objectives, then least relevant."""
        from api.evidence_assessment import build_review_queue_query

        sql = str(build_review_queue_query(org_id, "awaiting", 50, 0))
        order = sql.split("ORDER BY")[1]
        assert order.index("gap_count DESC") < order.index("cannot_assess_count DESC")
        assert order.index("cannot_assess_count DESC") < order.index("relevance_score ASC")
        assert "NULLS LAST" in order
        assert order.index("relevance_score ASC") < order.index("assessed_at ASC")

    def test_scoped_to_one_organization(self, org_id):
        from api.evidence_assessment import build_review_queue_query

        sql = str(build_review_queue_query(org_id, "awaiting", 50, 0))
        assert "evidence_assessments.organization_id = " in sql

    def test_non_terminal_rows_never_enter_the_queue(self, org_id):
        from api.evidence_assessment import build_review_queue_query, REVIEWABLE_STATUSES

        sql = str(build_review_queue_query(org_id, "awaiting", 50, 0))
        assert "status IN " in sql
        assert set(REVIEWABLE_STATUSES) == {
            "sufficient", "partial", "insufficient", "unassessable",
        }


class TestReviewQueueEndpoint:
    @pytest.mark.asyncio
    async def test_returns_items_and_total(self, membership, org_id):
        from api.evidence_assessment import get_review_queue

        row = MagicMock()
        row.file_id = uuid4()
        row.evidence_id = EVIDENCE_KEY
        row.filename = "policy.pdf"
        row.uploaded_at = datetime.utcnow()
        row.uploaded_by_user_id = uuid4()
        row.status = "partial"
        row.relevance_score = 61.0
        row.gap_count = 3
        row.cannot_assess_count = 1
        row.version_number = 1
        row.assessed_at = datetime.utcnow()
        row.review_decision = None
        row.reviewed_at = None

        db = AsyncMock()
        rows_result = MagicMock()
        rows_result.all = MagicMock(return_value=[row])
        count_result = MagicMock()
        count_result.scalar = MagicMock(return_value=7)
        db.execute = AsyncMock(side_effect=[rows_result, count_result])

        result = await get_review_queue(
            org_id=org_id, status="awaiting", limit=50, offset=0,
            membership=membership, db=db,
        )

        assert result.total == 7
        assert len(result.items) == 1
        assert result.items[0].gap_count == 3
        assert result.items[0].filename == "policy.pdf"

    @pytest.mark.asyncio
    async def test_rejects_an_unknown_status_filter(self, membership, org_id):
        from fastapi import HTTPException
        from api.evidence_assessment import get_review_queue

        with pytest.raises(HTTPException) as exc:
            await get_review_queue(
                org_id=org_id, status="everything", limit=50, offset=0,
                membership=membership, db=AsyncMock(),
            )
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Version history
# ---------------------------------------------------------------------------

def _fake_version_row(number: int):
    v = MagicMock()
    v.id = uuid4()
    v.version_number = number
    v.schema_version = 2
    v.status = "partial"
    v.relevance_score = 50.0
    v.summary = "s"
    v.findings = []
    v.ao_findings = []
    v.gap_count = 0
    v.cannot_assess_count = 0
    v.evidence_effective_date = None
    v.effective_date_source = None
    v.age_exceeds_12_months = None
    v.truncated = False
    v.unassessable_reason = None
    v.model_id = "claude-sonnet-4-6"
    v.prompt_version = "2.0.0"
    v.assessed_at = datetime.utcnow()
    v.created_at = datetime.utcnow()
    v.review_decision = None
    v.review_reason = None
    v.reviewed_by_user_id = None
    v.reviewed_at = None
    v.ao_overrides = None
    return v


def _scalars_db(rows):
    db = AsyncMock()
    result_mock = MagicMock()
    scalars = MagicMock()
    scalars.all = MagicMock(return_value=rows)
    result_mock.scalars = MagicMock(return_value=scalars)
    db.execute = AsyncMock(return_value=result_mock)
    return db


class TestVersionHistory:
    @pytest.mark.asyncio
    async def test_returns_newest_first(self, membership, org_id, file_id):
        from api.evidence_assessment import list_assessment_versions

        db = _scalars_db([_fake_version_row(2), _fake_version_row(1)])

        out = await list_assessment_versions(
            org_id=org_id, evidence_id=EVIDENCE_KEY, file_id=file_id,
            membership=membership, db=db,
        )

        assert [v.version_number for v in out] == [2, 1]
        sql = str(db.execute.await_args.args[0])
        assert "version_number DESC" in sql

    @pytest.mark.asyncio
    async def test_scoped_to_org_and_file(self, membership, org_id, file_id):
        from api.evidence_assessment import list_assessment_versions

        db = _scalars_db([])
        await list_assessment_versions(
            org_id=org_id, evidence_id=EVIDENCE_KEY, file_id=file_id,
            membership=membership, db=db,
        )
        sql = str(db.execute.await_args.args[0])
        assert "organization_id" in sql
        assert "evidence_file_id" in sql


# ---------------------------------------------------------------------------
# Summary buckets
# ---------------------------------------------------------------------------

class TestSummaryBuckets:
    @pytest.mark.asyncio
    async def test_unassessable_and_awaiting_review_are_reported(self, membership, org_id):
        """Without an unassessable bucket the dashboard totals do not add up."""
        from api.evidence_assessment import get_assessment_summary

        row = MagicMock()
        row.total = 10
        row.sufficient = 3
        row.partial = 2
        row.insufficient = 1
        row.unassessable = 2
        row.pending = 1
        row.error = 1
        row.awaiting_review = 5
        row.avg_score = 55.0
        row.total_cost = None

        db = AsyncMock()
        agg_result = MagicMock()
        agg_result.one = MagicMock(return_value=row)
        unassessed_result = MagicMock()
        unassessed_result.scalar = MagicMock(return_value=4)
        db.execute = AsyncMock(side_effect=[agg_result, unassessed_result])

        summary = await get_assessment_summary(org_id=org_id, membership=membership, db=db)

        assert summary.unassessable_count == 2
        assert summary.awaiting_review_count == 5
        buckets = (
            summary.sufficient_count + summary.partial_count + summary.insufficient_count
            + summary.unassessable_count + summary.pending_count + summary.error_count
        )
        assert buckets == summary.total_assessed
