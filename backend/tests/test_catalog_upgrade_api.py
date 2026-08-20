"""Endpoint tests for the platform catalog-upgrade admin API (WP1c, plan §4.5).

FastAPI TestClient + ``app.dependency_overrides`` (the in-process, scripted
style of test_control_composites_api.py): the database session is a fake that
hands back pre-arranged results in handler query order, auth is overridden at
``require_platform_admin`` (the user-session guard is NOT overridden, so the
static-API-key refusal tests exercise the real dependency), and the Celery /
object-storage boundaries are monkeypatched.

Covered (WP1c acceptance list):
- upload happy path (run created, workbook stashed, staging task dispatched);
- one-in-flight second upload -> 409;
- 403 for the static-API-key principal on every destructive route
  (pairings PUT, apply, revert, superseded-by PATCH);
- apply typed-confirm: expected_to_version mismatch -> 409,
  confirm_text mismatch -> 400, non-staged run -> 409;
- runs list/detail mapping, diff pagination + filters, pairings validation,
  cancel transitions, revert pre-flight refusals (blockers listed), tenants
  board eligibility;
- GET /api/catalog/status returns the ledger version, with row-max fallback
  only when the ledger is empty.

Fixture identifiers use a letter after the hyphen (``GOV-A1`` style) — opaque
to the code under test, never real control ids.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402 — imports the FastAPI app
from api import catalog_upgrade_admin as cua  # noqa: E402
from auth import User, require_platform_admin  # noqa: E402
from database import get_db  # noqa: E402
from models import CatalogImportRun, Organization, OrganizationCatalogState  # noqa: E402
from schemas_catalog_upgrade import (  # noqa: E402
    AddedEntity,
    CatalogEntityType,
    ChangedEntity,
    DeprecatedEntity,
    DiffDetail,
    EntityDiff,
    FieldChange,
    ResurrectedEntity,
    SupersededSuggestion,
)

_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

T0 = datetime(2026, 8, 20, 9, 0, 0)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_SENTINEL = object()


class _Result:
    """Duck-typed SQLAlchemy result: scalars().all()/first() and scalar()."""

    def __init__(self, items: Optional[List[Any]] = None, value: Any = _SENTINEL):
        self._items = list(items or [])
        self._value = value

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def scalar(self) -> Any:
        if self._value is not _SENTINEL:
            return self._value
        return self._items[0] if self._items else None


class FakeSession:
    """Pops one pre-arranged _Result per execute(), in handler query order."""

    def __init__(self, responses: Optional[List[Any]] = None):
        self._responses = list(responses or [])
        self.added: List[Any] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _stmt) -> _Result:
        if not self._responses:
            raise AssertionError("FakeSession: ran out of scripted results")
        nxt = self._responses.pop(0)
        return nxt if isinstance(nxt, _Result) else _Result(list(nxt))

    def add(self, obj) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid4()

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _make_run(**over) -> CatalogImportRun:
    defaults = dict(
        id=uuid4(),
        from_version="2026.1",
        to_version="2026.2",
        status="staged",
        workbook_object_key="_catalog-upgrade/x/workbook.xlsx",
        diff_detail_object_key="_catalog-upgrade/x/diff_detail.json",
        diff_summary=None,
        sanity_report=None,
        superseded_pairings=None,
        started_by=None,
        created_at=T0,
        updated_at=T0,
        completed_at=None,
    )
    defaults.update(over)
    run = CatalogImportRun()
    for key, value in defaults.items():
        setattr(run, key, value)
    return run


def _diff_detail() -> DiffDetail:
    return DiffDetail(
        from_version="2026.1",
        to_version="2026.2",
        entities={
            CatalogEntityType.CONTROLS: EntityDiff(
                added=[
                    AddedEntity(key="GOV-A1", name="Added One", data={"control_name": "Added One"}),
                    AddedEntity(key="GOV-A2", name="Added Two", data={"control_name": "Added Two"}),
                ],
                changed=[
                    ChangedEntity(
                        key="GOV-B1",
                        fields={"control_description": FieldChange(old="a", new="b")},
                    )
                ],
                deprecated=[
                    DeprecatedEntity(
                        key="GOV-C1",
                        superseded_by=None,
                        suggestions=[SupersededSuggestion(scf_id="GOV-A1", score=0.7)],
                    )
                ],
                resurrected=[ResurrectedEntity(key="GOV-D1")],
                unchanged=["GOV-E1", "GOV-E2"],
            ),
            CatalogEntityType.DOMAINS: EntityDiff(
                added=[AddedEntity(key="DEMODOM", name="Demo Domain")]
            ),
        },
    )


def _patch_diff_download(monkeypatch, detail: DiffDetail) -> None:
    payload = detail.model_dump_json().encode("utf-8")
    monkeypatch.setattr(
        cua.s3_service, "download_blob_stream", lambda key: iter([payload])
    )


def _patch_send_task(monkeypatch) -> List[dict]:
    calls: List[dict] = []

    def _send(name, kwargs=None, queue=None, **_extra):
        calls.append({"name": name, "kwargs": kwargs or {}, "queue": queue})
        return SimpleNamespace(id=f"task-{len(calls)}")

    monkeypatch.setattr(cua.celery_app, "send_task", _send)
    return calls


# ---------------------------------------------------------------------------
# Client fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def client_factory():
    """(session, *, auth_method='google') -> TestClient.

    Overrides ``require_platform_admin`` to return a principal with the given
    auth_method; ``require_platform_admin_user_session`` is deliberately NOT
    overridden so its static-API-key refusal runs for real.
    """
    app = main.app

    def _build(session: FakeSession, *, auth_method: str = "google") -> TestClient:
        async def _override_db():
            yield session

        async def _override_admin():
            if auth_method == "api_key":
                return User(user_id="api_user", auth_method="api_key")
            return User(
                user_id="google-sub-1",
                email="admin@example.com",
                auth_method=auth_method,
                db_id=str(uuid4()),
            )

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_platform_admin] = _override_admin
        return TestClient(app)

    yield _build

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_platform_admin, None)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def test_upload_creates_run_stashes_workbook_and_dispatches_staging(
    client_factory, monkeypatch
):
    calls = _patch_send_task(monkeypatch)
    stashed = {}

    def _put(key, body, content_type, org_id):
        stashed.update(key=key, size=len(body), content_type=content_type, org=org_id)

    monkeypatch.setattr(cua.s3_service, "put_bytes", _put)

    session = FakeSession([_Result([])])  # in-flight check: none
    client = client_factory(session)
    resp = client.post(
        "/api/admin/catalog/upgrade",
        files={"file": ("scf-new.xlsx", b"workbook-bytes", _XLSX)},
    )
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["status"] == "staging"
    assert data["task_id"] == "task-1"

    run = session.added[0]
    assert str(run.id) == data["run_id"]
    assert run.status == "staging"
    assert run.to_version == cua.TO_VERSION_PENDING
    assert run.workbook_object_key == f"_catalog-upgrade/{run.id}/workbook.xlsx"
    assert session.committed

    assert stashed["key"] == run.workbook_object_key
    assert stashed["org"] == "platform"
    assert calls == [
        {
            "name": "catalog.upgrade_stage",
            "kwargs": {"run_id": str(run.id), "force": False},
            "queue": "catalog",
        }
    ]


def test_upload_second_in_flight_run_is_409(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    existing = _make_run(status="staged")
    session = FakeSession([_Result([existing])])
    client = client_factory(session)
    resp = client.post(
        "/api/admin/catalog/upgrade",
        files={"file": ("scf-new.xlsx", b"workbook-bytes", _XLSX)},
    )
    assert resp.status_code == 409
    assert str(existing.id) in resp.json()["detail"]
    assert not session.added and not calls


def test_upload_rejects_non_xlsx(client_factory):
    session = FakeSession()
    client = client_factory(session)
    resp = client.post(
        "/api/admin/catalog/upgrade",
        files={"file": ("scf.csv", b"a,b", "text/csv")},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Static-API-key refusal on every destructive route (plan §4.5, §4.8)
# ---------------------------------------------------------------------------


DESTRUCTIVE_CALLS = [
    ("put", "/api/admin/catalog/upgrade/runs/{run_id}/pairings", {"pairings": []}),
    (
        "post",
        "/api/admin/catalog/upgrade/runs/{run_id}/apply",
        {"expected_to_version": "2026.2", "confirm_text": "2026.2"},
    ),
    ("post", "/api/admin/catalog/upgrade/runs/{run_id}/revert", None),
    (
        "patch",
        "/api/admin/catalog/controls/gov-c1/superseded-by",
        {"superseded_by": None},
    ),
]


@pytest.mark.parametrize("method,path,body", DESTRUCTIVE_CALLS)
def test_destructive_routes_refuse_the_static_api_key_principal(
    client_factory, method, path, body
):
    # No scripted DB results: the guard must reject before any query runs.
    session = FakeSession()
    client = client_factory(session, auth_method="api_key")
    url = path.format(run_id=uuid4())
    resp = getattr(client, method)(url, json=body)
    assert resp.status_code == 403, f"{method} {url}: {resp.text}"
    assert "static API key" in resp.json()["detail"]


def test_user_api_key_principal_passes_the_user_session_guard(client_factory):
    # Per-user API keys resolve to an accountable DB user — allowed through
    # (the run lookup then 404s, proving the guard did not block).
    session = FakeSession([_Result([])])
    client = client_factory(session, auth_method="user_api_key")
    resp = client.post(
        f"/api/admin/catalog/upgrade/runs/{uuid4()}/apply",
        json={"expected_to_version": "2026.2", "confirm_text": "2026.2"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Runs list / detail
# ---------------------------------------------------------------------------


def test_runs_list_maps_ledger_rows(client_factory):
    started_by = uuid4()
    applied = _make_run(
        status="applied",
        started_by=started_by,
        completed_at=T0 + timedelta(hours=1),
        diff_summary={
            "from_version": "2026.1",
            "to_version": "2026.2",
            "entities": {"controls": {"added": 2, "changed": 1}},
        },
    )
    pending = _make_run(
        status="staging", to_version=cua.TO_VERSION_PENDING, from_version=None,
        diff_detail_object_key=None,
    )
    session = FakeSession([_Result(value=2), _Result([pending, applied])])
    client = client_factory(session)
    resp = client.get("/api/admin/catalog/upgrade/runs")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 2
    first, second = data["runs"]
    assert first["to_version"] is None  # placeholder never leaks
    assert first["status"] == "staging"
    assert second["status"] == "applied"
    assert second["created_by"] == str(started_by)
    assert second["diff_summary"]["entities"]["controls"]["added"] == 2


def test_run_detail_404_and_mapping(client_factory):
    session = FakeSession([_Result([])])
    client = client_factory(session)
    assert client.get(f"/api/admin/catalog/upgrade/runs/{uuid4()}").status_code == 404

    run = _make_run(
        status="applied",
        completed_at=T0 + timedelta(hours=2),
        sanity_report={"passed": True, "checks": []},
        superseded_pairings=[{"deprecated_scf_id": "GOV-C1", "superseded_by": "GOV-A1"}],
    )
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.get(f"/api/admin/catalog/upgrade/runs/{run.id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sanity_report"]["passed"] is True
    assert data["superseded_pairings"][0]["superseded_by"] == "GOV-A1"
    assert data["applied_at"] is not None
    assert data["reverted_at"] is None


# ---------------------------------------------------------------------------
# Diff pagination + filters
# ---------------------------------------------------------------------------


def test_diff_flattens_filters_and_paginates(client_factory, monkeypatch):
    run = _make_run()
    _patch_diff_download(monkeypatch, _diff_detail())

    # Unfiltered: 7 control rows + 1 domain row, stable order.
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.get(f"/api/admin/catalog/upgrade/runs/{run.id}/diff")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 8
    assert [i["key"] for i in data["items"][:4]] == ["GOV-A1", "GOV-A2", "GOV-B1", "GOV-C1"]
    dep = data["items"][3]
    assert dep["change_class"] == "deprecated"
    assert dep["suggestions"][0]["scf_id"] == "GOV-A1"

    # Filtered to controls/added.
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.get(
        f"/api/admin/catalog/upgrade/runs/{run.id}/diff",
        params={"entity": "controls", "change_class": "added"},
    )
    data = resp.json()
    assert data["total"] == 2
    assert data["entity"] == "controls" and data["change_class"] == "added"

    # Page 2 of size 3.
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.get(
        f"/api/admin/catalog/upgrade/runs/{run.id}/diff",
        params={"page": 2, "page_size": 3},
    )
    data = resp.json()
    assert data["total"] == 8 and data["page"] == 2
    assert [i["key"] for i in data["items"]] == ["GOV-C1", "GOV-D1", "GOV-E1"]


def test_diff_before_staging_completes_is_409(client_factory):
    run = _make_run(status="staging", diff_detail_object_key=None)
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.get(f"/api/admin/catalog/upgrade/runs/{run.id}/diff")
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Pairings PUT
# ---------------------------------------------------------------------------


def test_pairings_put_stores_validated_pairings(client_factory, monkeypatch):
    run = _make_run(status="staged")
    _patch_diff_download(monkeypatch, _diff_detail())
    successor = SimpleNamespace(scf_id="GOV-A1", status="active")
    session = FakeSession([_Result([run]), _Result([successor])])
    client = client_factory(session)
    resp = client.put(
        f"/api/admin/catalog/upgrade/runs/{run.id}/pairings",
        json={"pairings": [{"deprecated_scf_id": "GOV-C1", "superseded_by": "GOV-A1"}]},
    )
    assert resp.status_code == 200, resp.text
    assert run.superseded_pairings == [
        {"deprecated_scf_id": "GOV-C1", "superseded_by": "GOV-A1"}
    ]
    assert session.committed


def test_pairings_put_rejects_unknown_deprecated_key_and_wrong_status(
    client_factory, monkeypatch
):
    run = _make_run(status="staged")
    _patch_diff_download(monkeypatch, _diff_detail())
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.put(
        f"/api/admin/catalog/upgrade/runs/{run.id}/pairings",
        json={"pairings": [{"deprecated_scf_id": "GOV-Z9", "superseded_by": None}]},
    )
    assert resp.status_code == 400
    assert "GOV-Z9" in resp.json()["detail"]

    applied = _make_run(status="applied")
    session = FakeSession([_Result([applied])])
    client = client_factory(session)
    resp = client.put(
        f"/api/admin/catalog/upgrade/runs/{applied.id}/pairings",
        json={"pairings": []},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Apply — typed confirm
# ---------------------------------------------------------------------------


def test_apply_happy_path_dispatches_celery(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="staged")
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.post(
        f"/api/admin/catalog/upgrade/runs/{run.id}/apply",
        json={"expected_to_version": "2026.2", "confirm_text": "2026.2"},
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "applying"
    assert calls == [
        {
            "name": "catalog.upgrade_apply",
            "kwargs": {"run_id": str(run.id)},
            "queue": "catalog",
        }
    ]


def test_apply_expected_version_mismatch_is_409(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="staged")
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.post(
        f"/api/admin/catalog/upgrade/runs/{run.id}/apply",
        json={"expected_to_version": "2026.9", "confirm_text": "2026.9"},
    )
    assert resp.status_code == 409
    assert not calls


def test_apply_confirm_text_mismatch_is_400(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="staged")
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.post(
        f"/api/admin/catalog/upgrade/runs/{run.id}/apply",
        json={"expected_to_version": "2026.2", "confirm_text": "2026.1"},
    )
    assert resp.status_code == 400
    assert not calls


def test_apply_refused_unless_staged(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="blocked")
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.post(
        f"/api/admin/catalog/upgrade/runs/{run.id}/apply",
        json={"expected_to_version": "2026.2", "confirm_text": "2026.2"},
    )
    assert resp.status_code == 409
    assert not calls


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


def test_cancel_transitions_and_guards(client_factory):
    run = _make_run(status="staged")
    session = FakeSession([_Result([run])])
    client = client_factory(session)
    resp = client.post(f"/api/admin/catalog/upgrade/runs/{run.id}/cancel")
    assert resp.status_code == 200 and run.status == "cancelled"
    assert session.committed

    applied = _make_run(status="applied")
    session = FakeSession([_Result([applied])])
    client = client_factory(session)
    resp = client.post(f"/api/admin/catalog/upgrade/runs/{applied.id}/cancel")
    assert resp.status_code == 409 and applied.status == "applied"


# ---------------------------------------------------------------------------
# Revert — pre-flighted WP1b guards
# ---------------------------------------------------------------------------


def test_revert_blocked_by_reconciled_orgs_lists_organization_ids(
    client_factory, monkeypatch
):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="applied", completed_at=T0 + timedelta(hours=1))
    org_id = uuid4()
    state = SimpleNamespace(organization_id=org_id, reconciled_catalog_version="2026.2")
    # Handler: run lookup; _check_revert_allowed: applied runs, org states.
    session = FakeSession([_Result([run]), _Result([run]), _Result([state])])
    client = client_factory(session)
    resp = client.post(f"/api/admin/catalog/upgrade/runs/{run.id}/revert")
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "revert_blocked"
    assert detail["organization_ids"] == [str(org_id)]
    assert not calls


def test_revert_refused_when_not_latest_applied(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="applied", completed_at=T0 + timedelta(hours=1))
    newer = _make_run(
        status="applied", to_version="2026.3", completed_at=T0 + timedelta(hours=5)
    )
    session = FakeSession([_Result([run]), _Result([run, newer])])
    client = client_factory(session)
    resp = client.post(f"/api/admin/catalog/upgrade/runs/{run.id}/revert")
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "revert_not_latest"
    assert not calls


def test_revert_happy_path_dispatches_celery(client_factory, monkeypatch):
    calls = _patch_send_task(monkeypatch)
    run = _make_run(status="applied", completed_at=T0 + timedelta(hours=1))
    session = FakeSession([_Result([run]), _Result([run]), _Result([])])
    client = client_factory(session)
    resp = client.post(f"/api/admin/catalog/upgrade/runs/{run.id}/revert")
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "reverting"
    assert calls[0]["name"] == "catalog.upgrade_revert"


# ---------------------------------------------------------------------------
# Tenants reconciliation board
# ---------------------------------------------------------------------------


def test_tenants_board_reports_eligibility_and_active_runs(client_factory):
    ledger_run = _make_run(status="applied", completed_at=T0 + timedelta(hours=1))

    org_current = Organization(id=uuid4(), name="Current Org", slug="current-org")
    org_behind = Organization(id=uuid4(), name="Lagging Org", slug="lagging-org")
    states = [
        SimpleNamespace(
            organization_id=org_current.id,
            reconciled_catalog_version="2026.2",
            last_reconciled_at=T0,
        ),
        SimpleNamespace(
            organization_id=org_behind.id,
            reconciled_catalog_version="2026.1",
            last_reconciled_at=T0,
        ),
    ]
    active = SimpleNamespace(
        id=uuid4(), organization_id=org_behind.id, status="previewed"
    )
    session = FakeSession(
        [
            _Result([ledger_run]),  # get_current_catalog_version
            _Result([org_current, org_behind]),
            _Result(states),
            _Result([active]),
        ]
    )
    client = client_factory(session)
    resp = client.get("/api/admin/catalog/tenants")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["platform_catalog_version"] == "2026.2"
    assert data["total"] == 2
    rows = {row["organization_name"]: row for row in data["tenants"]}
    assert rows["Current Org"]["eligible"] is False
    assert rows["Current Org"]["active_run_id"] is None
    assert rows["Lagging Org"]["eligible"] is True
    assert rows["Lagging Org"]["active_run_status"] == "previewed"
    assert rows["Lagging Org"]["active_run_id"] == str(active.id)


# ---------------------------------------------------------------------------
# Superseded-by PATCH
# ---------------------------------------------------------------------------


def test_superseded_by_patch_updates_deprecated_control(client_factory):
    control = SimpleNamespace(scf_id="GOV-C1", status="deprecated", superseded_by=None)
    successor = SimpleNamespace(scf_id="GOV-A1", status="active")
    session = FakeSession([_Result([control]), _Result([successor])])
    client = client_factory(session)
    resp = client.patch(
        "/api/admin/catalog/controls/gov-c1/superseded-by",
        json={"superseded_by": "gov-a1", "justification": "manual correction"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"scf_id": "GOV-C1", "superseded_by": "GOV-A1"}
    assert control.superseded_by == "GOV-A1"
    assert session.committed


def test_superseded_by_patch_guards(client_factory):
    # Active control -> 409.
    control = SimpleNamespace(scf_id="GOV-C1", status="active", superseded_by=None)
    session = FakeSession([_Result([control])])
    client = client_factory(session)
    resp = client.patch(
        "/api/admin/catalog/controls/gov-c1/superseded-by",
        json={"superseded_by": None},
    )
    assert resp.status_code == 409

    # Missing/inactive successor -> 400.
    control = SimpleNamespace(scf_id="GOV-C1", status="deprecated", superseded_by=None)
    session = FakeSession([_Result([control]), _Result([])])
    client = client_factory(session)
    resp = client.patch(
        "/api/admin/catalog/controls/gov-c1/superseded-by",
        json={"superseded_by": "gov-z9"},
    )
    assert resp.status_code == 400

    # Unknown control -> 404.
    session = FakeSession([_Result([])])
    client = client_factory(session)
    resp = client.patch(
        "/api/admin/catalog/controls/gov-c1/superseded-by",
        json={"superseded_by": None},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/catalog/status — ledger is the version authority (plan §4.2.5)
# ---------------------------------------------------------------------------


def test_catalog_status_returns_ledger_version(client_factory):
    ledger_run = _make_run(status="applied", completed_at=T0 + timedelta(hours=1))
    session = FakeSession(
        [
            _Result(value=1451),  # control count
            _Result([ledger_run]),  # ledger authority — consulted first
        ]
    )
    client = client_factory(session)
    resp = client.get("/api/catalog/status")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"seeded": True, "controls": 1451, "catalog_version": "2026.2"}


def test_catalog_status_falls_back_to_row_stamp_only_when_ledger_empty(client_factory):
    session = FakeSession(
        [
            _Result(value=1451),
            _Result([]),  # no applied run ever
            _Result(value="2025.4"),  # max stamped row version
        ]
    )
    client = client_factory(session)
    resp = client.get("/api/catalog/status")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"seeded": True, "controls": 1451, "catalog_version": "2025.4"}
