"""``organization_members.member_type`` — default, who may write it, and what it is not (#822 phase 2).

``member_type`` records whether somebody is permanent staff or an external
contractor **at one organisation**. It lives on the membership, not on the
user, because the same person can be staff at one tenant and a contractor at
another; a column on ``users`` would publish whichever answer it held into
every tenant's view. That placement is asserted here (ISC-10) rather than left
to the migration's good intentions.

Four claims, each tested where it would actually be broken:

* **ISC-29 — it defaults to 'internal'.** A membership created without the
  field must come back ``'internal'``, and by the *server* default: nothing in
  this codebase infers contractor status, so every existing row and every row
  written by an older client is internal until a human says otherwise.
* **ISC-30 — only an admin may write it.** Employment type is a governance
  fact about a person. An org editor and an org viewer are both refused.
* **ISC-31 — a change is auditable in the diff, not merely in the row count.**
  ``log_entity_changes`` writes one ``audit_log`` row *per changed tracked
  field*. If ``member_type`` is missing from ``ORG_MEMBER_TRACKED_FIELDS``
  (``services/audit_service.py``) the endpoint still runs, still returns 200,
  and still writes a row whenever ``role`` changed alongside — so a test that
  counted rows would pass while the trail said nothing about the change that
  was actually made. Every assertion below is on the *content* of the diff:
  a row whose ``field_name`` is ``member_type``, carrying the old and new
  values.
* **ISC-21 (anti-criterion) — it grants nothing.** Teams and employment type
  are labels; authorisation stays on ``organization_members.role``. Asserted
  two ways: no module that defines an authorisation primitive may so much as
  mention ``member_type``, and functionally, a contractor who is an org admin
  keeps every admin right while an internal viewer gains none.

**What runs where, honestly.** The structural and anti-criterion tests run
everywhere, now. The route-level tests need the ``member_type`` query
parameter on ``PATCH /api/organizations/{org_id}/members/{user_id}``, which is
being built in a parallel workstream. They discover it from
``app.openapi()`` and **skip, not pass**, until it lands — with one guard,
``TestTheCapabilityProbeWorks``, that *fails* rather than skips if the probe
machinery stops working, so those skips cannot rot into permanent silent
green. This is not hypothetical: FastAPI 0.141 stopped flattening included
routers into ``app.routes``, and a probe written against ``app.routes`` would
find nothing for any router while reporting success.

Run with::

    docker run --rm --network cg-scf-network -v <worktree>/backend:/app \\
        -w /app ghcr.io/markac007/scf-backend:latest \\
        python -m pytest tests/test_org_member_type.py -v
"""
from __future__ import annotations

import json
import os
import pathlib
import re
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
    OrganizationMember,
    User,
)
from models import OrganizationInvite  # noqa: E402
from services.audit_service import ORG_MEMBER_TRACKED_FIELDS  # noqa: E402
from services.org_utils import MEMBER_TYPES  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parents[1]

#: The two CHECK constraints holding this vocabulary. They are coupled: an
#: invite's member_type is copied verbatim onto the membership at acceptance
#: (``services/org_invite.py``), so a value one accepts and the other rejects
#: is an invite that sends cleanly and explodes days later, in front of the
#: invitee. ISC-40 below compares them to each other.
MEMBER_TYPE_CHECKS = {
    "ck_organization_members_member_type": OrganizationMember,
    "ck_organization_invites_member_type": OrganizationInvite,
}

MEMBER_PATH = "/api/organizations/{org_id}/members/{user_id}"
MEMBERS_PATH = "/api/organizations/{org_id}/members"

DATABASE_URL = os.getenv("DATABASE_URL", "")

requires_postgres = pytest.mark.skipif(
    not DATABASE_URL.startswith("postgresql"),
    reason="needs a Postgres DATABASE_URL — SKIPPED, not passed",
)


# ---------------------------------------------------------------------------
# Capability probe: has the API workstream's half landed?
# ---------------------------------------------------------------------------

def _patch_member_query_params() -> set[str]:
    """Query parameter names on ``PATCH /api/organizations/{id}/members/{id}``.

    Read from the OpenAPI schema rather than from ``app.routes``: FastAPI
    0.141 hides included routers behind a single ``_IncludedRouter`` whose
    ``path`` is empty, so walking ``app.routes`` matches nothing for *any*
    router. The schema is public API and already carries the ``/api`` prefix.
    """
    operation = main.app.openapi()["paths"].get(MEMBER_PATH, {}).get("patch")
    if operation is None:
        return set()
    return {
        parameter["name"]
        for parameter in operation.get("parameters", [])
        if parameter.get("in") == "query"
    }


