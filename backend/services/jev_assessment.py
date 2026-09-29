"""Evidence assessment on Jev (TypeSafe System One) — shadow or primary engine.

The platform's evidence assessment asks a model, per SCF assessment objective,
which of four designations the uploaded document supports. On the LLM path
that is one long prompt and one long JSON answer with a rationale per
objective. Jev answers the *same question* differently: one ``choice``
question per objective, all evaluated in parallel against one ``state``, each
returning a designation, a probability over the four options and a confidence
figure. It is fast and calibrated, and it cannot write a sentence.

Three things follow, and they shape everything in this module:

**The designation vocabulary is the contract, not the prompt.** The choice
criteria below are the four ``AO_DESIGNATIONS`` with the same meanings the LLM
prompt gives them, so a Jev answer and an LLM answer are directly comparable
objective by objective. That comparison — not the verdict — is what shadow
mode exists to produce, and it is what decides whether Jev becomes the engine.

**Confidence replaces rationale.** In primary mode there is no written reason
to read, so the reviewer is given what the LLM never told them: how sure the
model was, per objective, and which objectives fell below the cutoff. A
low-confidence designation is kept (downgrading it to ``cannot_assess`` would
throw away the calibrated signal) and named in a file-level finding so the
human reviewer knows exactly where to read the document themselves.

**State is not instructions, but Jev does not know that.** TypeSafe's
jaggedness notes say Jev "does not treat [state] as hostile by default", so a
document that *says* it satisfies a control can steer the designation. Every
Jev verdict stays advisory and behind the same human review as the LLM's.

Which engine an organisation uses is a value in ``Organization.settings``
(``evidence_assessment_engine``): ``llm`` (default), ``jev_shadow`` (the LLM
decides; Jev runs afterwards and the two are compared), or ``jev`` (Jev
decides). ``resolve_engine`` is the one reader of that value.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from services import jev_client
from services.assessment_prompts import AO_DESIGNATIONS, MAX_ASSESSMENT_OBJECTIVES
from services.assessment_verdict import derive_assessment_status
from services.model_registry import cost_cents as model_cost_cents, resolve as resolve_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------

ENGINE_SETTING_KEY = "evidence_assessment_engine"
ENGINE_LLM = "llm"
ENGINE_JEV_SHADOW = "jev_shadow"
ENGINE_JEV = "jev"
ENGINES = (ENGINE_LLM, ENGINE_JEV_SHADOW, ENGINE_JEV)
DEFAULT_ENGINE = ENGINE_LLM

MODEL_ROLE = "evidence_assessment_jev"

#: The release of the question set, recorded where the LLM path records
#: ``PROMPT_VERSION``. Bump when the criteria wording, the state shape or the
#: relevance rubric changes — every cached Jev verdict is then re-assessed,
#: exactly as a prompt-template bump re-assesses the LLM's.
QUESTION_SET_VERSION = "jevqs-1.0.0"

#: Below this confidence a designation is still recorded but is named to the
#: reviewer as one the model was not sure about. TypeSafe's guidance is to
#: start conservative and tune per domain from observed results; the shadow
#: statistics on the Settings page are where that tuning is read from.
DEFAULT_CONFIDENCE_CUTOFF = 0.85
CONFIDENCE_CUTOFF_ENV = "JEV_CONFIDENCE_CUTOFF"

#: Characters of evidence text that go into ``state``. Jev's budget is 32k
#: tokens for the state plus the longest question; the extractor already caps
#: text at 50k characters (~12.5k tokens), so this is headroom, not a cut, on
#: today's path. It exists so a raised extractor cap cannot silently push the
#: request past the model's window.
DEFAULT_STATE_CHAR_BUDGET = 90_000
STATE_CHAR_BUDGET_ENV = "JEV_STATE_CHAR_BUDGET"

RELEVANCE_QUESTION_ID = "__relevance__"


def resolve_engine(settings: Any) -> str:
    """The engine an organisation's settings select, defaulting to the LLM.

    Tolerant on purpose: the settings JSON is written by a generic PATCH, and
    an unknown or malformed value must fall back to today's behaviour rather
    than stop assessments.
    """
    if not isinstance(settings, Mapping):
        return DEFAULT_ENGINE
    value = settings.get(ENGINE_SETTING_KEY)
    if isinstance(value, str) and value in ENGINES:
        return value
    return DEFAULT_ENGINE


def confidence_cutoff() -> float:
    raw = os.getenv(CONFIDENCE_CUTOFF_ENV, "").strip()
    if not raw:
        return DEFAULT_CONFIDENCE_CUTOFF
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number — using %s", CONFIDENCE_CUTOFF_ENV, raw, DEFAULT_CONFIDENCE_CUTOFF)
        return DEFAULT_CONFIDENCE_CUTOFF
    return min(1.0, max(0.0, value))


def state_char_budget() -> int:
    raw = os.getenv(STATE_CHAR_BUDGET_ENV, "").strip()
    if not raw:
        return DEFAULT_STATE_CHAR_BUDGET
    try:
        return max(1_000, int(raw))
    except ValueError:
        logger.warning("%s=%r is not an integer — using %s", STATE_CHAR_BUDGET_ENV, raw, DEFAULT_STATE_CHAR_BUDGET)
        return DEFAULT_STATE_CHAR_BUDGET


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------

#: The four designations, described for Jev in the same terms the LLM prompt
#: uses. The keys ARE the stored designation values — an answer is usable
#: without translation.
DESIGNATION_CRITERIA: Dict[str, str] = {
    "appears_satisfied": (
        "The evidence shows this objective being met — the document contains "
        "what the objective asks for."
    ),
    "gap_identified": (
        "The evidence is on topic but does not show this objective being met, "
        "or shows it only partly."
    ),
    "not_applicable": (
        "This objective cannot apply to this organisation or to this kind of "
        "artifact. Rare."
    ),
    "cannot_assess": (
        "This document is not the kind of evidence that could demonstrate this "
        "objective, or it does not contain enough to judge either way. Not a "
        "criticism of the evidence."
    ),
}
assert tuple(DESIGNATION_CRITERIA) == AO_DESIGNATIONS, "criteria keys must be the designation vocabulary"

#: Five-level relevance rubric. ``score`` returns the probability-weighted
#: position 0..4, mapped linearly onto the 0–100 ``relevance_score`` the LLM
#: path stores, so the two are on one axis.
RELEVANCE_LEVELS: List[str] = [
    "Unrelated to the mapped controls — a different subject entirely.",
    "Touches the subject but addresses almost none of the control requirements.",
    "Addresses some of the control requirements, with clear areas not covered.",
    "Addresses most of the control requirements with minor omissions.",
    "Directly and comprehensively addresses the mapped control requirements.",
]


def _ao_instructions(objective: Mapping[str, str], control_names: Mapping[str, str]) -> str:
    scf_id = objective.get("scf_id", "")
    control = control_names.get(scf_id, "")
    control_line = f"Control {scf_id}" + (f" ({control})" if control else "") + "."
    text = (
        f"{control_line} Assessment objective {objective.get('ao_id', '')}: "
        f"{objective.get('objective_text', '')}"
    )
    expected = objective.get("expected_results")
    if expected:
        text += f" Expected results: {expected}"
    text += (
        " Judging only from the evidence document in the state, which "
        "designation fits this objective?"
    )
    return text


def build_questions(control_context) -> Dict[str, Dict[str, Any]]:
    """One ``choice`` per prompted objective (keyed by AO id) plus relevance.

    Objectives arrive ao_id-ordered and already capped at
    ``MAX_ASSESSMENT_OBJECTIVES`` by the context assembler; the cap is the
    LLM path's and is kept here so the two engines answer the same list.
    """
    control_names = {
        c.get("scf_id", ""): c.get("control_name", "") for c in control_context.controls
    }
    questions: Dict[str, Dict[str, Any]] = {}
    for objective in list(control_context.objectives)[:MAX_ASSESSMENT_OBJECTIVES]:
        ao_id = str(objective["ao_id"]).strip()
        questions[ao_id] = {
            "type": "choice",
            "instructions": _ao_instructions(objective, control_names),
            "criteria": dict(DESIGNATION_CRITERIA),
        }
    questions[RELEVANCE_QUESTION_ID] = {
        "type": "score",
        "instructions": (
            "How well does the evidence document in the state address the "
            "mapped control requirements overall?"
        ),
        "criteria": list(RELEVANCE_LEVELS),
    }
    return questions


def build_state(
    control_context,
    extracted_text: str,
    filename: str,
    content_type: str,
    assessment_date: str = "",
    truncated: bool = False,
) -> tuple[Dict[str, Any], bool]:
    """The state object and whether the evidence text was cut to fit.

    An object rather than one string, as TypeSafe recommends, so each part has
    a name. The evidence text is the last field: when it has to be cut, the
    control context above it stays intact.
    """
    budget = state_char_budget()
    content = extracted_text or ""
    state_truncated = False
    if len(content) > budget:
        content = content[:budget] + "\n\n[... truncated to fit the model's context ...]"
        state_truncated = True
    notes: List[str] = []
    if truncated:
        notes.append("Only the head of the document was extracted; the rest is not shown.")
    if state_truncated:
        notes.append("The extracted text was cut further to fit the model's context.")
    state = {
        "evidence": {
            "evidence_id": control_context.evidence_id,
            "artifact_title": control_context.artifact_title,
            "artifact_description": control_context.artifact_description,
            "area_of_focus": control_context.area_of_focus,
            "file": filename,
            "content_type": content_type,
            "assessment_date": assessment_date or None,
        },
        "mapped_controls": [
            {
                "scf_id": c.get("scf_id", ""),
                "control_name": c.get("control_name", ""),
                "control_description": c.get("control_description", ""),
            }
            for c in control_context.controls
        ],
        "notes": notes,
        "document_text": content,
    }
    return state, state_truncated


def hash_request(state: Any, questions: Mapping[str, Any]) -> str:
    """SHA-256 over the whole request — the ``prompt_hash`` of this path."""
    blob = json.dumps({"state": state, "questions": questions}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------

class JevVerdictError(Exception):
    """The answers cannot be read as a verdict. Not retried by this module."""


@dataclass
class JevAssessmentVerdict:
    """What Jev said about one file, in the platform's terms."""
    # [{ao_id, suggested_designation, confidence, probabilities}]
    ao_findings: List[Dict[str, Any]]
    relevance_score: Optional[float]
    model_id: str
    request_hash: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    state_truncated: bool
    confidence_cutoff: float
    cost_cents: Optional[float] = None
    relevance_confidence: Optional[float] = None

    @property
    def designations(self) -> List[str]:
        return [f["suggested_designation"] for f in self.ao_findings]

    @property
    def gap_count(self) -> int:
        return self.designations.count("gap_identified")

    @property
    def cannot_assess_count(self) -> int:
        return self.designations.count("cannot_assess")

    @property
    def low_confidence(self) -> List[Dict[str, Any]]:
        return [
            f for f in self.ao_findings
            if f.get("confidence") is None or f["confidence"] < self.confidence_cutoff
        ]

    @property
    def low_confidence_count(self) -> int:
        return len(self.low_confidence)

    @property
    def status(self) -> str:
        derived, _reason = derive_assessment_status(self.designations)
        # No published objectives: nothing to derive from. The relevance score
        # is the only signal there is, and it is a poor one, so record the
        # cautious middle rather than invent confidence.
        return derived or "partial"

    @property
    def unassessable_reason(self) -> Optional[str]:
        _derived, reason = derive_assessment_status(self.designations)
        return reason


