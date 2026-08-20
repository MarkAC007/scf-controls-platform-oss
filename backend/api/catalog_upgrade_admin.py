"""Platform-admin API for the SCF catalog upgrade (plan §4.2, §4.5 — WP1c).

Implements the WP-C contract stubs over the WP1b services:
- upload → run ledger row + Celery ``catalog.upgrade_stage`` dispatch;
- run list/detail + paginated field-level diff from the stored diff detail;
- admin-confirmed superseded pairings (PUT) + post-apply correction (PATCH);
- typed-confirm apply, cancel, and revert (pre-flighted against the WP1b
  guards so blockers surface as 409s instead of async task failures);
- the tenants reconciliation board.

Auth: every route requires platform admin. The destructive routes (pairings
PUT, apply, revert, superseded-by PATCH) additionally require a real user
session — the static API key is auto-granted platform admin and must not
drive destructive catalog ops (plan §4.5, §4.8).

Contracts live in backend/schemas_catalog_upgrade.py — imported, never
redefined.
"""
import logging
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_platform_admin, require_platform_admin_user_session, User
from catalog_models import SCFCatalogControl
from celery_app import celery_app
from database import get_db
from models import CatalogImportRun, Organization, OrganizationCatalogState, OrganizationReconciliationRun
from schemas_catalog_upgrade import (
    CatalogEntityType,
    ChangeClass,
    DiffDetail,
    DiffItem,
    DiffPageResponse,
    DiffSummary,
    PairingsUpdateRequest,
    PairingsUpdateResponse,
    PlatformImportRunDetail,
    PlatformImportRunsListResponse,
    PlatformImportRunSummary,
    PlatformRunStatus,
    SanityReport,
    SupersededByPatchRequest,
    SupersededByPatchResponse,
    SupersededPairing,
    TenantBoardRow,
    TenantsBoardResponse,
    UpgradeApplyRequest,
    UpgradeApplyResponse,
    UpgradeCancelResponse,
    UpgradeRevertResponse,
    UpgradeUploadResponse,
)
from services import s3_service
from services.catalog_apply import (
    RevertBlockedError,
    RevertNotLatestError,
    _check_revert_allowed,
    get_current_catalog_version,
)
from services.catalog_diff import parse_version
from tasks_catalog import UPGRADE_OBJECT_PREFIX

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog-upgrade-admin"])

# The stage task resolves the real to_version from the workbook; the column is
# NOT NULL so freshly-uploaded runs carry this placeholder until staging lands.
TO_VERSION_PENDING = "pending"

# Mirrors the partial unique indexes from migrations catupg004/catupg005.
IN_FLIGHT_RUN_STATUSES = ("staging", "staged", "applying")
ORG_ACTIVE_RUN_STATUSES = ("previewed", "applying", "rolling_back")

CANCELLABLE_RUN_STATUSES = ("staging", "staged", "blocked")

# Upload validation (same envelope as the OSS import in catalog_admin.py).
_XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MAX_XLSX_BYTES = 50 * 1024 * 1024
_ALLOWED_XLSX_TYPES = {_XLSX_CONTENT_TYPE, "application/octet-stream"}

# Object-storage metadata requires an org id; upgrade artifacts are
# platform-level, not tenant data (same tag the Celery tasks use).
_PLATFORM_ORG_TAG = "platform"

