"""
Celery tasks for AI evidence assessment.

Runs evidence content assessment against mapped control requirements
using Claude LLM. Executes in Celery workers (separate from web server)
to avoid blocking the uvicorn event loop.

Follows conventions from tasks_research.py and tasks_vendor_assessment.py.
"""
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, NamedTuple, Optional

from celery import shared_task
from sqlalchemy import create_engine, select, and_, text
from sqlalchemy.orm import sessionmaker

from services.assessment_prompts import (
    assemble_control_context_sync,
    build_assessment_prompt,
    hash_prompt,
    ASSESSMENT_OUTPUT_SCHEMA,
    MAX_ASSESSMENT_OBJECTIVES,
    PROMPT_VERSION,
)
from services.assessment_verdict import (
    AssessmentParseError,
    derive_assessment_status,
    exceeds_max_age,
    parse_assessment_v2,
    status_coercion_finding,
)
from services.text_extraction_service import (
    extract_text_from_bytes,
    download_evidence_bytes,
)
from services.anthropic_response import extract_text
from services.model_registry import cost_cents as model_cost_cents, resolve as resolve_model
from services import jev_assessment
from services import jev_client

from services.llm_client import build_anthropic_client
from services.secrets import get_secret

from db_url import get_sync_database_url

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
# The comment that used to sit here said "mirrored from ai_assessment_service"
# — and it was, including the retired pin and the rate card. Mirroring is the
# defect; both now come from services/model_registry (#782). That mirror has
# since been removed outright (#881): this module is the only per-file
# assessment path.

MODEL_ROLE = "evidence_assessment"
# A v2 response carries one rationale per assessment objective, up to
# MAX_ASSESSMENT_OBJECTIVES of them. 2048 tokens was sized for a single
# file-level opinion and would truncate an AO-grounded answer partway through
# the objective list — which the parser correctly refuses, turning every large
# control set into an error instead of a verdict.
# Opus 5 runs adaptive thinking by default and its thinking tokens are spent
# INSIDE max_tokens. 8192 fit a non-thinking Sonnet verdict; a thinking model
# burned most of it reasoning and hit the ceiling before the JSON verdict
# (v0.26.0 follow-up incident, 2026-09-03). Billed per token used, not per
# ceiling — the headroom is free on short runs. Ceilings this size require a
# streaming request (the SDK refuses long non-streaming calls).
MAX_OUTPUT_TOKENS = 32000

# Kept as a module constant rather than read back off `self.max_retries`:
# `retry_kwargs` is applied by the autoretry wrapper at retry time and does
# NOT overwrite Task.max_retries (which stays at Celery's default of 3), so
# reading it back would make the task think it had one attempt more than it
# actually gets and skip writing the terminal row on the real last attempt.
MAX_RETRIES = 2

# Verdicts worth reusing on an unchanged file+context. Deliberately excludes
# 'unassessable': that outcome costs no LLM call to recompute, and re-running
# it is how an evidence file picks up a later improvement to the extractor
# (OCR, xlsx support) instead of being frozen on the day extraction failed.
CACHEABLE_STATUSES = frozenset({"sufficient", "partial", "insufficient"})


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------

class LLMCallError(Exception):
    """A retryable failure calling the assessment model.

    Raised (not swallowed) so the task's autoretry_for actually fires. Carries
    the originating exception's class and message so the stored assessment row
    can say *what* broke rather than "LLM call failed".
    """

    def __init__(self, cause: BaseException):
        self.cause_class = type(cause).__name__
        self.cause_message = str(cause)[:500]
        super().__init__(f"{self.cause_class}: {self.cause_message}")


class LLMUnavailableError(Exception):
    """A non-retryable failure: the model cannot be called at all.

    Missing SDK or missing API key. Backing off and trying again cannot change
    either, so this is caught in the task and stored as a terminal error rather
    than being allowed to burn the retry budget.
    """


# ---------------------------------------------------------------------------
# Cache gate
# ---------------------------------------------------------------------------

def prompt_version_for_engine(engine: str) -> str:
    """The prompt release a verdict must carry to be reusable under *engine*.

    Jev verdicts name the question-set release, LLM verdicts the template
    release; comparing against the chosen engine's is what makes an engine
    switch re-assess instead of serving the other engine's verdict. Used by the
    worker gate and the trigger endpoint's gate alike — the two must agree.
    """
    return (
        jev_assessment.QUESTION_SET_VERSION
        if engine == jev_assessment.ENGINE_JEV else PROMPT_VERSION
    )


def is_cache_hit(
    status: Optional[str],
    prompt_hash: Optional[str],
    stored_context_hash: Optional[str],
    stored_prompt_version: Optional[str],
    file_sha256: Optional[str],
    current_context_hash: str,
    stored_file_sha256: Optional[str],
    current_prompt_version: str = PROMPT_VERSION,
) -> bool:
    """True when a stored verdict can be reused instead of re-calling the model.

    Reusable means: the file was actually assessed before (prompt_hash set),
    it reached a cacheable verdict, and none of the three inputs that can
    change the answer has moved — the bytes assessed, the assembled control
    context, and the release of the prompt template that framed them.

    The file hash is compared, not merely counted. The immutability of an
    evidence_files row made the old presence check *true*, but it made it true
    by an argument held in a comment somewhere else; the row now records which
    bytes each verdict was computed over, so this can assert it directly. A row
    with no recorded hash is a miss: reusing a verdict without being able to
    say what it was about is the thing this check exists to prevent, and the
    cost of getting it wrong is one extra assessment.
    """
    if status not in CACHEABLE_STATUSES:
        return False
    if not prompt_hash:
        return False
    if not file_sha256 or not stored_file_sha256:
        return False
    if stored_file_sha256 != file_sha256:
        return False
    if stored_context_hash != current_context_hash:
        return False
    if stored_prompt_version != current_prompt_version:
        return False
    return True

# ---------------------------------------------------------------------------
# Sync DB session (psycopg2 pattern from tasks_research.py)
# ---------------------------------------------------------------------------

_SYNC_DATABASE_URL = (
    get_sync_database_url("postgresql+asyncpg://cg:cg@localhost:5432/cg_scf")
)

