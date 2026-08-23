"""Tests for resolving the navigable key of a notification.

The defect: a notification names an evidence item in its own message
("Evidence collection task for E-HRS-16 is overdue by 4 day(s)") and stores only
a row UUID, so clicking it could not open the item it names.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any, List
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.notification_targets import resolve_reference_keys  # noqa: E402


def _notification(reference_type: str, reference_id) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), reference_type=reference_type, reference_id=reference_id
    )


class _Result:
    def __init__(self, rows: List[Any]):
        self._rows = rows

    def all(self) -> List[Any]:
        return list(self._rows)


class _FakeSession:
    """Scripted session that records how many queries were issued."""

    def __init__(self, responses: List[List[Any]]):
        self._responses = list(responses)
        self.query_count = 0

    async def execute(self, stmt) -> _Result:
        self.query_count += 1
        if not self._responses:
            raise AssertionError("ran out of scripted results")
        return _Result(self._responses.pop(0))


class _ExplodingSession:
    def __init__(self):
        self.query_count = 0

    async def execute(self, stmt):
        self.query_count += 1
        raise RuntimeError("database is having a day")


@pytest.mark.asyncio
async def test_evidence_notification_resolves_to_the_evidence_key():
    tracking_id = uuid4()
    n = _notification("evidence", tracking_id)
    session = _FakeSession([[SimpleNamespace(id=tracking_id, evidence_id="E-HRS-16")]])
    resolved = await resolve_reference_keys(session, [n])
    assert resolved[n.id] == "E-HRS-16"


@pytest.mark.asyncio
async def test_task_notification_resolves_through_its_tracking_row():
    """The case the message text always knew and the reference never did."""
    task_id = uuid4()
    n = _notification("task", task_id)
    session = _FakeSession([[SimpleNamespace(id=task_id, evidence_id="E-HRS-16")]])
    resolved = await resolve_reference_keys(session, [n])
    assert resolved[n.id] == "E-HRS-16"


@pytest.mark.asyncio
async def test_unresolvable_reference_yields_no_key():
    """A tracking row that has since been deleted must not fabricate a target."""
    n = _notification("evidence", uuid4())
    session = _FakeSession([[]])
    resolved = await resolve_reference_keys(session, [n])
    assert resolved.get(n.id) is None


@pytest.mark.asyncio
async def test_reference_types_without_a_key_are_left_alone():
    for reference_type in ("control", "catalog", "comment", "engagement_query"):
        n = _notification(reference_type, uuid4())
        session = _FakeSession([])
        resolved = await resolve_reference_keys(session, [n])
        assert resolved.get(n.id) is None
        assert session.query_count == 0, f"{reference_type} should issue no query"


@pytest.mark.asyncio
async def test_a_null_reference_id_is_skipped():
    n = _notification("evidence", None)
    session = _FakeSession([])
    resolved = await resolve_reference_keys(session, [n])
    assert resolved == {}
    assert session.query_count == 0


@pytest.mark.asyncio
async def test_resolution_is_batched_not_per_notification():
    """The bell polls this endpoint every 30s; per-row resolution is an N+1."""
    tracking_rows = [(uuid4(), f"E-BATCH-{i}") for i in range(20)]
    task_rows = [(uuid4(), f"E-TASK-{i}") for i in range(20)]
    notifications = [_notification("evidence", tid) for tid, _ in tracking_rows]
    notifications += [_notification("task", tid) for tid, _ in task_rows]

    session = _FakeSession([
        [SimpleNamespace(id=tid, evidence_id=key) for tid, key in tracking_rows],
        [SimpleNamespace(id=tid, evidence_id=key) for tid, key in task_rows],
    ])
    resolved = await resolve_reference_keys(session, notifications)

    assert len(resolved) == 40
    assert session.query_count == 2, (
        f"40 notifications took {session.query_count} queries; must be 2"
    )


@pytest.mark.asyncio
async def test_one_query_when_only_one_reference_kind_is_present():
    n = _notification("evidence", uuid4())
    session = _FakeSession([[]])
    await resolve_reference_keys(session, [n])
    assert session.query_count == 1


@pytest.mark.asyncio
async def test_an_empty_page_issues_no_query():
    session = _FakeSession([])
    assert await resolve_reference_keys(session, []) == {}
    assert session.query_count == 0


@pytest.mark.asyncio
async def test_a_database_failure_degrades_to_no_links_not_a_500():
    """A bell without deep links is degraded; a bell that 500s is no bell."""
    n = _notification("evidence", uuid4())
    session = _ExplodingSession()
    resolved = await resolve_reference_keys(session, [n])
    assert resolved == {}
