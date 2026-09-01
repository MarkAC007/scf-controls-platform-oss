"""Parsing and verdict derivation for AO-grounded evidence assessment (#881).

Two responsibilities, deliberately together because they are two halves of one
rule: what the model is allowed to say, and what the platform records as a
result of it.

**Parsing refuses rather than repairs.** The v1 parser answered a malformed
model response by manufacturing a verdict — ``status='error'`` with an invented
findings array — and returning it as if the model had produced it. That put a
fabricated row in a compliance record. Here, anything the contract does not
allow raises ``AssessmentParseError`` with the real reason, and the caller
writes an honest error row. There is no path that invents a verdict.

**The recorded status is derived, never quoted.** The model's own ``status``
field is advisory: it is one impression of a document, and it is free to
disagree with the per-objective designations it just produced. The status that
reaches the quality axis comes from counting those designations, so a file
whose objectives all show gaps cannot be recorded as sufficient because the
model felt positive about it.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence

from services.assessment_prompts import AO_DESIGNATIONS

logger = logging.getLogger(__name__)

# Statuses the platform can record for a file.
DERIVED_STATUSES = ("sufficient", "partial", "insufficient", "unassessable")

# What the model may put in its advisory `status` field. 'unassessable' is
# absent on purpose: that is a determination the pipeline makes about a file it
# could not read or could not evaluate, never a judgement the model is invited
# to make about content it did see.
MODEL_STATUSES = ("sufficient", "partial", "insufficient")

# Evidence supporting an assessment is normally expected to be no older than
# twelve months. Expressed in days so the comparison is a plain subtraction.
EVIDENCE_MAX_AGE_DAYS = 365


class AssessmentParseError(Exception):
    """The model's response cannot be read as a v2 assessment.

    ``retryable`` says whether asking again could produce a different answer.
    Malformed JSON and schema violations vary between samples, so they are
    worth another attempt. A response cut off at the token ceiling will be cut
    off at exactly the same place every time the same prompt is sent, so it is
    terminal — retrying it just spends the budget three times to reach the same
    place.
    """

    def __init__(self, reason: str, retryable: bool = True):
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


@dataclass
class ParsedAssessment:
    """A model response that satisfied the v2 contract."""
    summary: str
    relevance_score: Optional[float]
    # The model's own impression. None when it returned something off-contract;
    # nothing downstream depends on it, which is why an invalid value here is
    # dropped rather than fatal.
    model_status: Optional[str]
    ao_findings: List[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    evidence_effective_date: Optional[date] = None
    effective_date_source: Optional[str] = None

    @property
    def designations(self) -> List[str]:
        return [f["suggested_designation"] for f in self.ao_findings]

    @property
    def gap_count(self) -> int:
        return self.designations.count("gap_identified")

    @property
    def cannot_assess_count(self) -> int:
        return self.designations.count("cannot_assess")


def _strip_code_fence(content: str) -> str:
    """Remove a leading ```/```json fence and its closing counterpart."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    text = "\n".join(lines[1:])
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _clamp_score(raw: Any) -> Optional[float]:
    """Clamp relevance to 0-100, or null it when it is not a number.

    Null rather than zero: zero is a claim that the evidence is irrelevant,
    and a model that returned "high" made no such claim.
    """
    if raw is None:
        return None
    try:
        return max(0.0, min(100.0, float(raw)))
    except (TypeError, ValueError):
        logger.warning("Non-numeric relevance_score %r — storing null", raw)
        return None


