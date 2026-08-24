"""
Unit tests for the R4 notification triggers (#missing-notification-types).

Covers:
- evidence_rejected — per-file review endpoint and per-window review endpoint
  fire ``create_evidence_rejected_notifications`` only on ``rejected``.
- control_ready_for_review — scoped-control PATCH fires
  ``create_control_ready_for_review_notifications`` only when
  implementation_status genuinely transitions to ``ready_for_review``.
- Recipient resolution — deduplicates and never includes the acting user.

Mock-based — no real DB or env. Mirrors the test pattern in
``test_evidence_files_api.py`` / ``test_window_review_api.py``.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evidence_mocks import unasserted  # noqa: E402


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


def _make_evidence_file(file_id, org_id, user_id, uploaded_by_user):
    f = MagicMock()
    f.id = file_id
    f.organization_id = org_id
    f.evidence_id = "ERL-001"
    f.filename = "doc.pdf"
    f.s3_key = f"evidence/{org_id}/2026/02/abc_doc.pdf"
    f.content_type = "application/pdf"
    f.file_size_bytes = 1024
    f.sha256_hash = None
    f.classification = "internal"
    f.scan_status = "clean"
    f.scan_details = None
    f.computed_sha256 = None
    f.hash_verification_status = "pending"
    f.hash_verified_at = None
    f.hash_verification_details = None
    f.uploaded_by_user_id = user_id
    f.uploaded_at = datetime.utcnow()
    f.expires_at = None
    f.is_deleted = False
    f.uploaded_by = uploaded_by_user
    f.review_status = "pending"
    f.reviewed_by_user_id = None
    f.reviewed_at = None
    f.review_notes = None
    f.reviewed_by = None
    # Nothing asserted — the state almost every existing file is in, and the
    # only one these notification tests care about.
    unasserted(f)
    return f


# ---------------------------------------------------------------------------
# Trigger (a) — evidence_rejected via per-file review endpoint
# ---------------------------------------------------------------------------

class TestEvidenceFileRejectedTrigger:

    @pytest.mark.asyncio
    @patch("api.evidence_files.create_evidence_rejected_notifications", new_callable=AsyncMock)
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    async def test_rejected_review_creates_notification(
        self, mock_audit, mock_notify, membership, mock_db, org_id, user_id, monkeypatch,
    ):
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "false")
        file_id = uuid4()
        ef = _make_evidence_file(file_id, org_id, user_id, membership.user)

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = ef
        mock_db.execute.return_value = mock_result

        async def mock_refresh(obj, attribute_names=None):
            pass
        mock_db.refresh = mock_refresh

        body = EvidenceFileReviewRequest(
            review_status="rejected", review_notes="incomplete evidence"
        )

        await review_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        assert ef.review_status == "rejected"
        mock_notify.assert_awaited_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["organization_id"] == org_id
        assert kwargs["evidence_id"] == "ERL-001"
        assert kwargs["rejected_by_user_id"] == user_id

    # #787: pin the assurance policy to the default — this test approves a
    # file the reviewer uploaded, which segregation of duties would refuse if
    # the AsyncMock session's truthy MagicMock row were read as a policy.
    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["approved", "needs_revision"])
    @patch("api.evidence_files.get_assurance_policy", new_callable=AsyncMock)
    @patch("api.evidence_files.create_evidence_rejected_notifications", new_callable=AsyncMock)
    @patch("api.evidence_files.log_entity_changes", new_callable=AsyncMock)
    async def test_non_rejected_review_does_not_notify(
        self, mock_audit, mock_notify, mock_policy, status, membership, mock_db,
        org_id, user_id, monkeypatch,
    ):
        from api.evidence_files import review_evidence_file
        from schemas import EvidenceFileReviewRequest
        from services.assurance_policy import DEFAULT_ASSURANCE_POLICY

        mock_policy.return_value = DEFAULT_ASSURANCE_POLICY

        monkeypatch.setenv("ENABLE_PER_WINDOW_REVIEW", "false")
        file_id = uuid4()
        ef = _make_evidence_file(file_id, org_id, user_id, membership.user)

        mock_result = MagicMock()
        mock_result.unique.return_value.scalar_one_or_none.return_value = ef
        mock_db.execute.return_value = mock_result

        async def mock_refresh(obj, attribute_names=None):
            pass
        mock_db.refresh = mock_refresh

        body = EvidenceFileReviewRequest(review_status=status, review_notes=None)

        await review_evidence_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=file_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Trigger (a) — evidence_rejected via per-window review endpoint
# ---------------------------------------------------------------------------

class TestWindowReviewRejectedTrigger:

    def _make_ewa(self, ewa_id, org_id):
        ewa = MagicMock()
        ewa.id = ewa_id
        ewa.organization_id = org_id
        ewa.evidence_id = "EVID0002"
        ewa.review_status = "not_reviewed"
        ewa.reviewed_by_user_id = None
        ewa.reviewed_at = None
        ewa.review_notes = None
        return ewa

    @pytest.mark.asyncio
    @patch("api.evidence_window_assessment.create_evidence_rejected_notifications", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_rejected_window_review_creates_notification(
        self, mock_task, mock_audit, mock_notify, membership, mock_db, org_id, user_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa_id = uuid4()
        ewa = self._make_ewa(ewa_id, org_id)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = ewa
        mock_db.execute.return_value = mock_result

        body = WindowAssessmentReviewRequest(review_status="rejected")

        await review_window_assessment(
            org_id=org_id,
            ewa_id=ewa_id,
            body=body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

        mock_notify.assert_awaited_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["organization_id"] == org_id
        assert kwargs["evidence_id"] == "EVID0002"
        assert kwargs["rejected_by_user_id"] == user_id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["approved", "needs_revision", "not_reviewed"])
    @patch("api.evidence_window_assessment.create_evidence_rejected_notifications", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.log_entity_changes", new_callable=AsyncMock)
    @patch("api.evidence_window_assessment.assess_window_task")
    async def test_non_rejected_window_review_does_not_notify(
        self, mock_task, mock_audit, mock_notify, status, membership, mock_db, org_id,
    ):
        from api.evidence_window_assessment import review_window_assessment
        from schemas import WindowAssessmentReviewRequest

        ewa_id = uuid4()
        ewa = self._make_ewa(ewa_id, org_id)
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

        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Trigger (b) — control_ready_for_review via scoped-control PATCH
# ---------------------------------------------------------------------------

class TestControlReadyForReviewTrigger:

    def _make_control(self, org_id, *, implementation_status="planned"):
        control = MagicMock()
        control.id = uuid4()
        control.organization_id = org_id
        control.implementation_status = implementation_status
        control.completion_date = None
        return control

    async def _patch_control(self, control, update_body, membership, mock_db, org_id):
        from api.scoped_controls import update_scoped_control

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = control
        mock_db.execute.return_value = mock_result

        return await update_scoped_control(
            org_id=org_id,
            scf_id="CTL0001",
            control_update=update_body,
            request=MagicMock(),
            membership=membership,
            db=mock_db,
        )

    @pytest.mark.asyncio
    @patch("api.scoped_controls.create_control_ready_for_review_notifications", new_callable=AsyncMock)
    @patch("api.scoped_controls.log_entity_changes", new_callable=AsyncMock)
    async def test_transition_to_ready_for_review_notifies_admins(
        self, mock_audit, mock_notify, membership, mock_db, org_id, user_id,
    ):
        from schemas import ScopedControlUpdate

        control = self._make_control(org_id, implementation_status="planned")
        body = ScopedControlUpdate(implementation_status="ready_for_review")

        await self._patch_control(control, body, membership, mock_db, org_id)

        mock_notify.assert_awaited_once()
        kwargs = mock_notify.call_args.kwargs
        assert kwargs["organization_id"] == org_id
        assert kwargs["scoped_control_id"] == control.id
        assert kwargs["scf_id"] == "CTL0001"
        assert kwargs["actor_user_id"] == user_id

    @pytest.mark.asyncio
    @patch("api.scoped_controls.create_control_ready_for_review_notifications", new_callable=AsyncMock)
    @patch("api.scoped_controls.log_entity_changes", new_callable=AsyncMock)
    async def test_steady_state_ready_for_review_does_not_renotify(
        self, mock_audit, mock_notify, membership, mock_db, org_id,
    ):
        from schemas import ScopedControlUpdate

        control = self._make_control(org_id, implementation_status="ready_for_review")
        body = ScopedControlUpdate(implementation_status="ready_for_review")

        await self._patch_control(control, body, membership, mock_db, org_id)

        mock_notify.assert_not_called()

    @pytest.mark.asyncio
    @patch("api.scoped_controls.create_control_ready_for_review_notifications", new_callable=AsyncMock)
    @patch("api.scoped_controls.log_entity_changes", new_callable=AsyncMock)
    async def test_other_status_change_does_not_notify(
        self, mock_audit, mock_notify, membership, mock_db, org_id,
    ):
        from schemas import ScopedControlUpdate

        control = self._make_control(org_id, implementation_status="ready_for_review")
        body = ScopedControlUpdate(implementation_status="implemented")

        await self._patch_control(control, body, membership, mock_db, org_id)

        mock_notify.assert_not_called()


# ---------------------------------------------------------------------------
# Recipient resolution — deduplicated, never the acting user
# ---------------------------------------------------------------------------

class TestRecipientResolution:

    @pytest.mark.asyncio
    async def test_admin_ids_deduplicated_and_actor_excluded(self, mock_db, org_id):
        from services.notifications import _get_org_admin_user_ids

        admin_a = uuid4()
        admin_b = uuid4()
        actor = uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [admin_a, admin_b, actor, admin_a]
        mock_db.execute.return_value = mock_result

        result = await _get_org_admin_user_ids(mock_db, org_id, exclude_user_id=actor)

        assert actor not in result
        assert sorted(result, key=str) == sorted([admin_a, admin_b], key=str)

    @pytest.mark.asyncio
    async def test_ready_for_review_skips_when_actor_is_only_admin(self, mock_db, org_id):
        from services.notifications import create_control_ready_for_review_notifications

        actor = uuid4()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [actor]
        mock_db.execute.return_value = mock_result

        created = await create_control_ready_for_review_notifications(
            mock_db,
            organization_id=org_id,
            scoped_control_id=uuid4(),
            scf_id="CTL0001",
            actor_user_id=actor,
        )

        assert created == 0
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_evidence_rejected_skips_when_recipient_is_reviewer(self, mock_db, org_id):
        from services.notifications import create_evidence_rejected_notifications

        reviewer = uuid4()
        tracking = MagicMock()
        tracking.id = uuid4()
        tracking.assigned_user_id = reviewer
        tracking.owner_user_id = uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.first.return_value = tracking
        mock_db.execute.return_value = mock_result

        # #822 phase 4: this now returns the number of notifications written
        # rather than a Notification-or-None, because one event can reach the
        # accountable team's primary and delegate as well as the assignee. The
        # behaviour being pinned is unchanged — the reviewer who rejected the
        # item is not notified about their own rejection, and with nobody else
        # to tell, nothing is written.
        created = await create_evidence_rejected_notifications(
            mock_db,
            organization_id=org_id,
            evidence_id="ERL-001",
            rejected_by_user_id=reviewer,
        )

        assert created == 0
        mock_db.add.assert_not_called()
