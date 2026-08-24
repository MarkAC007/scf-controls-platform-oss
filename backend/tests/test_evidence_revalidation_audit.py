"""
Tests for the revalidation audit row (#57).

`POST .../files/{id}/validate` re-runs validation as an **upsert**: the previous
verdict is gone the moment it commits. Without an audit row, a reviewer could
re-run validation until a file came out clean and nothing would record that it
had ever come out otherwise, or that anyone had asked.
"""
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4, UUID

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


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
    m.user.db_id = str(user_id)
    m.role = "editor"
    return m


@pytest.fixture
def request_obj():
    req = MagicMock()
    req.headers = {}
    req.client = None
    return req


def _db(evidence_file, previous_status):
    db = AsyncMock()

    async def execute(stmt, *a, **kw):
        if "evidence_validation_results" in str(stmt):
            return MagicMock(**{"scalar_one_or_none.return_value": previous_status})
        return MagicMock(**{"scalar_one_or_none.return_value": evidence_file})

    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _file(org_id):
    f = MagicMock()
    f.id = uuid4()
    f.organization_id = org_id
    f.evidence_id = "ERL-001"
    f.is_deleted = False
    return f


class TestRevalidationAudit:
    @pytest.mark.asyncio
    @patch("api.evidence_validation.create_audit_entry", new_callable=AsyncMock)
    @patch("api.evidence_validation.run_validation", new_callable=AsyncMock)
    async def test_records_the_verdict_that_was_overwritten(
        self, mock_run, mock_audit, membership, org_id, user_id, request_obj
    ):
        from api.evidence_validation import revalidate_file

        evidence_file = _file(org_id)
        mock_run.return_value = MagicMock(status="valid")

        await revalidate_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=evidence_file.id,
            request=request_obj,
            membership=membership,
            db=_db(evidence_file, previous_status="invalid"),
        )

        mock_audit.assert_awaited_once()
        kwargs = mock_audit.await_args.kwargs
        assert kwargs["entity_type"] == "evidence_file"
        assert kwargs["field_name"] == "validation_status"
        assert kwargs["old_value"] == "invalid"
        assert kwargs["new_value"] == "valid"
        assert kwargs["changed_by_user_id"] == UUID(membership.user.db_id)

    @pytest.mark.asyncio
    @patch("api.evidence_validation.create_audit_entry", new_callable=AsyncMock)
    @patch("api.evidence_validation.run_validation", new_callable=AsyncMock)
    async def test_a_first_validation_records_a_null_predecessor(
        self, mock_run, mock_audit, membership, org_id, request_obj
    ):
        from api.evidence_validation import revalidate_file

        evidence_file = _file(org_id)
        mock_run.return_value = MagicMock(status="warning")

        await revalidate_file(
            org_id=org_id,
            evidence_id="ERL-001",
            file_id=evidence_file.id,
            request=request_obj,
            membership=membership,
            db=_db(evidence_file, previous_status=None),
        )

        assert mock_audit.await_args.kwargs["old_value"] is None

    @pytest.mark.asyncio
    @patch("api.evidence_validation.create_audit_entry", new_callable=AsyncMock)
    @patch("api.evidence_validation.run_validation", new_callable=AsyncMock)
    async def test_the_previous_status_is_read_without_touching_a_lazy_relationship(
        self, mock_run, mock_audit, membership, org_id, request_obj
    ):
        """Touching `evidence_file.validation_result` on an AsyncSession raises
        MissingGreenlet; the handler must query for it instead."""
        from api.evidence_validation import revalidate_file

        evidence_file = _file(org_id)
        type(evidence_file).validation_result = property(
            lambda self: (_ for _ in ()).throw(AssertionError("lazy load attempted"))
        )
        mock_run.return_value = MagicMock(status="valid")

        try:
            await revalidate_file(
                org_id=org_id,
                evidence_id="ERL-001",
                file_id=evidence_file.id,
                request=request_obj,
                membership=membership,
                db=_db(evidence_file, previous_status="partial"),
            )
        finally:
            del type(evidence_file).validation_result

        assert mock_audit.await_args.kwargs["old_value"] == "partial"
