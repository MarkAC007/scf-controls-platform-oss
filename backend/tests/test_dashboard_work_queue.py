"""API tests for the dashboard work-queue endpoint (R2).

Covers the aggregate response shape and the ``assigned_to_me`` per-user
filter. Uses FastAPI TestClient with ``app.dependency_overrides`` so the
suite stays in-process and dependency-free; the database session is a
hand-rolled scripted session mirroring ``test_control_composites_api.py``.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402 — imports the FastAPI app
from auth import OrgMembership, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
USER_DB_ID = uuid4()


def _overdue_row(days: int = 3) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        title="Collect firewall configs",
        due_date=date.today() - timedelta(days=days),
        priority="high",
        evidence_id="evidence_one",
    )


def _blocking_row(days: int = 10) -> SimpleNamespace:
    return SimpleNamespace(
        scf_id="control_one",
        implementation_status="not_started",
        updated_at=datetime.now() - timedelta(days=days),
    )


def _stale_row(days: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        evidence_id="evidence_two",
        next_collection_date=date.today() - timedelta(days=days),
    )


class _RowResult:
    def __init__(self, items: List[Any]):
        self._items = items

    def all(self) -> List[Any]:
        return list(self._items)


class _FakeAsyncSession:
    """Scripted async session — pop the next pre-arranged result per call.

    Statements are recorded so tests can assert on the compiled SQL
    (the query order is: overdue tasks, blocking controls, stale
    collections).
    """

    def __init__(self, responses: List[List[Any]]):
        self._responses = list(responses)
        self.statements: List[Any] = []

    async def execute(self, stmt) -> _RowResult:
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        self.statements.append(stmt)
        return _RowResult(self._responses.pop(0))


@pytest.fixture
def client_factory():
    """Returns a builder ``(session, *, db_id=USER_DB_ID) -> TestClient``."""
    app = main.app

    def _build(
        session: _FakeAsyncSession,
        *,
        db_id: Optional[UUID] = USER_DB_ID,
    ) -> TestClient:
        async def _override_db():
            yield session

        async def _override_auth():
            user = MagicMock()
            user.db_id = str(db_id) if db_id else None
            user.email = "test@example.com"
            return OrgMembership(
                user=user, organization_id=ORG_ID, role="viewer", is_consultant=False
            )

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_org_viewer] = _override_auth
        return TestClient(app)

    yield _build

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_viewer, None)


def test_work_queue_aggregates_all_categories(client_factory):
    session = _FakeAsyncSession([[_overdue_row()], [_blocking_row()], [_stale_row()]])
    client = client_factory(session)
    resp = client.get(f"/api/organizations/{ORG_ID}/dashboard/work-queue")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_items"] == 3
    assert body["overdue_evidence"][0]["evidence_id"] == "evidence_one"
    assert body["overdue_evidence"][0]["days_overdue"] == 3
    assert body["blocking_controls"][0]["scf_id"] == "control_one"
    assert body["stale_collections"][0]["evidence_id"] == "evidence_two"


def test_default_queries_have_no_user_filter(client_factory):
    session = _FakeAsyncSession([[], [], []])
    client = client_factory(session)
    resp = client.get(f"/api/organizations/{ORG_ID}/dashboard/work-queue")
    assert resp.status_code == 200
    overdue_sql, blocking_sql, stale_sql = [str(s) for s in session.statements]
    assert "assigned_user_id" not in overdue_sql
    assert "assigned_user_id" not in blocking_sql
    assert "owner_user_id" not in blocking_sql
    assert "assigned_user_id" not in stale_sql


def test_assigned_to_me_filters_tasks_and_controls(client_factory):
    session = _FakeAsyncSession([[], [], []])
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/dashboard/work-queue?assigned_to_me=true"
    )
    assert resp.status_code == 200
    overdue_sql, blocking_sql, stale_sql = [str(s) for s in session.statements]
    assert "assigned_user_id" in overdue_sql
    assert "owner_user_id" in blocking_sql
    assert "assigned_user_id" in blocking_sql
    # Stale collections narrow too, on the same owner-or-assignee test as
    # controls. This assertion used to be inverted, with a comment calling the
    # omission intentional. It was defensible when written (2026-08-20): nothing
    # wrote evidence_tracking.owner_user_id, so filtering on it would have
    # emptied "My work" for every user. #781 backfilled that column and gave the
    # UI a control that writes assigned_user_id, which is what expired it.
    assert "assigned_user_id" in stale_sql
    assert "owner_user_id" in stale_sql


def test_assigned_to_me_without_db_id_falls_back_to_unfiltered(client_factory):
    session = _FakeAsyncSession([[], [], []])
    client = client_factory(session, db_id=None)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/dashboard/work-queue?assigned_to_me=true"
    )
    assert resp.status_code == 200
    overdue_sql, blocking_sql, stale_sql = [str(s) for s in session.statements]
    assert "assigned_user_id" not in overdue_sql
    assert "owner_user_id" not in blocking_sql
    assert "owner_user_id" not in stale_sql


def test_assigned_to_me_stale_rows_count_toward_total(client_factory):
    """The filtered stale list is still aggregated, not merely filtered away."""
    session = _FakeAsyncSession([[], [], [_stale_row(days=9)]])
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/dashboard/work-queue?assigned_to_me=true"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_items"] == 1
    assert body["stale_collections"][0]["evidence_id"] == "evidence_two"
    assert body["stale_collections"][0]["days_overdue"] == 9


def test_stale_filter_matches_the_blocking_filter(client_factory):
    """"Mine" must mean one thing across the queue.

    Both branches test owner OR assignee. If a later change narrows one of them
    -- to assignee only, say -- the toggle silently acquires two meanings again,
    which is the defect this pair of assertions exists to catch.
    """
    session = _FakeAsyncSession([[], [], []])
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/dashboard/work-queue?assigned_to_me=true"
    )
    assert resp.status_code == 200
    _, blocking_sql, stale_sql = [str(s) for s in session.statements]
    for sql in (blocking_sql, stale_sql):
        assert "owner_user_id" in sql
        assert "assigned_user_id" in sql
        assert " OR " in sql.upper()
