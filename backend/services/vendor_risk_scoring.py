"""
Vendor Risk Scoring Service (Issue #60).

Implements deterministic risk scoring using weighted factors
and AI-powered analysis via Claude API.

Weight distribution:
- Breach history: 25%
- Certification status: 20%
- CVE severity: 20%
- Regulatory actions: 15%
- Data handling risk: 20%

Risk thresholds (aligned with platform):
- 1-4: Low (Approve)
- 5-9: Medium (Approve with monitoring)
- 10-16: High (Conditional approval)
- 17-25: Critical (Reject or escalate)
"""
import logging
import math
import os
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import VendorAssessment, Vendor, VendorResearchResult, OrganizationRiskProfile

logger = logging.getLogger(__name__)

# Factor weights (must sum to 1.0)
FACTOR_WEIGHTS = {
    "breach": 0.25,
    "certification": 0.20,
    "cve": 0.20,
    "regulatory": 0.15,
    "data_handling": 0.20,
}

# Risk level thresholds (platform defaults)
DEFAULT_THRESHOLDS = {
    "low_max": 4,
    "medium_max": 9,
    "high_max": 16,
}

# Recommendations per risk level
RECOMMENDATIONS = {
    "low": "Approve",
    "medium": "Approve with monitoring",
    "high": "Conditional approval",
    "critical": "Reject or escalate",
}


def get_risk_level(score: int, low_max: int = 4, medium_max: int = 9, high_max: int = 16) -> str:
    """Calculate risk level from score using configurable thresholds."""
    if score <= low_max:
        return "low"
    if score <= medium_max:
        return "medium"
    if score <= high_max:
        return "high"
    return "critical"


def calculate_weighted_score(
    breach_score: int = 0,
    certification_score: int = 0,
    cve_score: int = 0,
    regulatory_score: int = 0,
    data_handling_score: int = 0,
) -> float:
    """
    Calculate weighted risk score from individual factor scores.
    Each factor is scored 0-25. The weighted result is 0-25.
    """
    weighted = (
        breach_score * FACTOR_WEIGHTS["breach"]
        + certification_score * FACTOR_WEIGHTS["certification"]
        + cve_score * FACTOR_WEIGHTS["cve"]
        + regulatory_score * FACTOR_WEIGHTS["regulatory"]
        + data_handling_score * FACTOR_WEIGHTS["data_handling"]
    )
    return weighted


def calculate_likelihood_impact(weighted_score: float) -> Tuple[int, int]:
    """
    Derive likelihood and impact from the weighted score.
    Maps 0-25 weighted score to 1-5 likelihood and 1-5 impact.
    Uses square-root mapping so the product approximates the weighted score.
    """
    # Clamp to valid range
    clamped = max(1, min(25, round(weighted_score)))
    # Use sqrt to distribute evenly
    sqrt_val = math.sqrt(clamped)
    likelihood = max(1, min(5, round(sqrt_val)))
    impact = max(1, min(5, round(clamped / likelihood)))
    return likelihood, impact


async def score_from_research(
    research_data: Dict[str, Any],
) -> Dict[str, int]:
    """
    Derive individual factor scores from research data.

    Returns dict with breach_score, certification_score, cve_score,
    regulatory_score, data_handling_score (each 0-25).
    """
    scores = {
        "breach_score": 0,
        "certification_score": 0,
        "cve_score": 0,
        "regulatory_score": 0,
        "data_handling_score": 0,
    }

    # Breach scoring from HIBP results
    hibp = research_data.get("hibp_results") or {}
    breaches = hibp.get("breaches") or []
    if isinstance(breaches, list):
        breach_count = len(breaches)
        if breach_count == 0:
            scores["breach_score"] = 0
        elif breach_count <= 2:
            scores["breach_score"] = 8
        elif breach_count <= 5:
            scores["breach_score"] = 15
        else:
            scores["breach_score"] = 22

    # Certification scoring (lower = better certifications)
    # Start at 15 (no certs) and reduce based on what they have
    risk_indicators = research_data.get("risk_indicators") or {}
    cert_signals = risk_indicators.get("certifications") or []
    if cert_signals:
        scores["certification_score"] = max(0, 15 - len(cert_signals) * 5)
    else:
        scores["certification_score"] = 15

    # CVE scoring from CVE/NVD results
    cve_data = research_data.get("cve_nvd_results") or {}
    vulnerabilities = cve_data.get("vulnerabilities") or []
    if isinstance(vulnerabilities, list):
        critical_count = sum(
            1 for v in vulnerabilities
            if (v.get("severity") or "").lower() in ("critical", "high")
        )
        if critical_count == 0 and len(vulnerabilities) == 0:
            scores["cve_score"] = 0
        elif critical_count == 0:
            scores["cve_score"] = 5
        elif critical_count <= 3:
            scores["cve_score"] = 12
        elif critical_count <= 10:
            scores["cve_score"] = 18
        else:
            scores["cve_score"] = 23

    # Regulatory scoring
    regulatory = research_data.get("regulatory_results") or {}
    actions = regulatory.get("actions") or regulatory.get("enforcement_actions") or []
    if isinstance(actions, list):
        if len(actions) == 0:
            scores["regulatory_score"] = 0
        elif len(actions) <= 2:
            scores["regulatory_score"] = 10
        else:
            scores["regulatory_score"] = 20

    # Data handling (default moderate risk unless we have specific signals)
    overall_signal = research_data.get("overall_risk_signal") or "medium"
    signal_map = {"low": 3, "medium": 10, "high": 18, "critical": 23}
    scores["data_handling_score"] = signal_map.get(overall_signal.lower(), 10)

    return scores