_sync_engine = None
SyncSession = None


def _get_sync_session():
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
# LLM call (sync — runs naturally in Celery worker)
# ---------------------------------------------------------------------------

def _call_llm(system_prompt: str, user_prompt: str) -> dict:
    """Call Claude API for evidence assessment (sync).

    Raises rather than returning None. Returning None made every API failure
    look identical to a successful "we decided not to call it", and let the
    caller write status='error' and return normally — which is why the task's
    retry configuration never fired once.
    """
    try:
        import anthropic
    except ImportError:
        raise LLMUnavailableError(
            "anthropic package not installed — AI assessment cannot run in this worker"
        )

    if not get_secret("ANTHROPIC_API_KEY"):
        raise LLMUnavailableError(
            "ANTHROPIC_API_KEY not set — AI assessment cannot run in this worker"
        )

    try:
        client = build_anthropic_client()
        with client.messages.stream(
            model=resolve_model(MODEL_ROLE),
            max_tokens=MAX_OUTPUT_TOKENS,
            system=[{
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": user_prompt}],
        ) as stream:
            message = stream.get_final_message()
        return {
            "content": extract_text(message),
            "model": message.model,
            "input_tokens": message.usage.input_tokens,
            "output_tokens": message.usage.output_tokens,
            # Why the model stopped. 'max_tokens' means the answer was cut off
            # mid-JSON, which the parser has to be able to tell apart from a
            # model that simply wrote malformed output.
            "stop_reason": getattr(message, "stop_reason", None),
        }
    except Exception as exc:
        logger.error("Claude API call failed: %s", exc, exc_info=True)
        raise LLMCallError(exc) from exc


# ---------------------------------------------------------------------------
# Celery task: single evidence file assessment
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="tasks_assessment.assess_evidence_task",
    # 600/540 matches the platform's other model-calling tasks (recipe, vendor).
    # 360/300 predates thinking models: an Opus 5 call that reasons over a dense
    # document can legitimately run past 5 minutes without being stuck.
    time_limit=600,
    soft_time_limit=540,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": MAX_RETRIES},
)
def assess_evidence_task(
    self,
    evidence_file_id: str,
    organization_id: str,
    requested_by_user_id: str,
    assessment_source: str,
    force: bool = False,
) -> Dict[str, Any]:
    """Assess a single evidence file via Claude API.

    Runs in Celery worker — does not block the web server.

    ``force`` bypasses the cache gate and re-assesses even when the stored
    verdict is still valid for the current control context and prompt version.
    """
    task_id = self.request.id
    start_time = time.monotonic()
    logger.info(
        "assess_evidence_task[%s] starting for file=%s org=%s force=%s",
        task_id, evidence_file_id, organization_id, force,
    )

    session = _get_sync_session()
    try:
        # Step 1: Fetch the evidence file record
        result = session.execute(
            text("""
                SELECT id, evidence_id, organization_id, filename, content_type,
                       s3_key, sha256_hash, computed_sha256, storage_config_id
                FROM evidence_files
                WHERE id = :file_id AND organization_id = :org_id AND is_deleted = false
            """),
            {"file_id": evidence_file_id, "org_id": organization_id},
        )
        row = result.mappings().first()
        if not row:
            logger.error("assess_evidence_task[%s] file not found: %s", task_id, evidence_file_id)
            return {"status": "error", "message": "Evidence file not found"}

        evidence_id = row["evidence_id"]
        filename = row["filename"]
        content_type = row["content_type"]
        s3_key = row["s3_key"]
        # Which store the bytes are actually in. A row that predates per-file
        # storage carries NULL and resolves by organisation, as it always did.
        file_storage_config_id = (
            str(row["storage_config_id"]) if row["storage_config_id"] else None
        )
        # Prefer the hash the platform computed over the uploader's claim.
        file_sha256 = row["computed_sha256"] or row["sha256_hash"]

        # Step 2: Read the stored verdict BEFORE touching it. The trigger
        # endpoint sets the row to 'pending' on its way here, so a cache check
        # made after we flip it to 'processing' could never see a reusable
        # status — which is how the dormant twin's cache read came to have no
        # effect on the live path.
        prior = session.execute(
            text("""
                SELECT id, status, prompt_hash, prompt_version, control_context_hash,
                       assessed_file_sha256, current_version_id, ao_findings
                FROM evidence_assessments
                WHERE evidence_file_id = :file_id AND organization_id = :org_id
            """),
            {"file_id": evidence_file_id, "org_id": organization_id},
        ).mappings().first()

        # Step 3: Assemble control context (needed to evaluate the cache gate)
        control_context = assemble_control_context_sync(session, evidence_id)
        if not control_context:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                "No catalog entry found for evidence ID — cannot assess without control context",
            )
            return {"status": "error", "message": "No control context"}

        # Step 3b: Which engine this organisation has chosen in Settings. Read
        # per run, not per worker, so a change takes effect on the next file.
        engine = _resolve_engine(session, organization_id)
        current_prompt_version = prompt_version_for_engine(engine)

        # Step 4: Cache gate — unchanged file, context and prompt version means
        # the stored verdict is still the answer. Skip the model call. The
        # version compared is the chosen engine's, so an organisation that
        # switches engines gets a fresh verdict rather than the other engine's.
        if prior is not None and not force and is_cache_hit(
            status=prior["status"],
            prompt_hash=prior["prompt_hash"],
            stored_context_hash=prior["control_context_hash"],
            stored_prompt_version=prior["prompt_version"],
            file_sha256=file_sha256,
            current_context_hash=control_context.context_hash,
            stored_file_sha256=prior["assessed_file_sha256"],
            current_prompt_version=current_prompt_version,
        ):
            logger.info(
                "assess_evidence_task[%s] cache hit for file=%s (status=%s) — skipping model call",
                task_id, evidence_file_id, prior["status"],
            )
            outcome = {
                "status": prior["status"],
                "cached": True,
                "message": "Cached assessment reused — content and context unchanged",
            }
            # Shadow mode exists to compare Jev against verdicts the LLM has
            # already given. Those are exactly the files the gate reuses, so a
            # reused verdict that has no shadow yet gets one now — the primary
            # verdict is untouched, and nothing here can fail the task.
            if engine == jev_assessment.ENGINE_JEV_SHADOW:
                outcome["shadow"] = _shadow_cached_verdict(
                    session, evidence_file_id, organization_id, evidence_id, prior,
                    control_context, s3_key, file_storage_config_id, filename, content_type,
                )
            return outcome

        # Step 5: Claim the row
        session.execute(
            text("""
                UPDATE evidence_assessments
                SET status = 'processing'
                WHERE evidence_file_id = :file_id AND organization_id = :org_id
            """),
            {"file_id": evidence_file_id, "org_id": organization_id},
        )
        session.commit()

        # Step 6: Download and extract text
        file_bytes = download_evidence_bytes(
            s3_key,
            org_id=str(organization_id),
            storage_config_id=file_storage_config_id,
        )
        if file_bytes is None:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                "Failed to download evidence file from storage",
            )
            return {"status": "error", "message": "Download failed"}

        extracted = extract_text_from_bytes(file_bytes, content_type, filename)

        # An extraction failure is not a compliance verdict. Images, xlsx, zip
        # and broken files used to be stored as 'insufficient' with score 0,
        # which told the reviewer their evidence was bad and dragged the
        # quality axis down for a file the pipeline never actually read.
        if extracted.is_empty and extracted.error:
            _update_assessment_result(
                session, evidence_file_id, organization_id, start_time,
                status="unassessable",
                relevance_score=None,
                findings=[{
                    "category": "error",
                    "level": "info",
                    "message": extracted.error,
                    "suggestion": (
                        "Upload this evidence in a text-extractable format "
                        "(PDF, DOCX, CSV, JSON, YAML or TXT) so it can be assessed."
                    ),
                }],
                summary=f"Cannot assess this file: {extracted.error}",
                unassessable_reason=extracted.error,
                assessed_file_sha256=file_sha256,
            )
            return {"status": "unassessable", "message": extracted.error}

        if extracted.is_empty:
            # Extraction worked and found nothing. That IS a verdict on the
            # evidence: a blank document does not demonstrate compliance.
            _update_assessment_result(
                session, evidence_file_id, organization_id, start_time,
                status="insufficient",
                relevance_score=0,
                findings=[{
                    "category": "error",
                    "level": "insufficient",
                    "message": "Evidence file contains no readable content",
                    "suggestion": "Upload a document with substantive content",
                }],
                summary="Evidence file is empty or contains no extractable text.",
                assessed_file_sha256=file_sha256,
            )
            return {"status": "insufficient", "message": "Empty file"}

        assessment_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Step 7 (Jev primary): the organisation has moved off the LLM. Jev
        # answers the per-objective questions and its verdict is the one
        # stored; there is no LLM call. Failures raise into the handlers at
        # the bottom of this task exactly as LLM failures do.
        if engine == jev_assessment.ENGINE_JEV:
            terminal = _jev_primary_verdict(
                control_context, extracted, filename, content_type,
                assessment_date, file_sha256, start_time,
            )
            _write_terminal_verdict(session, evidence_file_id, organization_id, terminal)
            logger.info(
                "Jev assessment complete: file=%s, status=%s, score=%s, model=%s, cost=%s, time=%dms",
                evidence_file_id, terminal.status, terminal.relevance_score, terminal.model_id,
                f"{terminal.cost_cents:.4f} cents" if terminal.cost_cents is not None else "unknown",
                terminal.processing_time_ms,
                extra={
                    "custom_dimensions": {
                        "event_type": "ai_assessment_result",
                        "engine": engine,
                        "evidence_file_id": evidence_file_id,
                        "evidence_id": evidence_id,
                        "organization_id": organization_id,
                        "status": terminal.status,
                        "relevance_score": terminal.relevance_score,
                        "ao_finding_count": len(terminal.ao_findings),
                        "gap_count": terminal.gap_count,
                        "cannot_assess_count": terminal.cannot_assess_count,
                        "input_tokens": terminal.input_token_count,
                        "output_tokens": terminal.output_token_count,
                        "cost_cents": terminal.cost_cents,
                        "processing_time_ms": terminal.processing_time_ms,
                        "model_id": terminal.model_id,
                        "prompt_hash": terminal.prompt_hash,
                    }
                },
            )
            return {
                "status": terminal.status,
                "engine": engine,
                "relevance_score": terminal.relevance_score,
                "cached": False,
                "truncated": extracted.truncated,
                "gap_count": terminal.gap_count,
                "cannot_assess_count": terminal.cannot_assess_count,
                "processing_time_ms": terminal.processing_time_ms,
            }

        # Step 7: Build prompt
        system_prompt, user_prompt = build_assessment_prompt(
            control_context=control_context,
            extracted_text=extracted.text,
            filename=filename,
            content_type=content_type,
            assessment_date=assessment_date,
            truncated=extracted.truncated,
        )
        prompt_hash_value = hash_prompt(system_prompt, user_prompt)

        # Step 8: Log prompt for App Insights monitoring
        logger.info(
            "AI assessment prompt assembled",
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_prompt",
                    "evidence_file_id": evidence_file_id,
                    "evidence_id": evidence_id,
                    "organization_id": organization_id,
                    "filename": filename,
                    "content_type": content_type,
                    "prompt_hash": prompt_hash_value,
                    "control_context_hash": control_context.context_hash,
                    "framework_version": control_context.framework_version,
                    "control_count": len(control_context.controls),
                    "objective_count": len(control_context.objectives),
                    "objectives_capped": control_context.objectives_capped,
                    "extracted_text_length": len(extracted.text),
                    "extracted_text_truncated": extracted.truncated,
                    "model_id": resolve_model(MODEL_ROLE),
                    "system_prompt": system_prompt,
                    "user_prompt": user_prompt,
                }
            },
        )

        # Step 9: Call LLM. Failures raise from here — see LLMCallError /
        # LLMUnavailableError and the handlers at the bottom of this task.
        llm_result = _call_llm(system_prompt, user_prompt)

        # Step 10: Parse. A response that does not satisfy the contract raises;
        # nothing here manufactures a verdict the model did not give.
        prompted_ao_ids = [obj["ao_id"] for obj in control_context.objectives]
        parsed = parse_assessment_v2(
            llm_result["content"],
            llm_result.get("stop_reason"),
            prompted_ao_ids,
        )

        # Step 11: Derive the recorded status from the objective designations.
        # The model's own status is advisory and loses on disagreement — but
        # the disagreement is recorded rather than swallowed.
        derived_status, unassessable_reason = derive_assessment_status(parsed.designations)
        findings = list(parsed.findings)
        if derived_status is None:
            # No published objectives for these controls: there is no
            # designation arithmetic to do, so the model's own read is all
            # there is. It is labelled as such on the row.
            derived_status = parsed.model_status or "partial"
            findings.append({
                "category": "quality",
                "level": "info",
                "message": (
                    "The mapped controls publish no SCF assessment objectives, so this "
                    "status reflects the model's overall read of the file rather than a "
                    "per-objective evaluation."
                ),
            })
        else:
            coercion = status_coercion_finding(parsed.model_status, derived_status)
            if coercion:
                findings.append(coercion)

        if control_context.objectives_capped:
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

        age_exceeded = exceeds_max_age(parsed.evidence_effective_date)
        findings = _with_truncation_finding(
            findings, extracted.truncated, len(extracted.text),
        )

        # Price the model that ANSWERED — see services/model_registry.cost_cents.
        model_id = llm_result.get("model") or resolve_model(MODEL_ROLE)
        cost_cents = model_cost_cents(
            model_id,
            llm_result.get("input_tokens", 0),
            llm_result.get("output_tokens", 0),
        )
        processing_time_ms = int((time.monotonic() - start_time) * 1000)

        # Step 12: One transaction — append the frozen version, then repoint
        # the parent row at it.
        written = _write_terminal_verdict(
            session,
            evidence_file_id,
            organization_id,
            TerminalVerdict(
                status=derived_status,
                relevance_score=parsed.relevance_score,
                summary=parsed.summary,
                findings=findings,
                ao_findings=parsed.ao_findings,
                gap_count=parsed.gap_count,
                cannot_assess_count=parsed.cannot_assess_count,
                evidence_effective_date=parsed.evidence_effective_date,
                effective_date_source=parsed.effective_date_source,
                age_exceeds_12_months=age_exceeded,
                truncated=extracted.truncated,
                unassessable_reason=unassessable_reason,
                assessed_file_sha256=file_sha256,
                model_id=model_id,
                prompt_hash=prompt_hash_value,
                control_context_hash=control_context.context_hash,
                framework_version=control_context.framework_version,
                input_token_count=llm_result.get("input_tokens", 0),
                output_token_count=llm_result.get("output_tokens", 0),
                cost_cents=cost_cents,
                processing_time_ms=processing_time_ms,
            ),
        )

        # Step 12b (shadow): the LLM verdict above is the one that stands.
        # Jev now answers the same objectives, and its answer is stored beside
        # the version with a per-objective comparison. Nothing here can change
        # the verdict or fail the task — a shadow that could break the primary
        # would not be a shadow.
        if engine == jev_assessment.ENGINE_JEV_SHADOW and written is not None:
            _run_shadow_assessment(
                session, evidence_file_id, organization_id, evidence_id,
                control_context, extracted, filename, content_type, assessment_date,
                llm_ao_findings=parsed.ao_findings, llm_status=derived_status,
                assessment_id=written.assessment_id, version_id=written.version_id,
            )

        # Step 13: Log result for App Insights
        logger.info(
            "AI assessment complete: file=%s, status=%s, score=%s, model=%s, cost=%s, time=%dms",
            evidence_file_id,
            derived_status,
            parsed.relevance_score,
            model_id,
            f"{cost_cents:.4f} cents" if cost_cents is not None else "unknown (model not priced)",
            processing_time_ms,
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_result",
                    "evidence_file_id": evidence_file_id,
                    "evidence_id": evidence_id,
                    "organization_id": organization_id,
                    "status": derived_status,
                    "model_status": parsed.model_status,
                    "relevance_score": parsed.relevance_score,
                    "finding_count": len(findings),
                    "ao_finding_count": len(parsed.ao_findings),
                    "gap_count": parsed.gap_count,
                    "cannot_assess_count": parsed.cannot_assess_count,
                    "evidence_effective_date": (
                        parsed.evidence_effective_date.isoformat()
                        if parsed.evidence_effective_date else None
                    ),
                    "age_exceeds_12_months": age_exceeded,
                    "extracted_text_truncated": extracted.truncated,
                    "input_tokens": llm_result.get("input_tokens", 0),
                    "output_tokens": llm_result.get("output_tokens", 0),
                    "cost_cents": cost_cents,
                    "processing_time_ms": processing_time_ms,
                    "model_id": model_id,
                    "prompt_hash": prompt_hash_value,
                }
            },
        )

        return {
            "status": derived_status,
            "relevance_score": parsed.relevance_score,
            "cached": False,
            "truncated": extracted.truncated,
            "gap_count": parsed.gap_count,
            "cannot_assess_count": parsed.cannot_assess_count,
            "processing_time_ms": processing_time_ms,
        }

    except (LLMUnavailableError, jev_client.JevUnavailableError) as exc:
        # Deliberately NOT re-raised. A missing SDK or a missing API key is
        # identical on every attempt, so retrying it just delays the same
        # answer by the backoff interval and hides the real cause behind a
        # generic retry exhaustion. The Jev twin is the same failure on the
        # other engine: an organisation set to `jev` with no TypeSafe key.
        logger.error(
            "assess_evidence_task[%s] cannot run — %s", task_id, exc,
        )
        try:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                str(exc),
                exception_class=type(exc).__name__,
                retryable=False,
            )
        except Exception:
            logger.exception(
                "assess_evidence_task[%s] could not record unavailable-model error", task_id,
            )
        return {"status": "error", "message": str(exc), "retryable": False}

    except Exception as exc:
        # Every attempt writes a terminal-looking row before re-raising, so a
        # worker that dies mid-backoff can never leave the assessment stuck on
        # 'processing'. A queued retry overwrites it back to 'processing' when
        # it starts; the last attempt's row is the one that stands.
        attempt = self.request.retries + 1
        # A response cut off at the token ceiling comes back cut off at exactly
        # the same place every time the same prompt is sent, so it is final on
        # the first attempt rather than after three identical ones.
        worth_retrying = exc.retryable if isinstance(exc, AssessmentParseError) else True
        is_final = self.request.retries >= MAX_RETRIES or not worth_retrying
        logger.error(
            "assess_evidence_task[%s] failed on attempt %d/%d: %s",
            task_id, attempt, MAX_RETRIES + 1, exc, exc_info=True,
        )
        if isinstance(exc, LLMCallError):
            reason = f"Model call failed ({exc.cause_class}): {exc.cause_message}"
        elif isinstance(exc, AssessmentParseError):
            reason = f"Model response could not be read: {exc.reason}"
        elif isinstance(exc, jev_client.JevCallError):
            http = f" (HTTP {exc.status_code})" if exc.status_code else ""
            reason = f"Jev call failed{http}: {str(exc)[:500]}"
        elif isinstance(exc, (jev_client.JevResponseError, jev_assessment.JevVerdictError)):
            reason = f"Jev response could not be read: {str(exc)[:500]}"
        else:
            reason = f"Assessment failed ({type(exc).__name__}): {str(exc)[:500]}"
        if not is_final:
            reason = f"{reason} — retry {attempt} of {MAX_RETRIES} scheduled"
        try:
            _update_assessment_error(
                session, evidence_file_id, organization_id, start_time,
                reason,
                exception_class=type(exc).__name__,
                retryable=not is_final,
            )
        except Exception:
            logger.exception(
                "assess_evidence_task[%s] could not record failure state", task_id,
            )
        if not worth_retrying:
            # Terminal by nature, not by exhaustion: returning rather than
            # raising keeps the retry budget for failures that can change.
            return {"status": "error", "message": reason, "retryable": False}
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TRUNCATION_FINDING_KEY = "truncated"
TRUNCATION_CHARS_KEY = "truncated_at_chars"


