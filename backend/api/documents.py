"""Document generation API.

**Tenancy.** Every query filters on ``membership.organization_id`` — the value
the auth dependency resolved from the user's membership — and never on the
``org_id`` path parameter directly. The two are equal whenever the dependency
passed, which is exactly why using the wrong one is a defect that is invisible
in testing: it only diverges the day the dependency changes. Documents are
looked up by ``(id, organization_id)`` together, so a valid document id from
another tenant is a 404, not a leak.

**Authorisation.** Reads need viewer. Editing a section needs editor.
Approving, publishing, enabling the feature, and acknowledging the SCF licence
need admin. That split is the point of the lifecycle: the person who writes the
policy must not be the person who signs it off alone.
"""
import base64
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import OrgMembership, require_org_role
from celery_app import celery_app
from database import get_db
from models import (
    DocGenSettings,
    DocumentSection,
    DocumentTransition,
    DocumentVersion,
    GeneratedDocument,
    Organization,
)
from services.audit_service import create_audit_entry, detect_action_source
from services.doc_gen import lifecycle
from services.doc_gen.licence import (
    ACKNOWLEDGEMENT_TEXT,
    LICENCE_TEXT_VERSION,
    platform_kill_switch_engaged,
)
from services.doc_gen.registry import GeneratorNotFound, all_generators, get_generator
from services.doc_gen.renderer import export_markdown, render_pdf, safe_filename
from services.doc_gen.section_parser import section_body_from_markdown
from services.doc_gen.staleness import NOT_STALE, Staleness, assess_documents

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class GeneratorInfo(BaseModel):
    name: str
    display_name: str
    tier: int
    document_type: str
    is_derivative: bool
    domain_scoped: bool
    description: str


class DomainOption(BaseModel):
    """A domain the organisation can actually generate a domain-scoped document for.

    The list is derived from the same name-to-identifier mapping the generator
    itself uses (``services.doc_gen.context`` groups controls by the catalog
    domain's ``identifier``, looked up from the control's ``scf_domain`` name).
    Deriving it any other way -- from the SCF ID prefix, say -- would let the
    picker offer a domain the generator then finds empty.
    """

    identifier: str
    name: str
    control_count: int


class GenerationRequestItem(BaseModel):
    generator: str
    domain_id: Optional[str] = None


class GenerateRequest(BaseModel):
    requests: List[GenerationRequestItem] = Field(..., min_length=1, max_length=40)
    force: bool = False


class GenerateResponse(BaseModel):
    task_id: str
    queued: int


class DocumentSummary(BaseModel):
    id: str
    generator_name: str
    document_type: str
    domain_id: str
    title: str
    lifecycle_status: str
    tier: int
    is_derivative: bool
    generation_version: int
    catalog_version: Optional[str] = None
    #: Operative sections only. A ``pending_retirement`` section is a ghost the
    #: generator no longer produces, kept for a human to dispose of; counting it
    #: made a Statement of Applicability claim 71 sections when 33 of them were
    #: awaiting deletion, which is not a count of anything the document says.
    section_count: int = 0
    conflict_count: int = 0
    edited_count: int = 0
    pending_retirement_count: int = 0
    #: The organisation's inputs have moved since this document was generated.
    #: Advisory only -- nothing auto-regenerates. See
    #: :mod:`services.doc_gen.staleness` for what is compared and what is not.
    is_stale: bool = False
    stale_reason: Optional[str] = None
    updated_at: Optional[str] = None


class SectionOut(BaseModel):
    section_id: str
    heading_text: str
    heading_level: int
    ordinal: int
    status: str
    human_edited: bool
    control_ids: List[str] = []
    edited_at: Optional[str] = None


class DocumentDetail(DocumentSummary):
    merged_content: str
    sections: List[SectionOut] = []
    available_transitions: List[Dict[str, str]] = []


class SectionEditRequest(BaseModel):
    content: str


class SectionResolveRequest(BaseModel):
    """One human decision about one section's merge state.

    ``keep_mine`` / ``take_generated`` answer a conflict; ``retire`` / ``keep``
    answer a pending retirement. The pairing is enforced in the route rather
    than in the schema so a misapplied choice returns 409 (this section is not
    in that state) rather than 422 (that is not a word) -- the two mean very
    different things to a client that has just raced another editor.
    """

    choice: str = Field(..., pattern="^(keep_mine|take_generated|retire|keep)$")


class SectionResolveResponse(BaseModel):
    ok: bool
    section_id: str
    status: str
    removed: bool
    conflict_count: int
    pending_retirement_count: int
    lifecycle_status: str


class GeneratedSectionOut(BaseModel):
    """What the generator wrote for one section, in one version snapshot."""

    section_id: str
    version: int
    heading_text: Optional[str] = None
    content: Optional[str] = None
    available: bool
    current_content: str


class TransitionRequest(BaseModel):
    to_status: str
    reason: Optional[str] = None


class SettingsOut(BaseModel):
    enabled: bool
    derivative_generators_enabled: bool
    licence_acknowledged: bool
    licence_acknowledged_at: Optional[str] = None
    licence_acknowledged_by_email: Optional[str] = None
    licence_text_version: Optional[str] = None
    daily_generation_limit: int
    platform_disabled: bool
    acknowledgement_text: str


class SettingsUpdate(BaseModel):
    enabled: Optional[bool] = None
    derivative_generators_enabled: Optional[bool] = None
    acknowledge_licence: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _actor_id(membership: OrgMembership) -> Optional[UUID]:
    db_id = membership.user.db_id
    return UUID(db_id) if db_id else None