def _needs_member_type_write() -> None:
    """Skip loudly when the endpoint cannot yet be asked to write member_type."""
    params = _patch_member_query_params()
    if "member_type" not in params:
        pytest.skip(
            "PATCH /api/organizations/{org_id}/members/{user_id} does not yet "
            f"declare a 'member_type' query parameter (it declares {sorted(params)}). "
            "The API workstream is adding it on a parallel branch. This is a "
            "SKIP, not a pass — it begins executing unchanged the moment that "
            "lands, with no edit to this file."
        )


class TestTheCapabilityProbeWorks:
    """Fails — never skips — if the probe stops being able to see the route.

    Without this, a renamed path or another FastAPI introspection change would
    turn every gated test below into a permanent skip while the suite reported
    green.
    """

    def test_the_member_patch_route_is_discoverable(self):
        paths = main.app.openapi()["paths"]
        assert MEMBER_PATH in paths, (
            f"{MEMBER_PATH} is not in the OpenAPI schema; the probe below "
            "cannot tell 'not landed yet' from 'moved'"
        )
        assert "patch" in paths[MEMBER_PATH]

    def test_the_probe_sees_the_parameter_that_already_exists(self):
        # `role` has been a query parameter on this route since long before
        # #822. If the probe cannot see it, it cannot see `member_type` either
        # and every skip below is meaningless.
        assert "role" in _patch_member_query_params()


# ---------------------------------------------------------------------------
# ISC-10 — member_type is on the membership, not on the user
# ---------------------------------------------------------------------------

class TestMemberTypeBelongsToTheMembership:
    def test_users_has_no_member_type_column(self):
        assert "member_type" not in {c.name for c in User.__table__.columns}, (
            "member_type on users would give one person a single employment "
            "type across every tenant, and leak one tenant's HR fact into "
            "another's view"
        )

    def test_no_global_employment_field_crept_onto_users(self):
        # The property is "no global employment type", not merely "not this
        # spelling". A column named employment_type or contractor_flag would
        # be the same mistake wearing a different name.
        columns = {c.name for c in User.__table__.columns}
        offenders = [
            c for c in columns
            if "contractor" in c or "employment" in c or c == "member_type"
        ]
        assert offenders == [], f"users carries a global employment field: {offenders}"

    def test_organization_members_carries_it(self):
        assert "member_type" in {c.name for c in OrganizationMember.__table__.columns}

    @requires_postgres
    async def test_the_live_users_table_has_no_such_column(self, engine):
        # The model is what the ORM believes; this is what the database is.
        async with engine.connect() as connection:
            rows = await connection.execute(sa.text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'users'"
            ))
            columns = {r[0] for r in rows}
        assert columns, "read no columns for users — the query, not the schema, is wrong"
        assert "member_type" not in columns


# ---------------------------------------------------------------------------
# ISC-29 — the default is 'internal', structurally and in the database
# ---------------------------------------------------------------------------

