"""
The generation pipeline — one function that runs a document end to end.

Order matters here, and the order encodes the design:

1. **Gate first.** The licence check runs before any data is read, so a
   refused generation costs one query, not a full context build and a model
   call.
2. **Build context, then fingerprint.** The fingerprint is computed from the
   assembled context and the fully-interpolated prompt, so it captures both
   what the data says and how the generator asked about it.
3. **Compare and skip.** If the fingerprint matches what is stored, nothing
   has changed that could change the document, and the run stops. This is the
   difference between a feature people use and one they turn off — without it,
   regenerating an ISMS is a bill and a merge review for no reason.
4. **Generate, then merge.** The generated layer is stored immutably in
   ``document_versions``; the merge decides what the operative document says.
5. **Persist as one transaction.** A half-written merge would leave sections
   describing content the document does not contain.

The pipeline is synchronous and takes a session. The Celery task owns the
session lifecycle; this function owns the correctness.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from . import lifecycle, tier2
from .context import OrganisationContext, build_context
from .fingerprint import compute_fingerprint, describe_change
from .licence import assert_generation_allowed, attribution_footer
from .registry import GeneratorSpec, get_generator
from .section_parser import parse_markdown_sections, to_section_rows
from .three_layer import STATUS_CONFLICT, collect_human_edits, three_way_merge

logger = logging.getLogger(__name__)

GENERATOR_VERSION = "1.0.0"


class PipelineError(RuntimeError):
    """Raised when a generation cannot proceed for a non-licensing reason."""


@dataclass
class GenerationResult:
    """The outcome of one generation run."""

    document_id: Optional[str]
    generator_name: str
    domain_id: str
    action: str                      # "created" | "updated" | "skipped"
    title: str = ""
    manifest: List[Dict[str, Any]] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    conflict_count: int = 0
    skip_reason: Optional[str] = None
    change_reasons: List[str] = field(default_factory=list)
    mocked: bool = False
    generation_version: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "document_id": self.document_id,
            "generator": self.generator_name,
            "domain_id": self.domain_id,
            "action": self.action,
            "title": self.title,
            "manifest": self.manifest,
            "counts": self.counts,
            "conflict_count": self.conflict_count,
            "skip_reason": self.skip_reason,
            "change_reasons": self.change_reasons,
            "mocked": self.mocked,
            "generation_version": self.generation_version,
        }


def _settings_for(session: Session, organization_id: str):
    from models import DocGenSettings
    return (
        session.query(DocGenSettings)
        .filter(DocGenSettings.organization_id == organization_id)
        .one_or_none()
    )


def _existing_document(session: Session, organization_id: str, generator_name: str,
                       domain_id: str):
    from models import GeneratedDocument
    return (
        session.query(GeneratedDocument)
        .filter(
            GeneratedDocument.organization_id == organization_id,
            GeneratedDocument.generator_name == generator_name,
            GeneratedDocument.domain_id == domain_id,
        )
        .one_or_none()
    )


def _render_tier1(spec: GeneratorSpec, ctx: OrganisationContext) -> tuple:
    """Return (content, system_prompt, user_prompt, model_id, mocked).

    Tier 1 has no prompts, but the fingerprint wants a template hash and a
    prompt hash. Using the renderer's dotted name as the template hash input
    means renaming or repointing a renderer correctly invalidates its
    documents; using the generator name plus domain as the prompt hash input
    keeps two domains' documents from colliding.
    """
    renderer = spec.resolve_renderer()
    content = renderer(ctx)
    return content, f"tier1:{spec.renderer}", f"tier1:{spec.name}", None, False


def run_generation(
    session: Session,
    *,
    organization_id: str,
    generator_name: str,
    domain_id: Optional[str] = None,
    user_id: Optional[str] = None,
    user_email: Optional[str] = None,
    force: bool = False,
    progress: Optional[Callable[[str, str], None]] = None,
) -> GenerationResult:
    """Generate or regenerate one document.

    Args:
        session: Synchronous session. The caller commits.
        organization_id: Resolved from membership by the caller, never from a
            request path parameter.
        generator_name: A name registered in ``templates/generators.yaml``.
        domain_id: Required for domain-scoped generators.
        user_id, user_email: Recorded on the document and its transitions.
        force: Bypass the fingerprint skip. Costs a model call, so it is opt-in.
        progress: Optional ``(stage, message)`` callback for the status key.

    Returns:
        A :class:`GenerationResult`.

    Raises:
        LicenceError: if the organisation may not run this generator.
        PipelineError: for a missing domain or an empty scope.
    """
    from models import (
        DocumentSection, DocumentTransition, DocumentVersion, GeneratedDocument,
    )

    def emit(stage: str, message: str) -> None:
        if progress:
            progress(stage, message)

    spec = get_generator(generator_name)

    # --- 1. Gate, before touching any data ---------------------------------
    emit("checking", "Checking permissions")
    assert_generation_allowed(
        _settings_for(session, organization_id),
        tier=spec.tier,
        is_derivative=spec.is_derivative,
    )

    if spec.domain_scoped and not domain_id:
        raise PipelineError(
            f"Generator '{spec.name}' produces one document per domain; "
            "a domain must be specified."
        )
    # Non-domain generators store '' rather than NULL. Postgres treats NULLs as
    # distinct in a unique index, so a NULL here would silently permit an
    # unlimited number of duplicate documents per organisation.
    stored_domain = (domain_id or "").upper() if spec.domain_scoped else ""

    # --- 2. Context ---------------------------------------------------------
    emit("loading", "Loading organisation data")
    ctx = build_context(
        session,
        organization_id,
        domain_filter=stored_domain or None,
        include_evidence=spec.requires.evidence,
        include_risks=spec.requires.risks,
        include_systems=spec.requires.systems,
    )
    if not ctx.all_controls:
        # Order matters: a domain-scoped run filters the context to one domain,
        # so an unknown or unscoped domain also produces an empty context. The
        # generic "scope some controls" message would be actively misleading
        # there, so the domain case is reported first.
        if spec.domain_scoped:
            raise PipelineError(
                f"Domain '{stored_domain}' has no controls in scope. Scope "
                f"controls in that domain before generating its documents."
            )
        raise PipelineError(
            "No controls are in scope for this generation. Scope controls "
            "before generating documents."
        )

    bundle = None
    if spec.domain_scoped:
        bundle = ctx.domain(stored_domain)
        if bundle is None:
            available = ", ".join(d.domain.identifier for d in ctx.domains)
            raise PipelineError(
                f"Domain '{stored_domain}' has no scoped controls. "
                f"Available: {available or 'none'}"
            )

    existing = _existing_document(session, organization_id, spec.name, stored_domain)
    generation_version = existing.generation_version if existing else 0

    # --- 3. Generate ---------------------------------------------------------
    emit("generating", f"Generating {spec.display_name}")
    if spec.tier == 1:
        content, system_prompt, user_prompt, model_id, mocked = _render_tier1(spec, ctx)
        title = spec.resolve_title()
    else:
        # Build the prompt first so the fingerprint can be checked before the
        # model is called — the prompt is deterministic, the call is not.
        user_prompt = tier2.build_user_prompt(
            spec, ctx, bundle,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
        )
        system_prompt = tier2.SYSTEM_PROMPT
        title = spec.resolve_title(bundle.domain.name)
        content = model_id = None
        mocked = False

    fingerprint = compute_fingerprint(
        [c.to_fingerprint_dict() for c in (bundle.controls if bundle else ctx.all_controls)],
        system_prompt,
        user_prompt,
        catalog_version=ctx.catalog_version,
    )

    # --- 4. Skip when nothing that matters has changed -----------------------
    if (
        not force
        and existing is not None
        and existing.input_fingerprint == fingerprint.input_fingerprint
    ):
        logger.info(
            "doc_gen skip: org=%s generator=%s domain=%s fingerprint unchanged",
            organization_id, spec.name, stored_domain,
        )
        return GenerationResult(
            document_id=str(existing.id),
            generator_name=spec.name,
            domain_id=stored_domain,
            action="skipped",
            title=existing.title,
            skip_reason="Inputs are unchanged since the last generation.",
            generation_version=existing.generation_version,
        )

    change_reasons = describe_change(
        (existing.input_components if existing else {}) or {},
        fingerprint.input_components,
    )

    if spec.tier >= 2:
        output = tier2.generate_document(
            spec, ctx, bundle,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
        )
        content, model_id, mocked = output.content, output.model_id, output.mocked

    content = content.rstrip() + attribution_footer(spec.is_derivative)

    # --- 5. Merge ------------------------------------------------------------
    emit("merging", "Merging with your edits")
    stored_rows: List[Dict[str, Any]] = []
    if existing is not None:
        for row in existing.sections:
            stored_rows.append({
                "section_id": row.section_id,
                "heading_text": row.heading_text,
                "heading_level": row.heading_level,
                "ordinal": row.ordinal,
                "content_hash": row.content_hash,
                "last_generated_hash": row.last_generated_hash,
                "human_edited": row.human_edited,
                "edited_content": row.edited_content,
                "status": row.status,
                "control_ids": row.control_ids or [],
            })

    if existing is None:
        parsed = parse_markdown_sections(content)
        merged_content = content
        section_rows = to_section_rows(parsed)
        manifest = [
            {"section_id": r["section_id"], "status": "new",
             "heading_text": r["heading_text"], "control_ids": r["control_ids"],
             "detail": None}
            for r in section_rows
        ]
        counts = {"new": len(section_rows)}
        conflict_count = 0
    else:
        merge = three_way_merge(
            content,
            existing.merged_content,
            stored_rows,
            collect_human_edits(stored_rows),
        )
        merged_content = merge.merged_content
        section_rows = merge.sections
        manifest = [entry.to_dict() for entry in merge.manifest]
        counts = merge.counts
        conflict_count = merge.conflict_count

    # --- 6. Persist ----------------------------------------------------------
    emit("saving", "Saving document")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    new_generation_version = generation_version + 1

    if existing is None:
        document = GeneratedDocument(
            organization_id=organization_id,
            generator_name=spec.name,
            document_type=spec.document_type,
            domain_id=stored_domain,
            title=title,
            filename=spec.resolve_filename(stored_domain),
            created_by_user_id=user_id,
            lifecycle_status="draft",
        )
        session.add(document)
        session.flush()
        session.add(DocumentTransition(
            document_id=document.id,
            from_status=None,
            to_status="draft",
            actor_user_id=user_id,
            actor_email=user_email,
            trigger="generation",
            reason="Document generated",
        ))
        action = "created"
    else:
        document = existing
        action = "updated"
        # An edit to an approved document returns it to review. A regeneration
        # is an edit: a sign-off granted against different text is not a
        # sign-off for this one.
        next_status = lifecycle.transition_on_edit(document.lifecycle_status)
        if next_status:
            session.add(DocumentTransition(
                document_id=document.id,
                from_status=document.lifecycle_status,
                to_status=next_status,
                actor_user_id=user_id,
                actor_email=user_email,
                trigger="regeneration",
                reason="Document regenerated after approval",
            ))
            document.lifecycle_status = next_status

    document.title = title
    document.merged_content = merged_content
    document.input_fingerprint = fingerprint.input_fingerprint
    document.input_components = fingerprint.input_components.to_dict()
    document.catalog_version = ctx.catalog_version
    document.generator_version = GENERATOR_VERSION
    document.model_id = model_id
    document.generation_version = new_generation_version
    document.tier = spec.tier
    document.is_derivative = spec.is_derivative
    document.updated_at = now
    session.flush()

    # The pure generated layer, stored immutably. This is what the section-diff
    # UI shows beside a human edit, and what "take the generated version"
    # restores from.
    session.add(DocumentVersion(
        document_id=document.id,
        version=new_generation_version,
        content=content,
        input_fingerprint=fingerprint.input_fingerprint,
        model_id=model_id,
        generator_version=GENERATOR_VERSION,
    ))

    # Replace section rows wholesale. They are derived state — rebuilt from the
    # merge on every run — so reconciling in place would add a failure mode
    # without adding information.
    session.query(DocumentSection).filter(
        DocumentSection.document_id == document.id
    ).delete(synchronize_session=False)
    for row in section_rows:
        session.add(DocumentSection(
            document_id=document.id,
            section_id=row["section_id"],
            heading_text=row.get("heading_text") or "",
            heading_level=row.get("heading_level") or 2,
            ordinal=row.get("ordinal") or 0,
            content_hash=row.get("content_hash") or "",
            last_generated_hash=row.get("last_generated_hash") or "",
            human_edited=bool(row.get("human_edited")),
            edited_content=row.get("edited_content"),
            status=row.get("status") or "new",
            control_ids=row.get("control_ids") or [],
            edited_by_user_id=row.get("edited_by_user_id"),
        ))

    logger.info(
        "doc_gen %s: org=%s generator=%s domain=%s v%d conflicts=%d",
        action, organization_id, spec.name, stored_domain,
        new_generation_version, conflict_count,
    )

    return GenerationResult(
        document_id=str(document.id),
        generator_name=spec.name,
        domain_id=stored_domain,
        action=action,
        title=title,
        manifest=manifest,
        counts=counts,
        conflict_count=conflict_count,
        change_reasons=change_reasons,
        mocked=mocked,
        generation_version=new_generation_version,
    )