_CHANGE_CLASS_ORDER = (
    ChangeClass.ADDED,
    ChangeClass.CHANGED,
    ChangeClass.DEPRECATED,
    ChangeClass.RESURRECTED,
    ChangeClass.UNCHANGED,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_run(db: AsyncSession, run_id: UUID) -> CatalogImportRun:
    result = await db.execute(
        select(CatalogImportRun).where(CatalogImportRun.id == run_id)
    )
    run = result.scalars().first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Catalog import run {run_id} not found")
    return run


def _public_to_version(run: CatalogImportRun) -> Optional[str]:
    return None if run.to_version == TO_VERSION_PENDING else run.to_version


def _run_summary_kwargs(run: CatalogImportRun) -> dict:
    return {
        "id": run.id,
        "from_version": run.from_version,
        "to_version": _public_to_version(run),
        "status": PlatformRunStatus(run.status),
        "created_by": str(run.started_by) if run.started_by else None,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "diff_summary": (
            DiffSummary.model_validate(run.diff_summary) if run.diff_summary else None
        ),
    }


def _run_to_summary(run: CatalogImportRun) -> PlatformImportRunSummary:
    return PlatformImportRunSummary(**_run_summary_kwargs(run))


def _run_to_detail(run: CatalogImportRun) -> PlatformImportRunDetail:
    return PlatformImportRunDetail(
        **_run_summary_kwargs(run),
        sanity_report=(
            SanityReport.model_validate(run.sanity_report) if run.sanity_report else None
        ),
        superseded_pairings=[
            SupersededPairing.model_validate(p) for p in (run.superseded_pairings or [])
        ],
        workbook_object_key=run.workbook_object_key,
        diff_detail_object_key=run.diff_detail_object_key,
        applied_at=run.completed_at if run.status in ("applied", "reverted") else None,
        reverted_at=run.updated_at if run.status == "reverted" else None,
    )


def _load_diff_detail(run: CatalogImportRun) -> DiffDetail:
    """Fetch and validate the stored diff detail for a staged/applied run."""
    if not run.diff_detail_object_key:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run.id} has no staged diff yet (status: {run.status})",
        )
    import json as _json

    chunks = s3_service.download_blob_stream(run.diff_detail_object_key)
    if chunks is None:
        raise HTTPException(
            status_code=502,
            detail=f"Stored diff detail missing from object storage: {run.diff_detail_object_key}",
        )
    return DiffDetail.model_validate(_json.loads(b"".join(chunks)))


def _flatten_diff(detail: DiffDetail) -> List[DiffItem]:
    """One generic row per change, in stable entity/change-class order."""
    items: List[DiffItem] = []
    for entity in CatalogEntityType:
        diff = detail.entities.get(entity)
        if diff is None:
            continue
        for added in diff.added:
            items.append(
                DiffItem(
                    entity=entity,
                    change_class=ChangeClass.ADDED,
                    key=added.key,
                    name=added.name,
                    data=added.data,
                )
            )
        for changed in diff.changed:
            items.append(
                DiffItem(
                    entity=entity,
                    change_class=ChangeClass.CHANGED,
                    key=changed.key,
                    name=changed.name,
                    fields=changed.fields,
                )
            )
        for deprecated in diff.deprecated:
            items.append(
                DiffItem(
                    entity=entity,
                    change_class=ChangeClass.DEPRECATED,
                    key=deprecated.key,
                    name=deprecated.name,
                    superseded_by=deprecated.superseded_by,
                    suggestions=deprecated.suggestions,
                )
            )
        for resurrected in diff.resurrected:
            items.append(
                DiffItem(
                    entity=entity,
                    change_class=ChangeClass.RESURRECTED,
                    key=resurrected.key,
                    name=resurrected.name,
                    fields=resurrected.fields,
                )
            )
        for key in diff.unchanged:
            items.append(
                DiffItem(entity=entity, change_class=ChangeClass.UNCHANGED, key=key)
            )
    return items


def _versions_differ_and_newer(platform_version: Optional[str], reconciled: Optional[str]) -> bool:
    """Eligibility: platform (ledger) version is ahead of the org's (plan §4.3)."""
    if not platform_version:
        return False
    if not reconciled:
        return True
    parsed_platform = parse_version(platform_version)
    parsed_reconciled = parse_version(reconciled)
    if parsed_platform is not None and parsed_reconciled is not None:
        return parsed_platform > parsed_reconciled
    return platform_version != reconciled


# ---------------------------------------------------------------------------
# Upload (plan §4.2.1)
# ---------------------------------------------------------------------------


