"""Engagement evidence review notifications (engrev001).

Each creator routes as the board needs, excludes the actor, stamps
``engagement_id`` on every row, and never raises. Fake-session style: the
queue answers the routing reads in order; ``select(User)`` reads are answered
from a user registry, so the rows written are exactly the people the code
resolved.
"""
import os
import sys
from unittest.mock import AsyncMock, patch

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

from services import notifications as svc  # noqa: E402
from services.owner_resolution import (  # noqa: E402
    EVENTS,
    ROUTE_DIRECTED,
    ROUTE_OWNERSHIP,
    ROUTE_ORG_ADMIN,
)

ORG = uuid4()
ENG = uuid4()
REVIEW = uuid4()


@pytest.fixture(autouse=True)
def no_email():
    with patch.object(svc, "send_event_notification_email", AsyncMock()):
        yield


def recipients(db):
    return {n.user_id for n in db.added}


def assert_stamped(db, notification_type, reference_type="engagement_evidence_review",
                   reference_id=REVIEW):
    assert db.added, "expected notification rows"
    for n in db.added:
        assert n.engagement_id == ENG
        assert n.organization_id == ORG
        assert n.type == notification_type
        assert n.reference_type == reference_type
        assert n.reference_id == reference_id


# --- EVENTS rows ------------------------------------------------------------

@pytest.mark.parametrize("key,route,escalates", [
    ("engagement_evidence_action_required", ROUTE_OWNERSHIP, True),
    ("engagement_evidence_submitted", ROUTE_DIRECTED, False),
    ("engagement_evidence_reassigned", ROUTE_DIRECTED, False),
    ("engagement_evidence_query_raised", ROUTE_OWNERSHIP, False),
    ("engagement_query_raised", ROUTE_ORG_ADMIN, False),
    ("engagement_query_answered", ROUTE_DIRECTED, False),
    ("mention", ROUTE_DIRECTED, False),
])
def test_events_rows(key, route, escalates):
    assert EVENTS[key].routing == route
    assert EVENTS[key].escalates is escalates


# --- action required: owner chain + escalation ------------------------------

@pytest.mark.asyncio
async def test_action_required_goes_to_owner_plus_escalation_and_excludes_actor():
    owner = make_user("Owner")
    primary, delegate, consulted = make_user(), make_user(), make_user()
    auditor = make_user("Auditor")
    row = make_tracking(assigned=owner.id)
    db = FakeSession(
        [
            row,                                                      # tracking
            [(primary.id, "primary"), (delegate.id, "delegate")],     # accountable team
            [(consulted.id, "primary"), (auditor.id, "primary")],     # consulted teams
            [uuid4()],                                                # admins
        ],
        users=[owner, primary, delegate, consulted, auditor],
    )

    created = await svc.create_engagement_evidence_action_required_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=auditor.id, note="Need the Q3 export",
    )

    assert recipients(db) == {owner.id, primary.id, delegate.id, consulted.id}
    assert created == 4
    assert_stamped(db, "engagement_evidence_action_required")
    assert db.added[0].message == "Auditor needs more on evidence E-IAC-07: Need the Q3 export"


@pytest.mark.asyncio
async def test_action_required_note_is_trimmed():
    owner = make_user()
    db = FakeSession([make_tracking(assigned=owner.id), [], [], []], users=[owner])

    await svc.create_engagement_evidence_action_required_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=uuid4(), note="x" * 1000,
    )

    assert len(db.added[0].message) < 220
    assert db.added[0].message.endswith("…")


@pytest.mark.asyncio
async def test_action_required_with_no_tracking_row_falls_to_org_admins():
    admin = make_user()
    # No row: no team or consulted statement, straight to admins.
    db = FakeSession([None, [admin.id]], users=[admin])

    created = await svc.create_engagement_evidence_action_required_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-NONE-01", actor_user_id=uuid4(),
    )

    assert created == 1
    assert recipients(db) == {admin.id}
    assert_stamped(db, "engagement_evidence_action_required")


# --- submitted --------------------------------------------------------------

@pytest.mark.asyncio
async def test_submitted_goes_only_to_the_active_assigned_reviewer():
    reviewer, other = make_user(), make_user()
    actor = make_user("Org editor")
    db = FakeSession([[reviewer.id, other.id]], users=[reviewer, other, actor])

    created = await svc.create_engagement_evidence_submitted_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=actor.id,
        assigned_reviewer_user_id=reviewer.id,
    )

    assert created == 1
    assert recipients(db) == {reviewer.id}
    assert_stamped(db, "engagement_evidence_submitted")
    assert "active" in bind_values(db.statements[0])


