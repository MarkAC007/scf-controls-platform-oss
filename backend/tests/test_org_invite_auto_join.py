"""Sign-in acceptance of provisioned invites (#984 follow-up).

On a bundled-Keycloak install the invite creates the invitee's Keycloak account
and records its id in ``idp_user_id``. Signing in with that account is the
acceptance: :func:`services.org_invite.accept_provisioned_invites` joins the
organisation there and then, so the invitee lands in the workspace rather than
on an empty one, and the admin sees them in the member list.

What these tests defend:

1. **The binding is the subject, not the email.** An invite whose
   ``idp_user_id`` is empty or belongs to a different subject is left alone no
   matter whose email it carries. Email alone would let any identity provider
   asserting that address claim the seat.
2. **The membership is the same one the link path creates.** Role and
   member_type come off the invite, and an ``org_member`` audit row lands in the
   same flush.
3. **Nothing is consumed that should not be.** Expired invites stay pending
   (the link path reports them as expired); an existing membership is not
   duplicated.
4. **Login is never the casualty.** ``auth.upsert`` wraps the call; a failure
   inside it is logged, rolled back, and the user is still returned.

No database: every assertion is about which rows were added and which fields
were set.

Run:
    python -m pytest tests/test_org_invite_auto_join.py -v
"""
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

import catalog_models  # noqa: F401 — registers mappers referenced by models.System
from models import AuditLog, OrganizationInvite, OrganizationMember, OrgInviteStatus
from services import org_invite


class FakeScalars:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return self._values


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalars(self):
        return FakeScalars(self._value if isinstance(self._value, list) else [])

    def scalar_one_or_none(self):
        return self._value

    def first(self):
        return self._value


class FakeSession:
    """Replays scripted results in order: first the invite list, then one
    membership lookup per non-expired invite."""

    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj):
        pass

    @property
    def members(self):
        return [o for o in self.added if isinstance(o, OrganizationMember)]

    @property
    def audit_entries(self):
        return [o for o in self.added if isinstance(o, AuditLog)]


def _invite(**overrides):
    fields = dict(
        id=uuid4(),
        organization_id=uuid4(),
        invited_by_user_id=uuid4(),
        email="newcomer@example.com",
        role="editor",
        member_type="contractor",
        invite_token="token",
        invite_token_hash="hash",
        status=OrgInviteStatus.PENDING.value,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=7),
        idp_user_id=str(uuid4()),
        idp_provisioned_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return OrganizationInvite(**fields)


# ---------------------------------------------------------------------------
# The happy path: the account the invite created signs in
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provisioned_invite_joins_with_invite_role_and_member_type():
    invite = _invite()
    user_id = uuid4()
    db = FakeSession(results=[[invite], None])

    accepted = await org_invite.accept_provisioned_invites(
        sub=invite.idp_user_id, user_id=user_id, db=db
    )

    assert accepted == [invite]
    assert invite.status == OrgInviteStatus.ACCEPTED.value
    assert len(db.members) == 1
    member = db.members[0]
    assert member.organization_id == invite.organization_id
    assert member.user_id == user_id
    assert member.role == "editor"
    assert member.member_type == "contractor"


@pytest.mark.asyncio
async def test_membership_is_audited_in_the_same_flush():
    invite = _invite()
    user_id = uuid4()
    db = FakeSession(results=[[invite], None])

    await org_invite.accept_provisioned_invites(sub=invite.idp_user_id, user_id=user_id, db=db)

    # One audit row per tracked field (role, member_type), all on org_member.
    assert {e.field_name for e in db.audit_entries} == {"role", "member_type"}
    for entry in db.audit_entries:
        assert entry.entity_type == "org_member"
        assert entry.action == "create"
        assert entry.organization_id == invite.organization_id
        assert entry.changed_by_user_id == user_id
    by_field = {e.field_name: e for e in db.audit_entries}
    assert json.loads(by_field["member_type"].new_value) == "contractor"
    assert json.loads(by_field["role"].new_value) == "editor"
    # Flushed so the audit row can reference member.id; committing is the
    # caller's job (auth.upsert owns the transaction).
    assert db.flushes >= 1
    assert db.commits == 0