class TestTheDefaultIsInternal:
    def test_the_column_is_not_nullable_and_defaults_server_side(self):
        column = OrganizationMember.__table__.c.member_type
        assert column.nullable is False
        assert column.server_default is not None, (
            "a Python-side default only applies to rows this ORM writes; the "
            "migration backfilled every existing row from the SERVER default, "
            "and a raw INSERT must get 'internal' too"
        )
        assert "internal" in str(column.server_default.arg)

    def test_the_vocabulary_is_exactly_these_two_values(self):
        """The one deliberate literal in this file.

        Everything else derives from ``services.org_utils.MEMBER_TYPES``, so
        this is the single place a third employment type has to be argued for
        rather than appearing by accident. It is also what stops the parity
        tests below passing on two identically-wrong constraints.
        """
        assert set(MEMBER_TYPES) == {"internal", "external_contractor"}

    def test_the_check_constraint_names_exactly_that_vocabulary(self):
        assert _model_check_values("ck_organization_members_member_type") == set(MEMBER_TYPES)

    def test_the_index_is_tenant_scoped(self):
        # member_type alone is a two-value column across every tenant's rows;
        # an index on it would not be selective enough to be used.
        index = next(
            i for i in OrganizationMember.__table__.indexes
            if i.name == "ix_organization_members_member_type"
        )
        assert [c.name for c in index.columns] == ["organization_id", "member_type"]

    @requires_postgres
    async def test_a_membership_created_without_it_reads_back_internal(self, session, estate):
        """ISC-29, functionally, through a raw INSERT that names no member_type."""
        await session.execute(sa.text(
            "INSERT INTO organization_members (id, organization_id, user_id, role) "
            "VALUES (:id, :org, :user, 'viewer')"
        ), {
            "id": str(uuid.uuid4()),
            "org": str(estate.org_id),
            "user": str(estate.users["spare"]),
        })
        value = (await session.execute(sa.text(
            "SELECT member_type FROM organization_members "
            "WHERE organization_id = :org AND user_id = :user"
        ), {"org": str(estate.org_id), "user": str(estate.users["spare"])})).scalar_one()
        assert value == "internal"

    @requires_postgres
    async def test_every_seeded_membership_is_internal(self, session, estate):
        """The ORM path too: the fixture set no member_type on any of them."""
        rows = (await session.execute(
            sa.select(OrganizationMember.member_type)
            .where(OrganizationMember.organization_id == estate.org_id)
        )).scalars().all()
        assert rows and set(rows) == {"internal"}

    @requires_postgres
    async def test_the_database_refuses_a_third_value(self, session, estate):
        with pytest.raises(Exception) as exc:
            await session.execute(sa.text(
                "INSERT INTO organization_members "
                "(id, organization_id, user_id, role, member_type) "
                "VALUES (:id, :org, :user, 'viewer', 'contractor')"
            ), {
                "id": str(uuid.uuid4()),
                "org": str(estate.org_id),
                "user": str(estate.users["spare"]),
            })
        assert "ck_organization_members_member_type" in str(exc.value)
        await session.rollback()


# ---------------------------------------------------------------------------
# ISC-40 — the two CHECK constraints must not drift apart
# ---------------------------------------------------------------------------

class TestTheTwoCheckConstraintsStayInStep:
    """``organization_invites`` and ``organization_members`` share a vocabulary.

    Not by coincidence — by dataflow. ``accept_invite`` copies the invite's
    ``member_type`` straight onto the ``OrganizationMember`` it creates, so
    every value one constraint accepts, the other must accept too.

    Widening one alone produces the worst shape of bug this issue can produce:
    the invite validates, sends and sits in the pending list looking correct;
    the failure arrives days later, at acceptance, in front of the invitee, who
    can do nothing about it, and after the admin who sent it has stopped
    watching. Narrowing one alone strands every pending invite carrying the
    removed value.

    Both assertions below compare the two constraints **to each other**, never
    to a literal typed here — a hardcoded pair would just move the drift into a
    third place that also has to be remembered.
    """

    def test_the_two_check_constraints_accept_the_same_values(self):
        members = _model_check_values("ck_organization_members_member_type")
        invites = _model_check_values("ck_organization_invites_member_type")
        assert members, "parsed no values from the membership CHECK — the parser is wrong, not the schema"
        assert members == invites, (
            "organization_members and organization_invites no longer accept the "
            f"same member_type values ({sorted(members)} vs {sorted(invites)}). "
            "accept_invite copies the invite's value straight onto the "
            "membership, so an invite carrying a value only ONE of these "
            "allows will send cleanly and then fail at acceptance — days "
            "later, in front of the invitee. Widen or narrow both in the same "
            "migration."
        )

    def test_both_columns_have_the_same_shape(self):
        # A CHECK that agrees while the columns disagree on width or nullability
        # is the same coupling failing one level down: a value that fits one
        # column and is truncated or rejected by the other.
        members = OrganizationMember.__table__.c.member_type
        invites = OrganizationInvite.__table__.c.member_type
        assert members.type.length == invites.type.length
        assert members.nullable == invites.nullable is False
        assert str(members.server_default.arg) == str(invites.server_default.arg)

    @requires_postgres
    async def test_the_live_constraints_accept_the_same_values(self, engine):
        """The models are what the ORM believes; this is what Postgres enforces.

        Read through ``pg_get_constraintdef`` so a migration that landed a
        different definition from the model's is caught here rather than at
        acceptance time.
        """
        async with engine.connect() as connection:
            rows = (await connection.execute(sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = ANY(:names)"
            ), {"names": sorted(MEMBER_TYPE_CHECKS)})).all()
        definitions = {name: definition for name, definition in rows}

        # Two constraints that do not exist would parse to two empty sets, and
        # two empty sets are equal. Prove both were actually read first.
        missing = sorted(set(MEMBER_TYPE_CHECKS) - set(definitions))
        assert not missing, f"these CHECK constraints are not in the database: {missing}"

        parsed = {name: _quoted_values(text) for name, text in definitions.items()}
        assert all(parsed.values()), (
            f"parsed no values from {[n for n, v in parsed.items() if not v]} — "
            "the parser is wrong, not the schema"
        )
        members = parsed["ck_organization_members_member_type"]
        invites = parsed["ck_organization_invites_member_type"]
        assert members == invites, (
            "the two live CHECK constraints have drifted "
            f"({sorted(members)} vs {sorted(invites)}). An invite carrying a "
            "value only one of them allows sends cleanly and fails at "
            "acceptance."
        )
        # And neither has drifted from the vocabulary the endpoints validate
        # against, which would let a request through that the database refuses.
        assert members == set(MEMBER_TYPES)


