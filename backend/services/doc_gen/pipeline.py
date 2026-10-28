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
from typing import Any, Callable, Dict, Iterable, List, Optional

from sqlalchemy.orm import Session

from . import chaptered, lifecycle, scopes, tier2, tier3
from .context import Domain, DomainWithControls, OrganisationContext, build_context
from .fingerprint import compute_fingerprint, describe_change
from .licence import assert_generation_allowed
from .registry import GeneratorSpec, get_generator
from .scopes import MODE_BUNDLE, MODE_DOMAIN, MODE_NONE, MODE_TOPIC
from .section_parser import (
    extract_control_ids,
    parse_markdown_sections,
    to_section_rows,
)
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
    #: SCF ids the document cites that are NOT in scope for this run. Empty is
    #: the expected case; a non-empty list means the generated prose asserts a
    #: requirement this organisation has not scoped, which is an audit finding.
    out_of_scope_citations: List[str] = field(default_factory=list)

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
            "out_of_scope_citations": list(self.out_of_scope_citations),
        }


def find_out_of_scope_citations(
    content: str, in_scope_ids: Iterable[str]
) -> List[str]:
    """SCF ids cited by generated prose that are not in the scoped set.

    The context builder already restricts every generator's input to controls
    with ``selected = True``, and the Tier 2 prompts now forbid going beyond
    that set. Neither is a guarantee: a prompt is an instruction to a model, not
    a constraint on it. This is the check that turns "no drift was observed"
    into "drift would be seen".

    Only bracketed identifiers count, matching
    :func:`section_parser.extract_control_ids` -- prose that names a control
    without brackets is not a citation.
    """
    scoped = set(in_scope_ids)
    return sorted(
        scf_id for scf_id in extract_control_ids(content) if scf_id not in scoped
    )


def _settings_for(session: Session, organization_id: str):
    from models import DocGenSettings
    return (
        session.query(DocGenSettings)
        .filter(DocGenSettings.organization_id == organization_id)
        .one_or_none()
    )


def _change_note(reasons: List[str]) -> str:
    """A one-line description of what moved, for the Change History table.

    ``describe_change`` already names the components that differ between two
    fingerprints -- it is what the staleness column renders. The same sentence
    is exactly what a Change History row should carry: "Revised -- updated
    control data" is a record in shape only, and an auditor reading a version
    table full of it learns nothing about any version.

    Returns "" when nothing is nameable, so the caller falls back to the
    generic wording rather than emitting an empty claim.
    """
    named = [r for r in reasons if r and r != "first generation"]
    if not named:
        return ""
    return "; ".join(named)


def _version_change_summary(
    counts: Dict[str, int], control_count: int, initial: bool
) -> Dict[str, Any]:
    """What this generation did, in the shape ``DocumentVersion`` stores.

    Only non-zero tallies are kept. A summary saying ``{"unchanged": 41}`` and
    one saying ``{"unchanged": 41, "conflict": 0}`` mean the same thing, and the
    second invites a renderer to write "0 conflicts" as though that were news.
    """
    return {
        "counts": {k: v for k, v in (counts or {}).items() if v},
        "control_count": control_count,
        "initial": initial,
    }


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


