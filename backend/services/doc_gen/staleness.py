"""
Is this document still describing the organisation it was generated from?

The pipeline already answers that question for itself: it recomputes an input
fingerprint on every run and skips the generation when nothing has moved
(:mod:`services.doc_gen.pipeline`, step 4). The answer was never surfaced, so a
Statement of Applicability generated before a scoping exercise looked exactly
like one generated after it, and the only way to find out was to regenerate and
read the manifest.

**What is compared, and why it is not the whole composite.** A stored
fingerprint has four components (:mod:`services.doc_gen.fingerprint`). Two of
them -- ``template_hash`` and ``prompt_hash`` -- describe how the generator
asked, not what the organisation holds, and for Tier 2 the user prompt embeds
the document's *own previous content* so the Change History survives
regeneration. Recomputing a composite outside a generation run would therefore
mark every edited Tier 2 document permanently stale: a false positive on the
documents people work on most, which is the fastest way to teach users to
ignore a badge. Staleness is measured on the two components that are genuinely
a function of the organisation's inputs -- ``controls_hash`` and
``catalog_version`` -- using the very same canonical serialisation the pipeline
stored, so the comparison is byte-exact rather than approximate.

**Cost.** Four queries per request regardless of how many documents are being
listed: the catalog state, the active domains, the scoped controls joined to
their catalog definitions, and their assessment objectives. The per-document
work is then a dict comprehension and a SHA-256 over one domain's controls.
That is what makes this affordable on the list route; building a full
:class:`~services.doc_gen.context.OrganisationContext` per document (its own
seven-plus queries, against a synchronous session the async API does not have)
would not be.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .fingerprint import compute_controls_hash

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Staleness:
    """One document's answer, ready to put on the API schema."""

    is_stale: bool
    reason: Optional[str] = None


NOT_STALE = Staleness(is_stale=False, reason=None)


@dataclass
class CurrentInputs:
    """The organisation's generation inputs as they stand right now.

    Read once per request and then interrogated per document. ``by_domain``
    keys on the catalog domain *identifier* ("GOV"), which is what
    ``generated_documents.domain_id`` stores; the empty-string key that
    non-domain-scoped generators use resolves to the whole estate.
    """

    catalog_version: Optional[str]
    all_controls: List[Dict[str, Any]]
    by_domain: Dict[str, List[Dict[str, Any]]]

    #: Memoised ``compute_controls_hash`` per domain key. Every document in a
    #: domain hashes the same control list, and the whole estate -- 346
    #: controls here -- takes ~4ms a pass, so a page of documents paid that
    #: repeatedly for an answer that cannot differ within one request. The
    #: inputs are read once and never mutated afterwards, which is what makes
    #: caching against them safe.
    _hashes: Dict[str, str] = field(default_factory=dict, repr=False)

    def controls_for(self, domain_id: str) -> List[Dict[str, Any]]:
        if not domain_id:
            return self.all_controls
        return self.by_domain.get(domain_id, [])

    def hash_for(self, domain_id: str) -> str:
        """``compute_controls_hash`` for a domain's controls, computed once."""
        key = domain_id or ""
        cached = self._hashes.get(key)
        if cached is None:
            cached = compute_controls_hash(self.controls_for(key))
            self._hashes[key] = cached
        return cached


async def load_current_inputs(
    db: AsyncSession, organization_id: UUID
) -> CurrentInputs:
    """Read every scoped control's fingerprint projection, in four queries.

    The projection is deliberately identical to
    :meth:`services.doc_gen.context.EnrichedControl.to_fingerprint_dict` --
    same fields, same fallbacks -- because a hash computed from a different
    projection would differ from the stored one for reasons that have nothing
    to do with staleness, and every document would read as stale forever.
    """
    from catalog_models import (
        SCFCatalogAssessmentObjective,
        SCFCatalogControl,
        SCFCatalogDomain,
    )
    from models import OrganizationCatalogState, ScopedControl

    catalog_state = (await db.execute(
        select(OrganizationCatalogState).where(
            OrganizationCatalogState.organization_id == organization_id
        )
    )).scalar_one_or_none()
    catalog_version = (
        catalog_state.reconciled_catalog_version if catalog_state else None
    )

    domain_rows = (await db.execute(
        select(SCFCatalogDomain).where(SCFCatalogDomain.status == "active")
    )).scalars().all()
    code_by_name = {d.name: d.identifier for d in domain_rows if d.name}

    rows = (await db.execute(
        select(ScopedControl, SCFCatalogControl)
        .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .where(
            ScopedControl.organization_id == organization_id,
            ScopedControl.selected.is_(True),
        )
        .order_by(ScopedControl.scf_id)
    )).all()

    scf_ids = [scoped.scf_id for scoped, _ in rows]
    objectives: Dict[str, List[Dict[str, str]]] = {}
    if scf_ids:
        ao_rows = (await db.execute(
            select(SCFCatalogAssessmentObjective).where(
                SCFCatalogAssessmentObjective.scf_id.in_(scf_ids),
                SCFCatalogAssessmentObjective.status == "active",
            )
        )).scalars().all()
        for ao in ao_rows:
            objectives.setdefault(ao.scf_id, []).append(
                {"ao_id": ao.ao_id, "objective_text": ao.objective_text}
            )

    all_controls: List[Dict[str, Any]] = []
    by_domain: Dict[str, List[Dict[str, Any]]] = {}
    for scoped, catalog in rows:
        projection = {
            "scf_id": scoped.scf_id,
            "control_name": catalog.control_name or "",
            "control_description": catalog.control_description or "",
            "implementation_status": scoped.implementation_status or "not_started",
            "maturity_level": scoped.maturity_level,
            "owner": scoped.owner,
            "implementation_notes": scoped.implementation_notes,
            "assessment_objectives": objectives.get(scoped.scf_id, []),
        }
        all_controls.append(projection)
        identifier = code_by_name.get(
            catalog.scf_domain or "", catalog.scf_domain or ""
        )
        by_domain.setdefault(identifier, []).append(projection)

    return CurrentInputs(
        catalog_version=catalog_version,
        all_controls=all_controls,
        by_domain=by_domain,
    )


