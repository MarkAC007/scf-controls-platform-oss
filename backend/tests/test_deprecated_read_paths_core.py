"""Tests for the deprecated-control read-path consumers §4.4.1-4 (WP3a).

One test class per consumer:

1. Main listing (``/organizations/{org}/scoped-controls-paginated``): deprecated
   catalog rows stay in the listing ONLY where the org has data on them
   (``cat.status='active' OR sc.id IS NOT NULL``), badged via
   catalog_status/retired_in_version/superseded_by.
2. Scoping writes: creating a NEW scoped row for a deprecated control is
   refused with 409 + superseded_by hint; updating an EXISTING row stays
   allowed (no catalog gate on the update path).
3. ``/api/catalog`` lists: active-only by default, ``include_deprecated=true``
   opt-in; detail endpoints always resolve deprecated rows, badged.
4. Capability themes: the module-level metrics SQL filters to active catalog
   rows; the SQLAlchemy aggregates join the catalog on status='active'; the
   drill-down keeps org-data rows for deprecated controls, badged.

In-process FastAPI TestClient over scripted fake async sessions in the style
of ``test_control_composites_api.py`` / ``test_vendor_assessments_api.py`` (no
real Postgres). Control ids are opaque strings to these endpoints, so neutral
placeholders are used (same convention as ``test_scoping_service.py``). Where
the behaviour lives in a SQL predicate rather than in Python, the captured
statement is compiled and the predicate asserted directly — with all live rows
'active' this is also what proves "deprecated row without org data disappears".
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402 — imports the FastAPI app
import auth as auth_module  # noqa: E402
from auth import OrgMembership, require_auth  # noqa: E402
from database import get_db  # noqa: E402
from api import scoped_controls as scoped_controls_api  # noqa: E402
from api import capability_themes as capability_themes_api  # noqa: E402


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AUTH = {"Authorization": "Bearer test-key"}

# Neutral control-id placeholders (ids are opaque strings to the endpoints).
RETIRED_ID = "ctl-retired"
SUCCESSOR_ID = "ctl-successor"
ACTIVE_ID = "ctl-active"
RETIRED_VERSION = "2026.2"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def scalars(self) -> "_Result":
        return self

    def all(self) -> List[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None

    def one(self) -> Any:
        return self._items[0]


class _FakeAsyncSession:
    """Scripted session recording every statement for predicate assertions.

    ``responses`` entries are consumed in call order: a list feeds the next
    ``execute()``; a bare int/None feeds the next ``scalar()``.
    """

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.statements: List[Any] = []

    def _pop(self) -> Any:
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return self._responses.pop(0)

    async def execute(self, stmt, params=None) -> _Result:
        self.statements.append(stmt)
        return _Result(list(self._pop()))

    async def scalar(self, stmt) -> Any:
        self.statements.append(stmt)
        return self._pop()

    def add(self, _obj) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def commit(self) -> None:
        pass

    async def refresh(self, _obj) -> None:
        pass


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def _catalog_control(
    scf_id: str,
    status: str = "active",
    retired_in_version: str | None = None,
    superseded_by: str | None = None,
) -> SimpleNamespace:
    """All SCFCatalogControl attributes the listing/detail serializers touch."""
    return SimpleNamespace(
        scf_id=scf_id,
        scf_domain="DOM",
        control_name=f"Control {scf_id}",
        control_description="Description",
        control_question="Question?",
        validation_cadence="Annual",
        control_weighting=5,
        nist_csf_function="GV",
        evidence_requests=[],
        framework_mappings={},
        status=status,
        retired_in_version=retired_in_version,
        superseded_by=superseded_by,
        pptdf_people=True,
        pptdf_process=True,
        pptdf_technology=False,
        pptdf_data=False,
        pptdf_facility=False,
        cmm_level_0="", cmm_level_1="", cmm_level_2="",
        cmm_level_3="", cmm_level_4="", cmm_level_5="",
        biz_micro_small="", biz_small="", biz_medium="",
        biz_large="", biz_enterprise="",
        scrm_tier1_strategic="", scrm_tier2_operational="", scrm_tier3_tactical="",
        risk_codes=[],
        threat_codes=[],
        required_artifact_types=[],
        required_artifact_types_extracted_at=None,
        catalog_version="2026.1",
    )


@pytest.fixture
def client_factory(monkeypatch):
    """(responses, role='editor') -> (TestClient, fake session)."""
    app = main.app

    def _build(responses: List[Any], role: str = "editor"):
        session = _FakeAsyncSession(responses)

        async def _override_db():
            yield session

        def _fake_user():
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return user

        async def _fake_require_auth(credentials, db):
            return _fake_user()

        async def _fake_verify_org_membership(org_id, user, db, min_role="viewer"):
            return OrgMembership(
                user=user, organization_id=org_id, role=role, is_consultant=False
            )

        # Org-scoped endpoints resolve these through the auth module globals…
        monkeypatch.setattr(auth_module, "require_auth", _fake_require_auth)
        monkeypatch.setattr(auth_module, "verify_org_membership", _fake_verify_org_membership)
        # …while /api/catalog endpoints depend on require_auth directly.
        app.dependency_overrides[require_auth] = _fake_user
        app.dependency_overrides[get_db] = _override_db
        return TestClient(app), session

    yield _build
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


# ---------------------------------------------------------------------------
# Consumer 1 — main listing (scoped_controls-paginated)
# ---------------------------------------------------------------------------

class TestMainListing:
    def test_deprecated_row_with_org_data_stays_and_is_badged(self, client_factory):
        deprecated = _catalog_control(
            RETIRED_ID, status="deprecated",
            retired_in_version=RETIRED_VERSION, superseded_by=SUCCESSOR_ID,
        )
        rows = [(deprecated, True, "implemented", "kept for history")]
        client, _session = client_factory([1, rows])  # count, then page rows

        resp = client.get(
            f"/api/organizations/{ORG_ID}/scoped-controls-paginated", headers=AUTH
        )

        assert resp.status_code == 200
        item = resp.json()["controls"][0]
        assert item["scf_id"] == RETIRED_ID
        assert item["catalog_status"] == "deprecated"
        assert item["retired_in_version"] == RETIRED_VERSION
        assert item["superseded_by"] == SUCCESSOR_ID
        assert item["is_scoped"] is True

    def test_listing_predicate_drops_deprecated_without_org_data(self, client_factory):
        """The WHERE clause carries status='active' OR scoped_controls.id IS
        NOT NULL — a deprecated catalog row with no org row cannot match."""
        client, session = client_factory([0, []])

        resp = client.get(
            f"/api/organizations/{ORG_ID}/scoped-controls-paginated", headers=AUTH
        )

        assert resp.status_code == 200
        page_stmt = session.statements[1]
        compiled = _compiled(page_stmt)
        assert "scf_catalog_controls.status" in str(page_stmt.whereclause)
        assert "scoped_controls.id IS NOT NULL" in compiled
        assert " OR " in str(page_stmt.whereclause)


# ---------------------------------------------------------------------------
# Consumer 2 — scoping writes
# ---------------------------------------------------------------------------

class TestScopingWrites:
    def test_new_scope_of_deprecated_control_409_with_hint(self, client_factory):
        responses = [
            [],  # existing ScopedControl lookup -> none (so this is a CREATE)
            [SimpleNamespace(  # catalog lifecycle row
                status="deprecated", retired_in_version=RETIRED_VERSION,
                superseded_by=SUCCESSOR_ID,
            )],
        ]
        client, _session = client_factory(responses)

        resp = client.post(
            f"/api/organizations/{ORG_ID}/scoped-controls",
            json={"scf_id": RETIRED_ID, "selected": True},
            headers=AUTH,
        )

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["superseded_by"] == SUCCESSOR_ID
        assert detail["catalog_status"] == "deprecated"
        assert f"superseded by {SUCCESSOR_ID}" in detail["message"]

    def test_update_of_existing_row_for_deprecated_control_allowed(
        self, client_factory, monkeypatch
    ):
        now = datetime(2026, 8, 20, 12, 0, 0)
        existing = SimpleNamespace(
            id=uuid4(), organization_id=ORG_ID, scf_id=RETIRED_ID,
            selected=True, selection_reason=None,
            implementation_status="in_progress", priority=None, owner=None,
            assigned_to=None, maturity_level=None, target_date=None,
            completion_date=None, implementation_notes=None,
            related_documentation=None, custom_fields=None,
            control_weighting=None, validation_cadence=None,
            nist_csf_function=None, control_question=None,
            pptdf_people=False, pptdf_process=False, pptdf_technology=False,
            pptdf_data=False, pptdf_facility=False,
            created_at=now, updated_at=now, updated_by_user_id=None,
        )
        monkeypatch.setattr(scoped_controls_api, "log_entity_changes", AsyncMock())
        client, session = client_factory([[existing]])

        resp = client.post(
            f"/api/organizations/{ORG_ID}/scoped-controls",
            json={"scf_id": RETIRED_ID, "implementation_status": "implemented"},
            headers=AUTH,
        )

        assert resp.status_code == 201
        assert resp.json()["implementation_status"] == "implemented"
        # Update path never consulted the catalog: only the existing-row lookup ran.
        assert len(session.statements) == 1


# ---------------------------------------------------------------------------
# Consumer 3 — /api/catalog lists and details
# ---------------------------------------------------------------------------

class TestCatalogApi:
    def test_list_defaults_to_active_only(self, client_factory):
        client, session = client_factory([1, [_catalog_control(ACTIVE_ID)]])

        resp = client.get("/api/catalog/controls")

        assert resp.status_code == 200
        list_stmt = session.statements[1]
        assert "scf_catalog_controls.status" in str(list_stmt.whereclause)

    def test_list_include_deprecated_opt_in(self, client_factory):
        deprecated = _catalog_control(
            RETIRED_ID, status="deprecated",
            retired_in_version=RETIRED_VERSION, superseded_by=SUCCESSOR_ID,
        )
        client, session = client_factory([1, [deprecated]])

        resp = client.get("/api/catalog/controls?include_deprecated=true")

        assert resp.status_code == 200
        list_stmt = session.statements[1]
        assert "status" not in str(list_stmt.whereclause or "")
        item = resp.json()["controls"][0]
        assert item["catalog_status"] == "deprecated"
        assert item["superseded_by"] == SUCCESSOR_ID

    def test_detail_always_resolves_deprecated_badged(self, client_factory):
        deprecated = _catalog_control(
            RETIRED_ID, status="deprecated",
            retired_in_version=RETIRED_VERSION, superseded_by=SUCCESSOR_ID,
        )
        client, _session = client_factory([[deprecated]])

        resp = client.get(f"/api/catalog/controls/{RETIRED_ID}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["catalog_status"] == "deprecated"
        assert body["retired_in_version"] == RETIRED_VERSION
        assert body["superseded_by"] == SUCCESSOR_ID


# ---------------------------------------------------------------------------
# Consumer 4 — capability themes (SQL constants, aggregates, drill-down)
# ---------------------------------------------------------------------------

def _theme(theme_code: str = "CED") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(), theme_code=theme_code, name="Change & Deployment",
        description="d", ksi_reference="KSI-CED", icon="icon", display_order=1,
    )


class TestCapabilityThemes:
    def test_metrics_sql_constants_filter_to_active(self):
        for sql in (
            capability_themes_api._EVIDENCE_METRICS_SQL,
            capability_themes_api._EVIDENCE_METRICS_WINDOW_AWARE_SQL,
            capability_themes_api._EVIDENCE_METRICS_COMPOSITE_AWARE_SQL,
            capability_themes_api._EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL,
        ):
            body = str(sql)
            assert "ce.status = 'active'" in body
            assert "JOIN scf_catalog_controls c ON" in body
            assert "c.status = 'active'" in body

    def test_theme_aggregates_join_catalog_on_active(self, client_factory):
        # themes select, stats rows, evidence metrics rows
        client, session = client_factory([[_theme()], [], []])

        resp = client.get(
            f"/api/organizations/{ORG_ID}/capability-themes", headers=AUTH
        )

        assert resp.status_code == 200
        stats_stmt = session.statements[1]
        compiled = _compiled(stats_stmt)
        assert "JOIN scf_catalog_controls ON" in compiled
        assert "scf_catalog_controls.status" in compiled

    def test_drilldown_keeps_deprecated_org_rows_badged(self, client_factory):
        theme = _theme()
        row = SimpleNamespace(
            scf_id=RETIRED_ID, relevance="primary",
            control_name=f"Control {RETIRED_ID}",
            scf_domain="DOM", status="deprecated",
            retired_in_version=RETIRED_VERSION,
            superseded_by=SUCCESSOR_ID, selected=True,
            implementation_status="implemented", maturity_level=None,
        )
        # theme lookup, count scalar, page rows
        client, session = client_factory([[theme], 1, [row]])

        resp = client.get(
            f"/api/organizations/{ORG_ID}/capability-themes/CED/controls"
            "?scope_status=all",
            headers=AUTH,
        )

        assert resp.status_code == 200
        item = resp.json()["controls"][0]
        assert item["catalog_status"] == "deprecated"
        assert item["retired_in_version"] == RETIRED_VERSION
        assert item["superseded_by"] == SUCCESSOR_ID
        # Predicate keeps deprecated rows only where an org row exists.
        page_stmt = session.statements[2]
        compiled = _compiled(page_stmt)
        assert "scoped_controls.id IS NOT NULL" in compiled
        assert "scf_catalog_controls.status" in str(page_stmt.whereclause)
