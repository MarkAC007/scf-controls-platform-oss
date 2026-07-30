"""CDM v2 — relevance scoring composed from observable evidence (epic #709).

v1's central failure was a number nobody could interrogate: ``relevance_score``
was ``1.0 - 0.05 * rank_index``, i.e. list position. The top hit always scored
exactly 1.0 whether it was a perfect match or noise, and with ``top_k=10`` and
``threshold=0.5`` the minimum possible score was 0.55 — so the threshold was
mathematically unreachable and there was no quality gate at all.

v2 composes the score from three quantities a reviewer can check:

    score = w1 · ts_rank            (absolute, normalised inside Postgres)
          + w2 · objective_coverage (fraction of the control's objectives matched)
          + w3 · term_overlap       (control terms present / control terms queried)

Two properties are deliberate and load-bearing:

* **The components are persisted, and so are the weights.** Persisting
  components alone is not enough. Weights are env-configurable, so a later
  tuning change would leave every historical score un-recomputable from its own
  parts — a reviewer recomputing the sum would get a different number with no
  way to tell whether the score or the components were wrong. That is exactly
  the position this epic is being written from.

* **Weights are normalised to sum to 1.0**, so the score is bounded [0,1]
  regardless of how they are configured. A deployment that sets all three to 1
  gets a mean, not a 3.0.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_TS_RANK_WEIGHT = 0.5
DEFAULT_OBJECTIVE_COVERAGE_WEIGHT = 0.3
DEFAULT_TERM_OVERLAP_WEIGHT = 0.2

# v1's default was 0.5 against a scale whose floor was 0.55 — unreachable by
# construction. On the composed scale a threshold is a real gate, so the
# default is set where a hit with weak rank AND weak coverage falls below it.
DEFAULT_SCORE_THRESHOLD = 0.15
DEFAULT_TOP_K = 25

# #712 precision filters. The absolute threshold above is a noise floor, not a
# relevance bar — on the dev corpus every scoped control in a claimed domain
# averaged ~16 above-floor proposals, which is review-queue flooding. These two
# are per-control relative measures precisely so they degrade gracefully on
# low-signal documents where a raised absolute floor would silently suppress
# true matches.
DEFAULT_MAX_PROPOSALS_PER_CONTROL = 3
DEFAULT_RELATIVE_SCORE_CUTOFF = 0.6


@dataclass(frozen=True)
class ScoreWeights:
    """Weights used for one scoring run, normalised to sum to 1.0."""

    ts_rank: float
    objective_coverage: float
    term_overlap: float

    @classmethod
    def from_env(cls) -> "ScoreWeights":
        return cls.normalised(
            _get_float_env("CDM_SCORE_WEIGHT_TS_RANK", DEFAULT_TS_RANK_WEIGHT),
            _get_float_env("CDM_SCORE_WEIGHT_OBJECTIVE_COVERAGE", DEFAULT_OBJECTIVE_COVERAGE_WEIGHT),
            _get_float_env("CDM_SCORE_WEIGHT_TERM_OVERLAP", DEFAULT_TERM_OVERLAP_WEIGHT),
        )

    @classmethod
    def normalised(cls, ts_rank: float, objective_coverage: float, term_overlap: float) -> "ScoreWeights":
        values = [max(0.0, ts_rank), max(0.0, objective_coverage), max(0.0, term_overlap)]
        total = sum(values)
        if total <= 0:
            logger.warning("CDM score weights sum to zero; falling back to defaults")
            values = [
                DEFAULT_TS_RANK_WEIGHT,
                DEFAULT_OBJECTIVE_COVERAGE_WEIGHT,
                DEFAULT_TERM_OVERLAP_WEIGHT,
            ]
            total = sum(values)
        return cls(
            ts_rank=values[0] / total,
            objective_coverage=values[1] / total,
            term_overlap=values[2] / total,
        )

    def as_dict(self) -> dict[str, float]:
        """Shape persisted to ``cdm_mappings.score_weights``."""
        return {
            "ts_rank": round(self.ts_rank, 6),
            "objective_coverage": round(self.objective_coverage, 6),
            "term_overlap": round(self.term_overlap, 6),
        }


@dataclass(frozen=True)
class ScoredComponents:
    """The three components plus the composed score they produce."""

    ts_rank: float
    objective_coverage: float
    term_overlap: float
    score: float


def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%r; using %s", name, raw, default)
        return default


def _clamp_unit(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def compose_score(
    *,
    ts_rank: float,
    objective_coverage: float,
    term_overlap: float,
    weights: ScoreWeights,
) -> ScoredComponents:
    """Combine the three components into a bounded, reproducible score.

    ``ts_rank`` is expected already normalised to [0,1) by Postgres
    (``ts_rank_cd(..., 32)`` applies ``rank/(rank+1)``). It is clamped here as
    a guard, not as the normalisation — normalising in Python against the
    result set is exactly the defect that made v1's score meaningless.
    """
    ts_component = _clamp_unit(ts_rank)
    coverage_component = _clamp_unit(objective_coverage)
    overlap_component = _clamp_unit(term_overlap)

    score = (
        weights.ts_rank * ts_component
        + weights.objective_coverage * coverage_component
        + weights.term_overlap * overlap_component
    )

    return ScoredComponents(
        ts_rank=ts_component,
        objective_coverage=coverage_component,
        term_overlap=overlap_component,
        score=_clamp_unit(score),
    )


def recompute_score(components: ScoredComponents, weights: ScoreWeights) -> float:
    """Recompute a stored score from its stored parts.

    Exists so the claim "the score is interrogable" is testable rather than
    asserted: a reviewer (or a test) can take the persisted components and
    weights and arrive at the persisted score.
    """
    return _clamp_unit(
        weights.ts_rank * components.ts_rank
        + weights.objective_coverage * components.objective_coverage
        + weights.term_overlap * components.term_overlap
    )


def get_score_threshold() -> float:
    return _get_float_env("CDM_MAPPING_SCORE_THRESHOLD", DEFAULT_SCORE_THRESHOLD)


def get_top_k() -> int:
    raw = os.getenv("CDM_MAPPING_TOP_K")
    if raw is None:
        return DEFAULT_TOP_K
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TOP_K
    if value < 1:
        return DEFAULT_TOP_K
    return min(value, 200)


def get_max_proposals_per_control() -> int:
    """Per-control proposal cap (#712). Bounds reviewer effort per control."""
    raw = os.getenv("CDM_MAX_PROPOSALS_PER_CONTROL")
    if raw is None:
        return DEFAULT_MAX_PROPOSALS_PER_CONTROL
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_PROPOSALS_PER_CONTROL
    if value < 1:
        return DEFAULT_MAX_PROPOSALS_PER_CONTROL
    return value


def get_relative_score_cutoff() -> float:
    """Within-control relative cutoff (#712), as a fraction of the best hit.

    Clamped to [0, 1]: a fraction outside the unit interval either keeps
    everything (< 0) or drops the best hit itself (> 1), and the second is a
    misconfiguration this filter must never express — the best excerpt per
    control always survives.
    """
    value = _get_float_env("CDM_RELATIVE_SCORE_CUTOFF", DEFAULT_RELATIVE_SCORE_CUTOFF)
    if not 0.0 <= value <= 1.0:
        logger.warning(
            "CDM_RELATIVE_SCORE_CUTOFF=%r outside [0, 1]; using %s",
            value,
            DEFAULT_RELATIVE_SCORE_CUTOFF,
        )
        return DEFAULT_RELATIVE_SCORE_CUTOFF
    return value