def assess(
    stored_components: Optional[Dict[str, Any]],
    domain_id: str,
    inputs: CurrentInputs,
) -> Staleness:
    """Compare one document's stored inputs against the current ones.

    ``stored_components`` is ``generated_documents.input_components``, which
    the pipeline writes alongside ``input_fingerprint`` on every run -- so it
    already describes the document's latest version and needs no extra query.

    A document with no stored ``controls_hash`` reports *not* stale. That is
    the honest answer: it predates fingerprint tracking, so nothing is known
    about whether its inputs have moved, and asserting staleness on no evidence
    would put a badge on documents that may well be current.
    """
    stored = stored_components or {}
    previous_hash = stored.get("controls_hash")
    if not previous_hash:
        return NOT_STALE

    controls = inputs.controls_for(domain_id or "")
    current_hash = inputs.hash_for(domain_id or "")
    catalog_moved = stored.get("catalog_version") != inputs.catalog_version

    if current_hash == previous_hash and not catalog_moved:
        return NOT_STALE

    return Staleness(
        is_stale=True,
        reason=_reason(stored, controls, current_hash, inputs.catalog_version),
    )


def _reason(
    stored: Dict[str, Any],
    controls: List[Dict[str, Any]],
    current_hash: str,
    catalog_version: Optional[str],
) -> str:
    """A sentence a person can act on, not a hash diff.

    Named counts come first because "3 controls added, 1 removed" tells the
    reader whether regenerating is urgent; a catalog move alone usually is not.
    """
    before = set(stored.get("control_ids") or [])
    after = {c["scf_id"] for c in controls}
    added, removed = after - before, before - after

    parts: List[str] = []
    if added or removed:
        bits = []
        if added:
            bits.append(f"{len(added)} added")
        if removed:
            bits.append(f"{len(removed)} removed")
        parts.append(f"scope has changed ({', '.join(bits)})")
    elif current_hash != stored.get("controls_hash"):
        parts.append("control details have changed")

    if stored.get("catalog_version") != catalog_version:
        parts.append(
            f"SCF catalog moved from {stored.get('catalog_version') or 'an untracked version'} "
            f"to {catalog_version or 'an untracked version'}"
        )

    if not parts:  # pragma: no cover -- assess() only calls this when something moved
        parts.append("inputs have changed")

    sentence = "; ".join(parts)
    return sentence[0].upper() + sentence[1:] + " since this was generated"


async def assess_documents(
    db: AsyncSession,
    organization_id: UUID,
    documents: Iterable[Any],
) -> Dict[UUID, Staleness]:
    """Staleness for a whole page of documents, on one read of the inputs.

    Failure here is not allowed to take the document list with it. Staleness is
    an advisory badge; a catalog table that is mid-upgrade, or an organisation
    with no catalog state at all, must not turn "list my documents" into a 500.
    """
    documents = list(documents)
    if not documents:
        return {}
    try:
        inputs = await load_current_inputs(db, organization_id)
    except Exception:  # pragma: no cover -- defensive, see docstring
        logger.exception(
            "doc_gen staleness: could not read current inputs for org=%s",
            organization_id,
        )
        return {d.id: NOT_STALE for d in documents}

    return {
        d.id: assess(d.input_components, d.domain_id or "", inputs)
        for d in documents
    }
