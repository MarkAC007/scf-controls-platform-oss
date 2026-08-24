"""The team-assignment API surface: authorisation, audit, atomicity, N+1 (#822 phase 3).

Four API-level acceptance criteria:

* marking a team accountable when another already is must be ONE atomic
  transaction, leaving exactly one accountable row;
* an org ``editor`` is refused assignment mutations; any org member may read;
* every mutation writes an ``audit_log`` row;
* the bulk read returns many items' assignments without an N+1 — the query
  count must not scale with the number of items.

**What runs where, honestly.** ``backend/api/team_assignments.py`` (or wherever
the API workstream puts it) is not on this branch. Every test that needs it is
therefore a **SKIP, not a pass**, and says so in its skip reason. None of them
hard-codes a module name, a handler name or a URL: routes are discovered from
``app.openapi()["paths"]`` and request bodies are built from the operation's
own schema, so they begin executing the moment that branch merges with no edit
needed.

Two guards keep those skips honest, and neither of them skips:

* ``TestTheRouteDiscoveryStillWorks`` proves the discovery machinery finds the
  phase-1 team routes *today*. If FastAPI changes shape again, that class goes
  red instead of every test below going quietly green-by-skipping. This is not
  hypothetical: FastAPI 0.141 stopped flattening included routers into
  ``app.routes`` — each ``include_router`` now leaves one ``_IncludedRouter``
  whose ``path`` is ``None`` — so the obvious ``route.path`` match finds
  nothing for *any* router, and a guard written that way would skip for ever
  while reporting success.
* ``TestDiscoveryIsNotSilentlyEmpty`` fails, rather than skips, if the schema
  contains an ``is_accountable`` field anywhere but route discovery found no
  assignment routes — i.e. if the API landed under a name this file did not
  anticipate.

The N+1 test drives real HTTP against a real database. Its harness is
exercised today against the phase-1 team list
(``TestTheQueryCounterWorks``), so the machinery is proven even while the
route it will eventually measure does not exist.

Run with::

    docker compose exec -T backend python -m pytest \\
        tests/test_team_assignments_api.py -v
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import re
import sys
import textwrap
import uuid
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401
import auth  # noqa: E402
import main  # noqa: E402
from database import get_db  # noqa: E402
from models import (  # noqa: E402
    ControlTeamAssignment,
    EvidenceTeamAssignment,
    EvidenceTracking,
    Function,
    Organization,
    OrganizationMember,
    ScopedControl,
    Team,
    User,
)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

#: Either of these means the handler left a trail. ``log_entity_changes`` is
#: the diffing helper most routes use; ``create_audit_entry`` is the primitive
#: underneath it, which a handler with nothing to diff may call directly.
AUDIT_HELPERS = ("log_entity_changes", "create_audit_entry")


def _leaves_an_audit_trail(endpoint) -> bool:
    """Whether ``endpoint`` writes to ``audit_log``, following ONE level of indirection.

    A handler may call the helper directly, or delegate to a module-level wrapper
    that pins the entity type, the tracked-field set and the request plumbing in
    one place. The wrapper is the better shape — six handlers repeating that
    setup is six chances for the tracked fields to drift apart — so recognising
    only the direct call would penalise the safer code.

    Deliberately one level and no further. A helper reachable only through a
    chain of wrappers is not something this test should vouch for, and an
    unbounded search would eventually find `log_entity_changes` somewhere in
    almost any import graph and stop meaning anything.

    This is still only evidence that the call was *written*. That rows actually
    land is proved against a live database elsewhere.
    """
    try:
        source = textwrap.dedent(inspect.getsource(endpoint))
    except (OSError, TypeError):  # pragma: no cover - C or dynamic callables
        return False
    if any(helper in source for helper in AUDIT_HELPERS):
        return True

    # ``endpoint.__globals__`` rather than ``inspect.getmodule``: the latter
    # resolves via ``sys.modules`` and returns None for a module that was
    # loaded but never registered, which would silently turn this check into
    # "no trail" — the wrong answer, and a quiet one.
    namespace = getattr(inspect.unwrap(endpoint), "__globals__", None) or {}

    # Real call nodes, not a ``f"{name}("`` substring: the substring form counts
    # a mention inside a docstring, a comment or a string literal as a call, and
    # would vouch for a handler that only talks about auditing.
    called = {
        node.func.id
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    for name in called:
        candidate = namespace.get(name)
        if not inspect.isfunction(candidate):
            continue
        try:
            if any(h in inspect.getsource(candidate) for h in AUDIT_HELPERS):
                return True
        except (OSError, TypeError):  # pragma: no cover
            continue
    return False

#: The awaits that make a loop an N+1. A handler that issues one of these once
#: per item is the defect the bulk read exists to avoid.
QUERY_AWAITS = {"execute", "scalars", "scalar", "stream", "get", "refresh"}

DATABASE_URL = os.getenv("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
)


# ---------------------------------------------------------------------------
# Route discovery
# ---------------------------------------------------------------------------

def _walk_routes(router, prefix: str = ""):
    """Every routed (method, full path, route object) reachable from ``router``.

    Recurses through ``_IncludedRouter`` wrappers, which is what FastAPI
    0.141 leaves behind for each ``include_router`` call: the wrapper's own
    ``path`` is ``None``, the real routes hang off ``original_router`` and the
    prefix off ``include_context``. Walking ``app.routes`` without this
    recursion sees only the wrappers and matches nothing at all.

    ``app.openapi()`` gives the same paths more cheaply and is what most of
    this file uses; the route objects are needed only where a test has to
    reach the endpoint function itself, to read its source or its
    dependencies.
    """
    for route in getattr(router, "routes", []):
        inner = getattr(route, "original_router", None)
        if inner is not None:
            context = getattr(route, "include_context", None)
            yield from _walk_routes(
                inner, prefix + (getattr(context, "prefix", "") or "")
            )
            continue
        for method in sorted((getattr(route, "methods", None) or set()) - {"HEAD", "OPTIONS"}):
            yield method, prefix + route.path, route


def _normalise(path: str) -> str:
    """Drop Starlette path converters so a route matches its schema path.

    A route declared ``{section_id:path}`` appears in the OpenAPI document as
    ``{section_id}``. Without this, three real endpoints look like routes the
    walk failed to find, and the consistency check below would be red for a
    reason that has nothing to do with discovery.
    """
    return re.sub(r"\{([^{}:]+):[^{}]+\}", r"{\1}", path)


def _routes_by_key() -> dict:
    return {
        (method, _normalise(path)): route
        for method, path, route in _walk_routes(main.app)
    }


def _spec() -> dict:
    return main.app.openapi()


def _resolve(spec: dict, node):
    """Follow a ``$ref`` one hop. Returns the node unchanged if it is not one."""
    if isinstance(node, dict) and "$ref" in node:
        target = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            target = target.get(part, {})
        return target
    return node


def _schema_mentions(spec: dict, node, field: str, seen=None) -> bool:
    """Does this schema, or anything it composes, declare ``field``?

    Recursive because the assignment payload will almost certainly arrive
    wrapped — in an array, in ``allOf``, behind a ``$ref`` to a component. A
    shallow check would miss all three.
    """
    seen = seen if seen is not None else set()
    node = _resolve(spec, node)
    if isinstance(node, dict):
        marker = id(node)
        if marker in seen:
            return False
        seen.add(marker)
        if field in (node.get("properties") or {}):
            return True
        return any(_schema_mentions(spec, child, field, seen) for child in node.values())
    if isinstance(node, list):
        return any(_schema_mentions(spec, child, field, seen) for child in node)
    return False


def _looks_like_an_assignment_path(path: str) -> bool:
    """A team attached to a control or an evidence item, under any naming.

    Matched on fragments rather than a fixed prefix because the router may be
    mounted under the organisation, under the item, or flat, and the claim
    holds either way.

    ``team-assignment`` is the first clause and the one that actually fires:
    the resource is flat — ``/api/organizations/{org_id}/team-assignments`` —
    and discriminates control from evidence with a ``type`` parameter, so
    neither word appears in the path at all. That naming also survives phase
    5, where risk and vendor become further ``?type=`` values on this same
    path rather than new routes. The second clause stays for a router mounted
    under the item instead.

    ``_assignment_operations`` additionally accepts anything whose schema
    carries ``is_accountable`` at any depth, so a path avoiding all of these
    words is still found.
    """
    lowered = path.lower()
    if "team-assignment" in lowered or "team_assignment" in lowered:
        return True
    return "team" in lowered and ("control" in lowered or "evidence" in lowered)


def _assignment_operations():
    """(method, path, operation) for every team-assignment endpoint in the schema."""
    spec = _spec()
    found = []
    for path, operations in spec["paths"].items():
        for method, operation in operations.items():
            if method.upper() == "PARAMETERS" or not isinstance(operation, dict):
                continue
            if _looks_like_an_assignment_path(path) or _schema_mentions(
                spec, operation, "is_accountable"
            ):
                found.append((method.upper(), path, operation))
    return found


def _assignment_mutations():
    return [
        (method, path, operation)
        for method, path, operation in _assignment_operations()
        if method in MUTATING_METHODS
    ]


def _assignment_reads():
    return [
        (method, path, operation)
        for method, path, operation in _assignment_operations()
        if method == "GET"
    ]


def _absent(what: str) -> str:
    return (
        f"No team-assignment {what} is registered on this branch — the API "
        "workstream is building it in parallel and backend/api/"
        "team_assignments.py is not on disk here. This is a SKIP, NOT A PASS. "
        "It starts executing unchanged once that branch merges."
    )


def _fill_path_params(path: str, values: dict | None = None) -> str:
    """Substitute ``{param}`` with a UUID, or with a supplied value.

    A non-UUID path parameter would 422, which is harmless for the RBAC tests:
    FastAPI solves sub-dependencies before it validates path, query and body,
    so an authorisation refusal still arrives as a 403.
    """
    values = values or {}
    return re.sub(
        r"\{([^{}]+)\}",
        lambda m: str(values.get(m.group(1), uuid.uuid4())),
        path,
    )


# ---------------------------------------------------------------------------
# The guards that keep every skip below honest. These never skip.
# ---------------------------------------------------------------------------

class TestTheRouteDiscoveryStillWorks:
    """Proves the machinery finds routes that exist *today*.

    Without this, a FastAPI change that broke discovery would turn every
    deferred test in this file into a permanent silent skip, and the suite
    would keep reporting success while asserting nothing.
    """

    def test_the_schema_walk_finds_the_phase_one_team_routes(self):
        paths = [p for p in _spec()["paths"] if "/teams" in p]
        assert "/api/organizations/{org_id}/teams" in paths
        assert "/api/organizations/{org_id}/teams/{team_id}/members" in paths

    def test_the_route_walk_finds_the_same_endpoints(self):
        # The recursion through _IncludedRouter is the fragile part. If it
        # silently returned nothing, the audit test below would skip for ever.
        keys = _routes_by_key()
        assert ("GET", "/api/organizations/{org_id}/teams") in keys
        assert ("POST", "/api/organizations/{org_id}/teams") in keys

    def test_every_documented_operation_has_a_route_object_behind_it(self):
        # The two mechanisms must agree, or a test that reads the schema and a
        # test that reads the endpoint are talking about different things.
        keys = set(_routes_by_key())
        missing = [
            (method.upper(), path)
            for path, operations in _spec()["paths"].items()
            for method in operations
            if method.upper() != "PARAMETERS"
            and (method.upper(), path) not in keys
        ]
        assert missing == []

    def test_the_walk_reaches_endpoint_functions(self):
        route = _routes_by_key()[("POST", "/api/organizations/{org_id}/teams")]
        assert inspect.getsource(route.endpoint)


class TestTheBadgeStaysOutOfEveryOtherResponse:
    """Only the team-assignment routes expose accountability. App-wide.

    Phase 3's fourth invariant is that it is purely additive: the accountable
    badge is served by one indexable bulk read, so a list that wants to show
    ownership adds a column without that list's own shape changing. The badge
    being absent from ``scoped-controls-paginated`` and the evidence list is
    therefore the design, not an oversight, and reversing it is a decision
    somebody should have to argue for rather than something that arrives as a
    schema change.

    This is also the narrowing clause that keeps the discovery in this file
    honest, and it fails rather than skips. ``_assignment_operations`` matches
    anything mentioning ``is_accountable`` at any depth, so a list response
    that embedded an assignment would be silently swept in — and then the RBAC
    test would start demanding org-admin of a controls-list mutation, and
    ``_bulk_read_operations()[0]`` could measure the wrong endpoint while
    still reporting green. Excluded by PATH, not by the schema predicate,
    which would make the assertion circular and vacuously true.

    ONE BLIND SPOT, AND DO NOT CASUALLY "FIX" IT.
    ``GET /api/organizations/{org_id}/scoped-controls-paginated`` declares no
    ``response_model``: it hand-builds a dict of roughly twenty-five keys, some
    of them nested, and returns it raw (``api/scoped_controls.py``). Its
    response is therefore untyped in the schema and this test cannot see inside
    it. Blind in the safe direction — an untyped response can never be swept
    into route discovery — but blind.

    The obvious repair is to add a ``response_model`` "so the schema documents
    it". That is a trap. ``response_model`` is a FILTER: every key the Pydantic
    model fails to declare is silently dropped from the response, with no error
    and no warning. On a hand-built twenty-five-key payload it turns a one-line
    annotation into a filtering layer over the whole contract, and one
    forgotten key is a silent API break that no backend test catches — the
    handler still returns its dict, and a handler-level test still passes. Only
    a full-payload HTTP comparison sees it.

    If it is ever added, the sequence is capture-hash-diff: hash the current
    JSON with sorted keys, add the model, diff, and treat any difference as a
    break unless it was the point. Until then the missing ``response_model`` is
    a finding, not a defect to close.
    """

    def test_no_other_operation_exposes_is_accountable(self):
        spec = _spec()
        offenders, scanned = [], 0
        for path, operations in spec["paths"].items():
            if _looks_like_an_assignment_path(path):
                continue
            for method, operation in operations.items():
                if method.upper() == "PARAMETERS" or not isinstance(operation, dict):
                    continue
                scanned += 1
                if _schema_mentions(spec, operation, "is_accountable"):
                    offenders.append((method.upper(), path))

        # The scan has to have covered the app, or "no offenders" would mean
        # "nothing was looked at". The platform ships hundreds of operations;
        # anything this low means the walk broke, not that the app shrank.
        assert scanned > 100, f"only {scanned} operations scanned — the walk is broken"
        assert offenders == [], (
            "these non-assignment operations expose is_accountable: "
            f"{offenders}. Either the accountable badge has been embedded in a "
            "list response — which reverses a deliberate phase-3 decision and "
            "should be argued explicitly — or a new assignment route needs "
            "adding to _looks_like_an_assignment_path. Until one of those is "
            "settled, route discovery in this file is quietly wrong."
        )


class TestDiscoveryIsNotSilentlyEmpty:
    """If the API landed, this file must have found it.

    A failure here means the assignment routes exist under a shape
    ``_looks_like_an_assignment_path`` did not anticipate, and the deferred
    tests below are skipping when they should be running. Failing is the
    point: a skip would hide exactly this.
    """

    def test_an_accountable_field_in_the_schema_implies_discovered_routes(self):
        if "is_accountable" not in json.dumps(_spec()):
            pytest.skip(
                "No is_accountable field anywhere in the OpenAPI schema, so "
                "the assignment API is genuinely not on this branch yet. "
                "SKIP, not a pass."
            )
        assert _assignment_operations(), (
            "the schema documents an is_accountable field but no route was "
            "recognised as a team-assignment route — widen "
            "_looks_like_an_assignment_path; every deferred test in this file "
            "is currently skipping when it should be running"
        )

    def test_discovery_does_not_sweep_up_unrelated_routes(self):
        # The mirror: a predicate loose enough to match everything would make
        # the RBAC test below assert things about routes it knows nothing of.
        for _method, path, _operation in _assignment_operations():
            assert "team" in path.lower() or _schema_mentions(
                _spec(), _operation, "is_accountable"
            ), path


# ---------------------------------------------------------------------------
# Teams grant no permissions — an editor cannot reassign work
# ---------------------------------------------------------------------------

ORG_ID = uuid.uuid4()
USER_DB_ID = uuid.uuid4()


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _EditorSession:
    """Answers exactly the two queries ``verify_org_membership`` issues.

    The organisation exists and the caller is a direct member of it with the
    ``editor`` role. Everything after that is the real refusal logic in
    ``auth.verify_org_membership`` — this fake supplies the rows, not the
    decision.
    """

    def __init__(self):
        self._scripted = [
            SimpleNamespace(id=ORG_ID),
            SimpleNamespace(role="editor", user_id=USER_DB_ID),
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


class TestAnEditorCannotChangeAssignments:
    """Assignment is governance metadata: an admin decides who owns a control.

    This encodes a design decision. If the API workstream lands
    editor-managed assignments, this test fails — and that failure is the
    conversation to have, not a broken test to delete.
    """

    def test_every_mutating_assignment_route_refuses_an_editor(self, editor_client):
        mutations = _assignment_mutations()
        if not mutations:
            pytest.skip(_absent("mutation route"))

        refused, allowed = [], []
        for method, path, _operation in mutations:
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
            "assignment mutations must require org admin; these did not "
            f"refuse an org editor: {allowed}"
        )
        assert refused, "no mutating assignment route was exercised"

    def test_any_org_member_may_read_assignments(self, editor_client):
        """The refusal above must be about the role, not about the harness.

        A GET that also 403s would mean the fixture cannot reach the router at
        all and the test above proves nothing. It also pins the criterion in
        its own right: reading who owns a control is open to every member.
        """
        reads = _assignment_reads()
        if not reads:
            pytest.skip(_absent("read route"))

        forbidden = []
        for _method, path, _operation in reads:
            response = editor_client.request(
                "GET",
                _fill_path_params(path),
                headers={"Authorization": "Bearer test-token"},
            )
            if response.status_code == 403:
                forbidden.append((path, response.status_code))

        assert forbidden == [], (
            "reading assignments must be open to any org member: "
            f"{forbidden}"
        )


# ---------------------------------------------------------------------------
# Every mutation is on the record
# ---------------------------------------------------------------------------

class TestEveryMutationIsAudited:
    def test_every_mutating_route_writes_an_audit_entry(self):
        """Invariant: no reassignment of accountability happens off the record.

        Who owns a control is exactly the fact an auditor asks about after the
        event, and a change with no trail is worse than no change.

        Follows one level of indirection on purpose. Phase 1's ``teams.py``
        calls the audit helper inline, but that is house style rather than a
        rule, and a router that factors the twelve-argument call into a
        module-level ``_audit()`` — fixing the entity type, the tracked-field
        set and the request plumbing once instead of three times — audits
        just as truthfully. Insisting on the literal string would have made
        the code worse to satisfy a string match, so the test moved instead.
        """
        mutations = _assignment_mutations()
        if not mutations:
            pytest.skip(_absent("mutation route"))

        routes = _routes_by_key()
        silent = []
        for method, path, _operation in mutations:
            route = routes.get((method, path))
            assert route is not None, f"{method} {path} is in the schema but has no route"
            if not _leaves_an_audit_trail(route.endpoint):
                silent.append((method, path))

        assert silent == [], (
            f"these mutations write no audit_log row: {silent} "
            f"(expected one of {AUDIT_HELPERS})"
        )

    def test_every_mutating_route_takes_the_request_it_audits_from(self):
        """``detect_action_source``/``get_request_id`` need the live Request.

        Without the parameter the entry cannot say whether the change came
        from the UI, an API key or MCP — which is most of what makes it useful.
        """
        from fastapi import Request

        mutations = _assignment_mutations()
        if not mutations:
            pytest.skip(_absent("mutation route"))

        routes = _routes_by_key()
        missing = [
            (method, path)
            for method, path, _operation in mutations
            if not any(
                parameter.annotation is Request
                for parameter in inspect.signature(
                    routes[(method, path)].endpoint
                ).parameters.values()
            )
        ]
        assert missing == []


# ---------------------------------------------------------------------------
# The bulk read must not be an N+1
# ---------------------------------------------------------------------------

@pytest.fixture
def query_counter():
    """Counts every statement any engine sends, while armed.

    Listens on the ``Engine`` class rather than one instance because the
    session under test is created inside the request, on the TestClient's own
    event loop, and is not reachable from out here.
    """
    state = {"count": 0, "armed": False}

    @sa.event.listens_for(Engine, "before_cursor_execute")
    def _count(_conn, _cursor, _statement, _parameters, _context, _many):
        if state["armed"]:
            state["count"] += 1

    class Counter:
        def measure(self, call):
            state["count"] = 0
            state["armed"] = True
            try:
                return call(), state["count"]
            finally:
                state["armed"] = False

    try:
        yield Counter()
    finally:
        sa.event.remove(Engine, "before_cursor_execute", _count)


def _run(coroutine_factory):
    """Run one coroutine on a throwaway engine and event loop.

    The live tests below are synchronous, because ``TestClient`` is. Each call
    therefore gets its own engine: an asyncpg connection belongs to the loop
    that created it, and reusing one across ``asyncio.run`` calls fails with
    "another operation is in progress".
    """
    async def _inner():
        engine = create_async_engine(DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                return await coroutine_factory(session)
        finally:
            await engine.dispose()

    return asyncio.run(_inner())


class _Estate:
    def __init__(self, org_id, user_id, team_id, function_id):
        self.org_id = org_id
        self.user_id = user_id
        self.team_id = team_id
        self.function_id = function_id
        self.control_ids: list = []
        self.evidence_ids: list = []


@pytest.fixture
def estate():
    """A real, committed organisation, torn down whatever happens.

    Committed rather than held in a rolled-back transaction because the
    request under test runs on the TestClient's event loop, on its own
    connection, and cannot see an uncommitted one. Everything is tagged with a
    fresh uuid and removed in the fixture's teardown.
    """
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("needs a Postgres DATABASE_URL — SKIPPED, not passed")

    tag = uuid.uuid4().hex[:10]

    async def _create(session):
        function = (await session.execute(
            sa.select(Function).where(Function.is_active.is_(True)).limit(1)
        )).scalar_one_or_none()
        if function is None:  # pragma: no cover - environment dependent
            pytest.skip("no seeded functions in this database")

        org = Organization(name=f"nplus-{tag}", slug=f"nplus-{tag}")
        session.add(org)
        await session.flush()

        user = User(email=f"nplus-{tag}@example.invalid", google_sub=f"sub-{tag}")
        session.add(user)
        await session.flush()
        session.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, role="admin",
        ))

        team = Team(
            organization_id=org.id, function_id=function.id, name=f"team-{tag}",
        )
        session.add(team)
        await session.flush()
        await session.commit()
        return _Estate(org.id, user.id, team.id, function.id)

    built = _run(_create)

    async def _destroy(session):
        for model, column in (
            (ControlTeamAssignment, "organization_id"),
            (EvidenceTeamAssignment, "organization_id"),
            (ScopedControl, "organization_id"),
            (EvidenceTracking, "organization_id"),
            (Team, "organization_id"),
            (OrganizationMember, "organization_id"),
        ):
            await session.execute(
                sa.delete(model).where(getattr(model, column) == built.org_id)
            )
        await session.execute(
            sa.delete(Organization).where(Organization.id == built.org_id)
        )
        # The user last: it is not org-scoped, so nothing above reaches it and
        # a run would otherwise leave one behind every time.
        await session.execute(sa.delete(User).where(User.id == built.user_id))
        await session.commit()

    try:
        yield built
    finally:
        _run(_destroy)


def _add_items(estate, count, *, assign=True):
    """Add ``count`` controls and evidence records, each with an assignment."""

    async def _create(session):
        for _ in range(count):
            tag = uuid.uuid4().hex[:12]
            control = ScopedControl(
                organization_id=estate.org_id, scf_id=f"NP-{tag}",
            )
            evidence = EvidenceTracking(
                organization_id=estate.org_id, evidence_id=f"NPE-{tag}",
            )
            session.add_all([control, evidence])
            await session.flush()
            estate.control_ids.append(control.id)
            estate.evidence_ids.append(evidence.id)
            if assign:
                session.add(ControlTeamAssignment(
                    scoped_control_id=control.id,
                    team_id=estate.team_id,
                    organization_id=estate.org_id,
                    is_accountable=True,
                ))
                session.add(EvidenceTeamAssignment(
                    evidence_tracking_id=evidence.id,
                    team_id=estate.team_id,
                    organization_id=estate.org_id,
                    is_accountable=True,
                ))
        await session.commit()

    _run(_create)


@pytest.fixture
def live_client(estate):
    """A TestClient whose ``get_db`` is a real session and whose org role is admin.

    The org-role dependency is overridden by the exact closure object the
    route was built with — ``require_org_role`` is a factory, so each call
    produces a distinct callable and only the one on the route will match.
    """
    async def _override_db():
        engine = create_async_engine(DATABASE_URL)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as session:
                yield session
        finally:
            await engine.dispose()

    async def _membership():
        return auth.OrgMembership(
            user=SimpleNamespace(
                db_id=str(estate.user_id),
                email="nplus@example.invalid",
                auth_method="oauth",
                is_platform_admin=False,
            ),
            organization_id=estate.org_id,
            role="admin",
        )

    main.app.dependency_overrides[get_db] = _override_db
    for route in _routes_by_key().values():
        # Starlette's own Route objects (static mounts, the docs endpoints)
        # have no dependency graph; only APIRoute does.
        for dependency in getattr(route, "dependant", SimpleNamespace(dependencies=[])).dependencies:
            call = dependency.call
            freevars = getattr(getattr(call, "__code__", None), "co_freevars", ())
            if "min_role" in freevars:
                main.app.dependency_overrides[call] = _membership
    try:
        yield TestClient(main.app, raise_server_exceptions=False)
    finally:
        main.app.dependency_overrides.clear()


@requires_postgres
class TestTheQueryCounterWorks:
    """Exercises the N+1 harness against a route that exists today.

    The test below it cannot run until the assignment API merges. This one can,
    and it is what stops the harness from rotting into a permanent skip: if
    the counter stopped counting, or the dependency override stopped
    overriding, this goes red now rather than in three weeks.
    """

    def test_it_counts_a_real_request_against_the_phase_one_team_list(
        self, estate, live_client, query_counter
    ):
        path = f"/api/organizations/{estate.org_id}/teams"

        response, queries = query_counter.measure(lambda: live_client.get(path))

        assert response.status_code == 200, response.text
        assert queries > 0, (
            "the counter recorded no statements for a request that must have "
            "read the database — the harness is broken, not the route"
        )

    def test_the_team_list_itself_does_not_scale_with_team_count(
        self, estate, live_client, query_counter
    ):
        """A free assertion, and a second check on the harness.

        If adding nine teams changed the count here, either the phase-1 list
        has an N+1 or the counter is measuring something other than the
        request.
        """
        path = f"/api/organizations/{estate.org_id}/teams"
        _first, small = query_counter.measure(lambda: live_client.get(path))

        async def _more(session):
            for index in range(9):
                session.add(Team(
                    organization_id=estate.org_id,
                    function_id=estate.function_id,
                    name=f"extra-{index}-{uuid.uuid4().hex[:6]}",
                ))
            await session.commit()

        _run(_more)
        response, large = query_counter.measure(lambda: live_client.get(path))

        assert response.status_code == 200
        assert len(response.json()) == 10
        assert large == small, f"{small} queries for 1 team, {large} for 10"


def _accountable_mutations():
    """Mutations that can set ``is_accountable`` — the handover endpoints."""
    spec = _spec()
    return [
        (method, path, operation)
        for method, path, operation in _assignment_mutations()
        if _schema_mentions(spec, operation.get("requestBody", {}), "is_accountable")
    ]


def _request_schema(spec: dict, operation: dict) -> dict:
    body = _resolve(spec, operation.get("requestBody", {}))
    content = (body.get("content") or {}).get("application/json") or {}
    return _resolve(spec, content.get("schema", {}))


def _enum_values(definition: dict) -> list:
    """The literal values a string property accepts.

    Read from ``enum`` when there is one, otherwise parsed out of a
    ``^(a|b)$`` pattern. The assignable types live in exactly one of those two
    places depending on how the field was declared, and hard-coding
    ``["control", "evidence"]`` here would silently stop covering phase 5's
    risk and vendor types the moment they are added to the same endpoint.
    """
    if definition.get("enum"):
        return list(definition["enum"])
    pattern = definition.get("pattern") or ""
    match = re.fullmatch(r"\^\(([^)]+)\)\$", pattern)
    return match.group(1).split("|") if match else []


def _assignable_types(spec: dict, operation: dict) -> list:
    """The values the operation's ``type`` discriminator accepts.

    The resource is flat and one endpoint serves every assignable type, so
    "which types exist" is a fact about the schema, not about this test.
    """
    schema = _request_schema(spec, operation)
    for name, definition in (schema.get("properties") or {}).items():
        if "type" in name.lower():
            values = _enum_values(_resolve(spec, definition))
            if values:
                return values
    for parameter in operation.get("parameters") or []:
        parameter = _resolve(spec, parameter)
        if "type" in parameter.get("name", "").lower():
            values = _enum_values(_resolve(spec, parameter.get("schema", {})))
            if values:
                return values
    return []


def _required_query(spec: dict, operation: dict, values: dict) -> dict:
    """Fill the operation's required query parameters, by the same keywords.

    The bulk read takes a required ``type`` discriminator in the query string
    rather than in the path. Without this the request 422s, and a 422 is
    indistinguishable from the endpoint rejecting the call — which would make
    the N+1 measurement below meaningless rather than merely absent.
    """
    query, unfillable = {}, []
    for parameter in operation.get("parameters") or []:
        parameter = _resolve(spec, parameter)
        if parameter.get("in") != "query" or not parameter.get("required"):
            continue
        name = parameter["name"]
        matched = next(
            (value for keyword, value in values.items() if keyword in name.lower()),
            None,
        )
        if matched is not None:
            query[name] = str(matched) if isinstance(matched, uuid.UUID) else matched
        else:
            unfillable.append(name)

    if unfillable:
        pytest.fail(
            f"required query parameters {unfillable} match none of "
            f"{sorted(values)}; add the keyword to the caller's map"
        )
    return query


def _build_body(spec: dict, operation: dict, values: dict) -> dict:
    """Fill the operation's own request schema from ``values``, by keyword.

    Derived from the schema rather than hard-coded so this test survives
    whatever the API workstream names its fields. A required property that
    cannot be filled raises rather than being quietly omitted — an omitted
    required field would come back as a 422 and be indistinguishable from the
    endpoint refusing the operation, which is the thing under test.
    """
    schema = _request_schema(spec, operation)
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    body, unfillable = {}, []
    for name, definition in properties.items():
        definition = _resolve(spec, definition)
        matched = next(
            (value for keyword, value in values.items() if keyword in name.lower()),
            None,
        )
        if matched is not None:
            body[name] = str(matched) if isinstance(matched, uuid.UUID) else matched
        elif "default" in definition:
            body[name] = definition["default"]
        elif name in required:
            unfillable.append(name)

    if unfillable:
        pytest.fail(
            f"cannot build a request body for this operation: required "
            f"{unfillable} matches none of {sorted(values)}. Add the keyword "
            "to the map in the test that called this — the endpoint exists, "
            "so this criterion should be under test rather than skipped."
        )
    return body


@requires_postgres
class TestHandingAccountabilityOverIsAtomic:
    """One request moves accountability, and one row holds it afterwards.

    ``uq_control_accountable_team`` is a non-deferrable partial unique index,
    so the incumbent must be cleared and flushed before the challenger is
    promoted — both inside a single transaction. Two requests would leave a
    window with no owner at all, and a half-applied one would leave the
    control unowned for good.

    The database mechanism that forces this is proved today in
    ``test_control_evidence_team_assignment_constraints.py``
    (``TestHandingAccountabilityOverIsOneTransaction``). What is proved here
    is that the endpoint uses it correctly, which needs the endpoint.
    """

    #: How each assignable type maps onto a table and a column. Keyed by the
    #: discriminator value the schema itself declares, so a type the endpoint
    #: gains and this map lacks fails loudly instead of going untested.
    MODELS = {
        "control": (ControlTeamAssignment, "scoped_control_id"),
        "evidence": (EvidenceTeamAssignment, "evidence_tracking_id"),
    }

    def test_one_request_moves_accountability_and_leaves_exactly_one_holder(
        self, estate, live_client
    ):
        """Run once per assignable type the endpoint declares.

        Both tables carry their own partial unique index, so proving the
        handover for controls says nothing about evidence. The types are read
        off the schema rather than listed here: phase 5 adds risk and vendor
        to the same endpoint, and this should start covering them without an
        edit — or say plainly that it cannot.
        """
        mutations = _accountable_mutations()
        if not mutations:
            pytest.skip(_absent("route that sets is_accountable"))

        _add_items(estate, 1)
        method, path, operation = mutations[0]

        types = _assignable_types(_spec(), operation) or ["control"]
        unknown = [name for name in types if name not in self.MODELS]
        assert unknown == [], (
            f"the endpoint accepts assignable types {unknown} that this test "
            "cannot check; add them to MODELS rather than leaving them untested"
        )

        for assignable in types:
            model, column = self.MODELS[assignable]
            item_id = (
                estate.control_ids[0] if assignable == "control"
                else estate.evidence_ids[0]
            )
            self._check_one_handover(
                estate, live_client, method, path, operation,
                assignable, model, column, item_id,
            )

    def _check_one_handover(
        self, estate, live_client, method, path, operation,
        assignable, model, column, item_id,
    ):
        async def _challenger_team(session):
            team = Team(
                organization_id=estate.org_id,
                function_id=estate.function_id,
                name=f"challenger-{uuid.uuid4().hex[:8]}",
            )
            session.add(team)
            await session.flush()
            await session.commit()
            return team.id

        challenger = _run(_challenger_team)

        # `_add_items` already left an accountable incumbent on this item, so
        # this request is a handover and not a first assignment — which is the
        # only version of it that exercises the clear-then-promote path.
        values = {
            "org": estate.org_id,
            "team": challenger,
            "item": item_id,
            "type": assignable,
            "control": estate.control_ids[0],
            "evidence": estate.evidence_ids[0],
            "accountable": True,
        }
        spec = _spec()
        response = live_client.request(
            method,
            _fill_path_params(path, {
                "org_id": estate.org_id,
                "organization_id": estate.org_id,
                "item_id": item_id,
                "scoped_control_id": estate.control_ids[0],
                "control_id": estate.control_ids[0],
                "evidence_tracking_id": estate.evidence_ids[0],
                "team_id": challenger,
            }),
            params=_required_query(spec, operation, values),
            json=_build_body(spec, operation, values),
        )
        # Any 2xx. The endpoint is an idempotent upsert: a new (item, team)
        # pair is a 201 and an existing one a 200, and which of those a
        # handover produces is not this test's business.
        assert response.status_code < 300, (
            f"{method} {path} [{assignable}] -> {response.status_code} "
            f"{response.text}"
        )

        async def _holders(session):
            return (await session.execute(
                sa.select(model.team_id).where(
                    getattr(model, column) == item_id,
                    model.is_accountable.is_(True),
                )
            )).scalars().all()

        holders = _run(_holders)
        assert len(holders) == 1, (
            f"[{assignable}] {len(holders)} accountable teams after the "
            "handover; the swap was not one atomic operation"
        )
        assert holders[0] == challenger, (
            f"[{assignable}] the request succeeded but accountability did "
            "not move"
        )


def _bulk_read_operations():
    """Assignment GETs that return many items — the ones an N+1 would ruin.

    A read whose path names a single control or evidence item is not a bulk
    read; anything else is. Identified by path shape rather than by name so it
    survives whatever the endpoint ends up being called.
    """
    bulk = []
    for method, path, operation in _assignment_reads():
        params = re.findall(r"\{([^{}]+)\}", path)
        item_params = [
            p for p in params
            if p not in {"org_id", "organization_id"}
        ]
        if not item_params:
            bulk.append((method, path, operation))
    return bulk


@requires_postgres
class TestTheBulkReadIsNotAnNPlusOne:
    def test_the_query_count_does_not_scale_with_item_count(
        self, estate, live_client, query_counter
    ):
        """Three items and twelve items must cost the same number of queries.

        Not "few queries" — *the same* queries. An N+1 is invisible on a demo
        tenant with four controls and fatal on a real one with nine hundred,
        so the assertion has to be about the shape of the growth, not about a
        threshold that happens to hold today.
        """
        bulk = _bulk_read_operations()
        if not bulk:
            pytest.skip(_absent("bulk read route"))

        _method, path, operation = bulk[0]
        url = _fill_path_params(path, {
            "org_id": estate.org_id, "organization_id": estate.org_id,
        })
        spec = _spec()
        # The bulk read takes its assignable type as a required query
        # parameter. Left off, every call below would be a 422 and the
        # comparison would be between two identical failures.
        assignable = (_assignable_types(spec, operation) or ["control"])[0]
        query = _required_query(spec, operation, {
            "org": estate.org_id, "type": assignable,
        })
        # Deliberately unfiltered by item id: the whole-organisation map is
        # the call the controls list actually makes, and the one an N+1 ruins.

        def _call():
            return live_client.get(url, params=query)

        _add_items(estate, 3)
        first_response, small = query_counter.measure(_call)
        assert first_response.status_code == 200, first_response.text

        _add_items(estate, 9)
        second_response, large = query_counter.measure(_call)
        assert second_response.status_code == 200, second_response.text

        assert large == small, (
            f"{small} queries for 3 items and {large} for 12: the bulk read "
            f"at {path} issues a query per item"
        )


class TestTheBulkReadFetchesInOnePass:
    """The same criterion, read off the handler rather than measured.

    Structural, so it needs no database and no request, and it names the
    defect precisely: a query awaited inside a loop over items. It is the
    version of this criterion that will still run in a CI with no Postgres,
    where the measured test above skips.
    """

    def test_no_assignment_read_awaits_a_query_inside_a_loop(self):
        reads = _assignment_reads()
        if not reads:
            pytest.skip(_absent("read route"))

        routes = _routes_by_key()
        offenders = []
        for method, path, _operation in reads:
            source = textwrap.dedent(inspect.getsource(routes[(method, path)].endpoint))
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
                    continue
                for inner in ast.walk(node):
                    if not isinstance(inner, ast.Await):
                        continue
                    call = inner.value
                    attribute = getattr(getattr(call, "func", None), "attr", None)
                    if attribute in QUERY_AWAITS:
                        offenders.append((method, path, attribute, inner.lineno))

        assert offenders == [], (
            "a database call awaited inside a loop is an N+1; fetch every "
            f"item's assignments in one statement instead: {offenders}"
        )
