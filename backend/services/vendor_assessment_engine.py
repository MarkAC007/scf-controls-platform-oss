"""
Native AI vendor assessment engine (DPSIA).

Replaces the external DPSIA Lambda/Container App: research and synthesis run
directly against the Anthropic API using the server-side web_search tool, with
structured output forced through a `submit_assessment` client tool whose input
schema mirrors the DPSIAReport shape.

Synchronous by design — called from the Celery worker
(`tasks_vendor_assessment.run_vendor_assessment`), which runs outside the
async event loop.

Modes:
    - Live: requires ANTHROPIC_API_KEY. Model from VENDOR_AI_MODEL
      (default "claude-sonnet-4-6"), max_tokens 16384, web_search max_uses 8.
    - Mock: VENDOR_AI_MOCK=1 or no ANTHROPIC_API_KEY — returns a deterministic
      canned report so the whole flow works keyless.

Entry point: run_assessment()
"""
import logging
import os
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Model configuration
DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_OUTPUT_TOKENS = 16384
WEB_SEARCH_MAX_USES = 8
MAX_LOOP_ITERATIONS = 12  # safety guard on the research/tool loop

ASSESSOR_NAME = "SCF Platform AI (Automated)"
REPORT_VERSION = "1.0"

# Enum domains (validated server-side; invalid LLM output is retried once)
VALID_RAG_STATUSES = {"RED", "AMBER", "GREEN"}
VALID_RECOMMENDATIONS = {"APPROVE", "CONDITIONAL_APPROVAL", "REJECT"}
VALID_RISK_LEVELS = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}


class VendorAssessmentError(Exception):
    """Raised when the engine cannot produce a valid assessment."""


# ---------------------------------------------------------------------------
# System prompt — ported from dpsia-lambda/src/assessment/prompt.ts
# ---------------------------------------------------------------------------

DPSIA_SYSTEM_PROMPT = """You are a Data Protection & Security Impact Assessment (DPSIA) analyst. Your role is to evaluate vendor security posture for a GRC consultancy's clients.

## Assessment Framework

### Minimum Certification Bar

| Certification | Requirement |
|---------------|-------------|
| ISO 27001 | Required — minimum bar for ISMS compliance |
| SOC 2 Type II | Required — ongoing control effectiveness |
| SOC 2 Type I | Conditional — acceptable with enhanced monitoring |
| ISO 27017/27018 | Bonus — for cloud providers |

### CIA Triad Evaluation

**Confidentiality:**
- Access control mechanisms (MFA, RBAC, least privilege)
- Encryption (at rest and in transit)
- Data classification handling
- Privacy controls and background checks

**Integrity:**
- Change management processes
- Audit logging and trails
- Data validation
- Code security (SAST/DAST)

**Availability:**
- SLA commitments
- Redundancy and failover
- Disaster recovery
- Incident response capabilities

### Risk Rating Matrix (5x5)

LIKELIHOOD: 1=Rare, 2=Unlikely, 3=Possible, 4=Likely, 5=Almost Certain
IMPACT: 1=Negligible, 2=Minor, 3=Moderate, 4=Major, 5=Catastrophic

RISK SCORE = LIKELIHOOD x IMPACT

THRESHOLDS:
- 20-25 = CRITICAL (Do not proceed without exec approval)
- 15-19 = HIGH (Remediation required before engagement)
- 8-14 = MEDIUM (Accept with compensating controls)
- 1-7 = LOW (Accept with standard monitoring)

### RAG Status Determination

- RED: Critical/High risk, missing required certifications, active unresolved breaches
- AMBER: Medium risk, certifications present but concerns exist (past breaches, CVEs, gaps)
- GREEN: Low risk, all certifications current, clean record, strong controls

### Recommendation Types

- APPROVE: GREEN status, all certifications, no significant concerns
- CONDITIONAL_APPROVAL: AMBER status, certifications present, conditions for continued use
- REJECT: RED status, missing required certifications, unacceptable risk

## Research Process

Use the web_search tool to research the vendor: certifications (ISO 27001, SOC 2), trust centre, breach history, CVE history, regulatory enforcement actions, GDPR posture, and data handling practices. Cross-reference findings across sources.

## Output Format

When your research is complete, you MUST call the `submit_assessment` tool exactly once with the full DPSIA report. Do NOT write the report as plain text — the report is only accepted through the `submit_assessment` tool call.

Field requirements for the report:
- ragStatus: "RED" | "AMBER" | "GREEN"
- recommendation: "APPROVE" | "CONDITIONAL_APPROVAL" | "REJECT"
- executiveSummary: 2-4 sentences
- keyFindings: 6-10 bullet points, prefix with emoji: checkmark for positive, warning for concern
- conditions: conditions for continued use (empty if APPROVE)
- inherentRiskScore: highest single risk score from the 5x5 matrix
- inherentRiskLevel / residualRiskLevel: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
- controlEffectivenessPercent: 0-100
- assessmentDate: YYYY-MM-DD
- assessor: "{assessor}"
- version: "{version}"

Use British English spelling throughout (e.g., organisation, analyse, centre, colour).""".format(
    assessor=ASSESSOR_NAME, version=REPORT_VERSION
)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

