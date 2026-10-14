"""The approved Statement of Applicability is the Living Control Set revision (#1123, PIG R3).

The playbook asks for an approved revision of the organisation's control set.
The platform already keeps one: the SoA, whose approval is an admin act that
is recorded with the approver, and which falls back to review the moment its
text changes. The journey gate for that step is the R1 vocabulary:

    {"type": "documents_approved", "generator": "soa", "min_count": 1}

These tests pin that contract end to end through the real endpoints, so a
change to the document lifecycle cannot quietly turn the gate into "an SoA
existed once":

* a draft or in-review SoA is not a revision;
* only an admin can approve one, and the approver is recorded;
* an edit to an approved SoA re-opens the gate until it is approved again;
* a published SoA still counts.

Everything written runs inside a transaction that is rolled back.
"""
import uuid as _uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import documents as documents_api
from auth import OrgMembership
from models import (
    AuditLog,
    DocumentSection,
    DocumentTransition,
    GeneratedDocument,
    Organization,
    User,
)
from services import journey as journey_service
from services.doc_gen import lifecycle
from services.doc_gen.section_parser import flatten_sections, parse_markdown_sections

from test_journey_service import DATABASE_URL, requires_postgres

LCS_CHECK = {"type": "documents_approved", "generator": "soa", "min_count": 1}


def test_the_lcs_check_is_well_formed():
    assert journey_service.check_param_problem(LCS_CHECK) is None


def test_every_edit_path_takes_an_approved_document_back_to_review():
    # Section edits and regeneration both route through transition_on_edit;
    # a sign-off granted against different text is not a sign-off for this one.
    assert lifecycle.transition_on_edit("approved") == "in_review"
    assert lifecycle.transition_on_edit("published") == "in_review"


def test_only_an_admin_may_approve():
    with pytest.raises(lifecycle.TransitionError):
        lifecycle.validate_transition("in_review", "approved", "editor")
    lifecycle.validate_transition("in_review", "approved", "admin")


@pytest_asyncio.fixture
async def db():
    """A session on a transaction that is always rolled back."""
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.fixture
def no_commit(db, monkeypatch):
    """Endpoints commit; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)


async def _org(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"pig-{tag}", slug=f"pig-{tag}")
    db.add(org)
    await db.flush()
    return org


async def _user(db):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"pig-{tag}", email=f"pig-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    return user


def _membership(org, user, role):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id), email=user.email),
        organization_id=org.id,
        role=role,
    )


def _request():
    return Request({"type": "http", "headers": [], "method": "POST", "path": "/"})


_SOA_TEXT = "# Statement of Applicability\n\n## Scope\n\nControls in scope.\n"


async def _soa(db, org):
    doc = GeneratedDocument(
        organization_id=org.id, generator_name="soa", document_type="report",
        domain_id="", title="Statement of Applicability", filename="soa.md",
        merged_content=_SOA_TEXT, lifecycle_status="draft",
    )
    db.add(doc)
    await db.flush()
    sections = flatten_sections(parse_markdown_sections(_SOA_TEXT))
    for ordinal, section in enumerate(sections):
        db.add(DocumentSection(
            document_id=doc.id, section_id=section.section_id, heading_text=section.heading_text,
            heading_level=section.heading_level, ordinal=ordinal,
        ))
    await db.flush()
    return doc, [s.section_id for s in sections]


async def _gate(db, org):
    totals = await journey_service._in_scope_counts(db, org.id)
    return await journey_service._evaluate_one(db, org.id, LCS_CHECK, totals)


async def _move(db, org, doc, to_status, membership):
    return await documents_api.transition_document(
        org.id, doc.id, documents_api.TransitionRequest(to_status=to_status),
        _request(), membership, db,
    )


@requires_postgres
@pytest.mark.asyncio
class TestApprovedSoaIsTheRevision:

    @pytest_asyncio.fixture
    async def world(self, db, no_commit):
        org = await _org(db)
        admin = _membership(org, await _user(db), "admin")
        editor = _membership(org, await _user(db), "editor")
        doc, section_ids = await _soa(db, org)
        return SimpleNamespace(org=org, admin=admin, editor=editor, doc=doc, section_ids=section_ids)

    async def test_a_draft_or_in_review_soa_is_not_a_revision(self, db, world):
        assert (await _gate(db, world.org))["met"] is False
        await _move(db, world.org, world.doc, "in_review", world.editor)
        out = await _gate(db, world.org)
        assert out["met"] is False and out["detail"] == "0 soa approved (need 1)"

    async def test_an_editor_cannot_approve_the_revision(self, db, world):
        await _move(db, world.org, world.doc, "in_review", world.editor)
        with pytest.raises(HTTPException) as exc:
            await _move(db, world.org, world.doc, "approved", world.editor)
        assert exc.value.status_code == 403
        assert (await _gate(db, world.org))["met"] is False

    async def test_admin_approval_makes_it_the_revision_and_records_the_approver(self, db, world):
        await _move(db, world.org, world.doc, "in_review", world.editor)
        await _move(db, world.org, world.doc, "approved", world.admin)

        out = await _gate(db, world.org)
        assert out["met"] is True and out["detail"] == "1 soa approved (need 1)"

        approval = (await db.execute(select(DocumentTransition).where(
            DocumentTransition.document_id == world.doc.id,
            DocumentTransition.to_status == "approved",
        ))).scalar_one()
        assert approval.actor_user_id == _uuid.UUID(world.admin.user.db_id)
        assert approval.actor_email == world.admin.user.email

        audited = (await db.execute(select(AuditLog.old_value, AuditLog.new_value).where(
            AuditLog.organization_id == world.org.id,
            AuditLog.entity_id == world.doc.id,
            AuditLog.field_name == "lifecycle_status",
        ))).all()
        assert sorted(tuple(r) for r in audited) == [("draft", "in_review"), ("in_review", "approved")]

    async def test_editing_the_approved_revision_reopens_the_gate(self, db, world):
        await _move(db, world.org, world.doc, "in_review", world.editor)
        await _move(db, world.org, world.doc, "approved", world.admin)
        assert (await _gate(db, world.org))["met"] is True

        out = await documents_api.edit_section(
            world.org.id, world.doc.id, world.section_ids[-1],
            documents_api.SectionEditRequest(content="Controls in scope, revised."),
            _request(), world.editor, db,
        )
        assert out["lifecycle_status"] == "in_review"
        assert (await _gate(db, world.org))["met"] is False

        reopened = (await db.execute(select(DocumentTransition.trigger).where(
            DocumentTransition.document_id == world.doc.id,
            DocumentTransition.from_status == "approved",
        ))).scalars().all()
        assert reopened == ["edit"]

        # Re-approval makes the edited text the current revision.
        await _move(db, world.org, world.doc, "approved", world.admin)
        assert (await _gate(db, world.org))["met"] is True

    async def test_a_published_revision_still_counts(self, db, world):
        await _move(db, world.org, world.doc, "in_review", world.editor)
        await _move(db, world.org, world.doc, "approved", world.admin)
        await _move(db, world.org, world.doc, "published", world.admin)
        assert (await _gate(db, world.org))["met"] is True

    async def test_another_organisations_approved_soa_is_not_this_revision(self, db, world):
        await _move(db, world.org, world.doc, "in_review", world.editor)
        await _move(db, world.org, world.doc, "approved", world.admin)
        other = await _org(db)
        assert (await _gate(db, other))["met"] is False
