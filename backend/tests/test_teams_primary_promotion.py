"""Postgres-backed tests for exclusive team roles (#822 phase 1).

``uq_team_primary`` and ``uq_team_delegate`` are **non-deferrable** partial
unique indexes, which means Postgres checks them at the end of every statement
rather than at commit. That single fact is what the promotion path has to be
built around, and it is not something a faked session can demonstrate — so
these tests run against a real database.

Each test opens its own session, builds a throwaway organisation, and rolls
back at the end. Nothing is ever committed, so nothing is left behind, and
tests that deliberately provoke an ``IntegrityError`` cannot poison a
neighbour's transaction.

The whole file skips when no Postgres ``DATABASE_URL`` is reachable, so it
never turns a database-free environment red.
"""
import os
import sys
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The SQLAlchemy registry spans both modules: models.System has a relationship
# to SystemCatalogTemplate, which lives here. Import it or mapper
# configuration fails on first query with an unresolvable class name.
import catalog_models  # noqa: E402,F401
from api.teams import _claim_exclusive_role  # noqa: E402
from models import (  # noqa: E402
    Function,
    Organization,
    OrganizationMember,
    Team,
    TeamMember,
    User,
)

DATABASE_URL = os.getenv("DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL",
)


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


class _Fixture:
    """Handles for a throwaway org with one team and two org members."""

    def __init__(self, org, team, alice, bob):
        self.org = org
        self.team = team
        self.alice = alice
        self.bob = bob


async def _make_user(db, org=None, role="editor"):
    tag = uuid.uuid4().hex[:12]
    user = User(email=f"teams-{tag}@example.invalid", google_sub=f"sub-{tag}")
    db.add(user)
    await db.flush()
    if org is not None:
        db.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, role=role,
        ))
        await db.flush()
    return user


@pytest.fixture
async def scenario(db):
    tag = uuid.uuid4().hex[:12]

    function = (await db.execute(
        select(Function).where(Function.is_active.is_(True)).limit(1)
    )).scalar_one_or_none()
    if function is None:  # pragma: no cover - environment dependent
        pytest.skip("no seeded functions in this database")

    org = Organization(name=f"teams-test-{tag}", slug=f"teams-test-{tag}")
    db.add(org)
    await db.flush()

    alice = await _make_user(db, org)
    bob = await _make_user(db, org)

    team = Team(
        organization_id=org.id,
        function_id=function.id,
        name=f"team-{tag}",
    )
    db.add(team)
    await db.flush()

    return _Fixture(org, team, alice, bob)


async def _add(db, scenario, user, role):
    member = TeamMember(
        team_id=scenario.team.id,
        organization_id=scenario.org.id,
        user_id=user.id,
        membership_role=role,
    )
    db.add(member)
    await db.flush()
    return member


async def _roles(db, team_id):
    result = await db.execute(
        select(TeamMember.user_id, TeamMember.membership_role)
        .where(TeamMember.team_id == team_id)
    )
    return dict(result.all())


# ---------------------------------------------------------------------------
# The acceptance criterion: promotion is atomic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["primary", "delegate"])
async def test_promoting_over_an_incumbent_swaps_them_in_one_transaction(
    db, scenario, role
):
    """Demote-then-promote, both statements inside the caller's transaction.

    No client is ever asked to demote first: one request does the whole swap,
    and either both halves land or neither does.
    """
    incumbent = await _add(db, scenario, scenario.alice, role)
    challenger = await _add(db, scenario, scenario.bob, "member")

    demoted = await _claim_exclusive_role(
        db, scenario.team.id, role, scenario.bob.id
    )
    challenger.membership_role = role
    await db.flush()

    assert demoted is incumbent
    assert await _roles(db, scenario.team.id) == {
        scenario.alice.id: "member",
        scenario.bob.id: role,
    }


