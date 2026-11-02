"""GET /organizations/{org_id}/evidence/{evidence_id}/requiring-controls.

The catalogue's ``control_mappings`` is the SCF's global mapping; the
organisation's requirement is ``in_scope_evidence_requests`` (selected scoped
rows, via the control's ``evidence_requests``). The endpoint returns the
in-scope requirers as the primary list and the catalogue mapping alongside,
labelled, with the mapped-but-unscoped controls split out — so the web app and
the MCP read one definition instead of each computing their own.

Scripted fake async session in the style of the other backend tests (no real
Postgres): each ``execute`` pops the next result in order.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import auth as auth_module  # noqa: E402
from auth import OrgMembership, require_auth  # noqa: E402
from database import get_db  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AUTH = {"Authorization": "Bearer test-key"}
URL = f"/api/organizations/{ORG_ID}/evidence/E-AST-09/requiring-controls"


class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def fetchall(self):
        return list(self._items)

    def scalars(self):
        return self

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def all(self):
        return list(self._items)


class _FakeAsyncSession:
    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.statements: List[Any] = []

    async def execute(self, stmt, params=None) -> _Result:
        self.statements.append(stmt)
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(list(self._responses.pop(0)))


@pytest.fixture
def client_factory(monkeypatch):
    app = main.app

    def _build(responses: List[Any], role: str = "viewer"):
        session = _FakeAsyncSession(responses)

        async def _override_db():
            yield session

        def _fake_user():
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return user

        async def _fake_require_auth(request, credentials, db):
            user = _fake_user()
            request.state.user = user
            return user

        async def _fake_verify_org_membership(org_id, user, db, min_role="viewer"):
            return OrgMembership(user=user, organization_id=org_id, role=role, is_consultant=False)

        monkeypatch.setattr(auth_module, "require_auth", _fake_require_auth)
        monkeypatch.setattr(auth_module, "verify_org_membership", _fake_verify_org_membership)
        app.dependency_overrides[require_auth] = _fake_user
        app.dependency_overrides[get_db] = _override_db
        return TestClient(app), session

    yield _build
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


def _catalog(evidence_id="E-AST-09", mappings=("AST-37", "AST-38", "AST-39", "AST-40")):
    return SimpleNamespace(
        evidence_id=evidence_id,
        artifact_title="Computer Lifecycle Plan",
        artifact_description="Plan covering procurement through disposal",
        area_of_focus="Asset Management",
        status="active",
        control_mappings=list(mappings),
    )


def _scoped(scf_id, status="implemented", maturity="L3", override=None):
    return SimpleNamespace(
        scf_id=scf_id, implementation_status=status, maturity_level=maturity, scope_override=override
    )


class TestRequiringControlsIsScopeAware:
    def test_one_of_four_mapped_controls_in_scope(self, client_factory):
        """The E-AST-09 case: mapped to four controls, only AST-39 is in scope."""
        client, session = client_factory([
            [_catalog()],
            [("AST-39", ["E-AST-09", "E-AST-02"]), ("GOV-01", ["E-GOV-01"])],   # in_scope_evidence_requests
            [(_scoped("AST-39"), "Technology Lifecycle Management")],         # scoped + catalog name
        ])
        resp = client.get(URL, headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["evidence_id"] == "E-AST-09"
        assert body["artifact_title"] == "Computer Lifecycle Plan"
        assert body["required_by_scope"] is True
        assert body["required_by_in_scope_controls"] == [
            {
                "scf_id": "AST-39",
                "control_name": "Technology Lifecycle Management",
                "implementation_status": "implemented",
                "maturity_level": "L3",
                "scope_override": None,
            }
        ]
        assert body["catalog_control_mappings"] == ["AST-37", "AST-38", "AST-39", "AST-40"]
        assert body["catalog_mappings_not_in_scope"] == ["AST-37", "AST-38", "AST-40"]
        assert "selected" in body["scope_rule"]
        # The requirement query is the scoping service's, not a fresh definition.
        assert "scoped_controls" in str(session.statements[1])

    def test_not_applicable_control_is_listed_with_its_status(self, client_factory):
        client, _ = client_factory([
            [_catalog()],
            [("AST-39", ["E-AST-09"]), ("AST-40", ["E-AST-09"])],
            [(_scoped("AST-39"), "Lifecycle"), (_scoped("AST-40", status="not_applicable", maturity=None), "Disposal")],
        ])
        body = client.get(URL, headers=AUTH).json()
        statuses = {c["scf_id"]: c["implementation_status"] for c in body["required_by_in_scope_controls"]}
        assert statuses == {"AST-39": "implemented", "AST-40": "not_applicable"}
        assert body["catalog_mappings_not_in_scope"] == ["AST-37", "AST-38"]

    def test_nothing_in_scope_requires_it(self, client_factory):
        """Untracked and unrequired evidence still resolves — scope, not tracking, decides."""
        client, session = client_factory([
            [_catalog()],
            [("GOV-01", ["E-GOV-01"])],
        ])
        body = client.get(URL, headers=AUTH).json()
        assert body["required_by_scope"] is False
        assert body["required_by_in_scope_controls"] == []
        assert body["catalog_mappings_not_in_scope"] == ["AST-37", "AST-38", "AST-39", "AST-40"]
        assert len(session.statements) == 2  # no control lookup when nothing requires it

    def test_requirer_outside_the_catalog_mapping_is_still_listed(self, client_factory):
        """The two catalogue directions are seeded separately; the control's evidence_requests wins."""
        client, _ = client_factory([
            [_catalog(mappings=("AST-39",))],
            [("SEA-01", ["E-AST-09"]), ("AST-39", ["E-AST-09"])],
            [(_scoped("AST-39"), "Lifecycle"), (_scoped("SEA-01"), "Secure Engineering")],
        ])
        body = client.get(URL, headers=AUTH).json()
        assert [c["scf_id"] for c in body["required_by_in_scope_controls"]] == ["AST-39", "SEA-01"]
        assert body["catalog_mappings_not_in_scope"] == []

    def test_lowercase_id_resolves_and_missing_evidence_is_404(self, client_factory):
        client, _ = client_factory([[_catalog()], [], ])
        resp = client.get(URL.replace("E-AST-09", "e-ast-09"), headers=AUTH)
        assert resp.status_code == 200 and resp.json()["evidence_id"] == "E-AST-09"

        client, _ = client_factory([[]])
        resp = client.get(URL, headers=AUTH)
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Evidence E-AST-09 not found in catalog"