def _with_truncation_finding(findings: list, truncated: bool, assessed_chars: int) -> list:
    """Append a truncation disclosure finding when the text was cut short.

    The *fact* of truncation now lives in the ``truncated`` column, which is
    what readers should test. This finding still carries it because it is the
    user-visible disclosure, and because it carries the character count — which
    has no column of its own and would otherwise force clients to guess at the
    extractor's limit.
    """
    if not truncated:
        return list(findings)
    return list(findings) + [{
        "category": "quality",
        "level": "info",
        "message": (
            f"Only the first {assessed_chars:,} characters of this document were "
            "assessed; the rest was truncated to stay within the model's context "
            "budget. Findings may not reflect later sections."
        ),
        "suggestion": (
            "Split large documents into control-specific extracts so the whole "
            "of each one can be assessed."
        ),
        TRUNCATION_FINDING_KEY: True,
        TRUNCATION_CHARS_KEY: assessed_chars,
    }]


def assessment_truncated_chars(findings) -> Optional[int]:
    """How many characters were actually assessed, when the text was cut.

    Exposed so consumers can state the real figure instead of hardcoding
    MAX_TEXT_LENGTH, which lives in the extractor and can change without
    anything downstream noticing.
    """
    finding = _truncation_finding(findings)
    if finding is None:
        return None
    value = finding.get(TRUNCATION_CHARS_KEY)
    return value if isinstance(value, int) else None


