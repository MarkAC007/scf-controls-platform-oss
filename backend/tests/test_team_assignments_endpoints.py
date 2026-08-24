"""Contract tests for the team-assignment API surface (#822 phases 3 and 4).

Deliberately database-free, matching ``test_teams_api.py``. Everything here is
read off the router's own dependency graph and the request schema, so it pins
the *contract* — which endpoints exist, who may call them, what they refuse to
accept — without scripting a fake session through each handler. The
Postgres-backed behaviour lives in ``test_team_assignments.py``.

What is pinned, and why each matters:

* the endpoint set is exactly the four now in scope — the three phase 3
  defined, plus the batch assign phase 4 adds so that a bulk operation can
  emit one aggregate notification instead of one per item. The original point
  of this assertion is preserved below as
  ``test_no_risk_or_vendor_routes_have_appeared``: risk and vendor routes are
  phase-5 scope and must not turn up early;
* **teams grant no permissions.** RBAC is ``organization_members.role`` and
  nothing else: admin for every mutation, viewer for every read, exactly the
  tiering phase 1 established. There is no team admin;
* every mutating route writes an audit trail, and takes the ``Request`` the
  audit call needs to attribute the action;
* ``organization_id`` cannot be supplied by the caller. It is derived from the
  path, and a body that names it must not be able to redirect the write.
"""
import inspect
import os
import sys

import pytest
from fastapi import Request
from pydantic import ValidationError

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import team_assignments as ta_api  # noqa: E402
from schemas import TeamAssignmentCreate  # noqa: E402
from services.team_assignments import TEAM_ASSIGNMENT_TYPE_KEYS  # noqa: E402

ASSIGNMENTS = "/organizations/{org_id}/team-assignments"
ASSIGNMENT = ASSIGNMENTS + "/{assignment_id}"
BATCH = ASSIGNMENTS + "/batch"

#: The endpoint set and the minimum organisation role each demands. The batch
#: route is phase 4's; it demands admin like every other mutation here, because
#: assigning fifty items is the same governance act as assigning one.
EXPECTED_ROUTES = {
    ("GET", ASSIGNMENTS): "viewer",
    ("POST", ASSIGNMENTS): "admin",
    ("POST", BATCH): "admin",
    ("DELETE", ASSIGNMENT): "admin",
}

MUTATIONS = [key for key in EXPECTED_ROUTES if key[0] in {"POST", "PATCH", "DELETE"}]


def _routes() -> dict:
    found = {}
    for route in ta_api.router.routes:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            found[(method, route.path)] = route
    return found


