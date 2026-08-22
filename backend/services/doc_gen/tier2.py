"""
Tier 2 generation — prompt assembly and the language-model call.

Everything a Tier 2 generator does that is *not* the model call happens here
deterministically: the prompt is built from context by pure functions, so it
can be fingerprinted, diffed, and asserted on in tests without an API key.
Only :func:`generate_document` reaches the network, and it degrades to mock
output when no key is configured — the pattern
``services/recipe_generation_engine`` already established, so the whole flow
is exercisable on a keyless developer machine.

The prompts live in ``templates/prompts/*.md`` rather than in Python string
literals. They are the part of this feature most likely to be edited by
somebody who is not editing code.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .context import DomainWithControls, OrganisationContext
from .registry import GeneratorSpec
from .tier1 import status_label

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 16384

SYSTEM_PROMPT = (
    "You are an expert Information Security Management System (ISMS) "
    "documentation author. You write formal, auditor-ready documents based on "
    "the Secure Controls Framework (SCF). Your output is always well-structured "
    "Markdown that meets ISO 27001 documentation standards. You write in formal "
    "UK English."
)


class GenerationError(RuntimeError):
    """Raised when the model cannot produce usable output."""


@dataclass(frozen=True)
class GenerationOutput:
    """What a Tier 2 run produced, plus the provenance to store with it."""

    content: str
    model_id: str
    system_prompt: str
    user_prompt: str
    mocked: bool = False


def is_mock_mode() -> bool:
    """Mock mode: explicit flag, or no API key configured.

    Keyless mode matters beyond developer convenience. It means the merge
    engine, the lifecycle, the editor and the export path can all be tested
    end to end without spending a token or holding a credential.
    """
    if os.getenv("DOC_GEN_AI_MOCK", "").strip() == "1":
        return True
    return not os.getenv("ANTHROPIC_API_KEY", "").strip()


def resolve_model() -> str:
    return os.getenv("DOC_GEN_AI_MODEL", "").strip() or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Prompt assembly — pure, deterministic, fingerprint-safe
# ---------------------------------------------------------------------------


def _control_sections(bundle: DomainWithControls) -> str:
    """Render the control data block the prompt interpolates.

    Assessment objectives are included because they are what turn a control
    description into something a policy statement can be verified against.
    """
    parts: List[str] = []
    for c in bundle.controls:
        section = [
            f"### {c.scf_id} — {c.control_name}",
            f"- **Status:** {status_label(c.implementation_status)}",
            f"- **Maturity:** {c.maturity_level or 'Unrated'}",
            f"- **Owner:** {c.owner or 'Unassigned'}",
            f"- **Description:** {c.control_description}",
        ]
        if c.implementation_notes:
            section.append(f"- **Implementation Notes:** {c.implementation_notes}")
        if c.assessment_objectives:
            section.append("")
            section.append("**Assessment Objectives:**")
            for ao in c.assessment_objectives:
                section.append(f"- {ao['ao_id']}: {ao['objective_text']}")
        parts.append("\n".join(section))
    return "\n\n".join(parts)


def compute_doc_version(generation_version: Optional[int]) -> str:
    """``1.0`` on first generation, then ``1.1``, ``1.2``...

    Deliberately not semantic versioning. The number answers "how many times
    has the generator rewritten this?", which is the question the Change
    History table asks.
    """
    gen = generation_version or 0
    return "1.0" if gen == 0 else f"1.{gen}"


def extract_change_history(markdown: str) -> str:
    """Pull existing Change History rows out of a previous document.

    Regeneration must not amnesia away the revision record — an ISMS document
    whose history restarts at 1.0 every time is worse than no history, because
    it looks like a record while asserting something false. The rows are handed
    back to the model with an instruction to reproduce them verbatim.
    """
    if not markdown:
        return ""
    lines = markdown.split("\n")
    start = next(
        (i for i, line in enumerate(lines) if re.search(r"change\s*history", line, re.I)),
        None,
    )
    if start is None:
        return ""

    rows: List[str] = []
    in_table = False
    for line in lines[start + 1:]:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        if stripped.startswith("|"):
            # Skip the separator row and the header row.
            if re.match(r"^\|[\s\-:|]+\|$", stripped):
                continue
            if re.search(r"version.*date.*author", stripped, re.I):
                continue
            in_table = True
            rows.append(stripped)
        elif in_table:
            break
    return "\n".join(rows)


#: What a regeneration row says when nothing specific can be named. Kept as the
#: fallback only: a table of identical rows is a record in shape alone.
GENERIC_REVISION_NOTE = "Revised — updated control data"


def _change_history_instruction(
    existing_history: str,
    doc_version: str,
    created_date: str,
    change_note: str = "",
) -> str:
    """The Change History block of the user prompt.

    ``change_note`` names what actually moved since the last generation --
    "controls 3 added, 1 removed", "catalog 2025.2 → 2025.3" -- and comes from
    the same :func:`~services.doc_gen.fingerprint.describe_change` the staleness
    column renders. Without it every regeneration row read "Revised — updated
    control data", which tells a reader that something happened and nothing
    about what, on the one table an auditor uses to trace a policy's history.

    The note is appended to the generic wording rather than replacing it, so the
    column still scans as a list of revisions and the detail rides along after
    the em dash.
    """
    if existing_history:
        description = f"Revised — {change_note}" if change_note else GENERIC_REVISION_NOTE
        return (
            f"   Carry forward these existing Change History rows VERBATIM, then "
            f"append exactly one new row for Version {doc_version}:\n"
            f"{existing_history}\n"
            f"   | {doc_version} | {created_date} | CISO function | "
            f"{description} |"
        )
    return (
        f"   First generation. The Change History table MUST contain exactly one "
        f"row:\n   | 1.0 | {created_date} | CISO function | Initial issue |"
    )


def build_user_prompt(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    bundle: DomainWithControls,
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
    change_note: str = "",
) -> str:
    """Fill the generator's prompt template from context.

    Pure: same inputs, same string. This is what
    :func:`fingerprint.compute_fingerprint` hashes as ``prompt_hash``, so any
    drift here correctly invalidates every document the generator produced.

    ``change_note`` is the one argument the pipeline deliberately withholds when
    it builds the prompt *for the fingerprint*: the note describes the delta
    against the previous fingerprint, so feeding it back in would hash a prompt
    that describes its own comparison. The fingerprint sees the note-free
    prompt; the model sees the note. Both are deterministic in their own inputs.
    """
    template = spec.load_prompt_template()
    created_date = ctx.generated_at[:10]
    year = created_date[:4]
    try:
        next_review = f"{int(year) + 1}{created_date[4:]}"
    except ValueError:
        next_review = created_date

    owners = sorted({c.owner for c in bundle.controls if c.owner})
    doc_version = compute_doc_version(generation_version)

    return template.format(
        org_name=ctx.name,
        # Prompt templates render this inline, so an unset industry becomes a
        # neutral phrase rather than the literal "None" leaking into an LLM prompt.
        industry=ctx.industry or "not specified",
        created_date=created_date,
        year=year,
        next_review_date=next_review,
        domain_id=bundle.domain.identifier,
        domain_name=bundle.domain.name,
        domain_principle=bundle.domain.principle or "Not specified",
        domain_intent=bundle.domain.principle_intent or "Not specified",
        control_count=len(bundle.controls),
        owners_list="\n".join(f"- {o}" for o in owners) or "- Unassigned",
        control_sections=_control_sections(bundle),
        doc_version=doc_version,
        change_history_instruction=_change_history_instruction(
            extract_change_history(existing_content), doc_version, created_date,
            change_note,
        ),
    )


# ---------------------------------------------------------------------------
# Model invocation
# ---------------------------------------------------------------------------


def _mock_content(spec: GeneratorSpec, bundle: DomainWithControls, doc_version: str,
                  created_date: str) -> str:
    """Clearly-marked sample output for keyless runs.

    Marked loudly and deliberately: a mock document that could be mistaken for
    a real one would be a compliance hazard, not a convenience.
    """
    lines = [
        "> **MOCK OUTPUT** — generated without a language model because no API "
        "key is configured. This document is structurally representative and "
        "must not be used as a control artefact.",
        "",
        "## Document Control",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Document Identifier** | {spec.document_type.upper()[:3]}-"
        f"{bundle.domain.identifier}-{created_date[:4]} |",
        f"| **Version** | {doc_version} |",
        "| **Status** | Draft |",
        f"| **Effective Date** | {created_date} |",
        "| **Document Owner** | Chief Information Security Officer function |",
        "| **Review Cycle** | Annual |",
        "| **Classification** | Internal |",
        "",
        "### Change History",
        "",
        "| Version | Date | Author | Description of Changes |",
        "|---------|------|--------|------------------------|",
        f"| {doc_version} | {created_date} | CISO function | Initial issue |",
        "",
        "## Purpose",
        "",
        f"This document addresses the {bundle.domain.name} domain "
        f"({bundle.domain.identifier}) for the controls currently in scope.",
        "",
        "## Scope",
        "",
        f"{len(bundle.controls)} controls are in scope for this domain.",
        "",
        "## Policy Statements",
        "",
    ]
    for c in bundle.controls:
        lines += [
            f"### {c.control_name}",
            "",
            f"The organisation shall maintain the capability described by this "
            f"control. [{c.scf_id}]",
            "",
        ]
    lines += [
        "## Roles and Responsibilities",
        "",
        "All roles referenced in this document describe organisational "
        "*functions*, not specific job titles.",
        "",
        "## Review and Revision",
        "",
        "This document is reviewed annually.",
        "",
    ]
    return "\n".join(lines)


def generate_document(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    bundle: DomainWithControls,
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
    change_note: str = "",
) -> GenerationOutput:
    """Produce Tier 2 document content.

    Returns mock content when no API key is configured rather than raising, so
    the surrounding pipeline is testable end to end. The ``mocked`` flag rides
    along on the result so callers can record it.
    """
    user_prompt = build_user_prompt(
        spec, ctx, bundle,
        generation_version=generation_version,
        existing_content=existing_content,
        change_note=change_note,
    )
    model_id = resolve_model()

    if is_mock_mode():
        logger.info("doc_gen tier2 running in mock mode for generator=%s", spec.name)
        created_date = ctx.generated_at[:10]
        return GenerationOutput(
            content=_mock_content(
                spec, bundle, compute_doc_version(generation_version), created_date
            ),
            model_id="mock",
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            mocked=True,
        )

    # Imported lazily: the SDK is not needed on the API process, only in the
    # worker, and only when a key is present.
    from anthropic import Anthropic

    client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the task's status key
        logger.exception("doc_gen tier2 model call failed for generator=%s", spec.name)
        raise GenerationError(f"Document generation failed: {exc}") from exc

    text = "".join(
        block.text for block in response.content if getattr(block, "type", "") == "text"
    ).strip()
    if not text:
        raise GenerationError("The model returned no document content")

    return GenerationOutput(
        content=text,
        model_id=model_id,
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        mocked=False,
    )