async def _load_document(
    db: AsyncSession, document_id: UUID, organization_id: UUID, *, with_sections: bool = True
) -> GeneratedDocument:
    """Fetch one document scoped to the caller's organisation.

    The organisation predicate is part of the lookup, not a check afterwards.
    A document that exists but belongs to another tenant is indistinguishable
    from one that does not exist, which is what it should be.
    """
    stmt = select(GeneratedDocument).where(
        GeneratedDocument.id == document_id,
        GeneratedDocument.organization_id == organization_id,
    )
    if with_sections:
        stmt = stmt.options(selectinload(GeneratedDocument.sections))
    document = (await db.execute(stmt)).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


async def _section_stats(db: AsyncSession, document_ids: List[UUID]) -> Dict[UUID, Dict[str, int]]:
    """Per-document section tallies, in one query rather than N.

    ``total`` counts *operative* sections. Retiring sections are counted
    separately and excluded here on purpose: they are content the generator has
    stopped producing, held only until someone disposes of them, so reporting
    them as part of "how big is this document" overstates it by however many
    ghosts a scope change left behind.
    """
    if not document_ids:
        return {}
    retiring = DocumentSection.status == "pending_retirement"
    rows = (await db.execute(
        select(
            DocumentSection.document_id,
            func.count().filter(~retiring).label("total"),
            func.count().filter(DocumentSection.status == "conflict").label("conflicts"),
            func.count().filter(DocumentSection.human_edited.is_(True)).label("edited"),
            func.count().filter(retiring).label("pending_retirement"),
        )
        .where(DocumentSection.document_id.in_(document_ids))
        .group_by(DocumentSection.document_id)
    )).all()
    return {
        r.document_id: {
            "total": r.total,
            "conflicts": r.conflicts,
            "edited": r.edited,
            "pending_retirement": r.pending_retirement,
        }
        for r in rows
    }


def _summary(
    document: GeneratedDocument,
    stats: Dict[str, int],
    staleness: Optional[Staleness] = None,
) -> DocumentSummary:
    staleness = staleness or NOT_STALE
    return DocumentSummary(
        id=str(document.id),
        generator_name=document.generator_name,
        document_type=document.document_type,
        domain_id=document.domain_id or "",
        title=document.title,
        lifecycle_status=document.lifecycle_status,
        tier=document.tier,
        is_derivative=document.is_derivative,
        generation_version=document.generation_version,
        catalog_version=document.catalog_version,
        section_count=stats.get("total", 0),
        conflict_count=stats.get("conflicts", 0),
        edited_count=stats.get("edited", 0),
        pending_retirement_count=stats.get("pending_retirement", 0),
        is_stale=staleness.is_stale,
        stale_reason=staleness.reason,
        updated_at=document.updated_at.isoformat() if document.updated_at else None,
    )


async def _get_settings(db: AsyncSession, organization_id: UUID) -> Optional[DocGenSettings]:
    return (await db.execute(
        select(DocGenSettings).where(DocGenSettings.organization_id == organization_id)
    )).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Generators + settings
# ---------------------------------------------------------------------------


@router.get("/organizations/{org_id}/documents/generators", response_model=List[GeneratorInfo])
async def list_generators(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
):
    """List available generators and their derivation classification."""
    return [
        GeneratorInfo(
            name=g.name, display_name=g.display_name, tier=g.tier,
            document_type=g.document_type, is_derivative=g.is_derivative,
            domain_scoped=g.domain_scoped, description=g.description,
        )
        for g in all_generators()
    ]