async def calculate_vendor_risk(
    db: AsyncSession,
    assessment: VendorAssessment,
    research_data: Optional[Dict[str, Any]] = None,
    org_thresholds: Optional[Tuple[int, int, int]] = None,
) -> VendorAssessment:
    """
    Calculate and persist risk scores for a vendor assessment.

    If research_data is provided, derives factor scores from it.
    Otherwise uses whatever scores are already on the assessment.

    Returns the updated assessment.
    """
    # Load org thresholds if not provided
    if org_thresholds is None:
        # Get vendor to find org_id
        vendor_result = await db.execute(
            select(Vendor).where(Vendor.id == assessment.vendor_id)
        )
        vendor = vendor_result.scalar_one_or_none()
        if vendor:
            profile_result = await db.execute(
                select(OrganizationRiskProfile).where(
                    OrganizationRiskProfile.organization_id == vendor.organization_id
                )
            )
            profile = profile_result.scalar_one_or_none()
            if profile:
                org_thresholds = (profile.low_max, profile.medium_max, profile.high_max)

    if org_thresholds is None:
        org_thresholds = (4, 9, 16)

    low_max, medium_max, high_max = org_thresholds

    # Guard: if no research data and all factor scores are NULL but we already
    # have a holistic risk score (e.g. from DPSIA), preserve it rather than
    # recalculating from zeros.
    if not research_data:
        all_factors_null = all(
            getattr(assessment, f) is None
            for f in [
                "breach_score", "certification_score", "cve_score",
                "regulatory_score", "data_handling_score",
            ]
        )
        if all_factors_null and assessment.final_risk_score is not None:
            return assessment

    # If research data is provided, derive scores
    if research_data:
        factor_scores = await score_from_research(research_data)
        assessment.breach_score = factor_scores["breach_score"]
        assessment.certification_score = factor_scores["certification_score"]
        assessment.cve_score = factor_scores["cve_score"]
        assessment.regulatory_score = factor_scores["regulatory_score"]
        assessment.data_handling_score = factor_scores["data_handling_score"]

    # Calculate weighted score
    weighted = calculate_weighted_score(
        breach_score=assessment.breach_score or 0,
        certification_score=assessment.certification_score or 0,
        cve_score=assessment.cve_score or 0,
        regulatory_score=assessment.regulatory_score or 0,
        data_handling_score=assessment.data_handling_score or 0,
    )

    # Derive likelihood and impact
    likelihood, impact = calculate_likelihood_impact(weighted)
    assessment.likelihood = likelihood
    assessment.impact = impact

    # Calculate final score
    final_score = likelihood * impact
    assessment.final_risk_score = final_score
    assessment.risk_level = get_risk_level(final_score, low_max, medium_max, high_max)

    # Calculate inherent risk and control effectiveness (DPSIA Enhancement)
    if research_data:
        try:
            inherent_score, inherent_level = await calculate_inherent_risk(research_data)
            assessment.inherent_risk_score = inherent_score
            assessment.inherent_risk_level = inherent_level

            effectiveness = await calculate_control_effectiveness(db, assessment, str(assessment.vendor_id))
            assessment.control_effectiveness_pct = effectiveness
        except Exception as exc:
            logger.warning(f"Failed to calculate inherent risk / effectiveness: {exc}")

    return assessment


