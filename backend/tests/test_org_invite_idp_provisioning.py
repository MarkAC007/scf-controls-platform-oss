"""Invite-time identity provisioning against the bundled Keycloak (#984).

Inviting someone to an organisation used to write a row and send an email. On a
bundled-Keycloak install that left the invitee with a link, a redirect to the
OP, and no account to log in with -- the invitation promised a login that did
not exist. `services.org_invite.create_invite` now creates that identity.

What these tests are really defending is a short list of things that are easy to
simplify away and expensive to get wrong:

1. **The gate.** On a bring-your-own-OIDC install the platform does not own the
   customer's directory and must never write to it. `test_disabled_*` stubs
   every Keycloak entry point with a function that *raises*, so a future change
   that calls one without checking `is_enabled()` fails loudly rather than
   silently reaching for someone else's user store.
2. **The password never lands anywhere.** It is returned once, emailed once, and
   never written to a column. `test_created_password_is_returned_not_stored` and
   the source-level list-endpoint test are the two halves of that.
3. **An existing identity is not ours.** Someone who already has an account may
   be using it in another organisation. We do not reset their password and we do
   not record them in `idp_user_id` -- so cancelling the invite cannot delete
   them.
4. **No half-states.** A Keycloak failure means no invite at all (an invite that
   cannot be logged into is worse than none); a database failure means the
   account is removed again.

No database here: the unit suite has none (see `tests/conftest.py`), and none is
needed -- every assertion is about ordering and about which calls were made.
Every password literal in this file is a fixed string, never a real credential.

Run:
    python -m pytest tests/test_org_invite_idp_provisioning.py -v
"""
import pathlib
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

import catalog_models  # noqa: F401 — registers mappers referenced by models.System
from models import AuditLog, OrganizationInvite, OrgInviteStatus
from schemas import OrgInviteResponse
from services import keycloak_admin, org_invite
from services.keycloak_admin import KeycloakAdminError, ProvisionResult


# A fixed, obviously-fake value. The real one comes from `token_urlsafe`; this
# file must never contain anything that could be mistaken for a live credential.
UNIT_TEST_TEMP_PASSWORD = "unit-test-temp-password"

BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value

    def scalar_one(self):
        return self._value

    def scalar(self):
        return self._value


class FakeSession:
    """Async session stub replaying scripted results in order.

    `create_invite` runs four queries before it provisions anything (consultant
    relationship, duplicate invite, existing user, org owner). Unscripted
    results come back as None, which is the "clean slate" answer to all four.
    """

    def __init__(self, results=None, commit_error=None):
        self._results = list(results or [])
        self.added = []
        self.commits = 0
        self.flushes = 0
        self._commit_error = commit_error

    async def execute(self, statement, params=None):
        value = self._results.pop(0) if self._results else None
        return FakeResult(value)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushes += 1

    async def commit(self):
        if self._commit_error is not None:
            raise self._commit_error
        self.commits += 1

    async def refresh(self, obj):
        pass

    @property
    def audit_entries(self):
        return [obj for obj in self.added if isinstance(obj, AuditLog)]

    @property
    def invites(self):
        return [obj for obj in self.added if isinstance(obj, OrganizationInvite)]


class Recorder:
    """Records the calls a stub received, so tests assert on them."""

    def __init__(self, result=None, error=None):
        self.calls = []
        self._result = result
        self._error = error

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self._error is not None:
            raise self._error
        return self._result


async def _must_not_be_called(*args, **kwargs):
    raise AssertionError(
        "a Keycloak admin call was made on an install where provisioning is "
        "disabled — the is_enabled() gate has been bypassed"
    )


def _enable(monkeypatch, *, provision=None, delete=None, find=None):
    """Turn provisioning on and install the three stubs it can reach."""
    monkeypatch.setattr(keycloak_admin, "is_enabled", lambda: True)
    monkeypatch.setattr(
        keycloak_admin, "provision_user", provision or _must_not_be_called
    )
    monkeypatch.setattr(keycloak_admin, "delete_user", delete or _must_not_be_called)
    monkeypatch.setattr(keycloak_admin, "find_user_id", find or _must_not_be_called)


def _disable(monkeypatch):
    """A bring-your-own-OIDC install: the gate is shut and nothing may call out."""
    monkeypatch.setattr(keycloak_admin, "is_enabled", lambda: False)
    monkeypatch.setattr(keycloak_admin, "provision_user", _must_not_be_called)
    monkeypatch.setattr(keycloak_admin, "delete_user", _must_not_be_called)
    monkeypatch.setattr(keycloak_admin, "find_user_id", _must_not_be_called)


