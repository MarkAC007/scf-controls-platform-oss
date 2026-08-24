"""Server-side team and function filters on the controls and evidence lists (#822 phase 3).

These filters cannot live in the client. ``list_scoped_controls_paginated`` is
server-paginated (``limit`` defaults to 50, caps at 200), so a client filtering
the rows it happens to hold would filter one page: a control owned by the
selected team on page 3 would simply not appear and the user would be told
there are no matches. A filter that lies is the same failure this whole issue
exists to stop.

Driven over HTTP against a real database, because what is being asserted is the
behaviour of the whole endpoint — its filters composing, its pagination
``total``, and the shape of its response — not the behaviour of a query
fragment. Auth is stubbed at the module the ``require_org_role`` closures
resolve through; everything below that is genuine.

The session handed to the app is the test's own, inside a transaction that is
always rolled back. Both endpoints under test are read-only, so nothing commits
and nothing is left behind.

The trap these tests exist to catch is **row multiplication**. "Any assigned
team" means a control with three assigned teams matches three assignment rows,
so filtering with a JOIN would return that control three times and count it
three times in ``total``. The implementation uses a correlated EXISTS — a
semi-join — and ``test_a_control_with_two_assigned_teams_appears_exactly_once``
is what holds it to that.
"""
import os
import sys
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401
from catalog_models import SCFCatalogControl  # noqa: E402
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

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL",
)


@pytest.fixture
async def db():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")

    session_factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    session = session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


class _Scenario:
    def __init__(self, org, fn_a, fn_b, team_a, team_b, team_other_fn):
        self.org = org
        self.fn_a = fn_a
        self.fn_b = fn_b
        self.team_a = team_a
        self.team_b = team_b
        self.team_other_fn = team_other_fn
        self.controls = {}
        self.evidence = {}


@pytest.fixture
async def scenario(db):
    """One org, two functions, three teams, four catalog-backed controls."""
    functions = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).order_by(Function.key).limit(2)
    )).scalars().all()
    if len(functions) < 2:  # pragma: no cover - environment dependent
        pytest.skip("need two seeded functions")
    fn_a, fn_b = functions

    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"filt-{tag}", slug=f"filt-{tag}")
    db.add(org)
    await db.flush()

    team_a = Team(organization_id=org.id, function_id=fn_a.id, name=f"A {tag}")
    team_b = Team(organization_id=org.id, function_id=fn_a.id, name=f"B {tag}")
    team_other_fn = Team(organization_id=org.id, function_id=fn_b.id, name=f"C {tag}")
    db.add_all([team_a, team_b, team_other_fn])
    await db.flush()

    s = _Scenario(org, fn_a, fn_b, team_a, team_b, team_other_fn)

    # Real catalog rows, so the controls endpoint (which is catalog-driven,
    # LEFT JOINing scoped_controls) actually returns them.
    catalog = (await db.execute(
        select(SCFCatalogControl)
        .where(SCFCatalogControl.status == "active")
        .order_by(SCFCatalogControl.scf_id)
        .limit(4)
    )).scalars().all()
    if len(catalog) < 4:  # pragma: no cover - environment dependent
        pytest.skip("need four active catalog controls")
    s.catalog = catalog

    for key, cat in zip(("accountable", "consulted", "two_teams", "unassigned"), catalog):
        sc = ScopedControl(organization_id=org.id, scf_id=cat.scf_id, selected=True)
        db.add(sc)
        s.controls[key] = sc
    await db.flush()

    db.add_all([
        # team_a is ACCOUNTABLE for this one
        ControlTeamAssignment(scoped_control_id=s.controls["accountable"].id,
                              team_id=team_a.id, organization_id=org.id,
                              is_accountable=True),
        # team_a is merely CONSULTED here - must still match a team filter
        ControlTeamAssignment(scoped_control_id=s.controls["consulted"].id,
                              team_id=team_a.id, organization_id=org.id,
                              is_accountable=False),
        # two teams on one control - the row-multiplication trap
        ControlTeamAssignment(scoped_control_id=s.controls["two_teams"].id,
                              team_id=team_a.id, organization_id=org.id,
                              is_accountable=True),
        ControlTeamAssignment(scoped_control_id=s.controls["two_teams"].id,
                              team_id=team_b.id, organization_id=org.id,
                              is_accountable=False),
    ])

    for key in ("assigned", "unassigned"):
        ev = EvidenceTracking(organization_id=org.id,
                              evidence_id=f"E-FILT-{key[:3]}-{tag}")
        db.add(ev)
        s.evidence[key] = ev
    await db.flush()
    db.add(EvidenceTeamAssignment(
        evidence_tracking_id=s.evidence["assigned"].id, team_id=team_a.id,
        organization_id=org.id, is_accountable=True,
    ))
    await db.flush()
    return s