async def generate_ai_analysis(
    vendor_name: str,
    domain: Optional[str],
    research_data: Dict[str, Any],
    factor_scores: Dict[str, int],
    final_score: int,
    risk_level: str,
) -> Optional[str]:
    """
    Generate AI-powered analysis summary using Claude.

    Returns markdown-formatted analysis or None if AI unavailable.
    """
    try:
        import anthropic

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning("ANTHROPIC_API_KEY not set - skipping AI analysis")
            return None

        client = anthropic.Anthropic(api_key=api_key)

        prompt = f"""Analyse this vendor security data and provide a comprehensive assessment:

1. Executive Summary (3-4 sentences covering overall risk posture)
2. Top 3-5 Risk Factors (with specific evidence)
3. Certification Verification Status (cross-reference claims with evidence)
4. Inherent Risk Assessment (risk before controls are applied)
5. Control Effectiveness (how well existing controls mitigate risks)
6. Recommended Actions (specific, actionable items with priority)
7. Overall Recommendation (Approve/Conditional/Reject with rationale)

Vendor: {vendor_name}
Domain: {domain or 'N/A'}
Research Data: {_summarise_research(research_data)}

Risk Scores:
- Breach History: {factor_scores.get('breach_score', 0)}/25 (weight: 25%)
- Certification: {factor_scores.get('certification_score', 0)}/25 (weight: 20%)
- CVE Severity: {factor_scores.get('cve_score', 0)}/25 (weight: 20%)
- Regulatory: {factor_scores.get('regulatory_score', 0)}/25 (weight: 15%)
- Data Handling: {factor_scores.get('data_handling_score', 0)}/25 (weight: 20%)
- Final Score: {final_score}/25 ({risk_level})

Use RAG ratings (Red/Amber/Green) where applicable.
Provide a concise, actionable analysis. Use British English."""

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        return message.content[0].text

    except ImportError:
        logger.warning("anthropic package not installed - skipping AI analysis")
        return None
    except Exception as exc:
        logger.error(f"AI analysis failed: {exc}")
        return None


def _summarise_research(data: Dict[str, Any]) -> str:
    """Create a condensed summary of research data for the AI prompt."""
    parts = []

    hibp = data.get("hibp_results") or {}
    breaches = hibp.get("breaches") or []
    if breaches:
        parts.append(f"HIBP: {len(breaches)} breaches found")
    else:
        parts.append("HIBP: No breaches found")

    cve = data.get("cve_nvd_results") or {}
    vulns = cve.get("vulnerabilities") or []
    if vulns:
        parts.append(f"CVE/NVD: {len(vulns)} vulnerabilities")
    else:
        parts.append("CVE/NVD: No vulnerabilities found")

    regulatory = data.get("regulatory_results") or {}
    actions = regulatory.get("actions") or regulatory.get("enforcement_actions") or []
    if actions:
        parts.append(f"Regulatory: {len(actions)} enforcement actions")
    else:
        parts.append("Regulatory: No enforcement actions")

    summary = data.get("summary") or ""
    if summary:
        parts.append(f"Summary: {summary[:500]}")

    return "\n".join(parts)


async def calculate_inherent_risk(
    research_data: Dict[str, Any],
) -> Tuple[int, str]:
    """
    Calculate inherent risk from research data only (excludes certifications and compensating controls).

    Returns (score, level) tuple.
    """
    scores = await score_from_research(research_data)
    # Inherent risk uses only breach, CVE, regulatory, and data handling
    # Certification is excluded (it represents a control, not inherent risk)
    inherent_weighted = (
        scores["breach_score"] * 0.30
        + scores["cve_score"] * 0.25
        + scores["regulatory_score"] * 0.20
        + scores["data_handling_score"] * 0.25
    )
    inherent_score = max(1, min(25, round(inherent_weighted)))
    inherent_level = get_risk_level(inherent_score)
    return inherent_score, inherent_level


async def calculate_control_effectiveness(
    db: AsyncSession,
    assessment: VendorAssessment,
    vendor_id: str,
) -> int:
    """
    Calculate control effectiveness percentage (0-100) from CIA control scores and certifications.
    """
    effectiveness_points = 0
    max_points = 0

    # CIA control scores contribute up to 60%
    from models import VendorCIAControl
    cia_result = await db.execute(
        select(VendorCIAControl).where(VendorCIAControl.assessment_id == assessment.id)
    )
    cia_controls = cia_result.scalars().all()

    if cia_controls:
        total_score = sum(c.score or 0 for c in cia_controls)
        max_score = len(cia_controls) * 5
        if max_score > 0:
            effectiveness_points += (total_score / max_score) * 60
        max_points += 60
    else:
        # Use high-level CIA scores if no detailed controls
        cia_scores = [
            assessment.confidentiality_score,
            assessment.integrity_score,
            assessment.availability_score,
        ]
        scored = [s for s in cia_scores if s is not None]
        if scored:
            avg = sum(scored) / len(scored)
            effectiveness_points += (avg / 5) * 60
        max_points += 60

    # Certifications contribute up to 40%
    from models import VendorCertification
    vid = UUID(vendor_id) if isinstance(vendor_id, str) else vendor_id
    cert_result = await db.execute(
        select(VendorCertification).where(
            VendorCertification.vendor_id == vid,
            VendorCertification.status == "valid",
        )
    )
    valid_certs = cert_result.scalars().all()
    # Each valid cert contributes up to 10%, max 40%
    cert_contribution = min(len(valid_certs) * 10, 40)
    effectiveness_points += cert_contribution
    max_points += 40

    if max_points == 0:
        return 0

    return max(0, min(100, round((effectiveness_points / max_points) * 100)))
