"""API tests for the ControlAssessmentComposite read endpoints (M3 PR 2, #575).

Covers ISC-15..17 and §8a: single GET, list GET (cursor pagination, status
and domain filters, ETag/304), RBAC. Uses FastAPI TestClient with
``app.dependency_overrides`` so the suite stays in-process and dependency-
free; the database session is a hand-rolled ``_FakeAsyncSession`` that
scripts results per query, mirroring the lightweight style of
``test_composite_service.py`` (PR 1).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402 — imports the FastAPI app
from auth import OrgMembership, require_org_viewer  # noqa: E402
from database import get_db  # noqa: E402
from api import control_composites  # noqa: E402


# ---------------------------------------------------------------------------
# Lightweight fakes — composite rows + a scripted AsyncSession.
# ---------------------------------------------------------------------------

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG_ID = UUID("00000000-0000-0000-0000-0000000000ff")


def _composite(
    scf_id: str,
    status: str = "partial",
    score: Optional[float] = 62.5,
    *,
    organization_id: UUID = ORG_ID,
    included_evidence_ids: Optional[List[str]] = None,
    included_window_ids: Optional[List[UUID]] = None,
    mandatory_gaps: Optional[List[Dict[str, Any]]] = None,
    computation_version: int = 1,
    computed_at: Optional[datetime] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        scf_id=scf_id,
        composite_status=status,
        composite_score=Decimal(str(score)) if score is not None else None,
        included_evidence_ids=included_evidence_ids or [],
        included_window_ids=included_window_ids or [],
        mandatory_gaps=mandatory_gaps or [],
        computation_version=computation_version,
        computed_at=computed_at or datetime(2026, 5, 9, 12, 0, 0),
    )


def _window(
    evidence_id: str,
    status: str = "partial",
    relevance: Optional[float] = 50.0,
    window_id: Optional[UUID] = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=window_id or uuid4(),
        evidence_id=evidence_id,
        status=status,
        relevance_score=Decimal(str(relevance)) if relevance is not None else None,
    )


class _ScalarResult:
    def __init__(self, items: List[Any]):
        self._items = items

    def all(self) -> List[Any]:
        return list(self._items)

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None


class _RowResult:
    """For tuple-result queries (the windows JOIN returns named tuples)."""

    def __init__(self, items: List[Any]):
        self._items = items

    def all(self) -> List[Any]:
        return list(self._items)

    def scalars(self) -> "_ScalarResult":
        return _ScalarResult(self._items)

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None


class _FakeAsyncSession:
    """Scripted async session — pop the next pre-arranged result per call.

    We don't introspect the SQL — we just hand back results in the order
    tests register them. Each test sets ``responses`` to a list whose order
    matches the handler's query order:
      single GET: [composite-row, window-rows]
      list   GET: [composite-rows, [window-rows for row 1], [windows row 2]…]
    """

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.calls = 0

    async def execute(self, _stmt) -> Any:  # noqa: D401
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        self.calls += 1
        nxt = self._responses.pop(0)
        if isinstance(nxt, _RowResult):
            return nxt
        # Convenience: bare list → wrap as _RowResult
        return _RowResult(list(nxt))


# ---------------------------------------------------------------------------
# Test client fixture with dep overrides.
# ---------------------------------------------------------------------------


@pytest.fixture
def client_factory():
    """Returns a builder ``(session, *, role='viewer', org=ORG_ID) -> TestClient``.

    The ``session`` argument is the pre-loaded ``_FakeAsyncSession``; role
    controls the membership returned by the auth override (use ``None`` to
    simulate forbidden access by raising 403).
    """
    app = main.app

    # Reset cache between tests so list-endpoint tests don't leak state.
    control_composites._cache_get = control_composites._cache_get  # noqa: SLF001
    if hasattr(control_composites, "_list_cache"):
        with control_composites._list_cache_lock:  # type: ignore[attr-defined]
            control_composites._list_cache.clear()  # type: ignore[attr-defined]
    if hasattr(control_composites, "_list_cache_shim"):
        control_composites._list_cache_shim._store.clear()  # type: ignore[attr-defined]

    def _build(
        session: _FakeAsyncSession,
        *,
        role: Optional[str] = "viewer",
        org: UUID = ORG_ID,
    ) -> TestClient:
        async def _override_db():
            yield session

        async def _override_auth():
            if role is None:
                raise HTTPException(status_code=403, detail="forbidden")
            user = MagicMock()
            user.db_id = uuid4()
            user.email = "test@example.com"
            return OrgMembership(
                user=user, organization_id=org, role=role, is_consultant=False
            )

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[require_org_viewer] = _override_auth
        return TestClient(app)

    yield _build

    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_org_viewer, None)


# ---------------------------------------------------------------------------
# Single GET — ISC-15.
# ---------------------------------------------------------------------------


def test_single_get_returns_404_when_no_row(client_factory):
    """ISC-3 / ISC-24: single GET returns 404 when no composite exists."""
    session = _FakeAsyncSession([_RowResult([])])
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/BCD-99/assessment-composite"
    )
    assert resp.status_code == 404
    assert "No composite" in resp.json()["detail"]


def test_single_get_returns_full_payload_with_windows(client_factory):
    """ISC-4 / ISC-5: 200 response carries full payload + JOIN-on-read windows."""
    win_id = uuid4()
    row = _composite(
        scf_id="BCD-11",
        status="partial",
        score=62.5,
        included_evidence_ids=["E-BCM-11", "E-BCM-12"],
        included_window_ids=[win_id],
        mandatory_gaps=[
            {"evidence_id": "E-BCM-15", "reason": "missing_window"},
            {
                "evidence_id": "E-BCM-11",
                "reason": "missing",
                "artifact_type": "restore_test_result",
            },
        ],
    )
    win = _window("E-BCM-11", "insufficient", 25.0, window_id=win_id)
    session = _FakeAsyncSession([_RowResult([row]), _RowResult([win])])
    client = client_factory(session)

    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/BCD-11/assessment-composite"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scf_id"] == "BCD-11"
    assert body["composite_status"] == "partial"
    assert body["composite_score"] == 62.5
    assert body["included_evidence_ids"] == ["E-BCM-11", "E-BCM-12"]
    assert body["missing_evidence_ids"] == ["E-BCM-15"]
    assert len(body["mandatory_gaps"]) == 2
    assert len(body["windows"]) == 1
    assert body["windows"][0]["evidence_id"] == "E-BCM-11"
    assert body["windows"][0]["status"] == "insufficient"
    assert body["windows"][0]["relevance_score"] == 25.0
    # ETag header set per ISC-6
    assert resp.headers.get("ETag", "").startswith('W/"')


def test_single_get_etag_returns_304_on_match(client_factory):
    """ISC-6 / ISC-7: ETag honoured for If-None-Match."""
    row = _composite(scf_id="BCD-11", included_window_ids=[])
    # First call to learn the ETag — script: composite, then no windows.
    session = _FakeAsyncSession([_RowResult([row]), _RowResult([])])
    client = client_factory(session)
    first = client.get(
        f"/api/organizations/{ORG_ID}/controls/BCD-11/assessment-composite"
    )
    assert first.status_code == 200
    etag = first.headers["ETag"]

    # Second call with If-None-Match: handler short-circuits BEFORE windows fetch
    # so we only need to script the composite row.
    session2 = _FakeAsyncSession([_RowResult([row])])
    client2 = client_factory(session2)
    second = client2.get(
        f"/api/organizations/{ORG_ID}/controls/BCD-11/assessment-composite",
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304
    assert second.headers["ETag"] == etag
    # No body on 304
    assert second.text == ""


def test_single_get_rbac_forbidden(client_factory):
    """ISC-8 / §8a: viewer role enforced via require_org_viewer dependency."""
    session = _FakeAsyncSession([])
    client = client_factory(session, role=None)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/BCD-11/assessment-composite"
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# List GET — ISC-16, ISC-17.
# ---------------------------------------------------------------------------


def test_list_get_returns_items_with_etag(client_factory):
    """ISC-9 / ISC-16: list GET returns multiple composites + ETag."""
    rows = [
        _composite(scf_id="BCD-11", status="insufficient", score=15.0),
        _composite(scf_id="BCD-12", status="partial", score=62.5),
    ]
    session = _FakeAsyncSession(
        [
            _RowResult(rows),
            _RowResult([]),  # windows for row 1
            _RowResult([]),  # windows for row 2
        ]
    )
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites"
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["items"][0]["scf_id"] == "BCD-11"
    assert body["next_cursor"] is None
    assert resp.headers.get("ETag", "").startswith('W/"')


def test_list_get_status_filter_passes_to_query(client_factory):
    """ISC-10: status filter accepted; unknown values rejected."""
    # Valid — only one row is scripted to confirm pipeline works.
    rows = [_composite(scf_id="BCD-11", status="insufficient", score=15.0)]
    session = _FakeAsyncSession([_RowResult(rows), _RowResult([])])
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites",
        params={"status": "insufficient"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1

    # Invalid — 400 with helpful message.
    session2 = _FakeAsyncSession([])
    client2 = client_factory(session2)
    bad = client2.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites",
        params={"status": "wibble"},
    )
    assert bad.status_code == 400
    assert "Unknown status" in bad.json()["detail"]


def test_list_get_cursor_round_trips(client_factory):
    """ISC-14 / ISC-28: cursor round-trip pagination — no duplicates, no gaps."""
    # Page 1: limit=2, returns 3 rows (the +1 sentinel) → first 2 items + cursor.
    page1_rows = [
        _composite(scf_id="BCD-11", status="insufficient", score=15.0),
        _composite(scf_id="BCD-12", status="insufficient", score=20.0),
        _composite(scf_id="BCD-13", status="partial", score=70.0),
    ]
    # 2 items returned → 2 windows queries
    session1 = _FakeAsyncSession(
        [_RowResult(page1_rows), _RowResult([]), _RowResult([])]
    )
    client = client_factory(session1)
    r1 = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites",
        params={"limit": 2},
    )
    assert r1.status_code == 200
    body1 = r1.json()
    assert [i["scf_id"] for i in body1["items"]] == ["BCD-11", "BCD-12"]
    assert body1["next_cursor"] is not None

    # Page 2: only 1 row left; +1 sentinel not present → no further cursor.
    page2_rows = [_composite(scf_id="BCD-13", status="partial", score=70.0)]
    session2 = _FakeAsyncSession([_RowResult(page2_rows), _RowResult([])])
    client2 = client_factory(session2)
    r2 = client2.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites",
        params={"limit": 2, "cursor": body1["next_cursor"]},
    )
    assert r2.status_code == 200
    body2 = r2.json()
    assert [i["scf_id"] for i in body2["items"]] == ["BCD-13"]
    assert body2["next_cursor"] is None


def test_list_get_rejects_malformed_cursor(client_factory):
    """ISC-15: malformed cursor → 400."""
    session = _FakeAsyncSession([])
    client = client_factory(session)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites",
        params={"cursor": "this-is-not-base64-json!"},
    )
    assert resp.status_code == 400
    assert "Malformed cursor" in resp.json()["detail"]


def test_list_get_etag_returns_304(client_factory):
    """ISC-16 / ISC-17 / ISC-29: page ETag honoured for If-None-Match."""
    rows = [_composite(scf_id="BCD-11", status="partial", score=62.5)]
    session = _FakeAsyncSession([_RowResult(rows), _RowResult([])])
    client = client_factory(session)
    first = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites"
    )
    assert first.status_code == 200
    etag = first.headers["ETag"]

    # Second call uses cache (no DB needed); should still 304.
    session2 = _FakeAsyncSession([])
    client2 = client_factory(session2)
    # Note: client_factory clears the cache between builds — invoke same client.
    second = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites",
        headers={"If-None-Match": etag},
    )
    assert second.status_code == 304
    assert second.headers["ETag"] == etag


def test_list_get_rbac_forbidden(client_factory):
    """ISC-19 / §8a: viewer required for the list endpoint too."""
    session = _FakeAsyncSession([])
    client = client_factory(session, role=None)
    resp = client.get(
        f"/api/organizations/{ORG_ID}/controls/assessment-composites"
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Helper unit tests — cursor + ETag primitives.
# ---------------------------------------------------------------------------


def test_cursor_encode_decode_round_trip():
    """Encode/decode survives an opaque round-trip with Decimal preservation."""
    enc = control_composites._encode_cursor(
        band=2, score=Decimal("62.50"), scf_id="BCD-11"
    )
    band, neg_score, scf_id = control_composites._decode_cursor(enc)
    assert band == 2
    assert neg_score == Decimal("-62.50")
    assert scf_id == "BCD-11"


def test_cursor_decode_rejects_garbage():
    """Decoder raises 400 not silent failure on malformed input."""
    with pytest.raises(HTTPException) as exc:
        control_composites._decode_cursor("@@notb64@@")
    assert exc.value.status_code == 400


def test_cursor_handles_null_score():
    enc = control_composites._encode_cursor(band=4, score=None, scf_id="X-1")
    band, neg_score, scf_id = control_composites._decode_cursor(enc)
    assert band == 4
    assert neg_score is None
    assert scf_id == "X-1"


def test_etag_for_row_stable():
    """Same inputs → same ETag across calls (process-stable)."""
    row = _composite(
        scf_id="BCD-11",
        computed_at=datetime(2026, 5, 9, 12, 0, 0),
        computation_version=1,
    )
    e1 = control_composites._etag_for_row(row)
    e2 = control_composites._etag_for_row(row)
    assert e1 == e2
    assert e1.startswith('W/"')


def test_etag_match_strips_weak_prefix():
    """If-None-Match works whether client sends ``W/"x"`` or ``"x"``."""
    e = 'W/"abcdef"'
    assert control_composites._etag_match('W/"abcdef"', e) is True
    assert control_composites._etag_match('"abcdef"', e) is True
    assert control_composites._etag_match("*", e) is True
    assert control_composites._etag_match('W/"deadbeef"', e) is False
    assert control_composites._etag_match(None, e) is False
