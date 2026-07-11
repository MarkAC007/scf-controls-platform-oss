"""
Celery task for vendor AI assessments (unified persistence).

Runs the in-process engine (`services.vendor_assessment_engine`) — or, when
DPSIA_SERVICE_URL is set, the legacy external HTTP DPSIA service — and
persists results directly onto the single unified `vendor_assessments` row
(created as 'pending' at trigger time, updated through running -> completed
or failed).

On completion:
- the report (markdown + JSON), RAG status, recommendation, executive
  summary and research sources are written to the assessment row itself;
- VendorCIAControl and VendorActionItem children are created against the
  assessment;
- the vendor's authoritative risk score is updated with provenance
  (risk_score_source + risk_scored_at) and the annual review date is set to
  completed_at + 12 months;
- results are cached in Redis for the read endpoints.
"""
import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional

import httpx
from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

TASK_PREFIX = "tasks_vendor_assessment"

# ---------------------------------------------------------------------------
# Sync database session (Celery runs outside the async event loop)
# ---------------------------------------------------------------------------
_SYNC_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://odin:changeme@localhost:5432/odin_scf"
).replace("+asyncpg", "+psycopg2").replace("?ssl=require", "?sslmode=require")

_sync_engine = None
SyncSession = None


def _get_sync_session():
    """Lazily create the sync engine and session factory."""
    global _sync_engine, SyncSession
    if SyncSession is None:
        _sync_engine = create_engine(_SYNC_DATABASE_URL, pool_pre_ping=True, pool_size=2, max_overflow=3)
        SyncSession = sessionmaker(bind=_sync_engine, expire_on_commit=False)
    return SyncSession()


# ---------------------------------------------------------------------------
# Sync Redis helper + cache constants (key prefix kept from the DPSIA era so
# existing cached entries and readers stay compatible)
# ---------------------------------------------------------------------------
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
ASSESSMENT_CACHE_TTL = int(timedelta(days=30).total_seconds())
CACHE_KEY_PREFIX = "scf:cache:v1:dpsia"


def _get_sync_redis():
    """Return a synchronous Redis client."""
    import redis as sync_redis
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
# Rating / score / date helpers
# ---------------------------------------------------------------------------

def _rating_to_score(rating: str) -> int:
    """Convert report control rating string to 1-5 numeric score.

    Case-insensitive and tolerant of near-vocabulary the model occasionally
    emits (e.g. MODERATE) so pillar averages don't collapse to the default.
    """
    mapping = {
        "strong": 5,
        "moderate": 3,
        "adequate": 3,
        "weak": 1,
        "not assessed": 2,
        "n/a": 3,
    }
    return mapping.get((rating or "").strip().lower(), 2)


def _avg_cia_score(report_json: dict, controls_key: str) -> Optional[int]:
    """Average control scores for a CIA pillar, returning 1-5 integer."""
    controls = report_json.get(controls_key, [])
    if not controls:
        return None
    scores = [_rating_to_score(c.get("rating", "Not Assessed")) for c in controls]
    return round(sum(scores) / len(scores))


def _map_priority(priority: str) -> str:
    """Map report priority values to platform action item priorities."""
    mapping = {
        "Critical": "critical",
        "High": "high",
        "Medium": "medium",
        "Low": "low",
    }
    return mapping.get(priority, "medium")


def _parse_due_date(due_str: str) -> Optional[date]:
    """Try to parse a due date string from the report output."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%B %Y"):
        try:
            return datetime.strptime(due_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _add_months(dt: datetime, months: int) -> date:
    """Add calendar months to a datetime, returning a date (clamps day)."""
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    # Clamp the day to the last day of the target month
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start - timedelta(days=1)).day
    return date(year, month, min(dt.day, last_day))


# ---------------------------------------------------------------------------
# Persistence helpers (unified vendor_assessments row)
# ---------------------------------------------------------------------------

def _update_assessment_status(job_id: str, status: str, **kwargs):
    """Update the unified assessment row's status (+ optional columns) by job_id."""
    try:
        session = _get_sync_session()
        try:
            set_parts = ["status = :status"]
            params: Dict[str, Any] = {"status": status, "job_id": job_id}
            for key, value in kwargs.items():
                set_parts.append(f"{key} = :{key}")
                params[key] = value
            sql = text(f"UPDATE vendor_assessments SET {', '.join(set_parts)} WHERE job_id = :job_id")
            session.execute(sql, params)
            session.commit()
        finally:
            session.close()
    except Exception as exc:
        logger.error(f"Failed to update assessment job {job_id} status to {status}: {exc}")


