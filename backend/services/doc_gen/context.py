"""
Organisation context assembly for document generation.

This module replaces ``scf-doc-gen``'s ``ScfClient`` entirely. The standalone
tool made roughly 40 HTTP round-trips per run against the platform's own API,
paginating scoped controls, then fetching assessment objectives one control at
a time. Inside the platform those are joins, so the network layer, the API key,
the response cache, and the retry logic all dissolve into a handful of queries.

That deletion is the point of the integration. What survives is the *shape* of
the data — :class:`OrganisationContext` mirrors the TypeScript interface field
for field, so the ported generators need no rewriting to consume it.

Everything here is synchronous. Generation runs in a Celery worker, outside the
async event loop, using the sync engine pattern established by
``tasks_recipe_generation``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Statuses that count as "the organisation has done something about it".
#: Used for the coverage percentages that Tier 1 documents report.
IMPLEMENTED_STATUSES = ("implemented", "monitored", "ready_for_review")


# ---------------------------------------------------------------------------
# Data shapes — mirrors of src/client/types.ts
# ---------------------------------------------------------------------------


@dataclass
class Domain:
    identifier: str
    name: str
    principle: Optional[str] = None
    principle_intent: Optional[str] = None
    order: int = 0


@dataclass
class EnrichedControl:
    """A scoped control joined to its catalog definition and objectives.

    The standalone tool built this by calling three endpoints and stitching
    the results in memory. Here it is one query with two joins.
    """

    scf_id: str
    control_name: str
    control_description: str
    domain_identifier: str
    implementation_status: str
    maturity_level: Optional[str] = None
    owner: Optional[str] = None
    priority: Optional[str] = None
    implementation_notes: Optional[str] = None
    selection_reason: Optional[str] = None
    assigned_to: Optional[str] = None
    control_question: Optional[str] = None
    validation_cadence: Optional[str] = None
    control_weighting: Optional[int] = None
    nist_csf_function: Optional[str] = None
    pptdf_people: bool = False
    pptdf_process: bool = False
    pptdf_technology: bool = False
    pptdf_data: bool = False
    pptdf_facility: bool = False
    assessment_objectives: List[Dict[str, Any]] = field(default_factory=list)
    evidence_mappings: List[Dict[str, Any]] = field(default_factory=list)

    def to_fingerprint_dict(self) -> Dict[str, Any]:
        """The projection :mod:`fingerprint` hashes.

        Deliberately narrow: only fields that change generated prose belong
        here. Adding ``updated_at`` would make every document permanently
        stale.
        """
        return {
            "scf_id": self.scf_id,
            "control_name": self.control_name,
            "control_description": self.control_description,
            "implementation_status": self.implementation_status,
            "maturity_level": self.maturity_level,
            "owner": self.owner,
            "implementation_notes": self.implementation_notes,
            "assessment_objectives": [
                {"ao_id": ao.get("ao_id", ""), "objective_text": ao.get("objective_text", "")}
                for ao in self.assessment_objectives
            ],
        }


@dataclass
class DomainWithControls:
    domain: Domain
    controls: List[EnrichedControl] = field(default_factory=list)
    maturity_breakdown: Dict[str, int] = field(default_factory=dict)
    status_breakdown: Dict[str, int] = field(default_factory=dict)


@dataclass
class OrganisationContext:
    """Everything a generator can read. Assembled once per generation run."""

    organization_id: str
    name: str
    generated_at: str
    catalog_version: Optional[str] = None
    industry: Optional[str] = None

    domains: List[DomainWithControls] = field(default_factory=list)
    all_controls: List[EnrichedControl] = field(default_factory=list)
    evidence_items: List[Dict[str, Any]] = field(default_factory=list)
    risk_assessments: List[Dict[str, Any]] = field(default_factory=list)
    systems: List[Dict[str, Any]] = field(default_factory=list)
    vendors: List[Dict[str, Any]] = field(default_factory=list)
    risk_profile: Dict[str, Any] = field(default_factory=dict)
    frameworks: List[Dict[str, Any]] = field(default_factory=list)

    maturity_distribution: Dict[str, int] = field(default_factory=dict)
    status_distribution: Dict[str, int] = field(default_factory=dict)
    total_scoped_controls: int = 0
    total_domains: int = 0

    def domain(self, identifier: str) -> Optional[DomainWithControls]:
        """Look up one domain bundle, or ``None`` if it is not in scope."""
        for bundle in self.domains:
            if bundle.domain.identifier == identifier:
                return bundle
        return None

    def implemented_count(self) -> int:
        return sum(
            1 for c in self.all_controls
            if (c.implementation_status or "") in IMPLEMENTED_STATUSES
        )

    def coverage_percent(self) -> float:
        if not self.all_controls:
            return 0.0
        return round(100.0 * self.implemented_count() / len(self.all_controls), 1)


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def _breakdown(values) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        key = v or "unspecified"
        out[key] = out.get(key, 0) + 1
    return out


def _resolve_domain_name(
    domain_filter: str,
    name_by_code: Dict[str, str],
    code_by_name: Dict[str, str],
) -> str:
    """Translate a caller's domain filter into the catalog's display name.

    Controls carry ``scf_domain`` as the display name, so a filter expressed as
    an identifier ("GOV") would match nothing. Accept either form, in either
    case, and fall through unchanged when neither matches — an unknown domain
    then yields an empty context, which the pipeline reports as "no controls in
    scope for that domain" rather than silently generating the whole estate.
    """
    wanted = (domain_filter or "").strip()
    by_code = name_by_code.get(wanted.upper())
    if by_code:
        return by_code
    for name in code_by_name:
        if name.lower() == wanted.lower():
            return name
    return wanted


def build_context(
    session: Session,
    organization_id: str,
    *,
    domain_filter: Optional[str] = None,
    include_evidence: bool = True,
    include_risks: bool = True,
    include_systems: bool = True,
) -> OrganisationContext:
    """Assemble an :class:`OrganisationContext` from the database.

    Args:
        session: A synchronous SQLAlchemy session.
        organization_id: The organisation to build context for. Callers must
            resolve this from the membership dependency, never from a request
            path parameter — see ``api/documents.py``.
        domain_filter: Restrict controls to one SCF domain. Domain policies
            pass this so a single-domain generation does not read, or
            fingerprint, the whole estate.
        include_evidence: Skip the evidence join for generators that do not
            need it.
        include_risks: Skip risk assessments likewise.
        include_systems: Skip systems and vendors likewise.

    Returns:
        A populated context. Absent optional sections are empty lists, never
        ``None`` — generators index into these without guarding.
    """
    # Imported here rather than at module scope: this module is imported by the
    # Celery task, and models pulls in the whole ORM graph.
    from models import (
        EvidenceTracking,
        Organization,
        OrganizationCatalogState,
        OrganizationRiskProfile,
        RiskAssessment,
        ScopedControl,
        System,
        Vendor,
    )
    from catalog_models import (
        SCFCatalogAssessmentObjective,
        SCFCatalogControl,
        SCFCatalogDomain,
        SCFCatalogEvidence,
    )

    org = session.query(Organization).filter(Organization.id == organization_id).one_or_none()
    if org is None:
        raise ValueError(f"Organisation {organization_id} not found")

    catalog_state = (
        session.query(OrganizationCatalogState)
        .filter(OrganizationCatalogState.organization_id == organization_id)
        .one_or_none()
    )
    catalog_version = catalog_state.reconciled_catalog_version if catalog_state else None

    # --- domain metadata ----------------------------------------------------
    # Loaded before the controls query because a domain filter has to be
    # translated first: ``scf_catalog_controls.scf_domain`` stores the domain's
    # DISPLAY NAME ("Governance & Risk Management"), while callers, stored
    # documents and filenames all speak the short identifier ("GOV"). Keeping
    # both directions lets either form be accepted and lets the grouping below
    # key on the identifier, which is what ``OrganisationContext.domain()``
    # looks up.
    domain_rows = (
        session.query(SCFCatalogDomain)
        .filter(SCFCatalogDomain.status == "active")
        .order_by(SCFCatalogDomain.order)
        .all()
    )
    domain_meta = {d.identifier: d for d in domain_rows}
    code_by_name = {d.name: d.identifier for d in domain_rows if d.name}
    name_by_code = {d.identifier: d.name for d in domain_rows}

    # --- controls, joined to their catalog definitions ---------------------
    q = (
        session.query(ScopedControl, SCFCatalogControl)
        .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .filter(
            ScopedControl.organization_id == organization_id,
            ScopedControl.selected.is_(True),
        )
    )
    if domain_filter:
        q = q.filter(
            SCFCatalogControl.scf_domain
            == _resolve_domain_name(domain_filter, name_by_code, code_by_name)
        )
    rows = q.order_by(ScopedControl.scf_id).all()

    scf_ids = [sc.scf_id for sc, _ in rows]

    # --- assessment objectives, one query for every control ----------------
    objectives_by_control: Dict[str, List[Dict[str, Any]]] = {}
    if scf_ids:
        ao_rows = (
            session.query(SCFCatalogAssessmentObjective)
            .filter(
                SCFCatalogAssessmentObjective.scf_id.in_(scf_ids),
                SCFCatalogAssessmentObjective.status == "active",
            )
            .order_by(SCFCatalogAssessmentObjective.ao_id)
            .all()
        )
        for ao in ao_rows:
            objectives_by_control.setdefault(ao.scf_id, []).append({
                "ao_id": ao.ao_id,
                "objective_text": ao.objective_text,
                "assessment_rigor": ao.assessment_rigor,
                "ao_origins": ao.ao_origins,
                "assessment_procedure": ao.assessment_procedure,
                "expected_results": ao.expected_results,
            })

    # --- evidence requirements per control ---------------------------------
    evidence_by_control: Dict[str, List[Dict[str, Any]]] = {}
    if include_evidence and scf_ids:
        wanted = set(scf_ids)
        for ev in session.query(SCFCatalogEvidence).filter(
            SCFCatalogEvidence.status == "active"
        ).all():
            for mapped in (ev.control_mappings or []):
                if mapped in wanted:
                    evidence_by_control.setdefault(mapped, []).append({
                        "evidence_id": ev.evidence_id,
                        "title": ev.artifact_title,
                        "description": ev.artifact_description,
                        "area_of_focus": ev.area_of_focus,
                    })

    controls: List[EnrichedControl] = []
    for scoped, catalog in rows:
        controls.append(EnrichedControl(
            scf_id=scoped.scf_id,
            control_name=catalog.control_name or "",
            control_description=catalog.control_description or "",
            domain_identifier=code_by_name.get(
                catalog.scf_domain or "", catalog.scf_domain or ""
            ),
            implementation_status=scoped.implementation_status or "not_started",
            maturity_level=scoped.maturity_level,
            owner=scoped.owner,
            priority=scoped.priority,
            implementation_notes=scoped.implementation_notes,
            selection_reason=scoped.selection_reason,
            assigned_to=scoped.assigned_to,
            control_question=scoped.control_question or catalog.control_question,
            validation_cadence=scoped.validation_cadence or catalog.validation_cadence,
            control_weighting=scoped.control_weighting or catalog.control_weighting,
            nist_csf_function=scoped.nist_csf_function or catalog.nist_csf_function,
            pptdf_people=bool(scoped.pptdf_people),
            pptdf_process=bool(scoped.pptdf_process),
            pptdf_technology=bool(scoped.pptdf_technology),
            pptdf_data=bool(scoped.pptdf_data),
            pptdf_facility=bool(scoped.pptdf_facility),
            assessment_objectives=objectives_by_control.get(scoped.scf_id, []),
            evidence_mappings=evidence_by_control.get(scoped.scf_id, []),
        ))

    # --- group by domain ---------------------------------------------------
    grouped: Dict[str, List[EnrichedControl]] = {}
    for c in controls:
        grouped.setdefault(c.domain_identifier, []).append(c)

    domains: List[DomainWithControls] = []
    for identifier in sorted(
        grouped, key=lambda i: domain_meta[i].order if i in domain_meta else 999
    ):
        meta = domain_meta.get(identifier)
        bucket = grouped[identifier]
        domains.append(DomainWithControls(
            domain=Domain(
                identifier=identifier,
                name=meta.name if meta else identifier,
                principle=meta.principle if meta else None,
                principle_intent=meta.principle_intent if meta else None,
                order=meta.order if meta else 999,
            ),
            controls=bucket,
            maturity_breakdown=_breakdown(c.maturity_level for c in bucket),
            status_breakdown=_breakdown(c.implementation_status for c in bucket),
        ))

    # --- supporting registers ----------------------------------------------
    evidence_items: List[Dict[str, Any]] = []
    if include_evidence:
        for et in session.query(EvidenceTracking).filter(
            EvidenceTracking.organization_id == organization_id
        ).all():
            evidence_items.append({
                "evidence_id": et.evidence_id,
                "is_tracked": bool(et.is_tracked),
                "method_of_collection": et.method_of_collection,
                "collecting_system": et.collecting_system,
                "owner": et.owner,
                "frequency": et.frequency,
                "comments": et.comments,
                "maturity_level": getattr(et, "maturity_level", None),
            })

    risk_assessments: List[Dict[str, Any]] = []
    risk_profile: Dict[str, Any] = {}
    if include_risks:
        profile = (
            session.query(OrganizationRiskProfile)
            .filter(OrganizationRiskProfile.organization_id == organization_id)
            .one_or_none()
        )
        # Risk levels are DERIVED, not stored: RiskAssessment holds
        # likelihood/impact and converts the score to a band using the
        # organisation's own thresholds. Reading a ``*_risk_level`` attribute
        # off it raises AttributeError and kills the whole generation.
        bands = {}
        if profile:
            bands = {
                "low_max": profile.low_max,
                "medium_max": profile.medium_max,
                "high_max": profile.high_max,
            }
        for ra in session.query(RiskAssessment).filter(
            RiskAssessment.organization_id == organization_id
        ).all():
            risk_assessments.append({
                "risk_code": ra.risk_code,
                "likelihood": ra.likelihood,
                "impact": ra.impact,
                "inherent_risk_score": ra.inherent_risk_score,
                "residual_risk_score": ra.residual_risk_score,
                "inherent_risk_level": ra.get_inherent_risk_level(**bands),
                "residual_risk_level": ra.get_residual_risk_level(**bands),
                "treatment_status": ra.treatment_status,
                "treatment_plan": ra.treatment_plan,
                "treatment_due_date": (
                    ra.treatment_due_date.isoformat() if ra.treatment_due_date else None
                ),
                # ``ra.owner`` is the User relationship, not a name. Rendering
                # it straight into a table cell prints a repr; put the human
                # identifier in instead.
                "owner": (
                    (ra.owner.display_name or ra.owner.email) if ra.owner else None
                ),
                "notes": ra.notes,
            })
        if profile:
            risk_profile = {
                "low_max": profile.low_max,
                "medium_max": profile.medium_max,
                "high_max": profile.high_max,
                "acceptable_risk_level": profile.acceptable_risk_level,
                "auto_escalate_above": profile.auto_escalate_above,
            }

    systems: List[Dict[str, Any]] = []
    vendors: List[Dict[str, Any]] = []
    if include_systems:
        for s in session.query(System).filter(
            System.organization_id == organization_id
        ).order_by(System.name).all():
            systems.append({
                "name": s.name,
                "system_type": s.system_type,
                "category": getattr(s, "category", None),
                "description": s.description,
                "status": s.status,
            })
        for v in session.query(Vendor).filter(
            Vendor.organization_id == organization_id
        ).order_by(Vendor.name).all():
            vendors.append({
                "name": v.name,
                "description": v.description,
                "website": v.website,
                "category": getattr(v, "category", None),
                "status": v.status,
                "criticality": getattr(v, "criticality", None),
            })

    ctx = OrganisationContext(
        organization_id=str(organization_id),
        name=org.name,
        generated_at=datetime.now(timezone.utc).isoformat(),
        catalog_version=catalog_version,
        domains=domains,
        all_controls=controls,
        evidence_items=evidence_items,
        risk_assessments=risk_assessments,
        systems=systems,
        vendors=vendors,
        risk_profile=risk_profile,
        maturity_distribution=_breakdown(c.maturity_level for c in controls),
        status_distribution=_breakdown(c.implementation_status for c in controls),
        total_scoped_controls=len(controls),
        total_domains=len(domains),
    )

    logger.info(
        "doc_gen context assembled: org=%s controls=%d domains=%d catalog=%s",
        organization_id, len(controls), len(domains), catalog_version,
    )
    return ctx
