"""Organisation deletion: one service, two routes, one confirmation contract.

Pinned:

- the request schema refuses a delete that does not acknowledge data loss,
- the service refuses a name that does not match, refuses while a storage copy
  is in flight, deletes every stored object best-effort (per-file store
  resolution, soft-deleted rows included), records the platform audit event,
  removes the evidence_files rows before the organisation, and never commits,
- both routes refuse API-key callers, translate the service's refusals into
  400/409, and the consultant route additionally requires an ACTIVE
  relationship and the admin gate.

Mock-based, no database.
"""
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401 — registers mappers referenced by models
from schemas import OrganizationDeleteRequest  # noqa: E402
from services import organization_deletion as od  # noqa: E402


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows or [])

    def scalar_one_or_none(self):
        return self._rows


class FakeSession:
    """Replays scripted `.all()` / `.scalar_one_or_none()` results in order."""

    def __init__(self, results=None):
        self._results = list(results or [])
        self.statements = []
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.committed = True


@pytest.fixture
def org():
    o = MagicMock()
    o.id = uuid4()
    o.name = "Acme Widgets"
    return o


@pytest.fixture
def no_copy_in_flight():
    with patch.object(od, "active_run_id", return_value=None):
        yield


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

class TestOrganizationDeleteRequest:

    def test_requires_explicit_true_acknowledgement(self):
        with pytest.raises(ValidationError):
            OrganizationDeleteRequest(confirm_name="Acme Widgets", acknowledge_data_loss=False)
        with pytest.raises(ValidationError):
            OrganizationDeleteRequest(confirm_name="Acme Widgets")

    def test_requires_a_name(self):
        with pytest.raises(ValidationError):
            OrganizationDeleteRequest(confirm_name="", acknowledge_data_loss=True)

    def test_accepts_the_full_confirmation(self):
        body = OrganizationDeleteRequest(confirm_name="Acme Widgets", acknowledge_data_loss=True)
        assert body.acknowledge_data_loss is True


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TestDeleteOrganizationCompletely:

    @pytest.mark.asyncio
    async def test_name_mismatch_refuses_before_touching_anything(self, org):
        db = FakeSession()
        with patch.object(od, "active_run_id") as run_check:
            with pytest.raises(od.OrganizationNameMismatch):
                await od.delete_organization_completely(
                    db, org, confirm_name="acme widgets", actor_email="a@b.c", actor_user_id=None
                )
        run_check.assert_not_called()
        assert db.statements == [] and db.deleted == [] and db.added == []

    @pytest.mark.asyncio
    async def test_copy_in_flight_is_a_conflict(self, org):
        db = FakeSession()
        with patch.object(od, "active_run_id", return_value="run-123"):
            with pytest.raises(od.OrganizationDeleteConflict) as exc_info:
                await od.delete_organization_completely(
                    db, org, confirm_name=org.name, actor_email="a@b.c", actor_user_id=None
                )
        assert "run-123" in str(exc_info.value)
        assert db.statements == [] and db.deleted == []

    @pytest.mark.asyncio
    async def test_deletes_every_object_from_its_own_store_then_rows_then_org(self, org, no_copy_in_flight):
        old_cfg, new_cfg = uuid4(), uuid4()
        files = [
            (uuid4(), "org/a.pdf", old_cfg),
            (uuid4(), "org/b.pdf", new_cfg),
            (uuid4(), "org/soft-deleted.pdf", None),  # is_deleted rows are still selected
        ]
        blobs = [("docs/v1.md",), ("docs/v2.md",)]
        db = FakeSession([files, blobs])
        actor = uuid4()

        evidence_calls, platform_calls = [], []

        def _evidence(*a):
            # The bytes must go only once the rows are committed: a failed
            # transaction after a sweep would leave a tenant whose files 404.
            assert db.committed is True, "storage swept before the row delete committed"
            evidence_calls.append(a)

        def _platform(k):
            assert db.committed is True
            platform_calls.append(k)

        with patch.object(od.storage_service, "delete_evidence_object", side_effect=_evidence), \
             patch.object(od.storage_service, "delete_object", side_effect=_platform), \
             patch.object(od, "record_platform_event", new=AsyncMock()) as audit:
            result = await od.delete_organization_completely(
                db, org, confirm_name=org.name, actor_email="a@b.c", actor_user_id=actor
            )

        # Per-file store resolution: key, org, and THAT file's config id.
        assert evidence_calls == [
            ("org/a.pdf", str(org.id), str(old_cfg)),
            ("org/b.pdf", str(org.id), str(new_cfg)),
            ("org/soft-deleted.pdf", str(org.id), None),
        ]
        assert platform_calls == ["docs/v1.md", "docs/v2.md"]

        # The select over evidence_files carries NO is_deleted filter.
        files_select = sql_text(db.statements[0])
        assert "evidence_files" in files_select and "is_deleted" not in files_select

        # Audit event, keyed to the organisation and the actor.
        audit.assert_awaited_once()
        kwargs = audit.await_args.kwargs
        assert kwargs["entity_type"] == "organization"
        assert kwargs["entity_id"] == str(org.id)
        assert kwargs["action"] == "delete"
        assert kwargs["actor"] == "a@b.c"
        assert kwargs["actor_user_id"] == actor

        # Rows: evidence_files DELETE first, then the ORM delete of the org.
        row_deletes = [sql_text(s) for s in db.statements if sql_text(s).startswith("DELETE")]
        assert len(row_deletes) == 1 and "evidence_files" in row_deletes[0]
        assert db.deleted == [org]
        assert db.committed is True, "the service owns the commit, before the sweep"

        assert result.evidence_files_deleted == 3
        assert result.storage_objects_failed == 0
        assert result.orphaned_keys == []

    @pytest.mark.asyncio
    async def test_storage_failures_are_counted_not_fatal(self, org, no_copy_in_flight):
        files = [(uuid4(), "org/gone.pdf", None), (uuid4(), "org/ok.pdf", None)]
        db = FakeSession([files, []])

        def _delete(key, *_):
            if key == "org/gone.pdf":
                raise RuntimeError("bucket decommissioned")

        with patch.object(od.storage_service, "delete_evidence_object", side_effect=_delete), \
             patch.object(od.storage_service, "delete_object"), \
             patch.object(od, "record_platform_event", new=AsyncMock()) as audit:
            result = await od.delete_organization_completely(
                db, org, confirm_name=org.name, actor_email="a@b.c", actor_user_id=None
            )

        assert result.evidence_files_deleted == 2
        assert result.storage_objects_failed == 1
        assert result.orphaned_keys == ["org/gone.pdf"]
        assert db.deleted == [org]

        # The erasure gap is durable: a second platform event, not just a log line.
        actions = [c.kwargs["action"] for c in audit.await_args_list]
        assert actions == ["delete", od.STORAGE_ORPHANED_ACTION]
        assert all(c.kwargs["entity_id"] == str(org.id) for c in audit.await_args_list)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _user(auth_method="oidc"):
    u = MagicMock()
    u.auth_method = auth_method
    u.db_id = str(uuid4())
    u.email = "admin@example.com"
    return u