def _persist_completed(
    job_id: str,
    vendor_id: str,
    result: Dict[str, Any],
    completed_at: datetime,
) -> Optional[str]:
    """
    Write a completed assessment onto the unified vendor_assessments row,
    create CIA control / action item children, and update the vendor's
    authoritative risk score with provenance + next review date.

    Returns the assessment id (str) or None if the row was not found.
    """
    report_json = result["report_json"] or {}
    risk_level_lower = (result["risk_level"] or "").lower() or None
    review_date = _add_months(completed_at, 12)

    session = _get_sync_session()
    try:
        row = session.execute(
            text("""
                UPDATE vendor_assessments SET
                    status = 'completed',
                    assessment_date = :assessment_date,
                    completed_at = :completed_at,
                    rag_status = :rag_status,
                    recommendation = :recommendation,
                    executive_summary = :executive_summary,
                    report_markdown = :report_markdown,
                    report_json = :report_json,
                    research_sources = :research_sources,
                    processing_time_ms = :processing_time_ms,
                    final_risk_score = :risk_score,
                    risk_level = :risk_level,
                    risk_rating = :risk_level,
                    ai_analysis = :executive_summary,
                    findings = :executive_summary,
                    confidentiality_score = :conf_score,
                    integrity_score = :integ_score,
                    availability_score = :avail_score,
                    inherent_risk_score = :inherent_risk_score,
                    inherent_risk_level = :inherent_risk_level,
                    control_effectiveness_pct = :control_effectiveness_pct,
                    next_assessment_date = :next_assessment_date,
                    error_message = NULL
                WHERE job_id = :job_id
                RETURNING id
            """),
            {
                "job_id": job_id,
                "assessment_date": completed_at.date(),
                "completed_at": completed_at,
                "rag_status": result["rag_status"],
                "recommendation": result["recommendation"],
                "executive_summary": result["executive_summary"],
                "report_markdown": result["report_markdown"],
                "report_json": json.dumps(report_json) if report_json else None,
                "research_sources": json.dumps(result["research_sources"]) if result["research_sources"] else None,
                "processing_time_ms": result["processing_time_ms"],
                "risk_score": result["risk_score"],
                "risk_level": risk_level_lower,
                "conf_score": _avg_cia_score(report_json, "confidentialityControls"),
                "integ_score": _avg_cia_score(report_json, "integrityControls"),
                "avail_score": _avg_cia_score(report_json, "availabilityControls"),
                "inherent_risk_score": report_json.get("inherentRiskScore"),
                "inherent_risk_level": (report_json.get("inherentRiskLevel") or "").lower() or None,
                "control_effectiveness_pct": report_json.get("controlEffectivenessPercent"),
                "next_assessment_date": review_date,
            },
        ).fetchone()

        if not row:
            session.rollback()
            logger.error(f"No vendor_assessments row found for job {job_id} — results not persisted")
            return None

        assessment_id = str(row[0])

        # --- VendorCIAControl children ---
        for pillar, controls_key in [
            ("confidentiality", "confidentialityControls"),
            ("integrity", "integrityControls"),
            ("availability", "availabilityControls"),
        ]:
            for ctrl in report_json.get(controls_key, []):
                session.execute(
                    text("""
                        INSERT INTO vendor_cia_controls (id, assessment_id, pillar, control_name, score, detail)
                        VALUES (gen_random_uuid(), :assessment_id, :pillar, :control_name, :score, :detail)
                    """),
                    {
                        "assessment_id": assessment_id,
                        "pillar": pillar,
                        "control_name": ctrl.get("control", "Unknown")[:255],
                        "score": _rating_to_score(ctrl.get("rating", "Not Assessed")),
                        "detail": ctrl.get("implementation", ""),
                    },
                )

        # --- VendorActionItem children ---
        # Supersede auto-generated items from earlier assessments: cancel any
        # still-open ones so the vendor carries a single current action list.
        # Items a user has started (in_progress) or finished are left alone.
        session.execute(
            text("""
                UPDATE vendor_action_items
                SET status = 'cancelled'
                WHERE vendor_id = :vendor_id
                  AND assessment_id != :assessment_id
                  AND auto_generated = true
                  AND category = 'dpsia_mandatory'
                  AND status = 'open'
            """),
            {"vendor_id": vendor_id, "assessment_id": assessment_id},
        )
        for action in report_json.get("mandatoryActions", []):
            session.execute(
                text("""
                    INSERT INTO vendor_action_items (
                        id, vendor_id, assessment_id, title, description, priority,
                        status, category, owner_name, due_date, auto_generated
                    ) VALUES (
                        gen_random_uuid(), :vendor_id, :assessment_id, :title, :description, :priority,
                        'open', 'dpsia_mandatory', :owner_name, :due_date, true
                    )
                """),
                {
                    "vendor_id": vendor_id,
                    "assessment_id": assessment_id,
                    "title": action.get("action", "Action required")[:255],
                    "description": action.get("action", ""),
                    "priority": _map_priority(action.get("priority", "Medium")),
                    "owner_name": action.get("owner", ""),
                    "due_date": _parse_due_date(action.get("dueDate", "")),
                },
            )

        # --- Vendor: authoritative risk score with provenance + review date ---
        if result["risk_score"] is not None:
            session.execute(
                text("""
                    UPDATE vendors SET
                        risk_score = :risk_score,
                        risk_level = :risk_level,
                        risk_score_source = :assessment_id,
                        risk_scored_at = :scored_at,
                        next_review_date = :next_review_date
                    WHERE id = :vendor_id
                """),
                {
                    "vendor_id": vendor_id,
                    "risk_score": result["risk_score"],
                    "risk_level": risk_level_lower,
                    "assessment_id": assessment_id,
                    "scored_at": completed_at,
                    "next_review_date": review_date,
                },
            )

        session.commit()
        logger.info(
            f"Persisted completed assessment {assessment_id} (job {job_id}): "
            f"controls={sum(len(report_json.get(k, [])) for k in ['confidentialityControls', 'integrityControls', 'availabilityControls'])}, "
            f"actions={len(report_json.get('mandatoryActions', []))}"
        )
        return assessment_id
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _cache_results(vendor_id: str, job_id: str, payload: dict):
    """Cache assessment results in Redis with 30-day TTL."""
    try:
        r = _get_sync_redis()
        cache_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:{job_id}"
        r.setex(cache_key, ASSESSMENT_CACHE_TTL, json.dumps(payload, default=str))
        latest_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:latest"
        r.setex(latest_key, ASSESSMENT_CACHE_TTL, json.dumps(payload, default=str))
        logger.info(f"Cached assessment results for job {job_id}")
    except Exception as exc:
        logger.warning(f"Failed to cache assessment results for job {job_id}: {exc}")


