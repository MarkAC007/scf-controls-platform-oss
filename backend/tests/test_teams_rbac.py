"""Teams are accountability, not authorisation, and functions are platform-static (#822 phase 1).

Two claims, each tested at the place it would actually be broken:

**Functions are read-only to tenants.** No tenant-facing route may create,
edit or delete a row in ``functions`` — the fourteen rows are identical in
every deployment and a mapping written against one must mean the same thing
in the next. Asserted by introspecting ``app.routes`` rather than by omission,
so a mutating route added later trips it.

**Teams grant no permissions.** Being on a team must never widen what somebody
can do; ``organization_members.role`` remains the only thing authorisation
consults. Two halves: the authorisation module never reads team membership,
and an org ``editor`` is refused team creation and membership mutation.

What runs where, honestly:

* the model-shape, role-hierarchy and authorisation-source tests run
  everywhere, now, and fail if the property is broken;
* the route-level tests **skip on this branch**, because ``backend/api/teams.py``
  and the functions router are being built in a parallel workstream and are
  not on disk here. They are written against ``app.routes`` and the real
  ``verify_org_membership`` — no handler name is hard-coded — so they begin
  executing the moment that branch merges, with no edit needed. Until then
  they are a skip, not a pass.

The route-level RBAC test encodes one design decision: team management is
admin-only. If the API workstream lands editor-managed teams, this test fails,
and that failure is the conversation, not a broken test.
"""
from __future__ import annotations

import os
import re
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import main  # noqa: E402
from database import get_db  # noqa: E402
from models import Function, Team, TeamMember  # noqa: E402


MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# The migration's CHECK constraint. Kept literal here so a fourth role added
# to the model has to be argued for against the org roles below.
TEAM_MEMBERSHIP_ROLES = {"primary", "delegate", "member"}


def _routes_matching(fragment: str):
    """Every routed path containing ``fragment``, with its HTTP methods.

    Matches on a fragment rather than a prefix because the router may be
    mounted flat (``/api/teams``) or under the organisation
    (``/api/organizations/{org_id}/teams``), and the claim holds either way.

    Reads the OpenAPI schema rather than walking ``app.routes``. FastAPI
    0.141 stopped flattening included routers into ``app.routes``: each
    ``include_router`` call now leaves a single ``_IncludedRouter`` object
    whose ``path`` is the empty string, with the real routes behind
    ``original_router`` and the prefix behind ``include_context``. Walking
    ``app.routes`` therefore matches nothing for *any* router, so the two
    guards below would have skipped for ever, in this phase and every later
    one, while reporting green. The schema is public API, already carries the
    ``/api`` prefix, and does not move between releases.
    """
    found = []
    for path, operations in main.app.openapi()["paths"].items():
        if fragment not in path:
            continue
        methods = {m.upper() for m in operations if m.upper() != "PARAMETERS"}
        if methods:
            found.append((path, methods))
    return found


def _fill_path_params(path: str) -> str:
    """Replace every ``{param}`` with a UUID so the route can be requested.

    A non-UUID path parameter would fail validation, which is fine: FastAPI
    solves sub-dependencies before it validates path, query and body, so an
    authorisation refusal still arrives as 403 rather than 422.
    """
    return re.sub(r"\{[^{}]+\}", lambda _m: str(uuid4()), path)


# ---------------------------------------------------------------------------
# Functions are platform-static — model shape. Runs everywhere.
# ---------------------------------------------------------------------------

class TestFunctionsArePlatformStatic:
    def test_functions_are_not_tenant_scoped(self):
        # No organization_id means no tenant can own a row, which is the
        # structural reason the table cannot be tenant-writable in the first
        # place. A column added here would be the first sign that changed.
        columns = {c.name for c in Function.__table__.columns}
        assert "organization_id" not in columns
        assert columns == {
            "id", "key", "name", "description", "display_order", "is_active",
        }

    def test_functions_carry_no_authorship(self):
        # created_by / updated_by would imply somebody creates these. Nobody
        # does; the migration seeds them.
        columns = {c.name for c in Function.__table__.columns}
        assert not [c for c in columns if c.endswith("_by_user_id")]

    def test_a_tenant_team_points_at_a_function_it_cannot_remove(self):
        fk = next(
            fk for fk in Team.__table__.foreign_keys
            if fk.column.table.name == "functions"
        )
        assert fk.ondelete == "RESTRICT"


class TestFunctionsHaveNoTenantWritePath:
    def test_no_mutating_route_is_registered_on_functions(self):
        routes = _routes_matching("/functions")
        if not routes:
            pytest.skip(
                "No /functions route is registered on this branch — the API "
                "workstream is on a parallel branch. This guard is a skip, "
                "not a pass, until that merges."
            )
        offenders = [
            (path, sorted(methods & MUTATING_METHODS))
            for path, methods in routes
            if methods & MUTATING_METHODS
        ]
        assert offenders == [], (
            "functions are platform-static; these routes would let a tenant "
            f"write to them: {offenders}"
        )

    def test_the_functions_router_is_readable(self):
        # The mirror of the test above: "no mutating route" is satisfied
        # trivially by a router that does nothing at all, so the read route
        # has to exist for the guard above to mean anything.
        routes = _routes_matching("/functions")
        if not routes:
            pytest.skip(
                "No /functions route is registered on this branch — see "
                "test_no_mutating_route_is_registered_on_functions."
            )
        assert any("GET" in methods for _path, methods in routes)