def parse_verdict(
    response: jev_client.JevResponse,
    prompted_ao_ids: Sequence[str],
    *,
    request_hash: str,
    state_truncated: bool,
    cutoff: float,
) -> JevAssessmentVerdict:
    """Read the answers against the objectives that were asked.

    Refuses rather than repairs, as ``parse_assessment_v2`` does: a
    designation outside the vocabulary or an answer for an objective nobody
    asked about is an error row, never a manufactured verdict.
    """
    findings: List[Dict[str, Any]] = []
    for ao_id in prompted_ao_ids:
        answer = response.answers.get(ao_id)
        if answer is None:
            raise JevVerdictError(f"no answer for objective {ao_id!r}")
        if answer.type != "choice":
            raise JevVerdictError(f"objective {ao_id!r} answered as {answer.type!r}, not a choice")
        designation = answer.value
        if designation not in AO_DESIGNATIONS:
            raise JevVerdictError(
                f"objective {ao_id!r} has designation {designation!r}, which is not one of "
                f"{', '.join(AO_DESIGNATIONS)}"
            )
        findings.append({
            "ao_id": ao_id,
            "suggested_designation": designation,
            "confidence": answer.confidence,
            "probabilities": {
                d: round(answer.probabilities.get(d, 0.0), 4) for d in AO_DESIGNATIONS
            },
        })
    unexpected = sorted(
        qid for qid in response.answers
        if qid not in prompted_ao_ids and qid != RELEVANCE_QUESTION_ID
    )
    if unexpected:
        raise JevVerdictError(
            f"answers for {len(unexpected)} objective(s) that were not asked: {', '.join(unexpected[:5])}"
        )

    relevance_score: Optional[float] = None
    relevance_confidence: Optional[float] = None
    relevance = response.answers.get(RELEVANCE_QUESTION_ID)
    if relevance is not None and relevance.type == "score":
        try:
            position = float(relevance.value)
            top = max(1, len(RELEVANCE_LEVELS) - 1)
            relevance_score = round(max(0.0, min(100.0, position / top * 100.0)), 2)
            relevance_confidence = relevance.confidence
        except (TypeError, ValueError):
            relevance_score = None

    model_id = response.model or resolve_model(MODEL_ROLE)
    return JevAssessmentVerdict(
        ao_findings=findings,
        relevance_score=relevance_score,
        model_id=model_id,
        request_hash=request_hash,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        latency_ms=response.latency_ms,
        state_truncated=state_truncated,
        confidence_cutoff=cutoff,
        cost_cents=model_cost_cents(model_id, response.input_tokens, response.output_tokens),
        relevance_confidence=relevance_confidence,
    )