@pytest.mark.asyncio
async def test_submitted_with_no_reviewer_goes_to_every_active_auditor():
    a, b = make_user(), make_user()
    db = FakeSession([[a.id, b.id]], users=[a, b])

    created = await svc.create_engagement_evidence_submitted_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=uuid4(),
    )

    assert created == 2
    assert recipients(db) == {a.id, b.id}


@pytest.mark.asyncio
async def test_submitted_with_revoked_reviewer_goes_to_every_active_auditor():
    revoked_reviewer, a, b = make_user(), make_user(), make_user()
    db = FakeSession([[a.id, b.id]], users=[revoked_reviewer, a, b])

    await svc.create_engagement_evidence_submitted_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=uuid4(),
        assigned_reviewer_user_id=revoked_reviewer.id,
    )

    assert recipients(db) == {a.id, b.id}


@pytest.mark.asyncio
async def test_submitted_excludes_the_actor():
    actor, other = make_user(), make_user()
    db = FakeSession([[actor.id, other.id]], users=[actor, other])

    await svc.create_engagement_evidence_submitted_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=actor.id,
    )

    assert recipients(db) == {other.id}


# --- reassigned -------------------------------------------------------------

@pytest.mark.asyncio
async def test_reassigned_goes_to_the_new_reviewer_only():
    new_reviewer, actor = make_user(), make_user("Lead auditor")
    db = FakeSession([], users=[new_reviewer, actor])

    created = await svc.create_engagement_evidence_reassigned_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=actor.id,
        new_reviewer_user_id=new_reviewer.id,
    )

    assert created == 1
    assert recipients(db) == {new_reviewer.id}
    assert_stamped(db, "engagement_evidence_reassigned")
    assert db.added[0].message == "Lead auditor assigned you to review evidence E-IAC-07"


@pytest.mark.asyncio
async def test_reassigning_yourself_notifies_nobody():
    me = make_user()
    db = FakeSession([], users=[me])

    created = await svc.create_engagement_evidence_reassigned_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", actor_user_id=me.id, new_reviewer_user_id=me.id,
    )

    assert created == 0
    assert db.added == []


# --- evidence query: ownership; control query untouched ---------------------

@pytest.mark.asyncio
async def test_evidence_query_goes_to_the_evidence_owner_without_escalation():
    owner, auditor = make_user(), make_user()
    query_id = uuid4()
    team_primary = uuid4()
    db = FakeSession(
        [make_tracking(assigned=owner.id), [(team_primary, "primary")], [uuid4()]],
        users=[owner, auditor],
    )

    created = await svc.create_engagement_evidence_query_raised_notifications(
        db, organization_id=ORG, engagement_id=ENG, query_id=query_id,
        evidence_id="E-IAC-07", raised_by_user_id=auditor.id,
    )

    assert created == 1
    assert recipients(db) == {owner.id}
    assert_stamped(db, "engagement_evidence_query_raised",
                   reference_type="engagement_query", reference_id=query_id)


@pytest.mark.asyncio
async def test_control_query_creator_still_goes_to_org_admins_unstamped():
    admin, owner_like = make_user(), make_user()
    db = FakeSession([[admin.id]], users=[admin, owner_like])

    created = await svc.create_engagement_query_raised_notifications(
        db, ORG, uuid4(), "IAC-01", raised_by_user_id=uuid4(),
    )

    assert created == 1
    assert recipients(db) == {admin.id}
    assert db.added[0].type == "engagement_query_raised"
    assert db.added[0].engagement_id is None


# --- query answered: the raiser, while they still have access ---------------

@pytest.mark.asyncio
async def test_answer_notifies_the_raiser_with_display_name_only():
    raiser = make_user("Ada Auditor")
    responder = make_user(None, email="jane.doe@client.example")   # no display name
    query_id = uuid4()
    db = FakeSession([[raiser.id]], users=[raiser, responder])     # raiser holds an active grant

    created = await svc.create_engagement_query_answered_notifications(
        db, organization_id=ORG, engagement_id=ENG, query_id=query_id,
        raised_by_user_id=raiser.id, responder_user_id=responder.id, scf_id="IAC-01",
    )

    assert created == 1
    assert recipients(db) == {raiser.id}
    assert_stamped(db, "engagement_query_answered",
                   reference_type="engagement_query", reference_id=query_id)
    assert db.added[0].message == "Someone answered your query on control IAC-01"
    assert "client.example" not in db.added[0].message
    assert "active" in bind_values(db.statements[0])


@pytest.mark.asyncio
async def test_answer_on_an_evidence_query_names_the_evidence():
    raiser, responder = make_user("Ada"), make_user("Olu Owner")
    db = FakeSession([[], raiser.id], users=[raiser, responder])  # no grant; an org member

    created = await svc.create_engagement_query_answered_notifications(
        db, organization_id=ORG, engagement_id=ENG, query_id=uuid4(),
        raised_by_user_id=raiser.id, responder_user_id=responder.id, evidence_id="E-IAC-07",
    )

    assert created == 1
    assert db.added[0].message == "Olu Owner answered your query on evidence E-IAC-07"


