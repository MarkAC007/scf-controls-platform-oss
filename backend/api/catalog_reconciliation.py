"""Org-level catalog reconciliation API (plan §4.3, §4.5).

Read side (status, preview, runs, actions PUT, changelog) implemented by WP2b;
apply, rollback and cancel by WP2c — apply and rollback pre-flight the guards
synchronously (409s instead of async task failures) then enqueue the Celery
tasks in ``tasks_reconciliation.py``. Contracts live in
backend/schemas_catalog_upgrade.py — import, never redefine.

Auth (plan §4.5): reconciliation operations use
``require_org_admin_or_platform_admin`` — org admins (members or consultants)
plus platform admins with a real user identity, so the platform console can
operate tenant-by-tenant. The org-visible status and changelog reads stay at
``require_org_role("viewer")``.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_admin_or_platform_admin, require_org_role
from celery_app import celery_app
from database import get_db
from schemas_catalog_upgrade import (
    DiffSummary,
    OrgCatalogStatusResponse,
    OrgChangelogResponse,
    OrgReconciliationRunDetail,
    OrgReconciliationRunSummary,
    OrgReconciliationRunsListResponse,
    PlannedAction,
    ReconciliationActionsUpdateRequest,
    ReconciliationActionsUpdateResponse,
    ReconciliationApplyRequest,
    ReconciliationApplyResponse,
    ReconciliationCancelResponse,
    ReconciliationPreviewRequest,
    ReconciliationPreviewResponse,
    ReconciliationRollbackRequest,
    ReconciliationRollbackResponse,
)
from services import reconciliation_service as recon

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog-reconciliation"])


def _actor_user_id(membership: OrgMembership) -> Optional[UUID]:
    db_id = membership.user.db_id
    return UUID(db_id) if db_id else None


def _run_summary(run) -> OrgReconciliationRunSummary:
    return OrgReconciliationRunSummary.model_validate(run)


def _run_detail(run) -> OrgReconciliationRunDetail:
    summary = _run_summary(run)
    return OrgReconciliationRunDetail(
        **summary.model_dump(),
        diff_summary=(
            DiffSummary.model_validate(run.diff_summary) if run.diff_summary else None
        ),
        planned_actions=[
            PlannedAction.model_validate(a) for a in (run.planned_actions or [])
        ],
        actions_log=list(run.actions_log or []),
        error=None,
        applied_at=run.completed_at if run.status == "applied" else None,
        rolled_back_at=run.completed_at if run.status == "rolled_back" else None,
    )


@router.get(
    "/organizations/{org_id}/catalog-reconciliation/status",
    response_model=OrgCatalogStatusResponse,
)
async def get_reconciliation_status(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Org's reconciled version vs the platform version — drives the
    org-visible version card and availability banner (plan §4.6)."""
    info = await recon.check_eligibility(db, org_id)
    return OrgCatalogStatusResponse(
        organization_id=org_id,
        reconciled_catalog_version=info.reconciled_catalog_version,
        platform_catalog_version=info.platform_catalog_version,
        eligible=info.eligible,
        last_reconciled_at=info.last_reconciled_at,
        active_run=_run_summary(info.active_run) if info.active_run else None,
        first_reconciliation=info.first_reconciliation,
    )