def assess(
    control_context,
    extracted_text: str,
    filename: str,
    content_type: str,
    assessment_date: str = "",
    truncated: bool = False,
    *,
    cutoff: Optional[float] = None,
    ask: Optional[Callable[..., jev_client.JevResponse]] = None,
) -> JevAssessmentVerdict:
    """Build the request, call Jev, parse the answers.

    Raises ``jev_client.JevUnavailableError`` (no key — terminal),
    ``jev_client.JevCallError`` (the API failed), ``jev_client.JevResponseError``
    or ``JevVerdictError`` (the answers do not fit the contract).
    """
    cutoff = confidence_cutoff() if cutoff is None else cutoff
    state, state_truncated = build_state(
        control_context, extracted_text, filename, content_type, assessment_date, truncated,
    )
    questions = build_questions(control_context)
    request_hash = hash_request(state, questions)
    # Looked up at call time, not bound at import: the client is a module
    # attribute so a test (or a future transport swap) can replace it.
    call = ask if ask is not None else jev_client.ask
    response = call(resolve_model(MODEL_ROLE), state, questions)
    prompted = [q for q in questions if q != RELEVANCE_QUESTION_ID]
    return parse_verdict(
        response, prompted,
        request_hash=request_hash, state_truncated=state_truncated, cutoff=cutoff,
    )


