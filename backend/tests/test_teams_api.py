"""Contract tests for the functions/teams API surface (#822 phase 1).

Deliberately database-free. Everything asserted here is read off the router's
own dependency graph, its pure helpers, and the model's table definition, so
these tests pin the *contract* — which endpoints exist, who may call them, what
they promise — without scripting a fake session through each handler's query
order. The Postgres-backed behaviour lives in
``test_teams_primary_promotion.py``.

What is pinned, and why each matters:

* the endpoint set is exactly the ten in the phase-1 scope — a new route here
  is a scope change and should fail a test, not slip through;
* **functions are read-only.** No POST/PATCH/DELETE exists under ``/functions``.
  The rows are platform-static literals; a tenant able to edit them breaks the
  guarantee that a function means the same thing in every deployment;
* the RBAC tier on every route: admin for every mutation, viewer for every
  read, plain authentication for the static function list;
* every mutating route logs to ``audit_log`` and takes the ``Request`` the
  audit call needs to attribute the action;
* health is advisory — it reports, it never gates.
"""
import inspect
import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import Request
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import teams as teams_api  # noqa: E402
from auth import require_auth  # noqa: E402
from models import TeamMember  # noqa: E402
from schemas import TeamMemberUpdate, TeamUpdate  # noqa: E402

TEAMS = "/organizations/{org_id}/teams"
TEAM = TEAMS + "/{team_id}"
MEMBERS = TEAM + "/members"
MEMBER = MEMBERS + "/{user_id}"

#: The phase-1 endpoint set and the minimum organisation role each demands.
#: ``None`` means "authenticated, but not org-scoped".
EXPECTED_ROUTES = {
    ("GET", "/functions"): None,
    ("GET", TEAMS): "viewer",
    ("POST", TEAMS): "admin",
    ("GET", TEAM): "viewer",
    ("PATCH", TEAM): "admin",
    ("DELETE", TEAM): "admin",
    ("GET", MEMBERS): "viewer",
    ("POST", MEMBERS): "admin",
    ("PATCH", MEMBER): "admin",
    ("DELETE", MEMBER): "admin",
}

#: Routes that change state, and therefore must write an audit trail.
MUTATIONS = [key for key in EXPECTED_ROUTES if key[0] in {"POST", "PATCH", "DELETE"}]


def _routes() -> dict:
    """Every route on the teams router, keyed by (method, path)."""
    found = {}
    for route in teams_api.router.routes:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found[(method, route.path)] = route
    return found


def _min_org_role(route) -> object:
    """The ``min_role`` baked into the route's ``require_org_role`` dependency.

    ``require_org_role`` is a closure factory, so the tier is not a value on the
    route — it is a free variable of the dependency it produced. Reading it back
    out is the only way to assert the tier without standing up auth.

    Returns ``None`` when the route has no org-role dependency at all.
    """
    for dependency in route.dependant.dependencies:
        call = dependency.call
        freevars = getattr(getattr(call, "__code__", None), "co_freevars", ())
        if "min_role" in freevars:
            cells = dict(zip(freevars, (c.cell_contents for c in call.__closure__)))
            return cells["min_role"]
    return None


# ---------------------------------------------------------------------------
# Endpoint set and authorisation
# ---------------------------------------------------------------------------

def test_exactly_the_phase_one_endpoints_exist():
    """No more, no fewer. Extra routes here are scope creep into phases 2-5."""
    assert set(_routes()) == set(EXPECTED_ROUTES)


def test_functions_are_read_only():
    """There is no way for a tenant to create, edit or delete a function.

    This is an acceptance criterion, not a convenience: the fourteen rows are
    fixed literals, and a tenant that could change them would break what a
    function alignment means everywhere else.
    """
    writes = [
        (method, path)
        for (method, path) in _routes()
        if path.startswith("/functions") and method != "GET"
    ]
    assert writes == []


@pytest.mark.parametrize(("method", "path"), sorted(k for k in EXPECTED_ROUTES if k[1] != "/functions"))
def test_org_scoped_routes_demand_the_expected_role(method, path):
    assert _min_org_role(_routes()[(method, path)]) == EXPECTED_ROUTES[(method, path)]


@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_requires_org_admin(method, path):
    """Teams are governance metadata; only an org admin reshapes them."""
    assert _min_org_role(_routes()[(method, path)]) == "admin"


