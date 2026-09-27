"""Audit log rows resolve to the object they changed, inside one organisation.

``list_audit_log`` enriches each row with ``entity_label`` / ``entity_ref`` so
the webclient can link a row to its object (a risk opens by risk code, not the
assessment UUID the row stores). These pin the resolver's contract without a
database: one query per entity type present, every query filtered to the
requesting organisation, and nothing resolved for a type with no page or an
object that no longer exists.
"""
from __future__ import annotations

import os
import sys
import uuid
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# System's relationships name catalog models by string; the app imports both
# modules, so load them here too or mapper configuration fails in isolation.
import catalog_models  # noqa: E402,F401
from api.audit_log import _resolve_entity_refs  # noqa: E402

# CI runs pytest from the repo root, where backend/pytest.ini's asyncio_mode
# does not apply.
pytestmark = pytest.mark.asyncio


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeSession:
    """Records each statement and answers from a table-name keyed map."""

    def __init__(self, rows_by_table):
        self.rows_by_table = rows_by_table
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        table = stmt.get_final_froms()[0].name
        return _FakeResult(self.rows_by_table.get(table, []))


def _entry(entity_type, entity_id):
    return SimpleNamespace(entity_type=entity_type, entity_id=entity_id)


async def test_resolves_risk_assessment_to_its_risk_code():
    org = uuid.uuid4()
    risk_id = uuid.uuid4()
    db = _FakeSession({"risk_assessments": [(risk_id, "R-TEST-1", "R-TEST-1")]})

    refs = await _resolve_entity_refs(db, org, [_entry("risk_assessment", risk_id)])

    assert refs == {("risk_assessment", risk_id): ("R-TEST-1", "R-TEST-1")}


async def test_uuid_refs_are_returned_as_strings():
    org = uuid.uuid4()
    vendor_id = uuid.uuid4()
    db = _FakeSession({"vendors": [(vendor_id, "Acme Hosting", vendor_id)]})

    refs = await _resolve_entity_refs(db, org, [_entry("vendor", vendor_id)])

    assert refs[("vendor", vendor_id)] == ("Acme Hosting", str(vendor_id))


async def test_every_lookup_is_scoped_to_the_requesting_org():
    org = uuid.uuid4()
    entries = [
        _entry("risk_assessment", uuid.uuid4()),
        _entry("vendor", uuid.uuid4()),
        _entry("system", uuid.uuid4()),
        _entry("evidence_tracking", uuid.uuid4()),
        _entry("evidence_collection_task", uuid.uuid4()),
    ]
    db = _FakeSession({})

    await _resolve_entity_refs(db, org, entries)

    assert len(db.statements) == 5
    for stmt in db.statements:
        compiled = stmt.compile(compile_kwargs={"literal_binds": False})
        assert "organization_id" in str(compiled)
        assert org in compiled.params.values()


async def test_one_query_per_type_present():
    org = uuid.uuid4()
    entries = [_entry("vendor", uuid.uuid4()) for _ in range(3)]
    db = _FakeSession({})

    await _resolve_entity_refs(db, org, entries)

    assert len(db.statements) == 1


async def test_types_without_a_page_are_not_queried():
    db = _FakeSession({})

    refs = await _resolve_entity_refs(
        db, uuid.uuid4(), [_entry("api_key", uuid.uuid4()), _entry("scoped_control", uuid.uuid4())]
    )

    assert refs == {}
    assert db.statements == []


async def test_a_deleted_object_does_not_resolve():
    org = uuid.uuid4()
    gone = uuid.uuid4()
    db = _FakeSession({"systems": []})

    refs = await _resolve_entity_refs(db, org, [_entry("system", gone)])

    assert refs == {}
