"""The ``my_teams`` filter on the controls and evidence lists (#1052).

#822 built the machinery: ``team_assignment_filter`` is an EXISTS clause that
both list endpoints already reach through their ``team_id`` parameter. #1052
adds the small missing piece — resolve *the caller's* teams and pass them in —
so a stakeholder opening Controls or Evidence sees what their team is
responsible for rather than the whole organisation.

Three things here are worth more than the rest, and each has a test that fails
loudly if it regresses:

**An empty set of teams narrows to nothing.** A caller who is on no team asks
"show me my teams' controls" and the only honest answer is zero controls.
Collapsing an empty team set into "no filter" would hand them the entire
organisation under a label promising the opposite. That is the failure mode the
accountable-owner filter was already built to avoid on the evidence screen, and
``test_a_caller_on_no_team_sees_nothing`` is what stops it reappearing here.

**``my_teams`` and ``team_id`` intersect.** Both clauses AND, like every other
filter on these endpoints. Asking for a team you are not on returns nothing
rather than being silently rewritten into the question you did not ask.

**``total`` counts the filtered set.** The clause is applied above the count
subquery, so the pagination footer agrees with the rows above it. A footer
that disagrees with its own page is how #822's filters were nearly shipped
broken, and the EXISTS semi-join is what keeps a control with two of the
caller's teams on it from being counted twice.

Driven over HTTP against a real database, for the same reason the #822 filter
tests are: what is asserted is the behaviour of the whole endpoint — filters
composing, ``total``, response shape — not of a query fragment.
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
    TeamMember,
    User,
)

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
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
    """One org, three teams, and a caller who is on two of them.

    ``mine_a`` and ``mine_b`` are the caller's teams; ``theirs`` is a team in
    the same organisation that the caller has nothing to do with. The whole
    point of the filter is the boundary between those two, so every control and
    evidence item below sits deliberately on one side of it.
    """

    def __init__(self, org, user, mine_a, mine_b, theirs):
        self.org = org
        self.user = user
        self.mine_a = mine_a
        self.mine_b = mine_b
        self.theirs = theirs
        self.controls = {}
        self.evidence = {}
        self.catalog = []


@pytest.fixture
async def scenario(db):
    functions = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).order_by(Function.key).limit(1)
    )).scalars().all()
    if not functions:  # pragma: no cover - environment dependent
        pytest.skip("need a seeded function")
    fn = functions[0]

    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"myteams-{tag}", slug=f"myteams-{tag}")
    db.add(org)
    await db.flush()

    user = User(email=f"mt-{tag}@example.invalid", google_sub=f"mt-{tag}")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="admin"))

    mine_a = Team(organization_id=org.id, function_id=fn.id, name=f"Mine A {tag}")
    mine_b = Team(organization_id=org.id, function_id=fn.id, name=f"Mine B {tag}")
    theirs = Team(organization_id=org.id, function_id=fn.id, name=f"Theirs {tag}")
    db.add_all([mine_a, mine_b, theirs])
    await db.flush()

    # The caller is a plain `member` of one team and the `primary` of the
    # other. #1052 settles "my teams" as ANY membership role, so both must
    # count — this is deliberately not `my_item_filter`, which restricts to
    # accountable roles because it is a notification queue.
    db.add_all([
        TeamMember(team_id=mine_a.id, organization_id=org.id,
                   user_id=user.id, membership_role="member"),
        TeamMember(team_id=mine_b.id, organization_id=org.id,
                   user_id=user.id, membership_role="primary"),
    ])
    await db.flush()

    s = _Scenario(org, user, mine_a, mine_b, theirs)

    catalog = (await db.execute(
        select(SCFCatalogControl)
        .where(SCFCatalogControl.status == "active")
        .order_by(SCFCatalogControl.scf_id)
        .limit(4)
    )).scalars().all()
    if len(catalog) < 4:  # pragma: no cover - environment dependent
        pytest.skip("need four active catalog controls")
    s.catalog = catalog

    for key, cat in zip(("on_mine_a", "on_mine_b_consulted", "on_both_mine", "on_theirs"), catalog):
        sc = ScopedControl(organization_id=org.id, scf_id=cat.scf_id, selected=True)
        db.add(sc)
        s.controls[key] = sc
    await db.flush()

    db.add_all([
        ControlTeamAssignment(scoped_control_id=s.controls["on_mine_a"].id,
                              team_id=mine_a.id, organization_id=org.id,
                              is_accountable=True),
        # Merely CONSULTED, and on the team where the caller is only a
        # `member`. Both narrowings are deliberate: this row is what proves
        # the filter is generous where the notification queue is not.
        ControlTeamAssignment(scoped_control_id=s.controls["on_mine_b_consulted"].id,
                              team_id=mine_b.id, organization_id=org.id,
                              is_accountable=False),
        # Two of the caller's OWN teams on one control — the row-multiplication
        # trap, now reachable through a single parameter.
        ControlTeamAssignment(scoped_control_id=s.controls["on_both_mine"].id,
                              team_id=mine_a.id, organization_id=org.id,
                              is_accountable=True),
        ControlTeamAssignment(scoped_control_id=s.controls["on_both_mine"].id,
                              team_id=mine_b.id, organization_id=org.id,
                              is_accountable=False),
        ControlTeamAssignment(scoped_control_id=s.controls["on_theirs"].id,
                              team_id=theirs.id, organization_id=org.id,
                              is_accountable=True),
    ])

    for key in ("on_mine_a", "on_theirs"):
        ev = EvidenceTracking(organization_id=org.id,
                              evidence_id=f"E-MT-{key[:7]}-{tag}")
        db.add(ev)
        s.evidence[key] = ev
    await db.flush()
    db.add_all([
        EvidenceTeamAssignment(evidence_tracking_id=s.evidence["on_mine_a"].id,
                               team_id=mine_a.id, organization_id=org.id,
                               is_accountable=True),
        EvidenceTeamAssignment(evidence_tracking_id=s.evidence["on_theirs"].id,
                               team_id=theirs.id, organization_id=org.id,
                               is_accountable=True),
    ])
    await db.flush()
    return s


def _client_for(db, scenario, user):
    """An httpx client authenticated as ``user`` against the real app."""
    import main
    from auth import OrgMembership, User as AuthUser
    from database import get_db
    import auth as auth_mod

    membership = OrgMembership(
        user=AuthUser(user_id="stub", email=user.email, db_id=str(user.id),
                      auth_method="google"),
        organization_id=scenario.org.id, role="admin",
    )

    async def _db():
        yield db

    original = (auth_mod.require_auth, auth_mod.verify_org_membership)

    async def _require_auth(*a, **k):
        return membership.user

    async def _verify(org_id, user_, db_, min_role="viewer"):
        return membership

    # FastAPI 0.141 hides included routes behind _IncludedRouter, so
    # dependency_overrides cannot reach the require_org_role closures.
    # Stubbing the module they resolve through is what works.
    auth_mod.require_auth = _require_auth
    auth_mod.verify_org_membership = _verify
    main.app.dependency_overrides[get_db] = _db

    class _Ctx:
        async def __aenter__(self):
            self.c = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=main.app),
                base_url="http://myteams",
                headers={"Authorization": "Bearer stub"},
            )
            return await self.c.__aenter__()

        async def __aexit__(self, *exc):
            await self.c.__aexit__(*exc)
            auth_mod.require_auth, auth_mod.verify_org_membership = original
            main.app.dependency_overrides.pop(get_db, None)

    return _Ctx()


@pytest.fixture
async def client(db, scenario):
    async with _client_for(db, scenario, scenario.user) as c:
        yield c


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
# The parameter is additive: omitting it must change nothing
# ---------------------------------------------------------------------------

async def test_omitting_my_teams_returns_the_whole_catalogue(client, scenario):
    """Default False, and False must be indistinguishable from absent.

    Every existing caller of this endpoint passes neither, and a new optional
    parameter that narrows their result by one row is a breaking change wearing
    a backwards-compatible costume.
    """
    absent = await _controls(client, scenario, limit=200)
    explicit_false = await _controls(client, scenario, my_teams="false", limit=200)
    assert _ids(absent) == _ids(explicit_false)
    assert absent["total"] == explicit_false["total"]
    # The control belonging to a team the caller is not on is present.
    assert scenario.catalog[3].scf_id in _ids(absent)


async def test_my_teams_does_not_change_the_response_shape(client, scenario):
    payload = await _controls(client, scenario, my_teams="true", limit=1)
    assert set(payload) == {"total", "limit", "offset", "controls"}


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

async def test_my_teams_returns_only_the_callers_teams_controls(client, scenario):
    payload = await _controls(client, scenario, my_teams="true", limit=200)
    assert _ids(payload) == {
        scenario.catalog[0].scf_id,  # accountable, team the caller is a member of
        scenario.catalog[1].scf_id,  # consulted, team the caller is primary of
        scenario.catalog[2].scf_id,  # both of the caller's teams
    }
    assert scenario.catalog[3].scf_id not in _ids(payload)


async def test_a_consulted_team_still_counts_as_mine(client, scenario):
    """"My teams" is any assignment, not accountable-only.

    This is the deliberate divergence from ``my_item_filter``. That filter is a
    notification queue and stays narrow, because a queue that pages three
    people is not a queue. A list filter is a different job and is generous.
    """
    payload = await _controls(client, scenario, my_teams="true", limit=200)
    assert scenario.catalog[1].scf_id in _ids(payload)


async def test_a_plain_member_role_still_counts_as_mine(client, scenario):
    """Any membership role, not just primary or delegate.

    ``mine_a`` is the team the caller joined as a plain ``member``. #1052
    settles that membership role does not gate list visibility — only
    notification routing cares about the role.
    """
    payload = await _controls(client, scenario, my_teams="true", limit=200)
    assert scenario.catalog[0].scf_id in _ids(payload)


async def test_a_control_on_two_of_my_teams_appears_exactly_once(client, scenario):
    """The row-multiplication trap, reached through ``my_teams`` this time.

    ``team_ids`` becomes an ``IN`` inside the same correlated EXISTS, so two
    matching assignment rows still stop at the first match. A JOIN here would
    return the control twice and count it twice in ``total``.
    """
    payload = await _controls(client, scenario, my_teams="true", limit=200)
    matches = [c for c in payload["controls"] if c["scf_id"] == scenario.catalog[2].scf_id]
    assert len(matches) == 1


async def test_total_counts_the_filtered_set(client, scenario):
    """The footer must agree with the page above it.

    The clause is applied before ``count_subquery``; if it ever moves below,
    ``total`` silently reports the unfiltered catalogue while the rows are
    narrowed, and the user is told there are 1,240 of something they can see
    three of.
    """
    payload = await _controls(client, scenario, my_teams="true", limit=200)
    assert payload["total"] == len(payload["controls"]) == 3


# ---------------------------------------------------------------------------
# The empty-set case — the one that must never fall back to "everything"
# ---------------------------------------------------------------------------

async def test_a_caller_on_no_team_sees_nothing(db, scenario):
    """Zero teams means zero rows, not the whole organisation.

    This is the single most important assertion in the file. The tempting
    implementation — "no team ids, so add no clause" — silently shows an
    unfiltered list under a header that says My teams. An unfiltered list
    presented as a filtered one is worse than an empty one, because the user
    has no way to tell.
    """
    loner = User(email=f"loner-{uuid.uuid4().hex[:8]}@example.invalid",
                 google_sub=f"loner-{uuid.uuid4().hex[:8]}")
    db.add(loner)
    await db.flush()
    db.add(OrganizationMember(organization_id=scenario.org.id,
                              user_id=loner.id, role="admin"))
    await db.flush()

    async with _client_for(db, scenario, loner) as c:
        payload = await _controls(c, scenario, my_teams="true", limit=200)
        assert payload["controls"] == []
        assert payload["total"] == 0

        rows = await _evidence(c, scenario, my_teams="true")
        assert rows == []


# ---------------------------------------------------------------------------
# Intersection with an explicit team_id
# ---------------------------------------------------------------------------

async def test_my_teams_and_team_id_intersect(client, scenario):
    """Both clauses AND — no precedence rule.

    Picking one of your own teams narrows to that team, exactly as it would
    without the header set.
    """
    payload = await _controls(client, scenario, my_teams="true",
                              team_id=str(scenario.mine_a.id), limit=200)
    assert _ids(payload) == {
        scenario.catalog[0].scf_id,  # on mine_a
        scenario.catalog[2].scf_id,  # on both, mine_a among them
    }


async def test_my_teams_with_a_foreign_team_id_returns_nothing(client, scenario):
    """Asking for a team you are not on is answered honestly, not rewritten.

    The alternative — letting one parameter win — means the list quietly
    answers a question the user did not ask. The empty case is prevented in the
    UI, by constraining the team picker while the header is set to My teams,
    not by second-guessing the request on the wire.
    """
    payload = await _controls(client, scenario, my_teams="true",
                              team_id=str(scenario.theirs.id), limit=200)
    assert payload["controls"] == []
    assert payload["total"] == 0


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

async def test_evidence_my_teams_returns_only_the_callers_teams_evidence(client, scenario):
    rows = await _evidence(client, scenario, my_teams="true")
    ids = {e["evidence_id"] for e in rows}
    assert scenario.evidence["on_mine_a"].evidence_id in ids
    assert scenario.evidence["on_theirs"].evidence_id not in ids


async def test_evidence_without_my_teams_is_unchanged(client, scenario):
    rows = await _evidence(client, scenario)
    ids = {e["evidence_id"] for e in rows}
    assert scenario.evidence["on_mine_a"].evidence_id in ids
    assert scenario.evidence["on_theirs"].evidence_id in ids


# ---------------------------------------------------------------------------
# GET /teams?mine=true
# ---------------------------------------------------------------------------

async def _teams(client, scenario, **params):
    r = await client.get(f"/api/organizations/{scenario.org.id}/teams", params=params)
    assert r.status_code == 200, r.text
    return r.json()


async def test_teams_mine_returns_only_my_teams(client, scenario):
    rows = await _teams(client, scenario, mine="true")
    assert {t["id"] for t in rows} == {str(scenario.mine_a.id), str(scenario.mine_b.id)}


async def test_teams_without_mine_returns_every_team(client, scenario):
    rows = await _teams(client, scenario)
    assert {t["id"] for t in rows} >= {
        str(scenario.mine_a.id), str(scenario.mine_b.id), str(scenario.theirs.id),
    }


async def test_teams_mine_carries_the_membership_role(client, scenario):
    """So the picker can say *why* a team is on your list, not just that it is."""
    rows = await _teams(client, scenario, mine="true")
    by_id = {t["id"]: t for t in rows}
    assert by_id[str(scenario.mine_a.id)]["membership_role"] == "member"
    assert by_id[str(scenario.mine_b.id)]["membership_role"] == "primary"


async def test_membership_role_is_null_on_the_unfiltered_list(client, scenario):
    """"Whose role?" has no answer on a list that is not about anybody."""
    rows = await _teams(client, scenario)
    assert all(t["membership_role"] is None for t in rows)