# ---------------------------------------------------------------------------
# ISC-21 (anti-criterion) — member_type is a label, never a grant
# ---------------------------------------------------------------------------

def _authorisation_modules() -> list[pathlib.Path]:
    """Every module that defines an authorisation primitive.

    Discovered rather than hard-coded, so authorisation moving into a new file
    does not quietly take it out of this test's scope.
    """
    markers = (
        "ROLE_HIERARCHY",
        "def require_org_role",
        "def verify_org_membership",
        "def require_role",
        "def has_role",
    )
    found = []
    for path in list(BACKEND.glob("*.py")) + list(BACKEND.glob("services/*.py")) \
            + list(BACKEND.glob("api/*.py")):
        try:
            source = path.read_text()
        except OSError:  # pragma: no cover - unreadable file
            continue
        if any(marker in source for marker in markers):
            found.append(path)
    return found


class TestMemberTypeIsNotAuthorisation:
    def test_the_role_hierarchy_is_untouched(self):
        assert auth_mod.ROLE_HIERARCHY == {"admin": 3, "editor": 2, "viewer": 1}

    def test_member_types_and_org_roles_share_no_names(self):
        # A member_type that collided with a role name would be one careless
        # lookup away from becoming a permission.
        assert MEMBER_TYPES.isdisjoint(auth_mod.ROLE_HIERARCHY)

    def test_the_discovery_finds_the_authorisation_module(self):
        # The guard that stops the test below passing vacuously.
        modules = {p.name for p in _authorisation_modules()}
        assert "auth.py" in modules, (
            f"authorisation-module discovery found {sorted(modules)} and not "
            "auth.py; the test below would pass by not looking anywhere"
        )

    def test_no_authorisation_module_reads_member_type(self):
        offenders = [
            str(path.relative_to(BACKEND))
            for path in _authorisation_modules()
            if "member_type" in path.read_text()
        ]
        assert offenders == [], (
            "these modules define an authorisation primitive AND mention "
            f"member_type: {offenders}. Employment type is a label; every "
            "authorisation decision stays on organization_members.role."
        )

    def test_the_membership_model_gained_no_permission_bearing_column(self):
        columns = {c.name for c in OrganizationMember.__table__.columns}
        for token in ("permission", "scope", "grant", "privilege"):
            assert not [c for c in columns if token in c], (
                f"organization_members.{token} would make employment type a grant"
            )

    @requires_postgres
    async def test_a_contractor_who_is_an_admin_keeps_every_admin_right(self, api, session, estate):
        """The functional half: authority tracks role, and only role."""
        _needs_member_type_write()
        await _set_member_type(session, estate.org_id, estate.users["admin"], "external_contractor")

        api.as_("admin")
        response = await api.patch_member(estate.users["target"], role="editor")
        assert response.status_code == 200, (
            "an org admin who is an external contractor was refused an "
            f"admin-only action: {response.status_code} {response.text}"
        )

    @requires_postgres
    async def test_an_internal_viewer_gains_nothing_from_being_internal(self, api, estate):
        _needs_member_type_write()
        api.as_("viewer")
        response = await api.patch_member(estate.users["target"], role="editor")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# ISC-30 — writing member_type is admin-only