async def _create(db, **overrides):
    kwargs = dict(
        org_id=uuid4(),
        inviter_user_id=uuid4(),
        inviter_email="admin@example.com",
        email="newcomer@example.com",
        role="viewer",
        message=None,
        db=db,
    )
    kwargs.update(overrides)
    return await org_invite.create_invite(**kwargs)


def _pending_invite(**overrides):
    fields = dict(
        id=uuid4(),
        organization_id=uuid4(),
        invited_by_user_id=uuid4(),
        email="newcomer@example.com",
        role="viewer",
        member_type="internal",
        invite_token="token",
        invite_token_hash="hash",
        status=OrgInviteStatus.PENDING.value,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(days=7),
    )
    fields.update(overrides)
    return OrganizationInvite(**fields)


# ---------------------------------------------------------------------------
# (a) Enabled + a new identity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_created_password_is_returned_not_stored(monkeypatch):
    """A new account: the invite records it, and the password comes back once.

    `idp_user_id` and `idp_provisioned_at` are the platform's claim of ownership
    over this identity — they are what later authorises deleting it on
    cancellation, so they are only ever set for an account this call made.
    """
    user_id = str(uuid4())
    provision = Recorder(
        result=ProvisionResult(
            user_id=user_id,
            created=True,
            temporary_password=UNIT_TEST_TEMP_PASSWORD,
        )
    )
    _enable(monkeypatch, provision=provision)
    db = FakeSession()

    invite, temporary_password = await _create(db)

    assert provision.calls, "provisioning was never attempted"
    assert provision.calls[0][0][0] == "newcomer@example.com"
    assert invite.idp_user_id == user_id
    assert invite.idp_provisioned_at is not None
    assert temporary_password == UNIT_TEST_TEMP_PASSWORD
    # Returned, never persisted: no attribute on the row holds it.
    assert UNIT_TEST_TEMP_PASSWORD not in str(invite.__dict__)
    assert db.commits == 1


@pytest.mark.asyncio
async def test_created_identity_is_audited(monkeypatch):
    """Every IdP mutation writes an audit_log row, in the invite's transaction."""
    user_id = str(uuid4())
    inviter_user_id = uuid4()
    org_id = uuid4()
    _enable(
        monkeypatch,
        provision=Recorder(
            result=ProvisionResult(
                user_id=user_id,
                created=True,
                temporary_password=UNIT_TEST_TEMP_PASSWORD,
            )
        ),
    )
    db = FakeSession()

    await _create(db, org_id=org_id, inviter_user_id=inviter_user_id)

    entries = db.audit_entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.entity_type == "idp_user"
    assert entry.action == "create"
    assert entry.entity_id == UUID(user_id)
    assert entry.organization_id == org_id
    assert entry.changed_by_user_id == inviter_user_id
    assert entry.new_value == "newcomer@example.com"
    # The password is not in the audit trail either.
    assert UNIT_TEST_TEMP_PASSWORD not in str(entry.__dict__)
    # Flushed before the audit row so entity ids are real, committed after so
    # the invite and its audit entry land together.
    assert db.flushes == 1
    assert db.commits == 1


# ---------------------------------------------------------------------------
# (b) Enabled + an identity that already existed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_existing_identity_is_left_alone(monkeypatch):
    """Someone who already has an account keeps it, their password, and no row.

    Recording an identity we did not create would arm the cancellation path to
    delete a stranger's account — one they may be using in another organisation.
    """
    _enable(
        monkeypatch,
        provision=Recorder(
            result=ProvisionResult(
                user_id=str(uuid4()), created=False, temporary_password=None
            )
        ),
    )
    db = FakeSession()

    invite, temporary_password = await _create(db)

    assert invite.idp_user_id is None
    assert invite.idp_provisioned_at is None
    assert temporary_password is None
    assert db.audit_entries == []
    assert db.commits == 1


# ---------------------------------------------------------------------------
# (c) Disabled: an external-OIDC install behaves exactly as before
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_disabled_makes_no_keycloak_call(monkeypatch):
    """Zero HTTP calls, and an invite indistinguishable from the old behaviour."""
    _disable(monkeypatch)
    db = FakeSession()

    invite, temporary_password = await _create(db)

    assert temporary_password is None
    assert invite.idp_user_id is None
    assert invite.idp_provisioned_at is None
    assert db.commits == 1


