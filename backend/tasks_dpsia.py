"""
Celery task for DPSIA vendor assessments.

Calls the DPSIA assessment service via HTTP, parses the response, stores the
DOCX in Azure Blob Storage (or S3 for legacy AWS deployments), and auto-creates
platform records (VendorAssessment, VendorCIAControl, VendorActionItem, VendorReport).
"""
import base64
import json
import logging
import os
import uuid
from datetime import datetime, date, timedelta
from typing import Any, Dict, Optional

import httpx
from celery import shared_task
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

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
# Sync Redis helper
# ---------------------------------------------------------------------------
_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


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
# Constants
# ---------------------------------------------------------------------------
DPSIA_CACHE_TTL = int(timedelta(days=30).total_seconds())
CACHE_KEY_PREFIX = "scf:cache:v1:dpsia"
TASK_PREFIX = "tasks_dpsia"

DPSIA_SERVICE_URL = os.getenv("DPSIA_SERVICE_URL", "http://localhost:3000")


# ---------------------------------------------------------------------------
# Rating / score helpers
# ---------------------------------------------------------------------------

def _rating_to_score(rating: str) -> int:
    """Convert DPSIA control rating string to 1-5 numeric score."""
    mapping = {
        "Strong": 5,
        "Adequate": 3,
        "Weak": 1,
        "Not Assessed": 2,
        "N/A": 3,
    }
    return mapping.get(rating, 2)


def _avg_cia_score(report_json: dict, controls_key: str) -> Optional[int]:
    """Average control scores for a CIA pillar, returning 1-5 integer."""
    controls = report_json.get(controls_key, [])
    if not controls:
        return None
    scores = [_rating_to_score(c.get("rating", "Not Assessed")) for c in controls]
    return round(sum(scores) / len(scores))


def _map_dpsia_priority(priority: str) -> str:
    """Map DPSIA priority values to platform action item priorities."""
    mapping = {
        "Critical": "critical",
        "High": "high",
        "Medium": "medium",
        "Low": "low",
    }
    return mapping.get(priority, "medium")