# ---------------------------------------------------------------------------
# Teams grant no permissions — authorisation shape. Runs everywhere.
# ---------------------------------------------------------------------------

class TestTeamsAreNotAuthorisation:
    def test_the_role_hierarchy_is_untouched_by_teams(self):
        assert auth.ROLE_HIERARCHY == {"admin": 3, "editor": 2, "viewer": 1}

    def test_team_roles_and_org_roles_share_no_names(self):
        # A 'primary' that collided with an org role name would be one
        # careless lookup away from becoming a permission.
        assert TEAM_MEMBERSHIP_ROLES.isdisjoint(auth.ROLE_HIERARCHY)

    def test_the_check_constraint_still_names_exactly_those_three_roles(self):
        check = next(
            c for c in TeamMember.__table__.constraints
            if getattr(c, "name", None) == "ck_team_members_membership_role"
        )
        named = set(re.findall(r"'(\w+)'", str(check.sqltext)))
        assert named == TEAM_MEMBERSHIP_ROLES

    def test_authorisation_never_reads_team_membership(self):
        # The single place the property would be broken. Prose mentioning
        # "team members" is fine; a code reference is not.
        source = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "auth.py")
        ).read()
        for token in ("TeamMember", "team_members", "membership_role", "from models import Team"):
            assert token not in source, (
                f"auth.py references {token!r}: team membership must never "
                "reach an authorisation decision"
            )

    def test_team_membership_has_no_permission_bearing_column(self):
        columns = {c.name for c in TeamMember.__table__.columns}
        for token in ("role", "permission", "scope", "grant"):
            assert not [
                c for c in columns
                if token in c and c != "membership_role"
            ], f"team_members.{token} would make a team a permission grant"


# ---------------------------------------------------------------------------
# Teams grant no permissions — route level. SKIPS until the API branch merges.
# ---------------------------------------------------------------------------

ORG_ID = uuid4()
USER_DB_ID = uuid4()


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _EditorSession:
    """Answers exactly the two queries ``verify_org_membership`` issues.

    The organisation exists, and the caller is a direct member of it with the
    ``editor`` role. Everything after that is the real refusal logic in
    ``auth.verify_org_membership`` — this fake supplies the database rows, it
    does not supply the decision.
    """

    def __init__(self):
        self._scripted = [
            SimpleNamespace(id=ORG_ID),                       # organization exists
            SimpleNamespace(role="editor", user_id=USER_DB_ID),  # direct membership
        ]

    async def execute(self, *_args, **_kwargs):
        if not self._scripted:
            raise AssertionError(
                "handler queried the database after the role check should "
                "already have refused it"
            )
        return _Scalar(self._scripted.pop(0))


@pytest.fixture
def editor_client(monkeypatch):
    async def _override_db():
        yield _EditorSession()

    async def _require_auth(*_args, **_kwargs):
        return SimpleNamespace(
            db_id=str(USER_DB_ID),
            email="editor@example.com",
            auth_method="oauth",
            is_platform_admin=False,
        )

    # require_org_role's dependency resolves both of these from auth's module
    # globals at call time, so patching here reaches every router that guards
    # itself the normal way.
    monkeypatch.setattr(auth, "require_auth", _require_auth)
    main.app.dependency_overrides[get_db] = _override_db
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.pop(get_db, None)


def _mutating_team_routes():
    return [
        (path, method)
        for path, methods in _routes_matching("/teams")
        for method in sorted(methods & MUTATING_METHODS)
    ]


class TestAnEditorCannotManageTeams:
    def test_every_mutating_team_route_refuses_an_editor(self, editor_client):
        routes = _mutating_team_routes()
        if not routes:
            pytest.skip(
                "backend/api/teams.py is not on this branch (the API "
                "workstream is building it in parallel), so no /teams route "
                "is registered. This test is a SKIP, not a pass — it starts "
                "executing unchanged once that branch merges."
            )

        refused, allowed = [], []
        for path, method in routes:
            response = editor_client.request(
                method,
                _fill_path_params(path),
                json={},
                headers={"Authorization": "Bearer test-token"},
            )
            (refused if response.status_code == 403 else allowed).append(
                (method, path, response.status_code)
            )

        assert allowed == [], (
            "team management must require admin; these mutating routes did "
            f"not refuse an org editor: {allowed}"
        )
        assert refused, "no mutating team route was exercised"

    def test_reading_teams_is_open_to_an_editor(self, editor_client):
        # The refusal above must be about the role, not about the router
        # being unreachable in this harness. A GET that also 403s would mean
        # the fixture is broken and the test above proves nothing.
        reads = [
            (path, methods) for path, methods in _routes_matching("/teams")
            if "GET" in methods
        ]
        if not reads:
            pytest.skip(
                "backend/api/teams.py is not on this branch — see "
                "test_every_mutating_team_route_refuses_an_editor."
            )
        path, _methods = reads[0]
        response = editor_client.request(
            "GET",
            _fill_path_params(path),
            headers={"Authorization": "Bearer test-token"},
        )
        assert response.status_code != 403