# ---------------------------------------------------------------------------
# (d) The database commit fails after the account was made
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commit_failure_removes_the_created_account(monkeypatch):
    """No orphan. The identity is undone and the original error still surfaces.

    Leaving it would also poison the retry: the next invite for this address
    would find an existing account, leave it untouched by design, and issue no
    password at all.
    """
    user_id = str(uuid4())
    delete = Recorder()
    boom = RuntimeError("database went away")
    _enable(
        monkeypatch,
        provision=Recorder(
            result=ProvisionResult(
                user_id=user_id,
                created=True,
                temporary_password=UNIT_TEST_TEMP_PASSWORD,
            )
        ),
        delete=delete,
    )
    db = FakeSession(commit_error=boom)

    with pytest.raises(RuntimeError) as exc_info:
        await _create(db)

    assert exc_info.value is boom
    assert [call[0][0] for call in delete.calls] == [user_id]


@pytest.mark.asyncio
async def test_commit_failure_with_existing_identity_deletes_nothing(monkeypatch):
    """A failed commit must not delete an account this invite did not create."""
    _enable(
        monkeypatch,
        provision=Recorder(
            result=ProvisionResult(
                user_id=str(uuid4()), created=False, temporary_password=None
            )
        ),
        delete=_must_not_be_called,
    )
    db = FakeSession(commit_error=RuntimeError("database went away"))

    with pytest.raises(RuntimeError):
        await _create(db)


# ---------------------------------------------------------------------------
# (e) Keycloak refuses: no invite at all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provisioning_failure_creates_no_invite(monkeypatch):
    """A database-only invite is the defect, so the whole request fails.

    The step name survives into `IdpProvisioningError.step`, which is the only
    diagnostic the API is allowed to surface — no response body, no token, no
    password.
    """
    _enable(
        monkeypatch,
        provision=Recorder(
            error=KeycloakAdminError(
                "the user create was rejected", step="create", status_code=500
            )
        ),
    )
    db = FakeSession()

    with pytest.raises(org_invite.IdpProvisioningError) as exc_info:
        await _create(db)

    assert exc_info.value.step == "create"
    assert "rejected" in exc_info.value.message
    assert db.added == []
    assert db.commits == 0


# ---------------------------------------------------------------------------
# (f) and (g) Cancellation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_removes_an_unused_provisioned_identity(monkeypatch):
    """Nobody ever logged in, so the account goes with the invitation.

    Otherwise cancelling leaves an account with a live temporary password behind
    an invitation that no longer exists.
    """
    user_id = str(uuid4())
    invite = _pending_invite(idp_user_id=user_id)
    delete = Recorder()
    _enable(monkeypatch, delete=delete)
    # Query 1: the invite. Query 2: the users row — absent.
    db = FakeSession(results=[invite, None])
    canceller = uuid4()

    result = await org_invite.cancel_invite(
        invite.id, invite.organization_id, db, cancelled_by_user_id=canceller
    )

    assert [call[0][0] for call in delete.calls] == [user_id]
    assert result.status == OrgInviteStatus.CANCELLED.value
    entries = db.audit_entries
    assert len(entries) == 1
    assert entries[0].entity_type == "idp_user"
    assert entries[0].action == "delete"
    assert entries[0].entity_id == UUID(user_id)
    assert entries[0].changed_by_user_id == canceller


@pytest.mark.asyncio
async def test_cancel_spares_an_identity_that_has_been_used(monkeypatch):
    """A `users` row means the person logged in. The identity is theirs now."""
    invite = _pending_invite(idp_user_id=str(uuid4()))
    _enable(monkeypatch, delete=_must_not_be_called)
    db = FakeSession(results=[invite, object()])  # a users row exists

    result = await org_invite.cancel_invite(invite.id, invite.organization_id, db)

    assert result.status == OrgInviteStatus.CANCELLED.value
    assert db.audit_entries == []


@pytest.mark.asyncio
async def test_cancel_without_provisioned_identity_touches_nothing(monkeypatch):
    """A null `idp_user_id` means we never created an account. Nothing to undo."""
    invite = _pending_invite()
    _enable(monkeypatch, delete=_must_not_be_called)
    db = FakeSession(results=[invite])

    result = await org_invite.cancel_invite(invite.id, invite.organization_id, db)

    assert result.status == OrgInviteStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_cancel_survives_a_keycloak_failure(monkeypatch):
    """The invitation is still withdrawn; the stranded account is the operator's.

    Refusing to cancel because a directory call failed would leave an admin with
    an invitation they cannot take back.
    """
    invite = _pending_invite(idp_user_id=str(uuid4()))
    _enable(
        monkeypatch,
        delete=Recorder(
            error=KeycloakAdminError(
                "the user delete was rejected", step="delete", status_code=503
            )
        ),
    )
    db = FakeSession(results=[invite, None])

    result = await org_invite.cancel_invite(invite.id, invite.organization_id, db)

    assert result.status == OrgInviteStatus.CANCELLED.value
    # No audit row: nothing was deleted, so nothing is claimed to have been.
    assert db.audit_entries == []