# ---------------------------------------------------------------------------
# Research context (existing platform signals injected into the AI prompt)
# ---------------------------------------------------------------------------

def _gather_research_context(vendor_id: str) -> str:
    """
    Format the latest completed platform research (HIBP/CISA KEV/NVD/regulatory,
    collected by tasks_research) as additional context for the AI synthesis.
    Returns "" when nothing is available — never raises.
    """
    try:
        session = _get_sync_session()
        try:
            row = session.execute(
                text("""
                    SELECT summary, risk_indicators, overall_risk_signal,
                           hibp_results, cve_nvd_results, regulatory_results
                    FROM vendor_research_results
                    WHERE vendor_id = :vendor_id AND status IN ('completed', 'partial')
                    ORDER BY created_at DESC
                    LIMIT 1
                """),
                {"vendor_id": vendor_id},
            ).fetchone()
        finally:
            session.close()

        if not row:
            return ""

        parts = []
        summary, risk_indicators, overall_signal, hibp, cve_nvd, regulatory = row
        if overall_signal:
            parts.append(f"Overall platform risk signal: {overall_signal}")
        if summary:
            parts.append(f"Research summary: {summary}")
        for label, payload in [
            ("Breach data (HIBP)", hibp),
            ("CVE data (NVD)", cve_nvd),
            ("Regulatory findings", regulatory),
            ("Risk indicators", risk_indicators),
        ]:
            if payload:
                parts.append(f"{label}: {json.dumps(payload, default=str)[:2000]}")
        return "\n\n".join(parts)
    except Exception as exc:
        logger.warning("Could not gather platform research context for vendor %s: %s", vendor_id, exc)
        return ""


# ---------------------------------------------------------------------------
# Legacy external DPSIA service (optional override, kept until retired)
# ---------------------------------------------------------------------------

