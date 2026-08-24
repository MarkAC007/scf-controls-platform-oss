"""Postgres-backed tests for team assignment of controls and evidence (#822 phase 3).

Three things here can only be demonstrated against a real database, which is
why this file needs one:

* ``uq_control_accountable_team`` and ``uq_evidence_accountable_team`` are
  **non-deferrable** partial unique indexes. Postgres evaluates them at the end
  of each *statement*, not at commit, and that single fact is what the
  accountable-promotion path is built around. A faked session proves nothing
  about it.
* Cross-tenant isolation is a pair of composite foreign keys. The control is
  the database's, so the test has to be the database's too — asserting that a
  Python guard rejects the row would be asserting the wrong thing.
* "One query, not N+1" is a claim about emitted SQL. It is counted here, from
  the driver, rather than reasoned about.

Each test opens its own session, builds a throwaway organisation, and rolls
back. Nothing is committed, so nothing is left behind and a test that
deliberately provokes an ``IntegrityError`` cannot poison its neighbour.

The whole file skips when no Postgres ``DATABASE_URL`` is reachable.
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The SQLAlchemy registry spans both modules: models.System has a relationship
# to SystemCatalogTemplate, which lives here. Import it or mapper configuration
# fails on first query with an unresolvable class name.
import catalog_models  # noqa: E402,F401
from api.team_assignments import (  # noqa: E402
    _claim_accountable,
    _load_assignment_map,
)
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
from services.owner_resolution import (  # noqa: E402
    OWNER_TIER_ACCOUNTABLE_TEAM,
    OWNER_TIER_ORG_ADMIN,
    resolve_item_owners,
)
from services.team_assignments import TEAM_ASSIGNMENT_TYPES  # noqa: E402

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL",
)

#: Phase 3 ships exactly these two. Risk and vendor arrive in phase 5, and when
#: they do this parametrisation picks them up without being edited — which is
#: the point of the registry being a table rather than a chain of ifs.
PHASE_3_TYPES = ["control", "evidence"]


@pytest.fixture
async def db():
    """A session on a transaction that is always rolled back."""
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")

    session_factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    session = session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


class _Scenario:
    """One throwaway organisation with two teams, a control and an evidence item."""

    def __init__(self, org, function, team_a, team_b, control, evidence):
        self.org = org
        self.function = function
        self.team_a = team_a
        self.team_b = team_b
        self.control = control
        self.evidence = evidence

    def item_id(self, type_key):
        return {
            "control": self.control.id,
            "evidence": self.evidence.id,
        }[type_key]


async def _make_user(db, org=None, role="editor"):
    tag = uuid.uuid4().hex[:12]
    user = User(email=f"ta-{tag}@example.invalid", google_sub=f"sub-{tag}")
    db.add(user)
    await db.flush()
    if org is not None:
        db.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, role=role,
        ))
        await db.flush()
    return user


async def _make_org(db):
    tag = uuid.uuid4().hex[:12]
    org = Organization(name=f"ta-test-{tag}", slug=f"ta-test-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _make_team(db, org, function, name=None):
    team = Team(
        organization_id=org.id,
        function_id=function.id,
        name=name or f"team-{uuid.uuid4().hex[:12]}",
    )
    db.add(team)
    await db.flush()
    return team


@pytest.fixture
async def scenario(db):
    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")

    org = await _make_org(db)
    team_a = await _make_team(db, org, function)
    team_b = await _make_team(db, org, function)

    control = ScopedControl(
        organization_id=org.id, scf_id=f"TST-{uuid.uuid4().hex[:6]}", selected=True,
    )
    evidence = EvidenceTracking(
        organization_id=org.id, evidence_id=f"E-TST-{uuid.uuid4().hex[:6]}",
    )
    db.add_all([control, evidence])
    await db.flush()

    return _Scenario(org, function, team_a, team_b, control, evidence)


async def _assign(db, scenario, type_key, team, *, is_accountable=False):
    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    row = spec.model(**{
        spec.item_id_field: scenario.item_id(type_key),
        "team_id": team.id,
        "organization_id": scenario.org.id,
        "is_accountable": is_accountable,
    })
    db.add(row)
    await db.flush()
    return row


async def _accountable_rows(db, scenario, type_key):
    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    result = await db.execute(
        select(spec.model).where(
            spec.item_column == scenario.item_id(type_key),
            spec.model.is_accountable.is_(True),
        )
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# The registry is a table, not a chain of ifs
# ---------------------------------------------------------------------------

def test_registry_covers_exactly_the_phase_three_types():
    """risk and vendor are phase 5. Shipping their tables early would be scope."""
    assert sorted(TEAM_ASSIGNMENT_TYPES) == sorted(PHASE_3_TYPES)


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
def test_every_registered_type_is_fully_specified(type_key):
    """A new type is a table entry. This is the shape that entry must have."""
    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    assert spec.type_key == type_key
    assert spec.model is not None
    assert spec.item_model is not None
    assert spec.item_id_field
    assert spec.entity_type
    assert spec.tracked_fields
    # The dispatch column has to be a real mapped attribute, or the bulk read
    # and the accountable claim both address the wrong table.
    assert spec.item_column.key == spec.item_id_field


# ---------------------------------------------------------------------------
# Invariant 1: at most one accountable team, promoted atomically
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_promotion_leaves_exactly_one_accountable_row(db, scenario, type_key):
    """The acceptance criterion: one transaction, one accountable team at the end."""
    incumbent = await _assign(db, scenario, type_key, scenario.team_a,
                              is_accountable=True)
    challenger = await _assign(db, scenario, type_key, scenario.team_b)

    demoted = await _claim_accountable(
        db, TEAM_ASSIGNMENT_TYPES[type_key],
        scenario.item_id(type_key), scenario.team_b.id,
    )
    challenger.is_accountable = True
    await db.flush()

    assert demoted is incumbent
    assert incumbent.is_accountable is False

    accountable = await _accountable_rows(db, scenario, type_key)
    assert len(accountable) == 1
    assert accountable[0].team_id == scenario.team_b.id


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_promoting_without_demoting_first_is_refused_by_the_index(
    db, scenario, type_key
):
    """Proves the atomicity above is load-bearing rather than decorative.

    The partial unique index is non-deferrable, so the second accountable row
    fails at the end of its own statement — a client-side "set the new one,
    then clear the old one" sequence cannot work, and a two-call client
    sequence races.
    """
    await _assign(db, scenario, type_key, scenario.team_a, is_accountable=True)

    with pytest.raises(IntegrityError):
        await _assign(db, scenario, type_key, scenario.team_b, is_accountable=True)


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_many_non_accountable_teams_are_legal(db, scenario, type_key):
    """Consulted teams are unlimited. Only accountability is exclusive."""
    await _assign(db, scenario, type_key, scenario.team_a)
    await _assign(db, scenario, type_key, scenario.team_b)

    assert await _accountable_rows(db, scenario, type_key) == []


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_claiming_when_nobody_is_accountable_demotes_nobody(
    db, scenario, type_key
):
    """Every item starts here, so this path must be silent, not an error."""
    demoted = await _claim_accountable(
        db, TEAM_ASSIGNMENT_TYPES[type_key],
        scenario.item_id(type_key), scenario.team_a.id,
    )
    assert demoted is None


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_reclaiming_by_the_incumbent_itself_is_not_a_self_demotion(
    db, scenario, type_key
):
    """Re-POSTing the team that is already accountable must be a no-op, not a
    demotion that leaves the item with no owner at all."""
    incumbent = await _assign(db, scenario, type_key, scenario.team_a,
                              is_accountable=True)

    demoted = await _claim_accountable(
        db, TEAM_ASSIGNMENT_TYPES[type_key],
        scenario.item_id(type_key), scenario.team_a.id,
    )

    assert demoted is None
    assert incumbent.is_accountable is True


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_accountability_is_scoped_to_one_item_not_to_the_team(
    db, scenario, db_second_item_factory, type_key
):
    """A team accountable for control A stays accountable for it when it also
    becomes accountable for control B. The index is per item."""
    other_item_id = await db_second_item_factory(type_key)
    spec = TEAM_ASSIGNMENT_TYPES[type_key]

    first = await _assign(db, scenario, type_key, scenario.team_a,
                          is_accountable=True)
    second = spec.model(**{
        spec.item_id_field: other_item_id,
        "team_id": scenario.team_a.id,
        "organization_id": scenario.org.id,
        "is_accountable": True,
    })
    db.add(second)
    await db.flush()

    assert first.is_accountable is True
    assert second.is_accountable is True


@pytest.fixture
async def db_second_item_factory(db, scenario):
    async def _make(type_key):
        if type_key == "control":
            item = ScopedControl(
                organization_id=scenario.org.id,
                scf_id=f"TST2-{uuid.uuid4().hex[:6]}",
            )
        else:
            item = EvidenceTracking(
                organization_id=scenario.org.id,
                evidence_id=f"E-TST2-{uuid.uuid4().hex[:6]}",
            )
        db.add(item)
        await db.flush()
        return item.id
    return _make


# ---------------------------------------------------------------------------
# Invariant 5: cross-tenant isolation lives in the database
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_another_tenants_team_cannot_be_assigned_to_this_tenants_item(
    db, scenario, type_key
):
    """The attack the composite foreign keys exist to stop: name the caller's
    own organisation_id while naming a team that belongs to someone else."""
    other_org = await _make_org(db)
    other_team = await _make_team(db, other_org, scenario.function)

    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    db.add(spec.model(**{
        spec.item_id_field: scenario.item_id(type_key),
        "team_id": other_team.id,
        "organization_id": scenario.org.id,
        "is_accountable": False,
    }))

    with pytest.raises(IntegrityError):
        await db.flush()


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_a_forged_organization_id_cannot_smuggle_a_row_through(
    db, scenario, type_key
):
    """The other half of the same door: an attacker's own team, an attacker's
    own organization_id, but a victim organisation's item."""
    other_org = await _make_org(db)
    other_team = await _make_team(db, other_org, scenario.function)

    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    db.add(spec.model(**{
        spec.item_id_field: scenario.item_id(type_key),   # victim's item
        "team_id": other_team.id,                          # attacker's team
        "organization_id": other_org.id,                   # attacker's org
        "is_accountable": False,
    }))

    with pytest.raises(IntegrityError):
        await db.flush()