@pytest.mark.asyncio
async def test_every_provisioned_invite_for_the_subject_is_joined():
    """Two organisations invited the same new person; one sign-in joins both."""
    sub = str(uuid4())
    first, second = _invite(idp_user_id=sub), _invite(idp_user_id=sub)
    db = FakeSession(results=[[first, second], None, None])

    accepted = await org_invite.accept_provisioned_invites(sub=sub, user_id=uuid4(), db=db)

    assert {i.id for i in accepted} == {first.id, second.id}
    assert {m.organization_id for m in db.members} == {
        first.organization_id,
        second.organization_id,
    }


# ---------------------------------------------------------------------------
# What must NOT be consumed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_invite_is_left_pending():
    invite = _invite(
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
    )
    db = FakeSession(results=[[invite]])

    accepted = await org_invite.accept_provisioned_invites(
        sub=invite.idp_user_id, user_id=uuid4(), db=db
    )

    assert accepted == []
    assert invite.status == OrgInviteStatus.PENDING.value
    assert db.members == []
    assert db.audit_entries == []


@pytest.mark.asyncio
async def test_existing_membership_is_not_duplicated_but_invite_closes():
    invite = _invite()
    user_id = uuid4()
    already = OrganizationMember(
        organization_id=invite.organization_id, user_id=user_id, role="viewer"
    )
    db = FakeSession(results=[[invite], already])

    accepted = await org_invite.accept_provisioned_invites(
        sub=invite.idp_user_id, user_id=user_id, db=db
    )

    assert accepted == [invite]
    assert invite.status == OrgInviteStatus.ACCEPTED.value
    assert db.members == []
    assert db.audit_entries == []


@pytest.mark.asyncio
async def test_empty_subject_touches_nothing():
    db = FakeSession(results=[[_invite()]])

    assert await org_invite.accept_provisioned_invites(sub="", user_id=uuid4(), db=db) == []
    assert db.members == []
    # Not even the lookup ran.
    assert len(db._results) == 1


def test_lookup_is_bound_to_the_subject_never_the_email():
    """The query filters on ``idp_user_id``; the email column is not in it.

    Source-level because the fake session returns whatever it is scripted to.
    The property that matters is *which* column the WHERE clause names: an
    email predicate here would let a bring-your-own IdP claim an org seat by
    asserting an address.
    """
    import inspect

    src = inspect.getsource(org_invite.accept_provisioned_invites)
    where = src[src.index("select(OrganizationInvite)"):src.index("scalars().all()")]
    assert "OrganizationInvite.idp_user_id == sub" in where
    assert "OrganizationInvite.email" not in where


# ---------------------------------------------------------------------------
# The link path is unchanged and shares the helper
# ---------------------------------------------------------------------------

def test_accept_invite_and_auto_join_share_one_membership_helper():
    import inspect

    link_src = inspect.getsource(org_invite.accept_invite)
    join_src = inspect.getsource(org_invite.accept_provisioned_invites)
    assert "_grant_membership(" in link_src
    assert "_grant_membership(" in join_src
    # Neither path builds a membership row on its own any more.
    assert "OrganizationMember(" not in link_src
    assert "OrganizationMember(" not in join_src


# ---------------------------------------------------------------------------
# Login survives a failure inside the join
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_auth_upsert_returns_user_when_auto_join_raises(monkeypatch):
    """``auth.upsert`` must treat the join as best effort.

    The user is committed first; if the join blows up the session is rolled
    back and the user is still returned, so the invitee can at least sign in
    and use the link, and the invite stays pending for that.
    """
    import auth as auth_module
    from models import User as DBUser

    class ExplodingSession(FakeSession):
        def __init__(self):
            super().__init__(results=[])
            self.user = DBUser(
                id=uuid4(),
                google_sub="kc-sub",
                oidc_issuer="http://idp/realms/scf",
                email="newcomer@example.com",
                display_name="Newcomer",
            )

        async def execute(self, statement, params=None):
            # Every lookup the upsert makes: returning-user match hits first.
            return FakeResult(self.user)

    async def boom(**kwargs):
        raise RuntimeError("audit table locked")

    monkeypatch.setattr(org_invite, "accept_provisioned_invites", boom)
    db = ExplodingSession()

    user = await auth_module._persist_oidc_user(
        db=db,
        sub="kc-sub",
        issuer="http://idp/realms/scf",
        email="newcomer@example.com",
        display_name="Newcomer",
        email_verified=True,
    )

    assert user is db.user
    assert db.commits == 1          # the user commit went through
    assert db.rollbacks == 1        # the failed join was rolled back