@pytest.fixture
async def client(db, scenario):
    """The real app, real session, auth stubbed to an admin of the scenario org."""
    import main
    from auth import OrgMembership, User as AuthUser
    from database import get_db
    import auth as auth_mod

    user = User(email=f"filt-{uuid.uuid4().hex[:8]}@example.invalid",
                google_sub=f"f-{uuid.uuid4().hex[:8]}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=scenario.org.id,
                              user_id=user.id, role="admin"))
    await db.flush()

    membership = OrgMembership(
        user=AuthUser(user_id="stub", email=user.email, db_id=str(user.id),
                      auth_method="google"),
        organization_id=scenario.org.id, role="admin",
    )

    async def _db():
        # The test's own session, so every read happens inside the transaction
        # that gets rolled back. Both endpoints are read-only, so nothing here
        # commits.
        yield db

    original = (auth_mod.require_auth, auth_mod.verify_org_membership)

    async def _require_auth(*a, **k):
        return membership.user

    async def _verify(org_id, user, db_, min_role="viewer"):
        return membership

    # FastAPI 0.141 hides included routes behind _IncludedRouter, so
    # dependency_overrides cannot reach the require_org_role closures. Stubbing
    # the module they resolve through is what works.
    auth_mod.require_auth = _require_auth
    auth_mod.verify_org_membership = _verify
    main.app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://filters",
            headers={"Authorization": "Bearer stub"},
        ) as c:
            yield c
    finally:
        auth_mod.require_auth, auth_mod.verify_org_membership = original
        main.app.dependency_overrides.pop(get_db, None)


