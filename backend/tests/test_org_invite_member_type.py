"""An invited contractor arrives as a contractor, and the arrival is audited (#822 phase 2).

``organization_invites.member_type`` exists for one reason: an employment type
chosen when the invite is sent must survive until somebody accepts it, days
later, and be copied onto the membership that gets created. If
``accept_invite`` drops the field, the selector in the invite modal is a
control that silently does nothing — which is the precedent defect #822 was
raised over, reappearing one table along.

So the assertions here are deliberately on the **resulting
``organization_members`` row**, never on the invite row. An invite that stores
``external_contractor`` and creates an internal membership is exactly the
failure, and a test that stopped at the invite would call it a pass.

* **ISC-38** an invite carrying ``external_contractor`` yields a contractor
  membership on acceptance;
* **ISC-39** an invite carrying nothing yields ``'internal'`` — the path almost
  every real invite takes, and the one where a missing default would be least
  visible;
* **ISC-41** acceptance writes ``audit_log`` rows for the created membership,
  naming ``role`` and ``member_type``. A member *reclassified* by PATCH is
  audited (ISC-31); a member who *arrives* as a contractor through an invite
  must be too, or the contractor report shows somebody accountable for a
  control with no record of how they got there.

Two negatives, because "the right membership changed" is only half of it: the
invite must not touch any other membership in the organisation, and must not
reach across into the invitee's membership of a different organisation — the
same person is deliberately staff in one and arriving as a contractor in the
other.

Driven over HTTP end to end, create through accept, because the field has to
survive both hops and a service-level test would not exercise the request
schema's default. Both endpoints commit, so the estate is committed too and
torn down explicitly; deleting the organisation cascades to its invites,
memberships and ``audit_log`` rows, which is the only permitted way to remove
the last of those (the table is append-only, #789).

Out of scope, deliberately: ``consultant_invites`` (a different flow, not named
by phase 2), and auditing invite *creation*, *cancellation* or *expiry* — none
of those is a membership mutation.

Run with::

    docker run --rm --network cg-scf-network -v <worktree>/backend:/app \\
        -w /app ghcr.io/markac007/scf-backend:latest \\
        python -m pytest tests/test_org_invite_member_type.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402
import main  # noqa: E402
from database import get_db  # noqa: E402
from models import (  # noqa: E402
    AuditLog,
    Organization,
    OrganizationInvite,
    OrganizationMember,
    User,
)
from services.audit_service import ORG_MEMBER_TRACKED_FIELDS  # noqa: E402
from services.org_utils import MEMBER_TYPES  # noqa: E402

INVITE_PATH = "/api/organizations/{org_id}/invite"
INVITES_PATH = "/api/organizations/{org_id}/invites"
ACCEPT_PATH = "/api/org-invites/{token}/accept"

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

def _invite_body_fields() -> set[str]:
    """Property names on the POST-invite request body, from the OpenAPI schema.

    ``member_type`` is a **body** field here, unlike the PATCH on an existing
    member where it is a query parameter. Two different shapes on purpose, so
    this probe deliberately looks somewhere different from the one in
    ``test_org_member_type.py``.
    """
    schema = main.app.openapi()
    operation = schema["paths"].get(INVITE_PATH, {}).get("post")
    if operation is None:
        return set()
    content = operation.get("requestBody", {}).get("content", {})
    ref = content.get("application/json", {}).get("schema", {}).get("$ref")
    if not ref:
        return set()
    model = schema["components"]["schemas"].get(ref.rsplit("/", 1)[-1], {})
    return set(model.get("properties", {}))


def _needs_invite_member_type() -> None:
    fields = _invite_body_fields()
    if "member_type" not in fields:
        pytest.skip(
            f"POST {INVITE_PATH} does not yet accept a 'member_type' body field "
            f"(it accepts {sorted(fields)}). The API workstream is adding it on "
            "a parallel branch. This is a SKIP, not a pass."
        )


class TestTheCapabilityProbeWorks:
    """Fails — never skips — if the probe stops seeing the invite body."""

    def test_the_invite_route_is_discoverable(self):
        assert "post" in main.app.openapi()["paths"].get(INVITE_PATH, {})

    def test_the_probe_sees_the_fields_that_already_exist(self):
        # `email` and `role` predate #822. If the probe cannot see them it
        # cannot see member_type either, and its skip would mean nothing.
        assert {"email", "role"} <= _invite_body_fields()

    def test_the_accept_route_is_discoverable(self):
        assert "post" in main.app.openapi()["paths"].get(ACCEPT_PATH, {})


# ---------------------------------------------------------------------------
# ISC-38 / ISC-39 — the choice survives to the membership
# ---------------------------------------------------------------------------

class TestTheInvitedEmploymentTypeReachesTheMembership:
    async def test_a_contractor_invite_creates_a_contractor_membership(
        self, api, session, estate,
    ):
        """ISC-38. Asserted on organization_members, not on the invite row.

        An invite that stores the value correctly and then creates an internal
        membership IS the defect; only the membership can tell them apart.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)
        assert await _member_type(session, estate.org_id, estate.invitee) == CONTRACTOR

    async def test_an_invite_that_says_nothing_creates_an_internal_membership(
        self, api, session, estate,
    ):
        """ISC-39. The default path — what almost every real invite does."""
        _needs_invite_member_type()
        token = await api.invite()  # no member_type in the body at all
        await api.accept(token)
        assert await _member_type(session, estate.org_id, estate.invitee) == INTERNAL

    async def test_an_explicit_internal_invite_also_creates_an_internal_membership(
        self, api, session, estate,
    ):
        # The mirror of ISC-38: without this, an accept path that hardcoded
        # 'external_contractor' would satisfy it.
        _needs_invite_member_type()
        token = await api.invite(member_type=INTERNAL)
        await api.accept(token)
        assert await _member_type(session, estate.org_id, estate.invitee) == INTERNAL

    async def test_the_pending_invite_shows_what_acceptance_will_grant(self, api, estate):
        """An admin reviewing the list should not have to accept one to find out."""
        _needs_invite_member_type()
        await api.invite(member_type=CONTRACTOR)
        api.as_("admin")
        response = await api.client.get(INVITES_PATH.format(org_id=estate.org_id))
        assert response.status_code == 200, response.text
        invites = response.json()["invites"]
        assert [i["member_type"] for i in invites] == [CONTRACTOR]

    async def test_an_invalid_member_type_is_refused_at_send(self, api, estate):
        """Refused when the admin can still fix it, not at acceptance.

        The invite's value is copied verbatim onto the membership, so a value
        that slipped past here would fail against the membership's CHECK
        constraint days later, in front of the invitee.
        """
        _needs_invite_member_type()
        api.as_("admin")
        response = await api.client.post(
            INVITE_PATH.format(org_id=estate.org_id),
            json={"email": estate.invitee_email, "role": "viewer",
                  "member_type": "contractor"},
        )
        assert response.status_code in (400, 422), response.text

    async def test_every_value_in_the_vocabulary_survives_acceptance(
        self, api, session, estate,
    ):
        """Parameterised over MEMBER_TYPES rather than the pair spelled out.

        A third employment type added later extends this test automatically
        instead of slipping through the two cases above.
        """
        _needs_invite_member_type()
        for value in sorted(MEMBER_TYPES):
            token = await api.invite(member_type=value)
            await api.accept(token)
            assert await _member_type(session, estate.org_id, estate.invitee) == value
            # Clear the membership so the next value can be accepted; the
            # endpoint refuses an invite for somebody who is already a member.
            await _drop_membership(session, estate.org_id, estate.invitee)