@router.post("/admin/catalog/upgrade", response_model=UpgradeUploadResponse, status_code=202)
async def upload_upgrade_workbook(
    file: UploadFile = File(...),
    force: bool = Query(
        False,
        description="Force-stage a same-version/downgrade workbook (forced-and-recorded, plan §4.2.2)",
    ),
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload a new SCF workbook; creates a run in 'staging' and enqueues
    the Celery staging task (plan §4.2.1)."""
    filename = file.filename or ""
    if not filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Upload must be a .xlsx file.")
    if file.content_type and file.content_type not in _ALLOWED_XLSX_TYPES:
        raise HTTPException(
            status_code=415, detail=f"Unsupported content type: {file.content_type}"
        )
    body = await file.read()
    if not body:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(body) > _MAX_XLSX_BYTES:
        raise HTTPException(status_code=413, detail="Workbook exceeds the 50 MB limit.")

    # One in-flight platform run (plan §4.1 M4). The partial unique index is
    # the authority; this pre-check turns the common case into a clean 409.
    result = await db.execute(
        select(CatalogImportRun).where(CatalogImportRun.status.in_(IN_FLIGHT_RUN_STATUSES))
    )
    in_flight = result.scalars().first()
    if in_flight is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"An upgrade run is already in flight "
                f"(run {in_flight.id}, status {in_flight.status}); "
                f"cancel it or wait for it to finish"
            ),
        )

    run = CatalogImportRun(
        to_version=TO_VERSION_PENDING,
        status="staging",
        started_by=UUID(user.db_id) if user.db_id else None,
    )
    db.add(run)
    try:
        await db.flush()  # assigns run.id for the object key
        object_key = f"{UPGRADE_OBJECT_PREFIX}/{run.id}/workbook.xlsx"
        try:
            s3_service.put_bytes(object_key, body, _XLSX_CONTENT_TYPE, _PLATFORM_ORG_TAG)
        except Exception as exc:  # noqa: BLE001 — surface a clean 502 to the operator
            logger.exception("Failed to stash upgrade workbook")
            raise HTTPException(
                status_code=502, detail="Could not store the uploaded workbook."
            ) from exc
        run.workbook_object_key = object_key
        await db.commit()
    except IntegrityError:
        # Lost the one-in-flight race to a concurrent upload.
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="An upgrade run is already in flight"
        )
    except HTTPException:
        await db.rollback()
        raise

    task = celery_app.send_task(
        "catalog.upgrade_stage",
        kwargs={"run_id": str(run.id), "force": force},
        queue="catalog",
    )
    logger.info(
        "Queued catalog upgrade staging for run %s (%s, %d bytes, force=%s)",
        run.id,
        filename,
        len(body),
        force,
    )
    return UpgradeUploadResponse(
        run_id=run.id, status=PlatformRunStatus.STAGING, task_id=task.id
    )


# ---------------------------------------------------------------------------
# Runs (ledger reads)
# ---------------------------------------------------------------------------


@router.get("/admin/catalog/upgrade/runs", response_model=PlatformImportRunsListResponse)
async def list_upgrade_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Platform import-run ledger, newest first."""
    total = (
        await db.execute(select(func.count()).select_from(CatalogImportRun))
    ).scalar() or 0
    result = await db.execute(
        select(CatalogImportRun)
        .order_by(CatalogImportRun.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    runs = result.scalars().all()
    return PlatformImportRunsListResponse(
        runs=[_run_to_summary(run) for run in runs], total=total
    )


@router.get("/admin/catalog/upgrade/runs/{run_id}", response_model=PlatformImportRunDetail)
async def get_upgrade_run(
    run_id: UUID,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """One run with sanity report, pairings, and diff summary."""
    run = await _get_run(db, run_id)
    return _run_to_detail(run)


@router.get("/admin/catalog/upgrade/runs/{run_id}/diff", response_model=DiffPageResponse)
async def get_upgrade_run_diff(
    run_id: UUID,
    entity: Optional[CatalogEntityType] = Query(None),
    change_class: Optional[ChangeClass] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Paginated, filterable field-level diff for a staged run (plan §4.2.3)."""
    run = await _get_run(db, run_id)
    detail = _load_diff_detail(run)

    items = _flatten_diff(detail)
    if entity is not None:
        items = [item for item in items if item.entity is entity]
    if change_class is not None:
        items = [item for item in items if item.change_class is change_class]

    total = len(items)
    start = (page - 1) * page_size
    return DiffPageResponse(
        run_id=run.id,
        items=items[start : start + page_size],
        total=total,
        page=page,
        page_size=page_size,
        entity=entity,
        change_class=change_class,
    )


# ---------------------------------------------------------------------------
# Pairings (plan §4.2.3) — destructive: real user session required
# ---------------------------------------------------------------------------


@router.put("/admin/catalog/upgrade/runs/{run_id}/pairings", response_model=PairingsUpdateResponse)
async def put_upgrade_run_pairings(
    run_id: UUID,
    body: PairingsUpdateRequest,
    user: User = Depends(require_platform_admin_user_session),
    db: AsyncSession = Depends(get_db),
):
    """Replace the admin-confirmed superseded_by pairings for the run's
    planned deprecations. Never auto-applied — the apply service writes and
    re-validates them inside the apply transaction."""
    run = await _get_run(db, run_id)
    if run.status != "staged":
        raise HTTPException(
            status_code=409,
            detail=f"Pairings can only be set on a staged run (status: {run.status})",
        )

    detail = _load_diff_detail(run)
    controls_diff = detail.entities.get(CatalogEntityType.CONTROLS)
    deprecated_keys = {d.key for d in controls_diff.deprecated} if controls_diff else set()

    unknown = sorted(
        {p.deprecated_scf_id for p in body.pairings} - deprecated_keys
    )
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Pairings reference controls this run does not deprecate: {', '.join(unknown)}",
        )

    successor_ids = sorted({p.superseded_by for p in body.pairings if p.superseded_by})
    if successor_ids:
        result = await db.execute(
            select(SCFCatalogControl).where(SCFCatalogControl.scf_id.in_(successor_ids))
        )
        active = {
            row.scf_id
            for row in result.scalars().all()
            if getattr(row, "status", "active") == "active"
        }
        invalid = sorted(set(successor_ids) - active)
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Successor controls missing or not active: {', '.join(invalid)}",
            )

    run.superseded_pairings = [p.model_dump() for p in body.pairings]
    await db.commit()
    logger.info(
        "Superseded pairings updated on run %s by %s (%d pairings)",
        run.id,
        user.email or user.user_id,
        len(body.pairings),
    )
    return PairingsUpdateResponse(run_id=run.id, pairings=body.pairings)


# ---------------------------------------------------------------------------
# Apply (plan §4.2.4) — destructive: real user session required
# ---------------------------------------------------------------------------


@router.post("/admin/catalog/upgrade/runs/{run_id}/apply", response_model=UpgradeApplyResponse, status_code=202)
async def apply_upgrade_run(
    run_id: UUID,
    body: UpgradeApplyRequest,
    user: User = Depends(require_platform_admin_user_session),
    db: AsyncSession = Depends(get_db),
):
    """Typed-confirm apply (plan §4.2.4)."""
    run = await _get_run(db, run_id)
    if run.status != "staged":
        raise HTTPException(
            status_code=409,
            detail=f"Only a staged run can be applied (status: {run.status})",
        )
    if body.expected_to_version != run.to_version:
        # Stale UI: the run on the server is not the one the admin reviewed.
        raise HTTPException(
            status_code=409,
            detail=(
                f"expected_to_version {body.expected_to_version!r} does not match "
                f"the run's to_version {run.to_version!r}"
            ),
        )
    if body.confirm_text != run.to_version:
        raise HTTPException(
            status_code=400,
            detail=f"Confirmation text must be exactly {run.to_version!r}",
        )

    task = celery_app.send_task(
        "catalog.upgrade_apply", kwargs={"run_id": str(run.id)}, queue="catalog"
    )
    logger.info(
        "Catalog apply enqueued for run %s (-> %s) by %s",
        run.id,
        run.to_version,
        user.email or user.user_id,
    )
    return UpgradeApplyResponse(
        run_id=run.id, status="applying", task_id=task.id, detail="apply enqueued"
    )


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


@router.post("/admin/catalog/upgrade/runs/{run_id}/cancel", response_model=UpgradeCancelResponse)
async def cancel_upgrade_run(
    run_id: UUID,
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a staged/blocked run before apply."""
    run = await _get_run(db, run_id)
    if run.status not in CANCELLABLE_RUN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run.id} cannot be cancelled from status {run.status!r}",
        )
    run.status = "cancelled"
    await db.commit()
    logger.info("Catalog upgrade run %s cancelled by %s", run.id, user.email or user.user_id)
    return UpgradeCancelResponse(run_id=run.id, status="cancelled")


# ---------------------------------------------------------------------------
# Revert (plan §4.2.6) — destructive: real user session required
# ---------------------------------------------------------------------------


@router.post("/admin/catalog/upgrade/runs/{run_id}/revert", response_model=UpgradeRevertResponse, status_code=202)
async def revert_upgrade_run(
    run_id: UUID,
    user: User = Depends(require_platform_admin_user_session),
    db: AsyncSession = Depends(get_db),
):
    """Revert the latest applied run (plan §4.2.6).

    Pre-flights the WP1b revert guards so refusals surface synchronously as
    409s; the Celery task re-checks them under the catalog advisory lock.
    """
    run = await _get_run(db, run_id)
    try:
        await _check_revert_allowed(db, run)
    except RevertBlockedError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "revert_blocked",
                "to_version": exc.to_version,
                "organization_ids": exc.blockers,
                "message": str(exc),
            },
        )
    except RevertNotLatestError as exc:
        raise HTTPException(
            status_code=409, detail={"error": "revert_not_latest", "message": str(exc)}
        )
    if not run.diff_detail_object_key:
        raise HTTPException(
            status_code=409,
            detail=f"Run {run.id} has no stored diff detail; cannot revert",
        )

    task = celery_app.send_task(
        "catalog.upgrade_revert", kwargs={"run_id": str(run.id)}, queue="catalog"
    )
    logger.info(
        "Catalog revert enqueued for run %s (back to %s) by %s",
        run.id,
        run.from_version,
        user.email or user.user_id,
    )
    return UpgradeRevertResponse(
        run_id=run.id, status="reverting", task_id=task.id, detail="revert enqueued"
    )


# ---------------------------------------------------------------------------
# Tenants reconciliation board (plan §4.5, §4.6)
# ---------------------------------------------------------------------------


@router.get("/admin/catalog/tenants", response_model=TenantsBoardResponse)
async def get_tenants_reconciliation_board(
    user: User = Depends(require_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Org × reconciled version × eligibility × active run (plan §4.6)."""
    platform_version = await get_current_catalog_version(db)

    orgs = (
        (await db.execute(select(Organization).order_by(Organization.name)))
        .scalars()
        .all()
    )
    states = {
        state.organization_id: state
        for state in (await db.execute(select(OrganizationCatalogState))).scalars().all()
    }
    active_runs = {
        run.organization_id: run
        for run in (
            await db.execute(
                select(OrganizationReconciliationRun).where(
                    OrganizationReconciliationRun.status.in_(ORG_ACTIVE_RUN_STATUSES)
                )
            )
        )
        .scalars()
        .all()
    }

    tenants = []
    for org in orgs:
        state = states.get(org.id)
        active = active_runs.get(org.id)
        reconciled = state.reconciled_catalog_version if state else None
        tenants.append(
            TenantBoardRow(
                organization_id=org.id,
                organization_name=org.name,
                reconciled_catalog_version=reconciled,
                last_reconciled_at=state.last_reconciled_at if state else None,
                eligible=_versions_differ_and_newer(platform_version, reconciled),
                active_run_id=active.id if active else None,
                active_run_status=active.status if active else None,
            )
        )
    return TenantsBoardResponse(
        platform_catalog_version=platform_version, tenants=tenants, total=len(tenants)
    )


# ---------------------------------------------------------------------------
# Post-apply superseded-by correction (plan §4.2.3 PATCH) — destructive
# ---------------------------------------------------------------------------


@router.patch("/admin/catalog/controls/{scf_id}/superseded-by", response_model=SupersededByPatchResponse)
async def patch_control_superseded_by(
    scf_id: str,
    body: SupersededByPatchRequest,
    user: User = Depends(require_platform_admin_user_session),
    db: AsyncSession = Depends(get_db),
):
    """Post-apply pairing correction, audit-logged (plan §4.2.3)."""
    scf_id = scf_id.upper()
    result = await db.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.scf_id == scf_id)
    )
    control = result.scalars().first()
    if control is None:
        raise HTTPException(status_code=404, detail=f"Control {scf_id} not found")
    if getattr(control, "status", "active") != "deprecated":
        raise HTTPException(
            status_code=409,
            detail=f"Control {scf_id} is not deprecated; superseded_by only applies to deprecated controls",
        )

    successor_id = body.superseded_by.upper() if body.superseded_by else None
    if successor_id is not None:
        if successor_id == scf_id:
            raise HTTPException(
                status_code=400, detail="A control cannot supersede itself"
            )
        result = await db.execute(
            select(SCFCatalogControl).where(SCFCatalogControl.scf_id == successor_id)
        )
        successor = result.scalars().first()
        if successor is None or getattr(successor, "status", "active") != "active":
            raise HTTPException(
                status_code=400,
                detail=f"Successor control {successor_id} missing or not active",
            )

    old_value = control.superseded_by
    control.superseded_by = successor_id
    await db.commit()
    # Catalog rows are platform-level (no organisation), so the org-scoped
    # audit_log table cannot hold this; the structured log line is the record.
    logger.info(
        "AUDIT catalog superseded_by patched: control=%s old=%s new=%s by=%s justification=%s",
        scf_id,
        old_value,
        successor_id,
        user.email or user.user_id,
        body.justification,
    )
    return SupersededByPatchResponse(scf_id=scf_id, superseded_by=successor_id)
