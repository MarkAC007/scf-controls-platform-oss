"""
Document Map API — per-domain CDM coverage aggregate.

Answers one question for a GRC manager: *for each SCF domain, what do we have,
and how much of it has a human actually confirmed?* The distinction between
"a model thinks this document is about a domain" and "someone accepted a
mapping into it" is the whole point of the view, because measured
over-classification means silent trust is the failure mode that costs most.

Two design constraints are load-bearing:

* **Bookkeeping never leaves the database.** ``provider``, ``model_id``,
  ``prompt_version``, ``classification_id`` and ``rationale`` exist on
  ``cdm_document_intents`` for operators. The response models below simply do
  not declare them, so no future ``.model_dump()`` can leak them into the
  webclient. That is enforcement at the schema boundary rather than a rule
  someone has to remember.

* **Domain state is computed server-side.** The moment the client owns the
  model-versus-confirmed distinction, a second implementation appears with the
  first export or report, and the two will disagree. Sort order, colour scales
  and expand/collapse stay client-side; they carry no correctness weight.

Domains are derived from ``split_part(scoped_controls.scf_id, '-', 1)``.
``scf_catalog_controls.scf_domain`` is never joined on — it is known-broken.
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_org_role, OrgMembership
from catalog_models import SCFCatalogDomain
from database import get_db
from services import cdm_intent

logger = logging.getLogger(__name__)

router = APIRouter(tags=["cdm_document_map"])

# Mapping statuses reported per document. Fixed rather than derived from the
# data so a domain with no mappings still renders four zeros instead of an
# empty object the client has to defend against.
_MAPPING_STATUSES = ("proposed", "accepted", "dismissed", "stale")

STATE_COVERED = "covered"
STATE_CLAIMED = "claimed"
STATE_GAP = "gap"
STATE_OUT_OF_SCOPE = "out_of_scope"


class MappingCounts(BaseModel):
    proposed: int = 0
    accepted: int = 0
    dismissed: int = 0
    stale: int = 0


class DomainDocument(BaseModel):
    cdm_document_id: UUID
    filename: str
    # "confirmed" when a human accepted at least one mapping from this document
    # into this domain, otherwise "model". Carried alongside claimed_by_model so
    # confirmed-but-not-claimed (the model missed a domain a human confirmed)
    # stays visible rather than collapsed into one flag.
    intent_source: str
    claimed_by_model: bool
    rank: Optional[int] = None
    mapping_counts: MappingCounts


class ScopedControlCounts(BaseModel):
    total: int = 0
    selected: int = 0


class DomainTotals(BaseModel):
    documents: int = 0
    confirmed_documents: int = 0
    controls_with_accepted_mapping: int = 0
    controls_with_proposed_mapping: int = 0


class DomainEntry(BaseModel):
    domain: str
    name: str
    display_order: int
    scoped_control_counts: ScopedControlCounts
    state: str
    totals: DomainTotals
    documents: list[DomainDocument]


class OrphanDocument(BaseModel):
    cdm_document_id: UUID
    filename: str
    ingest_status: str
    intent_state: str
    mapping_counts: MappingCounts


class CoverageSummary(BaseModel):
    total_domains: int
    covered: int
    claimed: int
    gap: int
    documents_total: int
    documents_orphaned: int
    documents_awaiting_classification: int


class DocumentMapResponse(BaseModel):
    generated_at: datetime
    coverage_summary: CoverageSummary
    domains: list[DomainEntry]
    orphan_documents: list[OrphanDocument]


def derive_domain_state(
    *,
    selected_controls: int,
    accepted_mappings: int,
    proposed_mappings: int,
    model_intents: int,
) -> str:
    """The four-value domain state, computed once and used by API and UI alike.

    ``out_of_scope`` is tested first because deselecting a domain's controls is
    a decision, and a decision outranks any leftover edge pointing at controls
    that are no longer in scope.
    """
    if selected_controls == 0:
        return STATE_OUT_OF_SCOPE
    if accepted_mappings > 0:
        return STATE_COVERED
    if model_intents > 0 or proposed_mappings > 0:
        return STATE_CLAIMED
    return STATE_GAP


@router.get(
    "/organizations/{org_id}/cdm/document-map",
    response_model=DocumentMapResponse,
)
async def get_document_map(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Per-domain CDM coverage for the organization.

    Grouped queries stitched in Python across 33 domains — never a query per
    domain or per document.
    """
    # ── 1. Domain skeleton ──────────────────────────────────────────────
    # Every catalogue domain appears whether or not anything references it: a
    # domain missing from the payload and a domain with nothing in it are very
    # different statements, and only one of them is true here.
    domains_result = await db.execute(
        select(SCFCatalogDomain).order_by(SCFCatalogDomain.order, SCFCatalogDomain.identifier)
    )
    catalog_domains = domains_result.scalars().all()

    # ── 2. Scoped control counts per domain ─────────────────────────────
    control_counts_result = await db.execute(
        text(
            "SELECT split_part(scf_id, '-', 1) AS domain, "
            "       count(*) AS total, "
            "       count(*) FILTER (WHERE selected) AS selected "
            "FROM scoped_controls "
            "WHERE organization_id = :org_id "
            "GROUP BY 1"
        ),
        {"org_id": str(org_id)},
    )
    control_counts = {
        row.domain: ScopedControlCounts(total=row.total, selected=row.selected)
        for row in control_counts_result
    }

    # ── 3. Documents, with their intent lifecycle ───────────────────────
    documents_result = await db.execute(
        text(
            "SELECT id, original_filename, ingest_status, intent_status "
            "FROM cdm_documents WHERE organization_id = :org_id "
            "ORDER BY original_filename"
        ),
        {"org_id": str(org_id)},
    )
    documents = {
        row.id: {
            "filename": row.original_filename,
            "ingest_status": row.ingest_status,
            "intent_status": row.intent_status,
        }
        for row in documents_result
    }

    # ── 4. Model-claimed edges ──────────────────────────────────────────
    intents_result = await db.execute(
        text(
            "SELECT cdm_document_id, domain, rank FROM cdm_document_intents "
            "WHERE organization_id = :org_id"
        ),
        {"org_id": str(org_id)},
    )
    intent_rank: dict[tuple[str, UUID], int] = {}
    for row in intents_result:
        intent_rank[(row.domain, row.cdm_document_id)] = row.rank

    # ── 5. Mapping counts per (domain, document, status) ────────────────
    mapping_result = await db.execute(
        text(
            "SELECT split_part(sc.scf_id, '-', 1) AS domain, "
            "       m.cdm_document_id AS cdm_document_id, "
            "       m.status AS status, "
            "       count(*) AS mapping_count "
            "FROM cdm_mappings m "
            "JOIN scoped_controls sc ON sc.id = m.scoped_control_id "
            "WHERE m.organization_id = :org_id "
            "GROUP BY 1, 2, 3"
        ),
        {"org_id": str(org_id)},
    )
    doc_counts: dict[tuple[str, UUID], dict[str, int]] = {}
    for row in mapping_result:
        if row.status not in _MAPPING_STATUSES:
            continue
        bucket = doc_counts.setdefault((row.domain, row.cdm_document_id), {})
        bucket[row.status] = bucket.get(row.status, 0) + row.mapping_count

    # ── 6. Distinct controls touched per (domain, status) ───────────────
    # Separate from step 5 because a distinct count over controls cannot be
    # recovered by summing per-document counts — the same control is reachable
    # from several documents.
    control_hits_result = await db.execute(
        text(
            "SELECT split_part(sc.scf_id, '-', 1) AS domain, "
            "       m.status AS status, "
            "       count(DISTINCT m.scoped_control_id) AS control_count "
            "FROM cdm_mappings m "
            "JOIN scoped_controls sc ON sc.id = m.scoped_control_id "
            "WHERE m.organization_id = :org_id "
            "GROUP BY 1, 2"
        ),
        {"org_id": str(org_id)},
    )
    controls_by_domain_status = {
        (row.domain, row.status): row.control_count for row in control_hits_result
    }

    # ── Stitch ──────────────────────────────────────────────────────────
    documents_with_any_edge: set[UUID] = set()
    domain_entries: list[DomainEntry] = []
    covered = claimed = gap = 0

    for catalog_domain in catalog_domains:
        code = catalog_domain.identifier
        counts = control_counts.get(code, ScopedControlCounts())

        document_ids = {doc_id for (d, doc_id) in doc_counts if d == code}
        document_ids |= {doc_id for (d, doc_id) in intent_rank if d == code}

        entries: list[DomainDocument] = []
        confirmed_documents = 0
        accepted_total = 0
        proposed_total = 0
        model_intents = 0
        for document_id in document_ids:
            document = documents.get(document_id)
            if document is None:
                # The document was deleted between queries; reporting an edge to
                # something we cannot name would be worse than omitting it.
                continue
            bucket = doc_counts.get((code, document_id), {})
            mapping_counts = MappingCounts(
                **{status: bucket.get(status, 0) for status in _MAPPING_STATUSES}
            )
            accepted_total += mapping_counts.accepted
            proposed_total += mapping_counts.proposed
            confirmed = mapping_counts.accepted > 0
            if confirmed:
                confirmed_documents += 1
            rank = intent_rank.get((code, document_id))
            if rank is not None:
                model_intents += 1
            if confirmed or mapping_counts.proposed > 0 or rank is not None:
                documents_with_any_edge.add(document_id)
            entries.append(
                DomainDocument(
                    cdm_document_id=document_id,
                    filename=document["filename"],
                    intent_source="confirmed" if confirmed else "model",
                    claimed_by_model=rank is not None,
                    rank=rank,
                    mapping_counts=mapping_counts,
                )
            )

        # Ranked claims first, then alphabetical — a stable order the client can
        # re-sort, not a ranking the client has to reconstruct.
        entries.sort(key=lambda item: (item.rank if item.rank is not None else 99, item.filename))

        # Counted from surviving documents only, for the same reason the loop
        # skips them: a document deleted mid-request would otherwise derive
        # 'claimed' for a domain that renders with zero documents under it.
        state = derive_domain_state(
            selected_controls=counts.selected,
            accepted_mappings=accepted_total,
            proposed_mappings=proposed_total,
            model_intents=model_intents,
        )
        if state == STATE_COVERED:
            covered += 1
        elif state == STATE_CLAIMED:
            claimed += 1
        elif state == STATE_GAP:
            gap += 1

        domain_entries.append(
            DomainEntry(
                domain=code,
                name=catalog_domain.name,
                display_order=catalog_domain.order,
                scoped_control_counts=counts,
                state=state,
                totals=DomainTotals(
                    documents=len(entries),
                    confirmed_documents=confirmed_documents,
                    controls_with_accepted_mapping=controls_by_domain_status.get(
                        (code, "accepted"), 0
                    ),
                    controls_with_proposed_mapping=controls_by_domain_status.get(
                        (code, "proposed"), 0
                    ),
                ),
                documents=entries,
            )
        )

    # A document is orphaned when it has no model claim and no live mapping
    # anywhere. Dismissed-only counts as orphaned: a dismissal is a statement
    # that the document does *not* belong there.
    orphan_documents = [
        OrphanDocument(
            cdm_document_id=document_id,
            filename=document["filename"],
            ingest_status=document["ingest_status"],
            intent_state=document["intent_status"],
            mapping_counts=MappingCounts(
                **{
                    status: sum(
                        bucket.get(status, 0)
                        for (_, doc_id), bucket in doc_counts.items()
                        if doc_id == document_id
                    )
                    for status in _MAPPING_STATUSES
                }
            ),
        )
        for document_id, document in documents.items()
        if document_id not in documents_with_any_edge
    ]

    summary = CoverageSummary(
        total_domains=len(catalog_domains),
        # Confirmed-only. "claimed" is reported as its own number precisely so
        # the two are never added together into a coverage figure nobody earned.
        covered=covered,
        claimed=claimed,
        gap=gap,
        documents_total=len(documents),
        documents_orphaned=len(orphan_documents),
        # Absence renders as absent, never as in-flight. With no provider
        # configured nothing is ever queued, so every document sits at
        # 'pending' forever — reporting those as "awaiting classification"
        # would tell every org its whole corpus was mid-flight in a stage that
        # is switched off. The documents still appear as orphans carrying
        # their neutral intent_state.
        documents_awaiting_classification=(
            sum(1 for document in documents.values() if document["intent_status"] == "pending")
            if cdm_intent.intent_classification_enabled()
            else 0
        ),
    )

    return DocumentMapResponse(
        generated_at=datetime.now(timezone.utc),
        coverage_summary=summary,
        domains=domain_entries,
        orphan_documents=orphan_documents,
    )