# ---------------------------------------------------------------------------
# Comparison (shadow mode)
# ---------------------------------------------------------------------------

def compare(
    llm_ao_findings: Sequence[Mapping[str, Any]],
    llm_status: Optional[str],
    verdict: JevAssessmentVerdict,
) -> Dict[str, Any]:
    """Objective-by-objective agreement between the LLM verdict and Jev's.

    Two rates are reported because they answer different questions. The plain
    rate says how often the engines agree; the confident-subset rate says how
    often Jev is right *when it says it is sure*, which is the number the
    confidence-routing pattern is built on — above the cutoff Jev acts, below
    it a person reads the document.
    """
    llm_by_ao = {
        str(f.get("ao_id", "")).strip(): f.get("suggested_designation")
        for f in llm_ao_findings if isinstance(f, Mapping)
    }
    compared = agreed = confident_total = confident_agreed = 0
    disagreements: List[Dict[str, Any]] = []
    for jf in verdict.ao_findings:
        ao_id = jf["ao_id"]
        if ao_id not in llm_by_ao:
            continue
        compared += 1
        confidence = jf.get("confidence")
        is_confident = confidence is not None and confidence >= verdict.confidence_cutoff
        if is_confident:
            confident_total += 1
        if llm_by_ao[ao_id] == jf["suggested_designation"]:
            agreed += 1
            if is_confident:
                confident_agreed += 1
        else:
            disagreements.append({
                "ao_id": ao_id,
                "llm": llm_by_ao[ao_id],
                "jev": jf["suggested_designation"],
                "confidence": confidence,
            })
    jev_status = verdict.status
    return {
        "compared": compared,
        "agreed": agreed,
        "agreement_rate": round(agreed / compared, 4) if compared else None,
        "confident_total": confident_total,
        "confident_agreed": confident_agreed,
        "confident_agreement_rate": (
            round(confident_agreed / confident_total, 4) if confident_total else None
        ),
        "llm_status": llm_status,
        "jev_status": jev_status,
        "status_agrees": (llm_status == jev_status) if llm_status else None,
        "disagreements": disagreements,
    }


