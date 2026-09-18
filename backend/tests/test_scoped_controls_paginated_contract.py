"""Response contract for ``GET /organizations/{org}/scoped-controls-paginated``.

Why this file exists
--------------------
The paginated listing hand-builds its row dicts and shipped with no
``response_model=``. FastAPI enforces nothing without one, so the payload
silently omitted the organisation's ``maturity_level`` while the sibling
endpoint ``/scoped-controls`` (which DOES declare
``response_model=List[ScopedControlResponse]``) returned it. The scoping list
in the web client hardcoded an em dash as a result.

Two things are asserted here:

1. ``maturity_level`` is in the payload and carries the ORG's value — not the
   catalogue's recommended levels, which are a separate ``cmm_maturity``
   object. Confusing the two corrupts data while appearing to work, so both
   are asserted in the same test with different values.
2. The route now declares a response model, and that model covers every key
   the serializer emits. A response model DELETES undeclared keys, so a
   partial model would be a worse defect than none at all.

In-process FastAPI TestClient over a scripted fake async session, in the style
of ``test_deprecated_read_paths_core.py`` (no real Postgres). Control ids are
opaque strings to this endpoint, so neutral placeholders are used.
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

import main  # noqa: E402 — imports the FastAPI app
import auth as auth_module  # noqa: E402
from auth import OrgMembership, require_auth  # noqa: E402
from database import get_db  # noqa: E402
from schemas import ScopedControlListItem  # noqa: E402


ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AUTH = {"Authorization": "Bearer test-key"}
CONTROL_ID = "ctl-one"

ENDPOINT = f"/api/organizations/{ORG_ID}/scoped-controls-paginated"


class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def all(self) -> List[Any]:
        return list(self._items)


class _FakeAsyncSession:
    """Scripted session: a list feeds ``execute``, a scalar feeds ``scalar``."""

    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.statements: List[Any] = []

    def _pop(self) -> Any:
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return self._responses.pop(0)

    async def execute(self, stmt, params=None) -> _Result:
        self.statements.append(stmt)
        if "organization_assurance_policies" in str(stmt):
            return _Result([])
        return _Result(list(self._pop()))

    async def scalar(self, stmt) -> Any:
        self.statements.append(stmt)
        return self._pop()


def _catalog_control(scf_id: str = CONTROL_ID) -> SimpleNamespace:
    """Every SCFCatalogControl attribute the listing serializer touches."""
    return SimpleNamespace(
        scf_id=scf_id,
        scf_domain="DOM",
        control_name=f"Control {scf_id}",
        control_description="Description",
        control_question="Question?",
        validation_cadence="Annual",
        control_weighting=5,
        nist_csf_function="GV",
        evidence_requests=["a policy"],
        framework_mappings={"iso27001": ["A.5.1"]},
        status="active",
        retired_in_version=None,
        superseded_by=None,
        pptdf_people=True,
        pptdf_process=True,
        pptdf_technology=False,
        pptdf_data=False,
        pptdf_facility=False,
        # Catalogue RECOMMENDED levels — deliberately distinct strings so a
        # test cannot pass by reading the wrong field.
        cmm_level_0="catalogue L0 guidance",
        cmm_level_1="catalogue L1 guidance",
        cmm_level_2="catalogue L2 guidance",
        cmm_level_3="catalogue L3 guidance",
        cmm_level_4="catalogue L4 guidance",
        cmm_level_5="catalogue L5 guidance",
        biz_micro_small="micro", biz_small="small", biz_medium="medium",
        biz_large="large", biz_enterprise="enterprise",
        scrm_tier1_strategic="t1",
        scrm_tier2_operational="t2",
        scrm_tier3_tactical="t3",
        risk_codes=["R-1"],
        threat_codes=["T-1"],
    )


@pytest.fixture
def client_factory(monkeypatch):
    """(responses, role='viewer') -> (TestClient, fake session)."""
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
            return OrgMembership(
                user=user, organization_id=org_id, role=role, is_consultant=False
            )

        monkeypatch.setattr(auth_module, "require_auth", _fake_require_auth)
        monkeypatch.setattr(auth_module, "verify_org_membership", _fake_verify_org_membership)
        app.dependency_overrides[require_auth] = _fake_user
        app.dependency_overrides[get_db] = _override_db
        return TestClient(app), session

    yield _build
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


class TestMaturityLevelIsReturned:
    def test_org_maturity_level_is_in_the_payload(self, client_factory):
        """The org's own level reaches the client, and is not the catalogue's."""
        # (catalog, selected, implementation_status, selection_reason, maturity_level)
        rows = [(_catalog_control(), True, "implemented", "in scope", "L4")]
        client, _session = client_factory([1, rows])

        resp = client.get(ENDPOINT, headers=AUTH)

        assert resp.status_code == 200, resp.text
        item = resp.json()["controls"][0]
        assert "maturity_level" in item
        assert item["maturity_level"] == "L4"
        # …and the catalogue's recommendations are a DIFFERENT object that the
        # org value has not overwritten.
        assert item["cmm_maturity"]["level_4"] == "catalogue L4 guidance"

    def test_unscoped_control_reports_maturity_level_null(self, client_factory):
        """A catalogue control the org never scoped has no level — null, not absent."""
        rows = [(_catalog_control(), None, None, None, None)]
        client, _session = client_factory([1, rows])

        resp = client.get(ENDPOINT, headers=AUTH)

        assert resp.status_code == 200, resp.text
        item = resp.json()["controls"][0]
        assert item["maturity_level"] is None
        assert item["is_scoped"] is False

    def test_maturity_level_is_selected_from_the_scoped_control_table(self, client_factory):
        """Guards the field pin: the query reads scoped_controls.maturity_level.

        `cmm_maturity` is built from scf_catalog_controls.cmm_level_*; reading
        the org level off the catalogue would be a silent data error.
        """
        client, session = client_factory([0, []])

        resp = client.get(ENDPOINT, headers=AUTH)

        assert resp.status_code == 200
        page_stmt = session.statements[1]
        compiled = str(page_stmt.compile())
        assert "scoped_controls.maturity_level" in compiled


class TestResponseModelCoversEveryEmittedKey:
    """A response model deletes what it does not declare.

    This is the guard that makes the fix durable: if someone adds a key to the
    serializer and not to the model, the key would vanish from the payload
    with no error. Comparing the rendered payload against the raw serializer
    output catches exactly that.
    """

    def test_no_emitted_key_is_dropped_by_the_response_model(self, client_factory):
        rows = [(_catalog_control(), True, "implemented", "in scope", "L4")]
        client, _session = client_factory([1, rows])

        resp = client.get(ENDPOINT, headers=AUTH)

        assert resp.status_code == 200, resp.text
        item = resp.json()["controls"][0]

        # Every key the endpoint's serializer builds, enumerated from source.
        expected_keys = {
            "scf_id", "scf_domain", "control_name", "control_description",
            "control_question", "validation_cadence", "control_weighting",
            "nist_csf_function", "evidence_requests", "framework_mappings",
            "catalog_status", "retired_in_version", "superseded_by",
            "is_scoped", "selected", "implementation_status",
            "selection_reason", "out_of_scope_justification",
            "scoped_control_id", "priority", "scope_override",
            "scope_override_reason", "scope_override_set_at", "maturity_level", "pptdf_applicability",
            "cmm_maturity", "business_size_guidance", "scrm_focus",
            "risk_threat_mapping",
        }
        missing = expected_keys - set(item)
        assert not missing, f"response model dropped: {sorted(missing)}"
        # The model is the contract, so it must declare exactly these and no
        # phantom extras either.
        assert set(ScopedControlListItem.model_fields) == expected_keys

        # Nested objects survive whole, not flattened to null.
        assert item["pptdf_applicability"]["people"] is True
        assert item["framework_mappings"] == {"iso27001": ["A.5.1"]}
        assert item["risk_threat_mapping"]["threat_codes"] == ["T-1"]
        assert item["catalog_status"] == "active"

    def test_route_declares_a_response_model(self, client_factory):
        """Without one, FastAPI enforces nothing and the next omission is silent.

        Read off the generated OpenAPI document rather than ``app.routes``:
        included routers are not flattened onto ``app.routes`` in this FastAPI
        version, so the route object is not reachable there.
        """
        schema = main.app.openapi()
        path = f"/api/organizations/{{org_id}}/scoped-controls-paginated"
        operation = schema["paths"][path]["get"]
        content = operation["responses"]["200"]["content"]["application/json"]
        ref = content["schema"]["$ref"]
        assert ref.endswith("ScopedControlsPaginatedResponse"), ref

        # And the declared row schema carries maturity_level.
        row_schema = schema["components"]["schemas"]["ScopedControlListItem"]
        assert "maturity_level" in row_schema["properties"]
