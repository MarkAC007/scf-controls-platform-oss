"""CDM document-intent classification — provider seam and output validation.

The intent layer is a **filter, not a source of truth**. A model may narrow the
candidate set a control is scored against; it may never produce an edge the user
is shown as fact. Nothing in this module writes a ``cdm_mapping``, and nothing
downstream of it consumes a model output — proposals still come from FTS
retrieval with verified character offsets (epic #709 HTV-2).

Three properties are load-bearing:

* **Validation lives here, not in the providers.** A provider's job ends at
  "return the model's text and which model produced it". Parsing, intersecting
  the returned codes against the catalogue, dropping unknowns and truncating to
  the rank limit happen once, in one place, so a second provider cannot ship a
  second, subtly different notion of what a valid classification is.

* **An empty validated set is ``unclassified``, never ``classified``.** A
  document that genuinely matches no domain is indistinguishable from a model
  that failed to answer, and the cost asymmetry between those two mistakes is
  not close. The caller writes the status; this module reports the empty set
  honestly rather than inventing a domain to fill it.

* **The prompt is text-only.** Filenames and ground truth never reach a
  provider, matching the eval harness exactly — see
  :mod:`services.cdm_intent_prompt` for why.

Configuration is environment-driven and defaults to off:
``CDM_INTENT_PROVIDER`` (``disabled`` | ``claude`` | ``gpt`` | ``gemini``, default
``disabled``), ``CDM_INTENT_TIMEOUT_S``, ``CDM_INTENT_MAX_DOMAINS``.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Callable, Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.cdm_intent_prompt import PROMPT_VERSION, build_prompt

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 300.0

# The ``rank`` check constraint on cdm_document_intents is 1..3, so the
# configured limit is clamped rather than trusted: a larger value would produce
# rows the database refuses, which is a worse failure than a quieter one.
RANK_CEILING = 3

CLAUDE_MODEL = "claude-fable-5"
GPT_MODEL = "gpt-5.5"
# Newest non-preview Gemini in the live ListModels response (2026-08-17); the
# pro line stops at 3.1-preview, so the current flash is the stable pin. The
# eval harness's intent-gemini variant is the evidence for keeping or moving it.
GEMINI_MODEL = "gemini-3.7-flash"

# Claude Fable 5 always reasons; the request must not carry a ``thinking``
# block, and temperature/top_p/top_k are rejected outright. Only the token
# ceiling is ours to set.
CLAUDE_MAX_TOKENS = 16000

# The eval harness retries once with this suffix appended when the first reply
# does not parse. The measured accuracy includes that retry, so the runtime
# classifier reproduces it rather than reporting a lower success rate than the
# experiment did.
RETRY_SUFFIX = "\n\nReturn ONLY the JSON object, nothing else."


class IntentProviderError(RuntimeError):
    """Classification failed in a way that retrying will reproduce."""


class IntentProviderTransientError(IntentProviderError):
    """Classification failed for a reason that may not recur — retry is sane.

    Network faults, timeouts, rate limits and provider-side 5xx. Celery retries
    only on this type; everything else is treated as deterministic.
    """


@dataclass(frozen=True)
class IntentRequest:
    prompt: str
    timeout_s: float


@dataclass(frozen=True)
class IntentResponse:
    """A provider's raw reply. Deliberately unparsed — see module docstring."""

    text: str
    model_id: str


@dataclass(frozen=True)
class IntentClassification:
    """A validated classification, ready to persist.

    ``domains`` is ordered: position *i* is rank *i+1*. Rank is the model's own
    ordering, not a confidence score, and is never presented as one.
    """

    domains: tuple[str, ...]
    rationale: str | None
    provider: str
    model_id: str
    prompt_version: str
    truncated: bool


class IntentProvider(Protocol):
    name: str

    def classify(self, request: IntentRequest) -> IntentResponse: ...