def _truncation_finding(findings) -> Optional[dict]:
    if not isinstance(findings, list):
        return None
    for f in findings:
        if isinstance(f, dict) and f.get(TRUNCATION_FINDING_KEY) is True:
            return f
    return None


@dataclass
class TerminalVerdict:
    """Everything one finished assessment writes, in both places it goes.

    A terminal write appends an immutable version row and repoints the mutable
    parent at it, so the two must carry identical values. Passing them as one
    object is what stops the pair drifting apart the next time a field is added.
    """
    status: str
    summary: str
    findings: List[Dict[str, Any]]
    relevance_score: Optional[float] = None
    ao_findings: List[Dict[str, Any]] = field(default_factory=list)
    gap_count: int = 0
    cannot_assess_count: int = 0
    evidence_effective_date: Optional[date] = None
    effective_date_source: Optional[str] = None
    age_exceeds_12_months: Optional[bool] = None
    truncated: bool = False
    unassessable_reason: Optional[str] = None
    assessed_file_sha256: Optional[str] = None
    model_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    control_context_hash: Optional[str] = None
    framework_version: Optional[str] = None
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None
    cost_cents: Optional[float] = None
    processing_time_ms: int = 0
    # 2 = AO-grounded. Every row this module writes is; the field exists so a
    # reader never has to infer the contract from the shape of the payload.
    schema_version: int = 2
    # The release of whatever produced `prompt_hash`: the LLM prompt template
    # (`PROMPT_VERSION`) or the Jev question set. It is what the cache gate
    # compares, so switching an organisation between engines re-assesses
    # rather than reusing the other engine's verdict. None when no model ran.
    prompt_version: Optional[str] = None