def _run_external_dpsia_service(
    service_url: str,
    vendor_name: str,
    vendor_description: str,
    services_used: str,
    assessment_type: str,
    data_role: str,
    client_name: str,
    additional_context: str,
) -> Dict[str, Any]:
    """
    POST to the external DPSIA service and map its HTTP response into the
    same result shape as the native engine (DOCX output is discarded —
    reports are markdown + JSON now).
    """
    payload = {
        "vendorName": vendor_name,
        "vendorDescription": vendor_description or f"{vendor_name} - third-party vendor",
        "clientName": client_name or "Client",
        "assessmentType": assessment_type,
        "servicesUsed": services_used,
        "dataRole": data_role,
        "additionalContext": additional_context or None,
    }

    response = httpx.post(f"{service_url.rstrip('/')}/assess", json=payload, timeout=600.0)
    response.raise_for_status()
    output = response.json()

    # Handle API Gateway-shaped response (statusCode + body) for backward compat
    if "statusCode" in output:
        status_code = output["statusCode"]
        body = output.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)
        if status_code != 200:
            raise RuntimeError(f"DPSIA service returned status {status_code}: {body.get('error', 'Unknown error')}")
        output = body

    if output.get("status") == "error":
        raise RuntimeError(f"DPSIA assessment failed: {output.get('error', 'Unknown error')}")

    return {
        "rag_status": output.get("ragStatus"),
        "recommendation": output.get("recommendation"),
        "risk_score": output.get("riskScore"),
        "risk_level": output.get("riskLevel"),
        "executive_summary": output.get("executiveSummary"),
        "report_markdown": output.get("reportMarkdown") or "",
        "report_json": output.get("reportJson") or output,
        "research_sources": output.get("researchSources", []),
        "processing_time_ms": output.get("processingTimeMs"),
    }


# ---------------------------------------------------------------------------
# Main Celery task
# ---------------------------------------------------------------------------

@shared_task(bind=True, name=f"{TASK_PREFIX}.run_vendor_assessment", time_limit=600, soft_time_limit=540)
def run_vendor_assessment(
    self,
    vendor_id: str,
    organization_id: str,
    job_id: str,
    vendor_name: str,
    vendor_description: str,
    vendor_website: str,
    services_used: str,
    assessment_type: str = "new",
    data_role: str = "Processor",
    client_name: str = "",
    additional_context: str = "",
) -> Dict[str, Any]:
    """
    Run a vendor AI assessment against the unified vendor_assessments row:
    native engine by default; legacy external HTTP DPSIA service when
    DPSIA_SERVICE_URL is set. `assessment_type` uses the engine vocabulary
    (new | annual-review | adhoc).
    """
    task_id = self.request.id
    logger.info(f"run_vendor_assessment[{task_id}] starting for vendor={vendor_id} job={job_id}")

    _update_assessment_status(job_id, "running", started_at=datetime.utcnow())

    try:
        service_url = os.getenv("DPSIA_SERVICE_URL", "").strip()
        if service_url:
            logger.info(f"DPSIA_SERVICE_URL set — using external DPSIA service for job {job_id}")
            result = _run_external_dpsia_service(
                service_url=service_url,
                vendor_name=vendor_name,
                vendor_description=vendor_description,
                services_used=services_used,
                assessment_type=assessment_type,
                data_role=data_role,
                client_name=client_name,
                additional_context=additional_context,
            )
        else:
            from services.vendor_assessment_engine import run_assessment

            research_context = _gather_research_context(vendor_id)
            result = run_assessment(
                vendor_name=vendor_name,
                vendor_description=vendor_description or f"{vendor_name} - third-party vendor",
                vendor_website=vendor_website,
                services_used=services_used,
                data_role=data_role,
                assessment_type=assessment_type,
                client_name=client_name or "Client",
                additional_context=additional_context or "",
                research_context=research_context,
            )

        now = datetime.utcnow()
        assessment_id = _persist_completed(
            job_id=job_id,
            vendor_id=vendor_id,
            result=result,
            completed_at=now,
        )

        _cache_results(vendor_id, job_id, {
            "assessment_id": assessment_id,
            "job_id": job_id,
            "vendor_id": vendor_id,
            "status": "completed",
            "assessment_type": assessment_type,
            "data_role": data_role,
            "rag_status": result["rag_status"],
            "recommendation": result["recommendation"],
            "risk_score": result["risk_score"],
            "risk_level": result["risk_level"],
            "executive_summary": result["executive_summary"],
            "report_markdown": result["report_markdown"],
            "report_json": result["report_json"],
            "report_filename": None,
            "research_sources": result["research_sources"],
            "linked_assessment_id": assessment_id,
            "linked_report_id": None,
            "processing_time_ms": result["processing_time_ms"],
            "error_message": None,
            "started_at": None,
            "completed_at": now.isoformat(),
            "created_at": None,
        })

        logger.info(
            f"Vendor assessment {job_id} completed: RAG={result['rag_status']}, "
            f"score={result['risk_score']}, assessment={assessment_id}"
        )
        return {
            "job_id": job_id,
            "assessment_id": assessment_id,
            "status": "completed",
            "rag_status": result["rag_status"],
        }

    except Exception as exc:
        logger.exception(f"Vendor assessment {job_id} failed: {exc}")
        _update_assessment_status(
            job_id, "failed",
            error_message=str(exc)[:2000],
            completed_at=datetime.utcnow(),
        )
        return {"job_id": job_id, "status": "failed", "error": str(exc)[:500]}