def _parse_due_date(due_str: str) -> Optional[date]:
    """Try to parse a due date string from DPSIA output."""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%B %Y"):
        try:
            return datetime.strptime(due_str, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Main Celery task
# ---------------------------------------------------------------------------

@shared_task(bind=True, name=f"{TASK_PREFIX}.dpsia_invoke_lambda", time_limit=600, soft_time_limit=540)
def dpsia_invoke_lambda(
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
    Invoke the DPSIA Lambda, parse results, store DOCX in S3, and auto-create
    platform records (VendorAssessment, CIA controls, action items, report).
    """
    task_id = self.request.id
    logger.info(f"dpsia_invoke_lambda[{task_id}] starting for vendor={vendor_id} job={job_id}")

    # 1. Mark job as running
    _update_status(job_id, "running", started_at=datetime.utcnow())

    try:
        # 2. Build payload matching AssessmentInput interface
        # vendorDescription is required — use name as fallback
        effective_description = vendor_description or f"{vendor_name} - third-party vendor"
        payload = {
            "vendorName": vendor_name,
            "vendorDescription": effective_description,
            "clientName": client_name or "Client",
            "assessmentType": assessment_type,
            "servicesUsed": services_used,
            "dataRole": data_role,
            "additionalContext": additional_context or None,
        }

        # 3. Call DPSIA assessment service via HTTP
        response = httpx.post(
            f"{DPSIA_SERVICE_URL}/assess",
            json=payload,
            timeout=600.0,
        )
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

        # 5. Extract key fields from AssessmentOutput
        rag_status = output.get("ragStatus")
        recommendation = output.get("recommendation")
        risk_score = output.get("riskScore")
        risk_level = output.get("riskLevel")
        executive_summary = output.get("executiveSummary")
        report_markdown = output.get("reportMarkdown")
        report_docx_base64 = output.get("reportDocxBase64")
        report_filename = output.get("reportFilename", f"DPSIA-{vendor_name}.docx")
        research_sources = output.get("researchSources", [])
        processing_time_ms = output.get("processingTimeMs")

        # The full DPSIAReport JSON is nested inside the output
        report_json = output.get("reportJson") or output

        # 6. Store DOCX in Azure Blob Storage if available
        report_docx_s3_key = None
        if report_docx_base64:
            try:
                docx_bytes = base64.b64decode(report_docx_base64)
                blob_key = f"dpsia-reports/{vendor_id}/{job_id}/{report_filename}"
                from services.azure_blob_service import (
                    _get_container_client,
                    _content_settings,
                    is_configured as azure_configured,
                )
                if azure_configured():
                    blob_client = _get_container_client().get_blob_client(blob_key)
                    blob_client.upload_blob(
                        docx_bytes,
                        overwrite=True,
                        content_settings=_content_settings(
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                        ),
                    )
                    report_docx_s3_key = blob_key
                    logger.info(f"Stored DOCX in Azure Blob: {blob_key}")
                else:
                    logger.warning("Azure Blob Storage not configured — DOCX not stored")
            except Exception as exc:
                logger.warning(f"Failed to store DOCX: {exc}")

        # 7. Auto-create platform records
        linked_assessment_id, linked_report_id = _create_platform_records(
            vendor_id=vendor_id,
            organization_id=organization_id,
            report_json=report_json,
            report_markdown=report_markdown or "",
            rag_status=rag_status,
            recommendation=recommendation,
            risk_score=risk_score,
            risk_level=risk_level,
            executive_summary=executive_summary,
            assessment_type=assessment_type,
        )

        # 8. Update DPSIA record with results
        now = datetime.utcnow()
        session = _get_sync_session()
        try:
            from sqlalchemy import text
            session.execute(
                text("""
                    UPDATE vendor_dpsia_assessments SET
                        status = :status, rag_status = :rag_status, recommendation = :recommendation,
                        risk_score = :risk_score, risk_level = :risk_level,
                        executive_summary = :executive_summary, report_markdown = :report_markdown,
                        report_json = :report_json, report_docx_s3_key = :report_docx_s3_key,
                        report_filename = :report_filename, research_sources = :research_sources,
                        linked_assessment_id = :linked_assessment_id, linked_report_id = :linked_report_id,
                        processing_time_ms = :processing_time_ms, completed_at = :completed_at
                    WHERE job_id = :job_id
                """),
                {
                    "status": "completed",
                    "rag_status": rag_status,
                    "recommendation": recommendation,
                    "risk_score": risk_score,
                    "risk_level": risk_level,
                    "executive_summary": executive_summary,
                    "report_markdown": report_markdown,
                    "report_json": json.dumps(report_json) if report_json else None,
                    "report_docx_s3_key": report_docx_s3_key,
                    "report_filename": report_filename,
                    "research_sources": json.dumps(research_sources) if research_sources else None,
                    "linked_assessment_id": str(linked_assessment_id) if linked_assessment_id else None,
                    "linked_report_id": str(linked_report_id) if linked_report_id else None,
                    "processing_time_ms": processing_time_ms,
                    "completed_at": now,
                    "job_id": job_id,
                },
            )
            session.commit()
        finally:
            session.close()

        # 9. Update vendor risk_score and risk_level
        _update_vendor_risk(vendor_id, risk_score, risk_level)

        # 10. Cache results in Redis
        _cache_results(vendor_id, job_id, {
            "job_id": job_id,
            "vendor_id": vendor_id,
            "status": "completed",
            "assessment_type": assessment_type,
            "data_role": data_role,
            "rag_status": rag_status,
            "recommendation": recommendation,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "executive_summary": executive_summary,
            "report_markdown": report_markdown,
            "report_json": report_json,
            "report_filename": report_filename,
            "research_sources": research_sources,
            "linked_assessment_id": str(linked_assessment_id) if linked_assessment_id else None,
            "linked_report_id": str(linked_report_id) if linked_report_id else None,
            "processing_time_ms": processing_time_ms,
            "error_message": None,
            "started_at": None,
            "completed_at": now.isoformat(),
            "created_at": None,
        })

        logger.info(f"DPSIA assessment {job_id} completed: RAG={rag_status}, score={risk_score}")
        return {"job_id": job_id, "status": "completed", "rag_status": rag_status}

    except Exception as exc:
        logger.exception(f"DPSIA assessment {job_id} failed: {exc}")
        _update_status(job_id, "failed", error_message=str(exc)[:2000], completed_at=datetime.utcnow())
        return {"job_id": job_id, "status": "failed", "error": str(exc)[:500]}


# ---------------------------------------------------------------------------
# Platform record creation
# ---------------------------------------------------------------------------

def _create_platform_records(
    vendor_id: str,
    organization_id: str,
    report_json: dict,
    report_markdown: str,
    rag_status: Optional[str],
    recommendation: Optional[str],
    risk_score: Optional[int],
    risk_level: Optional[str],
    executive_summary: Optional[str],
    assessment_type: str,
) -> tuple:
    """
    Auto-create VendorAssessment, VendorCIAControl, VendorActionItem, VendorReport
    from the DPSIA output. Returns (assessment_id, report_id).
    Uses raw SQL to avoid model import issues in the Celery worker process.
    """
    from sqlalchemy import text

    session = _get_sync_session()
    assessment_id = None
    report_id = None

    try:
        # --- VendorAssessment ---
        conf_score = _avg_cia_score(report_json, "confidentialityControls")
        integ_score = _avg_cia_score(report_json, "integrityControls")
        avail_score = _avg_cia_score(report_json, "availabilityControls")
        inherent_risk_level_val = (report_json.get("inherentRiskLevel") or "").lower() or None

        result = session.execute(
            text("""
                INSERT INTO vendor_assessments (
                    id, vendor_id, assessment_type, assessment_date, status,
                    confidentiality_score, integrity_score, availability_score,
                    final_risk_score, risk_level, ai_analysis,
                    inherent_risk_score, inherent_risk_level, control_effectiveness_pct,
                    findings, risk_rating
                ) VALUES (
                    gen_random_uuid(), :vendor_id, :assessment_type, :assessment_date, 'completed',
                    :conf_score, :integ_score, :avail_score,
                    :risk_score, :risk_level, :ai_analysis,
                    :inherent_risk_score, :inherent_risk_level, :control_effectiveness_pct,
                    :findings, :risk_rating
                ) RETURNING id
            """),
            {
                "vendor_id": vendor_id,
                "assessment_type": _DPSIA_TO_PLATFORM_ASSESSMENT_TYPE.get(assessment_type, "triggered"),
                "assessment_date": date.today(),
                "conf_score": conf_score,
                "integ_score": integ_score,
                "avail_score": avail_score,
                "risk_score": risk_score,
                "risk_level": (risk_level or "").lower() if risk_level else None,
                "ai_analysis": executive_summary,
                "inherent_risk_score": report_json.get("inherentRiskScore"),
                "inherent_risk_level": inherent_risk_level_val,
                "control_effectiveness_pct": report_json.get("controlEffectivenessPercent"),
                "findings": executive_summary,
                "risk_rating": (risk_level or "").lower() if risk_level else None,
            },
        )
        assessment_id = result.fetchone()[0]

        # --- VendorCIAControl records ---
        for pillar, controls_key in [
            ("confidentiality", "confidentialityControls"),
            ("integrity", "integrityControls"),
            ("availability", "availabilityControls"),
        ]:
            controls = report_json.get(controls_key, [])
            for ctrl in controls:
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

        # --- VendorActionItem records ---
        mandatory_actions = report_json.get("mandatoryActions", [])
        for action in mandatory_actions:
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
                    "priority": _map_dpsia_priority(action.get("priority", "Medium")),
                    "owner_name": action.get("owner", ""),
                    "due_date": _parse_due_date(action.get("dueDate", "")),
                },
            )

        # --- VendorReport ---
        result = session.execute(
            text("""
                INSERT INTO vendor_reports (
                    id, vendor_id, assessment_id, organization_id, report_type,
                    title, content_markdown, content_json, risk_score, risk_level, recommendation
                ) VALUES (
                    gen_random_uuid(), :vendor_id, :assessment_id, :organization_id, 'dpsia',
                    :title, :content_markdown, :content_json, :risk_score, :risk_level, :recommendation
                ) RETURNING id
            """),
            {
                "vendor_id": vendor_id,
                "assessment_id": assessment_id,
                "organization_id": organization_id,
                "title": f"DPSIA Assessment - {report_json.get('vendorLegalName', 'Vendor')}"[:255],
                "content_markdown": report_markdown,
                "content_json": json.dumps(report_json) if report_json else None,
                "risk_score": risk_score,
                "risk_level": (risk_level or "").lower() if risk_level else None,
                "recommendation": recommendation,
            },
        )
        report_id = result.fetchone()[0]

        session.commit()
        logger.info(
            f"Created platform records: assessment={assessment_id}, "
            f"report={report_id}, controls={sum(len(report_json.get(k, [])) for k in ['confidentialityControls', 'integrityControls', 'availabilityControls'])}, "
            f"actions={len(mandatory_actions)}"
        )
    except Exception as exc:
        session.rollback()
        logger.error(f"Failed to create platform records: {exc}")
    finally:
        session.close()

    return assessment_id, report_id


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Map DPSIA assessment types to platform VendorAssessment types
_DPSIA_TO_PLATFORM_ASSESSMENT_TYPE = {
    "new": "initial",
    "annual-review": "periodic",
    "adhoc": "triggered",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _update_status(job_id: str, status: str, **kwargs):
    """Update DPSIA assessment status in the database."""
    try:
        from sqlalchemy import text
        session = _get_sync_session()
        try:
            set_parts = ["status = :status"]
            params = {"status": status, "job_id": job_id}
            for key, value in kwargs.items():
                set_parts.append(f"{key} = :{key}")
                params[key] = value
            sql = text(f"UPDATE vendor_dpsia_assessments SET {', '.join(set_parts)} WHERE job_id = :job_id")
            session.execute(sql, params)
            session.commit()
        finally:
            session.close()
    except Exception as exc:
        logger.error(f"Failed to update DPSIA job {job_id} status to {status}: {exc}")


def _update_vendor_risk(vendor_id: str, risk_score: Optional[int], risk_level: Optional[str]):
    """Update the vendor's risk_score and risk_level."""
    if risk_score is None:
        return
    try:
        from sqlalchemy import text
        session = _get_sync_session()
        try:
            session.execute(
                text("UPDATE vendors SET risk_score = :score, risk_level = :level WHERE id = :vid"),
                {"score": risk_score, "level": (risk_level or "").lower() if risk_level else None, "vid": vendor_id},
            )
            session.commit()
        finally:
            session.close()
    except Exception as exc:
        logger.error(f"Failed to update vendor {vendor_id} risk: {exc}")


def _cache_results(vendor_id: str, job_id: str, payload: dict):
    """Cache DPSIA results in Redis with 30-day TTL."""
    try:
        r = _get_sync_redis()
        cache_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:{job_id}"
        r.setex(cache_key, DPSIA_CACHE_TTL, json.dumps(payload, default=str))
        latest_key = f"{CACHE_KEY_PREFIX}:{vendor_id}:latest"
        r.setex(latest_key, DPSIA_CACHE_TTL, json.dumps(payload, default=str))
        logger.info(f"Cached DPSIA results for job {job_id}")
    except Exception as exc:
        logger.warning(f"Failed to cache DPSIA results for job {job_id}: {exc}")