def _min_org_role(route) -> object:
    """The ``min_role`` baked into the route's ``require_org_role`` closure.

    Same technique as ``test_teams_api._min_org_role``: the tier is a free
    variable of the dependency the factory produced, and reading it back out is
    the only way to assert it without standing up auth.
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

def test_exactly_the_expected_endpoints_exist():
    """No more, no fewer."""
    assert set(_routes()) == set(EXPECTED_ROUTES)


def test_no_risk_or_vendor_routes_have_appeared():
    """Phase 5's scope, kept out of phases 3 and 4.

    Risks and vendors join by being registered in ``TEAM_ASSIGNMENT_TYPES``,
    which is what makes them a registry entry rather than a route — so a route
    naming either is a sign somebody special-cased what the registry exists to
    generalise.
    """
    for (method, path) in _routes():
        assert "risk" not in path and "vendor" not in path, f"{method} {path}"


@pytest.mark.parametrize(("method", "path"), sorted(EXPECTED_ROUTES))
def test_routes_demand_the_expected_role(method, path):
    assert _min_org_role(_routes()[(method, path)]) == EXPECTED_ROUTES[(method, path)]


@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_requires_org_admin(method, path):
    """Assignment is a governance act; only an org admin performs it.

    Note what is *not* consulted: the caller's place on the team. Being the
    accountable team's primary confers no authority to reassign anything.
    """
    assert _min_org_role(_routes()[(method, path)]) == "admin"


def test_reading_is_open_to_any_member_of_the_organisation():
    """Who owns what is not privileged information inside a tenant."""
    assert _min_org_role(_routes()[("GET", ASSIGNMENTS)]) == "viewer"


def test_every_route_carries_org_id_in_the_path():
    """``require_org_role`` reads ``org_id`` from the path, so it must be there.

    This is also what makes the tenant scope underivable from the body.
    """
    for (method, path) in _routes():
        assert "{org_id}" in path, f"{method} {path}"


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_takes_the_request_its_audit_call_needs(method, path):
    """``detect_action_source`` and ``get_request_id`` both read the Request.

    A handler that did not accept one could still write an audit row, but it
    would be an anonymous one — no source, no request id — which is the sort of
    audit trail that fails the question it exists to answer.
    """
    handler = _routes()[(method, path)].endpoint
    params = inspect.signature(handler).parameters
    assert any(p.annotation is Request for p in params.values()), f"{method} {path}"


@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_writes_an_audit_trail(method, path):
    """Read off the handler's source: it must reach the audit helper.

    Both mutations go through ``_audit``, which is the module's single call
    into ``log_entity_changes``.
    """
    source = inspect.getsource(_routes()[(method, path)].endpoint)
    assert "_audit(" in source, f"{method} {path} does not audit"


@pytest.mark.parametrize(("method", "path"), sorted(MUTATIONS))
def test_every_mutation_commits_in_the_same_transaction_as_its_audit(method, path):
    """The audit row and the change it describes are one transaction or neither.

    ``log_entity_changes`` adds to the session and deliberately does not
    commit, so a handler that audits without committing writes nothing at all
    — and one that commits before auditing can record a change it then fails to
    describe.
    """
    source = inspect.getsource(_routes()[(method, path)].endpoint)
    assert source.index("_audit(") < source.index("db.commit()"), (
        f"{method} {path} commits before it audits"
    )


# ---------------------------------------------------------------------------
# The tenant boundary is not negotiable from the request body
# ---------------------------------------------------------------------------

def test_organization_id_cannot_be_supplied_in_the_request_body():
    """The isolation control the composite foreign keys depend on.

    ``organization_id`` is the column both composite foreign keys join
    through. A caller able to set it could name their own organisation while
    naming a victim's control, and the team-side check would validate the row
    happily. It is derived from the path, and Pydantic must not carry an
    override through.
    """
    assert "organization_id" not in TeamAssignmentCreate.model_fields


def test_an_organization_id_in_the_body_is_dropped_not_honoured():
    """Belt and braces on the line above.

    Pydantic's default for an unknown field is to ignore it, so a caller who
    sends ``organization_id`` gets no error — the point being asserted is that
    the value cannot reach the handler, not that it is rejected loudly.
    """
    payload = TeamAssignmentCreate(
        type="control",
        item_id="11111111-1111-1111-1111-111111111111",
        team_id="22222222-2222-2222-2222-222222222222",
        organization_id="33333333-3333-3333-3333-333333333333",
    )
    assert not hasattr(payload, "organization_id")


# ---------------------------------------------------------------------------
# Type dispatch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_key", TEAM_ASSIGNMENT_TYPE_KEYS)
def test_every_registered_type_is_accepted_by_the_request_schema(type_key):
    """The schema pattern is built from the registry, so it cannot drift."""
    payload = TeamAssignmentCreate(
        type=type_key,
        item_id="11111111-1111-1111-1111-111111111111",
        team_id="22222222-2222-2222-2222-222222222222",
    )
    assert payload.type == type_key


@pytest.mark.parametrize("type_key", ["risk", "vendor", "control ", "", "CONTROL"])
def test_unregistered_types_are_refused(type_key):
    """``risk`` and ``vendor`` are in #822's API surface and land in phase 5.

    Until their tables exist they must 422, not 500 — a caller reading the
    issue will try them.
    """
    with pytest.raises(ValidationError):
        TeamAssignmentCreate(
            type=type_key,
            item_id="11111111-1111-1111-1111-111111111111",
            team_id="22222222-2222-2222-2222-222222222222",
        )


def test_is_accountable_defaults_to_consulted():
    """Assigning a team must not silently take ownership from another one."""
    payload = TeamAssignmentCreate(
        type="control",
        item_id="11111111-1111-1111-1111-111111111111",
        team_id="22222222-2222-2222-2222-222222222222",
    )
    assert payload.is_accountable is False
