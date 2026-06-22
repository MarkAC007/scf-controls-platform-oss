"""
KSI multi-axis scoring helpers — issue #549, Phase 1.

Pure functions only. No DB, no FastAPI dependencies. Each helper takes raw
counts and returns a scalar (0.0–1.0 float, integer band threshold, or None).

Design source: 2026-04-14 KSI Scoring Methodology Analysis §4.

Four axes, independently reported:
  - IC (Implementation Coverage)  — done-status fraction of in-scope, non-N/A
  - M  (Maturity)                  — avg L-level over implemented/monitored
  - EC (Evidence Coverage)         — controls-with-evidence fraction
  - EQ (Evidence Quality)          — assessment-weighted, relevance-scaled

Composite KSI Posture Score (KPS) is a weighted sum with null-axis
weight redistribution.
"""
from typing import Optional, Tuple


KPS_DEFAULT_WEIGHTS: Tuple[float, float, float, float] = (0.35, 0.20, 0.20, 0.25)
"""(w_IC, w_M, w_EC, w_EQ). Sum to 1.0. Council-revisable per-org in later phases."""

MATURITY_SAMPLE_SIZE_FLOOR = 3
EQ_LOW_AI_COVERAGE_THRESHOLD = 0.30


def compute_ic(
    monitored: int,
    implemented: int,
    ready_for_review: int,
    in_progress: int,
    scoped: int,
    not_applicable: int,
) -> Optional[float]:
    """Implementation Coverage axis.

    IC = (monitored + implemented + 0.5·ready_for_review + 0.25·in_progress)
       / (scoped − not_applicable)

    Returns None when the denominator is zero or negative (no in-scope,
    non-N/A controls — undefined coverage).
    """
    denom = scoped - not_applicable
    if denom <= 0:
        return None
    numerator = monitored + implemented + 0.5 * ready_for_review + 0.25 * in_progress
    return max(0.0, min(1.0, numerator / denom))


def compute_maturity(maturity_values: list[int]) -> Optional[float]:
    """Maturity axis — average L-level (0–5) with small-sample guard.

    Returns None when sample size < MATURITY_SAMPLE_SIZE_FLOOR to avoid
    noisy scores on sparsely-rated themes.
    """
    if len(maturity_values) < MATURITY_SAMPLE_SIZE_FLOOR:
        return None
    return sum(maturity_values) / len(maturity_values)


def compute_ec(
    controls_with_evidence: int,
    scoped: int,
    not_applicable: int,
) -> Optional[float]:
    """Evidence Coverage axis.

    EC = controls_with_evidence / (scoped − not_applicable)

    Returns None when denominator is zero or negative.
    """
    denom = scoped - not_applicable
    if denom <= 0:
        return None
    return max(0.0, min(1.0, controls_with_evidence / denom))


def compute_eq(
    sufficient: int,
    partial: int,
    insufficient: int,
    avg_relevance_0_100: Optional[float],
    insufficient_sample: int = 0,
) -> Optional[float]:
    """Evidence Quality axis.

    EQ = (1.0·sufficient + 0.5·partial + 0.5·insufficient_sample + 0.0·insufficient)
       / total_assessed
       × (avg_relevance_score / 100, treating null as 0.5 neutral)

    `insufficient_sample` (M1a windowed assessment) is a coverage gap —
    the files present scored fine but not enough artifact types were
    uploaded in the window. Scored like `partial` because content quality
    isn't the issue; sample breadth is.

    Pending/processing/unassessed files are excluded from the denominator;
    they are accounted for via compute_eq_warning.

    Returns None when no files have a terminal AI assessment status.
    """
    total_assessed = sufficient + partial + insufficient + insufficient_sample
    if total_assessed == 0:
        return None
    quality_fraction = (
        1.0 * sufficient
        + 0.5 * partial
        + 0.5 * insufficient_sample
        + 0.0 * insufficient
    ) / total_assessed
    relevance_factor = 0.5 if avg_relevance_0_100 is None else avg_relevance_0_100 / 100.0
    return max(0.0, min(1.0, quality_fraction * relevance_factor))


def compute_eq_warning(unassessed_count: int, total_files: int) -> Optional[str]:
    """Return 'low_ai_coverage' when unassessed ratio exceeds the threshold."""
    if total_files == 0:
        return None
    if unassessed_count / total_files > EQ_LOW_AI_COVERAGE_THRESHOLD:
        return "low_ai_coverage"
    return None


def compute_kps(
    ic: Optional[float],
    m_normalised: Optional[float],
    ec: Optional[float],
    eq: Optional[float],
    weights: Tuple[float, float, float, float] = KPS_DEFAULT_WEIGHTS,
) -> Optional[float]:
    """Composite KSI Posture Score with null-axis weight redistribution.

    M must be pre-normalised to 0.0–1.0 by the caller (m / 5.0). Weights for
    null axes are redistributed proportionally across populated axes so the
    composite still sums to 1.0 of its weight base.

    Returns None when all four axes are None.
    """
    axes = [ic, m_normalised, ec, eq]
    populated = [(value, weight) for value, weight in zip(axes, weights) if value is not None]
    if not populated:
        return None
    total_weight_present = sum(weight for _, weight in populated)
    return sum(value * (weight / total_weight_present) for value, weight in populated)


# Per-axis band thresholds — source doc §4.4
_IC_BANDS = (0.75, 0.40)
_M_BANDS = (3.0, 2.0)
_EC_BANDS = (0.70, 0.35)
_EQ_BANDS = (0.70, 0.40)
_KPS_BANDS = (0.70, 0.40)


def _band_three_tier(value: Optional[float], strong: float, moderate: float) -> str:
    """Strong / Moderate / Developing tiering. Null treated as Developing."""
    if value is None:
        return "Developing"
    if value >= strong:
        return "Strong"
    if value >= moderate:
        return "Moderate"
    return "Developing"


def band_for_axis(axis: str, value: Optional[float]) -> str:
    """Return the band label ('Strong'/'Moderate'/'Developing') for a given axis."""
    bands = {
        "IC": _IC_BANDS,
        "M": _M_BANDS,
        "EC": _EC_BANDS,
        "EQ": _EQ_BANDS,
        "KPS": _KPS_BANDS,
    }
    if axis not in bands:
        raise ValueError(f"Unknown axis '{axis}'. Expected one of {list(bands)}.")
    return _band_three_tier(value, *bands[axis])