def test_org_scoped_routes_carry_org_id_in_the_path():
    """``require_org_role`` reads ``org_id`` from the path, so it must be there.

    A route missing it does not fall back to some other scope — it 422s.
    """
    for (method, path), route in _routes().items():
        if _min_org_role(route) is not None:
            assert "{org_id}" in path, f"{method} {path}"


def test_the_function_list_needs_authentication_but_no_organisation():
    route = _routes()[("GET", "/functions")]
    assert _min_org_role(route) is None
    calls = {d.call for d in route.dependant.dependencies}
    assert require_auth in calls


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_writes_an_audit_entry(method, path):
    """Invariant: no team or membership change happens off the record."""
    source = inspect.getsource(_routes()[(method, path)].endpoint)
    assert "log_entity_changes" in source


@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_takes_the_request_it_audits_from(method, path):
    """``detect_action_source``/``get_request_id`` need the live Request.

    Without the parameter the handler cannot say whether a change came from the
    UI, an API key or MCP — which is most of what makes the entry useful.
    """
    params = inspect.signature(_routes()[(method, path)].endpoint).parameters
    assert any(p.annotation is Request for p in params.values())


# ---------------------------------------------------------------------------
# Health is advisory
# ---------------------------------------------------------------------------

def _team(is_active=True, function_active=True):
    return SimpleNamespace(
        is_active=is_active,
        function=SimpleNamespace(is_active=function_active),
    )


def _member(role="member"):
    return SimpleNamespace(membership_role=role)


def test_a_brand_new_team_is_healthy_enough_to_exist():
    """Empty and primary-less is exactly how every team starts.

    It is reported, never refused — the warnings are badges for the UI.
    """
    health = teams_api._team_health(_team(), [])

    assert health["has_members"] is False
    assert health["has_primary"] is False
    assert health["member_count"] == 0
    assert "Team has no members." in health["warnings"]
    assert "Team has no primary owner." in health["warnings"]


def test_a_team_with_a_primary_warns_about_nothing():
    health = teams_api._team_health(_team(), [_member("primary"), _member()])

    assert health["has_primary"] is True
    assert health["has_delegate"] is False
    assert health["member_count"] == 2
    assert health["warnings"] == []


def test_a_deactivated_function_is_surfaced_not_hidden():
    health = teams_api._team_health(_team(function_active=False), [_member("primary")])

    assert health["function_is_active"] is False
    assert any("no longer active" in w for w in health["warnings"])


def test_an_archived_team_says_so():
    health = teams_api._team_health(_team(is_active=False), [_member("primary")])

    assert "Team is archived." in health["warnings"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

def test_a_team_update_may_change_one_field_and_leave_the_rest_alone():
    patch = TeamUpdate(name="Platform")

    assert patch.model_dump(exclude_unset=True) == {"name": "Platform"}


def test_an_empty_team_update_is_legal_and_changes_nothing():
    assert TeamUpdate().model_dump(exclude_unset=True) == {}


def test_a_member_update_must_actually_say_a_role():
    """This endpoint exists only to change the role; an empty body is a 422."""
    with pytest.raises(ValidationError):
        TeamMemberUpdate()


@pytest.mark.parametrize("role", ["primary", "delegate", "member"])
def test_the_three_membership_roles_are_accepted(role):
    assert TeamMemberUpdate(membership_role=role).membership_role == role


@pytest.mark.parametrize("role", ["admin", "owner", "Primary", ""])
def test_no_other_membership_role_is_accepted(role):
    """Notably 'admin': team membership confers no permissions, ever."""
    with pytest.raises(ValidationError):
        TeamMemberUpdate(membership_role=role)


# ---------------------------------------------------------------------------
# Exclusive roles agree with the database
# ---------------------------------------------------------------------------

def test_the_exclusive_roles_are_the_ones_the_database_indexes():
    """The router's notion of "only one of these per team" must be the schema's.

    If a partial unique index is added or dropped and this list is not updated,
    promotions either race into an IntegrityError or silently allow duplicates.
    """
    indexed = {
        index.name.removeprefix("uq_team_")
        for index in TeamMember.__table__.indexes
        if index.unique and index.name.startswith("uq_team_")
    }
    assert indexed == set(teams_api.EXCLUSIVE_MEMBERSHIP_ROLES)
