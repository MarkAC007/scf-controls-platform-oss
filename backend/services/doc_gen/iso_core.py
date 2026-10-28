"""
ISO/IEC 27001 core pack — the clause documents and registers.

Every renderer here is deterministic: it lays out facts the organisation has
recorded (the ISMS profile registers, the risk and deficiency registers, the
systems and vendor inventories) and the platform's own configuration (risk
thresholds, the document lifecycle). Nothing here calls a model and nothing
here rewrites SCF control text, so none of it is a derivative work.

Where a fact has not been recorded, the document says so with a visible
:data:`TBC` marker instead of filling the gap. A clause document that asserts
an interested party, objective or boundary nobody wrote down is worse than one
that admits the gap: the auditor finds the invention in the first interview.

The text cites clause numbers and never quotes the standard, whose text is
copyright.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Sequence

from .context import OrganisationContext
from .tier1 import _cell, _header, _level_index, _tally, status_label

#: Shown wherever the organisation has not recorded a fact.
TBC = "*[To be completed]*"


def _tbc(what: str) -> str:
    return f"*[To be completed: {what}]*"


def _text(value: Any, missing: str) -> str:
    value = (value or "").strip() if isinstance(value, str) else value
    return str(value) if value else _tbc(missing)


def _isms(ctx: OrganisationContext, key: str, default: Any = None) -> Any:
    profile = ctx.isms_profile or {}
    value = profile.get(key)
    return value if value is not None else default


def _done(lines: List[str]) -> str:
    return "\n".join(lines).rstrip() + "\n"


def _table(header: Sequence[str], rows: Iterable[Sequence[Any]]) -> List[str]:
    out = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    for row in rows:
        out.append("| " + " | ".join(_cell(v) for v in row) + " |")
    out.append("")
    return out


def _yes(flag: Any) -> str:
    return "Yes" if flag else "No"


def inputs_digest(ctx: OrganisationContext, *, exclude_generator: str = "") -> str:
    """Hash of the non-control inputs a core-pack document reads.

    The fingerprint covers controls and the prompt; a Tier 1 prompt is just the
    generator name. Without this, editing the interested-parties register would
    leave the Context document reporting "unchanged" and skipping regeneration.
    Only populated sections contribute, so each document hashes what it asked
    the context builder for and nothing more.
    """
    payload = {
        "isms": ctx.isms_profile,
        "risks": ctx.risk_assessments,
        "risk_profile": ctx.risk_profile,
        "systems": ctx.systems,
        "vendors": ctx.vendors,
        "deficiencies": ctx.deficiencies,
        "obligations": ctx.obligations,
        # A register of documents must not hash itself, or its own regeneration
        # would always look like a change.
        "documents": [
            {k: d.get(k) for k in ("title", "generator", "lifecycle_status", "generation_version")}
            for d in ctx.documents if d.get("generator") != exclude_generator
        ],
    }
    blob = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Shared sections — used by the individual documents and by the ISMS Manual
# ---------------------------------------------------------------------------


def _issue_rows(issues: List[Dict[str, Any]]) -> List[List[Any]]:
    return [
        [
            status_label(i.get("category")),
            i.get("issue"),
            i.get("effect") or TBC,
            i.get("response") or TBC,
            _yes(i.get("climate_related")),
        ]
        for i in issues
    ]


def _issues_section(ctx: OrganisationContext, level: str = "##") -> List[str]:
    issues = _isms(ctx, "context_issues", []) or []
    lines: List[str] = []
    for kind, label in (("external", "External Issues"), ("internal", "Internal Issues")):
        subset = [i for i in issues if i.get("kind") == kind]
        lines += [f"{level} {label}", ""]
        if subset:
            lines += _table(
                ["Category", "Issue", "Effect on the ISMS", "How it is addressed", "Climate-related"],
                _issue_rows(subset),
            )
        else:
            lines += [_tbc(f"record the {kind} issues relevant to the ISMS in the ISMS Profile"), ""]
    lines += [f"{level} Climate Change", ""]
    climate = [i for i in issues if i.get("climate_related")]
    if climate:
        lines += [
            f"{ctx.name} has determined that climate change is a relevant issue. "
            f"{_tally(len(climate), 'issue')} above {'is' if len(climate) == 1 else 'are'} "
            "marked as climate-related.",
            "",
        ]
    else:
        lines += [
            _tbc(
                "record whether climate change is a relevant issue, as ISO/IEC "
                "27001:2022 Amendment 1 (2024) asks; mark any climate-related issue "
                "in the ISMS Profile"
            ),
            "",
        ]
    return lines


def _parties_section(ctx: OrganisationContext, level: str = "##") -> List[str]:
    parties = _isms(ctx, "interested_parties", []) or []
    lines = [f"{level} Interested Parties and Their Requirements", ""]
    if not parties:
        return lines + [_tbc("record the interested parties and their requirements in the ISMS Profile"), ""]
    lines += _table(
        ["Interested party", "Internal / external", "Requirements", "Addressed through the ISMS", "How"],
        [
            [p.get("party"), status_label(p.get("kind")), p.get("requirements") or TBC,
             _yes(p.get("addressed_through_isms")),
             (p.get("how_addressed") or TBC) if p.get("addressed_through_isms") else "Not addressed through the ISMS"]
            for p in parties
        ],
    )
    return lines


def _objectives_section(ctx: OrganisationContext, level: str = "##") -> List[str]:
    objectives = _isms(ctx, "objectives", []) or []
    lines = [f"{level} Objectives", ""]
    if not objectives:
        return lines + [_tbc("record measurable information security objectives in the ISMS Profile"), ""]
    lines += _table(
        ["#", "Objective", "Measure", "Target", "Owner", "Due", "Status"],
        [
            [n, o.get("objective"), o.get("measure") or TBC, o.get("target") or TBC,
             o.get("owner") or TBC, o.get("due_date") or TBC, status_label(o.get("status"))]
            for n, o in enumerate(objectives, start=1)
        ],
    )
    return lines


def _communication_section(ctx: OrganisationContext, level: str = "##") -> List[str]:
    rows = _isms(ctx, "communication_plan", []) or []
    lines = [f"{level} Communication Plan", ""]
    if not rows:
        return lines + [_tbc("record what the ISMS communicates, when, to whom, by whom and how"), ""]
    lines += _table(
        ["What", "When", "With whom", "Who communicates", "How"],
        [[c.get("what"), c.get("when") or TBC, c.get("audience") or TBC,
          c.get("communicator") or TBC, c.get("method") or TBC] for c in rows],
    )
    return lines


def _audit_rows_section(ctx: OrganisationContext, level: str = "##") -> List[str]:
    audits = _isms(ctx, "audit_programme", []) or []
    lines = [f"{level} Audit Programme", ""]
    if not audits:
        return lines + [_tbc("plan the internal audits in the ISMS Profile"), ""]
    lines += _table(
        ["Area", "Planned period", "Auditor", "Criteria", "Status", "Findings"],
        [[a.get("area"), a.get("planned_period") or TBC, a.get("auditor") or TBC,
          a.get("criteria") or TBC, status_label(a.get("status")),
          a.get("findings") or ("—" if a.get("status") != "completed" else TBC)]
         for a in audits],
    )
    return lines


def _risk_bands(ctx: OrganisationContext) -> Dict[str, int]:
    profile = ctx.risk_profile or {}
    return {
        "low_max": int(profile.get("low_max") or 4),
        "medium_max": int(profile.get("medium_max") or 9),
        "high_max": int(profile.get("high_max") or 16),
    }


def _band(score: int, bands: Dict[str, int]) -> str:
    if score <= bands["low_max"]:
        return "Low"
    if score <= bands["medium_max"]:
        return "Medium"
    if score <= bands["high_max"]:
        return "High"
    return "Critical"


def _level_counts(risks: List[Dict[str, Any]], key: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for r in risks:
        level = (r.get(key) or "unscored").lower()
        out[level] = out.get(level, 0) + 1
    return out


# ---------------------------------------------------------------------------
# 4.3 ISMS Scope Statement
# ---------------------------------------------------------------------------


def render_isms_scope(ctx: OrganisationContext, **_: Any) -> str:
    scope = _isms(ctx, "scope", {}) or {}
    lines = _header(ctx, "ISMS Scope Statement")
    lines += [
        "## 1. Purpose",
        "",
        f"This statement defines the boundaries and applicability of {ctx.name}'s "
        "information security management system (ISO/IEC 27001, clause 4.3).",
        "",
        "## 2. The Organisation",
        "",
        _text(scope.get("organisation_description"), "describe what the organisation does"),
        "",
        "## 3. Boundaries",
        "",
        _text(scope.get("boundaries"), "state which business units, functions, people and information the ISMS covers"),
        "",
        "## 4. Locations",
        "",
    ]
    lines += [f"- {loc}" for loc in scope.get("locations") or []] or [_tbc("list the locations in scope")]
    lines += ["", "## 5. Products and Services", ""]
    lines += [f"- {svc}" for svc in scope.get("services") or []] or [_tbc("list the products and services in scope")]
    lines += ["", "## 6. Technology in Scope", ""]
    systems = [s for s in ctx.systems if (s.get("status") or "").lower() not in ("retired", "decommissioned")]
    if systems:
        lines += [f"{_tally(len(systems), 'system')} recorded in the Systems Registry are in scope.", ""]
        lines += _table(["System", "Type", "Status"],
                        [[s.get("name"), status_label(s.get("system_type")), status_label(s.get("status"))] for s in systems])
    else:
        lines += [_tbc("record the systems in scope in the Systems Registry"), ""]
    lines += [
        "## 7. Interfaces and Dependencies",
        "",
        _text(scope.get("interfaces"), "describe where activities in scope depend on, or connect to, activities outside it"),
        "",
    ]
    if ctx.vendors:
        lines += ["### External Service Providers", ""]
        lines += _table(["Supplier", "Category", "Criticality"],
                        [[v.get("name"), status_label(v.get("category")), status_label(v.get("criticality"))]
                         for v in ctx.vendors])
    lines += ["## 8. Issues, Requirements and Obligations Considered", ""]
    issues = len(_isms(ctx, "context_issues", []) or [])
    parties = len(_isms(ctx, "interested_parties", []) or [])
    lines += [
        f"The scope takes account of the {_tally(issues, 'issue')} and "
        f"{_tally(parties, 'interested party').replace('partys', 'parties')} recorded in the "
        "Context of the Organisation.",
        "",
    ]
    if ctx.obligations:
        lines += ["The following frameworks and obligations are in scope:", ""]
        lines += [f"- {o.get('name')}" for o in ctx.obligations] + [""]
    lines += ["## 9. Exclusions", ""]
    exclusions = scope.get("exclusions") or []
    if exclusions:
        lines += _table(["Excluded", "Justification"],
                        [[e.get("item"), e.get("justification") or TBC] for e in exclusions])
    else:
        lines += ["No exclusions have been recorded.", ""]
    lines += [
        "## 10. Controls",
        "",
        f"{_tally(ctx.total_scoped_controls, 'control')} across {_tally(ctx.total_domains, 'domain')} "
        "are in scope. Their applicability and implementation status are recorded in the "
        "Statement of Applicability.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 4.1 / 4.2 Context of the Organisation
# ---------------------------------------------------------------------------


def render_context_of_organisation(ctx: OrganisationContext, **_: Any) -> str:
    lines = _header(ctx, "Context of the Organisation")
    lines += [
        "## Purpose",
        "",
        f"This document records the internal and external issues relevant to {ctx.name}'s "
        "purpose that affect its ability to achieve the intended outcomes of its ISMS "
        "(ISO/IEC 27001, clause 4.1), and the interested parties and requirements relevant "
        "to the ISMS (clause 4.2).",
        "",
    ]
    lines += _issues_section(ctx)
    lines += _parties_section(ctx)
    lines += [
        "## Review",
        "",
        "Issues and interested-party requirements are reviewed as an input to each "
        "management review, and whenever a significant change affects the organisation.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 6.2 Information Security Objectives
# ---------------------------------------------------------------------------


def render_security_objectives(ctx: OrganisationContext, **_: Any) -> str:
    objectives = _isms(ctx, "objectives", []) or []
    lines = _header(ctx, "Information Security Objectives")
    lines += [
        "## 1. Purpose",
        "",
        f"This document sets out {ctx.name}'s information security objectives and how "
        "each will be achieved (ISO/IEC 27001, clause 6.2). Objectives are consistent "
        "with the Information Security Policy, take account of the risk assessment and "
        "treatment results, and are monitored, communicated and updated.",
        "",
    ]
    lines += _objectives_section(ctx, "## 2.")
    if objectives:
        lines += ["## 3. Planning", ""]
        for n, o in enumerate(objectives, start=1):
            lines += [
                f"### Objective {n}",
                "",
                f"**Objective:** {_cell(o.get('objective'))}",
                "",
                f"- **What will be done:** {_cell(o.get('objective'))}",
                f"- **Resources:** {o.get('resources') or TBC}",
                f"- **Responsible:** {o.get('owner') or TBC}",
                f"- **Completion:** {o.get('due_date') or TBC}",
                f"- **How results are evaluated:** {o.get('evaluation') or TBC}",
                "",
            ]
    below = [
        c for c in ctx.all_controls
        if c.target_maturity and _level_index(c.maturity_level) is not None
        and _level_index(c.target_maturity) is not None
        and _level_index(c.maturity_level) < _level_index(c.target_maturity)
    ]
    lines += [
        "## 4. Supporting Indicators",
        "",
        "These figures come from the platform and inform the objectives; they are not "
        "objectives in themselves.",
        "",
        "| Indicator | Value |",
        "|---|---|",
        f"| Controls implemented | {ctx.implemented_count()} of {ctx.total_scoped_controls} "
        f"({ctx.coverage_percent()}%) |",
        f"| Controls below their target maturity | {len(below)} |",
        "",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 7.4 Communication Plan
# ---------------------------------------------------------------------------


def render_communication_plan(ctx: OrganisationContext, **_: Any) -> str:
    lines = _header(ctx, "Communication Plan")
    lines += [
        "## 1. Purpose",
        "",
        f"This plan determines the internal and external communications relevant to "
        f"{ctx.name}'s ISMS: what is communicated, when, with whom, who communicates it "
        "and how (ISO/IEC 27001, clause 7.4).",
        "",
    ]
    lines += _communication_section(ctx, "## 2.")
    lines += [
        "## 3. Records",
        "",
        "Records of significant communications (policy acknowledgements, incident and "
        "breach notifications, management review outputs) are retained as evidence.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 9.2 Internal Audit Programme
# ---------------------------------------------------------------------------

#: ISMS clauses an audit programme is expected to cover over its cycle.
CLAUSES = [
    ("4", "Context of the organisation"), ("5", "Leadership"), ("6", "Planning"),
    ("7", "Support"), ("8", "Operation"), ("9", "Performance evaluation"),
    ("10", "Improvement"),
]


def _mentioned(audits: List[Dict[str, Any]], *needles: str) -> bool:
    text = " ".join(f"{a.get('area', '')} {a.get('criteria', '')}" for a in audits).lower()
    return any(n.lower() in text for n in needles if n)


def render_internal_audit_programme(ctx: OrganisationContext, **_: Any) -> str:
    audits = _isms(ctx, "audit_programme", []) or []
    lines = _header(ctx, "Internal Audit Programme")
    lines += [
        "## 1. Purpose",
        "",
        f"This programme plans the internal audits that provide information on whether "
        f"{ctx.name}'s ISMS conforms to its own requirements and to ISO/IEC 27001, and is "
        "effectively implemented and maintained (clause 9.2).",
        "",
    ]
    lines += _audit_rows_section(ctx, "## 2.")
    lines += [
        "## 3. Coverage",
        "",
        "Coverage below is matched against the text of each audit's area and criteria, "
        "so it is a prompt for review rather than a determination.",
        "",
        "| Area | Named in the programme |",
        "|---|---|",
    ]
    for number, name in CLAUSES:
        lines.append(f"| Clause {number} — {name} | {_yes(_mentioned(audits, f'clause {number}', name))} |")
    for d in ctx.domains:
        lines.append(
            f"| {d.domain.identifier} — {d.domain.name} | "
            f"{_yes(_mentioned(audits, d.domain.identifier, d.domain.name))} |"
        )
    lines += [
        "",
        "## 4. Conducting Audits",
        "",
        "1. Each audit has defined criteria and scope, recorded above.",
        "2. Auditors are selected so that no one audits their own work.",
        "3. Results are reported to the relevant management and recorded as findings.",
        "4. Nonconformities are raised in the Nonconformity and Corrective Action Register "
        "and followed to closure.",
        "5. Audit results are an input to management review.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 6.1.2 / 6.1.3 Risk Methodology and 8.2 Risk Assessment Report
# ---------------------------------------------------------------------------


def render_risk_methodology(ctx: OrganisationContext, **_: Any) -> str:
    bands = _risk_bands(ctx)
    profile = ctx.risk_profile or {}
    lines = _header(ctx, "Risk Assessment and Treatment Methodology")
    lines += [
        "## 1. Purpose",
        "",
        f"This methodology defines how {ctx.name} assesses and treats information security "
        "risks so that repeated assessments produce consistent, valid and comparable "
        "results (ISO/IEC 27001, clauses 6.1.2 and 6.1.3).",
        "",
        "## 2. Risk Identification",
        "",
        "Risks are identified against the scoped controls and recorded in the risk "
        "register with a code, description and risk owner. A risk owner is accountable "
        "for its assessment and treatment.",
        "",
        "## 3. Risk Analysis",
        "",
        "Each risk is scored for likelihood and impact on a scale of 1 (lowest) to 5 "
        "(highest). The risk score is likelihood multiplied by impact (1 to 25). Inherent "
        "risk is scored before controls, and residual risk with current controls in place.",
        "",
        "## 4. Risk Evaluation",
        "",
        "Scores are banded with the organisation's configured thresholds:",
        "",
        "| Band | Score |",
        "|---|---|",
        f"| Low | 1–{bands['low_max']} |",
        f"| Medium | {bands['low_max'] + 1}–{bands['medium_max']} |",
        f"| High | {bands['medium_max'] + 1}–{bands['high_max']} |",
        f"| Critical | {bands['high_max'] + 1}–25 |",
        "",
        "### Risk Matrix",
        "",
        "| Likelihood \\ Impact | 1 | 2 | 3 | 4 | 5 |",
        "|---|---|---|---|---|---|",
    ]
    for likelihood in range(5, 0, -1):
        cells = [f"{likelihood * impact} {_band(likelihood * impact, bands)}" for impact in range(1, 6)]
        lines.append(f"| **{likelihood}** | " + " | ".join(cells) + " |")
    lines += [
        "",
        "## 5. Risk Acceptance Criteria",
        "",
    ]
    if profile:
        lines += [
            f"- Risks at or below **{status_label(profile.get('acceptable_risk_level'))}** may be "
            "accepted without further treatment.",
            f"- Risks above **{status_label(profile.get('auto_escalate_above'))}** are escalated "
            "to senior management.",
        ]
    else:
        lines += [_tbc("configure the acceptable risk level and escalation threshold in the risk profile")]
    lines += [
        "- Accepting a risk is recorded by an administrator with a written rationale and an "
        "expiry date, after which the acceptance must be renewed or the risk treated.",
        "",
        "## 6. Risk Treatment",
        "",
        "For each risk the owner selects a treatment: modify the risk by implementing or "
        "improving controls, retain (accept) it within the acceptance criteria, avoid it by "
        "stopping the activity, or share it, for example through insurance or a supplier "
        "contract. Treatment progress is tracked through these statuses:",
        "",
        "| Status | Meaning |",
        "|---|---|",
        "| Identified | Recorded, not yet analysed |",
        "| Analysed | Scored for likelihood and impact |",
        "| Treating | Treatment under way |",
        "| Treated | Treatment complete; residual risk scored |",
        "| Accepted | Retained within the acceptance criteria, with recorded approval |",
        "| Monitoring | Under ongoing review |",
        "",
        "Controls selected for treatment are compared against the Statement of "
        "Applicability so that no necessary control is omitted, and the Risk Treatment Plan "
        "records the treatment, owner and due date for each risk.",
        "",
        "## 7. Review",
        "",
        "Risk assessments are repeated at planned intervals and when significant changes "
        "are proposed or occur. Results are an input to management review.",
    ]
    return _done(lines)


def render_risk_assessment_report(ctx: OrganisationContext, **_: Any) -> str:
    risks = ctx.risk_assessments
    lines = _header(ctx, "Risk Assessment Report")
    lines += [
        "## 1. Summary",
        "",
        f"{_tally(len(risks), 'risk')} {'is' if len(risks) == 1 else 'are'} recorded "
        "(ISO/IEC 27001, clause 8.2). Scoring follows the Risk Assessment and Treatment "
        "Methodology.",
        "",
    ]
    if not risks:
        return _done(lines + [_tbc("populate the risk register"), ""])
    inherent = _level_counts(risks, "inherent_risk_level")
    residual = _level_counts(risks, "residual_risk_level")
    levels = ["critical", "high", "medium", "low", "unscored"]
    lines += _table(
        ["Level", "Inherent", "Residual"],
        [[status_label(lv), inherent.get(lv, 0), residual.get(lv, 0)]
         for lv in levels if inherent.get(lv) or residual.get(lv)],
    )
    treatment: Dict[str, int] = {}
    for r in risks:
        key = r.get("treatment_status") or "identified"
        treatment[key] = treatment.get(key, 0) + 1
    lines += ["## 2. Treatment Status", ""]
    lines += _table(["Status", "Risks"], [[status_label(k), v] for k, v in sorted(treatment.items())])
    lines += ["## 3. Risk Register", ""]
    lines += _table(
        ["Risk", "Likelihood", "Impact", "Inherent", "Residual", "Treatment", "Owner"],
        [[r.get("risk_code"), r.get("likelihood"), r.get("impact"),
          f"{_cell(r.get('inherent_risk_score'))} ({_cell(r.get('inherent_risk_level'))})",
          f"{_cell(r.get('residual_risk_score'))} ({_cell(r.get('residual_risk_level'))})",
          status_label(r.get("treatment_status")), r.get("owner")]
         for r in sorted(risks, key=lambda r: -(r.get("residual_risk_score") or r.get("inherent_risk_score") or 0))],
    )
    unowned = [r for r in risks if not r.get("owner") or r.get("owner") == "—"]
    if unowned:
        lines += [
            "## 4. Observations",
            "",
            f"- {_tally(len(unowned), 'risk')} {'has' if len(unowned) == 1 else 'have'} no risk owner.",
            "",
        ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 10.2 Nonconformity and Corrective Action Register
# ---------------------------------------------------------------------------


def render_nonconformity_register(ctx: OrganisationContext, **_: Any) -> str:
    items = ctx.deficiencies
    open_items = [d for d in items if d.get("status") not in ("completed", "cancelled")]
    lines = _header(ctx, "Nonconformity and Corrective Action Register")
    lines += [
        "## 1. Procedure",
        "",
        "When a nonconformity is found — by internal audit, control validation, a capability "
        "test, an incident or any other source — it is recorded here and:",
        "",
        "1. Reacted to: controlled and corrected, and its consequences dealt with.",
        "2. Analysed for its root cause, and whether similar nonconformities exist or could occur.",
        "3. Corrected by an action proportionate to its effects, with an owner and a due date.",
        "4. Verified as effective before it is closed.",
        "",
        "Changes to the ISMS needed as a result are made through the normal change process "
        "(ISO/IEC 27001, clause 10.2).",
        "",
        "## 2. Summary",
        "",
        "| | Count |",
        "|---|---|",
        f"| Recorded | {len(items)} |",
        f"| Open | {len(open_items)} |",
        f"| Open without a root cause | {sum(1 for d in open_items if not (d.get('root_cause') or '').strip())} |",
        f"| Closed | {sum(1 for d in items if d.get('status') == 'completed')} |",
        "",
        "## 3. Register",
        "",
    ]
    if not items:
        return _done(lines + ["No nonconformities have been recorded.", ""])
    lines += _table(
        ["#", "Raised", "Nonconformity", "Control", "Source", "Priority", "Root cause",
         "Owner", "Due", "Status", "Closed"],
        [[n, d.get("raised"), d.get("title"), d.get("scf_id"), status_label(d.get("source")),
          status_label(d.get("priority")), d.get("root_cause") or TBC, d.get("owner"),
          d.get("due_date"), status_label(d.get("status")), d.get("completed_date")]
         for n, d in enumerate(items, start=1)],
    )
    return _done(lines)


# ---------------------------------------------------------------------------
# Registers: assets, suppliers, legal and regulatory
# ---------------------------------------------------------------------------


def render_asset_inventory(ctx: OrganisationContext, **_: Any) -> str:
    lines = _header(ctx, "Asset Inventory")
    lines += [
        "## 1. Purpose",
        "",
        "The inventory of information and other associated assets, with their owners, "
        "maintained from the Systems Registry.",
        "",
    ]
    if not ctx.systems:
        return _done(lines + [_tbc("record systems in the Systems Registry"), ""])
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for s in ctx.systems:
        by_type.setdefault(status_label(s.get("system_type")), []).append(s)
    lines += [f"## 2. Inventory ({_tally(len(ctx.systems), 'system')})", ""]
    for kind in sorted(by_type):
        lines += [f"### {kind}", ""]
        lines += _table(["System", "Category", "Status", "Description"],
                        [[s.get("name"), status_label(s.get("category")) if s.get("category") else "—",
                          status_label(s.get("status")), _cell(s.get("description"), 160)]
                         for s in by_type[kind]])
    return _done(lines)


def render_supplier_register(ctx: OrganisationContext, **_: Any) -> str:
    lines = _header(ctx, "Supplier Register")
    lines += [
        "## 1. Purpose",
        "",
        "Suppliers and service providers with access to, or that process, the "
        "organisation's information, maintained from the Vendor Inventory.",
        "",
    ]
    if not ctx.vendors:
        return _done(lines + [_tbc("record suppliers in the Vendor Inventory"), ""])
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    vendors = sorted(ctx.vendors, key=lambda v: (order.get((v.get("criticality") or "").lower(), 9), v.get("name") or ""))
    lines += [f"## 2. Register ({_tally(len(vendors), 'supplier')})", ""]
    lines += _table(["Supplier", "Category", "Criticality", "Status", "Website"],
                    [[v.get("name"), status_label(v.get("category")) if v.get("category") else "—",
                      status_label(v.get("criticality")) if v.get("criticality") else "Unrated",
                      status_label(v.get("status")), v.get("website")] for v in vendors])
    return _done(lines)


_BASIS = {"mcr": "Minimum compliance requirement", "dsr": "Discretionary security requirement"}


def render_legal_register(ctx: OrganisationContext, **_: Any) -> str:
    lines = _header(ctx, "Legal, Regulatory and Contractual Requirements Register")
    lines += [
        "## 1. Purpose",
        "",
        "The legal, statutory, regulatory and contractual requirements relevant to "
        "information security, and the organisation's approach to meeting them.",
        "",
        "## 2. Frameworks and Obligations in Scope",
        "",
    ]
    if ctx.obligations:
        lines += _table(
            ["Framework", "Basis", "Obligation type", "Source"],
            [[o.get("name"), _BASIS.get((o.get("basis") or "").lower(), TBC),
              status_label(o.get("obligation_type")) if o.get("obligation_type") else TBC,
              o.get("obligation_source") or TBC] for o in ctx.obligations],
        )
    else:
        lines += [_tbc("scope the frameworks and obligations that apply in Framework Scoping"), ""]
    # Every external party's requirements, not a keyword guess at which ones
    # are regulators: "Information Commissioner's Office" names no keyword, and
    # contractual requirements from customers belong here too.
    external = [
        p for p in (_isms(ctx, "interested_parties", []) or [])
        if p.get("kind") == "external" and (p.get("requirements") or "").strip()
    ]
    lines += ["## 3. Requirements of External Interested Parties", ""]
    if external:
        lines += _table(["Party", "Requirements"], [[p.get("party"), p.get("requirements")] for p in external])
    else:
        lines += [_tbc("record external interested parties and their requirements in the ISMS Profile"), ""]
    lines += [
        "## 4. Review",
        "",
        "This register is reviewed at least annually and whenever a new obligation is "
        "identified.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 7.5 Document and Record Control
# ---------------------------------------------------------------------------

_LIFECYCLE = [
    ("Draft", "Being written or regenerated", "Editor"),
    ("In review", "Submitted for approval", "Editor submits"),
    ("Approved", "Approved for use", "Administrator"),
    ("Published", "Issued to its audience", "Administrator"),
]


def render_document_control(ctx: OrganisationContext, **_: Any) -> str:
    lines = _header(ctx, "Document and Record Control Procedure")
    lines += [
        "## 1. Purpose",
        "",
        f"How {ctx.name} creates, updates and controls the documented information its ISMS "
        "requires (ISO/IEC 27001, clause 7.5).",
        "",
        "## 2. Creating and Updating",
        "",
        "ISMS documents are maintained in the SCF Controls Platform. Each carries a title, "
        "a version and a change history. A regenerated document keeps every human edit "
        "unless the person resolving a conflict chooses otherwise, and each generation is "
        "kept as an immutable version.",
        "",
        "## 3. Review and Approval",
        "",
        "| Status | Meaning | Who moves it here |",
        "|---|---|---|",
    ]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in _LIFECYCLE]
    lines += [
        "",
        "Editing or regenerating an approved or published document returns it to review: an "
        "approval given to one text is not an approval of another. Every transition is "
        "recorded with who made it and when.",
        "",
        "## 4. Distribution, Access and Retention",
        "",
        "Documents are available to members of the organisation according to their role, "
        "and can be exported for distribution. Superseded versions are retained in each "
        "document's history. Records are retained for " + _tbc("state the retention period") + ".",
        "",
        "## 5. Documents of External Origin",
        "",
        _tbc("describe how external documents (standards, contracts, supplier reports) are identified and controlled"),
        "",
        "## 6. Document Register",
        "",
    ]
    docs = [d for d in ctx.documents if d.get("generator") != "document-control"]
    if docs:
        lines += _table(["Document", "Type", "Version", "Status"],
                        [[d.get("title"), status_label(d.get("document_type")),
                          d.get("generation_version"), status_label(d.get("lifecycle_status"))] for d in docs])
    else:
        lines += ["No other documents have been generated yet.", ""]
    return _done(lines)


# ---------------------------------------------------------------------------
# 5.3 Roles, Responsibilities and Authorities (port of function-role-mapping)
# ---------------------------------------------------------------------------

_FUNCTIONS = [
    (("ciso", "chief information security"), "Chief Information Security Officer (CISO) Function"),
    (("cto", "chief technology"), "Chief Technology Officer (CTO) Function"),
    (("cio", "chief information officer"), "Chief Information Officer (CIO) Function"),
    (("dpo", "data protection officer", "privacy"), "Data Protection Officer (DPO) Function"),
    (("risk manager", "risk management"), "Risk Management Function"),
    (("it security", "security manager"), "IT Security Management Function"),
    (("it director", "it manager"), "IT Director Function"),
    (("infrastructure",), "Infrastructure Management Function"),
    (("network",), "Network Security Function"),
    (("hr", "human resources"), "Human Resources Function"),
    (("compliance", "grc"), "Compliance & GRC Function"),
    (("legal", "counsel"), "Legal Function"),
    (("security analyst", "soc"), "Security Operations Function"),
    (("operations", "ops", "devops", "sre"), "Operations Function"),
    (("development", "engineering", "developer", "architect"), "Engineering Function"),
    (("audit",), "Internal Audit Function"),
    (("facilities", "physical"), "Facilities Management Function"),
    (("procurement", "vendor"), "Procurement & Vendor Management Function"),
    (("training", "awareness"), "Security Awareness & Training Function"),
]


def infer_function(owner: str) -> str:
    """The ISMS function an owner's name or title suggests.

    Keyword matching, as in scf-doc-gen. Short keywords ("hr", "ops", "cto")
    match whole words only, so "Christopher" is not the HR function.
    """
    import re

    lower = (owner or "").lower()
    words = set(re.findall(r"[a-z]+", lower))
    for keys, function in _FUNCTIONS:
        for key in keys:
            if (" " in key and key in lower) or (" " not in key and (key in words if len(key) <= 4 else key in lower)):
                return function
    return "Information Security Function"


def render_roles_responsibilities(ctx: OrganisationContext, **_: Any) -> str:
    owner_controls: Dict[str, int] = {}
    owner_domains: Dict[str, set] = {}
    unassigned = 0
    for d in ctx.domains:
        for c in d.controls:
            if c.owner:
                owner_controls[c.owner] = owner_controls.get(c.owner, 0) + 1
                owner_domains.setdefault(c.owner, set()).add(d.domain.identifier)
            else:
                unassigned += 1
    groups: Dict[str, Dict[str, Any]] = {}
    for owner, count in owner_controls.items():
        g = groups.setdefault(infer_function(owner), {"owners": [], "controls": 0, "domains": set()})
        g["owners"].append(owner)
        g["controls"] += count
        g["domains"] |= owner_domains[owner]

    lines = _header(ctx, "Roles, Responsibilities and Authorities")
    lines += [
        "## 1. Purpose",
        "",
        f"This register assigns the responsibilities and authorities for roles relevant to "
        f"information security at {ctx.name} (ISO/IEC 27001, clause 5.3). ISMS documents refer "
        "to **functions** rather than job titles, so they stay valid when people or titles "
        "change; this register records who currently fulfils each function.",
        "",
        "## 2. Top Management",
        "",
        "Top management is accountable for the ISMS: it establishes the information security "
        "policy and objectives, provides resources, assigns the responsibilities below, and "
        "reviews the ISMS at planned intervals.",
        "",
        "## 3. Function-to-Role Mapping",
        "",
    ]
    if groups:
        ordered = sorted(groups.items(), key=lambda kv: -kv[1]["controls"])
        lines += _table(
            ["ISMS function", "Current role holder(s)", "Controls owned", "Domains"],
            [[fn, ", ".join(sorted(g["owners"])), g["controls"], ", ".join(sorted(g["domains"]))]
             for fn, g in ordered],
        )
        lines += [
            "Functions are inferred from each control owner's recorded name or title. Correct "
            "any that are wrong here and in the control owners.",
            "",
        ]
    else:
        lines += [_tbc("assign owners to scoped controls"), ""]
    if unassigned:
        lines += [f"**{_tally(unassigned, 'control')} {'has' if unassigned == 1 else 'have'} no owner** "
                  "and therefore no assigned responsibility.", ""]
    lines += ["## 4. Domain Responsibility Matrix", ""]
    lines += _table(
        ["Domain", "Functions", "Controls"],
        [[f"{d.domain.identifier} — {d.domain.name}",
          "; ".join(sorted({infer_function(c.owner) for c in d.controls if c.owner})) or "Unassigned",
          len(d.controls)] for d in ctx.domains],
    )
    lines += [
        "## 5. Reporting on ISMS Performance",
        "",
        "The CISO function reports on the performance of the ISMS to top management through "
        "management review.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# 9.3 Management Review
# ---------------------------------------------------------------------------


def render_management_review(ctx: OrganisationContext, **_: Any) -> str:
    issues = _isms(ctx, "context_issues", []) or []
    parties = _isms(ctx, "interested_parties", []) or []
    objectives = _isms(ctx, "objectives", []) or []
    audits = _isms(ctx, "audit_programme", []) or []
    open_nc = [d for d in ctx.deficiencies if d.get("status") not in ("completed", "cancelled")]
    obj_status: Dict[str, int] = {}
    for o in objectives:
        obj_status[o.get("status") or "planned"] = obj_status.get(o.get("status") or "planned", 0) + 1
    residual = _level_counts(ctx.risk_assessments, "residual_risk_level")

    lines = _header(ctx, "Management Review")
    lines += [
        "## 1. Purpose",
        "",
        f"The record of top management's review of {ctx.name}'s ISMS, to ensure its "
        "continuing suitability, adequacy and effectiveness (ISO/IEC 27001, clause 9.3). "
        "The inputs below are prefilled from the platform; the decisions are for the "
        "meeting to record.",
        "",
        "## 2. Meeting",
        "",
        "| | |",
        "|---|---|",
        f"| Date | {TBC} |",
        f"| Chair | {TBC} |",
        f"| Attendees | {TBC} |",
        "",
        "## 3. Inputs",
        "",
        "### Status of Actions from Previous Reviews",
        "",
        _tbc("record the status of actions agreed at the previous review"),
        "",
        "### Changes in External and Internal Issues",
        "",
        f"{_tally(len(issues), 'issue')} recorded in the Context of the Organisation."
        if issues else _tbc("record internal and external issues"),
        "",
        "### Changes in the Needs and Expectations of Interested Parties",
        "",
        f"{_tally(len(parties), 'interested party').replace('partys', 'parties')} recorded."
        if parties else _tbc("record interested parties and their requirements"),
        "",
        "### Information Security Performance",
        "",
        "| Indicator | Value |",
        "|---|---|",
        f"| Nonconformities open | {len(open_nc)} of {len(ctx.deficiencies)} recorded |",
        f"| Controls implemented | {ctx.implemented_count()} of {ctx.total_scoped_controls} "
        f"({ctx.coverage_percent()}%) |",
        f"| Internal audits completed | {sum(1 for a in audits if a.get('status') == 'completed')} "
        f"of {len(audits)} planned |",
        "| Objectives | " + (", ".join(f"{v} {status_label(k).lower()}" for k, v in sorted(obj_status.items()))
                             or "None recorded") + " |",
        "",
        "### Feedback from Interested Parties",
        "",
        _tbc("record feedback received from interested parties"),
        "",
        "### Results of Risk Assessment and Status of Risk Treatment",
        "",
        (", ".join(f"{v} {k}" for k, v in sorted(residual.items())) + " residual risk(s).")
        if ctx.risk_assessments else _tbc("populate the risk register"),
        "",
        "### Opportunities for Continual Improvement",
        "",
        _tbc("record opportunities for improvement"),
        "",
        "## 4. Decisions and Actions",
        "",
        "| # | Decision or action | Owner | Due |",
        "|---|---|---|---|",
        f"| 1 | {TBC} | {TBC} | {TBC} |",
        "",
        "Decisions cover opportunities for continual improvement and any need for changes "
        "to the ISMS.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# Incident Response Plan and Business Continuity Plan
# ---------------------------------------------------------------------------


def _domain_controls(ctx: OrganisationContext, identifier: str) -> List[Any]:
    d = ctx.domain(identifier)
    return list(d.controls) if d else []


def _cited(controls: List[Any], limit: int = 12) -> str:
    """Bracketed citations of scoped controls only."""
    ids = [c.scf_id for c in controls][:limit]
    more = len(controls) - len(ids)
    text = ", ".join(f"[{i}]" for i in ids)
    return f"{text} and {more} more" if more > 0 else text


def render_irp(ctx: OrganisationContext, **_: Any) -> str:
    iro = _domain_controls(ctx, "IRO")
    comms = [
        c for c in (_isms(ctx, "communication_plan", []) or [])
        if any(w in (c.get("what") or "").lower() for w in ("incident", "breach"))
    ]
    owners = sorted({c.owner for c in iro if c.owner})
    lines = _header(ctx, "Incident Response Plan")
    lines += [
        "## 1. Purpose and Scope",
        "",
        f"How {ctx.name} detects, responds to and recovers from information security "
        "incidents across the systems and services in the ISMS scope.",
        "",
    ]
    lines += ([f"Controls this plan implements: {_cited(iro)}.", ""] if iro else
              [_tbc("scope the Incident Response (IRO) controls; none are in scope"), ""])
    lines += [
        "## 2. Severity Levels",
        "",
        "Response times are organisation-defined; confirm or change them.",
        "",
        "| Severity | Definition | Response within |",
        "|---|---|---|",
        "| P1 Critical | Service unavailable, confirmed data breach, ransomware | 15 minutes (organisation-defined) |",
        "| P2 High | Partial degradation, suspected exposure | 1 hour (organisation-defined) |",
        "| P3 Medium | Limited impact, no data exposure, workaround available | 4 hours (organisation-defined) |",
        "| P4 Low | Informational, no service impact | 1 working day (organisation-defined) |",
        "",
        "## 3. Response Team",
        "",
    ]
    if owners:
        lines += _table(["Role holder", "Function"], [[o, infer_function(o)] for o in owners])
    else:
        lines += [_tbc("assign owners to the incident response controls"), ""]
    lines += [
        "## 4. Response Phases",
        "",
        "1. **Detect and report.** Anyone who suspects an incident reports it at once. The "
        "report is logged, classified and assigned a severity.",
        "2. **Contain.** Limit the spread and the damage: isolate affected systems, revoke "
        "compromised credentials, preserve evidence before changing anything.",
        "3. **Eradicate.** Remove the cause and close the weakness it exploited.",
        "4. **Recover.** Restore systems and data from trusted sources and confirm they "
        "operate normally before returning them to service.",
        "5. **Learn.** Review the incident, record the root cause and raise corrective "
        "actions in the Nonconformity and Corrective Action Register.",
        "",
        "## 5. Communication and Notification",
        "",
    ]
    if comms:
        lines += _table(["What", "When", "With whom", "Who", "How"],
                        [[c.get("what"), c.get("when"), c.get("audience"), c.get("communicator"), c.get("method")]
                         for c in comms])
    else:
        lines += [_tbc("add incident and breach notifications to the communication plan in the ISMS Profile"), ""]
    critical = [v for v in ctx.vendors if (v.get("criticality") or "").lower() in ("critical", "high")]
    if critical:
        lines += ["### Critical Suppliers to Involve", ""]
        lines += [f"- {v.get('name')}" for v in critical] + [""]
    lines += [
        "## 6. Systems in Scope",
        "",
        (", ".join(s.get("name") for s in ctx.systems) + ".") if ctx.systems
        else _tbc("record systems in the Systems Registry"),
        "",
        "## 7. Testing and Review",
        "",
        "The plan is tested at least annually and reviewed after every P1 or P2 incident.",
    ]
    return _done(lines)


def render_bcp(ctx: OrganisationContext, **_: Any) -> str:
    bcd = _domain_controls(ctx, "BCD")
    critical = [v for v in ctx.vendors if (v.get("criticality") or "").lower() in ("critical", "high")]
    lines = _header(ctx, "Business Continuity Plan")
    lines += [
        "## 1. Purpose and Scope",
        "",
        f"How {ctx.name} keeps critical services running, or restores them, after a "
        "disruption, and protects information security while doing so.",
        "",
    ]
    lines += ([f"Controls this plan implements: {_cited(bcd)}.", ""] if bcd else
              [_tbc("scope the Business Continuity (BCD) controls; none are in scope"), ""])
    lines += ["## 2. Recovery Objectives", "",
              "Recovery time (RTO) and recovery point (RPO) objectives are set by the "
              "organisation for each system.", ""]
    if ctx.systems:
        lines += _table(["System", "Type", "RTO", "RPO"],
                        [[s.get("name"), status_label(s.get("system_type")), TBC, TBC] for s in ctx.systems])
    else:
        lines += [_tbc("record systems in the Systems Registry"), ""]
    lines += ["## 3. Critical Suppliers", ""]
    if critical:
        lines += _table(["Supplier", "Criticality", "Continuity arrangement"],
                        [[v.get("name"), status_label(v.get("criticality")), TBC] for v in critical])
    else:
        lines += ["No suppliers are rated critical or high in the Vendor Inventory.", ""]
    lines += [
        "## 4. Invocation",
        "",
        f"The plan is invoked by {_tbc('name the function with authority to invoke the plan')} "
        "when a disruption is expected to exceed a system's recovery time objective.",
        "",
        "## 5. Recovery Steps",
        "",
        "1. Assess the disruption and its expected duration.",
        "2. Invoke the plan and notify the people in the communication plan.",
        "3. Recover systems in priority order from verified backups.",
        "4. Confirm data integrity and that security controls are in place before resuming service.",
        "5. Return to normal operation and review the response.",
        "",
        "## 6. Testing",
        "",
        "Backups are restored and the plan exercised at least annually; results and "
        "improvements are recorded.",
    ]
    return _done(lines)


# ---------------------------------------------------------------------------
# ISMS Manual — chapters 4 to 10, registers as appendices
# ---------------------------------------------------------------------------

_MANUAL_REFERENCES = [
    ("isms-scope", "ISMS Scope Statement"),
    ("context-of-organisation", "Context of the Organisation"),
    ("information-security-policy", "Information Security Policy"),
    ("roles-responsibilities", "Roles, Responsibilities and Authorities"),
    ("risk-methodology", "Risk Assessment and Treatment Methodology"),
    ("soa", "Statement of Applicability"),
    ("risk-treatment", "Risk Treatment Plan"),
    ("security-objectives", "Information Security Objectives"),
    ("communication-plan", "Communication Plan"),
    ("document-control", "Document and Record Control Procedure"),
    ("risk-assessment-report", "Risk Assessment Report"),
    ("internal-audit-programme", "Internal Audit Programme"),
    ("management-review", "Management Review"),
    ("nonconformity-register", "Nonconformity and Corrective Action Register"),
]


def render_isms_manual(ctx: OrganisationContext, **_: Any) -> str:
    scope = _isms(ctx, "scope", {}) or {}
    have = {d.get("generator") for d in ctx.documents}
    lines = _header(ctx, "ISMS Manual")
    lines += [
        "## 1. Introduction",
        "",
        f"This manual describes how {ctx.name}'s information security management system "
        "meets ISO/IEC 27001 clauses 4 to 10, and where each requirement is addressed. The "
        "registers the clauses depend on are appended.",
        "",
        "## 2. Context of the Organisation (Clause 4)",
        "",
        _text(scope.get("organisation_description"), "describe the organisation in the ISMS Profile"),
        "",
        f"**Scope:** {_text(scope.get('boundaries'), 'state the ISMS boundaries in the ISMS Profile')}",
        "",
        "Issues and interested parties are in Appendices A and B.",
        "",
        "## 3. Leadership (Clause 5)",
        "",
        "Top management sets the information security policy, assigns roles and "
        "responsibilities, and provides the resources the ISMS needs. Functions and their "
        "current holders are in Roles, Responsibilities and Authorities.",
        "",
        "## 4. Planning (Clause 6)",
        "",
        f"Risks are assessed and treated by the Risk Assessment and Treatment Methodology; "
        f"{_tally(len(ctx.risk_assessments), 'risk')} {'is' if len(ctx.risk_assessments) == 1 else 'are'} "
        "recorded. Control applicability is in the Statement of Applicability. Objectives are "
        "in Appendix C. Changes to the ISMS are planned and approved before they are made.",
        "",
        "## 5. Support (Clause 7)",
        "",
        "Competence is ensured through recruitment, training and awareness, with records "
        "kept as evidence. Communication is planned in Appendix D. Documented information is "
        "controlled by the Document and Record Control Procedure.",
        "",
        "## 6. Operation (Clause 8)",
        "",
        f"{ctx.implemented_count()} of {_tally(ctx.total_scoped_controls, 'control')} are "
        f"implemented ({ctx.coverage_percent()}%). Risk assessments are repeated at planned "
        "intervals and the Risk Treatment Plan is carried out.",
        "",
        "## 7. Performance Evaluation (Clause 9)",
        "",
        "Controls are monitored and measured through their validation cadence and key "
        "security indicators. Internal audits follow the programme in Appendix E. Top "
        "management reviews the ISMS at planned intervals.",
        "",
        "## 8. Improvement (Clause 10)",
        "",
        f"Nonconformities are corrected and their causes removed; {len(ctx.deficiencies)} "
        "recorded to date in the Nonconformity and Corrective Action Register.",
        "",
        "## 9. Document Map",
        "",
        "| Document | Generated |",
        "|---|---|",
    ]
    lines += [f"| {title} | {_yes(gen in have)} |" for gen, title in _MANUAL_REFERENCES]
    lines += ["", "## Appendix A. Issues", ""]
    lines += _issues_section(ctx, "###")
    lines += ["## Appendix B. Interested Parties", ""]
    lines += _parties_section(ctx, "###")
    lines += ["## Appendix C. Objectives", ""]
    lines += _objectives_section(ctx, "###")
    lines += ["## Appendix D. Communication", ""]
    lines += _communication_section(ctx, "###")
    lines += ["## Appendix E. Audit Programme", ""]
    lines += _audit_rows_section(ctx, "###")
    return _done(lines)
