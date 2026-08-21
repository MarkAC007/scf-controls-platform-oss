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
    section_count: int = 0
    conflict_count: int = 0
    edited_count: int = 0
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
    """Per-document section tallies, in one query rather than N."""
    if not document_ids:
        return {}
    rows = (await db.execute(
        select(
            DocumentSection.document_id,
            func.count().label("total"),
            func.count().filter(DocumentSection.status == "conflict").label("conflicts"),
            func.count().filter(DocumentSection.human_edited.is_(True)).label("edited"),
        )
        .where(DocumentSection.document_id.in_(document_ids))
        .group_by(DocumentSection.document_id)
    )).all()
    return {
        r.document_id: {"total": r.total, "conflicts": r.conflicts, "edited": r.edited}
        for r in rows
    }


def _summary(document: GeneratedDocument, stats: Dict[str, int]) -> DocumentSummary:
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
    return [_summary(d, stats.get(d.id, {})) for d in documents]


@router.get("/organizations/{org_id}/documents/{document_id}", response_model=DocumentDetail)
async def get_document(
    org_id: UUID,
    document_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    document = await _load_document(db, document_id, membership.organization_id)
    stats = (await _section_stats(db, [document.id])).get(document.id, {})
    detail = DocumentDetail(
        **_summary(document, stats).model_dump(),
        merged_content=document.merged_content,
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
        action_source=detect_action_source(request),
    )
    await db.commit()
    return {"ok": True, "lifecycle_status": document.lifecycle_status}


def _rebuild_merged(document: GeneratedDocument) -> str:
    """Reassemble the operative document from its stored layers.

    The current merged content supplies the skeleton — headings, ordering,
    preamble — and every section carrying an edit contributes its body. Bodies
    only: a human editing a section must not be able to renumber the document
    out from under the next regeneration, because section identity is derived
    from headings.
    """
    from services.doc_gen.three_layer import build_merged_document

    human_edits = {
        s.section_id: s.edited_content
        for s in document.sections
        if s.human_edited and s.edited_content is not None
    }
    return build_merged_document(document.merged_content, human_edits)


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
        body = export_markdown(document.merged_content, title=document.title)
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
            content=markdown_to_html(document.merged_content, title=document.title),
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
            document.merged_content,
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
        "html": markdown_to_reader_fragment(document.merged_content, document.sections),
        "title": document.title,
        "lifecycle_status": document.lifecycle_status,
    }