_INSERT_VERSION_SQL = text("""
    INSERT INTO evidence_assessment_versions (
        id, assessment_id, evidence_file_id, organization_id, evidence_id,
        version_number, schema_version,
        status, relevance_score, summary, findings, ao_findings,
        gap_count, cannot_assess_count,
        evidence_effective_date, effective_date_source, age_exceeds_12_months,
        truncated, unassessable_reason,
        model_id, prompt_hash, prompt_version, control_context_hash,
        framework_version, input_token_count, output_token_count,
        cost_cents, processing_time_ms, assessed_file_sha256,
        assessment_source, requested_by_user_id, assessed_at
    )
    SELECT
        :version_id, ea.id, ea.evidence_file_id, ea.organization_id, ea.evidence_id,
        :version_number, :schema_version,
        :status, :relevance_score, :summary, :findings, :ao_findings,
        :gap_count, :cannot_assess_count,
        :evidence_effective_date, :effective_date_source, :age_exceeds_12_months,
        :truncated, :unassessable_reason,
        :model_id, :prompt_hash, :prompt_version, :control_context_hash,
        :framework_version, :input_token_count, :output_token_count,
        :cost_cents, :processing_time_ms, :assessed_file_sha256,
        ea.assessment_source, ea.requested_by_user_id, :assessed_at
    FROM evidence_assessments ea
    WHERE ea.id = :assessment_id
""")