def _membership(user):
    m = MagicMock()
    m.user = user
    m.role = "admin"
    return m


class TestDeleteOrganizationRoute:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("auth_method", ["api_key", "user_api_key"])
    async def test_api_key_callers_are_refused(self, org, auth_method):
        from api.organizations import delete_organization

        db = FakeSession([org])
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        with pytest.raises(HTTPException) as exc_info:
            await delete_organization(
                request=MagicMock(), org_id=org.id, body=body, membership=_membership(_user(auth_method)), db=db
            )
        assert exc_info.value.status_code == 403
        assert db.statements == [] and db.deleted == []

    @pytest.mark.asyncio
    async def test_name_mismatch_is_400(self, org):
        from api.organizations import delete_organization

        db = FakeSession([org])
        body = OrganizationDeleteRequest(confirm_name="Wrong Name", acknowledge_data_loss=True)
        with pytest.raises(HTTPException) as exc_info:
            await delete_organization(request=MagicMock(), org_id=org.id, body=body, membership=_membership(_user()), db=db)
        assert exc_info.value.status_code == 400
        assert db.deleted == [] and db.committed is False

    @pytest.mark.asyncio
    async def test_copy_in_flight_is_409(self, org):
        from api.organizations import delete_organization

        db = FakeSession([org])
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        with patch.object(od, "active_run_id", return_value="run-9"):
            with pytest.raises(HTTPException) as exc_info:
                await delete_organization(request=MagicMock(), org_id=org.id, body=body, membership=_membership(_user()), db=db)
        assert exc_info.value.status_code == 409
        assert db.deleted == []

    @pytest.mark.asyncio
    async def test_happy_path_commits_and_reports(self, org):
        from api.organizations import delete_organization

        db = FakeSession([org])
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        outcome = od.OrganizationDeleteResult(org.id, org.name, 4, 1)
        with patch("api.organizations.delete_organization_completely", new=AsyncMock(return_value=outcome)):
            response = await delete_organization(request=MagicMock(), org_id=org.id, body=body, membership=_membership(_user()), db=db)
        assert db.committed is True
        assert response.organization_id == org.id
        assert response.evidence_files_deleted == 4
        assert response.storage_objects_failed == 1


