"""Admin API for the OSS live SCF catalogue import ("bring your own SCF Excel").

Lets a self-hosted operator upload their licensed SCF .xlsx through the UI and
seed the catalogue without the one-shot importer container. Gated to platform
admins AND single-tenant (self-hosted) deployments — never available to a
multi-tenant/SaaS tenant (a force-reseed would wipe shared catalogue tables).
"""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_platform_admin, User
from catalog_models import SCFCatalogControl
from celery_app import celery_app
from database import get_db
from services import s3_service
from services.single_tenant import is_single_tenant_active, single_tenant_org_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog-admin"])

_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_XLSX_BYTES = 50 * 1024 * 1024  # 50 MB — the SCF workbook is ~6 MB today.
_ALLOWED_XLSX_TYPES = {
    _XLSX_CONTENT_TYPE,
    "application/octet-stream",  # some browsers send this for .xlsx
}


class CatalogStatus(BaseModel):
    seeded: bool
    controls: int


class CatalogImportAccepted(BaseModel):
    task_id: str
    status: str = "accepted"


class CatalogImportStatus(BaseModel):
    task_id: str
    state: str
    step: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None


def _require_single_tenant() -> None:
    if not is_single_tenant_active():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Catalogue upload is only available in self-hosted single-tenant mode.",
        )


@router.get("/catalog/status", response_model=CatalogStatus)
async def catalog_status(db: AsyncSession = Depends(get_db)) -> CatalogStatus:
    """Is the SCF catalogue seeded yet? Drives the frontend onboarding gate.

    Unauthenticated by design — leaks only a control count, and the onboarding
    screen must render before any login on a fresh deploy.
    """
    count = (
        await db.execute(select(func.count()).select_from(SCFCatalogControl))
    ).scalar() or 0
    return CatalogStatus(seeded=count > 0, controls=count)


@router.post(
    "/admin/catalog/import",
    response_model=CatalogImportAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_catalog(
    file: UploadFile = File(...),
    current_user: User = Depends(require_platform_admin),
) -> CatalogImportAccepted:
    """Accept an SCF .xlsx, stash it, and enqueue the extract-and-reseed task."""
    _require_single_tenant()

    filename = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload must be a .xlsx file.",
        )
    if file.content_type and file.content_type not in _ALLOWED_XLSX_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported content type: {file.content_type}",
        )

    body = await file.read()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )
    if len(body) > _MAX_XLSX_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Workbook exceeds the 50 MB limit.",
        )

    task_id = str(uuid.uuid4())
    object_key = f"_catalog-import/{task_id}.xlsx"
    org_id = single_tenant_org_id() or "single-tenant"
    try:
        s3_service.put_bytes(object_key, body, _XLSX_CONTENT_TYPE, org_id)
    except Exception as exc:  # noqa: BLE001 — surface a clean 502 to the operator
        logger.exception("Failed to stash catalogue upload")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not store the uploaded workbook.",
        ) from exc

    celery_app.send_task(
        "catalog.import",
        kwargs={"object_key": object_key, "original_filename": filename},
        task_id=task_id,
        queue="catalog",
    )
    logger.info(
        "Queued catalogue import task %s (%s, %d bytes)", task_id, filename, len(body)
    )
    return CatalogImportAccepted(task_id=task_id)


@router.get(
    "/admin/catalog/import/{task_id}",
    response_model=CatalogImportStatus,
)
async def import_status(
    task_id: str,
    current_user: User = Depends(require_platform_admin),
) -> CatalogImportStatus:
    """Poll the state of a catalogue import task (Celery result backend)."""
    _require_single_tenant()
    result = celery_app.AsyncResult(task_id)
    state = result.state
    step: Optional[str] = None
    payload: Optional[dict] = None
    error: Optional[str] = None

    info = result.info
    if state == "PROGRESS" and isinstance(info, dict):
        step = info.get("step")
    elif state == "SUCCESS" and isinstance(result.result, dict):
        payload = result.result
    elif state == "FAILURE":
        error = str(result.result) if result.result else "Unknown error"

    return CatalogImportStatus(
        task_id=task_id, state=state, step=step, result=payload, error=error
    )