class ClaudeIntentProvider:
    """Anthropic API via the official SDK."""

    name = "claude"

    def classify(self, request: IntentRequest) -> IntentResponse:
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not api_key:
            raise IntentProviderError("ANTHROPIC_API_KEY is not set; the claude provider needs it")

        client = anthropic.Anthropic(api_key=api_key, timeout=request.timeout_s, max_retries=0)
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                messages=[{"role": "user", "content": request.prompt}],
            )
        except (
            anthropic.APITimeoutError,
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ) as exc:
            raise IntentProviderTransientError(f"claude API call failed: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise IntentProviderError(f"claude API call failed with HTTP {exc.status_code}") from exc
        except anthropic.AnthropicError as exc:
            raise IntentProviderError(f"claude API call failed: {exc}") from exc

        # A refusal returns 200 with empty or partial content, so the stop
        # reason has to be read before the content blocks are touched.
        if message.stop_reason == "refusal":
            raise IntentProviderError("claude declined to classify the document")

        parts = [block.text for block in message.content if getattr(block, "type", None) == "text"]
        model_text = "".join(parts)
        if not model_text.strip():
            raise IntentProviderError("claude returned no text content")

        model_id = message.model.strip() if isinstance(message.model, str) and message.model.strip() else CLAUDE_MODEL
        return IntentResponse(text=model_text, model_id=model_id)


class GptIntentProvider:
    """OpenAI Chat Completions over plain HTTP.

    Mirrors ``scripts/cdm_eval/classify_intents.py`` so the runtime call and the
    measured call are the same call. There is no official first-party SDK in
    this project's dependency set for it, and adding one to reach a single
    endpoint is not a trade worth making.
    """

    name = "gpt"

    def classify(self, request: IntentRequest) -> IntentResponse:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise IntentProviderError("OPENAI_API_KEY is not set; the gpt provider needs it")

        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                "model": GPT_MODEL,
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": request.prompt}],
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            # False positive: the URL is the hardcoded literal
            # "https://api.openai.com/v1/chat/completions" set on the Request
            # object above. The rule fires because it cannot see through the
            # Request wrapper to prove the destination is constant.
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with urllib.request.urlopen(http_request, timeout=request.timeout_s) as response:
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                raise IntentProviderTransientError(f"gpt API call failed with HTTP {exc.code}") from exc
            raise IntentProviderError(f"gpt API call failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IntentProviderTransientError(f"gpt API call failed: {exc}") from exc

        try:
            raw: object = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise IntentProviderError(f"gpt API returned invalid JSON envelope: {exc}") from exc
        if not isinstance(raw, dict):
            raise IntentProviderError("gpt API returned a non-object JSON envelope")

        choices = raw.get("choices")
        if not isinstance(choices, list) or not choices:
            raise IntentProviderError("gpt API envelope has no choices")
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise IntentProviderError("gpt API envelope missing non-empty message content")

        model = raw.get("model")
        model_id = model.strip() if isinstance(model, str) and model.strip() else GPT_MODEL
        return IntentResponse(text=content, model_id=model_id)


class GeminiIntentProvider:
    """Google Gemini generateContent over plain HTTP.

    Mirrors ``GptIntentProvider`` rather than introducing an SDK dependency for
    one endpoint. The provider remains a dumb transport: Gemini's nested
    envelope is checked so failures are deterministic, but the domain JSON in
    the returned text is still parsed only once by ``validate_classification``.
    """

    name = "gemini"

    def classify(self, request: IntentRequest) -> IntentResponse:
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise IntentProviderError("GEMINI_API_KEY is not set; the gemini provider needs it")

        import urllib.error
        import urllib.request

        payload = json.dumps(
            {
                # The shared prompt already demands JSON, and the only retry is
                # the upstream shape retry in ``classify_document_text``.
                "contents": [{"parts": [{"text": request.prompt}]}],
            }
        ).encode("utf-8")
        http_request = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent",
            data=payload,
            headers={
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            # False positive: the URL is built only from the module constant
            # GEMINI_MODEL on the Request object above. The rule cannot prove
            # that this is still effectively a hardcoded destination.
            # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with urllib.request.urlopen(http_request, timeout=request.timeout_s) as response:
                raw_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or exc.code >= 500:
                raise IntentProviderTransientError(f"gemini API call failed with HTTP {exc.code}") from exc
            raise IntentProviderError(f"gemini API call failed with HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IntentProviderTransientError(f"gemini API call failed: {exc}") from exc

        try:
            raw: object = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise IntentProviderError(f"gemini API returned invalid JSON envelope: {exc}") from exc
        if not isinstance(raw, dict):
            raise IntentProviderError("gemini API returned a non-object JSON envelope")

        # A prompt block returns as promptFeedback, not as ordinary candidate
        # content, so this has to be checked before the candidate tree is read —
        # the analogue of the claude provider's stop_reason check. An absent or
        # oddly-shaped promptFeedback means no block was signalled; a genuine
        # block also empties candidates, which the next guard catches.
        prompt_feedback = raw.get("promptFeedback")
        block_reason = prompt_feedback.get("blockReason") if isinstance(prompt_feedback, dict) else None
        if block_reason:
            raise IntentProviderError(f"gemini blocked the prompt: {block_reason}")

        candidates = raw.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise IntentProviderError("gemini API envelope has no candidates")
        candidate = candidates[0]
        if not isinstance(candidate, dict):
            raise IntentProviderError("gemini API envelope first candidate is not an object")

        finish_reason = candidate.get("finishReason")
        # Anything other than a clean STOP (or an absent field) means the reply
        # was cut short — MAX_TOKENS truncation, SAFETY. A truncated JSON answer
        # would otherwise surface downstream as a misleading parse error, so the
        # real cause is named here instead.
        if isinstance(finish_reason, str) and finish_reason not in ("", "STOP"):
            raise IntentProviderError(
                f"gemini reply did not finish cleanly (finishReason={finish_reason!r})"
            )
        content = candidate.get("content")
        if not isinstance(content, dict):
            raise IntentProviderError("gemini API envelope first candidate missing content object")
        parts = content.get("parts")
        if not isinstance(parts, list):
            raise IntentProviderError("gemini API envelope first candidate content missing parts")

        text_parts = [part.get("text") for part in parts if isinstance(part, dict)]
        model_text = "".join(part for part in text_parts if isinstance(part, str))
        if not model_text.strip():
            raise IntentProviderError(f"gemini returned no text content (finishReason={finish_reason!r})")

        model = raw.get("modelVersion")
        model_id = model.strip() if isinstance(model, str) and model.strip() else GEMINI_MODEL
        return IntentResponse(text=model_text, model_id=model_id)


_PROVIDERS: dict[str, Callable[[], IntentProvider]] = {
    "claude": ClaudeIntentProvider,
    "gpt": GptIntentProvider,
    "gemini": GeminiIntentProvider,
}


def get_intent_provider(name: str | None = None) -> IntentProvider | None:
    """Resolve the configured provider, or ``None`` when classification is off.

    Defaults to ``disabled``: the feature ships dark and is switched on per
    environment, so a deploy never starts spending on a hosted API by accident.
    """
    resolved = (name or os.getenv("CDM_INTENT_PROVIDER", "disabled")).strip().lower()
    if resolved in {"", "disabled", "none", "off"}:
        return None
    factory = _PROVIDERS.get(resolved)
    if factory is None:
        logger.warning(
            "Unknown CDM_INTENT_PROVIDER %r; intent classification stays disabled", resolved
        )
        return None
    return factory()


def intent_classification_enabled() -> bool:
    """Whether a provider is configured, without constructing one.

    Used on the ingest path to decide whether to dispatch at all: queueing to
    ``cdm_intent`` when nothing is configured would pile up work no environment
    intends to do.
    """
    return get_intent_provider() is not None


def get_intent_timeout_seconds() -> float:
    raw = os.getenv("CDM_INTENT_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid CDM_INTENT_TIMEOUT_S %r; using default", raw)
        return DEFAULT_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_TIMEOUT_SECONDS


def get_max_domains() -> int:
    raw = os.getenv("CDM_INTENT_MAX_DOMAINS", "").strip()
    if not raw:
        return RANK_CEILING
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid CDM_INTENT_MAX_DOMAINS %r; using %d", raw, RANK_CEILING)
        return RANK_CEILING
    if value < 1:
        return 1
    return min(value, RANK_CEILING)


def load_catalog_domains(session: Session) -> dict[str, tuple[str, str]]:
    """Ordered ``identifier -> (name, principle)`` for the domain catalogue.

    Ordering matters: the catalogue is rendered into the prompt, and a stable
    order keeps the prompt stable across runs for an unchanged catalogue.
    """
    rows = session.execute(
        text(
            'SELECT identifier, name, COALESCE(principle, \'\') AS principle '
            'FROM scf_catalog_domains ORDER BY "order", identifier'
        )
    ).fetchall()
    return {row.identifier: (row.name, row.principle) for row in rows}


def _strip_code_fence(value: str) -> str:
    stripped = value.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    opener = lines[0].strip().casefold()
    if opener not in {"```", "```json"}:
        return stripped
    if len(lines) >= 2 and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


def _parse_json_object(value: str) -> dict[object, object]:
    candidate = _strip_code_fence(value)
    try:
        raw: object = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise IntentProviderError("model output did not contain a JSON object")
        try:
            raw = json.loads(candidate[start:end + 1])
        except json.JSONDecodeError as exc:
            raise IntentProviderError(f"model output was not valid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise IntentProviderError("model output JSON root must be an object")
    return raw


def validate_classification(
    model_text: str,
    valid_codes: set[str],
    *,
    max_domains: int = RANK_CEILING,
) -> tuple[tuple[str, ...], str | None]:
    """Turn a model reply into a ranked, catalogue-valid domain tuple.

    Unlike the eval harness, an unknown code is **dropped rather than raised**.
    The harness is a measurement loop where a hallucinated code is a result
    worth failing on; here it is an ordinary event on the ingest path, and
    discarding a bad code degrades to a narrower filter instead of losing the
    whole classification. Order is preserved, so rank follows the model's.
    """
    raw = _parse_json_object(model_text)
    primary_domains = raw.get("primary_domains")
    rationale = raw.get("rationale")

    if not isinstance(primary_domains, list):
        raise IntentProviderError("model output primary_domains must be a list")

    codes: list[str] = []
    seen: set[str] = set()
    for value in primary_domains:
        if not isinstance(value, str) or not value.strip():
            continue
        code = value.strip().upper()
        if code in seen or code not in valid_codes:
            continue
        seen.add(code)
        codes.append(code)

    rationale_text = rationale.strip() if isinstance(rationale, str) and rationale.strip() else None
    return tuple(codes[:max_domains]), rationale_text


def classify_document_text(
    document_text: str,
    domains: dict[str, tuple[str, str]],
    *,
    provider: IntentProvider,
    timeout_s: float | None = None,
    max_domains: int | None = None,
) -> IntentClassification:
    """Classify one document's extracted text against the domain catalogue.

    Raises :class:`IntentProviderError` (or the transient subclass) on failure;
    an empty ``domains`` tuple in the returned classification is a *success*
    meaning "authoritative for nothing in the catalogue", which the caller
    records as ``unclassified``.
    """
    resolved_timeout = timeout_s if timeout_s is not None else get_intent_timeout_seconds()
    resolved_max = max_domains if max_domains is not None else get_max_domains()
    valid_codes = set(domains)

    prompt, truncated = build_prompt(domains, document_text)
    attempts = (prompt, prompt + RETRY_SUFFIX)
    last_error: IntentProviderError | None = None
    for attempt, attempt_prompt in enumerate(attempts, start=1):
        request = IntentRequest(prompt=attempt_prompt, timeout_s=resolved_timeout)
        try:
            response = provider.classify(request)
            codes, rationale = validate_classification(
                response.text, valid_codes, max_domains=resolved_max
            )
        except IntentProviderTransientError:
            # Transient faults are the Celery retry's job, not the reply-shape
            # retry's: re-asking immediately would just spend the same outage.
            raise
        except IntentProviderError as exc:
            last_error = exc
            if attempt == len(attempts):
                break
            continue
        return IntentClassification(
            domains=codes,
            rationale=rationale,
            provider=provider.name,
            model_id=response.model_id,
            prompt_version=PROMPT_VERSION,
            truncated=truncated,
        )

    raise IntentProviderError(f"classification failed after retry: {last_error}")
