# SPDX-License-Identifier: AGPL-3.0-only
"""Editing an audit engagement writes its audit trail instead of answering 500 (#1284).

ENGAGEMENT_TRACKED_FIELDS is a list and the update branch of
log_entity_changes intersected tracked_fields with a set, so every PATCH on an
engagement raised TypeError. Route functions are called directly against a
fake session, in the no-real-Postgres style of
test_engagement_evidence_queries.py; the audit writer is the real one unless a
test says otherwise.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.audit_engagements as ae  # noqa: E402
from models import AuditEngagement, AuditLog  # noqa: E402
from schemas import AuditEngagementUpdate  # noqa: E402
from services.audit_service import log_entity_changes  # noqa: E402

ORG_ID = uuid4()
ENGAGEMENT_ID = uuid4()
USER_ID = uuid4()


class _Result:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value


class _Session:
    """Pops scripted results; records added objects."""

    def __init__(self, scripted=()):
        self.scripted = list(scripted)
        self.added = []
        self.commits = 0

    async def execute(self, stmt, *_a, **_k):
        if not self.scripted:
            raise AssertionError(f"ran out of scripted results at {stmt}")
        return self.scripted.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


def _engagement():
    return AuditEngagement(
        id=ENGAGEMENT_ID, organization_id=ORG_ID, name="ISO 27001 surveillance",
        frameworks=["iso_27001_2022"], status="draft",
        start_date=date(2026, 11, 1), end_date=date(2026, 12, 1),
        created_by_user_id=USER_ID,
        created_at=datetime(2026, 11, 16), updated_at=datetime(2026, 11, 16),
    )


def _editor():
    return SimpleNamespace(user=SimpleNamespace(db_id=str(USER_ID)), role="editor")


@pytest.fixture(autouse=True)
def _no_request(monkeypatch):
    """Routes are called with request=None; the request-derived audit columns are not under test."""
    monkeypatch.setattr(ae, "detect_action_source", lambda request: "api")
    monkeypatch.setattr(ae, "get_request_id", lambda request: None)


async def _patch(engagement, update):
    # the engagement, then its scope count
    db = _Session([_Result(value=engagement), _Result(value=3)])
    resp = await ae.update_engagement(
        ORG_ID, ENGAGEMENT_ID, update, request=None, membership=_editor(), db=db)
    return resp, db


@pytest.mark.asyncio
async def test_patch_engagement_updates_and_audits_the_changed_fields():
    engagement = _engagement()
    resp, db = await _patch(engagement, AuditEngagementUpdate(name="Renamed", status="in_progress"))

    assert resp.name == "Renamed" and resp.status == "in_progress"
    assert resp.scope_count == 3
    assert db.commits == 1

    entries = [o for o in db.added if isinstance(o, AuditLog)]
    assert {e.field_name for e in entries} == {"name", "status"}
    by_field = {e.field_name: e for e in entries}
    assert by_field["name"].old_value == '"ISO 27001 surveillance"'
    assert by_field["name"].new_value == '"Renamed"'
    assert all(e.entity_type == "audit_engagement" and e.action == "update" for e in entries)
    assert all(e.entity_id == ENGAGEMENT_ID and e.changed_by_user_id == USER_ID for e in entries)


@pytest.mark.asyncio
async def test_patch_engagement_hands_the_audit_writer_a_set(monkeypatch):
    seen = []

    async def _audit(**kw):
        seen.append(kw)
        return []

    monkeypatch.setattr(ae, "log_entity_changes", _audit)
    await _patch(_engagement(), AuditEngagementUpdate(name="Renamed"))

    [call] = seen
    assert call["tracked_fields"] == set(ae.ENGAGEMENT_TRACKED_FIELDS)
    assert isinstance(call["tracked_fields"], (set, frozenset))


@pytest.mark.asyncio
@pytest.mark.parametrize("constant", [
    "ENGAGEMENT_TRACKED_FIELDS",
    "QUERY_TRACKED_FIELDS",
    "QUERY_CREATE_TRACKED_FIELDS",
    "AUDITOR_GRANT_TRACKED_FIELDS",
    "AUDITOR_GRANT_CREATE_FIELDS",
])
async def test_engagement_constants_are_usable_as_tracked_fields(constant):
    tracked = getattr(ae, constant)
    field = next(iter(tracked))
    db = _Session()
    entries = await log_entity_changes(
        db=db, organization_id=ORG_ID, entity_type="audit_engagement", entity_id=ENGAGEMENT_ID,
        action="update", changed_by_user_id=USER_ID,
        old_values={field: "a", "untracked": 1}, new_values={field: "b", "untracked": 2},
        tracked_fields=tracked,
    )
    assert [e.field_name for e in entries] == [field]


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [
    list, tuple, set, frozenset, lambda fields: (f for f in fields), lambda fields: dict.fromkeys(fields).keys(),
], ids=["list", "tuple", "set", "frozenset", "generator", "dict_keys"])
@pytest.mark.parametrize("action,old,new", [
    ("create", {}, {"name": "n", "status": "draft", "untracked": 1}),
    ("update", {"name": "a", "status": "draft", "untracked": 1}, {"name": "b", "status": "active", "untracked": 2}),
    ("delete", {"name": "n", "status": "draft", "untracked": 1}, {}),
])
async def test_log_entity_changes_takes_any_iterable_of_tracked_fields(make, action, old, new):
    db = _Session()
    entries = await log_entity_changes(
        db=db, organization_id=ORG_ID, entity_type="audit_engagement", entity_id=ENGAGEMENT_ID,
        action=action, changed_by_user_id=USER_ID, old_values=old, new_values=new,
        tracked_fields=make(["name", "status"]),
    )
    assert sorted(e.field_name for e in entries) == ["name", "status"]
    assert db.added == entries
