"""
Tier 3 generation — organisation-level documents written by a model.

The one Tier 3 generator so far is the top-level Information Security Policy,
ported from scf-doc-gen. Unlike Tier 2 it is not about one domain: its prompt
summarises the whole programme. Unlike the original it is grounded in the ISMS
profile -- the scope and objectives the organisation recorded -- and told to
write "[To be completed]" where those are missing, rather than inventing a
scope for the board to sign.

It reads domain principles from the SCF catalogue, so it is derivative and
gated with Tier 2. The model call, timeout and mock-mode behaviour are
:mod:`tier2`'s.
"""
from __future__ import annotations

from typing import List, Optional, Sequence

from .context import OrganisationContext
from .registry import GeneratorSpec
from . import tier2


def _missing(value: str) -> str:
    return value.strip() if value and value.strip() else "[To be completed]"


def build_prompt(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    policy_set: Sequence[str],
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
    change_note: str = "",
) -> str:
    """Fill the generator's prompt from context. Pure, like tier2's."""
    profile = ctx.isms_profile or {}
    scope = profile.get("scope") or {}
    objectives = profile.get("objectives") or []
    created = ctx.generated_at[:10]
    year = created[:4]
    try:
        next_review = f"{int(year) + 1}{created[4:]}"
    except ValueError:
        next_review = created
    doc_version = tier2.compute_doc_version(generation_version)
    exclusions = scope.get("exclusions") or []
    return spec.load_prompt_template().format(
        org_name=ctx.name,
        industry=ctx.industry or "not specified",
        created_date=created,
        year=year,
        next_review_date=next_review,
        doc_version=doc_version,
        change_history_instruction=tier2._change_history_instruction(
            tier2.extract_change_history(existing_content), doc_version, created, change_note,
        ),
        scope_description=_missing(scope.get("organisation_description", "")),
        scope_boundaries=_missing(scope.get("boundaries", "")),
        scope_locations=", ".join(scope.get("locations") or []) or "[To be completed]",
        scope_services=", ".join(scope.get("services") or []) or "[To be completed]",
        scope_exclusions="; ".join(
            f"{e.get('item')} ({e.get('justification') or 'no justification recorded'})"
            for e in exclusions
        ) or "None recorded",
        objectives="\n".join(
            f"- {o.get('objective')} (measure: {o.get('measure') or 'not set'}; "
            f"target: {o.get('target') or 'not set'}; due: {o.get('due_date') or 'not set'})"
            for o in objectives if o.get("objective")
        ) or "- None recorded",
        control_count=ctx.total_scoped_controls,
        domain_count=ctx.total_domains,
        implemented_count=ctx.implemented_count(),
        coverage=ctx.coverage_percent(),
        system_count=len(ctx.systems),
        vendor_count=len(ctx.vendors),
        risk_count=len(ctx.risk_assessments),
        acceptable_risk=(ctx.risk_profile or {}).get("acceptable_risk_level") or "not configured",
        frameworks=", ".join(o.get("name") for o in ctx.obligations) or "none recorded",
        domain_summaries="\n".join(
            f"- {d.domain.identifier} — {d.domain.name} ({len(d.controls)} controls): "
            f"{d.domain.principle or 'No principle recorded.'}"
            for d in ctx.domains
        ),
        policy_set="\n".join(f"- {t}" for t in policy_set) or "- None generated yet",
    )


def _mock(spec: GeneratorSpec, ctx: OrganisationContext, doc_version: str) -> str:
    created = ctx.generated_at[:10]
    scope = (ctx.isms_profile or {}).get("scope") or {}
    return "\n".join([
        "> **MOCK OUTPUT** — generated without a language model because no API key is "
        "configured. This document is structurally representative and must not be used "
        "as a control artefact.",
        "",
        "## Document Control",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Document Identifier** | ISP-{created[:4]} |",
        f"| **Version** | {doc_version} |",
        "| **Status** | Draft |",
        "",
        "### Change History",
        "",
        "| Version | Date | Author | Description of Changes |",
        "|---------|------|--------|------------------------|",
        f"| {doc_version} | {created} | CISO function | Initial issue |",
        "",
        "## Management Commitment",
        "",
        f"Top management of {ctx.name} is committed to information security.",
        "",
        "## Scope",
        "",
        _missing(scope.get("boundaries", "")),
        "",
        "## Review",
        "",
        "This policy is reviewed annually.",
        "",
    ])


def generate(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    policy_set: List[str],
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
    change_note: str = "",
) -> tier2.GenerationOutput:
    prompt = build_prompt(
        spec, ctx, policy_set, generation_version=generation_version,
        existing_content=existing_content, change_note=change_note,
    )
    if tier2.is_mock_mode():
        return tier2.GenerationOutput(
            content=_mock(spec, ctx, tier2.compute_doc_version(generation_version)),
            model_id="mock", system_prompt=tier2.SYSTEM_PROMPT, user_prompt=prompt, mocked=True,
        )
    return tier2.GenerationOutput(
        content=tier2.invoke_model(prompt, label=spec.name),
        model_id=tier2.resolve_model(), system_prompt=tier2.SYSTEM_PROMPT,
        user_prompt=prompt, mocked=False,
    )
