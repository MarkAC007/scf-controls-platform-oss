"""Control-level consolidation of CDM citation mappings (#722).

The retrieval pass keeps up to ``CDM_MAX_PROPOSALS_PER_CONTROL`` citations per
(control, document) pair, one ``cdm_mappings`` row each. This module folds
each pair into a single :class:`models.CDMControlProposal` — the unit the
reviewer decides on — in two phases:

* :func:`consolidate_proposals` — heuristic, no network. Groups ALL mappings
  for the org (every status: grouping over review state would make the
  fingerprint churn on partial reviews), upserts one proposal per pair, links
  the citations, derives the proposal's initial status from its children.
  Runs inline in the compute task, so the queue is correct immediately.

* :func:`recompute_proposals_llm` — the "context is king" pass. One provider
  call per proposed group, prompt carrying the control plus every citation at
  once, producing a consolidated relevance + rationale. Wall-clock budgeted:
  the Celery caller re-enqueues itself while unrecomputed groups remain, so a
  slow provider degrades to later completion, never to a SIGKILLed task.

Idempotency contract: ``citations_fingerprint`` = sha256 over the document's
``extracted_text_sha256`` plus the sorted citation offsets. Identical
fingerprint ⇒ re-run is a no-op and a dismissal stays sticky. Changed
fingerprint ⇒ the evidence the reviewer saw no longer exists: heuristic
values are refreshed, ``recompute_provider`` clears (marking the group for
re-upgrade), and a dismissed proposal resurrects to 'proposed' with an audit
row — the old ``dismiss_reason`` is kept so the UI can say "previously
dismissed".
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from catalog_models import SCFCatalogControl
from models import AuditLog, CDMControlProposal, CDMDocument, CDMMapping, ScopedControl
from services import cdm_intent

logger = logging.getLogger(__name__)

CONSOLIDATION_PROMPT_VERSION = "consol-v1"

# Excerpts are truncated per citation before prompting; the point of the pass
# is joint judgment over the group, not exhaustive re-reading.
_PROMPT_EXCERPT_MAX_CHARS = 600

_DEFAULT_TIMEOUT_S = 45.0
_DEFAULT_BUDGET_S = 420.0


@dataclass
class ConsolidationSummary:
    """Return shape of :func:`consolidate_proposals` (heuristic phase)."""

    groups_seen: int = 0
    proposals_created: int = 0
    proposals_updated: int = 0
    proposals_unchanged: int = 0
    proposals_resurrected: int = 0
    citations_linked: int = 0


@dataclass
class RecomputeSummary:
    """Return shape of :func:`recompute_proposals_llm` (LLM phase)."""

    proposals_recomputed: int = 0
    recompute_failures: int = 0
    proposals_remaining: int = 0
    budget_exhausted: bool = False


def get_consolidation_timeout_seconds() -> float:
    """Per-call provider timeout. Deliberately NOT ``CDM_INTENT_TIMEOUT_S``
    (default 300): at one call per group, intent-sized timeouts would blow
    through any task budget on the first slow group."""
    raw = os.getenv("CDM_CONSOLIDATION_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_TIMEOUT_S
    try:
        value = float(raw)
        return value if value > 0 else _DEFAULT_TIMEOUT_S
    except ValueError:
        logger.warning("Invalid CDM_CONSOLIDATION_TIMEOUT_S %r; using default", raw)
        return _DEFAULT_TIMEOUT_S


def get_consolidation_budget_seconds() -> float:
    """Wall-clock budget for one recompute task run — must stay under the
    Celery soft time limit (540s) with headroom for the in-flight call."""
    raw = os.getenv("CDM_CONSOLIDATION_BUDGET_S", "").strip()
    if not raw:
        return _DEFAULT_BUDGET_S
    try:
        value = float(raw)
        return value if value > 0 else _DEFAULT_BUDGET_S
    except ValueError:
        logger.warning("Invalid CDM_CONSOLIDATION_BUDGET_S %r; using default", raw)
        return _DEFAULT_BUDGET_S


def derive_proposal_status(child_statuses) -> str:
    """Proposal status from citation statuses.

    Any accepted child means the pair is evidenced — the reviewer said so.
    Otherwise a stale child (an acceptance invalidated by re-ingest) marks the
    pair for re-review. All-dismissed means the pair was rejected in full.
    Anything else is an open decision.
    """
    statuses = set(child_statuses)
    if "accepted" in statuses:
        return "accepted"
    if "stale" in statuses:
        return "stale"
    if statuses and statuses == {"dismissed"}:
        return "dismissed"
    return "proposed"


def citations_fingerprint(document_sha256: Optional[str], offsets) -> str:
    """sha256 over the extracted-text sha + sorted (start, end) offsets.

    The document sha is included because re-extraction can rewrite text at
    identical offsets (the compute dedupe path updates ``excerpt`` in place
    without moving offsets) — offsets alone would report "unchanged" for
    citations whose text no longer exists.
    """
    hasher = hashlib.sha256()
    hasher.update((document_sha256 or "").encode("utf-8"))
    for start, end in sorted(offsets):
        hasher.update(f":{start}-{end}".encode("utf-8"))
    return hasher.hexdigest()


def rederive_proposal_status(
    session: Session,
    proposal_id: UUID,
    *,
    actor_user_id: Optional[UUID] = None,
) -> Optional[str]:
    """Re-derive one proposal's status from its children; update if changed.

    Called after citation-level accept/dismiss so the parent card never
    contradicts its rows. Accepted/dismissed bookkeeping columns are left to
    the endpoint that acts on the proposal directly — this helper only keeps
    ``status`` coherent. Returns the new status when a change was written.
    """
    del actor_user_id  # bookkeeping stays with the direct endpoints
    row = session.execute(
        select(CDMControlProposal.status).where(CDMControlProposal.id == proposal_id)
    ).first()
    if row is None:
        return None
    current_status = row[0]

    child_statuses = [
        r[0]
        for r in session.execute(
            select(CDMMapping.status).where(
                CDMMapping.control_proposal_id == proposal_id
            )
        ).all()
    ]
    derived = derive_proposal_status(child_statuses)
    if derived == current_status:
        return None

    session.execute(
        update(CDMControlProposal)
        .where(CDMControlProposal.id == proposal_id)
        .values(status=derived, updated_at=datetime.now(timezone.utc))
    )
    return derived


def consolidate_proposals(
    session: Session,
    org_id: UUID,
    *,
    kb_revision: Optional[str] = None,
) -> ConsolidationSummary:
    """Heuristic consolidation pass — group, fingerprint, upsert, link.

    Commits per group: one late failure must not roll back hundreds of
    already-consolidated groups, and short transactions keep row locks from
    spanning the whole pass.

    Groups are built from surviving mappings only, so a proposal whose
    citations were ALL deleted is not garbage-collected here. That state is
    currently unreachable — mappings are only deleted with their document
    (proposal cascades too) or via the backfill purge (which deletes the
    proposed-status proposals in the same transaction). Any new mapping-delete
    path must keep that invariant or add GC to this pass.
    """
    from services.cdm_mapping import get_kb_revision as _get_kb_revision

    revision = kb_revision if kb_revision is not None else _get_kb_revision()
    summary = ConsolidationSummary()

    rows = session.execute(
        select(
            CDMMapping.id,
            CDMMapping.scoped_control_id,
            CDMMapping.cdm_document_id,
            CDMMapping.status,
            CDMMapping.relevance_score,
            CDMMapping.byte_offset_start,
            CDMMapping.byte_offset_end,
            CDMMapping.control_proposal_id,
            CDMDocument.extracted_text_sha256,
        )
        .join(CDMDocument, CDMMapping.cdm_document_id == CDMDocument.id)
        .where(CDMMapping.organization_id == org_id)
    ).all()

    groups: dict[tuple[UUID, UUID], list] = {}
    for row in rows:
        groups.setdefault((row[1], row[2]), []).append(row)

    for (control_id, document_id), members in groups.items():
        summary.groups_seen += 1
        doc_sha = members[0][8]
        fingerprint = citations_fingerprint(
            doc_sha, [(m[5], m[6]) for m in members]
        )
        heuristic_score = max(m[4] for m in members)
        member_ids = [m[0] for m in members]
        derived_status = derive_proposal_status(m[3] for m in members)

        try:
            proposal = session.execute(
                select(CDMControlProposal).where(
                    CDMControlProposal.organization_id == org_id,
                    CDMControlProposal.scoped_control_id == control_id,
                    CDMControlProposal.cdm_document_id == document_id,
                )
            ).scalar_one_or_none()

            if proposal is None:
                proposal = CDMControlProposal(
                    organization_id=org_id,
                    scoped_control_id=control_id,
                    cdm_document_id=document_id,
                    status=derived_status,
                    consolidated_score=heuristic_score,
                    citation_count=len(members),
                    citations_fingerprint=fingerprint,
                    kb_revision=revision,
                )
                session.add(proposal)
                try:
                    session.flush()
                except IntegrityError:
                    # Concurrent pass won the insert race — adopt its row.
                    session.rollback()
                    proposal = session.execute(
                        select(CDMControlProposal).where(
                            CDMControlProposal.organization_id == org_id,
                            CDMControlProposal.scoped_control_id == control_id,
                            CDMControlProposal.cdm_document_id == document_id,
                        )
                    ).scalar_one()
                else:
                    summary.proposals_created += 1

            elif proposal.citations_fingerprint == fingerprint:
                summary.proposals_unchanged += 1

            else:
                # Evidence changed since this proposal was computed. Refresh
                # the heuristic values and clear the recompute stamp so the
                # LLM pass revisits the group; a previous rationale is kept
                # (stale but related) rather than blanked.
                now = datetime.now(timezone.utc)
                values: dict = {
                    "citations_fingerprint": fingerprint,
                    "citation_count": len(members),
                    "consolidated_score": heuristic_score,
                    "recompute_provider": None,
                    "recompute_model_id": None,
                    "kb_revision": revision,
                    "updated_at": now,
                }
                if proposal.status == "dismissed":
                    # New evidence the dismissal never saw: back to the queue,
                    # with an audit trail and the old reason preserved.
                    values.update(
                        status="proposed",
                        dismissed_at=None,
                        dismissed_by_user_id=None,
                    )
                    session.add(
                        AuditLog(
                            organization_id=org_id,
                            entity_type="cdm_control_proposal",
                            entity_id=proposal.id,
                            action="resurrected",
                            field_name="status",
                            old_value="dismissed",
                            new_value=json.dumps(
                                {
                                    "status": "proposed",
                                    "old_fingerprint": proposal.citations_fingerprint,
                                    "new_fingerprint": fingerprint,
                                    "resurrected_at": now.isoformat(),
                                }
                            ),
                            changed_by_user_id=_system_actor(),
                            action_source="system",
                        )
                    )
                    summary.proposals_resurrected += 1
                session.execute(
                    update(CDMControlProposal)
                    .where(CDMControlProposal.id == proposal.id)
                    .values(**values)
                )
                summary.proposals_updated += 1

            linked = session.execute(
                update(CDMMapping)
                .where(
                    CDMMapping.id.in_(member_ids),
                    (CDMMapping.control_proposal_id.is_(None))
                    | (CDMMapping.control_proposal_id != proposal.id),
                )
                .values(control_proposal_id=proposal.id)
            )
            summary.citations_linked += linked.rowcount or 0

            session.commit()
        except Exception:
            logger.exception(
                "Consolidation failed for control %s document %s (org %s)",
                control_id,
                document_id,
                org_id,
            )
            session.rollback()

    return summary


def _system_actor() -> UUID:
    from services.cdm_mapping import _get_system_actor_user_id

    return _get_system_actor_user_id()


def _build_recompute_prompt(
    scf_id: Optional[str],
    control_name: Optional[str],
    control_question: Optional[str],
    document_filename: str,
    citations: list[tuple[Optional[str], float, Optional[str]]],
) -> str:
    """Prompt for one (control, document) group.

    One call per group by design: batching several groups into one prompt
    would couple unrelated verdicts to a single parse failure.
    """
    lines = [
        "You are assessing whether a policy document, as a whole, evidences a "
        "specific compliance control.",
        "",
        f"Control {scf_id or 'unknown'}: {control_name or 'unknown'}",
    ]
    if control_question:
        lines.append(f"Control question: {control_question}")
    lines += [
        "",
        f"Document: {document_filename}",
        f"The retrieval pass found {len(citations)} citation(s) in this document:",
        "",
    ]
    for index, (section, score, excerpt) in enumerate(citations, start=1):
        body = (excerpt or "").strip()
        if len(body) > _PROMPT_EXCERPT_MAX_CHARS:
            body = body[:_PROMPT_EXCERPT_MAX_CHARS] + "…"
        lines.append(
            f'{index}. [{section or "no section"}] (retrieval score {score:.3f}): "{body}"'
        )
    lines += [
        "",
        "Considering ALL citations together as one body of evidence, respond "
        "with a single JSON object and nothing else:",
        '{"relevance": <number 0..1 — how strongly the document as a whole '
        'evidences this control>, "rationale": "<2-3 sentences citing the '
        'sections that matter>"}',
    ]
    return "\n".join(lines)


def _parse_recompute_response(text: str) -> tuple[float, Optional[str]]:
    """Extract (relevance, rationale); raises ValueError on any bad shape."""
    payload = cdm_intent._parse_json_object(cdm_intent._strip_code_fence(text))
    relevance = payload.get("relevance")
    if isinstance(relevance, bool) or not isinstance(relevance, (int, float)):
        raise ValueError(f"relevance is not a number: {relevance!r}")
    relevance = float(relevance)
    if not 0.0 <= relevance <= 1.0:
        raise ValueError(f"relevance out of range: {relevance!r}")
    rationale = payload.get("rationale")
    if rationale is not None and not isinstance(rationale, str):
        raise ValueError("rationale is not a string")
    return relevance, (rationale.strip() or None) if rationale else None


def recompute_proposals_llm(
    session: Session,
    org_id: UUID,
    *,
    provider: Optional[cdm_intent.IntentProvider] = None,
    timeout_s: Optional[float] = None,
    budget_s: Optional[float] = None,
    clock=time.monotonic,
) -> RecomputeSummary:
    """LLM upgrade pass over proposals awaiting recompute.

    A proposal awaits recompute while ``status='proposed'`` and
    ``recompute_provider IS NULL`` (accepted/dismissed are human decisions;
    stale waits for re-proposal). Provider failure on a group leaves its
    heuristic values in place and moves on — the group stays marked and a
    later run retries it. Commits per proposal for the same reason the
    heuristic pass does.
    """
    summary = RecomputeSummary()
    resolved_provider = provider if provider is not None else cdm_intent.get_intent_provider()
    if resolved_provider is None:
        return summary

    resolved_timeout = timeout_s if timeout_s is not None else get_consolidation_timeout_seconds()
    resolved_budget = budget_s if budget_s is not None else get_consolidation_budget_seconds()
    started = clock()

    pending_rows = session.execute(
        select(
            CDMControlProposal.id,
            CDMControlProposal.scoped_control_id,
            CDMControlProposal.cdm_document_id,
            SCFCatalogControl.scf_id,
            SCFCatalogControl.control_name,
            SCFCatalogControl.control_question,
            CDMDocument.original_filename,
        )
        .join(ScopedControl, CDMControlProposal.scoped_control_id == ScopedControl.id)
        .outerjoin(SCFCatalogControl, ScopedControl.scf_id == SCFCatalogControl.scf_id)
        .join(CDMDocument, CDMControlProposal.cdm_document_id == CDMDocument.id)
        .where(
            CDMControlProposal.organization_id == org_id,
            CDMControlProposal.status == "proposed",
            CDMControlProposal.recompute_provider.is_(None),
        )
        .order_by(CDMControlProposal.created_at)
    ).all()

    remaining = len(pending_rows)
    for (
        proposal_id,
        _control_id,
        _document_id,
        scf_id,
        control_name,
        control_question,
        original_filename,
    ) in pending_rows:
        if clock() - started > resolved_budget:
            summary.budget_exhausted = True
            break

        citations = [
            (r[0], r[1], r[2])
            for r in session.execute(
                select(
                    CDMMapping.section,
                    CDMMapping.relevance_score,
                    CDMMapping.excerpt,
                )
                .where(CDMMapping.control_proposal_id == proposal_id)
                .order_by(CDMMapping.relevance_score.desc())
            ).all()
        ]
        if not citations:
            remaining -= 1
            continue

        prompt = _build_recompute_prompt(
            scf_id, control_name, control_question, original_filename, citations
        )

        try:
            response = resolved_provider.classify(
                cdm_intent.IntentRequest(prompt=prompt, timeout_s=resolved_timeout)
            )
            relevance, rationale = _parse_recompute_response(response.text)
        except (cdm_intent.IntentProviderError, ValueError) as exc:
            logger.warning(
                "Consolidation recompute failed for proposal %s: %s", proposal_id, exc
            )
            summary.recompute_failures += 1
            continue
        except Exception:
            logger.exception(
                "Unexpected consolidation recompute error for proposal %s", proposal_id
            )
            summary.recompute_failures += 1
            continue

        # Optimistic: only stamp if still awaiting recompute — the reviewer
        # may have decided the proposal while the provider call was in flight.
        result = session.execute(
            update(CDMControlProposal)
            .where(
                CDMControlProposal.id == proposal_id,
                CDMControlProposal.status == "proposed",
                CDMControlProposal.recompute_provider.is_(None),
            )
            .values(
                consolidated_score=relevance,
                rationale=rationale,
                recompute_provider=resolved_provider.name,
                recompute_model_id=response.model_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
        if result.rowcount:
            summary.proposals_recomputed += 1
            remaining -= 1

    # Anything not successfully stamped is still pending (failures retry on a
    # later run). The caller re-enqueues only on budget_exhausted, so a
    # permanently failing group cannot spin the task in a tight loop.
    summary.proposals_remaining = max(remaining, 0)
    return summary
