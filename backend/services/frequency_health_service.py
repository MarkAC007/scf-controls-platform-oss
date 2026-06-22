"""Frequency-health detection service.

M4 PR 1 (#574) — pure compute layer for the Frequency Health UX. Detects
mismatches between ``EvidenceTracking.frequency`` (declared cadence) and
the observed cadence inferred from ``EvidenceFile.uploaded_at`` histograms
over the last 90 days, per evidence_id.

This module exposes ``compute_for_org(db, org_id) -> FrequencyHealthReport``
and is consumed in M4 PR 2 by the new
``GET /organizations/{org_id}/evidence/frequency-health`` endpoint. PR 1
ships compute only — no API surface change.

Algorithm: see spec ISC-6..9 in ``/tmp/m4-design-spec.md``. Median + IQR
based, robust to outliers from missed runs. Bucket boundaries chosen to
align with ``STALENESS_THRESHOLDS`` (~30% headroom either side).
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import EvidenceFile, EvidenceTracking

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tunables (mirror spec — change here if spec changes)
# ---------------------------------------------------------------------------

EVALUATION_WINDOW_DAYS: int = 90

# Confidence thresholds — (min_file_count, max_cv).
# Order matters: first match wins, descending strictness.
_CONFIDENCE_BANDS: List[Tuple[str, int, float]] = [
    ("high", 8, 0.30),
    ("medium", 4, 0.50),
    ("low", 2, 1.00),
]

# Median-gap bucket boundaries (days) → suggested frequency.
# Per spec: <0.25 → real_time, [0.25, 1.5) → daily, [1.5, 12) → weekly,
# [12, 50) → monthly, [50, 200) → quarterly, ≥200 → annual.
_REAL_TIME_BOUNDARY: float = 0.25
_DAILY_BOUNDARY: float = 1.5
_WEEKLY_BOUNDARY: float = 12.0
_MONTHLY_BOUNDARY: float = 50.0
_QUARTERLY_BOUNDARY: float = 200.0


# ---------------------------------------------------------------------------
# Result types (spec ISC-7)
# ---------------------------------------------------------------------------


@dataclass
class FrequencyObservation:
    """Per-evidence_id cadence observation.

    Mirrors spec ISC-7 exactly. Field order intentionally matches the
    JSON response shape (ISC-18) so a downstream Pydantic model in PR 2
    can map field-for-field.
    """

    evidence_id: str
    declared_frequency: Optional[str]
    observed_cadence_days: Optional[float]
    suggested_frequency: Optional[str]
    confidence: str
    file_count: int
    misaligned: bool
    reason: str


@dataclass
class FrequencyHealthReport:
    """Aggregate report for an organization.

    Returned by :func:`compute_for_org`. PR 2 wraps this into the API
    response shape (ISC-18). PR 1 surfaces it via tests only.
    """

    items: List[FrequencyObservation] = field(default_factory=list)
    misaligned_count: int = 0
    low_confidence_count: int = 0
    total_evidence_ids_evaluated: int = 0
    computed_at: datetime = field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Pure helpers (no I/O — fully tested in test_frequency_health_service.py)
# ---------------------------------------------------------------------------


def _gaps_days(uploaded_ats: List[datetime]) -> List[float]:
    """Compute sorted inter-arrival gaps in days. Caller passes already
    in chronological order. Returns ``[]`` for ``len < 2``."""
    if len(uploaded_ats) < 2:
        return []
    sorted_ts = sorted(uploaded_ats)
    return [
        (sorted_ts[i + 1] - sorted_ts[i]).total_seconds() / 86400.0
        for i in range(len(sorted_ts) - 1)
    ]


def _compute_iqr_cv(gaps: List[float]) -> Tuple[float, float]:
    """Return (median_gap, cv) where cv = IQR / median.

    Per spec ISC-8 step 3:
    - For N gaps where N < 4 (i.e. fewer than 4 observed gaps), the
      ``statistics.quantiles(n=4)`` call would either fail or be
      meaningless — return cv=0 in that case so the confidence band can
      still be assigned by file_count alone.
    - For N >= 4, IQR = Q3 - Q1, cv = IQR / median.
    - If median == 0 (all gaps are zero — burst within the same instant),
      cv is treated as +inf to drop confidence to insufficient.

    Returns (0.0, 0.0) for empty input.
    """
    if not gaps:
        return 0.0, 0.0

    median_gap = statistics.median(gaps)

    if median_gap == 0:
        # All-zero gaps — degenerate (e.g. webhook burst with identical
        # uploaded_at). Treat as maximally noisy.
        return 0.0, float("inf")

    # statistics.quantiles requires at least 2 data points for n=4 in
    # Python 3.8+, but the boundary semantics (Q1, Q3 from 4 buckets) are
    # only meaningful at N >= 4. Below that, follow spec: cv=0.
    if len(gaps) < 4:
        return median_gap, 0.0

    qs = statistics.quantiles(gaps, n=4)
    iqr = qs[2] - qs[0]
    cv = iqr / median_gap
    return median_gap, cv


def _classify_confidence(file_count: int, cv: float) -> str:
    """Map (file_count, cv) → confidence band per spec ISC-8 step 4.

    Returns one of: 'high', 'medium', 'low', 'insufficient'.
    """
    for band, min_n, max_cv in _CONFIDENCE_BANDS:
        if file_count >= min_n and cv <= max_cv:
            return band
    return "insufficient"


def _bucket_median_to_frequency(median_gap_days: float) -> str:
    """Map a median gap (days) to the spec frequency vocabulary.

    Per spec ISC-8 step 5:
    - < 0.25  → real_time
    - < 1.5   → daily
    - < 12    → weekly
    - < 50    → monthly
    - < 200   → quarterly
    - >= 200  → annual
    """
    if median_gap_days < _REAL_TIME_BOUNDARY:
        return "real_time"
    if median_gap_days < _DAILY_BOUNDARY:
        return "daily"
    if median_gap_days < _WEEKLY_BOUNDARY:
        return "weekly"
    if median_gap_days < _MONTHLY_BOUNDARY:
        return "monthly"
    if median_gap_days < _QUARTERLY_BOUNDARY:
        return "quarterly"
    return "annual"


def _observe_one(
    evidence_id: str,
    declared_frequency: Optional[str],
    uploaded_ats: List[datetime],
) -> FrequencyObservation:
    """Compute a single FrequencyObservation. Pure function — no DB.

    Implements the full algorithm per spec ISC-8.
    """
    file_count = len(uploaded_ats)

    # ISC-8 step 1: empty
    if file_count == 0:
        return FrequencyObservation(
            evidence_id=evidence_id,
            declared_frequency=declared_frequency,
            observed_cadence_days=None,
            suggested_frequency=None,
            confidence="insufficient",
            file_count=0,
            misaligned=False,
            reason="no_files",
        )

    # ISC-8 step 2: single file
    if file_count == 1:
        return FrequencyObservation(
            evidence_id=evidence_id,
            declared_frequency=declared_frequency,
            observed_cadence_days=None,
            suggested_frequency=None,
            confidence="insufficient",
            file_count=1,
            misaligned=False,
            reason="single_file",
        )

    # ISC-8 step 3: 2+ files
    gaps = _gaps_days(uploaded_ats)
    median_gap, cv = _compute_iqr_cv(gaps)

    # ISC-8 step 4: confidence
    confidence = _classify_confidence(file_count, cv)

    if confidence == "insufficient":
        return FrequencyObservation(
            evidence_id=evidence_id,
            declared_frequency=declared_frequency,
            observed_cadence_days=median_gap if median_gap > 0 else None,
            suggested_frequency=None,
            confidence="insufficient",
            file_count=file_count,
            misaligned=False,
            reason="irregular_cadence",
        )

    # ISC-8 step 5: bucket median to frequency
    suggested = _bucket_median_to_frequency(median_gap)

    # ISC-8 step 7: no declared frequency is its own special case
    if declared_frequency is None or declared_frequency == "":
        # Surface as misaligned only when confidence is high or medium
        # (matches step 6 semantics — low-confidence observations never
        # generate fix suggestions to avoid noise on bursty collectors).
        flag = confidence in {"high", "medium"}
        return FrequencyObservation(
            evidence_id=evidence_id,
            declared_frequency=None,
            observed_cadence_days=median_gap,
            suggested_frequency=suggested,
            confidence=confidence,
            file_count=file_count,
            misaligned=flag,
            reason="no_frequency_set",
        )

    # ISC-8 step 6: misalignment — only flag at high/medium confidence
    declared_norm = declared_frequency.strip().lower()
    is_misaligned = (
        declared_norm != suggested
        and confidence in {"high", "medium"}
    )
    reason = "misaligned" if is_misaligned else "aligned"

    return FrequencyObservation(
        evidence_id=evidence_id,
        declared_frequency=declared_norm,
        observed_cadence_days=median_gap,
        suggested_frequency=suggested,
        confidence=confidence,
        file_count=file_count,
        misaligned=is_misaligned,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# DB-bound entrypoint
# ---------------------------------------------------------------------------


async def compute_for_org(
    db: AsyncSession,
    org_id: UUID,
    *,
    now: Optional[datetime] = None,
) -> FrequencyHealthReport:
    """Compute the frequency-health report for one organization.

    Reads:
    - ``EvidenceTracking`` rows for the org (declared_frequency per evidence_id).
    - ``EvidenceFile.uploaded_at`` for the last 90 days, grouped by
      evidence_id, ``is_deleted=false``.

    Returns a :class:`FrequencyHealthReport`. PR 2 wraps this into a
    Pydantic response model.

    The ``now`` kwarg exists purely for deterministic testing — production
    callers omit it.
    """
    evaluated_at = now or datetime.utcnow()
    cutoff = evaluated_at - timedelta(days=EVALUATION_WINDOW_DAYS)

    # 1. Pull declared frequencies — one row per (org, evidence_id). The
    #    EvidenceTracking table is small per-org so a single SELECT is fine.
    tracking_result = await db.execute(
        select(
            EvidenceTracking.evidence_id,
            EvidenceTracking.frequency,
        ).where(EvidenceTracking.organization_id == org_id)
    )
    declared_by_evidence: Dict[str, Optional[str]] = {
        row.evidence_id: row.frequency for row in tracking_result.all()
    }

    # 2. Pull uploaded_at timestamps for files in window, grouped by evidence_id.
    #    Use a single query — N (files) is bounded by org's evidence volume in
    #    90d (typically <2000), so streaming Python-side aggregation is cheap.
    files_result = await db.execute(
        select(
            EvidenceFile.evidence_id,
            EvidenceFile.uploaded_at,
        ).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.is_deleted == False,  # noqa: E712 — SQLA needs ==
                EvidenceFile.uploaded_at >= cutoff,
            )
        )
    )
    uploads_by_evidence: Dict[str, List[datetime]] = {}
    for row in files_result.all():
        uploads_by_evidence.setdefault(row.evidence_id, []).append(row.uploaded_at)

    # 3. Build the union of evidence_ids: every tracked one + every one with
    #    files. (An evidence_id with files but no tracking row hits the
    #    'no_frequency_set' path — surfaces today's collecting_system=None
    #    anomaly correctly.)
    all_evidence_ids = set(declared_by_evidence.keys()) | set(uploads_by_evidence.keys())

    items: List[FrequencyObservation] = []
    for eid in sorted(all_evidence_ids):
        obs = _observe_one(
            evidence_id=eid,
            declared_frequency=declared_by_evidence.get(eid),
            uploaded_ats=uploads_by_evidence.get(eid, []),
        )
        items.append(obs)

    misaligned_count = sum(1 for i in items if i.misaligned)
    low_confidence_count = sum(
        1 for i in items
        if i.confidence == "low" and not i.misaligned
    )

    return FrequencyHealthReport(
        items=items,
        misaligned_count=misaligned_count,
        low_confidence_count=low_confidence_count,
        total_evidence_ids_evaluated=len(items),
        computed_at=evaluated_at,
    )


__all__ = [
    "EVALUATION_WINDOW_DAYS",
    "FrequencyObservation",
    "FrequencyHealthReport",
    "compute_for_org",
]
