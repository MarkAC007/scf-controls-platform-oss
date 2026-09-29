"""The one HTTP client for TypeSafe's System One API (Jev).

Jev is a *judgment* model, not a text generator: you hand it a ``state`` and a
map of typed questions (``choice``, ``score``, ``noul``) and get back, per
question, an answer with a probability distribution and a confidence figure.
Every question is evaluated in parallel against the same state, so sixty
per-objective questions are one request, not sixty.

Why this is its own module rather than a few lines in the engine: the request
and answer shapes have two traps that cost real runs before they were written
down, and the client is the one place they are handled.

* ``choice`` criteria is a **map** of option → description; ``score`` criteria
  is an **ordered list** of level descriptions. A map where a list is expected
  is an HTTP 422 with a precise path; a malformed top level is an HTTP 400 with
  a useless "Invalid request".
* Answers are keyed by your own question ids, but the value field is named
  after the question **type**: ``answers.<id>.choice`` / ``.score`` / ``.noul``.
  Reading ``.value`` returns nothing while ``confidence`` still populates, which
  looks exactly like a model failure and is not one.

Credential resolution goes through ``services.secrets.get_secret`` like every
other tier-3 key, so the key can come from the database (Settings →
Integrations), a ``TYPESAFE_API_KEY_FILE`` or the environment, and rotation
takes effect without a restart. Jev does not treat the state as hostile
(TypeSafe's own jaggedness notes say so), so a caller must never put anything
in ``state`` it would not put in front of the model as instructions — evidence
text is exactly that, which is why the engine keeps Jev advisory and behind
human review.

Reference: https://docs.typesafe.ai/api
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional

from services.secrets import get_secret, is_placeholder

logger = logging.getLogger(__name__)

# The environment-variable NAME the key is read under. Same identifier
# discipline as `services.llm_client.KEY_ENV`: the value is a name, never a
# credential, and the name must not match CodeQL's sensitive-data families.
KEY_ENV = "TYPESAFE_API_KEY"

DEFAULT_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
ENDPOINT_ENV = "TYPESAFE_API_URL"

#: Seconds for one request. Jev answers in well under a second for typical
#: state; a long evidence document with sixty questions is still a few
#: seconds. Anything past this is the service, not the question.
REQUEST_TIMEOUT_SECONDS = 60.0

#: Retries on 429 (rate limited) and 529 (overloaded), with exponential backoff
#: as the API docs ask for. Other failures are not retried here — the calling
#: Celery task owns retry policy for those.
MAX_RETRIES = 2
BACKOFF_BASE_SECONDS = 1.0
RETRYABLE_STATUSES = frozenset({429, 529})


class JevUnavailableError(Exception):
    """Jev cannot be called at all from this process (no key, no client).

    Identical on every attempt, so callers should record it and not retry.
    """


class JevCallError(Exception):
    """The request was made and failed. Carries what the API said."""

    def __init__(self, message: str, status_code: Optional[int] = None, body: str = ""):
        self.status_code = status_code
        self.body = body[:2000]
        super().__init__(message)


class JevResponseError(Exception):
    """The API answered 200 with a body that does not match the contract."""


@dataclass
class JevAnswer:
    """One question's answer, normalised across the three types.

    ``value`` is the type-specific field lifted out: the chosen option key for
    ``choice``, the probability-weighted position for ``score``, the 0–1
    probability for ``noul``. ``probabilities`` and ``confidence`` are present
    for choice and score; noul has neither (its value *is* the probability).
    """
    question_id: str
    type: str
    value: Any
    confidence: Optional[float] = None
    probabilities: Dict[str, float] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class JevResponse:
    model: str
    answers: Dict[str, JevAnswer]
    input_tokens: int
    output_tokens: int
    latency_ms: int


def get_typesafe_key() -> str:
    """The TypeSafe key, or JevUnavailableError when there is no usable one.

    A shipped stand-in (``changeme…``) is "no key" here just as it is for
    ``integration_enabled`` and the Settings card: sending it as a bearer
    token would turn a configuration gap into a 401 that the task treats as a
    transient call failure and retries.
    """
    key = get_secret(KEY_ENV)
    if not key or is_placeholder(key):
        raise JevUnavailableError(
            f"{KEY_ENV} not set — Jev assessment cannot run in this worker"
        )
    return key


def endpoint() -> str:
    return os.getenv(ENDPOINT_ENV, "").strip() or DEFAULT_ENDPOINT


def _lift_answer(question_id: str, question_type: str, raw: Any) -> JevAnswer:
    if not isinstance(raw, dict):
        raise JevResponseError(
            f"answers[{question_id!r}] is a {type(raw).__name__}, not an object"
        )
    if question_type not in raw:
        raise JevResponseError(
            f"answers[{question_id!r}] has no {question_type!r} field "
            f"(keys: {', '.join(sorted(raw))})"
        )
    probabilities = raw.get("probabilities") or {}
    if not isinstance(probabilities, dict):
        probabilities = {}
    confidence = raw.get("confidence")
    try:
        confidence = float(confidence) if confidence is not None else None
    except (TypeError, ValueError):
        confidence = None
    return JevAnswer(
        question_id=question_id,
        type=question_type,
        value=raw[question_type],
        confidence=confidence,
        probabilities={str(k): float(v) for k, v in probabilities.items()
                       if isinstance(v, (int, float))},
        raw=raw,
    )


def parse_response(payload: Any, questions: Mapping[str, Mapping[str, Any]], latency_ms: int) -> JevResponse:
    """Read a 200 body against the questions that were asked.

    Every asked question must be answered and nothing else may be — an answer
    to a question nobody asked would be credited to an objective the model
    never evaluated.
    """
    if not isinstance(payload, dict):
        raise JevResponseError(f"response body is a {type(payload).__name__}, not an object")
    answers_raw = payload.get("answers")
    if not isinstance(answers_raw, dict):
        raise JevResponseError("response has no 'answers' object")

    answers: Dict[str, JevAnswer] = {}
    for qid, question in questions.items():
        if qid not in answers_raw:
            raise JevResponseError(f"response is missing an answer for question {qid!r}")
        answers[qid] = _lift_answer(qid, str(question.get("type", "")), answers_raw[qid])
    extra = sorted(set(answers_raw) - set(questions))
    if extra:
        raise JevResponseError(
            f"response answers {len(extra)} question(s) that were not asked: {', '.join(extra[:5])}"
        )

    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return JevResponse(
        model=str(payload.get("model") or ""),
        answers=answers,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        latency_ms=latency_ms,
    )


def ask(
    model: str,
    state: Any,
    questions: Mapping[str, Mapping[str, Any]],
    *,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    sleep=time.sleep,
) -> JevResponse:
    """POST one System One request and return the parsed answers.

    Raises ``JevUnavailableError`` before any network call when there is no
    key or no HTTP client; ``JevCallError`` when the API refuses or fails
    after the 429/529 retries; ``JevResponseError`` when a 200 body does not
    match the questions asked.
    """
    if not questions:
        raise ValueError("ask() needs at least one question")
    key = get_typesafe_key()
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - httpx is a hard dependency
        raise JevUnavailableError("httpx not installed — Jev assessment cannot run") from exc

    body = {"model": model, "state": state, "questions": dict(questions)}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = endpoint()

    attempt = 0
    while True:
        started = time.monotonic()
        try:
            response = httpx.post(url, json=body, headers=headers, timeout=timeout)
        except Exception as exc:  # transport-level: DNS, TLS, timeout
            raise JevCallError(f"Jev request failed: {type(exc).__name__}: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)

        if response.status_code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
            delay = BACKOFF_BASE_SECONDS * (2 ** attempt)
            logger.warning(
                "Jev answered HTTP %s — retrying in %.1fs (attempt %d of %d)",
                response.status_code, delay, attempt + 1, MAX_RETRIES,
            )
            sleep(delay)
            attempt += 1
            continue

        if response.status_code != 200:
            raise JevCallError(
                f"Jev answered HTTP {response.status_code}",
                status_code=response.status_code,
                body=response.text or "",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            raise JevResponseError(f"Jev answered 200 with a non-JSON body: {exc}") from exc
        return parse_response(payload, questions, latency_ms)
