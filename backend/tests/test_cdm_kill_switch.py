"""CDM retirement phase 1 — the kill switch the UI reads.

The Control Documents Mapper is being retired (``docs/plans/cdm-retirement.md``).
The backend has gated its *routes* on the tenant override, else ``ENABLE_CDM``,
since slice 7 — but nothing told the frontend. The sidebar entries and the
per-control "Knowledge Base" tab rendered regardless of the flag, so a
deployment with CDM off showed a surface whose every call 404s, and one route
(``GET .../cdm/document-map``) shipped with no gate at all.

These tests guard the reads that close that gap:

* ``GET/PATCH /organizations/{org_id}/settings`` carry ``cdm_enabled``, the
  per-org answer resolved by ``services.cdm_tenancy.get_tenant_cdm_enabled``
  (explicit tenant value wins, else the deployment's ``ENABLE_CDM``). This is
  what the webclient hides the module on.
* Every route under ``/cdm`` — the nineteen in ``api/cdm.py`` and the one in
  ``api/cdm_document_map.py`` — declares ``require_tenant_cdm_enabled``, and
  declares it *after* the membership check so a disabled module never turns
  an unauthenticated 401 into a 404 that reveals the org exists.

``cdm_enabled`` is deliberately **read-only**. A settings form that could turn
CDM on would let a tenant re-enable a module that is on its way out, so the
update schema does not accept the field, and one test below proves a PATCH
carrying it changes nothing.

The settings routes are called directly rather than through ``TestClient``:
``require_org_role`` is a factory that returns a fresh closure per call, so
``app.dependency_overrides`` cannot be keyed on it from out here. Nothing in
this file touches a database or the network.

``main`` is imported for its side effects: the ORM registry is only complete
once every model module has loaded, and this file must pass on its own, not
only after ``test_cdm_tenancy.py`` has happened to import it first.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Optional
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402,F401  (ORM registry — see module docstring)
from api import cdm as cdm_api  # noqa: E402
from api import cdm_document_map as cdm_document_map_api  # noqa: E402
from api import organizations as organizations_api  # noqa: E402
from models import Organization  # noqa: E402
from schemas import OrganizationSettingsUpdate  # noqa: E402
from services.cdm_tenancy import require_tenant_cdm_enabled  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000042")


# ───────────────────────── Test doubles ─────────────────────────


class _OrgSession:
    """Async session double answering the two queries a settings read makes.

    The route issues ``select(Organization)`` for the row it renders;
    ``get_tenant_cdm_enabled`` then issues ``select(Organization.settings)``
    for the flag. Both resolve through ``scalar_one_or_none``, so the two are
    told apart by what the statement selects rather than by call order — an
    ordered script would silently return the wrong object the day a caller
    adds a query, and the assertion it broke would point at the wrong thing.

    The settings query reads ``organization.settings`` live, so a PATCH that
    has already mutated the row resolves the flag against the mutated state,
    exactly as the real session would after the write.
    """

    def __init__(self, organization: Organization):
        self.organization = organization
        self.commits = 0

    @staticmethod
    def _selects_settings_column(stmt: Any) -> bool:
        descriptions = getattr(stmt, "column_descriptions", None) or []
        return bool(descriptions) and descriptions[0].get("name") == "settings"

    async def execute(self, stmt: Any):
        value = (
            self.organization.settings
            if self._selects_settings_column(stmt)
            else self.organization
        )

        class _Result:
            def scalar_one_or_none(self_inner):
                return value

            def scalar(self_inner):
                return value

        return _Result()

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, _instance: Any) -> None:
        # Nothing to reload: the double never round-trips through a database,
        # so the in-memory instance is already the post-commit state.
        return None


@dataclass
class _FakeUser:
    db_id: str
    email: str = "admin@example.com"


@dataclass
class _FakeMembership:
    """Stands in for auth.OrgMembership.

    Only ``user.db_id`` is read by the settings routes — the role check itself
    happened in the dependency these tests bypass.
    """

    user: _FakeUser
    organization_id: UUID
    role: str = "admin"
    is_consultant: bool = False


def make_organization(settings: Optional[dict] = None) -> Organization:
    return Organization(
        id=ORG_ID,
        name="Acme Ltd",
        slug="acme-ltd",
        settings=settings if settings is not None else {},
    )


def make_request() -> Request:
    """A minimal real Request — the audit helpers read headers and state."""
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": f"/api/organizations/{ORG_ID}/settings",
            "headers": [],
            "query_string": b"",
        }
    )


def make_membership() -> _FakeMembership:
    return _FakeMembership(user=_FakeUser(db_id=str(uuid4())), organization_id=ORG_ID)


async def get_settings(session: _OrgSession):
    return await organizations_api.get_organization_settings(
        org_id=ORG_ID,
        membership=make_membership(),  # type: ignore[arg-type]
        db=session,  # type: ignore[arg-type]
    )


async def patch_settings(session: _OrgSession, update: OrganizationSettingsUpdate):
    return await organizations_api.update_organization_settings(
        org_id=ORG_ID,
        settings_data=update,
        request=make_request(),
        membership=make_membership(),  # type: ignore[arg-type]
        db=session,  # type: ignore[arg-type]
    )


@pytest.fixture
def no_audit_writes(monkeypatch):
    """The PATCH route writes an audit row; that path is not under test here."""
    logger = AsyncMock(return_value=None)
    monkeypatch.setattr(organizations_api, "log_entity_changes", logger)
    return logger


# ───────────────────── GET settings — flag resolution ─────────────────────


class TestSettingsReportsCdmEnabled:
    @pytest.mark.asyncio
    async def test_defaults_false_when_env_unset_and_no_tenant_override(
        self, monkeypatch
    ):
        """The shape every default install sees: CDM off, so the UI hides it."""
        monkeypatch.delenv("ENABLE_CDM", raising=False)
        response = await get_settings(_OrgSession(make_organization()))
        assert response.cdm_enabled is False

    @pytest.mark.asyncio
    async def test_true_when_env_enables_cdm(self, monkeypatch):
        monkeypatch.setenv("ENABLE_CDM", "true")
        response = await get_settings(_OrgSession(make_organization()))
        assert response.cdm_enabled is True

    @pytest.mark.asyncio
    async def test_tenant_false_wins_over_env_true(self, monkeypatch):
        """An org that opted out stays out on a deployment that has CDM on."""
        monkeypatch.setenv("ENABLE_CDM", "true")
        session = _OrgSession(make_organization({"cdm_enabled": False}))
        response = await get_settings(session)
        assert response.cdm_enabled is False

    @pytest.mark.asyncio
    async def test_tenant_true_wins_over_env_unset(self, monkeypatch):
        monkeypatch.delenv("ENABLE_CDM", raising=False)
        session = _OrgSession(make_organization({"cdm_enabled": True}))
        response = await get_settings(session)
        assert response.cdm_enabled is True

    @pytest.mark.asyncio
    async def test_existing_fields_are_unchanged(self, monkeypatch):
        """Adding the flag must not disturb what the settings form round-trips."""
        monkeypatch.delenv("ENABLE_CDM", raising=False)
        session = _OrgSession(
            make_organization(
                {
                    "owner_teams": ["Security"],
                    "is_trust_portal_enabled": True,
                    "trust_portal_description": "Public posture",
                    "industry": "Manufacturing",
                }
            )
        )
        response = await get_settings(session)
        assert response.owner_teams == ["Security"]
        assert response.is_trust_portal_enabled is True
        assert response.trust_portal_description == "Public posture"
        assert response.name == "Acme Ltd"
        assert response.industry == "Manufacturing"


# ───────────────────── PATCH settings — read-only proof ─────────────────────


class TestPatchCarriesFlagWithoutAcceptingIt:
    @pytest.mark.asyncio
    async def test_patch_response_carries_cdm_enabled(
        self, monkeypatch, no_audit_writes
    ):
        """Without this the UI would hide CDM until the next full settings GET."""
        monkeypatch.setenv("ENABLE_CDM", "true")
        session = _OrgSession(make_organization())
        response = await patch_settings(
            session, OrganizationSettingsUpdate(industry="Healthcare")
        )
        assert response.cdm_enabled is True
        assert response.industry == "Healthcare"
        assert session.commits == 1

    def test_update_schema_rejects_the_field(self):
        """`cdm_enabled` is not an input. Pydantic drops the unknown key."""
        update = OrganizationSettingsUpdate(**{"cdm_enabled": True})
        assert not hasattr(update, "cdm_enabled")
        assert update.model_dump(exclude_unset=True) == {}

    @pytest.mark.asyncio
    async def test_patch_body_cannot_write_the_flag_into_settings(
        self, monkeypatch, no_audit_writes
    ):
        """A client that posts the flag must not enable a module it cannot run."""
        monkeypatch.delenv("ENABLE_CDM", raising=False)
        organization = make_organization()
        session = _OrgSession(organization)

        response = await patch_settings(
            session,
            OrganizationSettingsUpdate(**{"cdm_enabled": True, "industry": "Retail"}),
        )

        assert "cdm_enabled" not in organization.settings
        assert organization.settings["industry"] == "Retail"
        assert response.cdm_enabled is False


# ───────────────────── Every CDM route is gated ─────────────────────


def _cdm_routes():
    """All HTTP routes under ``/cdm`` from both CDM routers.

    A sweep rather than a per-route test: the document-map route was added
    on its own router after the gate existed and shipped without it. A test
    that named routes individually would have missed the next one too.
    """
    routes = []
    for router in (cdm_api.router, cdm_document_map_api.router):
        for route in router.routes:
            if "/cdm" in getattr(route, "path", ""):
                routes.append(route)
    assert routes, "expected CDM routes on the routers"
    return routes


def _dependency_calls(route):
    return [dep.call for dep in route.dependant.dependencies]


class TestEveryCdmRouteIsGated:
    def test_route_count_matches_the_retirement_audit(self):
        """19 in api/cdm.py + 1 in api/cdm_document_map.py (design doc §4)."""
        assert len(_cdm_routes()) == 20

    @pytest.mark.parametrize(
        "route", _cdm_routes(), ids=lambda r: f"{'|'.join(sorted(r.methods))} {r.path}"
    )
    def test_route_depends_on_tenant_gate(self, route):
        assert require_tenant_cdm_enabled in _dependency_calls(route), (
            f"{route.path} would answer with CDM disabled"
        )

    def test_document_map_checks_membership_before_the_gate(self):
        """The gate 404s. An anonymous caller must still meet 401 first.

        Declaring the gate before ``require_org_role`` would let anyone probe
        whether an org id exists and has CDM off. The document-map route is
        the one this PR gates; it is asserted explicitly.
        """
        route = next(
            r for r in cdm_document_map_api.router.routes
            if r.path.endswith("/cdm/document-map")
        )
        calls = _dependency_calls(route)
        gate_at = calls.index(require_tenant_cdm_enabled)
        assert gate_at > 0, "gate must not be the first dependency"
        before = calls[:gate_at]
        assert any(getattr(c, "__qualname__", "").startswith("require_org_role") for c in before), (
            "membership check must precede the tenant gate"
        )