@router.post(
    "/organizations/{org_id}/catalog-reconciliation/preview",
    response_model=ReconciliationPreviewResponse,
)
async def create_reconciliation_preview(
    org_id: UUID,
    body: ReconciliationPreviewRequest,
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Synchronous org-impact preview; creates a run in 'previewed'
    (plan §4.3 branches a–e). A previous previewed run is superseded."""
    try:
        result = await recon.build_preview(
            db,
            org_id,
            user_id=_actor_user_id(membership),
            target_version=body.target_version,
        )
    except (recon.NotEligibleError, recon.ActiveRunConflictError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except recon.DiffDetailUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    return ReconciliationPreviewResponse(
        run=_run_summary(result.run),
        additions=result.additions,
        deprecated_impacts=result.deprecated_impacts,
        changed_in_scope=result.changed_in_scope,
        orphans=result.orphans,
        framework_confirmation=result.framework_confirmation,
    )


@router.get(
    "/organizations/{org_id}/catalog-reconciliation/runs",
    response_model=OrgReconciliationRunsListResponse,
)
async def list_reconciliation_runs(
    org_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """The org's reconciliation-run ledger, newest first."""
    runs = await recon.list_org_runs(db, org_id)
    return OrgReconciliationRunsListResponse(
        runs=[_run_summary(r) for r in runs[offset : offset + limit]],
        total=len(runs),
    )


@router.get(
    "/organizations/{org_id}/catalog-reconciliation/runs/{run_id}",
    response_model=OrgReconciliationRunDetail,
)
async def get_reconciliation_run(
    org_id: UUID,
    run_id: UUID,
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """One run with planned actions, diff summary, and actions log."""
    try:
        run = await recon.get_org_run(db, org_id, run_id)
    except recon.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _run_detail(run)


@router.put(
    "/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/actions",
    response_model=ReconciliationActionsUpdateResponse,
)
async def put_reconciliation_actions(
    org_id: UUID,
    run_id: UUID,
    body: ReconciliationActionsUpdateRequest,
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Replace the run's planned actions (migrate/retain/retire_only) and,
    on the first reconciliation, record the confirmed framework list."""
    try:
        run = await recon.update_planned_actions(
            db,
            org_id,
            run_id,
            body.actions,
            confirmed_framework_ids=body.confirmed_framework_ids,
            user_id=_actor_user_id(membership),
        )
    except recon.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except recon.RunStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except recon.ActionValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors)
    return ReconciliationActionsUpdateResponse(
        run_id=run.id,
        actions=[PlannedAction.model_validate(a) for a in (run.planned_actions or [])],
    )


@router.post(
    "/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/apply",
    response_model=ReconciliationApplyResponse,
    status_code=202,
)
async def apply_reconciliation_run(
    org_id: UUID,
    run_id: UUID,
    body: ReconciliationApplyRequest,
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue the org apply (plan §4.3) — guarded by run status 'previewed'
    and stale-preview refusal. The Celery task re-checks every guard under
    the dual advisory locks."""
    try:
        run = await recon.check_apply_preflight(
            db, org_id, run_id, expected_to_version=body.expected_to_version
        )
    except recon.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (
        recon.RunStateError,
        recon.StalePreviewError,
        recon.FrameworksNotConfirmedError,
        recon.ActiveRunConflictError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    user_id = _actor_user_id(membership)
    task = celery_app.send_task(
        "org.reconcile_apply",
        kwargs={
            "run_id": str(run.id),
            "organization_id": str(org_id),
            "user_id": str(user_id) if user_id else None,
        },
        queue="catalog",
    )
    logger.info(
        "Reconciliation apply enqueued: org=%s run=%s -> %s",
        org_id, run.id, run.to_version,
    )
    return ReconciliationApplyResponse(
        run_id=run.id, status="applying", task_id=task.id, detail="apply enqueued"
    )


@router.post(
    "/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/rollback",
    response_model=ReconciliationRollbackResponse,
    status_code=202,
)
async def rollback_reconciliation_run(
    org_id: UUID,
    run_id: UUID,
    body: ReconciliationRollbackRequest,
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Snapshot-restore rollback of the latest applied run (plan §4.3),
    behind a typed confirmation (plan §4.6)."""
    try:
        run = await recon.check_rollback_preflight(db, org_id, run_id)
    except recon.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except (
        recon.RunStateError,
        recon.RollbackNotLatestError,
        recon.ActiveRunConflictError,
        recon.SnapshotUnavailableError,
    ) as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    if body.confirm_text != run.to_version:
        raise HTTPException(
            status_code=422,
            detail=f"Confirmation text must be exactly {run.to_version!r}",
        )

    user_id = _actor_user_id(membership)
    task = celery_app.send_task(
        "org.reconcile_rollback",
        kwargs={
            "run_id": str(run.id),
            "organization_id": str(org_id),
            "user_id": str(user_id) if user_id else None,
        },
        queue="catalog",
    )
    logger.info(
        "Reconciliation rollback enqueued: org=%s run=%s back to %s",
        org_id, run.id, run.from_version,
    )
    return ReconciliationRollbackResponse(
        run_id=run.id,
        status="rolling_back",
        task_id=task.id,
        detail="rollback enqueued",
    )


@router.post(
    "/organizations/{org_id}/catalog-reconciliation/runs/{run_id}/cancel",
    response_model=ReconciliationCancelResponse,
)
async def cancel_reconciliation_run(
    org_id: UUID,
    run_id: UUID,
    membership: OrgMembership = Depends(require_org_admin_or_platform_admin),
    db: AsyncSession = Depends(get_db),
):
    """Cancel a previewed run before apply."""
    try:
        run = await recon.cancel_reconciliation_run(
            db, org_id, run_id, user_id=_actor_user_id(membership)
        )
    except recon.RunNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except recon.RunStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    logger.info("Reconciliation run %s cancelled (org=%s)", run.id, org_id)
    return ReconciliationCancelResponse(run_id=run.id, status="cancelled")


@router.get(
    "/organizations/{org_id}/catalog-changelog",
    response_model=OrgChangelogResponse,
)
async def get_org_catalog_changelog(
    org_id: UUID,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Read-only org changelog of applied catalog changes (plan §4.6)."""
    try:
        entries, total = await recon.assemble_changelog(
            db, org_id, limit=limit, offset=offset
        )
    except recon.DiffDetailUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return OrgChangelogResponse(organization_id=org_id, entries=entries, total=total)
