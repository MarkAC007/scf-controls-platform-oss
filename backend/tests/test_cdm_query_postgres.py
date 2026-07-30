"""Tests for POST /cdm/query on the default Postgres FTS path (CDM v2, #709).

The v1 route proxied every query through Celery to a LightRAG service that
docker-compose.yml does not ship. These tests pin the v2 contract instead:

* the request is served synchronously — no broker, no 504 path;
* the response says which tier answered and whether that tier's hits can
  become mappings at all;
* truncation is visible (``candidates_shown`` vs ``candidates_total``);
* the two zero-result states are distinguished, because "you have uploaded
  nothing" and "your documents do not use this control's language" call for
  completely different actions from the user, and v1 rendered both as an
  empty list.

The control identifier below is a deliberate placeholder rather than a real
catalog ID: the session is faked, so the route only ever passes the string
through, and using a recognisable ID would imply these tests depend on
catalog contents they never read.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["ENABLE_CDM"] = "true"

import main  # noqa: E402
from auth import OrgMembership, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
CONTROL_ID = UUID("00000000-0000-0000-0000-0000000000c1")
SCF_ID = "TESTCONTROL"


class _Result:
    """Stands in for whichever accessor the route reaches for next."""

    def __init__(self, value: Any):
        self._value = value

    def one_or_none(self):
        return self._value

    def all(self):
        return self._value

    def mappings(self):
        return self

    def scalar(self):
        return self._value


class _FakeAsyncSession:
    def __init__(self, scripted: list[Any]):
        self._scripted = list(scripted)
        self.statements: list[Any] = []

    async def execute(self, stmt, params=None):
        self.statements.append((stmt, params))
        if not self._scripted:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(self._scripted.pop(0))


def _control_row(**overrides):
    row = {
        "id": CONTROL_ID,
        "scf_id": SCF_ID,
        "control_name": "Identity & Access Management",
        "control_description": "Does the organization govern access to its systems?",
    }
    row.update(overrides)
    return SimpleNamespace(**row)


def _chunk_row(**overrides):
    row = {
        "id": uuid4(),
        "cdm_document_id": uuid4(),
        "ordinal": 0,
        "heading": "4.2 Access Provisioning",
        "body": "All accounts are provisioned through a documented approval workflow.",
        "body_norm": "all accounts are provisioned through a documented approval workflow.",
        "char_start": 58,
        "char_end": 127,
        "ts_rank": 0.41,
        "matched_objectives": ["access is granted through an approval process"],
        "matched_objective_ranks": [0.41],
        "total_candidates": 1,
    }
    row.update(overrides)
    return row


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("ENABLE_CDM", "true")
    monkeypatch.delenv("CDM_RETRIEVAL_BACKEND", raising=False)
    monkeypatch.delenv("ENABLE_CDM_LIGHTRAG", raising=False)

    import api.cdm as cdm_router

    # A sentinel, not a convenience: reaching Celery here would mean the
    # default path still depends on a broker and a service that is not shipped.
    monkeypatch.setattr(
        cdm_router.tasks_cdm.query_cdm,
        "apply_async",
        lambda *a, **kw: pytest.fail("Postgres FTS path must not dispatch to Celery"),
    )

    membership = OrgMembership(
        organization_id=str(ORG_ID),
        role="viewer",
        user=SimpleNamespace(db_id=str(uuid4()), email="viewer@example.com"),
    )

    state: dict[str, _FakeAsyncSession] = {}

    main.app.dependency_overrides[require_tenant_cdm_enabled] = lambda: None
    main.app.dependency_overrides[require_org_viewer] = lambda: membership
    main.app.dependency_overrides[get_db] = lambda: state["session"]

    # No context manager: entering TestClient runs main's lifespan, whose
    # init_db/migration guard needs a live Postgres — absent in CI and not
    # required by these tests (the session is faked). Matches the convention
    # in every other backend test file.
    test_client = TestClient(main.app)
    yield test_client, state

    main.app.dependency_overrides.clear()


def _post(test_client, body=None):
    return test_client.post(
        f"/api/organizations/{ORG_ID}/cdm/query",
        json={"control_id": str(CONTROL_ID), "limit": 10, **(body or {})},
    )


def test_query_served_from_postgres_without_celery(client):
    test_client, state = client
    state["session"] = _FakeAsyncSession(
        [
            _control_row(),          # scoped control lookup
            [("objective one",)],    # assessment objectives
            [_chunk_row()],          # FTS rows
        ]
    )

    response = _post(test_client)

    assert response.status_code == 200
    body = response.json()
    assert body["retrieval_tier"] == "postgres_fts"
    assert body["can_produce_mappings"] is True
    assert body["candidates_shown"] == 1
    assert body["candidates_total"] == 1
    assert body["no_results_reason"] is None

    hit = body["hits"][0]
    # Offsets travel with the hit — that is the whole difference from v1,
    # where a hit could not be turned into a citation.
    assert hit["char_start"] == 58
    assert hit["char_end"] == 127
    assert hit["heading"] == "4.2 Access Provisioning"
    assert hit["matched_objectives"] == ["access is granted through an approval process"]


def test_truncation_is_visible_not_implied(client):
    """Showing 2 of 87 must not read the same as showing 2 of 2."""
    test_client, state = client
    state["session"] = _FakeAsyncSession(
        [
            _control_row(),
            [("objective one",)],
            [
                _chunk_row(total_candidates=87),
                _chunk_row(ordinal=1, ts_rank=0.22, total_candidates=87),
            ],
        ]
    )

    body = _post(test_client).json()

    assert body["candidates_shown"] == 2
    assert body["candidates_total"] == 87


def test_zero_hits_with_documents_reports_terminology_gap(client):
    test_client, state = client
    state["session"] = _FakeAsyncSession(
        [
            _control_row(),
            [("objective one",)],
            [],  # no FTS rows
            4,   # four parsed documents exist
        ]
    )

    body = _post(test_client).json()

    assert body["hits"] == []
    assert body["no_results_reason"] == "no_matching_passages"
    assert body["candidates_total"] == 0


def test_zero_hits_without_documents_reports_empty_corpus(client):
    test_client, state = client
    state["session"] = _FakeAsyncSession(
        [
            _control_row(),
            [("objective one",)],
            [],  # no FTS rows
            0,   # nothing ingested
        ]
    )

    body = _post(test_client).json()

    assert body["hits"] == []
    assert body["no_results_reason"] == "no_documents_ingested"


def test_explicit_query_text_replaces_control_language(client):
    """A user typing in the search box is searching their words, not the SCF's.

    The objectives lookup must be skipped entirely — folding control language
    into an explicit search silently returns results the user did not ask for.
    """
    test_client, state = client
    # Only two scripted results: if the route queried objectives it would
    # consume one extra and the FTS row would never be reached.
    state["session"] = _FakeAsyncSession([_control_row(), [_chunk_row()]])

    response = _post(test_client, {"query_text": "removable media encryption"})

    assert response.status_code == 200
    assert response.json()["candidates_shown"] == 1
    _stmt, params = state["session"].statements[-1]
    assert params["q0"] == "removable media encryption"


def test_control_without_any_query_text_returns_explicit_reason(client):
    test_client, state = client
    state["session"] = _FakeAsyncSession(
        [_control_row(scf_id=None, control_name=None, control_description=None)]
    )

    body = _post(test_client).json()

    assert body["hits"] == []
    assert body["no_results_reason"] == "control_has_no_query_text"
    assert body["candidates_total"] == 0
