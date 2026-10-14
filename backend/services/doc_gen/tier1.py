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

import hashlib
import json
from typing import Any, Dict, List, Optional

from .context import IMPLEMENTED_STATUSES, DomainWithControls, EnrichedControl, OrganisationContext

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


def _tally(count: int, noun: str) -> str:
    """"1 control" / "12 controls" -- a counted noun that agrees with its count.

    Trivial, and worth having: this text goes into a heading of a document an
    auditor reads, and "(1 controls)" in a Statement of Applicability is the
    kind of detail that makes a reader distrust everything around it.
    """
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


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
    # Omitted entirely when unset — an "Industry:" label with nothing after it
    # reads as a data error in a document handed to an auditor.
    if ctx.industry:
        lines.append(f"**Industry:** {ctx.industry}")
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
        f"### {index}. {domain.identifier} — {domain.name} ({_tally(len(controls), 'control')})",
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
                f"({_tally(len(required[eid]['controls']), 'control')})"
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

    lines += _target_gap_section(ctx)

    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def target_inputs_digest(ctx: OrganisationContext) -> str:
    """A short digest of the targets the Gap to Target section reads, or "".

    Targets are kept out of the document fingerprint's controls hash on
    purpose (adding them would mark every generated document stale). Folding
    this digest into the maturity report's prompt key instead means a
    changed target makes that one report regenerate rather than be skipped,
    and an organisation with no targets keeps the key it had before.
    """
    pairs = sorted(
        (c.scf_id, c.target_maturity, c.target_source, c.target_maturity_reason or "")
        for c in ctx.all_controls
        if c.target_maturity
    )
    if not pairs:
        return ""
    return hashlib.sha256(json.dumps(pairs).encode("utf-8")).hexdigest()[:16]


def _level_index(level: Optional[str]) -> Optional[int]:
    """Position of a CMM level, or None for anything that is not L0 to L5."""
    cleaned = (level or "").strip().upper()
    return MATURITY_ORDER.index(cleaned) if cleaned in MATURITY_ORDER else None


def _domain_code(scf_id: str) -> str:
    """The domain a control's target comes from: its scf_id prefix, as
    services/target_maturity.py ``domain_of`` reads it."""
    return (scf_id or "").split("-", 1)[0].strip().upper()


def _target_gap_section(ctx: OrganisationContext) -> List[str]:
    """Section 4: each control's maturity against its target (PIG R5).

    An unrated control with a target is below it: nothing recorded is not
    evidence of having reached anything. A control with no target is listed
    by count, because a gap cannot be measured against a target nobody set.
    """
    with_target = [c for c in ctx.all_controls if c.target_maturity]
    no_target = len(ctx.all_controls) - len(with_target)
    below = []
    for c in with_target:
        current = _level_index(c.maturity_level)
        target = _level_index(c.target_maturity)
        if target is not None and (current is None or current < target):
            below.append((c, current, target))

    lines = ["", "## 4. Gap to Target", ""]
    if not with_target:
        lines.append(
            "No target maturity is set. Set a target for each domain in the "
            "organisation settings to measure the gap."
        )
        return lines

    lines += [
        f"{len(with_target) - len(below)} of {_tally(len(with_target), 'control')} "
        f"with a target have reached it. {_tally(no_target, 'control')} "
        f"{'has' if no_target == 1 else 'have'} no target.",
        "",
        "| Domain | Controls | With target | At target | Below target |",
        "|--------|----------|-------------|-----------|--------------|",
    ]
    # Rows group by scf_id prefix, the attribution the targets and the gates
    # use, not by the catalogue's domain name, so a control always sits in the
    # row whose target it inherits.
    below_ids = {c.scf_id for c, _, _ in below}
    names = {(b.domain.identifier or "").strip().upper(): b.domain.name for b in ctx.domains}
    by_code: Dict[str, List[EnrichedControl]] = {}
    for c in ctx.all_controls:
        by_code.setdefault(_domain_code(c.scf_id), []).append(c)
    for code in sorted(by_code):
        members = by_code[code]
        targeted = [c for c in members if c.target_maturity]
        short = sum(1 for c in targeted if c.scf_id in below_ids)
        label = f"{_cell(code)} — {_cell(names[code])}" if names.get(code) else _cell(code)
        lines.append(
            f"| {label} | {len(members)} | {len(targeted)} | {len(targeted) - short} | {short} |"
        )

    lines += ["", f"### Controls Below Target ({len(below)})", ""]
    if not below:
        lines.append("Every control with a target has reached it.")
        return lines
    lines += [
        "| SCF ID | Control | Domain | Current | Target | Gap | Target set by |",
        "|--------|---------|--------|---------|--------|-----|---------------|",
    ]
    below.sort(key=lambda item: (-(item[2] - (item[1] if item[1] is not None else -1)), item[0].scf_id))
    for c, current, target in below:
        gap = f"{target - current} level{'s' if target - current != 1 else ''}" if current is not None else "unrated"
        source = (
            f"control: {_cell(c.target_maturity_reason, 60)}"
            if c.target_source == "control" else "domain"
        )
        lines.append(
            f"| {_cell(c.scf_id)} | {_cell(c.control_name)} | {_cell(_domain_code(c.scf_id))} | "
            f"{_cell(MATURITY_ORDER[current] if current is not None else 'Unrated')} | "
            f"{_cell(c.target_maturity)} | {gap} | {source} |"
        )
    return lines


