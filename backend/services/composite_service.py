"""Control Assessment Composite — rollup service.

M3 (#575) — aggregates ``EvidenceWindowAssessment`` rows for a single
``(organization_id, scf_id)`` pair into one ``ControlAssessmentComposite``
record. Recompute is Celery-async via the ``evidence_composite`` queue;
the trigger is an ``after_commit`` session listener that watches for
terminal-status transitions on EvidenceWindowAssessment.

Module structure:
  - ``CURRENT_COMPUTATION_VERSION``: bump in code when the rollup algorithm
    changes (ISC-6).
  - ``_compute_composite()``: pure compute function. No I/O outside DB read.
    Returns the row dict to upsert. Tested directly.
  - ``recompute_control_composite_task``: Celery task wrapper that calls
    ``_compute_composite``, applies the idempotency-key short-circuit,
    upserts the row, and emits the per-compute structured log line (§7).
  - ``backfill_all_composites_task``: defined for PR 1 but not auto-triggered
    on migration (ISC-21 — execution is out of scope for PR 1).
  - Dispatcher: ``event.listen(Session, "after_commit", ...)`` walks
    ``session.new`` / ``session.dirty`` for ``EvidenceWindowAssessment``
    instances that transitioned to a terminal status (ISC-13) and enqueues
    recompute tasks on the ``evidence_composite`` queue.
  - ``register_dispatcher()``: idempotent attachment of the after_commit
    listener; called at module import and again from app startup so both
    the API process and the Celery worker fire the dispatcher.

This module follows the ``tasks_window_assessment.py`` style:
  - Synchronous psycopg2 session per Celery task.
  - Top-of-file imports only — no lazy imports inside task bodies (PR #584).
  - Structured per-compute log line for observability.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta
from decimal import Decimal
from math import ceil
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

import redis as sync_redis
from celery import shared_task
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from models import (
    ControlAssessmentComposite,
    EvidenceFile,
    EvidenceWindowAssessment,
    ScopedControl,
)

# Inlined to avoid pulling in services.validation_service's transitive
# boto3/storage imports — the constant is small, stable, and copying it here
# keeps composite_service startable inside Celery workers without S3 deps.
# Mirrors services.validation_service.STALENESS_THRESHOLDS exactly. Bumped
# in lockstep when the source-of-truth value changes (covered by ISC-A1's
# "M3 must not perturb existing infra" — a constant copy is fine, drift is
# the risk and is detected by the unit-test parity check below).
STALENESS_THRESHOLDS: Dict[str, int] = {
    "real_time": 2,
    "daily": 2,
    "weekly": 9,
    "monthly": 35,
    "quarterly": 95,
    "annual": 370,
    "on_demand": 35,
}

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

#: Bump when the rollup algorithm changes. Older rows become eligible for
#: unconditional recompute on the next trigger. See ISC-6.
CURRENT_COMPUTATION_VERSION: int = 1

#: Recompute target queue. Added to the celery_app queue list and to the
#: docker-compose / Terraform worker -Q flag in this PR.
COMPOSITE_QUEUE = "evidence_composite"

#: Idempotency-key TTL (ISC-14). Short-circuit duplicate computes within
#: a fan-out window. 30s matches spec.
IDEMPOTENCY_TTL_SECONDS = 30

#: Coalescing debounce (ISC-14a). 10s matches the observed inter-file-upload
#: cadence on CG production.
DEBOUNCE_SECONDS = 10

#: Circuit-breaker threshold (ISC-14b). Once the queue depth exceeds this,
#: the dispatcher stops enqueuing. Operator flips
#: ``FORCE_COMPOSITE_RECOMPUTE_OFF=true`` to halt the queue entirely.
CIRCUIT_BREAKER_QUEUE_DEPTH = 500

#: Statuses that count as "terminal" for the dispatcher whitelist (ISC-13).
TERMINAL_WINDOW_STATUSES = frozenset(
    {"sufficient", "partial", "insufficient", "insufficient_sample"},
)

#: Per-evidence weight default. M3 uses uniform weighting; ISC-8 reserves
#: the formula shape for per-evidence weights when SCFCatalogEvidence.weight
#: is added in M3.1.
DEFAULT_EVIDENCE_WEIGHT = Decimal("1.0")

#: Stale window penalty multiplier (ISC-10).
STALE_RELEVANCE_MULTIPLIER = Decimal("0.5")

#: Worst-of severity ordering (worst -> best). Higher index == worse.
_STATUS_SEVERITY: List[str] = [
    "sufficient",
    "partial",
    "insufficient",
]


# ---------------------------------------------------------------------------
# Sync DB session (matches tasks_window_assessment._get_sync_session)
# ---------------------------------------------------------------------------

_SYNC_DATABASE_URL = (
    os.getenv("DATABASE_URL", "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf")
    .replace("+asyncpg", "+psycopg2")
    .replace("?ssl=require", "?sslmode=require")
)

_sync_engine = None
SyncSession: Optional[sessionmaker] = None


def _get_sync_session() -> Session:
    """Return a synchronous SQLAlchemy session (lazy-init engine).

    Mirrors the tasks_window_assessment helper so Celery tasks share the same
    pool semantics. The engine is module-global; sessions are short-lived.
    """
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(
            _SYNC_DATABASE_URL,
            pool_pre_ping=True,
            pool_size=2,
            max_overflow=3,
        )
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


# ---------------------------------------------------------------------------
# Sync Redis (matches tasks_vendor_assessment._get_sync_redis pattern)
# ---------------------------------------------------------------------------

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _get_sync_redis():
    """Return a synchronous Redis client. Top-of-file import — no laziness."""
    return sync_redis.from_url(
        _REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
        socket_keepalive=True,
        retry_on_timeout=True,
        health_check_interval=30,
    )


# ---------------------------------------------------------------------------
# Idempotency, debounce, circuit-breaker helpers
# ---------------------------------------------------------------------------

def _idempotency_key(organization_id: UUID, scf_id: str, version: int) -> str:
    """sha256 over (org, scf_id, version) — ISC-14."""
    raw = f"{organization_id}:{scf_id}:v{version}".encode("utf-8")
    return f"composite:idem:{hashlib.sha256(raw).hexdigest()}"


def _debounce_key(organization_id: UUID) -> str:
    """Per-org sorted set key for fan-out coalescing — ISC-14a."""
    return f"composite:debounce:{organization_id}"


def _claim_idempotency(organization_id: UUID, scf_id: str, version: int) -> bool:
    """SET NX with TTL. Returns True if we won the claim, False if already in flight."""
    try:
        rds = _get_sync_redis()
        key = _idempotency_key(organization_id, scf_id, version)
        # SET key value NX EX 30 — atomic claim
        result = rds.set(key, "1", nx=True, ex=IDEMPOTENCY_TTL_SECONDS)
        return bool(result)
    except Exception as exc:  # pragma: no cover — soft-fail on Redis hiccup
        logger.warning(
            "composite_service: idempotency claim failed (proceeding): %s", exc,
        )
        return True


def _should_debounce(organization_id: UUID, scf_id: str, now_ts: float) -> bool:
    """Return True if a recent enqueue for (org, scf_id) is still in the debounce window."""
    try:
        rds = _get_sync_redis()
        key = _debounce_key(organization_id)
        # Drop entries older than the debounce window
        rds.zremrangebyscore(key, 0, now_ts - DEBOUNCE_SECONDS)
        # Look up most recent score for this scf_id
        score = rds.zscore(key, scf_id)
        if score is not None and (now_ts - score) < DEBOUNCE_SECONDS:
            return True
        rds.zadd(key, {scf_id: now_ts})
        # Set a TTL on the sorted-set itself so an inactive org doesn't
        # leave entries hanging forever.
        rds.expire(key, DEBOUNCE_SECONDS * 6)
        return False
    except Exception as exc:  # pragma: no cover — soft-fail
        logger.warning(
            "composite_service: debounce check failed (proceeding): %s", exc,
        )
        return False


def _is_circuit_open() -> bool:
    """Check the circuit breaker — ISC-14b.

    Two gates: the env var ``FORCE_COMPOSITE_RECOMPUTE_OFF=true`` (operator
    kill switch) and the queue-depth threshold sampled via Celery inspect.
    Either tripped == circuit open.

    The queue-depth probe is best-effort — if the celery inspect call fails
    (e.g. running inside the worker that owns the queue), we proceed rather
    than block. The env-var path is the authoritative manual override.
    """
    if os.getenv("FORCE_COMPOSITE_RECOMPUTE_OFF", "").lower() == "true":
        return True
    try:
        # Lazy reference to celery_app to avoid an import cycle at module
        # import time (composite_service is imported before celery_app may
        # have finished bootstrapping in some startup orders). The function
        # body is fine — this isn't a Celery task body.
        from celery_app import celery_app  # noqa: WPS433  (intentional)

        inspector = celery_app.control.inspect(timeout=1.0)
        if inspector is None:
            return False
        active = inspector.active() or {}
        reserved = inspector.reserved() or {}
        total = sum(len(v) for v in active.values()) + sum(
            len(v) for v in reserved.values()
        )
        if total > CIRCUIT_BREAKER_QUEUE_DEPTH:
            logger.warning(
                "CompositeRecomputeCircuitOpen depth=%d threshold=%d",
                total, CIRCUIT_BREAKER_QUEUE_DEPTH,
            )
            return True
        return False
    except Exception:
        # Best-effort probe — never block compute on a flaky inspect call.
        return False


# ---------------------------------------------------------------------------
# Pure compute (the meat — ISC-7..11)
# ---------------------------------------------------------------------------

def _worst_of(statuses: List[str]) -> str:
    """Return the worst status per ISC-7 ordering (insufficient > partial > sufficient)."""
    worst_idx = -1
    for s in statuses:
        if s in _STATUS_SEVERITY:
            idx = _STATUS_SEVERITY.index(s)
            if idx > worst_idx:
                worst_idx = idx
    if worst_idx < 0:
        # No statuses ranked — caller should have hit no_evidence first.
        return "insufficient"
    return _STATUS_SEVERITY[worst_idx]


def _is_window_stale(window: EvidenceWindowAssessment, latest_file_at: Optional[datetime]) -> bool:
    """Stale = latest_file_at older than 2 x cadence (ISC-10)."""
    if latest_file_at is None:
        return False
    threshold_days = STALENESS_THRESHOLDS.get(window.frequency_used or "monthly", 35)
    cutoff = datetime.utcnow() - timedelta(days=threshold_days * 2)
    return latest_file_at < cutoff


def _required_artifact_types_for_control(
    session: Session, scf_id: str,
) -> List[Dict[str, Any]]:
    """Return the catalog control's required_artifact_types JSONB list.

    Empty list if the control has no extracted requirements yet.
    """
    row = session.execute(
        select(SCFCatalogControl.required_artifact_types).where(
            SCFCatalogControl.scf_id == scf_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return []
    return list(row) if isinstance(row, list) else []


def _evidence_ids_for_control(
    session: Session, scf_id: str,
) -> List[str]:
    """Resolve the evidence IDs mapped to this SCF control via SCFCatalogEvidence.

    Mirrors the ISC-24 acceptance query: ``sce.control_mappings ? sc.scf_id``.
    Returns a deterministic-ordered list (ascending evidence_id) so test
    expectations stay stable.
    """
    rows = session.execute(
        text(
            """
            SELECT evidence_id
              FROM scf_catalog_evidence
             WHERE control_mappings ? :scf_id
             ORDER BY evidence_id ASC
            """
        ),
        {"scf_id": scf_id},
    ).all()
    return [r[0] for r in rows]


def _latest_window_per_evidence(
    session: Session, organization_id: UUID, evidence_ids: List[str],
) -> Dict[str, EvidenceWindowAssessment]:
    """Return the most recent terminal-status window per evidence_id.

    "Most recent" is by ``window_end DESC, assessed_at DESC`` so a freshly
    assessed late-arriving window beats an older window with newer
    assessed_at — the user-facing "current state" of the evidence.

    Error-status windows are excluded from the latest-window search (ISC-8
    excludes them from numerator/denominator) but are reported as
    ``window_error`` in mandatory_gaps if they're the only thing present.
    """
    if not evidence_ids:
        return {}
    latest: Dict[str, EvidenceWindowAssessment] = {}
    rows = (
        session.query(EvidenceWindowAssessment)
        .filter(
            EvidenceWindowAssessment.organization_id == organization_id,
            EvidenceWindowAssessment.evidence_id.in_(evidence_ids),
            EvidenceWindowAssessment.status.in_(list(TERMINAL_WINDOW_STATUSES)),
        )
        .order_by(
            EvidenceWindowAssessment.evidence_id.asc(),
            EvidenceWindowAssessment.window_end.desc(),
            EvidenceWindowAssessment.assessed_at.desc().nullslast(),
        )
        .all()
    )
    for row in rows:
        if row.evidence_id not in latest:
            latest[row.evidence_id] = row
    return latest


def _latest_file_at_per_evidence(
    session: Session, organization_id: UUID, evidence_ids: List[str],
) -> Dict[str, datetime]:
    """Map evidence_id -> max(uploaded_at) for non-deleted files.

    Used for the stale-window check (ISC-10).
    """
    if not evidence_ids:
        return {}
    rows = session.execute(
        text(
            """
            SELECT evidence_id, MAX(uploaded_at) AS latest_at
              FROM evidence_files
             WHERE organization_id = :org_id
               AND is_deleted = false
               AND evidence_id = ANY(:ev_ids)
             GROUP BY evidence_id
            """
        ),
        {"org_id": str(organization_id), "ev_ids": list(evidence_ids)},
    ).all()
    return {r[0]: r[1] for r in rows}


def _compute_composite(
    session: Session,
    organization_id: UUID,
    scf_id: str,
) -> Dict[str, Any]:
    """Pure compute — return the row dict to upsert.

    No Celery, no Redis, no I/O beyond DB reads. Tested directly. Encodes
    ISC-7..11.

    Returns a dict with keys matching ``ControlAssessmentComposite`` columns
    plus ``computed_at``. Caller is responsible for the upsert.
    """
    now = datetime.utcnow()

    evidence_ids = _evidence_ids_for_control(session, scf_id)

    # ISC-7 step 1: empty mapping -> no_evidence.
    if not evidence_ids:
        return _row(
            organization_id=organization_id,
            scf_id=scf_id,
            composite_status="no_evidence",
            composite_score=None,
            included_window_ids=[],
            included_evidence_ids=[],
            mandatory_gaps=[],
            computed_at=now,
        )

    # Required artifact types (with mandatory flags) for this control.
    required_artifact_types = _required_artifact_types_for_control(session, scf_id)
    mandatory_types = [
        rt["type"] for rt in required_artifact_types
        if rt.get("mandatory", False) and rt.get("type")
    ]

    latest_per_ev = _latest_window_per_evidence(
        session, organization_id, evidence_ids,
    )
    latest_file_per_ev = _latest_file_at_per_evidence(
        session, organization_id, evidence_ids,
    )

    # If NO evidence has any terminal-status window at all, this control has
    # mappings but no usable assessments — distinct from "no mappings".
    if not latest_per_ev:
        return _row(
            organization_id=organization_id,
            scf_id=scf_id,
            composite_status="no_evidence",
            composite_score=None,
            included_window_ids=[],
            included_evidence_ids=[],
            mandatory_gaps=[
                {"evidence_id": ev, "reason": "missing_window"}
                for ev in evidence_ids
            ],
            computed_at=now,
        )

    # Sample-size guard (ISC-11).
    sample_floor = max(1, ceil(len(evidence_ids) / 3))
    if len(latest_per_ev) < sample_floor:
        # Even when we have some windows, if we're below the sample floor,
        # we can't commit a score. Still surface the missing windows so the
        # UI can show partial provenance.
        gaps: List[Dict[str, Any]] = []
        for ev in evidence_ids:
            if ev not in latest_per_ev:
                gaps.append({"evidence_id": ev, "reason": "missing_window"})
        return _row(
            organization_id=organization_id,
            scf_id=scf_id,
            composite_status="insufficient_sample",
            composite_score=None,
            included_window_ids=[
                str(w.id) for w in latest_per_ev.values()
            ],
            included_evidence_ids=sorted(latest_per_ev.keys()),
            mandatory_gaps=gaps,
            computed_at=now,
        )

    # Per-evidence statuses + score contributions.
    per_evidence_statuses: List[str] = []
    weighted_numerator = Decimal("0")
    weighted_denominator = Decimal("0")
    mandatory_gaps: List[Dict[str, Any]] = []
    included_window_ids: List[str] = []
    included_evidence_ids: List[str] = []

    for ev in evidence_ids:
        window = latest_per_ev.get(ev)
        if window is None:
            # Missing window — ISC-10. Treated as insufficient contribution.
            per_evidence_statuses.append("insufficient")
            mandatory_gaps.append({"evidence_id": ev, "reason": "missing_window"})
            continue

        included_window_ids.append(str(window.id))
        included_evidence_ids.append(ev)

        # Map insufficient_sample to the worst-of input vocabulary; ISC-11
        # says it dominates only when N is below the sample floor, which we
        # already handled at composite level.
        ev_status = window.status
        if ev_status == "insufficient_sample":
            per_evidence_statuses.append("insufficient")
        elif ev_status in _STATUS_SEVERITY:
            per_evidence_statuses.append(ev_status)
        else:
            # error or unknown — exclude from worst-of (ISC-8).
            mandatory_gaps.append({
                "evidence_id": ev, "reason": "window_error",
            })

        # Score contribution.
        relevance = window.relevance_score
        if relevance is None:
            # No score (e.g. insufficient_sample, error) -> 0 contribution.
            relevance_decimal = Decimal("0")
        else:
            relevance_decimal = Decimal(str(relevance))

        # M4 PR 3: human review overrides AI signal for composite math.
        # Decisions D1 + D2 (rejected and needs_revision both force
        # insufficient + zero relevance, with distinct gap reasons so the
        # UI can tell "reviewer says wrong" from "reviewer wants re-run").
        # D4: approved == not_reviewed == None all fall through unchanged.
        review_status = getattr(window, "review_status", None)
        if review_status == "rejected":
            # Replace the AI-derived ev_status contribution with insufficient.
            if per_evidence_statuses and per_evidence_statuses[-1] == ev_status:
                per_evidence_statuses[-1] = "insufficient"
            else:
                per_evidence_statuses.append("insufficient")
            mandatory_gaps.append({"evidence_id": ev, "reason": "review_rejected"})
            relevance_decimal = Decimal("0")
        elif review_status == "needs_revision":
            if per_evidence_statuses and per_evidence_statuses[-1] == ev_status:
                per_evidence_statuses[-1] = "insufficient"
            else:
                per_evidence_statuses.append("insufficient")
            mandatory_gaps.append({"evidence_id": ev, "reason": "pending_revision"})
            relevance_decimal = Decimal("0")

        if ev_status == "error":
            # Excluded from numerator AND denominator (ISC-8).
            continue

        weight = DEFAULT_EVIDENCE_WEIGHT
        # Stale-window check (ISC-10): 0.5x relevance + stale gap entry.
        latest_file_at = latest_file_per_ev.get(ev)
        if _is_window_stale(window, latest_file_at):
            relevance_decimal = relevance_decimal * STALE_RELEVANCE_MULTIPLIER
            mandatory_gaps.append({"evidence_id": ev, "reason": "stale"})

        # Mandatory artifact-type coverage check (ISC-9).
        coverage = window.artifact_type_coverage or {}
        for mandatory_type in mandatory_types:
            entry = coverage.get(mandatory_type) if isinstance(coverage, dict) else None
            present = bool(entry.get("present")) if isinstance(entry, dict) else False
            if not present:
                mandatory_gaps.append({
                    "evidence_id": ev,
                    "artifact_type": mandatory_type,
                    "reason": "missing",
                })

        # window_insufficient is surfaced as an explicit gap reason so the UI
        # can distinguish "single insufficient window dominated everything"
        # from "everything was fine but mandatory artifact type missing".
        if ev_status == "insufficient":
            mandatory_gaps.append({
                "evidence_id": ev, "reason": "window_insufficient",
            })

        weighted_numerator += weight * relevance_decimal
        weighted_denominator += weight

    # Worst-of status across per-evidence outcomes.
    if per_evidence_statuses:
        rolled_up = _worst_of(per_evidence_statuses)
    else:
        rolled_up = "insufficient"

    # ISC-9: non-empty mandatory_gaps forces insufficient.
    # M4 PR 3: review_rejected and pending_revision are reviewer overrides
    # — they also force insufficient (D1 + D2).
    has_mandatory_gap = any(
        g.get("reason") in {
            "missing", "missing_window", "window_insufficient",
            "review_rejected", "pending_revision",
        }
        or g.get("artifact_type") is not None
        for g in mandatory_gaps
    )
    if has_mandatory_gap:
        rolled_up = "insufficient"

    # Score (ISC-8).
    if weighted_denominator > 0:
        score = (weighted_numerator / weighted_denominator).quantize(Decimal("0.01"))
        composite_score: Optional[Decimal] = score
    else:
        composite_score = None

    return _row(
        organization_id=organization_id,
        scf_id=scf_id,
        composite_status=rolled_up,
        composite_score=composite_score,
        included_window_ids=included_window_ids,
        included_evidence_ids=sorted(set(included_evidence_ids)),
        mandatory_gaps=mandatory_gaps,
        computed_at=datetime.utcnow(),
    )


def _row(
    organization_id: UUID,
    scf_id: str,
    composite_status: str,
    composite_score: Optional[Decimal],
    included_window_ids: List[str],
    included_evidence_ids: List[str],
    mandatory_gaps: List[Dict[str, Any]],
    computed_at: datetime,
) -> Dict[str, Any]:
    """Bag the upsert payload — single source of truth for column shape."""
    return {
        "organization_id": organization_id,
        "scf_id": scf_id,
        "composite_status": composite_status,
        "composite_score": composite_score,
        "included_window_ids": list(included_window_ids),
        "included_evidence_ids": list(included_evidence_ids),
        "mandatory_gaps": list(mandatory_gaps),
        "computation_version": CURRENT_COMPUTATION_VERSION,
        "computed_at": computed_at,
    }


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def _upsert_composite(session: Session, payload: Dict[str, Any]) -> ControlAssessmentComposite:
    """ON CONFLICT (org, scf_id) DO UPDATE — Postgres-native upsert.

    Returns the persisted row (refreshed). Caller owns commit.
    """
    stmt = (
        pg_insert(ControlAssessmentComposite.__table__)
        .values(**payload)
        .on_conflict_do_update(
            constraint="uq_control_assessment_composites_org_scf",
            set_={
                "composite_status": payload["composite_status"],
                "composite_score": payload["composite_score"],
                "included_window_ids": payload["included_window_ids"],
                "included_evidence_ids": payload["included_evidence_ids"],
                "mandatory_gaps": payload["mandatory_gaps"],
                "computation_version": payload["computation_version"],
                "computed_at": payload["computed_at"],
                "updated_at": datetime.utcnow(),
            },
        )
        .returning(ControlAssessmentComposite.__table__.c.id)
    )
    result = session.execute(stmt).one()
    composite_id = result[0]
    return (
        session.query(ControlAssessmentComposite)
        .filter(ControlAssessmentComposite.id == composite_id)
        .one()
    )


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="services.composite_service.recompute_control_composite_task",
    time_limit=120,
    soft_time_limit=90,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def recompute_control_composite_task(
    self,
    organization_id: str,
    scf_id: str,
    trigger_event: str = "after_commit",
) -> Dict[str, Any]:
    """Recompute the composite for one (org, scf_id).

    Honours the idempotency-key short-circuit (ISC-14). Emits a structured
    per-compute log line on success (§7).
    """
    task_id = self.request.id
    org_uuid = UUID(organization_id)
    started_at = time.monotonic()

    if not _claim_idempotency(org_uuid, scf_id, CURRENT_COMPUTATION_VERSION):
        logger.info(
            "recompute_control_composite_task[%s] short-circuit: idempotency hit org=%s scf=%s",
            task_id, organization_id, scf_id,
        )
        return {
            "status": "deduplicated",
            "organization_id": organization_id,
            "scf_id": scf_id,
        }

    session = _get_sync_session()
    try:
        payload = _compute_composite(session, org_uuid, scf_id)
        composite = _upsert_composite(session, payload)
        session.commit()
        elapsed_ms = int((time.monotonic() - started_at) * 1000)

        # Structured per-compute log line (§7) — JSON for easy grep.
        logger.info(
            json.dumps(
                {
                    "event": "composite_recompute",
                    "scf_id": scf_id,
                    "organization_id": organization_id,
                    "trigger_event": trigger_event,
                    "windows_considered": len(payload["included_window_ids"]),
                    "resulting_status": payload["composite_status"],
                    "elapsed_ms": elapsed_ms,
                    "computation_version": CURRENT_COMPUTATION_VERSION,
                }
            )
        )
        return {
            "composite_id": str(composite.id),
            "composite_status": composite.composite_status,
            "composite_score": (
                float(composite.composite_score)
                if composite.composite_score is not None else None
            ),
            "elapsed_ms": elapsed_ms,
        }
    except Exception as exc:
        session.rollback()
        logger.error(
            "recompute_control_composite_task[%s] failed org=%s scf=%s: %s",
            task_id, organization_id, scf_id, exc, exc_info=True,
        )
        raise
    finally:
        session.close()


@shared_task(
    bind=True,
    name="services.composite_service.backfill_all_composites_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def backfill_all_composites_task(
    self, organization_id: str,
) -> Dict[str, Any]:
    """One-off backfill — enqueue recompute for every scoped control in an org.

    Defined in PR 1 per ISC-21 but NOT auto-triggered on migration. Operator
    invokes it manually after the migration lands. Rate-limited to ~10 tasks
    per second by the per-task ``apply_async(countdown=...)`` stagger.
    """
    task_id = self.request.id
    org_uuid = UUID(organization_id)
    logger.info(
        "backfill_all_composites_task[%s] starting org=%s",
        task_id, organization_id,
    )
    session = _get_sync_session()
    try:
        scoped_rows = (
            session.query(ScopedControl.scf_id)
            .filter(
                ScopedControl.organization_id == org_uuid,
                ScopedControl.selected.is_(True),
            )
            .distinct()
            .all()
        )
        scf_ids = [r[0] for r in scoped_rows]
        for idx, scf_id in enumerate(scf_ids):
            # Stagger ~10/s to avoid queue thrash.
            recompute_control_composite_task.apply_async(
                kwargs={
                    "organization_id": organization_id,
                    "scf_id": scf_id,
                    "trigger_event": "backfill",
                },
                queue=COMPOSITE_QUEUE,
                countdown=int(idx / 10),
            )
        logger.info(
            "backfill_all_composites_task[%s] enqueued=%d org=%s",
            task_id, len(scf_ids), organization_id,
        )
        return {"enqueued": len(scf_ids), "organization_id": organization_id}
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Dispatcher — after_commit listener for EvidenceWindowAssessment writes
# ---------------------------------------------------------------------------

# Per-session storage of the (instance, prior_status) tuples we observed in
# before_flush. We must capture prior_status BEFORE the flush, because
# after_commit no longer has access to the pre-update value.
_PENDING_KEY = "_composite_pending"


def _stash_pending(session: Session) -> List[Tuple[UUID, str, str]]:
    """Return the per-session pending list, creating it if absent."""
    pending: Optional[List[Tuple[UUID, str, str]]] = session.info.get(_PENDING_KEY)
    if pending is None:
        pending = []
        session.info[_PENDING_KEY] = pending
    return pending


def _before_flush_handler(session: Session, flush_context, instances) -> None:
    """Capture EvidenceWindowAssessment status transitions before flush.

    Walks ``session.new`` (inserts) and ``session.dirty`` (updates), records
    ``(organization_id, evidence_id, new_status)`` for every row that
    transitions INTO a terminal status (ISC-13 whitelist). The actual
    enqueue happens in ``_after_commit_handler`` once the row is visible.

    For inserts: if the new status is terminal, that's a transition.
    For updates: if the new status is terminal AND the prior status was NOT
    terminal, that's a transition. ``error -> terminal`` does fire (a row
    that erroneously errored and then resolved should refresh the
    composite); ``terminal -> error`` does not fire (errors don't inform
    composites — ISC-13).
    """
    pending = _stash_pending(session)

    # Inserts — session.new
    for obj in session.new:
        if not isinstance(obj, EvidenceWindowAssessment):
            continue
        new_status = getattr(obj, "status", None)
        if new_status in TERMINAL_WINDOW_STATUSES:
            pending.append((obj.organization_id, obj.evidence_id, new_status))

    # Updates — session.dirty
    from sqlalchemy.orm import attributes as orm_attributes  # local for clarity
    for obj in session.dirty:
        if not isinstance(obj, EvidenceWindowAssessment):
            continue

        # M3 (#575) — status transitions into terminal whitelist fire recompute.
        new_status = getattr(obj, "status", None)
        if new_status in TERMINAL_WINDOW_STATUSES:
            hist = orm_attributes.get_history(obj, "status")
            prior_values = list(hist.deleted)
            if prior_values:
                prior = prior_values[0]
                if prior != "error" and prior not in TERMINAL_WINDOW_STATUSES:
                    # pending->processing transitions never reach here because
                    # processing is not in the terminal whitelist.
                    pending.append((obj.organization_id, obj.evidence_id, new_status))

        # M4 PR 3 — review_status transitions ALSO fire recompute. Any change
        # is meaningful (approve/reject/needs_revision/reset-to-not_reviewed
        # all alter composite contribution per D1, D2, D4). Independent of
        # status-transition firing above — by_evidence dedupes downstream.
        hist_review = orm_attributes.get_history(obj, "review_status")
        review_prior = list(hist_review.deleted)
        if review_prior:
            pending.append((
                obj.organization_id,
                obj.evidence_id,
                getattr(obj, "status", "review_change") or "review_change",
            ))


def _after_commit_handler(session: Session) -> None:
    """Enqueue recompute tasks for the (org, scf_id) pairs touched by this commit."""
    pending: Optional[List[Tuple[UUID, str, str]]] = session.info.get(_PENDING_KEY)
    if not pending:
        return
    # Clear the stash NOW — even if enqueue raises, we don't want stale
    # entries leaking into the next transaction on this session.
    session.info[_PENDING_KEY] = []

    if _is_circuit_open():
        logger.warning(
            "composite_service: circuit open, skipping enqueue for %d events",
            len(pending),
        )
        return

    # Collapse to (org, evidence_id) and resolve each evidence_id -> mapped
    # scf_ids via SCFCatalogEvidence. Use a fresh short-lived session for
    # the resolution because the caller's session has just committed.
    by_evidence: Dict[Tuple[UUID, str], None] = {}
    for org_id, evidence_id, _status in pending:
        by_evidence[(org_id, evidence_id)] = None

    if not by_evidence:
        return

    resolver_session: Optional[Session] = None
    try:
        resolver_session = _get_sync_session()
        evidence_to_scf: Dict[str, List[str]] = {}
        evidence_ids = list({k[1] for k in by_evidence})
        rows = resolver_session.execute(
            text(
                """
                SELECT evidence_id, control_mappings
                  FROM scf_catalog_evidence
                 WHERE evidence_id = ANY(:ev_ids)
                """
            ),
            {"ev_ids": evidence_ids},
        ).all()
        for ev_id, mappings in rows:
            evidence_to_scf[ev_id] = list(mappings or [])

        now_ts = time.time()
        for (org_id, evidence_id) in by_evidence:
            scf_ids = evidence_to_scf.get(evidence_id, [])
            for scf_id in scf_ids:
                if _should_debounce(org_id, scf_id, now_ts):
                    logger.debug(
                        "composite_service: debounced org=%s scf=%s",
                        org_id, scf_id,
                    )
                    continue
                try:
                    recompute_control_composite_task.apply_async(
                        kwargs={
                            "organization_id": str(org_id),
                            "scf_id": scf_id,
                            "trigger_event": "after_commit",
                        },
                        queue=COMPOSITE_QUEUE,
                    )
                except Exception as exc:
                    logger.error(
                        "composite_service: enqueue failed org=%s scf=%s: %s",
                        org_id, scf_id, exc,
                    )
    finally:
        if resolver_session is not None:
            resolver_session.close()


_DISPATCHER_REGISTERED = False


def register_dispatcher() -> None:
    """Idempotently attach the after_commit dispatcher to ``Session``.

    Safe to call multiple times (FastAPI startup AND celery worker bootstrap).
    Listens on the SQLAlchemy ORM ``Session`` class so both async-wrapped and
    plain sync sessions fire the handler.
    """
    global _DISPATCHER_REGISTERED
    if _DISPATCHER_REGISTERED:
        return
    event.listen(Session, "before_flush", _before_flush_handler)
    event.listen(Session, "after_commit", _after_commit_handler)
    _DISPATCHER_REGISTERED = True
    logger.info("composite_service: after_commit dispatcher registered")


# Register at import time — ensures both API and Celery paths attach the
# listener as soon as this module is loaded.
register_dispatcher()