# ---------------------------------------------------------------------------
# Primary mode: the fields of a terminal verdict
# ---------------------------------------------------------------------------

def _format_probabilities(probabilities: Mapping[str, float]) -> str:
    return ", ".join(f"{d} {probabilities.get(d, 0.0):.2f}" for d in AO_DESIGNATIONS)


def terminal_ao_findings(verdict: JevAssessmentVerdict) -> List[Dict[str, Any]]:
    """AO findings in the stored shape, with confidence standing in for rationale.

    ``confidence`` and ``probabilities`` ride along in the JSONB — the response
    schema surfaces confidence, and a future reader of the row can see the
    whole distribution the designation was drawn from.
    """
    out: List[Dict[str, Any]] = []
    for f in verdict.ao_findings:
        confidence = f.get("confidence")
        sure = confidence is not None and confidence >= verdict.confidence_cutoff
        conf_text = f"{confidence:.2f}" if confidence is not None else "unknown"
        rationale = (
            f"Jev (System One) designation with confidence {conf_text} "
            f"({_format_probabilities(f.get('probabilities', {}))}). "
            "No written rationale: this engine returns a calibrated judgement, not text."
        )
        suggestion = "" if sure else (
            "Confidence is below the cutoff for this objective — read the document "
            "for it before confirming the designation."
        )
        out.append({
            "ao_id": f["ao_id"],
            "suggested_designation": f["suggested_designation"],
            "rationale": rationale,
            "suggestion": suggestion,
            "confidence": confidence,
            "probabilities": dict(f.get("probabilities", {})),
        })
    return out


def terminal_findings(verdict: JevAssessmentVerdict, control_context) -> List[Dict[str, Any]]:
    """File-level findings for a Jev-primary verdict."""
    findings: List[Dict[str, Any]] = [{
        "category": "quality",
        "level": "info",
        "message": (
            f"Assessed by Jev (System One, {verdict.model_id}). Designations carry a "
            "confidence figure instead of a written rationale, and no evidence "
            "effective date is extracted on this engine."
        ),
    }]
    if not verdict.ao_findings:
        # Same disclosure the LLM path makes: with no published objectives
        # there was no designation arithmetic, so the status is a stand-in,
        # not a per-objective verdict.
        findings.append({
            "category": "quality",
            "level": "info",
            "message": (
                "The mapped controls publish no SCF assessment objectives, so this "
                "status is a placeholder rather than a per-objective evaluation."
            ),
        })
    low = verdict.low_confidence
    if low:
        listed = ", ".join(
            f"{f['ao_id']} ({f['confidence']:.2f})" if f.get("confidence") is not None else f["ao_id"]
            for f in low[:10]
        )
        more = f" and {len(low) - 10} more" if len(low) > 10 else ""
        findings.append({
            "category": "quality",
            "level": "info",
            "message": (
                f"{len(low)} objective designation(s) fell below the {verdict.confidence_cutoff:.2f} "
                f"confidence cutoff: {listed}{more}. Read the document for these before confirming."
            ),
            "suggestion": "Review the low-confidence objectives against the document.",
        })
    if verdict.state_truncated:
        findings.append({
            "category": "completeness",
            "level": "info",
            "message": "The extracted text was cut to fit the model's context; the tail was not assessed.",
        })
    if getattr(control_context, "objectives_capped", False):
        findings.append({
            "category": "completeness",
            "level": "info",
            "message": (
                f"The mapped controls carry more than {MAX_ASSESSMENT_OBJECTIVES} "
                f"assessment objectives; the first {MAX_ASSESSMENT_OBJECTIVES} (by AO id) "
                "were assessed. Coverage of the remainder is unknown."
            ),
            "suggestion": (
                "Map this evidence item to a narrower set of controls so every "
                "objective can be assessed."
            ),
        })
    return findings


def terminal_summary(verdict: JevAssessmentVerdict) -> str:
    n = len(verdict.ao_findings)
    satisfied = verdict.designations.count("appears_satisfied")
    return (
        f"Jev assessed {n} objective(s): {satisfied} appear satisfied, "
        f"{verdict.gap_count} with gaps, {verdict.cannot_assess_count} could not be assessed; "
        f"{verdict.low_confidence_count} below the confidence cutoff."
    )
