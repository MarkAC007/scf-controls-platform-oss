"""The contractor report: items whose accountable team's primary owner is external (#822 phase 2).

"Which of our controls does an outside party actually own?" is a question an
auditor asks and a client asks, and it has exactly one correct answer. Getting
it right means walking three joins in the right order and refusing every
near-miss along the way::

    control/evidence
      -> team assignment WHERE is_accountable IS TRUE     (accountable, not merely assigned)
      -> team_members    WHERE membership_role = 'primary' (the owner, not the bench)
      -> organization_members.member_type = 'external_contractor'

Each arrow is a place the query can be wrong in a way that still returns
plausible rows, so the negatives below matter more than the positive:

* a **consulted** team is still an assigned team — dropping the
  ``is_accountable`` predicate returns items an outsider merely advises on and
  reports them as outsourced;
* a contractor on the team as ``delegate`` or ``member`` is not the owner —
  dropping the ``membership_role`` predicate turns "a contractor is somewhere
  near this" into "a contractor owns this";
* an accountable team whose primary is internal must never appear;
* and none of it may cross a tenant boundary. ``member_type`` is per-membership
  precisely because the same person is staff here and a contractor there; a
  join that forgets the organisation would answer the other tenant's question.

Row multiplication is the fifth trap. A control can carry several assignments
and a team several members, so a naive JOIN emits the control once per matching
combination — corrupting both the page and ``total`` on the server-paginated
controls list. ``test_a_contractor_owned_control_appears_exactly_once`` holds
the implementation to a semi-join.

Driven over HTTP against a real database, because what is asserted is the
behaviour of the whole endpoint — the filter composing with the others, and
``total`` agreeing with the page — not of a query fragment. Auth is stubbed at
the module the ``require_org_role`` closures resolve through; everything below
is genuine. Both endpoints are read-only, so the session's transaction is
rolled back and nothing is left behind.

**What runs where, honestly.** ``accountable_owner_type`` is being added by a
parallel workstream. Tests that need it are discovered from ``app.openapi()``
and **skip, not pass**, until it lands, with ``TestTheCapabilityProbeWorks``
failing rather than skipping if the probe itself stops working. The scenario
builder runs either way: ``TestTheScenarioIsWhatItClaims`` asserts the fixture
really did produce a contractor primary, an internal primary and a delegate-only
team, so the skips cannot be hiding a fixture that never built anything.

Run with::

    docker run --rm --network cg-scf-network -v <worktree>/backend:/app \\
        -w /app ghcr.io/markac007/scf-backend:latest \\
        python -m pytest tests/test_contractor_accountable_report.py -v
"""
from __future__ import annotations

import os
import sys
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import catalog_models  # noqa: E402,F401
import main  # noqa: E402
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
from services.org_utils import MEMBER_TYPES  # noqa: E402

CONTROLS_PATH = "/api/organizations/{org_id}/scoped-controls-paginated"
EVIDENCE_PATH = "/api/organizations/{org_id}/evidence-tracking"

PARAM = "accountable_owner_type"
CONTRACTOR = "external_contractor"
INTERNAL = "internal"

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
)


# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------

def _query_params(path: str, method: str = "get") -> dict:
    """``{name: schema}`` for the query parameters an operation declares.

    From the OpenAPI schema, not ``app.routes``: FastAPI 0.141 hides included
    routers behind one ``_IncludedRouter`` with an empty path, so walking
    ``app.routes`` finds nothing for any router while looking like it worked.
    """
    operation = main.app.openapi()["paths"].get(path, {}).get(method)
    if operation is None:
        return {}
    return {
        parameter["name"]: parameter.get("schema", {})
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query"
    }


def _needs_filter(path: str) -> None:
    params = _query_params(path)
    if PARAM not in params:
        pytest.skip(
            f"GET {path} does not yet declare a '{PARAM}' query parameter "
            f"(it declares {sorted(params)}). The API workstream is adding it "
            "on a parallel branch. This is a SKIP, not a pass — it begins "
            "executing unchanged the moment that lands."
        )