# ---------------------------------------------------------------------------

@requires_postgres
class TestWritingMemberTypeIsAdminOnly:
    async def test_an_editor_is_refused(self, api, estate):
        _needs_member_type_write()
        api.as_("editor")
        response = await api.patch_member(
            estate.users["target"], member_type="external_contractor",
        )
        assert response.status_code == 403, (
            "an org editor must not be able to relabel somebody's employment "
            f"type: got {response.status_code} {response.text}"
        )

    async def test_a_viewer_is_refused(self, api, estate):
        _needs_member_type_write()
        api.as_("viewer")
        response = await api.patch_member(
            estate.users["target"], member_type="external_contractor",
        )
        assert response.status_code == 403

    async def test_a_refused_write_changed_nothing(self, api, session, estate):
        """403 must mean *refused*, not "refused after writing"."""
        _needs_member_type_write()
        api.as_("editor")
        await api.patch_member(estate.users["target"], member_type="external_contractor")
        assert await _read_member_type(
            session, estate.org_id, estate.users["target"],
        ) == "internal"

    async def test_an_admin_is_allowed(self, api, session, estate):
        # The mirror of the two refusals: without this, a route that refused
        # everybody would satisfy them.
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(
            estate.users["target"], member_type="external_contractor",
        )
        assert response.status_code == 200, response.text
        assert await _read_member_type(
            session, estate.org_id, estate.users["target"],
        ) == "external_contractor"


# ---------------------------------------------------------------------------
# ISC-31 — the audit diff names member_type
# ---------------------------------------------------------------------------

class TestTheAuditDiffNamesMemberType:
    def test_member_type_is_in_the_tracked_field_set(self):
        """The single line that decides whether the trail says anything.

        ``log_entity_changes`` intersects its diff with ``tracked_fields``. A
        ``member_type`` missing from this set produces no ``audit_log`` row for
        the change — while a simultaneous ``role`` change still produces one,
        so the row count looks healthy and the trail is silent about what
        actually happened.
        """
        _needs_member_type_write()
        assert "member_type" in ORG_MEMBER_TRACKED_FIELDS, (
            "services/audit_service.py:ORG_MEMBER_TRACKED_FIELDS is "
            f"{sorted(ORG_MEMBER_TRACKED_FIELDS)}. Without 'member_type', the "
            "endpoint writes an audit row whose diff never mentions the field "
            "that changed."
        )

    def test_the_endpoint_still_passes_the_tracked_set_to_the_audit_service(self):
        # If a future edit stops passing tracked_fields, the test above stops
        # meaning anything: an unfiltered diff would track member_type by
        # accident and this whole class would pass for the wrong reason.
        source = (BACKEND / "api" / "users.py").read_text()
        assert "tracked_fields=ORG_MEMBER_TRACKED_FIELDS" in source

    @requires_postgres
    async def test_a_member_type_change_writes_a_diff_row_for_member_type(
        self, api, session, estate,
    ):
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(
            estate.users["target"], member_type="external_contractor",
        )
        assert response.status_code == 200, response.text

        rows = await _audit_rows(session, estate.org_id, estate.users["target"])
        by_field = {r.field_name: r for r in rows}
        assert "member_type" in by_field, (
            "the change was accepted but no audit_log row names member_type. "
            f"Rows written: {sorted(by_field)}. This is the failure a "
            "row-counting test would have missed."
        )
        row = by_field["member_type"]
        assert row.action == "update"
        assert row.entity_type == "org_member"
        assert json.loads(row.old_value) == "internal"
        assert json.loads(row.new_value) == "external_contractor"

    @requires_postgres
    async def test_a_row_count_alone_would_not_have_caught_it(
        self, api, session, estate,
    ):
        """Changing role AND member_type together: two rows, both named.

        This is the shape that makes a count-only assertion useless. The role
        change writes its row regardless, so ``len(rows) >= 1`` passes whether
        or not member_type was ever tracked.
        """
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(
            estate.users["target"], role="editor", member_type="external_contractor",
        )
        assert response.status_code == 200, response.text

        rows = await _audit_rows(session, estate.org_id, estate.users["target"])
        fields = {r.field_name for r in rows}
        assert fields == {"role", "member_type"}, (
            f"expected a diff row for each changed field, got {sorted(fields)}"
        )

    @requires_postgres
    async def test_writing_the_same_value_writes_no_diff_row(self, api, session, estate):
        """A diff, not a log of requests: an unchanged field is not a change."""
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(estate.users["target"], member_type="internal")
        assert response.status_code == 200, response.text
        rows = await _audit_rows(session, estate.org_id, estate.users["target"])
        assert [r.field_name for r in rows if r.field_name == "member_type"] == []

    @requires_postgres
    async def test_the_audit_row_names_the_admin_who_made_the_change(self, api, session, estate):
        _needs_member_type_write()
        api.as_("admin")
        await api.patch_member(estate.users["target"], member_type="external_contractor")
        rows = await _audit_rows(session, estate.org_id, estate.users["target"])
        row = next(r for r in rows if r.field_name == "member_type")
        assert row.changed_by_user_id == estate.users["admin"]


