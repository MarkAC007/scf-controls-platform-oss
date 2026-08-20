"""Contract test for the catalog upgrade API surface (WP-C, plan §4.5).

Pins every §4.5 path, method, and response model. Downstream WPs fill the 501
stubs but must not change this surface; a failure here means a contract drift
that requires an explicit contract-change WP.

Note: this app wraps include_router in lazy _IncludedRouter objects, so
app.routes is NOT walkable for APIRoutes. Path/model presence is asserted
against app.openapi(); exact response-model class identity and auth
dependencies are asserted by walking the two routers directly (whose routes
are plain APIRoutes).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.routing import APIRoute

import schemas_catalog_upgrade as contracts


# (path, method, response_model) — the frozen §4.5 surface as mounted under /api
PLATFORM_ROUTES = [
    ("/api/admin/catalog/upgrade", "POST", contracts.UpgradeUploadResponse),
    ("/api/admin/catalog/upgrade/runs", "GET", contracts.PlatformImportRunsListResponse),
    ("/api/admin/catalog/upgrade/runs/{run_id}", "GET", contracts.PlatformImportRunDetail),
    ("/api/admin/catalog/upgrade/runs/{run_id}/diff", "GET", contracts.DiffPageResponse),
    ("/api/admin/catalog/upgrade/runs/{run_id}/pairings", "PUT", contracts.PairingsUpdateResponse),
    ("/api/admin/catalog/upgrade/runs/{run_id}/apply", "POST", contracts.UpgradeApplyResponse),
    ("/api/admin/catalog/upgrade/runs/{run_id}/cancel", "POST", contracts.UpgradeCancelResponse),
    ("/api/admin/catalog/upgrade/runs/{run_id}/revert", "POST", contracts.UpgradeRevertResponse),
    ("/api/admin/catalog/tenants", "GET", contracts.TenantsBoardResponse),
    ("/api/admin/catalog/controls/{scf_id}/superseded-by", "PATCH", contracts.SupersededByPatchResponse),
]

ORG_ROUTES = [
    ("/api/organizations/{org_id}/catalog-reconciliation/status", "GET", contracts.OrgCatalogStatusResponse),
    ("/api/organizations/{org_id}/catalog-reconciliation/preview", "POST", contracts.ReconciliationPreviewResponse),
    ("/api/organizations/{org_id}/catalog-reconciliation/runs", "GET", contracts.OrgReconciliationRunsListResponse),
    ("/api/organizations/{org_id}/catalog-reconciliation/runs/{run_id}", "GET", contracts.OrgReconciliationRunDetail),
    ("/api/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/actions", "PUT", contracts.ReconciliationActionsUpdateResponse),
    ("/api/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/apply", "POST", contracts.ReconciliationApplyResponse),
    ("/api/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/rollback", "POST", contracts.ReconciliationRollbackResponse),
    ("/api/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/cancel", "POST", contracts.ReconciliationCancelResponse),
    ("/api/organizations/{org_id}/catalog-changelog", "GET", contracts.OrgChangelogResponse),
]

ALL_ROUTES = PLATFORM_ROUTES + ORG_ROUTES


def _router_routes_by_full_path():
    """Walk the two routers directly (plain APIRoutes) keyed by mounted path."""
    from api import catalog_reconciliation, catalog_upgrade_admin

    routes = {}
    for module in (catalog_upgrade_admin, catalog_reconciliation):
        for route in module.router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in route.methods:
                routes[("/api" + route.path, method)] = route
    return routes


def test_every_contract_path_appears_in_the_openapi_schema_with_its_model():
    from main import app

    paths = app.openapi()["paths"]
    for path, method, response_model in ALL_ROUTES:
        assert path in paths, f"missing from openapi: {path}"
        op = paths[path].get(method.lower())
        assert op is not None, f"missing method in openapi: {method} {path}"
        success = op["responses"].get("200") or op["responses"].get("202")
        assert success is not None, f"{method} {path}: no 200/202 response in openapi"
        ref = success["content"]["application/json"]["schema"].get("$ref", "")
        assert ref.endswith(f"/{response_model.__name__}"), (
            f"{method} {path}: openapi response schema is {ref!r}, "
            f"contract requires {response_model.__name__}"
        )


def test_every_contract_route_is_registered_with_its_response_model():
    registered = _router_routes_by_full_path()
    for path, method, response_model in ALL_ROUTES:
        route = registered.get((path, method))
        assert route is not None, f"missing route: {method} {path}"
        assert route.response_model is response_model, (
            f"{method} {path}: response_model is {route.response_model!r}, "
            f"contract requires {response_model.__name__}"
        )
    # No extra surface beyond the contract either
    assert len(registered) == len(ALL_ROUTES), (
        f"router surface has {len(registered)} routes, contract has {len(ALL_ROUTES)}"
    )


def test_platform_routes_use_platform_admin_dependency():
    # WP1c: destructive routes (pairings PUT, apply, revert, superseded-by
    # PATCH) carry require_platform_admin_user_session, which wraps
    # require_platform_admin and additionally refuses the static API key
    # (plan §4.5). Read routes stay on plain require_platform_admin.
    from auth import require_platform_admin, require_platform_admin_user_session
    from api import catalog_upgrade_admin

    destructive = {
        ("/admin/catalog/upgrade/runs/{run_id}/pairings", "PUT"),
        ("/admin/catalog/upgrade/runs/{run_id}/apply", "POST"),
        ("/admin/catalog/upgrade/runs/{run_id}/revert", "POST"),
        ("/admin/catalog/controls/{scf_id}/superseded-by", "PATCH"),
    }
    for route in catalog_upgrade_admin.router.routes:
        if not isinstance(route, APIRoute):
            continue
        dep_calls = [d.call for d in route.dependant.dependencies]
        is_destructive = any((route.path, m) in destructive for m in route.methods)
        if is_destructive:
            assert require_platform_admin_user_session in dep_calls, (
                f"{route.path}: destructive route missing require_platform_admin_user_session"
            )
        else:
            assert require_platform_admin in dep_calls, (
                f"{route.path}: missing require_platform_admin dependency"
            )


def test_org_routes_use_an_org_membership_dependency():
    # WP2b swaps in require_org_admin_or_platform_admin; until then each org
    # route must carry a require_org_role dependency (named 'dependency' by
    # the factory in auth.py).
    from api import catalog_reconciliation

    for route in catalog_reconciliation.router.routes:
        if not isinstance(route, APIRoute):
            continue
        dep_names = [d.call.__name__ for d in route.dependant.dependencies]
        assert any(
            n in ("dependency", "require_org_admin_or_platform_admin") for n in dep_names
        ), f"{route.path}: missing org-role dependency (got {dep_names})"


# Routes still awaiting their implementation WP. Each WP that fills routes in
# removes them from this list. The platform set was implemented by WP1c, the
# org read side by WP2b, and apply/rollback/cancel by WP2c — the list is now
# empty; the test machinery stays as the regression harness for any future
# contract additions.
STUBBED_ROUTES = []


def test_stub_handlers_raise_501_not_implemented():
    # Not-yet-implemented contract stubs must be inert: none may silently
    # succeed. Implemented routes are exercised by their own test modules.
    import asyncio
    import inspect
    from unittest.mock import MagicMock

    from fastapi import HTTPException

    registered = _router_routes_by_full_path()

    async def _collect():
        failures = []
        for path, method in STUBBED_ROUTES:
            route = registered.get((path, method))
            if route is None:
                failures.append(f"{method} {path}: stubbed route not registered")
                continue
            handler = route.endpoint
            kwargs = {
                name: MagicMock()
                for name in inspect.signature(handler).parameters
            }
            try:
                await handler(**kwargs)
                failures.append(f"{route.path}: stub returned instead of raising")
            except HTTPException as exc:
                if exc.status_code != 501:
                    failures.append(f"{route.path}: raised {exc.status_code}, expected 501")
        return failures

    failures = asyncio.run(_collect())
    assert not failures, "; ".join(failures)


def test_diff_detail_round_trips_through_the_contract_models():
    # Smoke-validates the frozen diff-detail shape (plan §4.2.2) so downstream
    # WPs can rely on serialisation stability. Keys are synthetic placeholders,
    # not real control ids.
    detail = contracts.DiffDetail(
        from_version="2026.1",
        to_version="2026.2",
        entities={
            contracts.CatalogEntityType.CONTROLS: contracts.EntityDiff(
                added=[contracts.AddedEntity(key="ctl-added-1", name="New Control", data={"domain": "demo"})],
                changed=[
                    contracts.ChangedEntity(
                        key="ctl-changed-1",
                        fields={"description": contracts.FieldChange(old="a", new="b")},
                    )
                ],
                deprecated=[
                    contracts.DeprecatedEntity(
                        key="ctl-deprecated-1",
                        superseded_by=None,
                        suggestions=[
                            contracts.SupersededSuggestion(scf_id="ctl-successor-1", score=0.72)
                        ],
                    )
                ],
                resurrected=[contracts.ResurrectedEntity(key="ctl-resurrected-1")],
                unchanged=["ctl-unchanged-1", "ctl-unchanged-2"],
            )
        },
    )
    rehydrated = contracts.DiffDetail.model_validate(detail.model_dump())
    assert rehydrated == detail
    ctl = rehydrated.entities[contracts.CatalogEntityType.CONTROLS]
    assert ctl.changed[0].fields["description"].old == "a"
    assert ctl.deprecated[0].suggestions[0].score == 0.72
