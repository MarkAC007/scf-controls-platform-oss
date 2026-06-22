"""
Unit tests for the per-window review API endpoint (M4 PR 2, #574).

Covers ``PUT /organizations/{org_id}/window-assessments/{ewa_id}/review``:
- 200 happy path for each of the 4 valid review_status values
- 422 on invalid review_status
- 404 on missing EWA
- 404 on cross-org EWA (handled by the org filter in the SELECT)
- audit log row written on update
- idempotency: same body twice produces same final state

Mock-based — no real DB or env. Mirrors the test pattern in
``test_evidence_files_api.py``.
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
def user_id():
    return uuid4()


@pytest.fixture
def ewa_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = user_id
    m.user.db_id = str(user_id)
    m.user.email = "reviewer@example.com"
    m.user.display_name = "Reviewer"
    m.role = "editor"
    return m


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _make_ewa(ewa_id, org_id, *, review_status="not_reviewed"):
    """Build a mock EvidenceWindowAssessment row with all response fields set."""
    ewa = MagicMock()
    ewa.id = ewa_id
    ewa.organization_id = org_id
    ewa.evidence_id = "E-BCM-11"
    ewa.window_start = datetime(2026, 4, 1, 0, 0, 0)
    ewa.window_end = datetime(2026, 5, 1, 0, 0, 0)
    ewa.frequency_used = "monthly"
    ewa.file_ids = []
    ewa.source_coverage = {}
    ewa.artifact_type_coverage = {}
    ewa.expected_artifact_types = []
    ewa.status = "sufficient"
    ewa.relevance_score = None
    ewa.findings = []
    ewa.summary = None
    ewa.model_id = None
    ewa.prompt_hash = None
    ewa.control_context_hash = None
    ewa.framework_version = None
    ewa.window_hash = None
    ewa.input_token_count = None
    ewa.output_token_count = None
    ewa.cost_cents = None
    ewa.processing_time_ms = None
    ewa.assessment_source = "on_demand"
    ewa.requested_by_user_id = None
    ewa.assessed_at = datetime.utcnow()
    ewa.created_at = datetime.utcnow()
    ewa.review_status = review_status
    ewa.reviewed_by_user_id = None
    ewa.reviewed_at = None
    ewa.review_notes = None
    return ewa


# ---------------------------------------------------------------------------
# Happy path — each valid review_status (ISC-11)
# ---------------------------------------------------------------------------

class TestReviewWindowAssessmentHappyPath:

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        ["approved", "rejected", "needs_revision", "not_reviewed"],
    )
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    async def test_each_valid_status_persists_and_returns(
        self, mock_audit, status, membership, mock_db, org_id, ewa_id, user_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = _make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(
            review_status=status, review_notes="looks good"
        )

        result = await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        assert ewa.review_status == status
        assert ewa.reviewed_by_user_id == user_id
        assert ewa.reviewed_at is not None
        assert ewa.review_notes == "looks good"
        # Returned object is the refreshed EWA (FastAPI serializes via response_model).
        assert result is ewa
        mock_audit.assert_called_once()
        mock_db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Validation failures
# ---------------------------------------------------------------------------

class TestReviewWindowAssessmentValidation:

    @pytest.mark.asyncio
    async def test_invalid_status_returns_422(
        self, membership, mock_db, org_id, ewa_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        body = WindowAssessmentReviewRequest(review_status="bogus")

        with pytest.raises(Exception) as exc_info:
            await review_window_assessment(
                org_id=org_id,
                ewa_id=ewa_id,
                body=body,
                request=MagicMock(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 422
        # No DB read or commit on validation failure.
        mock_db.execute.assert_not_called()
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_ewa_returns_404(
        self, membership, mock_db, org_id, ewa_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(review_status="approved")

        with pytest.raises(Exception) as exc_info:
            await review_window_assessment(
                org_id=org_id,
                ewa_id=ewa_id,
                body=body,
                request=MagicMock(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 404
        mock_db.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_cross_org_ewa_returns_404(
        self, membership, mock_db, ewa_id,
    ):
        """An EWA belonging to a different org is filtered out by the
        WHERE clause and surfaces as 404 — matches ISC-14."""
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        wrong_org = uuid4()
        # The SELECT filters on (id == ewa_id AND organization_id == wrong_org)
        # — the row owned by another org will not be returned.
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(review_status="approved")

        with pytest.raises(Exception) as exc_info:
            await review_window_assessment(
                org_id=wrong_org,
                ewa_id=ewa_id,
                body=body,
                request=MagicMock(),
                membership=membership,
                db=mock_db,
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Audit log integration (ISC-15)
# ---------------------------------------------------------------------------

class TestReviewWindowAssessmentAudit:

    @pytest.mark.asyncio
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    async def test_audit_log_called_with_correct_entity(
        self, mock_audit, membership, mock_db, org_id, ewa_id, user_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = _make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(
            review_status="approved", review_notes="signed off"
        )

        await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        assert mock_audit.call_count == 1
        kwargs = mock_audit.call_args.kwargs
        assert kwargs["entity_type"] == "evidence_window_assessment"
        assert kwargs["entity_id"] == ewa_id
        assert kwargs["action"] == "update"
        assert kwargs["organization_id"] == org_id
        assert kwargs["changed_by_user_id"] == user_id
        # Old values had review_status="not_reviewed", new have "approved".
        assert kwargs["old_values"]["review_status"] == "not_reviewed"
        assert kwargs["new_values"]["review_status"] == "approved"
        assert kwargs["new_values"]["review_notes"] == "signed off"

    @pytest.mark.asyncio
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    async def test_idempotent_same_body_twice(
        self, mock_audit, membership, mock_db, org_id, ewa_id,
    ):
        """Calling PUT with the same body twice should result in the same
        final state. Audit log is called both times — second call's diff is
        a no-op (old==new on review_status/notes), which the audit service
        handles by emitting no change rows for unchanged fields (ISC-16).
        """
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = _make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(
            review_status="approved", review_notes="ok"
        )

        # First call.
        await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )
        first_status = ewa.review_status
        first_notes = ewa.review_notes

        # Second call — same body.
        await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        assert ewa.review_status == first_status == "approved"
        assert ewa.review_notes == first_notes == "ok"
        # Audit called twice; the audit service is responsible for diff
        # filtering — the endpoint just hands it old/new pairs.
        assert mock_audit.call_count == 2


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class TestWindowAssessmentReviewRequestSchema:

    def test_review_notes_max_length_2000(self):
        from schemas import WindowAssessmentReviewRequest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            WindowAssessmentReviewRequest(
                review_status="approved",
                review_notes="x" * 2001,
            )

    def test_review_notes_optional(self):
        from schemas import WindowAssessmentReviewRequest

        body = WindowAssessmentReviewRequest(review_status="approved")
        assert body.review_notes is None


# ---------------------------------------------------------------------------
# needs_revision Celery dispatch (M4 PR 3, D2)
# ---------------------------------------------------------------------------


class TestNeedsRevisionDispatch:
    """``review_status="needs_revision"`` MUST dispatch a fresh window assessment
    via Celery (D2). All other review statuses MUST NOT dispatch.
    """

    @pytest.mark.asyncio
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_needs_revision_dispatches_assess_window_task(
        self, mock_task, mock_audit, membership, mock_db, org_id, ewa_id, user_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = _make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(
            review_status="needs_revision",
            review_notes="please re-run",
        )

        await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        # apply_async called exactly once with the correct task kwargs.
        mock_task.apply_async.assert_called_once()
        call_kwargs = mock_task.apply_async.call_args.kwargs.get("kwargs", {})
        assert call_kwargs.get("evidence_id") == ewa.evidence_id
        assert call_kwargs.get("organization_id") == str(org_id)
        assert call_kwargs.get("assessment_source") == "review_revision"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status",
        ["approved", "rejected", "not_reviewed"],
    )
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_non_revision_statuses_do_not_dispatch(
        self, mock_task, mock_audit, status, membership, mock_db, org_id, ewa_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = _make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(review_status=status)

        await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        mock_task.apply_async.assert_not_called()

    @pytest.mark.asyncio
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_dispatch_failure_does_not_roll_back_review(
        self, mock_task, mock_audit, membership, mock_db, org_id, ewa_id,
    ):
        """D2 / risk register: dispatch failure logs but the review write
        still succeeds. The nightly refresh will eventually pick up the
        evidence anyway.
        """
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa = _make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        mock_task.apply_async.side_effect = RuntimeError("broker down")

        body = WindowAssessmentReviewRequest(review_status="needs_revision")

        result = await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        # Review mutation persisted despite dispatch failure.
        assert ewa.review_status == "needs_revision"
        mock_db.commit.assert_awaited_once()
        # Endpoint returned the EWA, not an error.
        assert result is ewa