# ---------------------------------------------------------------------------
# The request contract
# ---------------------------------------------------------------------------

@requires_postgres
class TestTheRequestContract:
    async def test_neither_parameter_is_a_400(self, api, estate):
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(estate.users["target"])
        assert response.status_code == 400, (
            "a PATCH naming nothing to change is a client error, not a "
            f"silent no-op: got {response.status_code} {response.text}"
        )

    async def test_an_unknown_member_type_is_a_400(self, api, estate):
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(estate.users["target"], member_type="contractor")
        assert response.status_code == 400, response.text

    async def test_the_check_constraint_is_never_reached_by_a_bad_request(
        self, api, session, estate,
    ):
        # A 500 from the database CHECK would also "reject" the value. It must
        # be refused in the endpoint, before the row is touched.
        _needs_member_type_write()
        api.as_("admin")
        response = await api.patch_member(estate.users["target"], member_type="CONTRACTOR")
        assert response.status_code == 400
        assert await _read_member_type(
            session, estate.org_id, estate.users["target"],
        ) == "internal"

    async def test_role_alone_still_works_for_existing_callers(self, api, session, estate):
        """Purely additive: today's callers send only ``role`` and must be unaffected."""
        api.as_("admin")
        response = await api.patch_member(estate.users["target"], role="editor")
        assert response.status_code == 200, response.text
        assert await _read_member_type(
            session, estate.org_id, estate.users["target"],
        ) == "internal"

    async def test_the_member_list_carries_member_type(self, api, estate):
        api.as_("viewer")
        response = await api.client.get(
            MEMBERS_PATH.format(org_id=estate.org_id),
        )
        assert response.status_code == 200, response.text
        items = response.json()
        assert items, "the org has members; the list returned none"
        missing = [i for i in items if "member_type" not in i]
        if missing:
            _needs_member_type_write()
            pytest.fail(
                "the PATCH endpoint accepts member_type but the member list "
                "does not return it, so a UI can set the value and never read "
                "it back"
            )
        assert {i["member_type"] for i in items} == {"internal"}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _quoted_values(sqltext: str) -> set:
    """Every single-quoted literal in a CHECK definition.

    Postgres renders the constraint as
    ``CHECK (member_type::text = ANY (ARRAY['internal'::character varying, ...]))``
    and SQLAlchemy holds the migration's own ``member_type IN (...)`` text;
    both spellings put the accepted values, and nothing else, in single quotes.
    """
    return set(re.findall(r"'([^']+)'", sqltext))


def _model_check_values(name: str) -> set:
    """The values a named CHECK constraint accepts, read from the mapped class."""
    model = MEMBER_TYPE_CHECKS[name]
    check = next(
        c for c in model.__table__.constraints
        if getattr(c, "name", None) == name
    )
    return _quoted_values(str(check.sqltext))


async def _read_member_type(session, org_id, user_id) -> str:
    return (await session.execute(sa.text(
        "SELECT member_type FROM organization_members "
        "WHERE organization_id = :org AND user_id = :user"
    ), {"org": str(org_id), "user": str(user_id)})).scalar_one()


async def _set_member_type(session, org_id, user_id, value) -> None:
    await session.execute(sa.text(
        "UPDATE organization_members SET member_type = :value "
        "WHERE organization_id = :org AND user_id = :user"
    ), {"value": value, "org": str(org_id), "user": str(user_id)})
    await session.commit()