@pytest.mark.parametrize("role", ["primary", "delegate"])
async def test_promoting_without_demoting_first_is_refused_by_the_index(
    db, scenario, role
):
    """The negative control that gives the test above its teeth.

    If this passed, the index would not be enforcing anything and the ordering
    in ``_claim_exclusive_role`` would be decoration. It fails at the
    *statement*, not at commit, which is exactly why the demote must be flushed
    before the promote is issued.
    """
    await _add(db, scenario, scenario.alice, role)
    challenger = await _add(db, scenario, scenario.bob, "member")

    challenger.membership_role = role
    with pytest.raises(IntegrityError) as caught:
        await db.flush()

    assert f"uq_team_{role}" in str(caught.value)


async def test_a_promotion_touches_only_its_own_team(db, scenario):
    """Two teams may each have a primary; the index is per team, not per org."""
    other = Team(
        organization_id=scenario.org.id,
        function_id=scenario.team.function_id,
        name=f"{scenario.team.name}-other",
    )
    db.add(other)
    await db.flush()

    await _add(db, scenario, scenario.alice, "primary")
    db.add(TeamMember(
        team_id=other.id,
        organization_id=scenario.org.id,
        user_id=scenario.bob.id,
        membership_role="primary",
    ))
    await db.flush()

    demoted = await _claim_exclusive_role(
        db, other.id, "primary", scenario.alice.id
    )

    # Alice keeps her primary on her own team; only the other team's holder moved.
    assert demoted is not None
    assert demoted.team_id == other.id
    assert (await _roles(db, scenario.team.id))[scenario.alice.id] == "primary"


async def test_re_promoting_the_current_holder_does_not_demote_them(db, scenario):
    """A no-op promotion must not leave the team with no primary at all."""
    await _add(db, scenario, scenario.alice, "primary")

    demoted = await _claim_exclusive_role(
        db, scenario.team.id, "primary", scenario.alice.id
    )

    assert demoted is None
    assert (await _roles(db, scenario.team.id))[scenario.alice.id] == "primary"


async def test_adding_a_plain_member_moves_nobody(db, scenario):
    """'member' is not exclusive — several people hold it at once."""
    await _add(db, scenario, scenario.alice, "primary")

    demoted = await _claim_exclusive_role(
        db, scenario.team.id, "member", scenario.bob.id
    )
    await _add(db, scenario, scenario.bob, "member")

    assert demoted is None
    assert await _roles(db, scenario.team.id) == {
        scenario.alice.id: "primary",
        scenario.bob.id: "member",
    }


async def test_a_team_may_hold_a_primary_and_a_delegate_at_once(db, scenario):
    """The two indexes are independent; one person each, not one person total."""
    await _add(db, scenario, scenario.alice, "primary")
    await _add(db, scenario, scenario.bob, "delegate")

    assert await _roles(db, scenario.team.id) == {
        scenario.alice.id: "primary",
        scenario.bob.id: "delegate",
    }


# ---------------------------------------------------------------------------
# The consultant case
# ---------------------------------------------------------------------------

async def test_a_user_without_an_organisation_membership_cannot_join_a_team(
    db, scenario
):
    """This is the constraint behind the 400 in ``_require_org_member``.

    ``verify_org_membership`` also admits consultants through
    ``ConsultantClientRelationship``, and such a user has no
    ``organization_members`` row — so the composite foreign key rejects them
    here no matter what the API would prefer. The route pre-checks and returns
    an explanatory 400 rather than letting this surface as a 500.
    """
    outsider = await _make_user(db, org=None)

    db.add(TeamMember(
        team_id=scenario.team.id,
        organization_id=scenario.org.id,
        user_id=outsider.id,
        membership_role="member",
    ))
    with pytest.raises(IntegrityError) as caught:
        await db.flush()

    assert "fk_team_members_org_member" in str(caught.value)


# ---------------------------------------------------------------------------
# Archive, never delete
# ---------------------------------------------------------------------------

async def test_archiving_a_team_keeps_the_row_and_its_roster(db, scenario):
    """DELETE is an archive. History is the point of the feature."""
    await _add(db, scenario, scenario.alice, "primary")

    scenario.team.is_active = False
    await db.flush()

    still_there = (await db.execute(
        select(Team).where(Team.id == scenario.team.id)
    )).scalar_one()

    assert still_there.is_active is False
    assert await _roles(db, scenario.team.id) == {scenario.alice.id: "primary"}