def _accepted_values() -> set[str]:
    """The values this filter accepts, from the vocabulary the endpoints validate against.

    ``services.org_utils.MEMBER_TYPES`` is what both handlers check the
    parameter against before it reaches SQL, so it — not the OpenAPI schema —
    is the authority on what is accepted. Reading it here rather than
    restating the pair means the complement test below cannot go stale if the
    vocabulary ever widens, and cannot pass vacuously if the parameter is
    declared with no enum or pattern at all (which is how it is declared
    today).
    """
    return set(MEMBER_TYPES)


class TestTheCapabilityProbeWorks:
    """Fails — never skips — if the probe stops seeing the endpoints."""

    def test_both_endpoints_are_discoverable(self):
        paths = main.app.openapi()["paths"]
        assert CONTROLS_PATH in paths
        assert EVIDENCE_PATH in paths

    def test_the_probe_sees_a_filter_that_already_exists(self):
        # `team_id` landed in phase 3 on both endpoints. If the probe cannot
        # see it, every skip above is meaningless.
        assert "team_id" in _query_params(CONTROLS_PATH)
        assert "team_id" in _query_params(EVIDENCE_PATH)


# ---------------------------------------------------------------------------
# The scenario is real — these run today and never skip
# ---------------------------------------------------------------------------

class TestTheScenarioIsWhatItClaims:
    """Proves the fixture built the thing the gated tests will assert about.

    Without this, a scenario that silently failed to make anybody a contractor
    would leave the gated tests skipping now and passing vacuously later.
    """

    async def test_the_contractor_team_has_a_contractor_primary(self, session, scenario):
        assert await _primary_member_type(session, scenario.team_contractor.id) == CONTRACTOR

    async def test_the_internal_team_has_an_internal_primary(self, session, scenario):
        assert await _primary_member_type(session, scenario.team_internal.id) == INTERNAL

    async def test_the_delegate_only_team_has_no_primary_at_all(self, session, scenario):
        assert await _primary_member_type(session, scenario.team_delegate_only.id) is None
        roles = (await session.execute(
            sa.select(TeamMember.membership_role)
            .where(TeamMember.team_id == scenario.team_delegate_only.id)
        )).scalars().all()
        assert sorted(roles) == ["delegate", "member"]

    async def test_the_same_person_is_staff_here_and_a_contractor_there(self, session, scenario):
        """The reason member_type is on the membership at all.

        One user, two organisations, two answers. A column on ``users`` could
        not represent this. It is also the sharpest form of the tenant trap:
        this person is the primary owner of a team in each org, so a join that
        reached ``organization_members`` without the organisation would read
        the wrong tenant's answer and report an internally-owned control as
        outsourced.
        """
        rows = dict((await session.execute(
            sa.select(OrganizationMember.organization_id, OrganizationMember.member_type)
            .where(OrganizationMember.user_id == scenario.dual_user.id)
        )).all())
        assert rows[scenario.org.id] == INTERNAL
        assert rows[scenario.other_org.id] == CONTRACTOR

    async def test_exactly_one_team_is_accountable_per_control(self, session, scenario):
        counts = (await session.execute(
            sa.select(
                ControlTeamAssignment.scoped_control_id,
                sa.func.count(),
            )
            .where(
                (ControlTeamAssignment.organization_id == scenario.org.id)
                & (ControlTeamAssignment.is_accountable.is_(True))
            )
            .group_by(ControlTeamAssignment.scoped_control_id)
        )).all()
        assert counts and all(count == 1 for _id, count in counts)


# ---------------------------------------------------------------------------
# The controls report
# ---------------------------------------------------------------------------

