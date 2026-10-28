"""
Chaptered generation — one document from several domains.

A bundled policy can cover 15 domains and well over a hundred controls. One
model call cannot write that well: the output limit truncates it, and a model
asked to cover everything at once covers each part thinly. So a bundled
document is written in pieces and assembled deterministically:

* one **frame** call writes everything around the core section (Document
  Control, Purpose, Scope, Roles, Compliance, Exceptions, Related Documents,
  Review) and is told not to write the core section;
* one **chapter** call per domain writes that domain's statements;
* :func:`assemble` inserts the core heading and the chapters, in the scope's
  domain order, before the heading the core section precedes.

Each chapter is cached on the document with the hash of the prompt that
produced it. A regeneration after one domain's controls change rewrites that
chapter and the frame (whose Change History must gain a row) and reuses the
rest. The chapter heading paths -- ``policy-statements.<domain>.<subsection>``
-- are stable across runs, so the three-layer merge keeps human edits per
chapter with no change to the merge engine.

Like :mod:`tier2`, everything except the model call is a pure function of its
inputs, and keyless runs produce clearly marked mock output.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from .context import DomainWithControls, OrganisationContext
from .fingerprint import sha256
from .registry import GeneratorSpec, _TEMPLATES_DIR
from .scopes import Profile, Scope
from . import tier2

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChapterLayout:
    """Where the chapters go in a given document type."""

    frame_prompt: str
    chapter_prompt: str
    #: The level-2 heading the chapters sit under.
    core_heading: str
    #: The level-2 heading the core section is inserted before.
    insert_before: str
    identifier_prefix: str


LAYOUTS: Dict[str, ChapterLayout] = {
    "policy": ChapterLayout(
        frame_prompt="policy_frame.md",
        chapter_prompt="policy_chapter.md",
        core_heading="Policy Statements",
        insert_before="Roles and Responsibilities",
        identifier_prefix="POL",
    ),
    "standard": ChapterLayout(
        frame_prompt="standard_frame.md",
        chapter_prompt="standard_chapter.md",
        core_heading="Mandatory Requirements",
        insert_before="Compliance Measurement",
        identifier_prefix="STD",
    ),
}

#: Appended to a policy chapter when the profile has no separate standards
#: (``fold_standards``): the measurable parameters a standard would hold live
#: in the policy instead.
FOLDED_REQUIREMENTS = (
    "- After the policy statements, add a final \"#### Requirements\" subsection: a "
    "Markdown table with columns Requirement | Parameter | Control, giving the "
    "specific, testable parameter (value, threshold, interval or configuration) "
    "for each control where one applies. Where the SCF control does not fix a "
    "parameter, write \"organisation-defined\" with a recommended value, e.g. "
    "\"organisation-defined: recommended 90 days\". Never present an invented "
    "parameter as an SCF requirement.\n"
)


class ChapterError(RuntimeError):
    """The frame came back without a heading the chapters can be placed by."""


@dataclass
class BundleOutput:
    content: str
    model_id: str
    mocked: bool
    chapter_cache: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    reused_chapters: List[str] = field(default_factory=list)


def layout_for(spec: GeneratorSpec) -> ChapterLayout:
    try:
        return LAYOUTS[spec.name]
    except KeyError:
        raise ValueError(f"Generator '{spec.name}' has no chaptered layout") from None


def _template(name: str) -> str:
    return (_TEMPLATES_DIR / "prompts" / name).read_text(encoding="utf-8")


def _dates(ctx: OrganisationContext) -> Dict[str, str]:
    created = ctx.generated_at[:10]
    year = created[:4]
    try:
        next_review = f"{int(year) + 1}{created[4:]}"
    except ValueError:
        next_review = created
    return {"created_date": created, "year": year, "next_review_date": next_review}


def document_title(spec: GeneratorSpec, scope: Scope) -> str:
    return scope.document_title or spec.resolve_title(scope.title)


# ---------------------------------------------------------------------------
# Prompt assembly — pure, deterministic, fingerprint-safe
# ---------------------------------------------------------------------------


def build_frame_prompt(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    scope: Scope,
    chapters: Sequence[DomainWithControls],
    related_titles: Sequence[str],
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
    change_note: str = "",
) -> str:
    controls = [c for d in chapters for c in d.controls]
    owners = sorted({c.owner for c in controls if c.owner})
    doc_version = tier2.compute_doc_version(generation_version)
    dates = _dates(ctx)
    return _template(layout_for(spec).frame_prompt).format(
        org_name=ctx.name,
        industry=ctx.industry or "not specified",
        scope_title=scope.title,
        scope_key=scope.key,
        document_title=document_title(spec, scope),
        domain_list="\n".join(
            f"- {d.domain.identifier} — {d.domain.name}: "
            f"{d.domain.principle or 'No principle recorded.'}"
            for d in chapters
        ),
        control_count=len(controls),
        control_index="\n".join(f"- {c.scf_id} — {c.control_name}" for c in controls),
        owners_list="\n".join(f"- {o}" for o in owners) or "- Unassigned",
        related_documents="\n".join(f"- {t}" for t in related_titles) or "- None",
        doc_version=doc_version,
        change_history_instruction=tier2._change_history_instruction(
            tier2.extract_change_history(existing_content), doc_version,
            dates["created_date"], change_note,
        ),
        **dates,
    )


def build_chapter_prompt(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    scope: Scope,
    chapter: DomainWithControls,
    profile: Profile,
) -> str:
    fold = spec.name == "policy" and profile.fold_standards
    return _template(layout_for(spec).chapter_prompt).format(
        org_name=ctx.name,
        industry=ctx.industry or "not specified",
        document_title=document_title(spec, scope),
        domain_id=chapter.domain.identifier,
        domain_name=chapter.domain.name,
        domain_principle=chapter.domain.principle or "Not specified",
        domain_intent=chapter.domain.principle_intent or "Not specified",
        control_count=len(chapter.controls),
        control_sections=tier2._control_sections(chapter),
        example_id=chapter.controls[0].scf_id if chapter.controls else "SCF-01",
        requirements_instruction=FOLDED_REQUIREMENTS if fold else "",
    )


def fingerprint_prompt(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    scope: Scope,
    chapters: Sequence[DomainWithControls],
    profile: Profile,
    related_titles: Sequence[str],
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
) -> str:
    """Every prompt the document would send, change note withheld.

    The single string :func:`fingerprint.compute_fingerprint` hashes as the
    prompt component, for the same reason :func:`tier2.build_user_prompt` is
    hashed without its change note.
    """
    parts = [build_frame_prompt(
        spec, ctx, scope, chapters, related_titles,
        generation_version=generation_version, existing_content=existing_content,
    )]
    parts += [build_chapter_prompt(spec, ctx, scope, c, profile) for c in chapters]
    return "\n\n=== CHAPTER ===\n\n".join(parts)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def normalise_chapter(markdown: str, domain_name: str) -> str:
    """Make a chapter safe to place under a ``###`` domain heading.

    The prompt asks for ``####`` headings only. A model that ignores that would
    otherwise break the document's heading tree -- a stray ``##`` in a chapter
    would end the core section early and re-parent everything after it -- so
    shallower headings are demoted here rather than trusted. A leading heading
    that only repeats the domain name is dropped, as the platform adds its own.
    """
    lines = markdown.strip().split("\n")
    if lines:
        first = _HEADING.match(lines[0])
        if first and first.group(2).strip().lower() == domain_name.strip().lower():
            lines = lines[1:]
    out = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        match = None if in_fence else _HEADING.match(line)
        if match and len(match.group(1)) < 4:
            out.append(f"#### {match.group(2)}")
        else:
            out.append(line)
    return "\n".join(out).strip()


def _find_h2(lines: List[str], text: str) -> Optional[int]:
    wanted = text.strip().lower()
    for i, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 2 and match.group(2).strip().lower().lstrip(
            "0123456789. "
        ) == wanted:
            return i
    return None


def assemble(
    frame: str,
    chapters: Sequence[tuple],
    layout: ChapterLayout,
) -> str:
    """Insert the core section into the frame.

    ``chapters`` is ``[(domain_name, chapter_markdown), ...]`` in order. The
    core section goes before ``layout.insert_before``; if the model renamed or
    dropped that heading, it goes after the Scope section, and failing that at
    the end -- a document with its sections slightly out of order is
    recoverable by a human; one missing its policy statements is not.
    """
    body = [f"## {layout.core_heading}", ""]
    for name, content in chapters:
        body += [f"### {name}", "", content.strip(), ""]
    core = "\n".join(body).rstrip()

    lines = frame.strip().split("\n")
    # Any core heading the model wrote despite the instruction would duplicate
    # ours and fork the section ids; drop it and its body.
    existing = _find_h2(lines, layout.core_heading)
    if existing is not None:
        end = next(
            (j for j in range(existing + 1, len(lines))
             if (m := _HEADING.match(lines[j])) and len(m.group(1)) <= 2),
            len(lines),
        )
        lines = lines[:existing] + lines[end:]

    at = _find_h2(lines, layout.insert_before)
    if at is None:
        scope_at = _find_h2(lines, "Scope")
        if scope_at is not None:
            at = next(
                (j for j in range(scope_at + 1, len(lines))
                 if (m := _HEADING.match(lines[j])) and len(m.group(1)) <= 2),
                len(lines),
            )
        else:
            at = len(lines)
    head = "\n".join(lines[:at]).rstrip()
    tail = "\n".join(lines[at:]).strip()
    return "\n\n".join(p for p in (head, core, tail) if p) + "\n"


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def _mock_frame(spec: GeneratorSpec, scope: Scope, chapters: Sequence[DomainWithControls],
                doc_version: str, created_date: str) -> str:
    layout = layout_for(spec)
    lines = [
        "> **MOCK OUTPUT** — generated without a language model because no API "
        "key is configured. This document is structurally representative and "
        "must not be used as a control artefact.",
        "",
        "## Document Control",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| **Document Identifier** | {layout.identifier_prefix}-{scope.key}-{created_date[:4]} |",
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
        f"This document covers {scope.title}: "
        + ", ".join(d.domain.name for d in chapters) + ".",
        "",
        "## Scope",
        "",
        f"{sum(len(d.controls) for d in chapters)} controls are in scope across "
        f"{len(chapters)} domain(s).",
        "",
    ]
    tail = (
        ["## Roles and Responsibilities", "", "All roles referenced in this document "
         "describe organisational *functions*, not specific job titles.", "",
         "## Review and Revision", "", "This document is reviewed annually.", ""]
        if spec.name == "policy" else
        ["## Compliance Measurement", "", "Conformance is tested annually.", "",
         "## Deviation and Waiver", "", "Deviations require CISO function approval.", ""]
    )
    return "\n".join(lines + tail)


def _mock_chapter(chapter: DomainWithControls, fold: bool) -> str:
    lines = ["#### Requirements of this domain", ""]
    for c in chapter.controls:
        lines += [
            f"The organisation shall maintain the capability described by "
            f"{c.control_name}. [{c.scf_id}]",
            "",
        ]
    if fold:
        lines += ["#### Requirements", "", "| Requirement | Parameter | Control |",
                  "|---|---|---|"]
        lines += [f"| {c.control_name} | organisation-defined | [{c.scf_id}] |"
                  for c in chapter.controls]
    return "\n".join(lines)


def generate_bundle(
    spec: GeneratorSpec,
    ctx: OrganisationContext,
    scope: Scope,
    chapters: Sequence[DomainWithControls],
    profile: Profile,
    related_titles: Sequence[str],
    *,
    generation_version: Optional[int] = None,
    existing_content: str = "",
    change_note: str = "",
    chapter_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    progress: Optional[Callable[[str, str], None]] = None,
) -> BundleOutput:
    """Write the frame and each chapter, reusing cached chapters, and assemble.

    ``chapter_cache`` is the document's stored cache; pass ``None`` (as a
    forced regeneration does) to rewrite every chapter.
    """
    layout = layout_for(spec)
    cache = chapter_cache or {}
    mocked = tier2.is_mock_mode()
    model_id = "mock" if mocked else tier2.resolve_model()
    fold = spec.name == "policy" and profile.fold_standards

    new_cache: Dict[str, Dict[str, Any]] = {}
    written: List[tuple] = []
    reused: List[str] = []
    for index, chapter in enumerate(chapters, start=1):
        ident = chapter.domain.identifier
        prompt = build_chapter_prompt(spec, ctx, scope, chapter, profile)
        prompt_hash = sha256(prompt)
        cached = cache.get(ident)
        # A mock chapter is never reused once a key is configured, and a real
        # one never stands in for a mock run: the cache is only valid for the
        # mode that wrote it.
        if (cached and cached.get("prompt_hash") == prompt_hash
                and (cached.get("model_id") == "mock") == mocked):
            content = cached["content"]
            reused.append(ident)
        else:
            if progress:
                progress("generating", f"Writing chapter {index} of {len(chapters)}: "
                                       f"{chapter.domain.name}")
            raw = _mock_chapter(chapter, fold) if mocked else tier2.invoke_model(
                prompt, label=f"{spec.name}:{scope.key}:{ident}"
            )
            content = normalise_chapter(raw, chapter.domain.name)
        new_cache[ident] = {
            "prompt_hash": prompt_hash, "content": content,
            "model_id": cached.get("model_id") if ident in reused else model_id,
        }
        written.append((chapter.domain.name, content))

    if progress:
        progress("generating", "Writing the document frame")
    frame_prompt = build_frame_prompt(
        spec, ctx, scope, chapters, related_titles,
        generation_version=generation_version, existing_content=existing_content,
        change_note=change_note,
    )
    if mocked:
        frame = _mock_frame(spec, scope, chapters,
                            tier2.compute_doc_version(generation_version), ctx.generated_at[:10])
    else:
        frame = tier2.invoke_model(frame_prompt, label=f"{spec.name}:{scope.key}:frame")

    if reused:
        logger.info("doc_gen %s %s: reused %d of %d chapters",
                    spec.name, scope.key, len(reused), len(chapters))
    return BundleOutput(
        content=assemble(frame, written, layout),
        model_id=model_id,
        mocked=mocked,
        chapter_cache=new_cache,
        reused_chapters=reused,
    )