# ---------------------------------------------------------------------------
# The bulk read: one query for the whole page
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_bulk_map_reads_many_items_in_a_single_query(
    db, scenario, type_key, db_second_item_factory
):
    """The reason this endpoint exists.

    The controls list renders hundreds of rows and the accountable-team badge
    sits on that hot path; a per-row fetch is an N+1 measured in seconds. The
    count below is taken from the driver, so it cannot drift from the claim.
    """
    spec = TEAM_ASSIGNMENT_TYPES[type_key]

    # A primary and a delegate on the accountable team, so the badge has
    # somebody to render and the eager load has something to prove.
    primary = await _make_user(db, scenario.org)
    delegate = await _make_user(db, scenario.org)
    db.add_all([
        TeamMember(team_id=scenario.team_a.id, organization_id=scenario.org.id,
                   user_id=primary.id, membership_role="primary"),
        TeamMember(team_id=scenario.team_a.id, organization_id=scenario.org.id,
                   user_id=delegate.id, membership_role="delegate"),
    ])
    await db.flush()

    item_ids = [scenario.item_id(type_key)]
    await _assign(db, scenario, type_key, scenario.team_a, is_accountable=True)
    await _assign(db, scenario, type_key, scenario.team_b)

    for _ in range(6):
        other = await db_second_item_factory(type_key)
        item_ids.append(other)
        db.add(spec.model(**{
            spec.item_id_field: other,
            "team_id": scenario.team_a.id,
            "organization_id": scenario.org.id,
            "is_accountable": True,
        }))
    await db.flush()

    statements = []
    connection = await db.connection()

    def _count(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(connection.sync_connection.engine, "before_cursor_execute", _count)
    try:
        mapping = await _load_assignment_map(db, spec, scenario.org.id)
    finally:
        event.remove(connection.sync_connection.engine,
                     "before_cursor_execute", _count)

    assert len(statements) == 1, (
        f"expected one SELECT for {len(item_ids)} items, got "
        f"{len(statements)}:\n" + "\n".join(statements)
    )

    # Seven items, and the client indexes by item id without a second call.
    assert set(mapping) == set(item_ids)
    first = mapping[scenario.item_id(type_key)]
    assert len(first) == 2

    accountable = [a for a in first if a["is_accountable"]][0]
    assert accountable["team"]["id"] == scenario.team_a.id
    assert accountable["team"]["name"] == scenario.team_a.name
    # The badge renders from this without a follow-up query.
    assert accountable["team"]["primary"]["user_id"] == primary.id
    assert accountable["team"]["delegate"]["user_id"] == delegate.id
    assert accountable["team"]["primary"]["user"]["email"] == primary.email


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_bulk_map_is_scoped_to_the_calling_organisation(
    db, scenario, type_key
):
    """A tenant's map contains its own rows and nothing else."""
    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    await _assign(db, scenario, type_key, scenario.team_a, is_accountable=True)

    other_org = await _make_org(db)
    mapping = await _load_assignment_map(db, spec, other_org.id)

    assert mapping == {}


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_bulk_map_can_be_narrowed_to_accountable_rows(db, scenario, type_key):
    """The badge only ever needs the accountable team."""
    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    await _assign(db, scenario, type_key, scenario.team_a, is_accountable=True)
    await _assign(db, scenario, type_key, scenario.team_b)

    mapping = await _load_assignment_map(
        db, spec, scenario.org.id, accountable_only=True,
    )

    rows = mapping[scenario.item_id(type_key)]
    assert len(rows) == 1
    assert rows[0]["team"]["id"] == scenario.team_a.id


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_bulk_map_survives_a_team_with_no_primary_or_delegate(
    db, scenario, type_key
):
    """Every team looks like this the moment it is created. The outer joins
    must not drop the assignment along with the missing member."""
    spec = TEAM_ASSIGNMENT_TYPES[type_key]
    await _assign(db, scenario, type_key, scenario.team_a, is_accountable=True)

    mapping = await _load_assignment_map(db, spec, scenario.org.id)

    row = mapping[scenario.item_id(type_key)][0]
    assert row["team"]["primary"] is None
    assert row["team"]["delegate"] is None


# ---------------------------------------------------------------------------
# The resolution chain, against real rows
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_resolution_reaches_the_accountable_teams_primary_and_delegate(
    db, scenario, type_key
):
    primary = await _make_user(db, scenario.org)
    delegate = await _make_user(db, scenario.org)
    plain = await _make_user(db, scenario.org)
    db.add_all([
        TeamMember(team_id=scenario.team_a.id, organization_id=scenario.org.id,
                   user_id=primary.id, membership_role="primary"),
        TeamMember(team_id=scenario.team_a.id, organization_id=scenario.org.id,
                   user_id=delegate.id, membership_role="delegate"),
        TeamMember(team_id=scenario.team_a.id, organization_id=scenario.org.id,
                   user_id=plain.id, membership_role="member"),
    ])
    await db.flush()
    await _assign(db, scenario, type_key, scenario.team_a, is_accountable=True)
    # A consulted team's primary must NOT be paged for an ordinary event.
    consulted_primary = await _make_user(db, scenario.org)
    db.add(TeamMember(team_id=scenario.team_b.id, organization_id=scenario.org.id,
                      user_id=consulted_primary.id, membership_role="primary"))
    await db.flush()
    await _assign(db, scenario, type_key, scenario.team_b)

    result = await resolve_item_owners(
        db,
        organization_id=scenario.org.id,
        item_type=type_key,
        item_id=scenario.item_id(type_key),
    )

    assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
    assert result.user_ids == frozenset({primary.id, delegate.id})


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_resolution_falls_through_to_org_admins_with_no_accountable_team(
    db, scenario, type_key
):
    """The state every item is in until somebody assigns one."""
    admin = await _make_user(db, scenario.org, role="admin")
    await _make_user(db, scenario.org, role="viewer")

    result = await resolve_item_owners(
        db,
        organization_id=scenario.org.id,
        item_type=type_key,
        item_id=scenario.item_id(type_key),
    )

    assert result.tier == OWNER_TIER_ORG_ADMIN
    assert result.user_ids == frozenset({admin.id})


@pytest.mark.parametrize("type_key", PHASE_3_TYPES)
async def test_resolution_with_nothing_anywhere_returns_empty_without_raising(
    db, scenario, type_key
):
    """An organisation with no admins and no teams. Must not take down a run."""
    result = await resolve_item_owners(
        db,
        organization_id=scenario.org.id,
        item_type=type_key,
        item_id=scenario.item_id(type_key),
    )

    assert result.user_ids == frozenset()
    assert result.tier is None
