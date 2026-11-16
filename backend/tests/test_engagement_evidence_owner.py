"""Evidence owner resolution for the engagement review board (engrev001).

The owner of an evidence object is the org's EvidenceTracking row for it,
resolved through the same chain as every notification: explicit user ->
accountable team primary and delegate -> org admins. Fake-session style: no
database. The bulk resolver must cost a fixed number of queries however many
evidence ids it is handed.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.dialects import postgresql

import catalog_models  # noqa: F401 — registers mappers referenced by models
from models import User


class FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def all(self):
        return list(self._value or [])

    def scalars(self):
        value = self._value

        class _Scalars:
            def all(self_inner):
                if isinstance(value, list):
                    return list(value)
                return [] if value is None else [value]

            def first(self_inner):
                if isinstance(value, list):
                    return value[0] if value else None
                return value

        return _Scalars()


class FakeUserResult(FakeResult):
    """A user read: a list for ``scalars().all()``, one row for the
    single-user helpers (``scalar_one_or_none``)."""

    def scalar_one_or_none(self):
        return self._value[0] if self._value else None


def bind_values(statement):
    compiled = statement.compile(dialect=postgresql.dialect())
    values = set()
    for value in compiled.params.values():
        if isinstance(value, (list, tuple)):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


def sql_text(statement):
    return str(statement.compile(dialect=postgresql.dialect()))


def _selects_user_entity(statement):
    try:
        descriptions = statement.column_descriptions
    except Exception:
        return False
    return bool(descriptions) and descriptions[0].get("entity") is User and \
        descriptions[0].get("name") == "User"


class FakeSession:
    def __init__(self, results=None, users=()):
        self._results = list(results or [])
        self.users = {u.id: u for u in users}
        self.statements = []
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.fail_on_commit = False

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        if _selects_user_entity(statement):
            wanted = bind_values(statement)
            found = [u for uid, u in self.users.items() if str(uid) in wanted]
            return FakeUserResult(found)
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self.fail_on_commit:
            raise RuntimeError("database is down")
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass


def make_user(name=None, email=None):
    uid = uuid4()
    return SimpleNamespace(
        id=uid,
        display_name=name,
        email=email or f"{uid.hex[:8]}@example.com",
        email_notifications_enabled=False,
        notification_frequency="daily",
    )


def make_tracking(evidence_id="E-IAC-07", assigned=None, owner=None, is_tracked=True,
                  organization_id=None):
    return SimpleNamespace(
        id=uuid4(),
        organization_id=organization_id,
        evidence_id=evidence_id,
        is_tracked=is_tracked,
        assigned_user_id=assigned,
        owner_user_id=owner,
    )


# ---------------------------------------------------------------------------

from services.engagement_evidence_owner import (  # noqa: E402
    TIER_ACCOUNTABLE_TEAM,
    TIER_EXPLICIT,
    TIER_ORG_ADMIN,
    UNOWNED_LABEL,
    evidence_ownership_flags,
    resolve_evidence_owner,
    resolve_evidence_owners,
)

ORG = uuid4()


@pytest.mark.asyncio
async def test_explicit_user_wins_and_label_is_their_name():
    jo = make_user("Jo Bloggs")
    team_id, primary = uuid4(), uuid4()
    row = make_tracking("E-IAC-07", assigned=jo.id)
    db = FakeSession(
        [
            [row],                                                   # tracking
            [(row.id, team_id, "Platform team", primary, "primary")],  # teams
            [uuid4()],                                               # admins
        ],
        users=[jo],
    )

    owner = await resolve_evidence_owner(db, ORG, "E-IAC-07")

    assert owner.tier == TIER_EXPLICIT
    assert owner.user_ids == (jo.id,)
    assert owner.label == "Jo Bloggs"
    assert owner.tracking_id == row.id
    # The accountable team is reported even though tier 1 won.
    assert owner.team_id == team_id
    assert owner.team_name == "Platform team"


@pytest.mark.asyncio
async def test_owner_user_alone_is_explicit_and_the_label_never_falls_back_to_email():
    """An external auditor reads the owner label on the card, so a user with
    no display name is labelled generically, never by email address."""
    owner_user = make_user(None, email="owner@example.com")
    row = make_tracking(owner=owner_user.id)
    db = FakeSession([[row], [], [uuid4()]], users=[owner_user])

    owner = await resolve_evidence_owner(db, ORG, "E-IAC-07")

    assert owner.tier == TIER_EXPLICIT
    assert owner.label == "Unnamed user"
    assert "@" not in owner.label


@pytest.mark.asyncio
async def test_falls_to_accountable_team_primary_and_delegate():
    team_id, primary, delegate = uuid4(), uuid4(), uuid4()
    row = make_tracking()
    db = FakeSession([
        [row],
        [
            (row.id, team_id, "Platform team", primary, "primary"),
            (row.id, team_id, "Platform team", delegate, "delegate"),
        ],
        [uuid4()],
    ])

    owner = await resolve_evidence_owner(db, ORG, "E-IAC-07")

    assert owner.tier == TIER_ACCOUNTABLE_TEAM
    assert set(owner.user_ids) == {primary, delegate}
    assert owner.label == "Platform team"
    assert owner.team_id == team_id


@pytest.mark.asyncio
async def test_team_ids_carries_every_accountable_team():
    first, second, p1, p2 = uuid4(), uuid4(), uuid4(), uuid4()
    row = make_tracking()
    db = FakeSession([
        [row],
        [
            (row.id, first, "Platform team", p1, "primary"),
            (row.id, second, "Security team", p2, "primary"),
            (row.id, second, "Security team", None, None),
        ],
        [uuid4()],
    ])

    owner = await resolve_evidence_owner(db, ORG, "E-IAC-07")

    assert owner.team_id == first and owner.team_name == "Platform team"
    assert owner.team_ids == (first, second)


@pytest.mark.asyncio
async def test_falls_to_org_admins_when_nobody_is_assigned():
    admin_a, admin_b = uuid4(), uuid4()
    row = make_tracking()
    db = FakeSession([[row], [], [admin_a, admin_b]])

    owner = await resolve_evidence_owner(db, ORG, "E-IAC-07")

    assert owner.tier == TIER_ORG_ADMIN
    assert set(owner.user_ids) == {admin_a, admin_b}
    assert owner.label == UNOWNED_LABEL
    assert owner.team_id is None


@pytest.mark.asyncio
async def test_untracked_and_unassigned_is_unowned():
    admin = uuid4()
    row = make_tracking(is_tracked=False)
    db = FakeSession([[row], [], [admin]])

    owner = await resolve_evidence_owner(db, ORG, "E-IAC-07")

    assert owner.is_tracked is False
    assert owner.tier == TIER_ORG_ADMIN
    assert owner.label == UNOWNED_LABEL


@pytest.mark.asyncio
async def test_no_tracking_row_is_unowned_to_org_admins():
    admin = uuid4()
    # No tracking rows -> the team query is skipped; next result is admins.
    db = FakeSession([[], [admin]])

    owner = await resolve_evidence_owner(db, ORG, "E-NOPE-01")

    assert owner.tracking_id is None
    assert owner.is_tracked is False
    assert owner.tier == TIER_ORG_ADMIN
    assert owner.user_ids == (admin,)
    assert owner.label == UNOWNED_LABEL


@pytest.mark.parametrize("count", [1, 10, 250])
@pytest.mark.asyncio
async def test_bulk_resolver_issues_a_bounded_number_of_queries(count):
    users = [make_user(f"User {i}") for i in range(count)]
    rows = [make_tracking(f"E-X-{i:03d}", assigned=users[i].id) for i in range(count)]
    db = FakeSession([rows, [], [uuid4()]], users=users)

    owners = await resolve_evidence_owners(db, ORG, [r.evidence_id for r in rows])

    assert len(owners) == count
    # tracking + teams + admins + one batched user-name read, whatever the count.
    assert len(db.statements) <= 4
    assert owners["E-X-000"].label == "User 0"


@pytest.mark.asyncio
async def test_bulk_resolver_with_no_ids_runs_no_query():
    db = FakeSession()
    assert await resolve_evidence_owners(db, ORG, []) == {}
    assert db.statements == []


@pytest.mark.asyncio
async def test_bulk_resolver_is_tenant_scoped():
    db = FakeSession([[], [uuid4()]])
    await resolve_evidence_owners(db, ORG, ["E-IAC-07"])
    assert str(ORG) in bind_values(db.statements[0])
    assert "organization_id" in sql_text(db.statements[0])


# --- evidence_ownership_flags ----------------------------------------------

@pytest.mark.asyncio
async def test_flags_for_the_assigned_owner():
    me = uuid4()
    db = FakeSession([make_tracking(assigned=me), None])
    assert await evidence_ownership_flags(db, ORG, "E-IAC-07", me) == (True, False)


@pytest.mark.asyncio
async def test_flags_for_the_owner_user():
    me = uuid4()
    db = FakeSession([make_tracking(owner=me), None])
    assert await evidence_ownership_flags(db, ORG, "E-IAC-07", me) == (True, False)


@pytest.mark.parametrize("role", ["primary", "delegate", "member"])
@pytest.mark.asyncio
async def test_flags_for_any_role_on_the_accountable_team(role):
    me = uuid4()
    db = FakeSession([make_tracking(assigned=uuid4()), uuid4()])

    owns, in_team = await evidence_ownership_flags(db, ORG, "E-IAC-07", me)

    assert (owns, in_team) == (False, True)
    team_stmt = sql_text(db.statements[1])
    # No membership-role filter: any role counts for segregation of duties.
    assert "membership_role" not in team_stmt
    assert "is_accountable" in team_stmt
    assert str(me) in bind_values(db.statements[1])


@pytest.mark.asyncio
async def test_flags_for_neither():
    db = FakeSession([make_tracking(assigned=uuid4()), None])
    assert await evidence_ownership_flags(db, ORG, "E-IAC-07", uuid4()) == (False, False)


@pytest.mark.asyncio
async def test_flags_with_no_tracking_row():
    db = FakeSession([None])
    assert await evidence_ownership_flags(db, ORG, "E-IAC-07", uuid4()) == (False, False)
    assert len(db.statements) == 1