# ---------------------------------------------------------------------------
# (h) idp_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idp_status_external_when_disabled(monkeypatch):
    _disable(monkeypatch)
    status = await org_invite.idp_status_for(_pending_invite(), FakeSession())
    assert status == "external"


@pytest.mark.asyncio
async def test_idp_status_provisioned_from_the_column(monkeypatch):
    """A recorded id needs no lookup — the stub would raise if one happened."""
    _enable(monkeypatch)
    invite = _pending_invite(idp_user_id=str(uuid4()))
    assert await org_invite.idp_status_for(invite, FakeSession()) == "provisioned"


@pytest.mark.asyncio
async def test_idp_status_provisioned_for_a_pre_existing_account(monkeypatch):
    """No column, but the realm has the address: still `provisioned`.

    Reporting `not_in_idp` here would send an admin off to create a duplicate
    account for someone who already has one.
    """
    _enable(monkeypatch, find=Recorder(result=str(uuid4())))
    assert await org_invite.idp_status_for(_pending_invite(), FakeSession()) == (
        "provisioned"
    )


@pytest.mark.asyncio
async def test_idp_status_not_in_idp(monkeypatch):
    _enable(monkeypatch, find=Recorder(result=None))
    assert await org_invite.idp_status_for(_pending_invite(), FakeSession()) == (
        "not_in_idp"
    )


@pytest.mark.asyncio
async def test_idp_status_lookup_failure_does_not_raise(monkeypatch):
    """A directory hiccup must not take the invitations page down."""
    _enable(
        monkeypatch,
        find=Recorder(
            error=KeycloakAdminError("the user lookup was rejected", step="lookup")
        ),
    )
    assert await org_invite.idp_status_for(_pending_invite(), FakeSession()) == (
        "not_in_idp"
    )


@pytest.mark.asyncio
async def test_idp_statuses_for_is_cheap(monkeypatch):
    """One lookup per address, none for rows that cannot need one.

    The list endpoint renders every invitation an organisation ever sent; a
    round trip per row turns a page load into a directory scan.
    """
    find = Recorder(result=None)
    _enable(monkeypatch, find=find)
    provisioned = _pending_invite(idp_user_id=str(uuid4()))
    pending_a = _pending_invite(email="a@example.com")
    pending_a_again = _pending_invite(email="a@example.com")
    cancelled = _pending_invite(
        email="gone@example.com", status=OrgInviteStatus.CANCELLED.value
    )
    invites = [provisioned, pending_a, pending_a_again, cancelled]

    statuses = await org_invite.idp_statuses_for(invites, FakeSession())

    assert statuses[provisioned.id] == "provisioned"
    assert statuses[pending_a.id] == "not_in_idp"
    assert statuses[pending_a_again.id] == "not_in_idp"
    assert statuses[cancelled.id] == "not_in_idp"
    assert len(find.calls) == 1, "one lookup per distinct pending address"


@pytest.mark.asyncio
async def test_idp_statuses_for_disabled_makes_no_call(monkeypatch):
    _disable(monkeypatch)
    invites = [_pending_invite(), _pending_invite(email="b@example.com")]
    statuses = await org_invite.idp_statuses_for(invites, FakeSession())
    assert set(statuses.values()) == {"external"}


# ---------------------------------------------------------------------------
# The password never reaches a list response
# ---------------------------------------------------------------------------

def test_list_response_carries_no_password():
    """The schema allows the field to be absent, and the list endpoint omits it.

    Nothing stores the password, so the list endpoint has nothing to leak even
    if it tried — but the field exists on the shared response model, and a
    copy-paste from the create handler is the way it would get returned anyway.
    That is what the source assertion below guards.
    """
    response = OrgInviteResponse(
        id=uuid4(),
        organization_id=uuid4(),
        organization_name="Example Ltd",
        email="newcomer@example.com",
        role="viewer",
        member_type="internal",
        status="pending",
        invite_token=None,
        idp_temporary_password=None,
        idp_status="not_in_idp",
        expires_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    assert response.idp_temporary_password is None

    source = (BACKEND_DIR / "api" / "users.py").read_text()
    assert "idp_temporary_password=None,  # Never stored, never listed" in source, (
        "the invite list endpoint must pass the password field explicitly as "
        "None, so that a future edit cannot quietly start returning it"
    )


def test_invitation_email_accepts_the_password_by_that_name():
    """The `ops` agent's template renders `temporary_password`. Keep the name."""
    import inspect

    from services.email_service import send_invitation_email

    parameter = inspect.signature(send_invitation_email).parameters[
        "temporary_password"
    ]
    assert parameter.default is None, "existing callers must keep working"
