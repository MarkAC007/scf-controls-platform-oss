"""
Tier 1 document renderers — deterministic, no language model involved.

Ports of the template-based generators in ``scf-doc-gen/src/generators/``.
Every function here takes an :class:`~services.doc_gen.context.OrganisationContext`
and returns Markdown. No I/O, no network, no randomness: the same context
always renders the same bytes, which is what makes the input fingerprint
meaningful.

These renderers do not append the attribution footer — the pipeline does that
once, so the footer cannot drift between generators.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .context import IMPLEMENTED_STATUSES, DomainWithControls, OrganisationContext

#: Maturity levels in assessment order, so a report reads L0 to L5 rather than
#: alphabetically. Anything unrecognised sorts last.
MATURITY_ORDER = ["L0", "L1", "L2", "L3", "L4", "L5"]


def status_label(status: Optional[str]) -> str:
    """``in_progress`` -> ``In Progress``."""
    if not status:
        return "Unspecified"
    return " ".join(word.capitalize() for word in status.replace("_", " ").split())


def _cell(value: Any, limit: Optional[int] = None) -> str:
    """Make a value safe to sit inside a Markdown table cell.

    Pipes would break the column structure and newlines would break the row, so
    both are neutralised. This is why generated tables never contain a stray
    column: the escaping happens at the only place values enter a table.
    """
    if value is None or value == "":
        return "—"
    text = str(value).replace("|", "/").replace("\n", " ").replace("\r", " ").strip()
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or "—"


def _pct(count: int, total: int) -> str:
    if not total:
        return "0.0%"
    return f"{100.0 * count / total:.1f}%"


def _maturity_sort_key(level: str) -> tuple:
    try:
        return (0, MATURITY_ORDER.index(level))
    except ValueError:
        return (1, 0)


def _header(ctx: OrganisationContext, title: str) -> List[str]:
    """The identical preamble every Tier 1 document opens with."""
    lines = [
        f"# {title}",
        f"## {ctx.name}",
        "",
        f"**Generated:** {ctx.generated_at}",
        f"**Controls in Scope:** {ctx.total_scoped_controls}",
        f"**Domains:** {ctx.total_domains}",
    ]
    if ctx.catalog_version:
        lines.append(f"**SCF Catalog Version:** {ctx.catalog_version}")
    lines += ["", "---", ""]
    return lines


# ---------------------------------------------------------------------------
# Statement of Applicability
# ---------------------------------------------------------------------------


def _soa_domain_section(bundle: DomainWithControls, index: int) -> List[str]:
    domain, controls = bundle.domain, bundle.controls
    lines = [
        f"### {index}. {domain.identifier} — {domain.name} ({len(controls)} controls)",
        "",
    ]
    if domain.principle:
        lines += [f"> {domain.principle}", ""]

    lines += [
        "| SCF ID | Control | Status | Owner | Maturity | Justification |",
        "|--------|---------|--------|-------|----------|---------------|",
    ]
    for c in controls:
        lines.append(
            f"| {_cell(c.scf_id)} | {_cell(c.control_name)} | "
            f"{status_label(c.implementation_status)} | {_cell(c.owner)} | "
            f"{_cell(c.maturity_level)} | {_cell(c.implementation_notes, 120)} |"
        )
    lines.append("")
    return lines


def render_soa(ctx: OrganisationContext, **_: Any) -> str:
    """Statement of Applicability.

    Lists every in-scope control with its status, owner, maturity and the
    organisation's own justification. The SCF content reproduced here is
    identifiers and names only — the assertions are the organisation's.
    """
    total = ctx.total_scoped_controls
    lines = _header(ctx, "Statement of Applicability")

    lines += [
        "## 1. Purpose",
        "",
        f"This Statement of Applicability identifies the security controls selected by "
        f"{ctx.name} as part of its information security management system. It records "
        f"each control's implementation status, ownership, maturity level, and the "
        f"justification for its inclusion.",
        "",
        "## 2. Scope Summary",
        "",
        f"The ISMS scope covers {total} controls across {ctx.total_domains} domains "
        f"of the Secure Controls Framework. {ctx.implemented_count()} of those "
        f"({ctx.coverage_percent()}%) are implemented, monitored, or awaiting review.",
        "",
        "### Implementation Status",
        "",
        "| Status | Count | Percentage |",
        "|--------|-------|------------|",
    ]
    for status, count in sorted(
        ctx.status_distribution.items(), key=lambda kv: (-kv[1], kv[0])
    ):
        lines.append(f"| {status_label(status)} | {count} | {_pct(count, total)} |")

    lines += [
        "",
        "### Maturity Distribution",
        "",
        "| Level | Count | Percentage |",
        "|-------|-------|------------|",
    ]
    for level, count in sorted(
        ctx.maturity_distribution.items(), key=lambda kv: _maturity_sort_key(kv[0])
    ):
        lines.append(f"| {_cell(level)} | {count} | {_pct(count, total)} |")

    lines += ["", "## 3. Controls", ""]
    for index, bundle in enumerate(ctx.domains, start=1):
        lines += _soa_domain_section(bundle, index)

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Control Status Report
# ---------------------------------------------------------------------------


def render_control_status(ctx: OrganisationContext, **_: Any) -> str:
    """Control Status Report — progress, per domain, plus the exceptions.

    The exceptions section is the point of the document. A completion
    percentage tells a board nothing actionable; a list of unowned or stalled
    controls does.
    """
    total = ctx.total_scoped_controls
    lines = _header(ctx, "Control Status Report")

    lines += [
        "## 1. Overview",
        "",
        f"{ctx.implemented_count()} of {total} scoped controls "
        f"({ctx.coverage_percent()}%) are implemented, monitored, or ready for review.",
        "",
        "## 2. Progress by Domain",
        "",
        "| Domain | Controls | Implemented | Coverage |",
        "|--------|----------|-------------|----------|",
    ]
    for bundle in ctx.domains:
        done = sum(
            1 for c in bundle.controls
            if (c.implementation_status or "") in IMPLEMENTED_STATUSES
        )
        lines.append(
            f"| {_cell(bundle.domain.identifier)} — {_cell(bundle.domain.name)} | "
            f"{len(bundle.controls)} | {done} | {_pct(done, len(bundle.controls))} |"
        )

    unowned = [c for c in ctx.all_controls if not c.owner]
    not_started = [
        c for c in ctx.all_controls
        if (c.implementation_status or "not_started") == "not_started"
    ]

    lines += ["", "## 3. Exceptions", ""]

    lines += [f"### 3.1 Controls Without an Owner ({len(unowned)})", ""]
    if unowned:
        lines += [
            "| SCF ID | Control | Domain | Status |",
            "|--------|---------|--------|--------|",
        ]
        for c in unowned:
            lines.append(
                f"| {_cell(c.scf_id)} | {_cell(c.control_name)} | "
                f"{_cell(c.domain_identifier)} | {status_label(c.implementation_status)} |"
            )
    else:
        lines.append("Every scoped control has a named owner.")

    lines += ["", f"### 3.2 Controls Not Started ({len(not_started)})", ""]
    if not_started:
        lines += [
            "| SCF ID | Control | Domain | Owner |",
            "|--------|---------|--------|-------|",
        ]
        for c in not_started:
            lines.append(
                f"| {_cell(c.scf_id)} | {_cell(c.control_name)} | "
                f"{_cell(c.domain_identifier)} | {_cell(c.owner)} |"
            )
    else:
        lines.append("Work has begun on every scoped control.")

    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Risk Treatment Plan
# ---------------------------------------------------------------------------


def render_risk_treatment(ctx: OrganisationContext, **_: Any) -> str:
    """Risk Treatment Plan — the risk register with treatment decisions."""
    lines = _header(ctx, "Risk Treatment Plan")

    profile = ctx.risk_profile or {}
    lines += [
        "## 1. Risk Appetite",
        "",
    ]
    if profile:
        lines += [
            "| Threshold | Value |",
            "|-----------|-------|",
            f"| Low (up to) | {_cell(profile.get('low_max'))} |",
            f"| Medium (up to) | {_cell(profile.get('medium_max'))} |",
            f"| High (up to) | {_cell(profile.get('high_max'))} |",
            f"| Acceptable risk level | {_cell(profile.get('acceptable_risk_level'))} |",
            f"| Auto-escalate above | {_cell(profile.get('auto_escalate_above'))} |",
            "",
        ]
    else:
        lines += [
            f"{ctx.name} has not configured risk thresholds. Scores below are "
            "reported without a banding.",
            "",
        ]

    risks = ctx.risk_assessments
    lines += [
        "## 2. Risk Register",
        "",
        f"{len(risks)} risks are recorded.",
        "",
    ]
    if risks:
        lines += [
            "| Risk | Inherent | Residual | Treatment | Owner | Due |",
            "|------|----------|----------|-----------|-------|-----|",
        ]
        for r in sorted(
            risks, key=lambda r: -(r.get("residual_risk_score") or 0)
        ):
            lines.append(
                f"| {_cell(r.get('risk_code'))} | "
                f"{_cell(r.get('inherent_risk_score'))} ({_cell(r.get('inherent_risk_level'))}) | "
                f"{_cell(r.get('residual_risk_score'))} ({_cell(r.get('residual_risk_level'))}) | "
                f"{status_label(r.get('treatment_status'))} | "
                f"{_cell(r.get('owner'))} | {_cell(r.get('treatment_due_date'))} |"
            )
        lines.append("")

        with_plan = [r for r in risks if (r.get("treatment_plan") or "").strip()]
        lines += ["## 3. Treatment Plans", ""]
        if with_plan:
            for r in with_plan:
                lines += [
                    f"### {r.get('risk_code')}",
                    "",
                    str(r.get("treatment_plan")).strip(),
                    "",
                ]
        else:
            lines += ["No treatment plans have been recorded against these risks.", ""]
    else:
        lines += [
            "No risks have been recorded. A risk treatment plan cannot be "
            "evidenced until the risk register is populated.",
            "",
        ]

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Evidence Schedule
# ---------------------------------------------------------------------------


def render_evidence_schedule(ctx: OrganisationContext, **_: Any) -> str:
    """Evidence Schedule — what must be collected, by whom, how often.

    Tracking state comes from the organisation's evidence register, so a row
    marked untracked is a real gap rather than a formatting artefact.
    """
    lines = _header(ctx, "Evidence Schedule")

    tracking = {e["evidence_id"]: e for e in ctx.evidence_items}

    # One row per required artefact, not one per control — an artefact that
    # satisfies eleven controls is collected once.
    required: Dict[str, Dict[str, Any]] = {}
    for c in ctx.all_controls:
        for ev in c.evidence_mappings:
            entry = required.setdefault(ev["evidence_id"], {
                "evidence_id": ev["evidence_id"],
                "title": ev.get("title"),
                "area_of_focus": ev.get("area_of_focus"),
                "controls": [],
            })
            entry["controls"].append(c.scf_id)

    tracked = sum(
        1 for eid in required if tracking.get(eid, {}).get("is_tracked")
    )
    lines += [
        "## 1. Overview",
        "",
        f"The scoped controls require {len(required)} distinct evidence artefacts. "
        f"{tracked} of those are currently tracked.",
        "",
        "## 2. Schedule",
        "",
        "| Evidence | Artefact | Controls | Tracked | Method | Owner | Frequency |",
        "|----------|----------|----------|---------|--------|-------|-----------|",
    ]
    for eid in sorted(required):
        entry = required[eid]
        track = tracking.get(eid, {})
        lines.append(
            f"| {_cell(eid)} | {_cell(entry['title'], 60)} | "
            f"{len(entry['controls'])} | "
            f"{'Yes' if track.get('is_tracked') else 'No'} | "
            f"{_cell(track.get('method_of_collection'))} | "
            f"{_cell(track.get('owner'))} | {_cell(track.get('frequency'))} |"
        )

    untracked = [eid for eid in sorted(required) if not tracking.get(eid, {}).get("is_tracked")]
    lines += ["", f"## 3. Untracked Evidence ({len(untracked)})", ""]
    if untracked:
        lines += [
            "The following artefacts are required by scoped controls but are not "
            "yet being collected.",
            "",
        ]
        for eid in untracked:
            lines.append(
                f"- **{eid}** — {_cell(required[eid]['title'])} "
                f"({len(required[eid]['controls'])} controls)"
            )
    else:
        lines.append("Every required artefact is tracked.")

    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Maturity Assessment Report
# ---------------------------------------------------------------------------


def render_maturity_report(ctx: OrganisationContext, **_: Any) -> str:
    """Maturity Assessment Report — capability maturity by domain."""
    total = ctx.total_scoped_controls
    lines = _header(ctx, "Maturity Assessment Report")

    lines += [
        "## 1. Overall Distribution",
        "",
        "| Level | Count | Percentage |",
        "|-------|-------|------------|",
    ]
    for level, count in sorted(
        ctx.maturity_distribution.items(), key=lambda kv: _maturity_sort_key(kv[0])
    ):
        lines.append(f"| {_cell(level)} | {count} | {_pct(count, total)} |")

    lines += [
        "",
        "## 2. Maturity by Domain",
        "",
        "| Domain | Controls | " + " | ".join(MATURITY_ORDER) + " | Unrated |",
        "|--------|----------|" + "|".join(["------"] * len(MATURITY_ORDER)) + "|---------|",
    ]
    for bundle in ctx.domains:
        counts = bundle.maturity_breakdown
        cells = [str(counts.get(level, 0)) for level in MATURITY_ORDER]
        unrated = sum(v for k, v in counts.items() if k not in MATURITY_ORDER)
        lines.append(
            f"| {_cell(bundle.domain.identifier)} — {_cell(bundle.domain.name)} | "
            f"{len(bundle.controls)} | " + " | ".join(cells) + f" | {unrated} |"
        )

    low = [
        c for c in ctx.all_controls
        if (c.maturity_level or "") in ("L0", "L1", "")
    ]
    lines += ["", f"## 3. Controls Below L2 ({len(low)})", ""]
    if low:
        lines += [
            "These controls are performed informally or not at all. They are the "
            "gap between a documented ISMS and an operating one.",
            "",
            "| SCF ID | Control | Domain | Maturity | Owner |",
            "|--------|---------|--------|----------|-------|",
        ]
        for c in low:
            lines.append(
                f"| {_cell(c.scf_id)} | {_cell(c.control_name)} | "
                f"{_cell(c.domain_identifier)} | {_cell(c.maturity_level)} | "
                f"{_cell(c.owner)} |"
            )
    else:
        lines.append("Every scoped control is rated L2 or above.")

    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