_UPDATE_CURRENT_SQL = text("""
    UPDATE evidence_assessments SET
        status = :status,
        relevance_score = :relevance_score,
        summary = :summary,
        findings = :findings,
        ao_findings = :ao_findings,
        gap_count = :gap_count,
        cannot_assess_count = :cannot_assess_count,
        evidence_effective_date = :evidence_effective_date,
        effective_date_source = :effective_date_source,
        age_exceeds_12_months = :age_exceeds_12_months,
        truncated = :truncated,
        unassessable_reason = :unassessable_reason,
        assessed_file_sha256 = :assessed_file_sha256,
        model_id = :model_id,
        prompt_hash = :prompt_hash,
        prompt_version = :prompt_version,
        control_context_hash = :control_context_hash,
        framework_version = :framework_version,
        input_token_count = :input_token_count,
        output_token_count = :output_token_count,
        cost_cents = :cost_cents,
        processing_time_ms = :processing_time_ms,
        assessed_at = :assessed_at,
        current_version_id = :version_id,
        version_number = :version_number,
        -- A new verdict has not been reviewed. Carrying the previous review
        -- forward would show a reviewer's name against findings they never saw.
        review_decision = NULL,
        reviewed_by_user_id = NULL,
        reviewed_at = NULL
    WHERE id = :assessment_id
""")


class WrittenVerdict(NamedTuple):
    """What ``_write_terminal_verdict`` appended: the ids a follow-on writer
    (the Jev shadow) attaches to, so it never has to rediscover them."""
    assessment_id: str
    version_id: str
    version_number: int


def _write_terminal_verdict(
    session,
    evidence_file_id: str,
    organization_id: str,
    verdict: TerminalVerdict,
) -> Optional[WrittenVerdict]:
    """Append the verdict as a new version and repoint the current row at it.

    One transaction, committed once. The parent row is locked FOR UPDATE so two
    workers assessing the same file cannot both read version N and both write
    N+1; if they race past the lock anyway, the unique constraint on
    (assessment_id, version_number) refuses the second one rather than
    admitting a duplicate into the history.

    Returns the ids of what was written, or None when there is no assessment
    row to write to — which happens when the trigger endpoint's row was
    deleted mid-flight, and is a no-op rather than an error.
    """
    current = session.execute(
        text("""
            SELECT id, COALESCE(version_number, 0) AS version_number
            FROM evidence_assessments
            WHERE evidence_file_id = :file_id AND organization_id = :org_id
            FOR UPDATE
        """),
        {"file_id": evidence_file_id, "org_id": organization_id},
    ).mappings().first()

    if not current:
        logger.warning(
            "No evidence_assessments row for file=%s org=%s — verdict not stored",
            evidence_file_id, organization_id,
        )
        session.rollback()
        return None

    version_id = str(uuid.uuid4())
    next_version = int(current["version_number"] or 0) + 1
    assessed_at = datetime.utcnow()

    # The template version travels with the hash (#787). A verdict reached
    # without calling a model — an extraction failure, a download failure —
    # had no prompt, and stamping the current template release onto it would
    # claim provenance for something that never happened. A verdict that
    # names its own release (the Jev path) keeps it.
    prompt_version = (
        (verdict.prompt_version or PROMPT_VERSION) if verdict.prompt_hash else None
    )

    params = {
        "version_id": version_id,
        "assessment_id": current["id"],
        "version_number": next_version,
        "schema_version": verdict.schema_version,
        "status": verdict.status,
        "relevance_score": verdict.relevance_score,
        "summary": verdict.summary,
        "findings": json.dumps(verdict.findings, default=str),
        "ao_findings": json.dumps(verdict.ao_findings, default=str),
        "gap_count": verdict.gap_count,
        "cannot_assess_count": verdict.cannot_assess_count,
        "evidence_effective_date": verdict.evidence_effective_date,
        "effective_date_source": verdict.effective_date_source,
        "age_exceeds_12_months": verdict.age_exceeds_12_months,
        "truncated": verdict.truncated,
        "unassessable_reason": verdict.unassessable_reason,
        "assessed_file_sha256": verdict.assessed_file_sha256,
        "model_id": verdict.model_id,
        "prompt_hash": verdict.prompt_hash,
        "prompt_version": prompt_version,
        "control_context_hash": verdict.control_context_hash,
        "framework_version": verdict.framework_version,
        "input_token_count": verdict.input_token_count,
        "output_token_count": verdict.output_token_count,
        "cost_cents": verdict.cost_cents,
        "processing_time_ms": verdict.processing_time_ms,
        "assessed_at": assessed_at,
    }

    session.execute(_INSERT_VERSION_SQL, params)
    session.execute(_UPDATE_CURRENT_SQL, params)
    session.commit()
    return WrittenVerdict(
        assessment_id=str(current["id"]), version_id=version_id, version_number=next_version,
    )