async def _controls(client, scenario, **params):
    r = await client.get(
        f"/api/organizations/{scenario.org.id}/scoped-controls-paginated",
        params=params,
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _evidence(client, scenario, **params):
    r = await client.get(
        f"/api/organizations/{scenario.org.id}/evidence-tracking", params=params,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _ids(payload):
    return {c["scf_id"] for c in payload["controls"]}


# ---------------------------------------------------------------------------
# Invariant 5: the no-filter path is untouched
# ---------------------------------------------------------------------------

async def test_omitting_the_new_filters_returns_the_whole_catalogue(client, scenario):
    """Existing callers pass none of these and must see exactly today's result.

    The new parameters are optional and default to None; a caller that does not
    send them must not have their result narrowed by so much as one row.
    """
    baseline = await _controls(client, scenario, limit=200)
    assert baseline["total"] > len(scenario.controls)
    # Every scoped control is present, including the one with no team at all.
    assert _ids(baseline) >= {c.scf_id for c in scenario.catalog}


async def test_the_new_filters_do_not_appear_in_the_response_shape(client, scenario):
    """Purely additive: no new keys, no changed keys."""
    payload = await _controls(client, scenario, limit=1)
    assert set(payload) == {"total", "limit", "offset", "controls"}


async def test_evidence_list_without_filters_is_unchanged(client, scenario):
    rows = await _evidence(client, scenario)
    ids = {e["evidence_id"] for e in rows}
    assert scenario.evidence["assigned"].evidence_id in ids
    assert scenario.evidence["unassigned"].evidence_id in ids


# ---------------------------------------------------------------------------
# team_id
# ---------------------------------------------------------------------------

async def test_team_filter_restricts_to_that_teams_controls(client, scenario):
    payload = await _controls(client, scenario, team_id=str(scenario.team_a.id),
                              limit=200)
    assert _ids(payload) == {
        scenario.catalog[0].scf_id,  # accountable
        scenario.catalog[1].scf_id,  # consulted
        scenario.catalog[2].scf_id,  # two_teams
    }
    assert payload["total"] == 3


async def test_a_consulted_team_still_matches(client, scenario):
    """Any assigned team, not only the accountable one.

    #822: consulted teams "see the item in their team view". Visibility is not
    the same decision as notification routing, where consulted teams are
    deliberately off the routine path.
    """
    payload = await _controls(client, scenario, team_id=str(scenario.team_b.id),
                              limit=200)
    assert _ids(payload) == {scenario.catalog[2].scf_id}


async def test_a_control_with_two_assigned_teams_appears_exactly_once(client, scenario):
    """The row-multiplication trap.

    A JOIN would emit this control once per matching assignment and count it
    twice in ``total``, corrupting both the page and the pagination. EXISTS is
    a semi-join and does neither.
    """
    payload = await _controls(client, scenario, function_id=str(scenario.fn_a.id),
                              limit=200)
    rows = [c for c in payload["controls"]
            if c["scf_id"] == scenario.catalog[2].scf_id]
    assert len(rows) == 1
    assert payload["total"] == len(payload["controls"])


async def test_a_team_with_nothing_assigned_returns_an_empty_page(client, scenario):
    payload = await _controls(client, scenario,
                              team_id=str(scenario.team_other_fn.id), limit=200)
    assert payload["controls"] == []
    assert payload["total"] == 0


async def test_an_unassigned_control_is_excluded_by_a_team_filter(client, scenario):
    payload = await _controls(client, scenario, team_id=str(scenario.team_a.id),
                              limit=200)
    assert scenario.catalog[3].scf_id not in _ids(payload)


async def test_unscoped_catalog_controls_are_excluded_by_a_team_filter(client, scenario):
    """A control the org has never scoped has no scoped_controls row, so it
    cannot have a team. The LEFT JOIN makes its id NULL and EXISTS is false."""
    unfiltered = await _controls(client, scenario, limit=200)
    filtered = await _controls(client, scenario, team_id=str(scenario.team_a.id),
                               limit=200)
    assert len(filtered["controls"]) < len(unfiltered["controls"])
    for control in filtered["controls"]:
        assert control["is_scoped"] is True


# ---------------------------------------------------------------------------
# function_id
# ---------------------------------------------------------------------------

async def test_function_filter_matches_every_team_mapped_to_that_function(
    client, scenario
):
    """team_a and team_b are both aligned to fn_a, so the union of their work."""
    payload = await _controls(client, scenario, function_id=str(scenario.fn_a.id),
                              limit=200)
    assert _ids(payload) == {
        scenario.catalog[0].scf_id,
        scenario.catalog[1].scf_id,
        scenario.catalog[2].scf_id,
    }


async def test_function_filter_excludes_other_functions(client, scenario):
    payload = await _controls(client, scenario, function_id=str(scenario.fn_b.id),
                              limit=200)
    assert payload["controls"] == []


async def test_team_and_function_together_intersect(client, scenario):
    """Both supplied means both must hold, not either."""
    payload = await _controls(client, scenario, team_id=str(scenario.team_b.id),
                              function_id=str(scenario.fn_b.id), limit=200)
    assert payload["controls"] == []


# ---------------------------------------------------------------------------
# Composition with the existing filters
# ---------------------------------------------------------------------------

async def test_team_filter_composes_with_scope_status(client, scenario):
    payload = await _controls(client, scenario, team_id=str(scenario.team_a.id),
                              scope_status="in_scope", limit=200)
    assert len(payload["controls"]) == 3


async def test_team_filter_composes_with_search(client, scenario):
    """Both narrow; neither is dropped.

    ``search`` is a substring ILIKE, not an exact match, so searching "AAT-01"
    legitimately also returns "AAT-01.1". The assertion is therefore the
    intersection of the two filters, which is what composition means -- not the
    single row an exact-match search would give.
    """
    target = scenario.catalog[0].scf_id
    team_a_ids = {c.scf_id for c in scenario.catalog[:3]}
    expected = {i for i in team_a_ids if target.lower() in i.lower()}

    payload = await _controls(client, scenario, team_id=str(scenario.team_a.id),
                              search=target, limit=200)
    got = _ids(payload)

    # The search really narrowed: every row matches it.
    assert all(target.lower() in i.lower() for i in got)
    # The team filter really narrowed: nothing outside team_a survived, in
    # particular the unassigned control, which the bare search would return.
    assert got == expected
    assert scenario.catalog[3].scf_id not in got

    bare_search = await _controls(client, scenario, search=target, limit=200)
    assert len(_ids(bare_search)) > len(got)


async def test_team_filter_composes_with_pagination_and_total(client, scenario):
    """``total`` is the count of ALL matches, not of the page."""
    page = await _controls(client, scenario, team_id=str(scenario.team_a.id),
                           limit=1, offset=0)
    assert len(page["controls"]) == 1
    assert page["total"] == 3


# ---------------------------------------------------------------------------
# Tenancy
# ---------------------------------------------------------------------------

async def test_another_tenants_assignment_cannot_pull_a_control_into_the_list(
    db, client, scenario
):
    """The filter is scoped to the calling organisation's assignments."""
    other = Organization(name=f"filt-o-{uuid.uuid4().hex[:8]}",
                         slug=f"filt-o-{uuid.uuid4().hex[:8]}")
    db.add(other)
    await db.flush()
    other_team = Team(organization_id=other.id, function_id=scenario.fn_a.id,
                      name=f"Other {uuid.uuid4().hex[:6]}")
    db.add(other_team)
    await db.flush()

    payload = await _controls(client, scenario, team_id=str(other_team.id),
                              limit=200)
    assert payload["controls"] == []
    assert payload["total"] == 0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

async def test_evidence_team_filter_restricts(client, scenario):
    rows = await _evidence(client, scenario, team_id=str(scenario.team_a.id))
    assert [e["evidence_id"] for e in rows] == [
        scenario.evidence["assigned"].evidence_id
    ]


async def test_evidence_function_filter_restricts(client, scenario):
    rows = await _evidence(client, scenario, function_id=str(scenario.fn_a.id))
    assert [e["evidence_id"] for e in rows] == [
        scenario.evidence["assigned"].evidence_id
    ]


async def test_evidence_function_filter_excludes_other_functions(client, scenario):
    rows = await _evidence(client, scenario, function_id=str(scenario.fn_b.id))
    assert rows == []


async def test_evidence_team_filter_composes_with_system_id(client, scenario):
    """system_id is the filter that already existed; it must still apply."""
    rows = await _evidence(client, scenario, team_id=str(scenario.team_a.id),
                           system_id=str(uuid.uuid4()))
    assert rows == []


async def test_evidence_with_two_teams_appears_once(db, client, scenario):
    db.add(EvidenceTeamAssignment(
        evidence_tracking_id=scenario.evidence["assigned"].id,
        team_id=scenario.team_b.id, organization_id=scenario.org.id,
        is_accountable=False,
    ))
    await db.flush()

    rows = await _evidence(client, scenario, function_id=str(scenario.fn_a.id))
    assert len(rows) == 1