# ---------------------------------------------------------------------------
# Governance Oversight Report (PIG R10)
# ---------------------------------------------------------------------------


def governance_inputs_digest(ctx: OrganisationContext) -> str:
    """A short digest of the governance figures the report reads.

    Deficiencies, risks, suppliers and validations are outside the document
    fingerprint, so without this a changed figure would leave the report
    skipped as unchanged. ``as_of`` is left out: the date alone moving on is
    not new information, and would regenerate the report every day.
    """
    figures = {k: v for k, v in (ctx.governance or {}).items() if k != "as_of"}
    return hashlib.sha256(
        json.dumps(figures, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def _implemented_in(status_counts: Dict[str, int]) -> int:
    return sum(status_counts.get(s, 0) for s in IMPLEMENTED_STATUSES)


def _change(now: int, before: Optional[int]) -> str:
    if before is None:
        return "—"
    delta = now - before
    return f"{delta:+d}" if delta else "no change"


def _counts_table(counts: Dict[str, int], heading: str) -> List[str]:
    lines = [f"| {heading} | Count |", "|---|---|"]
    for key, value in counts.items():
        lines.append(f"| {_cell(status_label(key))} | {value} |")
    return lines


def render_governance_report(ctx: OrganisationContext, **_: Any) -> str:
    """Governance Oversight Report — the figures a board needs for due care.

    One section per oversight question: are controls implemented and where
    are we deficient; has the risk profile changed; what did validation find
    and correct; which obligations are in scope. Then the red flags, then the
    review record. Every figure comes from ``ctx.governance``.
    """
    from services.due_care import DUE_SOON_DAYS, red_flags

    g = ctx.governance or {}
    lines = _header(ctx, "Governance Oversight Report")
    if not g:
        lines += ["No governance figures were available for this report.", ""]
        return "\n".join(lines).rstrip() + "\n"

    posture = g["posture"]
    in_scope = posture["in_scope"]
    deficiencies = posture["deficiencies"]
    previous = g.get("previous_snapshot")
    lines += [
        f"**Figures as of:** {g['as_of']}",
        f"**Recent activity window:** the last {g['recent_days']} days",
        "",
        "This report gives the board the figures it needs to oversee the "
        "control programme. It does not replace the board's judgement: each "
        "section closes with the question the board should answer.",
        "",
    ]

    # 1. Implementation and known deficiencies ----------------------------
    material = posture["materiality"]["material"]
    lines += [
        "## 1. Implementation and Known Deficiencies",
        "",
        "| Measure | Count |",
        "|---|---|",
        f"| Controls in scope | {in_scope} |",
        f"| Implemented | {g['implemented']} ({_pct(g['implemented'], in_scope)}) |",
        f"| Material controls | {material} |",
        f"| Material controls implemented | {g['material_implemented']} ({_pct(g['material_implemented'], material)}) |",
        f"| Open deficiencies | {deficiencies['open']} |",
        f"| Open material deficiencies | {deficiencies['material_open']} |",
        f"| Open deficiencies past due | {deficiencies['overdue']} |",
        f"| Open deficiencies without a due date | {deficiencies['without_due_date']} |",
        f"| Open deficiencies without an owner | {g['deficiencies_without_owner']} |",
        "",
    ]
    if posture["status"]:
        lines += ["### Controls by implementation status", ""]
        lines += _counts_table(posture["status"], "Status") + [""]
    open_material = g["material_open_deficiencies"]
    lines += [f"### Open material deficiencies ({len(open_material)})", ""]
    if open_material:
        lines += [
            "| Deficiency | Control | Status | Due | Owner |",
            "|---|---|---|---|---|",
        ]
        for d in open_material:
            lines.append(
                f"| {_cell(d['title'], 80)} | {_cell(d['scf_id'])} | {_cell(status_label(d['status']))} | "
                f"{_cell(d['due_date'])} | {_cell(d['owner'])} |"
            )
    else:
        lines.append("No material deficiency is open.")
    lines += [
        "",
        "**For the board:** are the controls we rely on in place, where do we "
        "know we fall short, and is the remediation owned, dated and resourced?",
        "",
    ]

    # 2. Change in the risk profile -----------------------------------------
    lines += ["## 2. Change in the Risk Profile", ""]
    if previous:
        before = previous["counts"]
        before_def = before.get("deficiencies", {})
        lines += [
            f"Compared with the posture snapshot of {previous['period']}.",
            "",
            "| Measure | Then | Now | Change |",
            "|---|---|---|---|",
        ]
        for label, now_value, then_value in (
            ("Controls in scope", in_scope, before.get("in_scope")),
            ("Implemented", g["implemented"], _implemented_in(before.get("status", {}))),
            ("Material controls", material, before.get("materiality", {}).get("material")),
            ("Open deficiencies", deficiencies["open"], before_def.get("open")),
            ("Open material deficiencies", deficiencies["material_open"], before_def.get("material_open")),
            ("Open deficiencies past due", deficiencies["overdue"], before_def.get("overdue")),
        ):
            then_cell = "—" if then_value is None else str(then_value)
            lines.append(f"| {label} | {then_cell} | {now_value} | {_change(now_value, then_value)} |")
        lines.append("")
    else:
        lines += [
            "No posture snapshot from three or more months ago is on record, so "
            "the change cannot be shown yet. A snapshot is taken on the first of "
            "every month.",
            "",
        ]
    acceptance = g["risk_acceptance"]
    lines += [
        "### Risk acceptance",
        "",
        "| Accepted risks | Count |",
        "|---|---|",
        f"| Approved and current | {acceptance['approved']} |",
        f"| Approval expired | {acceptance['expired']} |",
        f"| Without an approval | {acceptance['unapproved']} |",
        "",
    ]
    if g["risk_treatment"]:
        lines += ["### Risks by treatment status", ""]
        lines += _counts_table(g["risk_treatment"], "Treatment") + [""]
    lines += ["### Suppliers", ""]
    if g["vendors_by_criticality"]:
        lines += _counts_table(g["vendors_by_criticality"], "Criticality") + [""]
    lines += [
        f"{_tally(g['vendors_added_recently'], 'supplier')} added in the last {g['recent_days']} days.",
        "",
        "**For the board:** has our exposure changed since the last review, "
        "have suppliers or partners brought new risk, and do the risks we "
        "accepted still sit within our appetite?",
        "",
    ]

    # 3. Validation: what failed and what was corrected -----------------------
    v = g["validation"]
    raised = g["deficiencies_raised_recently"]
    lines += [
        "## 3. Validation: What Failed and What Was Corrected",
        "",
        "| Control validation | Count |",
        "|---|---|",
        f"| Validated within their cadence | {v.get('current', 0) + v.get('due_soon', 0)} |",
        f"| Of which due again within {DUE_SOON_DAYS} days | {v.get('due_soon', 0)} |",
        f"| Validation overdue | {v.get('overdue', 0)} |",
        f"| Never validated | {v.get('never', 0)} |",
        f"| No validation cadence | {v.get('unknown_cadence', 0)} |",
        f"| Validated in the last {g['recent_days']} days | {g['validated_recently']} |",
        "",
        f"Deficiencies raised in the last {g['recent_days']} days: "
        f"{sum(raised.values())}"
        + (" (" + ", ".join(f"{_cell(status_label(k))} {n}" for k, n in raised.items()) + ")" if raised else "")
        + ".",
        f"Deficiencies closed in the same period: {g['deficiencies_completed_recently']}.",
        "",
        "**For the board:** what did testing find, which of our assumptions "
        "did it prove wrong, and what have we changed as a result?",
        "",
    ]

    # 4. Obligations in scope -------------------------------------------------
    basis = posture["basis"]
    lines += [
        "## 4. Obligations in Scope",
        "",
        "Mandatory (MCR) obligations come from law, regulation or contract; "
        "discretionary (DSR) ones are adopted by choice.",
        "",
    ]
    if g["frameworks"]:
        lines += ["| Framework | Basis |", "|---|---|"]
        for fw in g["frameworks"]:
            lines.append(f"| {_cell(fw['framework_id'])} | {_cell((fw['basis'] or 'not set').upper())} |")
        lines.append("")
    else:
        lines += ["No framework is in scope.", ""]
    lines += [
        f"Controls by basis: {basis['mcr']} mandatory, {basis['dsr']} discretionary, "
        f"{basis['none']} with no basis recorded.",
        "",
        "**For the board:** how do changes to the laws, regulations and "
        "frameworks we are bound by affect us, and what legal, financial and "
        "reputational harm would non-compliance cause?",
        "",
    ]

    # 5. Red flags --------------------------------------------------------------
    flags = red_flags(g)
    lines += ["## 5. Red Flags", ""]
    if flags:
        lines += [f"- {flag}" for flag in flags]
    else:
        lines.append("None of the due-care red flags this report checks for is raised.")
    lines += [
        "",
        "## 6. Review Record",
        "",
        "Approving this document in the platform records the board's review of "
        "these figures: the approver and the date are kept in the document's "
        "lifecycle history and the audit log.",
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"