@router.get(
    "/organizations/{org_id}/documents/domains",
    response_model=List[DomainOption],
)
async def list_generatable_domains(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Domains that have at least one in-scope control.

    The generate panel needs this to offer domain-scoped generators. Without it
    the picker has nothing to show and Tier 2 generation cannot be started from
    the UI at all.
    """
    from catalog_models import SCFCatalogControl, SCFCatalogDomain
    from models import ScopedControl

    result = await db.execute(
        select(
            SCFCatalogDomain.identifier,
            SCFCatalogDomain.name,
            func.count(ScopedControl.id),
        )
        .select_from(ScopedControl)
        .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .join(SCFCatalogDomain, SCFCatalogDomain.name == SCFCatalogControl.scf_domain)
        .where(ScopedControl.organization_id == membership.organization_id)
        .where(ScopedControl.selected.is_(True))
        .group_by(SCFCatalogDomain.identifier, SCFCatalogDomain.name, SCFCatalogDomain.order)
        .order_by(SCFCatalogDomain.order)
    )
    return [
        DomainOption(identifier=identifier, name=name, control_count=count)
        for identifier, name, count in result.all()
    ]


@router.get("/organizations/{org_id}/documents/settings", response_model=SettingsOut)
async def get_settings(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    settings = await _get_settings(db, membership.organization_id)
    return SettingsOut(
        enabled=bool(settings and settings.enabled),
        derivative_generators_enabled=bool(settings and settings.derivative_generators_enabled),
        licence_acknowledged=bool(settings and settings.licence_acknowledged_at),
        licence_acknowledged_at=(
            settings.licence_acknowledged_at.isoformat()
            if settings and settings.licence_acknowledged_at else None
        ),
        licence_acknowledged_by_email=settings.licence_acknowledged_by_email if settings else None,
        licence_text_version=settings.licence_text_version if settings else None,
        daily_generation_limit=settings.daily_generation_limit if settings else 25,
        platform_disabled=platform_kill_switch_engaged(),
        acknowledgement_text=ACKNOWLEDGEMENT_TEXT,
    )


@router.put("/organizations/{org_id}/documents/settings", response_model=SettingsOut)
async def update_settings(
    org_id: UUID,
    payload: SettingsUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable document generation for the organisation.

    Enabling requires the SCF licence acknowledgement in the same call or
    already on record — the database check constraint refuses the row
    otherwise, so this is a clearer error rather than a different rule.

    Disabling never clears the acknowledgement or deletes documents. Turning
    the feature off does not un-derive work that has already been produced, and
    the record of who accepted that is the part most worth keeping.
    """
    settings = await _get_settings(db, membership.organization_id)
    if settings is None:
        settings = DocGenSettings(organization_id=membership.organization_id)
        db.add(settings)
        await db.flush()

    # Snapshot before mutating. Which switch moved is the question this audit row
    # has to answer on its own — an auditor reading it a year from now should not
    # have to find the previous row and diff two JSON blobs to learn whether this
    # was the call that turned the derivative generators on.
    before = json.dumps({
        "enabled": settings.enabled,
        "derivative_generators_enabled": settings.derivative_generators_enabled,
        "licence_acknowledged": settings.licence_acknowledged_at is not None,
    })

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if payload.acknowledge_licence and settings.licence_acknowledged_at is None:
        settings.licence_acknowledged_at = now
        settings.licence_acknowledged_by_user_id = _actor_id(membership)
        settings.licence_acknowledged_by_email = membership.user.email
        settings.licence_text_version = LICENCE_TEXT_VERSION
        settings.acknowledged_ip = request.client.host if request.client else None

    if payload.enabled is not None:
        if payload.enabled and settings.licence_acknowledged_at is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "The SCF licence acknowledgement is required before document "
                    "generation can be enabled."
                ),
            )
        if payload.enabled and not settings.enabled:
            settings.enabled_at = now
            settings.enabled_by_user_id = _actor_id(membership)
        if not payload.enabled and settings.enabled:
            settings.disabled_at = now
        settings.enabled = payload.enabled

    if payload.derivative_generators_enabled is not None:
        if payload.derivative_generators_enabled and not settings.enabled:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Enable document generation before enabling AI-augmented "
                    "generators."
                ),
            )
        settings.derivative_generators_enabled = payload.derivative_generators_enabled

    await create_audit_entry(
        db,
        organization_id=membership.organization_id,
        entity_type="doc_gen_settings",
        entity_id=settings.id,
        action="update",
        changed_by_user_id=_actor_id(membership),
        field_name="settings",
        old_value=before,
        new_value=json.dumps({
            "enabled": settings.enabled,
            "derivative_generators_enabled": settings.derivative_generators_enabled,
            "licence_acknowledged": settings.licence_acknowledged_at is not None,
            "licence_text_version": settings.licence_text_version,
        }),
        action_source=detect_action_source(request),
    )
    await db.commit()
    await db.refresh(settings)

    return await get_settings(org_id, membership, db)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