async def _audit_rows(session, org_id, user_id):
    """Every audit row for this org's membership of ``user_id``.

    Keyed by entity_id rather than by field, so a missing row is visible as an
    absence rather than hidden by the query.
    """
    member_id = (await session.execute(
        sa.select(OrganizationMember.id).where(
            (OrganizationMember.organization_id == org_id)
            & (OrganizationMember.user_id == user_id)
        )
    )).scalar_one()
    return (await session.execute(
        sa.select(AuditLog).where(
            (AuditLog.organization_id == org_id)
            & (AuditLog.entity_type == "org_member")
            & (AuditLog.entity_id == member_id)
        )
    )).scalars().all()


@pytest.fixture
async def engine():
    if not DATABASE_URL.startswith("postgresql"):
        pytest.skip("needs a Postgres DATABASE_URL — SKIPPED, not passed")
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
    def __init__(self, org_id, users):
        self.org_id = org_id
        self.users = users


@pytest.fixture
async def estate(session):
    """One org; an admin, an editor, a viewer, a target member and a spare user.

    Committed rather than held in a rolled-back transaction, because the
    endpoint under test commits: a PATCH is a write, and a fixture that
    pretended otherwise would be asserting against a transaction the endpoint
    had already ended. Everything is tagged with a fresh uuid and removed in
    teardown — deleting the organisation cascades to its memberships and its
    audit_log rows, which is the only legitimate way to remove the latter
    (the table is append-only, #789).
    """
    tag = uuid.uuid4().hex[:10]
    org = Organization(name=f"mtype-{tag}", slug=f"mtype-{tag}")
    session.add(org)
    await session.flush()

    users = {}
    for key, role in (
        ("admin", "admin"),
        ("editor", "editor"),
        ("viewer", "viewer"),
        ("target", "viewer"),
    ):
        user = User(
            email=f"mtype-{key}-{tag}@example.invalid",
            google_sub=f"mtype-{key}-{tag}",
        )
        session.add(user)
        await session.flush()
        users[key] = user.id
        session.add(OrganizationMember(
            organization_id=org.id, user_id=user.id, role=role,
        ))

    # A user with no membership yet, so the raw-INSERT default test has
    # somebody to insert.
    spare = User(email=f"mtype-spare-{tag}@example.invalid", google_sub=f"mtype-spare-{tag}")
    session.add(spare)
    await session.flush()
    users["spare"] = spare.id

    await session.commit()
    built = _Estate(org.id, users)

    try:
        yield built
    finally:
        await session.rollback()
        await session.execute(
            sa.delete(Organization).where(Organization.id == built.org_id)
        )
        await session.execute(
            sa.delete(User).where(User.id.in_(list(built.users.values())))
        )
        await session.commit()


class _Api:
    def __init__(self, client, current, estate):
        self.client = client
        self._current = current
        self._estate = estate

    def as_(self, key: str) -> None:
        """Make subsequent requests come from the estate's ``key`` member."""
        self._current["user"] = auth_mod.User(
            user_id=f"stub-{key}",
            email=f"{key}@example.invalid",
            auth_method="google",
            db_id=str(self._estate.users[key]),
        )

    async def patch_member(self, user_id, **params):
        return await self.client.patch(
            MEMBER_PATH.format(org_id=self._estate.org_id, user_id=user_id),
            params=params,
        )


@pytest.fixture
async def api(session, estate):
    """The real app on the test's session, with real authorisation.

    Only ``require_auth`` is stubbed — everything from ``verify_org_membership``
    downwards is the genuine article reading the genuine membership rows, which
    is the only way a 403 in these tests means anything. ``require_org_role``
    resolves both names from ``auth``'s module globals at call time, so
    patching the module reaches every router.
    """
    current = {"user": None}

    async def _require_auth(*_a, **_k):
        user = current["user"]
        if user is None:
            raise AssertionError("the test did not choose a caller with api.as_()")
        return user

    async def _db():
        yield session

    original = auth_mod.require_auth
    auth_mod.require_auth = _require_auth
    main.app.dependency_overrides[get_db] = _db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=main.app),
            base_url="http://membertype",
            headers={"Authorization": "Bearer stub"},
        ) as client:
            yield _Api(client, current, estate)
    finally:
        auth_mod.require_auth = original
        main.app.dependency_overrides.pop(get_db, None)