def _parse_effective_date(raw: Any) -> tuple[Optional[date], Optional[str]]:
    """Read the model's effective date. Returns (date, complaint).

    A date the model got wrong in FORMAT is not a reason to throw away a whole
    set of objective findings, so this degrades to null and hands back a
    complaint for the caller to record as a finding. A date the model could not
    FIND is expected and silent — null is the contract's correct answer there.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not raw.strip():
        return None, f"Model returned a non-date value for evidence_effective_date ({raw!r}); recorded as unknown."
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date(), None
    except ValueError:
        return None, (
            f"Model returned '{raw}' as the evidence effective date, which is not "
            "a YYYY-MM-DD date; recorded as unknown."
        )


def _validate_ao_findings(
    raw: Any,
    prompted_ao_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    """Check the per-objective answers against the objectives actually asked.

    Exactly one answer per prompted objective, no more and no fewer. A missing
    objective is silent under-coverage; an extra one is an answer about
    something nobody asked, which would show up in the UI attributed to an AO
    the file was never assessed against.
    """
    if not isinstance(raw, list):
        raise AssessmentParseError(
            f"ao_findings must be an array, got {type(raw).__name__}"
        )

    expected = list(prompted_ao_ids)
    expected_set = set(expected)
    seen: List[str] = []
    cleaned: List[Dict[str, Any]] = []

    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise AssessmentParseError(
                f"ao_findings[{index}] must be an object, got {type(entry).__name__}"
            )
        ao_id = entry.get("ao_id")
        if not isinstance(ao_id, str) or not ao_id.strip():
            raise AssessmentParseError(f"ao_findings[{index}] has no usable ao_id")
        ao_id = ao_id.strip()
        if ao_id not in expected_set:
            raise AssessmentParseError(
                f"ao_findings[{index}] answers '{ao_id}', which was not among the "
                f"{len(expected)} objectives sent to the model"
            )
        if ao_id in seen:
            raise AssessmentParseError(f"ao_findings contains '{ao_id}' more than once")
        seen.append(ao_id)

        designation = entry.get("suggested_designation")
        if designation not in AO_DESIGNATIONS:
            raise AssessmentParseError(
                f"ao_findings for '{ao_id}' has designation {designation!r}, which is "
                f"not one of {', '.join(AO_DESIGNATIONS)}"
            )

        rationale = entry.get("rationale")
        suggestion = entry.get("suggestion")
        cleaned.append({
            "ao_id": ao_id,
            "suggested_designation": designation,
            "rationale": rationale if isinstance(rationale, str) else "",
            "suggestion": suggestion if isinstance(suggestion, str) else "",
        })

    missing = [ao_id for ao_id in expected if ao_id not in seen]
    if missing:
        shown = ", ".join(missing[:5])
        more = f" (and {len(missing) - 5} more)" if len(missing) > 5 else ""
        raise AssessmentParseError(
            f"ao_findings is missing {len(missing)} of {len(expected)} objectives: {shown}{more}"
        )

    # Answer order follows the order the objectives were asked in, so the UI
    # and the stored row read the same way regardless of what the model chose.
    by_id = {entry["ao_id"]: entry for entry in cleaned}
    return [by_id[ao_id] for ao_id in expected]


def parse_assessment_v2(
    content: str,
    stop_reason: Optional[str],
    prompted_ao_ids: Sequence[str],
) -> ParsedAssessment:
    """Parse a v2 assessment response, or raise AssessmentParseError.

    ``prompted_ao_ids`` is the exact list of objectives the prompt asked about;
    the answers are validated against it rather than against the catalog, so a
    catalog change between prompt and response cannot silently widen what the
    model is credited with having assessed.
    """
    if stop_reason == "max_tokens":
        raise AssessmentParseError(
            "Model output was cut off at the token ceiling before the assessment "
            "was complete — no verdict was returned.",
            retryable=False,
        )

    try:
        parsed = json.loads(_strip_code_fence(content))
    except json.JSONDecodeError as exc:
        raise AssessmentParseError(f"Model response was not valid JSON: {exc}") from exc

    if not isinstance(parsed, dict):
        raise AssessmentParseError(
            f"Model response was a JSON {type(parsed).__name__}, not an object"
        )

    ao_findings = _validate_ao_findings(parsed.get("ao_findings"), prompted_ao_ids)

    findings = parsed.get("findings")
    if not isinstance(findings, list):
        findings = []
    findings = [f for f in findings if isinstance(f, dict)]

    effective_date, date_complaint = _parse_effective_date(parsed.get("evidence_effective_date"))
    if date_complaint:
        findings.append({
            "category": "quality",
            "level": "info",
            "message": date_complaint,
        })

    source = parsed.get("effective_date_source")
    if effective_date is None:
        # A source without a date describes nothing.
        source = None
    elif not isinstance(source, str):
        source = None

    model_status = parsed.get("status")
    if model_status not in MODEL_STATUSES:
        logger.info(
            "Dropping off-contract advisory status %r — the derived status governs",
            model_status,
        )
        model_status = None

    summary = parsed.get("summary")

    return ParsedAssessment(
        summary=summary if isinstance(summary, str) else "",
        relevance_score=_clamp_score(parsed.get("relevance_score")),
        model_status=model_status,
        ao_findings=ao_findings,
        findings=findings,
        evidence_effective_date=effective_date,
        effective_date_source=source,
    )


def derive_assessment_status(
    designations: Sequence[str],
) -> tuple[Optional[str], Optional[str]]:
    """Derive the file's recorded status from its objective designations.

    Returns ``(status, unassessable_reason)``. ``status`` is None when there is
    no objective basis to derive from — the mapped controls publish no
    assessment objectives — and the caller falls back to the model's advisory
    status rather than inventing one.

    ``not_applicable`` objectives are excluded from the arithmetic entirely: an
    objective that cannot apply is not evidence of anything, in either
    direction, and counting it as a pass would let a file be recorded
    sufficient for objectives nobody claims it addresses.
    """
    if not designations:
        return None, None

    satisfied = designations.count("appears_satisfied")
    gaps = designations.count("gap_identified")
    cannot = designations.count("cannot_assess")
    not_applicable = designations.count("not_applicable")

    if not_applicable == len(designations):
        return "unassessable", (
            "Every assessment objective for the mapped controls was marked not "
            "applicable to this evidence, so there was nothing to evaluate it against."
        )

    # Nothing was shown and nothing was found missing: the file was simply not
    # the kind of artifact these objectives are answered with. That is a
    # statement about fit, not a failure, and it must not reach the quality
    # axis as one.
    if satisfied == 0 and gaps == 0:
        return "unassessable", (
            "No assessment objective could be evaluated from this file's content."
        )

    if gaps == 0 and cannot == 0:
        return "sufficient", None
    if gaps == 0:
        return "partial", None
    if satisfied > 0:
        return "partial", None
    return "insufficient", None


def status_coercion_finding(model_status: Optional[str], derived_status: str) -> Optional[Dict[str, Any]]:
    """Record it on the row when the derived status overrode the model's own.

    Not logging-only: a reviewer looking at a verdict marked partial when the
    model's summary reads positively deserves to see why in the same place as
    everything else they are reading.
    """
    if model_status is None or model_status == derived_status:
        return None
    return {
        "category": "quality",
        "level": "info",
        "message": (
            f"The model's overall impression was '{model_status}', but the recorded "
            f"status is '{derived_status}', derived from its own per-objective "
            "designations. The objective findings are authoritative."
        ),
    }


def exceeds_max_age(effective_date: Optional[date], as_of: Optional[date] = None) -> Optional[bool]:
    """Is this evidence older than the twelve months an assessment expects?

    None when there is no date to measure. Not False — False reads as "this
    evidence is current", which is a claim nobody made about a document whose
    date could not be determined.
    """
    if effective_date is None:
        return None
    reference = as_of or datetime.utcnow().date()
    return (reference - effective_date).days > EVIDENCE_MAX_AGE_DAYS