@pytest.mark.asyncio
async def test_raiser_answering_their_own_query_is_not_notified():
    me = make_user()
    db = FakeSession([], users=[me])
    created = await svc.create_engagement_query_answered_notifications(
        db, organization_id=ORG, engagement_id=ENG, query_id=uuid4(),
        raised_by_user_id=me.id, responder_user_id=me.id, scf_id="IAC-01",
    )
    assert created == 0
    assert db.added == [] and db.statements == []


@pytest.mark.asyncio
async def test_raiser_who_lost_access_is_not_notified():
    revoked = make_user("Former auditor")
    db = FakeSession([[], None], users=[revoked])   # no active grant, not a member
    created = await svc.create_engagement_query_answered_notifications(
        db, organization_id=ORG, engagement_id=ENG, query_id=uuid4(),
        raised_by_user_id=revoked.id, responder_user_id=uuid4(), scf_id="IAC-01",
    )
    assert created == 0
    assert db.added == []


# --- mentions -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_mentions_go_only_to_the_listed_users_minus_the_author():
    author, a, b, bystander = make_user("Author"), make_user(), make_user(), make_user()
    db = FakeSession([], users=[author, a, b, bystander])

    created = await svc.create_engagement_mention_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", comment_id=uuid4(), author_user_id=author.id,
        mentioned_user_ids=[a.id, b.id, author.id],
    )

    assert created == 2
    assert recipients(db) == {a.id, b.id}
    assert_stamped(db, "mention")


@pytest.mark.asyncio
async def test_no_mentions_notifies_nobody_and_runs_no_query():
    db = FakeSession([])
    created = await svc.create_engagement_mention_notifications(
        db, organization_id=ORG, engagement_id=ENG, review_id=REVIEW,
        evidence_id="E-IAC-07", comment_id=uuid4(), author_user_id=uuid4(),
        mentioned_user_ids=[],
    )
    assert created == 0
    assert db.statements == []


# --- failure handling ----------------------------------------------------------

CALLS = [
    ("create_engagement_evidence_action_required_notifications",
     dict(review_id=REVIEW, evidence_id="E-IAC-07", actor_user_id=None)),
    ("create_engagement_evidence_submitted_notifications",
     dict(review_id=REVIEW, evidence_id="E-IAC-07", actor_user_id=None)),
    ("create_engagement_evidence_reassigned_notifications",
     dict(review_id=REVIEW, evidence_id="E-IAC-07", actor_user_id=None,
          new_reviewer_user_id="SET")),
    ("create_engagement_evidence_query_raised_notifications",
     dict(query_id=uuid4(), evidence_id="E-IAC-07", raised_by_user_id=None)),
    ("create_engagement_mention_notifications",
     dict(review_id=REVIEW, evidence_id="E-IAC-07", comment_id=uuid4(),
          author_user_id=None, mentioned_user_ids="SET")),
]


@pytest.mark.parametrize("name,kwargs", CALLS)
@pytest.mark.asyncio
async def test_a_failing_emit_returns_zero_and_does_not_raise(name, kwargs):
    user = make_user()
    kwargs = dict(kwargs)
    if kwargs.get("new_reviewer_user_id") == "SET":
        kwargs["new_reviewer_user_id"] = user.id
    if kwargs.get("mentioned_user_ids") == "SET":
        kwargs["mentioned_user_ids"] = [user.id]
    db = FakeSession(
        [make_tracking(assigned=user.id), [], [], [], [user.id]] if "submitted" not in name
        else [[user.id]],
        users=[user],
    )
    db.fail_on_commit = True

    created = await getattr(svc, name)(
        db, organization_id=ORG, engagement_id=ENG, **kwargs,
    )

    assert created == 0
    assert db.rolled_back is True


@pytest.mark.parametrize("name,kwargs", CALLS)
@pytest.mark.asyncio
async def test_a_failing_resolution_returns_zero_and_does_not_raise(name, kwargs):
    kwargs = {k: (uuid4() if v == "SET" and k == "new_reviewer_user_id"
                  else [uuid4()] if v == "SET" else v) for k, v in kwargs.items()}
    db = FakeSession()

    async def boom(*a, **k):
        raise RuntimeError("resolver exploded")

    with patch.object(svc, "resolve_recipients_for", boom):
        created = await getattr(svc, name)(
            db, organization_id=ORG, engagement_id=ENG, **kwargs,
        )

    assert created == 0