class TestConsultantDeleteClientOrganisationRoute:

    def _profile(self):
        p = MagicMock()
        p.id = uuid4()
        return p

    @pytest.mark.asyncio
    async def test_api_key_callers_are_refused(self, org):
        from api.consultant import delete_client_organisation

        db = FakeSession()
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        with pytest.raises(HTTPException) as exc_info:
            await delete_client_organisation(
                request=MagicMock(), org_id=org.id, body=body,
                profile_and_service=(self._profile(), MagicMock()), current_user=_user("user_api_key"), db=db,
            )
        assert exc_info.value.status_code == 403
        assert db.statements == []

    @pytest.mark.asyncio
    async def test_no_active_relationship_is_404_before_any_admin_check(self, org):
        from api.consultant import delete_client_organisation

        db = FakeSession([None])  # relationship lookup finds nothing active
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        with patch("api.consultant.verify_org_membership", new=AsyncMock()) as gate:
            with pytest.raises(HTTPException) as exc_info:
                await delete_client_organisation(
                    request=MagicMock(), org_id=org.id, body=body,
                    profile_and_service=(self._profile(), MagicMock()), current_user=_user(), db=db,
                )
        assert exc_info.value.status_code == 404
        gate.assert_not_awaited()
        # The lookup was scoped to THIS consultant, this org, and ACTIVE only.
        text = sql_text(db.statements[0])
        assert "consultant_client_relationships" in text and "status" in text

    @pytest.mark.asyncio
    async def test_admin_gate_failure_propagates(self, org):
        from api.consultant import delete_client_organisation

        db = FakeSession([MagicMock()])  # active relationship exists
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        gate = AsyncMock(side_effect=HTTPException(status_code=403, detail="Access denied"))
        with patch("api.consultant.verify_org_membership", gate):
            with pytest.raises(HTTPException) as exc_info:
                await delete_client_organisation(
                    request=MagicMock(), org_id=org.id, body=body,
                    profile_and_service=(self._profile(), MagicMock()), current_user=_user(), db=db,
                )
        assert exc_info.value.status_code == 403
        assert db.deleted == []

    @pytest.mark.asyncio
    async def test_active_consultant_deletes_through_the_shared_service(self, org):
        from api.consultant import delete_client_organisation

        db = FakeSession([MagicMock(), org])
        body = OrganizationDeleteRequest(confirm_name=org.name, acknowledge_data_loss=True)
        outcome = od.OrganizationDeleteResult(org.id, org.name, 2, 0)
        with patch("api.consultant.verify_org_membership", new=AsyncMock()), \
             patch("api.organizations.delete_organization_completely", new=AsyncMock(return_value=outcome)) as svc:
            response = await delete_client_organisation(
                request=MagicMock(), org_id=org.id, body=body,
                profile_and_service=(self._profile(), MagicMock()), current_user=_user(), db=db,
            )
        svc.assert_awaited_once()
        assert svc.await_args.kwargs["confirm_name"] == org.name
        assert db.committed is True
        assert response.organization_id == org.id
        assert response.evidence_files_deleted == 2

    def test_both_routes_share_one_implementation(self):
        """Neither door can drift from the other's confirmation or cleanup."""
        import inspect
        from api import consultant, organizations

        assert "_run_organization_delete(" in inspect.getsource(consultant.delete_client_organisation)
        assert "_run_organization_delete(" in inspect.getsource(organizations.delete_organization)
        assert "_refuse_machine_callers(" in inspect.getsource(consultant.delete_client_organisation)
        assert "_refuse_machine_callers(" in inspect.getsource(organizations.delete_organization)