def _update_assessment_error(
    session,
    evidence_file_id: str,
    organization_id: str,
    start_time: float,
    error_message: str,
    prompt_hash: Optional[str] = None,
    control_context_hash: Optional[str] = None,
    exception_class: Optional[str] = None,
    retryable: Optional[bool] = None,
):
    """Record a failed assessment.

    ``exception_class`` and ``retryable`` are recorded on the finding so an
    operator reading the row can tell a transient API error apart from a
    misconfigured worker without going to the worker logs.

    A version row is appended only when the failure is terminal. A transient
    failure between retries is a state of the pipeline, not a verdict about the
    evidence, and three of them in the history would say the platform assessed
    the file three times and found it wanting.

    The provenance written here belongs to the attempt that failed — usually
    nothing. The previous run's model id and prompt hash are deliberately not
    carried forward: they describe a verdict this row is not, and they are
    preserved intact in that verdict's own version row.
    """
    processing_time_ms = int((time.monotonic() - start_time) * 1000)
    finding = {
        "category": "error",
        "level": "insufficient",
        "message": error_message,
    }
    if exception_class:
        finding["exception_class"] = exception_class
    if retryable is not None:
        finding["retryable"] = retryable

    verdict = TerminalVerdict(
        status="error",
        summary=error_message,
        findings=[finding],
        prompt_hash=prompt_hash,
        control_context_hash=control_context_hash,
        processing_time_ms=processing_time_ms,
    )

    if retryable:
        _update_current_only(session, evidence_file_id, organization_id, verdict)
        return
    _write_terminal_verdict(session, evidence_file_id, organization_id, verdict)


def _update_current_only(
    session,
    evidence_file_id: str,
    organization_id: str,
    verdict: TerminalVerdict,
) -> None:
    """Write the visible row without appending to history.

    Used for the transient-failure state between retries: the UI must not show
    a file stuck on 'processing' when a worker has died, but the history must
    not gain an entry for something that is not a verdict.
    """
    session.execute(
        text("""
            UPDATE evidence_assessments SET
                status = :status,
                summary = :summary,
                findings = :findings,
                processing_time_ms = :processing_time_ms,
                assessed_at = :assessed_at
            WHERE evidence_file_id = :file_id AND organization_id = :org_id
        """),
        {
            "status": verdict.status,
            "summary": verdict.summary,
            "findings": json.dumps(verdict.findings, default=str),
            "processing_time_ms": verdict.processing_time_ms,
            "assessed_at": datetime.utcnow(),
            "file_id": evidence_file_id,
            "org_id": organization_id,
        },
    )
    session.commit()


# ---------------------------------------------------------------------------
# Engine selection and the Jev paths
# ---------------------------------------------------------------------------

def _resolve_engine(session, organization_id: str) -> str:
    """The evidence-assessment engine chosen for this organisation in Settings.

    Anything unreadable — no row, a JSON column returned as text, an unknown
    value — resolves to the LLM, which is today's behaviour. An organisation
    can never lose assessments to a malformed setting.
    """
    row = session.execute(
        text("SELECT settings FROM organizations WHERE id = :org_id"),
        {"org_id": organization_id},
    ).first()
    settings = row[0] if row else None
    if isinstance(settings, (str, bytes)):
        try:
            settings = json.loads(settings)
        except ValueError:
            settings = None
    return jev_assessment.resolve_engine(settings)


def _jev_primary_verdict(
    control_context,
    extracted,
    filename: str,
    content_type: str,
    assessment_date: str,
    file_sha256: Optional[str],
    start_time: float,
) -> TerminalVerdict:
    """Ask Jev and shape its answer as the verdict this file will carry.

    ``prompt_hash`` is the hash of the Jev request and ``prompt_version`` the
    question-set release, so the cache gate treats a Jev verdict exactly as it
    treats an LLM one — and never mistakes one for the other.
    """
    verdict = jev_assessment.assess(
        control_context, extracted.text, filename, content_type,
        assessment_date, extracted.truncated,
    )
    findings = jev_assessment.terminal_findings(verdict, control_context)
    findings = _with_truncation_finding(findings, extracted.truncated, len(extracted.text))
    processing_time_ms = int((time.monotonic() - start_time) * 1000)
    return TerminalVerdict(
        status=verdict.status,
        relevance_score=verdict.relevance_score,
        summary=jev_assessment.terminal_summary(verdict),
        findings=findings,
        ao_findings=jev_assessment.terminal_ao_findings(verdict),
        gap_count=verdict.gap_count,
        cannot_assess_count=verdict.cannot_assess_count,
        truncated=extracted.truncated,
        unassessable_reason=verdict.unassessable_reason,
        assessed_file_sha256=file_sha256,
        model_id=verdict.model_id,
        prompt_hash=verdict.request_hash,
        prompt_version=jev_assessment.QUESTION_SET_VERSION,
        control_context_hash=control_context.context_hash,
        framework_version=control_context.framework_version,
        input_token_count=verdict.input_tokens,
        output_token_count=verdict.output_tokens,
        cost_cents=verdict.cost_cents,
        processing_time_ms=processing_time_ms,
    )


_INSERT_SHADOW_SQL = text("""
    INSERT INTO evidence_assessment_shadow_verdicts (
        id, assessment_id, version_id, evidence_file_id, organization_id, evidence_id,
        engine, model_id, question_set_version,
        status, relevance_score, ao_findings, gap_count, cannot_assess_count,
        low_confidence_count, confidence_cutoff, comparison, state_truncated,
        input_token_count, output_token_count, cost_cents, processing_time_ms, error
    ) VALUES (
        :id, :assessment_id, :version_id, :file_id, :org_id, :evidence_id,
        :engine, :model_id, :question_set_version,
        :status, :relevance_score, :ao_findings, :gap_count, :cannot_assess_count,
        :low_confidence_count, :confidence_cutoff, :comparison, :state_truncated,
        :input_token_count, :output_token_count, :cost_cents, :processing_time_ms, :error
    )
""")