@router.post("/organizations/{org_id}/documents/generate", response_model=GenerateResponse)
async def generate(
    org_id: UUID,
    payload: GenerateRequest,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Queue a generation run.

    The licence gate is re-checked inside the task as well. Settings can change
    between enqueue and execution, and a job must not outlive the permission
    that created it — this check exists so the user gets a 403 now rather than
    a failed job later.
    """
    from services.doc_gen.licence import check_generation_allowed

    settings = await _get_settings(db, membership.organization_id)

    for item in payload.requests:
        try:
            spec = get_generator(item.generator)
        except GeneratorNotFound as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if spec.domain_scoped and not item.domain_id:
            raise HTTPException(
                status_code=400,
                detail=f"Generator '{spec.name}' requires a domain.",
            )
        permission = check_generation_allowed(
            settings, tier=spec.tier, is_derivative=spec.is_derivative
        )
        if not permission.allowed:
            raise HTTPException(status_code=403, detail=permission.reason)

    task = celery_app.send_task(
        "doc_gen.generate",
        kwargs={
            "organization_id": str(membership.organization_id),
            "requests": [
                {"generator": i.generator, "domain_id": i.domain_id}
                for i in payload.requests
            ],
            "user_id": str(_actor_id(membership)) if _actor_id(membership) else None,
            "user_email": membership.user.email,
            "force": payload.force,
        },
    )
    return GenerateResponse(task_id=task.id, queued=len(payload.requests))


@router.get("/organizations/{org_id}/documents/generation-status")
async def generation_status(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
):
    """Poll the Redis status key for the organisation's current run."""
    from tasks_doc_gen import get_status

    return get_status(str(membership.organization_id)) or {"status": "idle"}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


@router.get("/organizations/{org_id}/documents", response_model=List[DocumentSummary])
async def list_documents(
    org_id: UUID,
    status: Optional[str] = Query(None, description="Filter by lifecycle status"),
    document_type: Optional[str] = Query(None),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(GeneratedDocument).where(
        GeneratedDocument.organization_id == membership.organization_id
    )
    if status:
        stmt = stmt.where(GeneratedDocument.lifecycle_status == status)
    if document_type:
        stmt = stmt.where(GeneratedDocument.document_type == document_type)
    documents = (await db.execute(
        stmt.order_by(GeneratedDocument.document_type, GeneratedDocument.title)
    )).scalars().all()

    stats = await _section_stats(db, [d.id for d in documents])
    # One read of the organisation's current inputs serves the whole page --
    # see :mod:`services.doc_gen.staleness` for why that is affordable here and
    # why building a generation context per document would not be.
    stale = await assess_documents(db, membership.organization_id, documents)
    return [
        _summary(d, stats.get(d.id, {}), stale.get(d.id))
        for d in documents
    ]


@router.get("/organizations/{org_id}/documents/{document_id}", response_model=DocumentDetail)
async def get_document(
    org_id: UUID,
    document_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    document = await _load_document(db, document_id, membership.organization_id)
    stats = (await _section_stats(db, [document.id])).get(document.id, {})
    stale = (await assess_documents(db, membership.organization_id, [document])).get(
        document.id
    )
    detail = DocumentDetail(
        **_summary(document, stats, stale).model_dump(),
        merged_content=_operative_markdown(document),
        sections=[
            SectionOut(
                section_id=s.section_id, heading_text=s.heading_text,
                heading_level=s.heading_level, ordinal=s.ordinal, status=s.status,
                human_edited=s.human_edited, control_ids=s.control_ids or [],
                edited_at=s.edited_at.isoformat() if s.edited_at else None,
            )
            for s in sorted(document.sections, key=lambda s: s.ordinal)
        ],
        # available_transitions() already returns UI-shaped dicts (to_status /
        # label / required_role). Re-wrapping them treated each dict as a
        # status string, so every document with a legal next state raised
        # "unhashable type: 'dict'" on the label lookup.
        available_transitions=lifecycle.available_transitions(
            document.lifecycle_status, membership.role
        ),
    )
    return detail


@router.put("/organizations/{org_id}/documents/{document_id}/sections/{section_id:path}")
async def edit_section(
    org_id: UUID,
    document_id: UUID,
    section_id: str,
    payload: SectionEditRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Save a human edit to one section.

    The edit is written to the human layer and the merged document is rebuilt
    around it. The generated layer is untouched — that is what allows the next
    regeneration to tell an edit from a divergence rather than simply losing it.

    **Editing does not decide a pending retirement.** Every other prior status
    becomes ``human_preserved``: saving your own text over a conflict *is* a
    "keep mine", and there is nothing left to review. ``pending_retirement`` is
    different — it is not a merge outcome awaiting confirmation, it is an open
    question about whether this clause should exist at all, and rewriting the
    clause before answering that question is a normal thing to do. Overwriting
    the status here silently answered it: a typo fix on an *unrelated* section
    took one policy from fifteen pending retirements to fourteen, because the
    rebuild rewrote the row it touched and the retirement went with it. The
    only way out of ``pending_retirement`` is the resolve endpoint, where the
    human says ``retire`` or ``keep`` deliberately.
    """
    from services.doc_gen.fingerprint import sha256

    document = await _load_document(db, document_id, membership.organization_id)
    section = next((s for s in document.sections if s.section_id == section_id), None)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_content = payload.content.rstrip()

    section.edited_content = new_content
    section.human_edited = True
    section.content_hash = sha256(new_content.strip())
    if section.status != "pending_retirement":
        section.status = "human_preserved"
    section.edited_by_user_id = _actor_id(membership)
    section.edited_at = now

    # Rebuild the operative document from the stored layers so what the reader
    # sees and what the sections table records can never disagree.
    document.merged_content = _rebuild_merged(document)
    document.updated_at = now

    next_status = lifecycle.transition_on_edit(document.lifecycle_status)
    if next_status:
        db.add(DocumentTransition(
            document_id=document.id,
            from_status=document.lifecycle_status,
            to_status=next_status,
            actor_user_id=_actor_id(membership),
            actor_email=membership.user.email,
            trigger="edit",
            reason="Document edited after approval",
        ))
        document.lifecycle_status = next_status

    await create_audit_entry(
        db,
        organization_id=membership.organization_id,
        entity_type="generated_document",
        entity_id=document.id,
        action="update",
        changed_by_user_id=_actor_id(membership),
        field_name=f"section:{section_id}",
        # ``field_name`` is a bounded column and is clamped for long section
        # ids; the exact identity is carried here, where the column is Text.
        new_value=json.dumps({"section_id": section_id, "status": section.status}),
        action_source=detect_action_source(request),
    )
    await db.commit()
    return {"ok": True, "lifecycle_status": document.lifecycle_status}


def _operative_markdown(document: GeneratedDocument) -> str:
    """The document's text as a reader should see it, right now.

    The generator writes the Document Control table once, at generation, and it
    has no way of knowing what happens to the document afterwards -- so a policy
    that has since been reviewed, approved and published still introduced itself
    as a draft. The platform record and the document's own front matter
    disagreed, and the front matter is the half an auditor reads.

    Applying the live status here rather than writing it into
    ``merged_content`` on transition is deliberate (see
    :func:`services.doc_gen.lifecycle.apply_lifecycle_status`): storing it would
    move the Document Control section's hashes, which is how a section starts
    reporting ``updated`` on every regeneration forever, and would have to
    reconcile against a human edit of that same section. Nothing is stored, so
    nothing can drift.

    Every path that hands document text to a person goes through here -- detail,
    preview, and all three exports -- so they cannot disagree with each other
    either.
    """
    return lifecycle.apply_lifecycle_status(
        document.merged_content, document.lifecycle_status
    )


def _rebuild_merged(
    document: GeneratedDocument,
    overrides: Optional[Dict[str, str]] = None,
) -> str:
    """Reassemble the operative document from its stored layers.

    The current merged content supplies the skeleton — headings, ordering,
    preamble — and every section carrying an edit contributes its body. Bodies
    only: a human editing a section must not be able to renumber the document
    out from under the next regeneration, because section identity is derived
    from headings.

    ``overrides`` substitutes a body for a section that carries no human edit.
    Exactly one caller needs it: ``take_generated`` clears the human layer and
    then has to put the generated text into the operative document in the same
    breath. Without it that section would keep whatever the merge had left
    there — the rejected human text — while its row claimed the generated
    content had been accepted.
    """
    from services.doc_gen.section_parser import (
        flatten_sections,
        pair_sections_to_headings,
        parse_markdown_sections,
    )
    from services.doc_gen.three_layer import build_merged_document

    human_edits = {
        s.section_id: s.edited_content
        for s in document.sections
        if s.human_edited and s.edited_content is not None
    }
    if overrides:
        human_edits.update(overrides)

    # ``build_merged_document`` keys the edits it applies off the ids it
    # re-derives from the markdown it is given, which is correct where it is
    # called from -- the generation pipeline hands it a *fresh* generation,
    # whose ids are by definition the ones the parser derives. Here the
    # markdown is the operative document, and one class of section reads back
    # under a different id there: a retiree, re-rendered at the end at its
    # original depth. Passing stored ids straight through would silently drop
    # that section's edit from the rebuilt document -- the save would report
    # success and the text would be unchanged.
    #
    # So translate the keys into the ids this markdown parses to, and leave the
    # merge engine alone; its own callers depend on its current behaviour.
    parsed = flatten_sections(parse_markdown_sections(document.merged_content))
    pairing = pair_sections_to_headings(
        document.merged_content, document.sections, parsed=parsed
    )
    translated = {}
    for section_id, content in human_edits.items():
        index = pairing.heading_index.get(section_id)
        if index is None:
            # No heading in the operative document belongs to this row, so
            # there is nowhere to put its body. Passing the stored id through
            # would be worse than skipping: if some *other* row's heading
            # happens to parse to the same id, the merge engine would write
            # this section's text into that one.
            logger.warning(
                "doc_gen: section %s of document %s has no heading in the "
                "operative document; its edit was not applied",
                section_id,
                document.id,
            )
            continue
        translated[parsed[index].section_id] = content

    return build_merged_document(document.merged_content, translated)


def _section_position(document: GeneratedDocument, section: DocumentSection) -> int:
    """Where this section's heading sits among the operative document's headings.

    Sections are addressed by position rather than by id whenever the *document
    text* has to be edited, for the reason the merge engine documents at
    length: a retiring section is re-rendered at the end of the document at its
    original depth, so re-parsing renames it after whichever heading now
    precedes it. Its stored id is real; the id a fresh parse derives for it is
    not.

    Neither ``ordinal`` nor a re-derived id can answer this on its own.
    ``ordinal`` is the render position, but only while the row sequence and the
    heading sequence agree exactly, and a human edit containing a markdown
    heading line is enough to break that for every row after it. A re-derived
    id is right for every section *except* a retiree, which is the one case
    that matters here. :func:`pair_sections_to_headings` resolves both by
    matching rows to headings by identity, consuming each heading once.

    ``-1`` means "cannot be located", and callers must leave the markdown
    alone rather than edit a guessed span.
    """
    from services.doc_gen.section_parser import pair_sections_to_headings

    pairing = pair_sections_to_headings(document.merged_content, document.sections)
    return pairing.heading_index.get(section.section_id, -1)


async def _version_markdown(version: DocumentVersion) -> Optional[str]:
    """The whole-document markdown a version snapshot holds.

    ``DocumentVersion.content`` is nullable: content over roughly 64KB is
    written to ``storage_service`` and the row keeps ``blob_key`` instead. A
    reader that only looks at ``content`` therefore works perfectly until the
    first genuinely large policy, then silently reports every one of its
    sections as "not in this version" — which is the same answer a retired
    section gives, so the failure would masquerade as normal behaviour rather
    than announce itself.

    The blob read is synchronous and goes over the network, so it runs off the
    event loop.
    """
    if version.content is not None:
        return version.content
    if not version.blob_key:
        return None

    import asyncio

    from services import storage_service

    def _read() -> Optional[str]:
        chunks = storage_service.download_blob_stream(version.blob_key)
        if chunks is None:
            return None
        return b"".join(chunks).decode("utf-8")

    try:
        return await asyncio.to_thread(_read)
    except Exception as exc:
        # A configured-but-unreachable blob store is an infrastructure fault,
        # not an absent section. Say so rather than rendering an empty pane.
        logger.exception("doc_gen: could not read version blob %s", version.blob_key)
        raise HTTPException(
            status_code=503,
            detail="The stored generated content for this version could not be read.",
        ) from exc


def _operative_body(document: GeneratedDocument, section: DocumentSection) -> str:
    """This section's body as it stands in the operative document, markers out.

    Returned alongside the generated alternative so the client can diff the two
    without re-slicing the merged markdown itself — and, more to the point,
    without having to reimplement the positional identity rule that
    :func:`_section_position` exists to encapsulate.
    """
    from services.doc_gen.section_parser import (
        flatten_sections,
        parse_markdown_sections,
    )
    from services.doc_gen.three_layer import strip_markers

    parsed = flatten_sections(parse_markdown_sections(document.merged_content))
    position = _section_position(document, section)
    if 0 <= position < len(parsed):
        return strip_markers(parsed[position].content)
    return strip_markers(section.edited_content or "")


@router.get(
    "/organizations/{org_id}/documents/{document_id}/sections/{section_id:path}/generated",
    response_model=GeneratedSectionOut,
)
async def get_generated_section(
    org_id: UUID,
    document_id: UUID,
    section_id: str,
    version: Optional[int] = Query(
        None, description="Version number; omitted means the latest."
    ),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """What the generator wrote for one section, sliced out of a version snapshot.

    ``document_versions`` stores the pure generated layer as one markdown blob
    per run. Until now the only thing the UI could show from it was a row in a
    list — number, model, date — so "your edit was kept, the generated
    alternative is in version history" was true and useless: there was no way
    to look at the alternative before choosing between them. Resolving a
    conflict was a decision made blind.

    ``available: false`` (with ``content: null``) is a real answer, not an
    error. A ``pending_retirement`` section is *defined* by its absence from
    the newest generation, so asking for its generated text and being told
    there is none is exactly the information the reader needs — and the reason
    this returns a flag rather than a 404 on the section body.
    """
    document = await _load_document(db, document_id, membership.organization_id)
    section = next((s for s in document.sections if s.section_id == section_id), None)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")

    stmt = select(DocumentVersion).where(DocumentVersion.document_id == document.id)
    if version is not None:
        stmt = stmt.where(DocumentVersion.version == version)
    snapshot = (await db.execute(
        stmt.order_by(DocumentVersion.version.desc()).limit(1)
    )).scalar_one_or_none()
    if snapshot is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Version {version} not found for this document."
                if version is not None
                else "This document has no generated versions."
            ),
        )

    markdown = await _version_markdown(snapshot)
    generated = (
        section_body_from_markdown(markdown, section_id) if markdown else None
    )

    return GeneratedSectionOut(
        section_id=section_id,
        version=snapshot.version,
        heading_text=generated.heading_text if generated else None,
        content=generated.content if generated else None,
        available=generated is not None,
        current_content=_operative_body(document, section),
    )


@router.post(
    "/organizations/{org_id}/documents/{document_id}/sections/{section_id:path}/resolve",
    response_model=SectionResolveResponse,
)
async def resolve_document_section(
    org_id: UUID,
    document_id: UUID,
    section_id: str,
    payload: SectionResolveRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Settle one section's merge state.

    The three-layer engine has always known how to resolve a section
    (``three_layer.resolve_section``) and has been unit-tested doing it since
    the feature shipped. Nothing called it. A conflict raised by a
    regeneration therefore stayed a conflict for ever, and a section the
    generator wanted to retire could only be disposed of by editing the
    markdown by hand — which the editor's own save path then re-marked. This
    endpoint is the door.

    Four choices, paired to the two states that can be open:

    ``keep_mine`` / ``take_generated``
        Only on a ``conflict``. Both are delegated to
        :func:`~services.doc_gen.three_layer.resolve_section`, whose semantics
        are not changed here. ``take_generated`` additionally clears the human
        layer and writes the generated body into the operative document, so
        the row and the text cannot disagree about which side won.

    ``retire``
        Only on a ``pending_retirement``. **This is the one destructive action
        in the feature**: the section is excised from ``merged_content`` and
        its row is deleted. It is still recoverable — every
        ``document_versions`` snapshot that contained the section is immutable
        and untouched, so the text can be read back out of history — but it
        will not come back on its own, and the next regeneration will not
        recreate it.

    ``keep``
        Only on a ``pending_retirement``. Cancels the retirement: the section
        goes back to ``human_preserved`` if it carries an edit and
        ``unchanged`` otherwise, and the PENDING RETIREMENT comment is stripped
        from that section alone. The next regeneration will propose the
        retirement again if the controls are still out of scope — keeping a
        section is a decision about this generation, not a permanent pin.

    A choice applied to the wrong state returns **409**, not 404 or 422: the
    section exists and the word is valid, but two editors looking at the same
    review queue will race, and "someone already resolved this" has to be
    distinguishable from "there is no such section".
    """
    from services.doc_gen.section_parser import excise_section_block
    from services.doc_gen.three_layer import (
        STATUS_CONFLICT,
        STATUS_HUMAN_PRESERVED,
        STATUS_PENDING_RETIREMENT,
        STATUS_UNCHANGED,
        resolve_section,
        strip_markers_in_section,
    )

    document = await _load_document(db, document_id, membership.organization_id)
    section = next((s for s in document.sections if s.section_id == section_id), None)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not found")

    choice = payload.choice
    prior_status = section.status
    required = (
        STATUS_CONFLICT
        if choice in ("keep_mine", "take_generated")
        else STATUS_PENDING_RETIREMENT
    )
    if prior_status != required:
        raise HTTPException(
            status_code=409,
            detail=(
                f"'{choice}' applies to a section in '{required}'; this section "
                f"is '{prior_status}'."
            ),
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    removed = False

    if choice in ("keep_mine", "take_generated"):
        generated_body: Optional[str] = None
        if choice == "take_generated":
            snapshot = (await db.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.version.desc())
                .limit(1)
            )).scalar_one_or_none()
            markdown = await _version_markdown(snapshot) if snapshot else None
            parsed = (
                section_body_from_markdown(markdown, section_id) if markdown else None
            )
            if parsed is None:
                # The generated alternative is what this choice selects. Without
                # it there is nothing to take, and silently keeping the human
                # text while reporting "updated" would be a lie about which
                # side won.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "The generated alternative for this section is not in the "
                        "latest version snapshot, so it cannot be taken."
                    ),
                )
            generated_body = parsed.content

        row = {
            "status": section.status,
            "human_edited": section.human_edited,
            "edited_content": section.edited_content,
            "content_hash": section.content_hash,
            "last_generated_hash": section.last_generated_hash,
        }
        row = resolve_section(row, choice, generated_body)

        section.status = row["status"]
        section.human_edited = bool(row["human_edited"])
        section.edited_content = row["edited_content"]
        section.content_hash = row["content_hash"]
        section.edited_by_user_id = _actor_id(membership)
        section.edited_at = now

        document.merged_content = _rebuild_merged(
            document,
            overrides={section_id: generated_body} if generated_body is not None else None,
        )

    elif choice == "keep":
        position = _section_position(document, section)
        if position >= 0:
            document.merged_content = strip_markers_in_section(
                document.merged_content, position
            )
        section.status = (
            STATUS_HUMAN_PRESERVED if section.human_edited else STATUS_UNCHANGED
        )
        section.edited_by_user_id = _actor_id(membership)
        section.edited_at = now

    else:  # retire
        position = _section_position(document, section)
        # ``-1`` means the heading could not be located, and
        # :func:`_section_position` is explicit that callers must then leave the
        # markdown alone. Deleting the row anyway is worse than editing a
        # guessed span: the prose stays in ``merged_content`` with nothing
        # backing it, the reader re-parses it as an ordinary ``unchanged``
        # section with no decision controls, and the only handle on it is gone
        # -- so the section can never be retired, while the call reports
        # success. Refuse instead, leaving both layers intact and recoverable.
        if position < 0:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This section's heading could not be located in the "
                    "document, so retiring it would leave its text behind with "
                    "nothing to remove it later. Regenerate the document and "
                    "try again."
                ),
            )
        document.merged_content = excise_section_block(
            document.merged_content, position
        )
        document.sections.remove(section)
        await db.delete(section)
        removed = True
        # Ordinals are the document's render order and the fallback identity
        # rule keys on them, so a gap left by a deletion is not cosmetic.
        for ordinal, remaining in enumerate(
            sorted(document.sections, key=lambda s: s.ordinal or 0)
        ):
            remaining.ordinal = ordinal

    document.updated_at = now

    next_status = lifecycle.transition_on_edit(document.lifecycle_status)
    if next_status:
        db.add(DocumentTransition(
            document_id=document.id,
            from_status=document.lifecycle_status,
            to_status=next_status,
            actor_user_id=_actor_id(membership),
            actor_email=membership.user.email,
            trigger="edit",
            reason=f"Section resolved ({choice}) after approval",
        ))
        document.lifecycle_status = next_status

    final_status = "removed" if removed else section.status
    await create_audit_entry(
        db,
        organization_id=membership.organization_id,
        entity_type="generated_document",
        entity_id=document.id,
        action="update",
        changed_by_user_id=_actor_id(membership),
        field_name=f"section:{section_id}:resolve",
        # As above: the clamped ``field_name`` stays readable, but the exact
        # section id must survive verbatim, so it rides in the Text columns.
        old_value=json.dumps({"section_id": section_id, "status": prior_status}),
        new_value=json.dumps(
            {"section_id": section_id, "status": final_status, "choice": choice}
        ),
        action_source=detect_action_source(request),
    )
    await db.commit()

    remaining_sections = list(document.sections)
    return SectionResolveResponse(
        ok=True,
        section_id=section_id,
        status=final_status,
        removed=removed,
        conflict_count=sum(
            1 for s in remaining_sections if s.status == STATUS_CONFLICT
        ),
        pending_retirement_count=sum(
            1 for s in remaining_sections if s.status == STATUS_PENDING_RETIREMENT
        ),
        lifecycle_status=document.lifecycle_status,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


@router.post("/organizations/{org_id}/documents/{document_id}/transition")
async def transition_document(
    org_id: UUID,
    document_id: UUID,
    payload: TransitionRequest,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Move a document through its lifecycle.

    The role requirement is per transition, not per endpoint: submitting for
    review needs editor, approving and publishing need admin. Enforcing it here
    rather than in the dependency is what lets one endpoint serve the whole
    state machine while still refusing an editor's attempt to approve their own
    document.
    """
    document = await _load_document(db, document_id, membership.organization_id,
                                    with_sections=False)
    try:
        lifecycle.validate_transition(
            document.lifecycle_status, payload.to_status, membership.role
        )
    except lifecycle.TransitionError as exc:
        # 403 when the transition is legal but the role is insufficient,
        # 409 when the transition itself is not permitted from this state.
        status_code = 403 if lifecycle.can_transition(
            document.lifecycle_status, payload.to_status
        ) else 409
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    from_status = document.lifecycle_status
    document.lifecycle_status = payload.to_status
    document.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    db.add(DocumentTransition(
        document_id=document.id,
        from_status=from_status,
        to_status=payload.to_status,
        actor_user_id=_actor_id(membership),
        actor_email=membership.user.email,
        trigger="manual",
        reason=payload.reason,
    ))
    await create_audit_entry(
        db,
        organization_id=membership.organization_id,
        entity_type="generated_document",
        entity_id=document.id,
        action="update",
        changed_by_user_id=_actor_id(membership),
        field_name="lifecycle_status",
        old_value=from_status,
        new_value=payload.to_status,
        action_source=detect_action_source(request),
    )
    await db.commit()
    return {
        "ok": True,
        "from_status": from_status,
        "to_status": payload.to_status,
        "available_transitions": lifecycle.available_transitions(
            payload.to_status, membership.role
        ),
    }


@router.get("/organizations/{org_id}/documents/{document_id}/history")
async def document_history(
    org_id: UUID,
    document_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The append-only transition log, plus the generated-version index."""
    # Imported here, as every other ``three_layer`` use in this module is: the
    # merge layer pulls in the parser and the renderer, and this module is
    # imported at app start.
    from services.doc_gen.three_layer import describe_version_summary

    document = await _load_document(db, document_id, membership.organization_id,
                                    with_sections=False)
    transitions = (await db.execute(
        select(DocumentTransition)
        .where(DocumentTransition.document_id == document.id)
        .order_by(DocumentTransition.created_at)
    )).scalars().all()
    versions = (await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version)
    )).scalars().all()
    return {
        "transitions": [
            {
                "from_status": t.from_status, "to_status": t.to_status,
                "actor_email": t.actor_email, "reason": t.reason,
                "trigger": t.trigger,
                "created_at": t.created_at.isoformat() if t.created_at else None,
            }
            for t in transitions
        ],
        "versions": [
            {
                "version": v.version, "model_id": v.model_id,
                "generator_version": v.generator_version,
                "input_fingerprint": v.input_fingerprint,
                # Which snapshot the operative document was merged from. The
                # list is otherwise a column of indistinguishable numbers, and
                # a reader comparing their text against "the generated version"
                # has no way to tell which row that is.
                "is_current": v.version == document.generation_version,
                # What the generation actually did. NULL on every row written
                # before the column existed, and the empty string it renders to
                # means "not recorded" -- the panel must not read that as
                # "nothing changed".
                "change_summary": v.change_summary,
                "change_description": describe_version_summary(v.change_summary),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in versions
        ],
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


@router.get("/organizations/{org_id}/documents/{document_id}/export")
async def export_document(
    org_id: UUID,
    document_id: UUID,
    format: str = Query("md", pattern="^(md|pdf|html)$"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Export a document as Markdown, HTML, or PDF.

    Merge markers are stripped in every format. They are review scaffolding;
    an exported document is the thing an auditor reads.
    """
    document = await _load_document(db, document_id, membership.organization_id,
                                    with_sections=False)

    if format == "md":
        body = export_markdown(_operative_markdown(document), title=document.title)
        return Response(
            content=body,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition":
                    f'attachment; filename="{safe_filename(document.title, "md", document.domain_id)}"'
            },
        )

    if format == "html":
        from services.doc_gen.renderer import markdown_to_html
        return Response(
            content=markdown_to_html(_operative_markdown(document), title=document.title),
            media_type="text/html; charset=utf-8",
        )

    # The masthead and footer name the organisation the document belongs to.
    # This used to be derived from the exporting user's email domain, which is a
    # property of the person clicking Export, not of the organisation — two
    # members with different email domains got different footers on the same
    # document. Read the org record.
    org_row = await db.execute(
        select(
            Organization.name,
            Organization.logo_data,
            Organization.logo_content_type,
        ).where(Organization.id == membership.organization_id)
    )
    org = org_row.one_or_none()
    org_name = (org.name if org else "") or ""

    # Inlined rather than linked: WeasyPrint would otherwise have to fetch the
    # logo endpoint over the network and authenticate to our own API to do it.
    logo_data_uri = ""
    if org and org.logo_data and org.logo_content_type:
        logo_data_uri = (
            f"data:{org.logo_content_type};base64,"
            f"{base64.b64encode(org.logo_data).decode('ascii')}"
        )

    # What kind of document this is and where it stands. The Document Control
    # table inside the prose is written by the generator and can drift; this
    # line is read straight off the platform record.
    try:
        type_label = get_generator(document.generator_name).display_name
    except GeneratorNotFound:
        type_label = (document.document_type or "Document").replace("_", " ").title()
    subtitle = " · ".join(
        part
        for part in (
            type_label,
            f"Version {document.generation_version}" if document.generation_version else "",
            (document.lifecycle_status or "").replace("_", " ").title(),
        )
        if part
    )

    try:
        pdf = render_pdf(
            _operative_markdown(document),
            title=document.title,
            organisation=org_name,
            subtitle=subtitle,
            domain_id=document.domain_id or "",
            logo_data_uri=logo_data_uri,
        )
    except RuntimeError as exc:
        # PDF rendering needs native libraries. If they are missing, say so —
        # a 500 with a stack trace about a shared object helps nobody.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition":
                f'attachment; filename="{safe_filename(document.title, "pdf", document.domain_id)}"'
        },
    )


@router.get("/organizations/{org_id}/documents/{document_id}/preview")
async def preview_document(
    org_id: UUID,
    document_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Rendered HTML for the in-app reader.

    Returns a *fragment*, not a page. The previous version returned a full
    ``<!DOCTYPE html>`` document with the merge markers left in as HTML
    comments -- unusable inside the React tree, and invisible to a reader even
    if it had been used. The fragment wraps each section with its stored id and
    merge status so the reader can show the three-layer merge in the document
    flow rather than only in the editor's outline.
    """
    from services.doc_gen.renderer import markdown_to_reader_fragment

    document = await _load_document(db, document_id, membership.organization_id,
                                    with_sections=True)
    return {
        "html": markdown_to_reader_fragment(
            _operative_markdown(document), document.sections
        ),
        "title": document.title,
        "lifecycle_status": document.lifecycle_status,
    }
