"""Endpoint wiring for the review gates (#787, ISC-75..77).

Two rules, two review paths. The per-file path is the one live by default,
so a gate that only landed on the per-window endpoint would be a control
nothing exercises — both are asserted here.

Mock-based, mirroring ``test_window_review_api.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.assurance_policy import AssurancePolicy  # noqa: E402

OPEN = AssurancePolicy()
INDEPENDENT = AssurancePolicy(require_reviewer_independence=True)


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = user_id
    m.user.db_id = str(user_id)
    m.role = "editor"
    return m


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock()
    return db


def _ewa(org_id, *, review_status="not_reviewed", file_ids=None):
    ewa = MagicMock()
    ewa.id = uuid4()
    ewa.organization_id = org_id
    ewa.evidence_id = "E-BCM-11"
    ewa.file_ids = file_ids if file_ids is not None else []
    ewa.review_status = review_status
    ewa.reviewed_by_user_id = None
    ewa.reviewed_at = None
    ewa.review_notes = None
    ewa.assessed_at = datetime.utcnow()
    ewa.created_at = datetime.utcnow()
    return ewa


def _result_for(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    return result


def _scalars_result(values):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = list(values)
    result.scalars.return_value = scalars
    return result


async def _call_window_review(org_id, membership, db, ewa, status, notes="n"):
    from api.evidence_window_assessment import review_window_assessment
    from schemas import WindowAssessmentReviewRequest

    return await review_window_assessment(
        org_id=org_id,
        ewa_id=ewa.id,
        body=WindowAssessmentReviewRequest(review_status=status, review_notes=notes),
        request=MagicMock(),
        membership=membership,
        db=db,
    )


# ---------------------------------------------------------------------------
# Constrained transitions — per-window path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestWindowTransitions:
    async def test_rejected_to_approved_is_409_not_a_silent_flip(
        self, membership, mock_db, org_id
    ):
        ewa = _ewa(org_id, review_status="rejected")
        mock_db.execute.return_value = _result_for(ewa)

        with pytest.raises(HTTPException) as exc:
            await _call_window_review(org_id, membership, mock_db, ewa, "approved")

        assert exc.value.status_code == 409
        assert "needs_revision" in exc.value.detail
        # The refusal must not have half-applied the review.
        assert ewa.review_status == "rejected"
        mock_db.commit.assert_not_awaited()

    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_rejected_to_needs_revision_is_allowed(
        self, _task, _audit, membership, mock_db, org_id
    ):
        ewa = _ewa(org_id, review_status="rejected")
        mock_db.execute.return_value = _result_for(ewa)

        await _call_window_review(org_id, membership, mock_db, ewa, "needs_revision")

        assert ewa.review_status == "needs_revision"

    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    async def test_resending_the_current_status_is_idempotent(
        self, _audit, membership, mock_db, org_id
    ):
        ewa = _ewa(org_id, review_status="approved")
        mock_db.execute.return_value = _result_for(ewa)

        await _call_window_review(org_id, membership, mock_db, ewa, "approved")

        assert ewa.review_status == "approved"


# ---------------------------------------------------------------------------
# Segregation of duties — per-window path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestWindowSegregationOfDuties:
    @patch("api.evidence_window_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_sole_uploader_approving_own_window_is_403(
        self, policy, membership, mock_db, org_id, user_id
    ):
        policy.return_value = INDEPENDENT
        ewa = _ewa(org_id, file_ids=[str(uuid4())])
        mock_db.execute.side_effect = [
            _result_for(ewa),
            _scalars_result([user_id]),
        ]

        with pytest.raises(HTTPException) as exc:
            await _call_window_review(org_id, membership, mock_db, ewa, "approved")

        assert exc.value.status_code == 403
        # ISC-76 — explanatory, not bare.
        assert "require_reviewer_independence" in exc.value.detail
        mock_db.commit.assert_not_awaited()

    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_a_second_contributor_makes_the_review_independent(
        self, policy, _audit, membership, mock_db, org_id, user_id
    ):
        policy.return_value = INDEPENDENT
        ewa = _ewa(org_id, file_ids=[str(uuid4()), str(uuid4())])
        mock_db.execute.side_effect = [
            _result_for(ewa),
            _scalars_result([user_id, uuid4()]),
        ]

        await _call_window_review(org_id, membership, mock_db, ewa, "approved")

        assert ewa.review_status == "approved"

    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    @patch("api.evidence_window_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_sending_your_own_evidence_back_is_not_blocked(
        self, policy, _task, _audit, membership, mock_db, org_id, user_id
    ):
        # Only approval is gated. Blocking a sole uploader from withdrawing
        # their own mistake would leave single-handed teams stuck.
        policy.return_value = INDEPENDENT
        ewa = _ewa(org_id, file_ids=[str(uuid4())])
        # The reviewer IS the sole uploader: if the gate were not confined to
        # approvals, this would 403.
        mock_db.execute.side_effect = [
            _result_for(ewa),
            _scalars_result([user_id]),
        ]

        await _call_window_review(org_id, membership, mock_db, ewa, "needs_revision")

        assert ewa.review_status == "needs_revision"

    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.get_assurance_policy", new_callable=AsyncMock)
    async def test_policy_off_leaves_self_review_working(
        self, policy, _audit, membership, mock_db, org_id, user_id
    ):
        # ISC-70's sibling: nothing changes for an org that has not opted in.
        policy.return_value = OPEN
        ewa = _ewa(org_id, file_ids=[str(uuid4())])
        # Sole uploader again: only the policy being off lets this through.
        mock_db.execute.side_effect = [
            _result_for(ewa),
            _scalars_result([user_id]),
        ]

        await _call_window_review(org_id, membership, mock_db, ewa, "approved")

        assert ewa.review_status == "approved"


# ---------------------------------------------------------------------------
# Per-file path — the one that is live by default
# ---------------------------------------------------------------------------

def _file(org_id, uploader_id, *, review_status="not_reviewed"):
    ef = MagicMock()
    ef.id = uuid4()
    ef.organization_id = org_id
    ef.evidence_id = "E-BCM-11"
    ef.uploaded_by_user_id = uploader_id
    ef.review_status = review_status
    ef.reviewed_by_user_id = None
    ef.reviewed_at = None
    ef.review_notes = None
    return ef


async def _call_file_review(org_id, membership, db, ef, status):
    from api.evidence_files import review_evidence_file
    from schemas import EvidenceFileReviewRequest

    return await review_evidence_file(
        org_id=org_id,
        evidence_id=ef.evidence_id,
        file_id=ef.id,
        body=EvidenceFileReviewRequest(review_status=status, review_notes="n"),
        request=MagicMock(),
        membership=membership,
        db=db,
    )


def _unique_result_for(row):
    result = MagicMock()
    unique = MagicMock()
    unique.scalar_one_or_none.return_value = row
    result.unique.return_value = unique
    return result


@pytest.mark.asyncio
class TestPerFileGates:
    @patch("api.evidence_files.get_assurance_policy", new_callable=AsyncMock)
    async def test_rejected_to_approved_is_409(
        self, policy, membership, mock_db, org_id, user_id
    ):
        policy.return_value = OPEN
        ef = _file(org_id, uuid4(), review_status="rejected")
        mock_db.execute.return_value = _unique_result_for(ef)

        with pytest.raises(HTTPException) as exc:
            await _call_file_review(org_id, membership, mock_db, ef, "approved")

        assert exc.value.status_code == 409
        assert ef.review_status == "rejected"

    @patch("api.evidence_files.get_assurance_policy", new_callable=AsyncMock)
    async def test_uploader_approving_own_file_is_403(
        self, policy, membership, mock_db, org_id, user_id
    ):
        policy.return_value = INDEPENDENT
        ef = _file(org_id, user_id)
        mock_db.execute.return_value = _unique_result_for(ef)

        with pytest.raises(HTTPException) as exc:
            await _call_file_review(org_id, membership, mock_db, ef, "approved")

        assert exc.value.status_code == 403
        assert "Segregation of duties" in exc.value.detail

    # ``_to_response`` builds the full pydantic payload, which a MagicMock
    # row cannot satisfy. These tests are about the gate, not serialisation.
    @patch("api.evidence_files._to_response")
    @patch("api.evidence_files._proxy_download_url")
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_files.get_assurance_policy", new_callable=AsyncMock)
    async def test_someone_elses_upload_can_be_approved(
        self, policy, _audit, _url, _resp, membership, mock_db, org_id
    ):
        policy.return_value = INDEPENDENT
        ef = _file(org_id, uuid4())
        mock_db.execute.return_value = _unique_result_for(ef)

        await _call_file_review(org_id, membership, mock_db, ef, "approved")

        assert ef.review_status == "approved"

    @patch("api.evidence_files._to_response")
    @patch("api.evidence_files._proxy_download_url")
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_files.get_assurance_policy", new_callable=AsyncMock)
    async def test_policy_off_leaves_self_review_working(
        self, policy, _audit, _url, _resp, membership, mock_db, org_id, user_id
    ):
        policy.return_value = OPEN
        ef = _file(org_id, user_id)
        mock_db.execute.return_value = _unique_result_for(ef)

        await _call_file_review(org_id, membership, mock_db, ef, "approved")

        assert ef.review_status == "approved"
