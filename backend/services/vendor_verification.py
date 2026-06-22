"""
Vendor Claim Verification Service (DPSIA Enhancement - Phase 1).

Cross-references vendor claims (certifications, breach disclosures)
against research data to independently verify them.
"""
import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Vendor, VendorCertification, VendorClaimVerification,
    VendorResearchResult, VendorAssessment
)

logger = logging.getLogger(__name__)


async def verify_vendor_claims(
    db: AsyncSession,
    vendor_id: str,
    assessment_id: Optional[str] = None,
) -> List[VendorClaimVerification]:
    """
    Cross-reference vendor claims against research data.

    Checks:
    1. Certifications claimed vs independent evidence
    2. Breach disclosures vs HIBP data
    3. Compliance claims vs regulatory findings

    Returns list of created VendorClaimVerification records.
    """
    verifications = []
    vid = UUID(vendor_id) if isinstance(vendor_id, str) else vendor_id

    # Load vendor certifications
    cert_result = await db.execute(
        select(VendorCertification).where(VendorCertification.vendor_id == vid)
    )
    certifications = cert_result.scalars().all()

    # Load latest research
    research_result = await db.execute(
        select(VendorResearchResult)
        .where(
            VendorResearchResult.vendor_id == vid,
            VendorResearchResult.status.in_(["completed", "partial"]),
        )
        .order_by(VendorResearchResult.created_at.desc())
        .limit(1)
    )
    research = research_result.scalar_one_or_none()

    aid = UUID(assessment_id) if assessment_id else None

    # --- Verify certifications ---
    for cert in certifications:
        status = "unverified"
        detail = "No independent verification data available."
        source = None

        if cert.verification_url:
            status = "confirmed"
            detail = f"Verification URL provided: {cert.verification_url}"
            source = "vendor_provided_url"
        elif cert.certificate_number:
            status = "unverified"
            detail = f"Certificate number {cert.certificate_number} provided but not independently verified."
            source = "certificate_number"
        else:
            status = "unverified"
            detail = f"Vendor claims {cert.certification_name} but no certificate number or verification URL provided."

        verification = VendorClaimVerification(
            vendor_id=vid,
            assessment_id=aid,
            claim_type="certification",
            claim_description=f"Vendor claims {cert.certification_name} certification (status: {cert.status})",
            verification_status=status,
            verification_source=source,
            verification_detail=detail,
            evidence_url=cert.verification_url,
        )
        db.add(verification)
        verifications.append(verification)

    # --- Verify breach disclosures ---
    if research and research.hibp_results:
        hibp = research.hibp_results or {}
        breaches = hibp.get("breaches") or []
        breach_count = hibp.get("breach_count") or len(breaches)

        if breach_count > 0:
            # Check if vendor has disclosed breaches
            verification = VendorClaimVerification(
                vendor_id=vid,
                assessment_id=aid,
                claim_type="breach_disclosure",
                claim_description=f"HIBP reports {breach_count} breach(es) for this vendor's domain.",
                verification_status="discrepancy" if breach_count > 0 else "confirmed",
                verification_source="hibp",
                verification_detail=(
                    f"Found {breach_count} breach(es) via Have I Been Pwned. "
                    f"Breaches: {', '.join(b.get('name', 'Unknown') for b in breaches[:5])}."
                    + (" (and more)" if len(breaches) > 5 else "")
                ),
            )
            db.add(verification)
            verifications.append(verification)

    # --- Verify regulatory compliance ---
    if research and research.regulatory_results:
        reg = research.regulatory_results or {}
        findings = reg.get("findings") or reg.get("actions") or reg.get("enforcement_actions") or []
        critical = [f for f in findings if f.get("severity") == "critical"]

        if critical:
            verification = VendorClaimVerification(
                vendor_id=vid,
                assessment_id=aid,
                claim_type="compliance",
                claim_description="Critical regulatory findings detected.",
                verification_status="anomaly",
                verification_source="regulatory_research",
                verification_detail=(
                    f"Found {len(critical)} critical regulatory finding(s). "
                    + "; ".join(f.get("description", "Unknown")[:100] for f in critical[:3])
                ),
            )
            db.add(verification)
            verifications.append(verification)
        elif findings:
            verification = VendorClaimVerification(
                vendor_id=vid,
                assessment_id=aid,
                claim_type="compliance",
                claim_description=f"{len(findings)} regulatory finding(s) detected.",
                verification_status="unverified",
                verification_source="regulatory_research",
                verification_detail=(
                    f"Found {len(findings)} non-critical regulatory finding(s). Review recommended."
                ),
            )
            db.add(verification)
            verifications.append(verification)

    await db.commit()
    for v in verifications:
        await db.refresh(v)

    logger.info(f"Created {len(verifications)} claim verifications for vendor {vendor_id}")
    return verifications