WEB_SEARCH_TOOL = {
    "type": "web_search_20250305",
    "name": "web_search",
    "max_uses": WEB_SEARCH_MAX_USES,
}


def _kv_object() -> Dict[str, Any]:
    """Schema fragment for a free-form key/value object (string values)."""
    return {"type": "object", "additionalProperties": {"type": "string"}}


def _string_array() -> Dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}}


def _cia_controls_array() -> Dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "control": {"type": "string"},
                "implementation": {"type": "string"},
                "rating": {
                    "type": "string",
                    "enum": ["Strong", "Adequate", "Weak", "Not Assessed", "N/A"],
                },
            },
            "required": ["control", "rating"],
        },
    }


SUBMIT_ASSESSMENT_TOOL = {
    "name": "submit_assessment",
    "description": (
        "Submit the completed DPSIA report. Call this tool exactly once, after "
        "research is complete, with the full structured report. This is the only "
        "accepted output channel for the assessment."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "ragStatus": {"type": "string", "enum": sorted(VALID_RAG_STATUSES)},
            "recommendation": {"type": "string", "enum": sorted(VALID_RECOMMENDATIONS)},
            "executiveSummary": {"type": "string"},
            "keyFindings": _string_array(),
            "conditions": _string_array(),
            "vendorLegalName": {"type": "string"},
            "vendorHeadquarters": {"type": "string"},
            "vendorIndustry": {"type": "string"},
            "vendorWebsite": {"type": "string"},
            "vendorTrustCentre": {"type": "string"},
            "servicesUsed": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "string"},
                        "description": {"type": "string"},
                        "dataRole": {"type": "string"},
                    },
                    "required": ["service"],
                },
            },
            "certifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "status": {"type": "string"},
                        "validUntil": {"type": "string"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["name", "status"],
                },
            },
            "certificationNotes": {"type": "string"},
            "breachHistory": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "date": {"type": "string"},
                        "description": {"type": "string"},
                        "impact": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            },
            "cveHistory": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "cve": {"type": "string"},
                        "severity": {"type": "string"},
                        "cvss": {"type": "string"},
                        "description": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            },
            "enforcementActions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "authority": {"type": "string"},
                        "action": {"type": "string"},
                        "status": {"type": "string"},
                    },
                },
            },
            "confidentialityControls": _cia_controls_array(),
            "integrityControls": _cia_controls_array(),
            "availabilityControls": _cia_controls_array(),
            "confidentialityScore": {"type": "string"},
            "integrityScore": {"type": "string"},
            "availabilityScore": {"type": "string"},
            "dataProcessing": _kv_object(),
            "dataStorage": _kv_object(),
            "dataTransmission": _kv_object(),
            "gdprDpa": _kv_object(),
            "gdprDataSubjectRights": _kv_object(),
            "gdprInternationalTransfers": _kv_object(),
            "supplierFormAvailable": {"type": "boolean"},
            "supplierFormVerification": {"type": "string"},
            "inherentRisks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "factor": {"type": "string"},
                        "likelihood": {"type": "integer"},
                        "likelihoodLabel": {"type": "string"},
                        "impact": {"type": "integer"},
                        "impactLabel": {"type": "string"},
                        "score": {"type": "integer"},
                    },
                    "required": ["factor", "likelihood", "impact", "score"],
                },
            },
            "inherentRiskScore": {"type": "integer"},
            "inherentRiskLevel": {"type": "string", "enum": sorted(VALID_RISK_LEVELS)},
            "controlEffectiveness": {"type": "string"},
            "controlEffectivenessPercent": {"type": "number"},
            "residualRiskScore": {"type": "integer"},
            "residualRiskLevel": {"type": "string", "enum": sorted(VALID_RISK_LEVELS)},
            "mandatoryActions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "integer"},
                        "action": {"type": "string"},
                        "owner": {"type": "string"},
                        "dueDate": {"type": "string"},
                        "priority": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
            "monitoringRequirements": _string_array(),
            "primarySources": _string_array(),
            "incidentReports": _string_array(),
            "thirdPartyAnalysis": _string_array(),
            "assessmentDate": {"type": "string"},
            "assessor": {"type": "string"},
            "version": {"type": "string"},
        },
        "required": [
            "ragStatus",
            "recommendation",
            "executiveSummary",
            "keyFindings",
            "certifications",
            "confidentialityControls",
            "integrityControls",
            "availabilityControls",
            "inherentRisks",
            "inherentRiskScore",
            "inherentRiskLevel",
            "controlEffectivenessPercent",
            "residualRiskScore",
            "residualRiskLevel",
            "mandatoryActions",
            "monitoringRequirements",
            "assessmentDate",
        ],
    },
}