def _run_shadow_assessment(
    session,
    evidence_file_id: str,
    organization_id: str,
    evidence_id: str,
    control_context,
    extracted,
    filename: str,
    content_type: str,
    assessment_date: str,
    *,
    llm_ao_findings: List[Dict[str, Any]],
    llm_status: str,
    assessment_id: str,
    version_id: Optional[str],
) -> None:
    """Run Jev against the file the LLM judged and store the comparison.

    Never raises. The primary verdict is already committed when this is
    called, and ``assessment_id``/``version_id`` name it — passed in by the
    writer that committed it rather than rediscovered here, so the shadow is
    attached to the version Jev actually saw. A shadow that failed is recorded
    as a row with ``error`` set (the failure rate is part of what shadow mode
    measures), and a shadow whose *row* could not be written is logged and
    dropped.
    """
    started = time.monotonic()
    params: Dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "assessment_id": assessment_id,
        "version_id": version_id,
        "file_id": evidence_file_id,
        "org_id": organization_id,
        "evidence_id": evidence_id,
        "engine": jev_assessment.ENGINE_JEV,
        "model_id": resolve_model(jev_assessment.MODEL_ROLE),
        "question_set_version": jev_assessment.QUESTION_SET_VERSION,
        "status": None,
        "relevance_score": None,
        "ao_findings": "[]",
        "gap_count": 0,
        "cannot_assess_count": 0,
        "low_confidence_count": 0,
        "confidence_cutoff": None,
        "comparison": None,
        "state_truncated": False,
        "input_token_count": None,
        "output_token_count": None,
        "cost_cents": None,
        "processing_time_ms": None,
        "error": None,
    }
    try:
        verdict = jev_assessment.assess(
            control_context, extracted.text, filename, content_type,
            assessment_date, extracted.truncated,
        )
        comparison = jev_assessment.compare(llm_ao_findings, llm_status, verdict)
        params.update({
            "model_id": verdict.model_id,
            "status": verdict.status,
            "relevance_score": verdict.relevance_score,
            "ao_findings": json.dumps(verdict.ao_findings, default=str),
            "gap_count": verdict.gap_count,
            "cannot_assess_count": verdict.cannot_assess_count,
            "low_confidence_count": verdict.low_confidence_count,
            "confidence_cutoff": verdict.confidence_cutoff,
            "comparison": json.dumps(comparison, default=str),
            "state_truncated": verdict.state_truncated,
            "input_token_count": verdict.input_tokens,
            "output_token_count": verdict.output_tokens,
            "cost_cents": verdict.cost_cents,
        })
        logger.info(
            "Jev shadow assessment: file=%s llm=%s jev=%s agreement=%s confident_agreement=%s",
            evidence_file_id, llm_status, verdict.status,
            comparison["agreement_rate"], comparison["confident_agreement_rate"],
            extra={
                "custom_dimensions": {
                    "event_type": "ai_assessment_shadow",
                    "evidence_file_id": evidence_file_id,
                    "organization_id": organization_id,
                    **{k: v for k, v in comparison.items() if k != "disagreements"},
                    "low_confidence_count": verdict.low_confidence_count,
                    "cost_cents": verdict.cost_cents,
                }
            },
        )
    except Exception as exc:  # noqa: BLE001 — the shadow must never fail the task
        params["error"] = f"{type(exc).__name__}: {str(exc)[:1000]}"
        logger.warning(
            "Jev shadow assessment failed for file=%s: %s", evidence_file_id, params["error"],
        )
    params["processing_time_ms"] = int((time.monotonic() - started) * 1000)

    try:
        session.execute(_INSERT_SHADOW_SQL, params)
        session.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Could not store Jev shadow verdict for file=%s", evidence_file_id)
        try:
            session.rollback()
        except Exception:  # noqa: BLE001
            pass


_SHADOW_EXISTS_SQL = text("""
    SELECT 1 FROM evidence_assessment_shadow_verdicts
    WHERE assessment_id = :assessment_id
      AND version_id IS NOT DISTINCT FROM :version_id
    LIMIT 1
""")


def _shadow_cached_verdict(
    session,
    evidence_file_id: str,
    organization_id: str,
    evidence_id: str,
    prior,
    control_context,
    s3_key: str,
    storage_config_id: Optional[str],
    filename: str,
    content_type: str,
) -> str:
    """Shadow a verdict the cache gate is reusing, once per version.

    Returns what happened — ``present`` (this version already has its shadow),
    ``ran`` (Jev was asked and the row written) or ``skipped`` (nothing to
    attach to, or the bytes could not be read back). Never raises: the cached
    verdict is already the answer, and a shadow cannot change that.
    """
    assessment_id = prior.get("id")
    version_id = prior.get("current_version_id")
    if assessment_id is None:
        return "skipped"
    try:
        if session.execute(
            _SHADOW_EXISTS_SQL,
            {"assessment_id": str(assessment_id), "version_id": version_id},
        ).first():
            return "present"
        file_bytes = download_evidence_bytes(
            s3_key, org_id=str(organization_id), storage_config_id=storage_config_id,
        )
        if file_bytes is None:
            logger.warning("Shadow skipped for file=%s: bytes could not be read back", evidence_file_id)
            return "skipped"
        extracted = extract_text_from_bytes(file_bytes, content_type, filename)
        if extracted.is_empty:
            return "skipped"
        ao_findings = prior.get("ao_findings") or []
        if isinstance(ao_findings, (str, bytes)):
            ao_findings = json.loads(ao_findings)
        _run_shadow_assessment(
            session, evidence_file_id, organization_id, evidence_id,
            control_context, extracted, filename, content_type,
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            llm_ao_findings=list(ao_findings), llm_status=prior["status"],
            assessment_id=str(assessment_id),
            version_id=str(version_id) if version_id is not None else None,
        )
        return "ran"
    except Exception:  # noqa: BLE001 — the cached verdict is already the answer
        logger.exception("Shadow of cached verdict failed for file=%s", evidence_file_id)
        return "skipped"


def _update_assessment_result(
    session,
    evidence_file_id: str,
    organization_id: str,
    start_time: float,
    status: str,
    relevance_score: Optional[float],
    findings: list,
    summary: str,
    unassessable_reason: Optional[str] = None,
    assessed_file_sha256: Optional[str] = None,
):
    """Record a terminal result reached without an LLM call.

    ``relevance_score`` is Optional because 'unassessable' has no score to
    give: a file the pipeline could not read was never scored against the
    controls, and writing 0 there is a claim about content nobody saw.
    """
    _write_terminal_verdict(
        session, evidence_file_id, organization_id,
        TerminalVerdict(
            status=status,
            summary=summary,
            findings=findings,
            relevance_score=relevance_score,
            unassessable_reason=unassessable_reason,
            assessed_file_sha256=assessed_file_sha256,
            processing_time_ms=int((time.monotonic() - start_time) * 1000),
        ),
    )
