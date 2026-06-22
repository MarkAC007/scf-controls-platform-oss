"""Celery tasks for windowed evidence assessment.

Runs portfolio-level assessment of evidence in time windows derived from
EvidenceTracking.frequency. Single-evidence trigger + nightly refresh of
windows whose latest files are newer than the latest assessment.

Follows conventions from tasks_assessment.py (sync psycopg2 session per task).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional
from uuid import UUID

from celery import shared_task
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from services.window_assessment_service import assess_window

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sync DB session (same pattern as tasks_assessment._get_sync_session)
# ---------------------------------------------------------------------------

_SYNC_DATABASE_URL = (
    os.getenv("DATABASE_URL", "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf")
    .replace("+asyncpg", "+psycopg2")
    .replace("?ssl=require", "?sslmode=require")
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
# Config
# ---------------------------------------------------------------------------

# Cap on how many evidence IDs the nightly refresh will enqueue in one run.
# Guards against cost spikes if a large backlog accumulates.
NIGHTLY_REFRESH_CAP = int(os.getenv("WINDOW_ASSESSMENT_NIGHTLY_CAP", "100"))


# ---------------------------------------------------------------------------
# Single-evidence window assessment
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="tasks_window_assessment.assess_window_task",
    time_limit=600,
    soft_time_limit=540,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def assess_window_task(
    self,
    organization_id: str,
    evidence_id: str,
    requested_by_user_id: Optional[str] = None,
    assessment_source: str = "on_demand",
) -> Dict[str, Any]:
    """Run a windowed assessment for one evidence ID in one org."""
    task_id = self.request.id
    logger.info(
        "assess_window_task[%s] starting org=%s evidence=%s",
        task_id, organization_id, evidence_id,
    )
    session = _get_sync_session()
    try:
        assessment = assess_window(
            session,
            organization_id=UUID(organization_id),
            evidence_id=evidence_id,
            assessment_source=assessment_source,
            requested_by_user_id=UUID(requested_by_user_id) if requested_by_user_id else None,
        )
        return {
            "assessment_id": str(assessment.id),
            "status": assessment.status,
            "relevance_score": float(assessment.relevance_score) if assessment.relevance_score is not None else None,
            "file_count": len(assessment.file_ids or []),
            "processing_time_ms": assessment.processing_time_ms,
            "cost_cents": float(assessment.cost_cents) if assessment.cost_cents is not None else None,
        }
    except Exception as exc:
        logger.error("assess_window_task[%s] failed: %s", task_id, exc, exc_info=True)
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Nightly refresh — queue window assessments for stale evidence
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="tasks_window_assessment.nightly_window_refresh_task",
    time_limit=1800,
    soft_time_limit=1500,
)
def nightly_window_refresh_task(self) -> Dict[str, Any]:
    """Find evidence where new files have landed since the last window
    assessment and queue fresh window assessments for each.

    Cap is NIGHTLY_REFRESH_CAP to avoid cost spikes from big backlogs.
    """
    task_id = self.request.id
    logger.info("nightly_window_refresh_task[%s] starting, cap=%d", task_id, NIGHTLY_REFRESH_CAP)
    session = _get_sync_session()
    try:
        rows = session.execute(
            text(
                """
                WITH per_evidence_latest AS (
                    SELECT ef.organization_id,
                           ef.evidence_id,
                           MAX(ef.uploaded_at) AS latest_file_at
                      FROM evidence_files ef
                     WHERE ef.is_deleted = false
                     GROUP BY ef.organization_id, ef.evidence_id
                ),
                per_window_latest AS (
                    SELECT ewa.organization_id,
                           ewa.evidence_id,
                           MAX(ewa.assessed_at) AS latest_assessed_at
                      FROM evidence_window_assessments ewa
                     GROUP BY ewa.organization_id, ewa.evidence_id
                )
                SELECT pe.organization_id::text AS org_id,
                       pe.evidence_id,
                       pe.latest_file_at,
                       pw.latest_assessed_at
                  FROM per_evidence_latest pe
             LEFT JOIN per_window_latest pw
                    ON pw.organization_id = pe.organization_id
                   AND pw.evidence_id = pe.evidence_id
                 WHERE pw.latest_assessed_at IS NULL
                    OR pe.latest_file_at > pw.latest_assessed_at
                 ORDER BY pe.latest_file_at ASC
                 LIMIT :cap
                """
            ),
            {"cap": NIGHTLY_REFRESH_CAP},
        ).mappings().all()

        queued = 0
        for row in rows:
            try:
                assess_window_task.delay(
                    organization_id=row["org_id"],
                    evidence_id=row["evidence_id"],
                    requested_by_user_id=None,
                    assessment_source="auto",
                )
                queued += 1
            except Exception as exc:
                logger.error(
                    "Failed to enqueue window assessment for org=%s evidence=%s: %s",
                    row["org_id"], row["evidence_id"], exc,
                )

        logger.info(
            "nightly_window_refresh_task[%s] queued=%d candidates=%d",
            task_id, queued, len(rows),
        )
        return {"queued": queued, "candidates": len(rows), "cap": NIGHTLY_REFRESH_CAP}
    finally:
        session.close()