class TestContractorOwnedControls:
    async def test_a_contractor_owned_control_is_returned(self, client, scenario):
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert scenario.scf("contractor_owned") in _ids(payload)

    async def test_only_contractor_owned_controls_are_returned(self, client, scenario):
        """The whole report in one assertion: an exact set, not a superset.

        Written as equality on purpose. ``assert x in result`` would pass
        against a filter that did nothing at all.
        """
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert _ids(payload) == {
            scenario.scf("contractor_owned"),
            scenario.scf("contractor_owned_plus_consulted"),
        }

    async def test_a_consulted_contractor_team_is_excluded(self, client, scenario):
        """Assigned is not accountable.

        The contractor team advises on this control and owns nothing. Reporting
        it as contractor-owned would tell a client they had outsourced work
        they had not.
        """
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert scenario.scf("contractor_consulted") not in _ids(payload)

    async def test_a_contractor_who_is_only_a_delegate_is_excluded(self, client, scenario):
        """``membership_role = 'primary'`` is the owner; delegate and member are not."""
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert scenario.scf("delegate_only") not in _ids(payload)

    async def test_an_internally_owned_control_is_excluded(self, client, scenario):
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert scenario.scf("internal_owned") not in _ids(payload)

    async def test_an_unassigned_control_is_excluded(self, client, scenario):
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert scenario.scf("unassigned") not in _ids(payload)

    async def test_another_tenants_contractor_control_is_excluded(self, client, scenario):
        """Tenant isolation, on a control owned by the very same person.

        ``scenario.dual_user`` is a contractor in the other org and staff in
        this one. A join that reached ``organization_members`` without the
        organisation would return this row here.
        """
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        assert scenario.scf("other_org_contractor") not in _ids(payload)

    async def test_a_contractor_owned_control_appears_exactly_once(self, client, scenario):
        """Row multiplication: one control, two assignments, two team members.

        A JOIN rather than a semi-join emits this control once per matching
        combination and counts each copy in ``total`` — breaking the page and
        the pagination together.
        """
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        returned = [c["scf_id"] for c in payload["controls"]]
        target = scenario.scf("contractor_owned_plus_consulted")
        assert returned.count(target) == 1
        assert payload["total"] == len(returned)

    async def test_the_filter_composes_with_the_team_filter(self, client, scenario):
        """Two filters must intersect, not replace one another."""
        _needs_filter(CONTROLS_PATH)
        payload = await _controls(
            client, scenario,
            team_id=str(scenario.team_internal.id),
            limit=200,
            **{PARAM: CONTRACTOR},
        )
        # team_internal is accountable for internal_owned and consulted on
        # contractor_owned_plus_consulted; only the latter is contractor-owned.
        assert _ids(payload) == {scenario.scf("contractor_owned_plus_consulted")}

    async def test_omitting_the_filter_narrows_nothing(self, client, scenario):
        """Purely additive: today's callers send none of this."""
        payload = await _controls(client, scenario, limit=200)
        assert _ids(payload) >= {scenario.scf(key) for key in scenario.own_controls}

    async def test_the_response_shape_is_unchanged(self, client, scenario):
        payload = await _controls(client, scenario, limit=1)
        assert set(payload) == {"total", "limit", "offset", "controls"}

    async def test_internal_is_the_complement(self, client, scenario):
        """Both members of the vocabulary must work, not only the interesting one.

        The handler validates this parameter against ``MEMBER_TYPES``, so
        ``internal`` is accepted; a filter that answered only the contractor
        question would 200 and quietly return the contractor set for either
        value. Reading the accepted set rather than assuming it means a third
        employment type would extend this test rather than slip past it.
        """
        _needs_filter(CONTROLS_PATH)
        assert INTERNAL in _accepted_values()
        payload = await _controls(client, scenario, **{PARAM: INTERNAL}, limit=200)
        ids = _ids(payload)
        assert scenario.scf("internal_owned") in ids
        assert scenario.scf("contractor_owned") not in ids
        # A control with no accountable team has no owner of either type.
        assert scenario.scf("unassigned") not in ids


    async def test_a_value_outside_the_vocabulary_is_refused(self, client, scenario):
        """Refused in the handler, with a 400 — not passed through to return nothing.

        A filter that accepted ``'contractor'`` and quietly matched no rows
        would tell an admin their contractors own nothing, which is the same
        lie as a wrong answer and harder to notice than an error.
        """
        _needs_filter(CONTROLS_PATH)
        for path in (CONTROLS_PATH, EVIDENCE_PATH):
            response = await client.get(
                path.format(org_id=scenario.org.id), params={PARAM: "contractor"},
            )
            assert response.status_code == 400, f"{path}: {response.status_code} {response.text}"