# ---------------------------------------------------------------------------
# The negatives — nothing else moves
# ---------------------------------------------------------------------------

class TestTheInviteChangesNothingElse:
    async def test_no_other_membership_in_the_organisation_is_touched(
        self, api, session, estate,
    ):
        """An arriving contractor must not relabel the people already there."""
        _needs_invite_member_type()
        before = await _all_member_types(session, estate.org_id)
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)
        after = await _all_member_types(session, estate.org_id)

        assert after.pop(estate.invitee) == CONTRACTOR
        assert after == before, (
            "accepting a contractor invite changed somebody else's employment "
            f"type: {before} -> {after}"
        )

    async def test_it_does_not_reach_the_invitees_membership_of_another_org(
        self, api, session, estate,
    ):
        """The whole reason member_type is per-membership.

        This person is already permanent staff at ``other_org``. Arriving as a
        contractor here must leave that answer alone; a write that found the
        user rather than the membership would overwrite it.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)
        assert await _member_type(session, estate.org_id, estate.invitee) == CONTRACTOR
        assert await _member_type(session, estate.other_org_id, estate.invitee) == INTERNAL

    async def test_the_invitees_other_org_membership_role_is_untouched_too(
        self, api, session, estate,
    ):
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)
        role = (await session.execute(sa.text(
            "SELECT role FROM organization_members "
            "WHERE organization_id = :org AND user_id = :user"
        ), {"org": str(estate.other_org_id), "user": str(estate.invitee)})).scalar_one()
        assert role == "editor"


# ---------------------------------------------------------------------------
# ISC-41 — the arrival is audited
# ---------------------------------------------------------------------------

class TestAcceptanceIsAudited:
    async def test_acceptance_writes_a_create_row_naming_member_type(
        self, api, session, estate,
    ):
        """ISC-41, on the contents of the diff — not on a row existing.

        ``log_entity_changes(action='create')`` writes one row per tracked
        field, so "an audit row exists" is satisfied by ``role`` alone while
        the trail says nothing about employment type. That is the same shape of
        silence ISC-31 guards against on the PATCH path, and it matters more
        here: this is how a contractor *arrives*.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)

        rows = await _audit_rows(session, estate.org_id, estate.invitee)
        assert rows, (
            "accepting an invite created a membership and wrote NO audit_log "
            "row. #822 invariant 6 requires every membership mutation to be "
            "audited, and acceptance creates a membership — this is the only "
            "path by which somebody can become a contractor with no record of "
            "how. Waiting on the audit call in services/org_invite.py's "
            "accept_invite (or the accept endpoint in api/users.py)."
        )
        created = {r.field_name: r for r in rows if r.action == "create"}
        assert {"role", "member_type"} <= set(created), (
            "the created membership was audited, but the diff does not name "
            f"both role and member_type — it names {sorted(created)}. A "
            "row-counting assertion would have passed here."
        )
        assert json.loads(created["member_type"].new_value) == CONTRACTOR
        assert json.loads(created["role"].new_value) == "viewer"
        assert created["member_type"].entity_type == "org_member"

    async def test_the_audit_row_points_at_the_membership_that_was_created(
        self, api, session, estate,
    ):
        """``entity_id`` must be the new membership's id, and must not be null.

        This is the assertion the implementation is most delicately balanced
        on. ``OrganizationMember.id`` is generated client-side at INSERT, so
        the audit call is preceded by a deliberate ``await db.flush()``; take
        that away and ``member.id`` is still ``None`` when the audit row is
        built. A test that only checked the row's field names would not
        notice, and an audit row that does not say WHICH membership changed is
        not an audit trail — it is a row.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)

        member_id = (await session.execute(
            sa.select(OrganizationMember.id).where(
                (OrganizationMember.organization_id == estate.org_id)
                & (OrganizationMember.user_id == estate.invitee)
            )
        )).scalar_one()
        rows = await _audit_rows(session, estate.org_id, estate.invitee)
        assert rows, "no audit row — see test_acceptance_writes_a_create_row_naming_member_type"
        assert all(r.entity_id is not None for r in rows), (
            "an audit row with a null entity_id does not say which membership "
            "was created; the flush before the audit call is what stops this"
        )
        assert {r.entity_id for r in rows} == {member_id}
        assert {r.organization_id for r in rows} == {estate.org_id}

    async def test_the_actor_is_the_accepting_user_not_the_inviter(
        self, api, session, estate,
    ):
        """Who *took the action*, not who set it up.

        The two are different people here on purpose. Acceptance is the
        invitee's act; the inviter's part of the story lives on the invite row
        as ``invited_by_user_id``. Recording the admin would make the trail say
        the admin created a membership they did not, at a moment they were not
        involved in.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)

        rows = await _audit_rows(session, estate.org_id, estate.invitee)
        assert rows, "see test_acceptance_writes_a_create_row_naming_member_type"
        actors = {r.changed_by_user_id for r in rows}
        assert actors == {estate.invitee}, (
            f"the audit names {actors}; the accepting user is {estate.invitee} "
            f"and the inviting admin is {estate.admin}"
        )
        assert estate.admin not in actors

    async def test_action_source_and_request_id_are_null(self, api, session, estate):
        """Absent, not invented.

        ``accept_invite`` takes no ``Request``, so there is nothing to derive
        either from. Both columns are nullable and must stay empty — a
        hardcoded ``'ui'`` or ``'system'`` would be the service asserting
        something it cannot know, and an auditor reading ``action_source`` on
        other rows would have no way to tell the difference.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)

        rows = await _audit_rows(session, estate.org_id, estate.invitee)
        assert rows, "see test_acceptance_writes_a_create_row_naming_member_type"
        assert {r.action_source for r in rows} == {None}
        assert {r.request_id for r in rows} == {None}

    async def test_a_role_and_member_type_invite_produces_a_row_for_each(
        self, api, session, estate,
    ):
        """One row per tracked field, and the count is not the assertion.

        ``new_values`` is built from ``ORG_MEMBER_TRACKED_FIELDS``, so this is
        a membership-creation audit rather than a member_type bolt-on. Asserted
        as an exact set: a superset would mean an untracked field leaked into
        the trail, a subset means part of the arrival is unrecorded.
        """
        _needs_invite_member_type()
        token = await api.invite(member_type=CONTRACTOR)
        await api.accept(token)

        rows = await _audit_rows(session, estate.org_id, estate.invitee)
        created = [r for r in rows if r.action == "create"]
        assert {r.field_name for r in created} == set(ORG_MEMBER_TRACKED_FIELDS)
        assert all(r.old_value is None for r in created), (
            "a create has no previous value; a non-null old_value would mean "
            "the diff was computed against something that never existed"
        )

    async def test_an_internal_arrival_is_audited_too(self, api, session, estate):
        """Not only the interesting value.

        An audit that fired only for contractors would make the absence of a
        row meaningful, which is a far more fragile thing for an auditor to
        rely on than a row that is always there.
        """
        _needs_invite_member_type()
        token = await api.invite()
        await api.accept(token)
        rows = await _audit_rows(session, estate.org_id, estate.invitee)
        assert rows, "see test_acceptance_writes_a_create_row_naming_member_type"
        created = {r.field_name: r for r in rows if r.action == "create"}
        assert "member_type" in created
        assert json.loads(created["member_type"].new_value) == INTERNAL


# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

async def _member_type(session, org_id, user_id) -> str:
    return (await session.execute(sa.text(
        "SELECT member_type FROM organization_members "
        "WHERE organization_id = :org AND user_id = :user"
    ), {"org": str(org_id), "user": str(user_id)})).scalar_one()


async def _all_member_types(session, org_id) -> dict:
    return dict((await session.execute(
        sa.select(OrganizationMember.user_id, OrganizationMember.member_type)
        .where(OrganizationMember.organization_id == org_id)
    )).all())


async def _drop_membership(session, org_id, user_id) -> None:
    await session.execute(
        sa.delete(OrganizationMember).where(
            (OrganizationMember.organization_id == org_id)
            & (OrganizationMember.user_id == user_id)
        )
    )
    await session.commit()


async def _audit_rows(session, org_id, user_id):
    """Audit rows for this org's membership of ``user_id``, or [] if there is none.

    Looked up by entity_id so an absent row shows as an absence rather than
    being hidden by the query. Returns [] rather than raising when the
    membership itself is missing, so the assertion in the test is what fails.
    """
    member_id = (await session.execute(
        sa.select(OrganizationMember.id).where(
            (OrganizationMember.organization_id == org_id)
            & (OrganizationMember.user_id == user_id)
        )
    )).scalar_one_or_none()
    if member_id is None:
        return []
    return (await session.execute(
        sa.select(AuditLog).where(
            (AuditLog.organization_id == org_id)
            & (AuditLog.entity_type == "org_member")
            & (AuditLog.entity_id == member_id)
        )
    )).scalars().all()


@pytest.fixture
async def engine():
    eng = create_async_engine(DATABASE_URL)
    try:
        async with eng.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await eng.dispose()
        pytest.skip(f"database not reachable: {exc}")
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        try:
            yield s
        finally:
            await s.rollback()


class _Estate:
    def __init__(self, org_id, other_org_id, admin, bystander, invitee,
                 admin_email, invitee_email):
        self.org_id = org_id
        self.other_org_id = other_org_id
        self.admin = admin
        self.bystander = bystander
        self.invitee = invitee
        self.admin_email = admin_email
        self.invitee_email = invitee_email


@pytest.fixture
async def estate(session):
    """An org with an admin and a bystander, plus the invitee — already staff elsewhere.

    Every address shares one domain: ``validate_invite_domain`` refuses a
    cross-domain invite from a non-consultant, and that rule is not what is
    under test here.

    Committed, because create and accept both commit. Torn down by deleting the
    organisations, which cascades to invites, memberships and audit_log.
    """
    tag = uuid.uuid4().hex[:10]
    domain = "example.invalid"

    org = Organization(name=f"inv-{tag}", slug=f"inv-{tag}")
    other = Organization(name=f"inv-other-{tag}", slug=f"inv-other-{tag}")
    session.add_all([org, other])
    await session.flush()

    async def _user(key):
        user = User(email=f"inv-{key}-{tag}@{domain}", google_sub=f"inv-{key}-{tag}")
        session.add(user)
        await session.flush()
        return user

    admin = await _user("admin")
    bystander = await _user("bystander")
    invitee = await _user("invitee")

    session.add_all([
        OrganizationMember(organization_id=org.id, user_id=admin.id, role="admin"),
        # A contractor already on the books, so "nothing else moved" is a real
        # comparison rather than a set of identical defaults.
        OrganizationMember(organization_id=org.id, user_id=bystander.id,
                           role="editor", member_type=CONTRACTOR),
        # The invitee is permanent staff at the OTHER organisation. Arriving
        # here as a contractor must not disturb that.
        OrganizationMember(organization_id=other.id, user_id=invitee.id,
                           role="editor", member_type=INTERNAL),
    ])
    await session.commit()

    built = _Estate(org.id, other.id, admin.id, bystander.id, invitee.id,
                    admin.email, invitee.email)
    user_ids = [admin.id, bystander.id, invitee.id]

    try:
        yield built
    finally:
        # Plain UUIDs, captured before the rollback below expires every ORM
        # object in the session: `org.id` on an expired instance is a lazy
        # load, and a lazy load while BUILDING a statement is synchronous IO
        # inside an async fixture — MissingGreenlet, not a query.
        await session.rollback()
        await session.execute(
            sa.delete(Organization).where(
                Organization.id.in_([built.org_id, built.other_org_id])
            )
        )
        await session.execute(sa.delete(User).where(User.id.in_(user_ids)))
        await session.commit()


class _Api:
    def __init__(self, client, current, estate):
        self.client = client
        self._current = current
        self._estate = estate

    def as_(self, key: str) -> None:
        user_id = {"admin": self._estate.admin, "invitee": self._estate.invitee}[key]
        email = {"admin": self._estate.admin_email,
                 "invitee": self._estate.invitee_email}[key]
        self._current["user"] = auth_mod.User(
            user_id=f"stub-{key}", email=email, auth_method="google",
            db_id=str(user_id),
        )

    async def invite(self, **body) -> str:
        """Send an invite as the admin and return its token.

        ``member_type`` is omitted from the body entirely unless a test passes
        one — that omission is what ISC-39 is about, and a key sent as ``None``
        would not test it.
        """
        self.as_("admin")
        payload = {"email": self._estate.invitee_email, "role": "viewer", **body}
        response = await self.client.post(
            INVITE_PATH.format(org_id=self._estate.org_id), json=payload,
        )
        assert response.status_code == 200, response.text
        token = response.json()["invite_token"]
        assert token
        return token

    async def accept(self, token: str):
        self.as_("invitee")
        response = await self.client.post(ACCEPT_PATH.format(token=token))
        assert response.status_code == 200, response.text
        return response


@pytest.fixture
async def api(session, estate, monkeypatch):
    """The real app on the test's session, with real authorisation on the invite route.

    Two different stubs are needed because the two endpoints resolve their
    caller differently. ``POST .../invite`` guards itself with
    ``require_org_role("admin")``, whose closure looks ``require_auth`` up in
    ``auth``'s module globals at call time — so patching the module reaches it,
    and everything from ``verify_org_membership`` down stays genuine.
    ``POST /api/org-invites/{token}/accept`` takes ``Depends(require_auth)``
    directly, holding a reference to the original function object, which a
    module patch cannot reach; that one goes through
    ``dependency_overrides`` keyed on the original.

    The invitation email is stubbed out: it is not under test, and a real send
    attempt would be a network call in a database test.
    """
    current = {"user": None}
    original_require_auth = auth_mod.require_auth

    def _caller():
        user = current["user"]
        if user is None:
            raise AssertionError("the test did not choose a caller with api.as_()")
        return user

    async def _require_auth(*_a, **_k):
        return _caller()

    async def _override_require_auth():
        # No parameters, deliberately. FastAPI builds a dependency signature
        # from an override the same way it does from the real thing, so a
        # ``*args, **kwargs`` stub turns into two required query parameters
        # named `_a` and `_k` and every request 422s.
        return _caller()

    async def _no_email(*_a, **_k):
        return None

    async def _db():
        yield session

    import api.users as users_api
    monkeypatch.setattr(users_api, "send_invitation_email", _no_email)
    auth_mod.require_auth = _require_auth
    main.app.dependency_overrides[get_db] = _db
    main.app.dependency_overrides[original_require_auth] = _override_require_auth
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://invite",
            headers={"Authorization": "Bearer stub"},
        ) as client:
            yield _Api(client, current, estate)
    finally:
        auth_mod.require_auth = original_require_auth
        main.app.dependency_overrides.pop(get_db, None)
        main.app.dependency_overrides.pop(original_require_auth, None)