def _related_titles(
    profile: scopes.Profile,
    ctx: OrganisationContext,
    generator_name: str,
    scope_key: str,
) -> List[str]:
    """Titles of the organisation's other profiled documents.

    Handed to a bundled document's frame so its Related Documents section
    names documents this profile actually produces, rather than guessing at
    a per-domain library the organisation does not have.
    """
    domains = [(d.domain.identifier, d.domain.name) for d in ctx.domains]
    pairs = [(c.scf_id, c.domain_identifier) for c in ctx.all_controls]
    titles: List[str] = []
    for name in scopes.PROFILED_GENERATORS:
        spec = get_generator(name)
        for scope in scopes.scopes_for(profile, name, domains, pairs):
            if name == generator_name and scope.key == scope_key:
                continue
            titles.append(chaptered.document_title(spec, scope)
                          if scope.kind == scopes.KIND_BUNDLE
                          else spec.resolve_title(scope.title))
    return titles


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
    user_prompt = f"tier1:{spec.name}"
    if spec.renderer == "tier1.render_maturity_report":
        # The report's gap section reads targets the fingerprint leaves out
        # (PIG R5); see tier1.target_inputs_digest.
        from .tier1 import target_inputs_digest

        digest = target_inputs_digest(ctx)
        if digest:
            user_prompt = f"{user_prompt}:targets:{digest}"
    elif spec.renderer == "tier1.render_governance_report":
        # The report reads deficiencies, risks, suppliers and validations the
        # fingerprint leaves out (PIG R10); see tier1.governance_inputs_digest.
        from .tier1 import governance_inputs_digest

        user_prompt = f"{user_prompt}:governance:{governance_inputs_digest(ctx)}"
    elif spec.renderer.startswith("iso_core."):
        # The core pack reads registers the fingerprint leaves out (ISMS
        # profile, risks, deficiencies, inventories); see iso_core.inputs_digest.
        from .iso_core import inputs_digest

        user_prompt = f"{user_prompt}:inputs:{inputs_digest(ctx, exclude_generator=spec.name)}"
    return content, f"tier1:{spec.renderer}", user_prompt, None, False


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
    settings = _settings_for(session, organization_id)
    assert_generation_allowed(
        settings,
        tier=spec.tier,
        is_derivative=spec.is_derivative,
    )

    # The organisation's document profile decides what a domain-scoped
    # generator's document covers: a domain, a bundle of domains, or an
    # operational topic (services/doc_gen/templates/bundles.yaml).
    profile = scopes.get_profile(getattr(settings, "document_profile", None))
    mode = (profile.mode_for(spec.name) or MODE_DOMAIN) if spec.domain_scoped else None
    if mode == MODE_NONE:
        raise PipelineError(
            f"The {profile.display_name} profile does not produce a separate "
            f"{spec.name}; its requirements are part of each policy."
        )

    if spec.domain_scoped and not domain_id:
        raise PipelineError(
            f"Generator '{spec.name}' produces one document per {mode}; "
            f"a {mode} must be specified."
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
        # A bundle or topic spans domains, so it reads the whole estate and
        # selects from it below.
        domain_filter=(stored_domain if mode == MODE_DOMAIN else None) or None,
        include_evidence=spec.requires.evidence,
        include_risks=spec.requires.risks,
        include_systems=spec.requires.systems,
        include_governance=spec.requires.governance,
        include_isms=spec.requires.isms,
        include_deficiencies=spec.requires.deficiencies,
        include_obligations=spec.requires.obligations,
        include_documents=spec.requires.documents,
    )
    if not ctx.all_controls:
        # Order matters: a domain-scoped run filters the context to one domain,
        # so an unknown or unscoped domain also produces an empty context. The
        # generic "scope some controls" message would be actively misleading
        # there, so the domain case is reported first.
        if mode == MODE_DOMAIN:
            raise PipelineError(
                f"Domain '{stored_domain}' has no controls in scope. Scope "
                f"controls in that domain before generating its documents."
            )
        raise PipelineError(
            "No controls are in scope for this generation. Scope controls "
            "before generating documents."
        )

    bundle = None
    scope: Optional[scopes.Scope] = None
    #: Bundled documents only: one DomainWithControls per chapter, in order.
    chapters: Optional[List[DomainWithControls]] = None
    if mode in (MODE_BUNDLE, MODE_TOPIC):
        domains = [(d.domain.identifier, d.domain.name) for d in ctx.domains]
        scope = scopes.resolve_for_generation(profile, spec.name, stored_domain, domains)
        if scope is None:
            available = ", ".join(
                s.key for s in scopes.scopes_for(
                    profile, spec.name, domains,
                    [(c.scf_id, c.domain_identifier) for c in ctx.all_controls],
                )
            )
            raise PipelineError(
                f"'{stored_domain}' is not a {mode} the {profile.display_name} profile "
                f"produces {spec.name} documents for. Available: {available or 'none'}"
            )
        stored_domain = scope.key
        if mode == MODE_BUNDLE:
            chapters = [ctx.domain(d) for d in scope.domains if ctx.domain(d) is not None]
        else:
            selected = scope.select(ctx.all_controls)
            if not selected:
                raise PipelineError(
                    f"No controls in scope for the {scope.title} topic. Scope its "
                    "controls before generating its procedure."
                )
            # A topic is written by the single-call path, presented to the
            # prompt as a domain whose principle is the topic's description.
            bundle = DomainWithControls(
                domain=Domain(
                    identifier=scope.key,
                    name=scope.title,
                    principle=scope.description,
                    principle_intent=(
                        "Operational procedure drawing on: "
                        + ", ".join(
                            d.domain.name for d in ctx.domains
                            if d.domain.identifier in scope.domains
                        )
                    ),
                ),
                controls=selected,
            )
    elif spec.domain_scoped:
        bundle = ctx.domain(stored_domain)
        if bundle is None:
            # The context keys domains by identifier, but a caller may have
            # passed the display name. Match that too rather than failing on a
            # spelling difference.
            bundle = next(
                (d for d in ctx.domains if (d.domain.name or "").upper() == stored_domain),
                None,
            )
        if bundle is None:
            available = ", ".join(d.domain.identifier for d in ctx.domains)
            raise PipelineError(
                f"Domain '{stored_domain}' has no scoped controls. "
                f"Available: {available or 'none'}"
            )
        # Canonicalise before anything persists it: the filename, the stored
        # domain_id and the per-domain uniqueness constraint must not fork on
        # whether the caller said "GOV" or "Governance & Risk Management".
        stored_domain = bundle.domain.identifier

    existing = _existing_document(session, organization_id, spec.name, stored_domain)
    generation_version = existing.generation_version if existing else 0
    if chapters is not None:
        scope_controls = [c for ch in chapters for c in ch.controls]
    else:
        scope_controls = bundle.controls if bundle else ctx.all_controls

    # --- 3. Generate ---------------------------------------------------------
    emit("generating", f"Generating {spec.display_name}")
    if spec.tier == 1:
        content, system_prompt, user_prompt, model_id, mocked = _render_tier1(spec, ctx)
        title = spec.resolve_title()
    elif chapters is not None:
        related = _related_titles(profile, ctx, spec.name, stored_domain)
        user_prompt = chaptered.fingerprint_prompt(
            spec, ctx, scope, chapters, profile, related,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
        )
        system_prompt = tier2.SYSTEM_PROMPT
        title = chaptered.document_title(spec, scope)
        content = model_id = None
        mocked = False
    elif not spec.domain_scoped:
        # Tier 3: one organisation-level document written by a model.
        policy_set = _related_titles(profile, ctx, spec.name, "")
        user_prompt = tier3.build_prompt(
            spec, ctx, policy_set,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
        )
        system_prompt = tier2.SYSTEM_PROMPT
        title = spec.resolve_title()
        content = model_id = None
        mocked = False
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
        [c.to_fingerprint_dict() for c in scope_controls],
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

    new_chapter_cache: Dict[str, Any] = {}
    if spec.tier >= 2 and chapters is not None:
        bundled = chaptered.generate_bundle(
            spec, ctx, scope, chapters, profile, related,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
            change_note=_change_note(change_reasons),
            # A forced regeneration rewrites every chapter; otherwise chapters
            # whose prompt is unchanged are reused.
            chapter_cache=(
                None if force or existing is None
                else (getattr(existing, "chapter_cache", None) or {})
            ),
            progress=progress,
        )
        content, model_id, mocked = bundled.content, bundled.model_id, bundled.mocked
        new_chapter_cache = bundled.chapter_cache
    elif spec.tier >= 2 and not spec.domain_scoped:
        output = tier3.generate(
            spec, ctx, policy_set,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
            change_note=_change_note(change_reasons),
        )
        content, model_id, mocked = output.content, output.model_id, output.mocked
    elif spec.tier >= 2:
        output = tier2.generate_document(
            spec, ctx, bundle,
            generation_version=generation_version,
            existing_content=(existing.merged_content if existing else ""),
            # Deliberately NOT passed to the fingerprint's prompt above: that
            # one must stay a pure function of the inputs, or every run would
            # hash a prompt describing the previous run.
            change_note=_change_note(change_reasons),
        )
        content, model_id, mocked = output.content, output.model_id, output.mocked

    # Trailing whitespace only. The document is the organisation's policy and
    # carries nothing about this platform's own licensing position -- that
    # acknowledgement is made once, when an administrator enables the module.
    content = content.rstrip()

    # The generated layer is what gets checked, not the merged document: a
    # human is entitled to cite whatever they like in their own edit, and
    # attributing that to the generator would make the signal noise. This
    # catches the model reaching past the controls it was given.
    out_of_scope = find_out_of_scope_citations(
        content, (c.scf_id for c in ctx.all_controls)
    )
    if out_of_scope:
        logger.warning(
            "doc_gen: %s cited %d control(s) outside %s's scope: %s",
            spec.name, len(out_of_scope), organization_id, ", ".join(out_of_scope),
        )

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
    document.scope_domains = (
        list(scope.domains) if scope is not None
        else ([stored_domain] if spec.domain_scoped else [])
    )
    document.chapter_cache = new_chapter_cache
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
        # The merge tallies exist for one request and were then discarded, so
        # the History panel could only ever list version numbers. Carried
        # forward here, "what changed in v7?" survives the request that
        # answered it.
        change_summary=_version_change_summary(
            counts,
            len(scope_controls),
            existing is None,
        ),
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
        out_of_scope_citations=out_of_scope,
    )