# ---------------------------------------------------------------------------
# The evidence report — the same question, the same answer
# ---------------------------------------------------------------------------

class TestContractorOwnedEvidence:
    async def test_only_contractor_owned_evidence_is_returned(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        assert {r["evidence_id"] for r in rows} == {scenario.ev("contractor_owned")}

    async def test_a_consulted_contractor_team_is_excluded(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        assert scenario.ev("contractor_consulted") not in {r["evidence_id"] for r in rows}

    async def test_a_contractor_who_is_only_a_delegate_is_excluded(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        assert scenario.ev("delegate_only") not in {r["evidence_id"] for r in rows}

    async def test_internally_owned_evidence_is_excluded(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        assert scenario.ev("internal_owned") not in {r["evidence_id"] for r in rows}

    async def test_unassigned_evidence_is_excluded(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        assert scenario.ev("unassigned") not in {r["evidence_id"] for r in rows}

    async def test_another_tenants_evidence_is_excluded(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        assert scenario.ev("other_org_contractor") not in {r["evidence_id"] for r in rows}

    async def test_each_row_appears_exactly_once(self, client, scenario):
        _needs_filter(EVIDENCE_PATH)
        rows = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        ids = [r["evidence_id"] for r in rows]
        assert len(ids) == len(set(ids))

    async def test_omitting_the_filter_narrows_nothing(self, client, scenario):
        rows = await _evidence(client, scenario)
        ids = {r["evidence_id"] for r in rows}
        assert ids >= {scenario.ev(key) for key in scenario.own_evidence}

    async def test_the_two_lists_agree_on_what_contractor_owned_means(self, client, scenario):
        """One question, one answer, whichever list is asked.

        The scenario gives controls and evidence the identical assignment
        shape, so the two reports must select the same keys. A helper used by
        one endpoint and hand-rolled in the other is exactly how these drift.
        """
        _needs_filter(CONTROLS_PATH)
        _needs_filter(EVIDENCE_PATH)
        controls = await _controls(client, scenario, **{PARAM: CONTRACTOR}, limit=200)
        evidence = await _evidence(client, scenario, **{PARAM: CONTRACTOR})
        control_keys = {
            key for key in scenario.controls if scenario.scf(key) in _ids(controls)
        }
        evidence_keys = {
            key for key in scenario.evidence
            if scenario.ev(key) in {r["evidence_id"] for r in evidence}
        }
        # contractor_owned_plus_consulted exists only on the controls side (it
        # is the row-multiplication case), so compare on the shared keys.
        shared = scenario.own_controls & scenario.own_evidence
        assert control_keys & shared == evidence_keys & shared


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

async def _primary_member_type(session, team_id):
    """The member_type of a team's primary owner, or None if it has no primary."""
    return (await session.execute(
        sa.select(OrganizationMember.member_type)
        .select_from(TeamMember)
        .join(
            OrganizationMember,
            (OrganizationMember.organization_id == TeamMember.organization_id)
            & (OrganizationMember.user_id == TeamMember.user_id),
        )
        .where(
            (TeamMember.team_id == team_id)
            & (TeamMember.membership_role == "primary")
        )
    )).scalar_one_or_none()


async def _controls(client, scenario, **params):
    response = await client.get(
        CONTROLS_PATH.format(org_id=scenario.org.id), params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _evidence(client, scenario, **params):
    response = await client.get(
        EVIDENCE_PATH.format(org_id=scenario.org.id), params=params,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _ids(payload):
    return {c["scf_id"] for c in payload["controls"]}


@pytest.fixture
async def session():
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    s = factory()
    try:
        yield s
    finally:
        await s.rollback()
        await s.close()
        await engine.dispose()


class _Scenario:
    def __init__(self, org, other_org):
        self.org = org
        self.other_org = other_org
        self.controls: dict = {}
        self.evidence: dict = {}
        self.dual_user = None
        self.team_contractor = None
        self.team_internal = None
        self.team_delegate_only = None

    #: Keys belonging to the OTHER tenant. Everything else is this org's, and
    #: only this org's items may appear in this org's lists at all.
    FOREIGN = {"other_org_contractor"}

    def scf(self, key: str) -> str:
        return self.controls[key]

    def ev(self, key: str) -> str:
        return self.evidence[key]

    @property
    def own_controls(self) -> set:
        return set(self.controls) - self.FOREIGN

    @property
    def own_evidence(self) -> set:
        return set(self.evidence) - self.FOREIGN


@pytest.fixture
async def scenario(session):
    """Two orgs, three teams, and one control and evidence record per case.

    Nothing commits: both endpoints under test are read-only and the session's
    transaction is rolled back in the ``session`` fixture's teardown.
    """
    functions = (await session.execute(
        sa.select(Function).where(Function.is_active.is_(True))
        .order_by(Function.key).limit(1)
    )).scalars().all()
    if not functions:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")
    function = functions[0]

    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"ctr-{tag}", slug=f"ctr-{tag}")
    other_org = Organization(name=f"ctr-other-{tag}", slug=f"ctr-other-{tag}")
    session.add_all([org, other_org])
    await session.flush()

    s = _Scenario(org, other_org)

    async def _member(org_row, key, member_type):
        user = User(
            email=f"ctr-{key}-{tag}@example.invalid",
            google_sub=f"ctr-{key}-{tag}",
        )
        session.add(user)
        await session.flush()
        session.add(OrganizationMember(
            organization_id=org_row.id, user_id=user.id,
            role="viewer", member_type=member_type,
        ))
        await session.flush()
        return user

    contractor = await _member(org, "contractor", CONTRACTOR)
    staff = await _member(org, "staff", INTERNAL)
    contractor_delegate = await _member(org, "cdelegate", CONTRACTOR)
    contractor_bench = await _member(org, "cbench", CONTRACTOR)

    # The same person: a contractor to the OTHER org, permanent staff here.
    # Two memberships, two answers — which is why member_type is per-membership.
    s.dual_user = User(email=f"ctr-dual-{tag}@example.invalid", google_sub=f"ctr-dual-{tag}")
    session.add(s.dual_user)
    await session.flush()
    session.add_all([
        OrganizationMember(organization_id=other_org.id, user_id=s.dual_user.id,
                           role="viewer", member_type=CONTRACTOR),
        OrganizationMember(organization_id=org.id, user_id=s.dual_user.id,
                           role="viewer", member_type=INTERNAL),
    ])
    await session.flush()

    def _team(org_row, name):
        team = Team(organization_id=org_row.id, function_id=function.id,
                    name=f"{name} {tag}")
        session.add(team)
        return team

    s.team_contractor = _team(org, "contractor-led")
    s.team_internal = _team(org, "internally-led")
    s.team_delegate_only = _team(org, "no-primary")
    team_other_org = _team(other_org, "other-org-contractor-led")
    await session.flush()

    session.add_all([
        # Owned by a contractor.
        TeamMember(team_id=s.team_contractor.id, organization_id=org.id,
                   user_id=contractor.id, membership_role="primary"),
        # …with an internal person also on it, so "any contractor on the team"
        # and "the contractor owns it" cannot be confused for one another.
        TeamMember(team_id=s.team_contractor.id, organization_id=org.id,
                   user_id=staff.id, membership_role="member"),
        # Owned by staff.
        TeamMember(team_id=s.team_internal.id, organization_id=org.id,
                   user_id=s.dual_user.id, membership_role="primary"),
        # Contractors present, but nobody owns it.
        TeamMember(team_id=s.team_delegate_only.id, organization_id=org.id,
                   user_id=contractor_delegate.id, membership_role="delegate"),
        TeamMember(team_id=s.team_delegate_only.id, organization_id=org.id,
                   user_id=contractor_bench.id, membership_role="member"),
        # The other tenant's contractor-owned team.
        TeamMember(team_id=team_other_org.id, organization_id=other_org.id,
                   user_id=s.dual_user.id, membership_role="primary"),
    ])
    await session.flush()

    # Real catalog rows: the controls endpoint is catalog-driven and LEFT JOINs
    # scoped_controls, so an invented scf_id would never be returned at all and
    # every exclusion below would pass for the wrong reason.
    keys = [
        "contractor_owned",
        "contractor_owned_plus_consulted",
        "contractor_consulted",
        "internal_owned",
        "delegate_only",
        "unassigned",
        "other_org_contractor",
    ]
    catalog = (await session.execute(
        sa.select(SCFCatalogControl)
        .where(SCFCatalogControl.status == "active")
        .order_by(SCFCatalogControl.scf_id)
        .limit(len(keys))
    )).scalars().all()
    if len(catalog) < len(keys):  # pragma: no cover - environment dependent
        pytest.skip(f"need {len(keys)} active catalog controls")

    scoped = {}
    for key, catalog_row in zip(keys, catalog):
        owner_org = other_org if key == "other_org_contractor" else org
        row = ScopedControl(organization_id=owner_org.id,
                            scf_id=catalog_row.scf_id, selected=True)
        session.add(row)
        scoped[key] = row
        s.controls[key] = catalog_row.scf_id
    await session.flush()

    tracked = {}
    for index, key in enumerate(keys):
        # The two-assignment case exists only on the controls side, where
        # pagination makes row multiplication a visible corruption.
        if key == "contractor_owned_plus_consulted":
            continue
        owner_org = other_org if key == "other_org_contractor" else org
        # Indexed, not name-derived: evidence_id is unique per organisation and
        # two of these keys share their first twelve characters.
        row = EvidenceTracking(organization_id=owner_org.id,
                               evidence_id=f"E-CTR-{index}-{tag}")
        session.add(row)
        tracked[key] = row
        s.evidence[key] = row.evidence_id
    await session.flush()

    #: (key, team, is_accountable). At most one accountable team per item is a
    #: partial unique index, so this table is also what keeps the fixture legal.
    assignments = [
        ("contractor_owned", s.team_contractor, True),
        ("contractor_owned_plus_consulted", s.team_contractor, True),
        ("contractor_owned_plus_consulted", s.team_internal, False),
        ("contractor_consulted", s.team_contractor, False),
        ("internal_owned", s.team_internal, True),
        ("delegate_only", s.team_delegate_only, True),
        ("other_org_contractor", team_other_org, True),
        # "unassigned" appears in neither.
    ]
    for key, team, accountable in assignments:
        owner_org = other_org if key == "other_org_contractor" else org
        session.add(ControlTeamAssignment(
            scoped_control_id=scoped[key].id, team_id=team.id,
            organization_id=owner_org.id, is_accountable=accountable,
        ))
        if key in s.evidence:
            session.add(EvidenceTeamAssignment(
                evidence_tracking_id=tracked[key].id, team_id=team.id,
                organization_id=owner_org.id, is_accountable=accountable,
            ))
    await session.flush()
    return s


@pytest.fixture
async def client(session, scenario):
    """The real app on the test's session, with auth stubbed to an org admin.

    Authorisation is not what these tests are about — ISC-30 covers that — so
    it is stubbed at the module ``require_org_role``'s closures resolve
    through. FastAPI 0.141 hides included routes behind ``_IncludedRouter``,
    which puts them out of ``dependency_overrides``' reach.
    """
    import auth as auth_mod
    from auth import OrgMembership, User as AuthUser
    from database import get_db

    membership = OrgMembership(
        user=AuthUser(user_id="stub", email="ctr@example.invalid",
                      db_id=str(uuid.uuid4()), auth_method="google"),
        organization_id=scenario.org.id,
        role="admin",
    )

    async def _db():
        yield session

    async def _require_auth(*_a, **_k):
        return membership.user

    async def _verify(org_id, user, db, min_role="viewer"):
        return membership

    original = (auth_mod.require_auth, auth_mod.verify_org_membership)
    auth_mod.require_auth = _require_auth
    auth_mod.verify_org_membership = _verify
    main.app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://contractor",
            headers={"Authorization": "Bearer stub"},
        ) as c:
            yield c
    finally:
        auth_mod.require_auth, auth_mod.verify_org_membership = original
        main.app.dependency_overrides.pop(get_db, None)