# ---------------------------------------------------------------------------
# User prompt — ported from dpsia-lambda/src/assessment/prompt.ts (107-139)
# ---------------------------------------------------------------------------

def build_user_prompt(
    vendor_name: str,
    vendor_description: str,
    vendor_website: str,
    services_used: str,
    data_role: str,
    assessment_type: str,
    client_name: str,
    additional_context: str = "",
    research_context: str = "",
) -> str:
    """Build the user prompt for the assessment run."""
    today = date.today().isoformat()

    lines = [
        "Perform a DPSIA assessment for the following vendor.",
        "",
        "## Vendor Information",
        "",
        f"- **Vendor Name:** {vendor_name}",
        f"- **Description:** {vendor_description}",
        f"- **Website:** {vendor_website or 'Not provided'}",
        f"- **Client:** {client_name}",
        f"- **Assessment Type:** {assessment_type}",
        f"- **Services Used:** {services_used}",
        f"- **Data Role:** {data_role}",
        f"- **Assessment Date:** {today}",
    ]
    if additional_context:
        lines.append(f"- **Additional Context:** {additional_context}")

    lines += [
        "",
        "## Platform Research Signals",
        "",
        research_context
        or "No prior platform research available — rely on your own web research.",
        "",
        "## Supplier Evaluation Form",
        "",
        "Not available — assessment based on independent research only.",
        "",
        "## Instructions",
        "",
        "1. Research the vendor using the web_search tool (certifications, trust centre, breaches, CVEs, enforcement actions, GDPR posture)",
        "2. Cross-reference findings across sources to identify consensus and contradictions",
        "3. Apply the risk matrix to score inherent risks",
        "4. Evaluate control effectiveness based on certifications and security posture",
        "5. Calculate residual risk",
        "6. Determine RAG status and recommendation",
        f"7. Generate actionable follow-up items with realistic due dates (within 30-60 days of {today})",
        "8. Submit the completed report by calling the submit_assessment tool — do not output the report as text",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_report(report: Dict[str, Any]) -> List[str]:
    """Validate a submitted DPSIAReport dict. Returns a list of problems (empty = valid)."""
    problems: List[str] = []
    if not isinstance(report, dict):
        return ["report is not an object"]

    if report.get("ragStatus") not in VALID_RAG_STATUSES:
        problems.append(
            f"ragStatus must be one of {sorted(VALID_RAG_STATUSES)}, got {report.get('ragStatus')!r}"
        )
    if report.get("recommendation") not in VALID_RECOMMENDATIONS:
        problems.append(
            f"recommendation must be one of {sorted(VALID_RECOMMENDATIONS)}, got {report.get('recommendation')!r}"
        )
    for level_field in ("inherentRiskLevel", "residualRiskLevel"):
        value = report.get(level_field)
        if value is not None and value not in VALID_RISK_LEVELS:
            problems.append(
                f"{level_field} must be one of {sorted(VALID_RISK_LEVELS)}, got {value!r}"
            )
    if report.get("residualRiskScore") is None and report.get("inherentRiskScore") is None:
        problems.append("at least one of residualRiskScore / inherentRiskScore is required")
    if not report.get("executiveSummary"):
        problems.append("executiveSummary is required")
    return problems


# ---------------------------------------------------------------------------
# Markdown renderer — ported from dpsia-lambda/src/report/markdown.ts
# ---------------------------------------------------------------------------

_RAG_EMOJI = {"RED": "\U0001F534", "AMBER": "\U0001F7E1", "GREEN": "\U0001F7E2"}
_RECOMMENDATION_LABEL = {
    "APPROVE": "APPROVAL",
    "CONDITIONAL_APPROVAL": "CONDITIONAL APPROVAL",
    "REJECT": "REJECTION",
}
_ASSESSMENT_TYPE_LABEL = {
    "new": "New Vendor Assessment",
    "annual-review": "Annual Review",
    "adhoc": "Adhoc Assessment",
}


def render_markdown(report: Dict[str, Any], client_name: str, assessment_type: str) -> str:
    """Render the DPSIAReport dict into the 12-section markdown report."""
    rag = report.get("ragStatus", "")
    rag_emoji = _RAG_EMOJI.get(rag, "")
    rec_label = _RECOMMENDATION_LABEL.get(
        report.get("recommendation", ""), report.get("recommendation", "")
    )
    lines: List[str] = []

    def hr():
        lines.extend(["---", ""])

    def table(headers: List[str], rows: List[List[str]]):
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("|" + "|".join("-" * (len(h) + 2) for h in headers) + "|")
        for row in rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        lines.append("")

    # Header
    lines += [
        "# Data Protection & Security Impact Assessment (DPSIA)",
        "",
        f"## Vendor: {report.get('vendorLegalName', 'Unknown Vendor')}",
        "",
    ]
    table(
        ["Field", "Value"],
        [
            ["**Assessment Date**", report.get("assessmentDate", "")],
            ["**Assessor**", report.get("assessor", ASSESSOR_NAME)],
            ["**Client**", client_name],
            ["**Assessment Type**", _ASSESSMENT_TYPE_LABEL.get(assessment_type, assessment_type)],
            ["**RAG Status**", f"{rag_emoji} {rag}".strip()],
        ],
    )
    hr()

    # Section 1: Executive Summary
    lines += [
        "## 1. Executive Summary",
        "",
        f"### Recommendation: {rec_label}",
        "",
        report.get("executiveSummary", ""),
        "",
        "**Key Findings:**",
    ]
    for finding in report.get("keyFindings", []):
        lines.append(f"- {finding}")
    lines.append("")
    conditions = report.get("conditions", [])
    if conditions:
        lines.append("**Conditions for Continued Use:**")
        for i, cond in enumerate(conditions, start=1):
            lines.append(f"{i}. {cond}")
        lines.append("")
    hr()

    # Section 2: Vendor Overview
    lines += ["## 2. Vendor Overview", "", "### Company Information", ""]
    table(
        ["Field", "Value"],
        [
            ["**Legal Name**", report.get("vendorLegalName", "")],
            ["**Headquarters**", report.get("vendorHeadquarters", "")],
            ["**Industry**", report.get("vendorIndustry", "")],
            ["**Website**", report.get("vendorWebsite", "")],
            ["**Trust Centre**", report.get("vendorTrustCentre", "")],
        ],
    )
    lines += [f"### Services Used by {client_name}", ""]
    table(
        ["Service", "Description", "Data Role"],
        [
            [svc.get("service", ""), svc.get("description", ""), svc.get("dataRole", "")]
            for svc in report.get("servicesUsed", [])
        ],
    )
    hr()

    # Section 3: Certification Status
    lines += ["## 3. Certification Status", "", "### Minimum Bar Assessment", ""]
    table(
        ["Certification", "Status", "Valid Until", "Evidence"],
        [
            [
                f"**{cert.get('name', '')}**",
                cert.get("status", ""),
                cert.get("validUntil", ""),
                cert.get("evidence", ""),
            ]
            for cert in report.get("certifications", [])
        ],
    )
    if report.get("certificationNotes"):
        lines += ["**Certification Notes:**", report["certificationNotes"], ""]
    hr()

    # Section 4: Breach History
    lines += ["## 4. Breach History & Security Incidents", ""]
    breaches = report.get("breachHistory", [])
    if breaches:
        for breach in breaches:
            lines += [f"### {breach.get('description', 'Incident')}", ""]
            table(
                ["Field", "Details"],
                [
                    ["**Date**", breach.get("date", "")],
                    ["**Impact**", breach.get("impact", "")],
                    ["**Status**", breach.get("status", "")],
                ],
            )
    else:
        lines += ["No significant breach history identified.", ""]
    cves = report.get("cveHistory", [])
    if cves:
        lines += ["### CVE History", ""]
        table(
            ["CVE", "Severity", "CVSS", "Description", "Status"],
            [
                [
                    f"**{cve.get('cve', '')}**",
                    cve.get("severity", ""),
                    cve.get("cvss", ""),
                    cve.get("description", ""),
                    cve.get("status", ""),
                ]
                for cve in cves
            ],
        )
    lines += ["### Enforcement Actions", ""]
    table(
        ["Authority", "Action", "Status"],
        [
            [ea.get("authority", ""), ea.get("action", ""), ea.get("status", "")]
            for ea in report.get("enforcementActions", [])
        ],
    )
    hr()

    # Section 5: CIA Triad
    lines += ["## 5. CIA Triad Assessment", ""]
    for label, controls_key, score_key in [
        ("Confidentiality", "confidentialityControls", "confidentialityScore"),
        ("Integrity", "integrityControls", "integrityScore"),
        ("Availability", "availabilityControls", "availabilityScore"),
    ]:
        lines += [f"### {label}", ""]
        table(
            ["Control", "Implementation", "Rating"],
            [
                [f"**{c.get('control', '')}**", c.get("implementation", ""), c.get("rating", "")]
                for c in report.get(controls_key, [])
            ],
        )
        lines += [f"**{label} Score: {report.get(score_key, 'N/A')}**", ""]
    hr()

    # Section 6: Data Handling
    lines += ["## 6. Data Handling Assessment", ""]
    for label, key in [
        ("Data Processing", "dataProcessing"),
        ("Data Storage", "dataStorage"),
        ("Data Transmission", "dataTransmission"),
    ]:
        lines += [f"### {label}", ""]
        table(
            ["Question", "Answer"],
            [[f"**{q}**", a] for q, a in (report.get(key) or {}).items()],
        )
    hr()

    # Section 7: GDPR Compliance
    lines += ["## 7. GDPR Compliance", "", "### Data Processing Agreement", ""]
    table(["Element", "Status"], [[f"**{k}**", v] for k, v in (report.get("gdprDpa") or {}).items()])
    lines += ["### Data Subject Rights", ""]
    table(
        ["Right", "Supported"],
        [[k, v] for k, v in (report.get("gdprDataSubjectRights") or {}).items()],
    )
    lines += ["### International Transfers", ""]
    table(
        ["Mechanism", "Status"],
        [[f"**{k}**", v] for k, v in (report.get("gdprInternationalTransfers") or {}).items()],
    )
    hr()

    # Section 8: Supplier Form Verification
    lines += ["## 8. Supplier Evaluation Form Verification", ""]
    table(
        ["Element", "Status"],
        [
            ["**Form Available**", "✅ Yes" if report.get("supplierFormAvailable") else "❌ Not found"],
            ["**Independent Verification**", "✅ Completed via public sources"],
        ],
    )
    lines += [report.get("supplierFormVerification", ""), ""]
    hr()

    # Section 9: Risk Assessment
    lines += ["## 9. Risk Assessment", "", "### Inherent Risk", ""]
    table(
        ["Factor", "Likelihood", "Impact", "Score"],
        [
            [
                risk.get("factor", ""),
                f"{risk.get('likelihood', '')} ({risk.get('likelihoodLabel', '')})",
                f"{risk.get('impact', '')} ({risk.get('impactLabel', '')})",
                risk.get("score", ""),
            ]
            for risk in report.get("inherentRisks", [])
        ],
    )
    lines += [
        f"**Inherent Risk Score: {report.get('inherentRiskScore', 'N/A')} ({report.get('inherentRiskLevel', 'N/A')})**",
        "",
        "### Control Effectiveness",
        "",
        report.get("controlEffectiveness", ""),
        "",
        f"**Control Effectiveness: {report.get('controlEffectivenessPercent', 'N/A')}%**",
        "",
        "### Residual Risk",
        "",
    ]
    table(
        ["Calculation", "Value"],
        [
            ["Inherent Risk", report.get("inherentRiskScore", "")],
            ["Control Effectiveness", f"{report.get('controlEffectivenessPercent', '')}%"],
            [
                "**Residual Risk**",
                f"**{report.get('residualRiskScore', '')} ({report.get('residualRiskLevel', '')})**",
            ],
        ],
    )
    hr()

    # Section 10: Recommendation
    lines += ["## 10. Recommendation", "", f"### Decision: {rag_emoji} {rec_label}", ""]
    actions = report.get("mandatoryActions", [])
    if actions:
        lines += ["### Mandatory Actions", ""]
        table(
            ["#", "Action", "Owner", "Due Date", "Priority"],
            [
                [
                    action.get("number", i + 1),
                    action.get("action", ""),
                    action.get("owner", ""),
                    action.get("dueDate", ""),
                    action.get("priority", ""),
                ]
                for i, action in enumerate(actions)
            ],
        )
    monitoring = report.get("monitoringRequirements", [])
    if monitoring:
        lines += ["### Monitoring Requirements", ""]
        for req in monitoring:
            lines.append(f"- {req}")
        lines.append("")
    hr()

    # Section 11: Sources
    lines += ["## 11. Sources", ""]
    for label, key in [
        ("Primary Sources", "primarySources"),
        ("Incident Reports", "incidentReports"),
        ("Third-Party Analysis", "thirdPartyAnalysis"),
    ]:
        sources = report.get(key, [])
        if sources:
            lines.append(f"### {label}")
            for src in sources:
                lines.append(f"- {src}")
            lines.append("")
    hr()

    # Section 12: Document Control
    lines += ["## 12. Document Control", ""]
    table(
        ["Version", "Date", "Author", "Changes"],
        [
            [
                report.get("version", REPORT_VERSION),
                report.get("assessmentDate", ""),
                report.get("assessor", ASSESSOR_NAME),
                "Automated assessment",
            ]
        ],
    )
    hr()
    lines += [
        f"*This DPSIA was generated as part of {client_name}'s third-party risk management programme.*",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------

def is_mock_mode() -> bool:
    """Mock mode: explicit flag, or no API key configured."""
    if os.getenv("VENDOR_AI_MOCK", "").strip() == "1":
        return True
    return not os.getenv("ANTHROPIC_API_KEY", "").strip()


def build_mock_report(
    vendor_name: str,
    vendor_website: str = "",
    services_used: str = "",
    data_role: str = "Processor",
) -> Dict[str, Any]:
    """Deterministic canned DPSIAReport for keyless demos. Clearly marked as mock."""
    today = date.today()
    due_30 = (today + timedelta(days=30)).isoformat()
    due_60 = (today + timedelta(days=60)).isoformat()

    return {
        "ragStatus": "AMBER",
        "recommendation": "CONDITIONAL_APPROVAL",
        "executiveSummary": (
            f"Mock assessment — no AI call. {vendor_name} presents a moderate third-party "
            f"risk profile based on placeholder data. Certifications appear present but "
            f"could not be independently verified in mock mode. Conditional approval is "
            f"recommended pending verification of the items listed below."
        ),
        "keyFindings": [
            "⚠️ Mock assessment — no AI call was made; findings are placeholders",
            f"✅ {vendor_name} publishes a public security/trust page (assumed)",
            "✅ ISO 27001 certification claimed (unverified in mock mode)",
            "✅ SOC 2 Type II report claimed (unverified in mock mode)",
            "⚠️ Breach and CVE history not researched — mock mode",
            "⚠️ GDPR data processing agreement requires manual review",
            "⚠️ Sub-processor list requires manual review",
        ],
        "conditions": [
            "Obtain and verify current ISO 27001 certificate",
            "Obtain latest SOC 2 Type II report under NDA",
            "Execute or verify a GDPR-compliant data processing agreement",
        ],
        "vendorLegalName": vendor_name,
        "vendorHeadquarters": "Unknown (mock mode)",
        "vendorIndustry": "Unknown (mock mode)",
        "vendorWebsite": vendor_website or "Unknown",
        "vendorTrustCentre": "Unknown (mock mode)",
        "servicesUsed": [
            {
                "service": services_used or "General services",
                "description": "As described by the organisation",
                "dataRole": data_role,
            }
        ],
        "certifications": [
            {
                "name": "ISO 27001",
                "status": "Claimed — unverified (mock)",
                "validUntil": "Unknown",
                "evidence": "Mock assessment — no research performed",
            },
            {
                "name": "SOC 2 Type II",
                "status": "Claimed — unverified (mock)",
                "validUntil": "Unknown",
                "evidence": "Mock assessment — no research performed",
            },
        ],
        "certificationNotes": "Mock assessment — certification status must be verified manually.",
        "breachHistory": [],
        "cveHistory": [],
        "enforcementActions": [],
        "confidentialityControls": [
            {"control": "Encryption at rest", "implementation": "Assumed AES-256 (mock)", "rating": "Adequate"},
            {"control": "Encryption in transit", "implementation": "Assumed TLS 1.2+ (mock)", "rating": "Adequate"},
            {"control": "Access control (MFA/RBAC)", "implementation": "Not assessed (mock)", "rating": "Not Assessed"},
        ],
        "integrityControls": [
            {"control": "Change management", "implementation": "Not assessed (mock)", "rating": "Not Assessed"},
            {"control": "Audit logging", "implementation": "Not assessed (mock)", "rating": "Not Assessed"},
        ],
        "availabilityControls": [
            {"control": "SLA commitments", "implementation": "Not assessed (mock)", "rating": "Not Assessed"},
            {"control": "Disaster recovery", "implementation": "Not assessed (mock)", "rating": "Not Assessed"},
        ],
        "confidentialityScore": "3/5",
        "integrityScore": "2/5",
        "availabilityScore": "2/5",
        "dataProcessing": {"Processes personal data?": "Assumed yes (mock)"},
        "dataStorage": {"Storage location": "Unknown (mock)"},
        "dataTransmission": {"Encrypted in transit?": "Assumed yes (mock)"},
        "gdprDpa": {"DPA in place": "Requires verification (mock)"},
        "gdprDataSubjectRights": {"DSAR support": "Requires verification (mock)"},
        "gdprInternationalTransfers": {"Transfer mechanism": "Requires verification (mock)"},
        "supplierFormAvailable": False,
        "supplierFormVerification": (
            "Mock assessment — no supplier evaluation form was reviewed and no "
            "independent verification was performed."
        ),
        "inherentRisks": [
            {
                "factor": "Third-party data processing (placeholder)",
                "likelihood": 3,
                "likelihoodLabel": "Possible",
                "impact": 4,
                "impactLabel": "Major",
                "score": 12,
            },
            {
                "factor": "Service availability dependency (placeholder)",
                "likelihood": 2,
                "likelihoodLabel": "Unlikely",
                "impact": 3,
                "impactLabel": "Moderate",
                "score": 6,
            },
        ],
        "inherentRiskScore": 12,
        "inherentRiskLevel": "MEDIUM",
        "controlEffectiveness": (
            "Mock assessment — control effectiveness assumed at 50% pending real research."
        ),
        "controlEffectivenessPercent": 50,
        "residualRiskScore": 6,
        "residualRiskLevel": "MEDIUM",
        "mandatoryActions": [
            {
                "number": 1,
                "action": "Verify ISO 27001 and SOC 2 Type II certifications",
                "owner": "Vendor Manager",
                "dueDate": due_30,
                "priority": "High",
            },
            {
                "number": 2,
                "action": "Review and execute GDPR data processing agreement",
                "owner": "Data Protection Officer",
                "dueDate": due_60,
                "priority": "Medium",
            },
        ],
        "monitoringRequirements": [
            "Re-run a live AI assessment once an Anthropic API key is configured",
            "Annual reassessment of vendor security posture",
        ],
        "primarySources": ["Mock assessment — no sources consulted"],
        "incidentReports": [],
        "thirdPartyAnalysis": [],
        "assessmentDate": today.isoformat(),
        "assessor": f"{ASSESSOR_NAME} — MOCK MODE",
        "version": REPORT_VERSION,
    }


# ---------------------------------------------------------------------------
# Anthropic call plumbing
# ---------------------------------------------------------------------------

def _block_type(block: Any) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _collect_sources(content: List[Any], seen: List[str]) -> None:
    """Append cited URLs from web_search_tool_result blocks into `seen` (deduped)."""
    for block in content or []:
        if _block_type(block) != "web_search_tool_result":
            continue
        results = block.get("content") if isinstance(block, dict) else getattr(block, "content", None)
        if not isinstance(results, (list, tuple)):
            continue
        for item in results:
            url = item.get("url") if isinstance(item, dict) else getattr(item, "url", None)
            if url and url not in seen:
                seen.append(url)


def _find_submission(content: List[Any]) -> Optional[Any]:
    """Return the first submit_assessment tool_use block, if any."""
    for block in content or []:
        if _block_type(block) != "tool_use":
            continue
        name = block.get("name") if isinstance(block, dict) else getattr(block, "name", None)
        if name == "submit_assessment":
            return block
    return None


def _tool_use_input(block: Any) -> Dict[str, Any]:
    raw = block.get("input") if isinstance(block, dict) else getattr(block, "input", None)
    return dict(raw) if isinstance(raw, dict) else {}


def _tool_use_id(block: Any) -> str:
    return block.get("id") if isinstance(block, dict) else getattr(block, "id", "")


def _call_anthropic_for_report(
    user_prompt: str,
    model: str,
    sources: List[str],
) -> Dict[str, Any]:
    """
    Run the research + synthesis conversation against the Anthropic API.

    Every request offers both the server-side web_search tool and the
    submit_assessment client tool. The system prompt instructs the model to
    call submit_assessment when research is complete; the loop feeds
    tool_use/tool_result turns back (handling pause_turn, invalid submissions,
    and end_turn-without-submission nudges) until a valid submission arrives.

    An invalid submission (bad enums / missing fields) is retried exactly once
    by returning the validation errors as an error tool_result.
    """
    import anthropic

    client = anthropic.Anthropic(timeout=540.0)
    messages: List[Dict[str, Any]] = [{"role": "user", "content": user_prompt}]
    tools = [WEB_SEARCH_TOOL, SUBMIT_ASSESSMENT_TOOL]
    invalid_attempts = 0
    nudges = 0

    for _ in range(MAX_LOOP_ITERATIONS):
        response = client.messages.create(
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=DPSIA_SYSTEM_PROMPT,
            messages=messages,
            tools=tools,
        )
        content = list(response.content or [])
        _collect_sources(content, sources)

        submission = _find_submission(content)
        if submission is not None:
            report = _tool_use_input(submission)
            problems = validate_report(report)
            if not problems:
                return report
            invalid_attempts += 1
            logger.warning(
                "submit_assessment validation failed (attempt %d): %s",
                invalid_attempts, problems,
            )
            if invalid_attempts > 1:
                raise VendorAssessmentError(
                    f"Model produced an invalid assessment twice: {'; '.join(problems)}"
                )
            # Feed the validation errors back as an error tool_result and retry.
            messages.append({"role": "assistant", "content": content})
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": _tool_use_id(submission),
                    "is_error": True,
                    "content": (
                        "The submitted report was rejected: "
                        + "; ".join(problems)
                        + ". Call submit_assessment again with corrected values."
                    ),
                }],
            })
            continue

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "pause_turn":
            # Server-side tool loop paused — re-send to continue.
            messages.append({"role": "assistant", "content": content})
            continue

        # Model finished (end_turn or otherwise) without submitting — nudge it
        # to call the client tool, at most twice.
        nudges += 1
        if nudges > 2:
            raise VendorAssessmentError(
                "Model ended the conversation without calling submit_assessment"
            )
        messages.append({"role": "assistant", "content": content})
        messages.append({
            "role": "user",
            "content": (
                "Now call the submit_assessment tool with the completed DPSIA "
                "report based on the research above."
            ),
        })

    raise VendorAssessmentError(
        f"No valid assessment produced within {MAX_LOOP_ITERATIONS} model calls"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_assessment(
    vendor_name: str,
    vendor_description: str,
    vendor_website: str,
    services_used: str,
    data_role: str = "Processor",
    assessment_type: str = "new",
    client_name: str = "Client",
    additional_context: str = "",
    research_context: str = "",
) -> Dict[str, Any]:
    """
    Run a full vendor AI assessment. Synchronous — call from a Celery worker.

    Returns a dict with:
        report_json, report_markdown, rag_status, recommendation,
        risk_score, risk_level, executive_summary, research_sources,
        processing_time_ms
    """
    start = time.monotonic()
    sources: List[str] = []

    if is_mock_mode():
        logger.info("Vendor assessment engine running in MOCK mode for %s", vendor_name)
        report = build_mock_report(vendor_name, vendor_website, services_used, data_role)
    else:
        model = os.getenv("VENDOR_AI_MODEL", DEFAULT_MODEL)
        logger.info(
            "Vendor assessment engine calling Anthropic (model=%s) for %s",
            model, vendor_name,
        )
        user_prompt = build_user_prompt(
            vendor_name=vendor_name,
            vendor_description=vendor_description or f"{vendor_name} - third-party vendor",
            vendor_website=vendor_website,
            services_used=services_used,
            data_role=data_role,
            assessment_type=assessment_type,
            client_name=client_name,
            additional_context=additional_context,
            research_context=research_context,
        )
        report = _call_anthropic_for_report(user_prompt, model, sources)

    problems = validate_report(report)
    if problems:
        raise VendorAssessmentError(f"Assessment failed validation: {'; '.join(problems)}")

    report.setdefault("assessmentDate", date.today().isoformat())
    report.setdefault("assessor", ASSESSOR_NAME)
    report.setdefault("version", REPORT_VERSION)

    risk_score = report.get("residualRiskScore")
    if risk_score is None:
        risk_score = report.get("inherentRiskScore")
    risk_level = report.get("residualRiskLevel") or report.get("inherentRiskLevel")
    risk_level = risk_level.lower() if isinstance(risk_level, str) else None

    research_sources = sources or list(report.get("primarySources", []))
    report_markdown = render_markdown(report, client_name, assessment_type)

    return {
        "report_json": report,
        "report_markdown": report_markdown,
        "rag_status": report["ragStatus"],
        "recommendation": report["recommendation"],
        "risk_score": risk_score,
        "risk_level": risk_level,
        "executive_summary": report.get("executiveSummary", ""),
        "research_sources": research_sources,
        "processing_time_ms": int((time.monotonic() - start) * 1000),
    }
