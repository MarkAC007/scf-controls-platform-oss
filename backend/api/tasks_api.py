"""
API endpoints for background task management and cache operations.
Provides task status checking and cache administration endpoints.
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from pydantic import BaseModel

from auth import require_auth
from cache import get_cache_stats, invalidate_prefix, clear_all_cache, CacheNamespace
from redis_client import redis_health_check
from tasks import (
    example_task,
    trigger_example_task,
    trigger_notification,
    trigger_bulk_operation,
)
from celery_app import celery_app

router = APIRouter(
    prefix="/tasks",
    tags=["tasks"],
    dependencies=[Depends(require_auth)],
)


# Request/Response models
class TriggerTaskRequest(BaseModel):
    """Request body for triggering a task."""
    data: dict = {}
    delay_seconds: int = 0


class TriggerNotificationRequest(BaseModel):
    """Request body for triggering a notification."""
    notification_type: str = "email"
    recipient: str
    subject: str
    body: str
    metadata: Optional[dict] = None


class TriggerBulkOperationRequest(BaseModel):
    """Request body for triggering a bulk operation."""
    operation: str
    items: list
    options: Optional[dict] = None


class TaskStatusResponse(BaseModel):
    """Response model for task status."""
    task_id: str
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None


class CacheStatsResponse(BaseModel):
    """Response model for cache statistics."""
    cache_keys: int
    used_memory: str
    used_memory_peak: str
    cache_prefix: str
    cache_version: str


# Task management endpoints
@router.post("/trigger/example", response_model=TaskStatusResponse)
async def trigger_example(request: TriggerTaskRequest):
    """
    Trigger an example background task.
    Useful for testing Celery setup.
    """
    try:
        result = trigger_example_task(request.data, request.delay_seconds)
        return TaskStatusResponse(
            task_id=result.id,
            status="queued",
            result={"message": "Task queued successfully"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue task: {str(e)}")


@router.post("/trigger/notification", response_model=TaskStatusResponse)
async def trigger_notification_task(request: TriggerNotificationRequest):
    """
    Trigger a notification delivery task.
    """
    try:
        result = trigger_notification(
            request.notification_type,
            request.recipient,
            request.subject,
            request.body,
            request.metadata,
        )
        return TaskStatusResponse(
            task_id=result.id,
            status="queued",
            result={"message": "Notification queued for delivery"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue notification: {str(e)}")


@router.post("/trigger/bulk", response_model=TaskStatusResponse)
async def trigger_bulk_task(request: TriggerBulkOperationRequest):
    """
    Trigger a bulk operation task.
    """
    try:
        result = trigger_bulk_operation(
            request.operation,
            request.items,
            request.options,
        )
        return TaskStatusResponse(
            task_id=result.id,
            status="queued",
            result={"message": f"Bulk {request.operation} queued with {len(request.items)} items"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to queue bulk operation: {str(e)}")


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    Get the status of a background task by ID.
    """
    try:
        result = celery_app.AsyncResult(task_id)

        status = result.status
        task_result = None
        error = None

        if status == "SUCCESS":
            task_result = result.result
        elif status == "FAILURE":
            error = str(result.result) if result.result else "Unknown error"
        elif status == "PROGRESS":
            task_result = result.info

        return TaskStatusResponse(
            task_id=task_id,
            status=status,
            result=task_result,
            error=error,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get task status: {str(e)}")


@router.delete("/revoke/{task_id}")
async def revoke_task(task_id: str, terminate: bool = False):
    """
    Revoke (cancel) a pending or running task.

    Args:
        task_id: The task ID to revoke
        terminate: If True, forcefully terminate the task even if running
    """
    try:
        celery_app.control.revoke(task_id, terminate=terminate)
        return {
            "success": True,
            "task_id": task_id,
            "message": f"Task revoked (terminate={terminate})",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to revoke task: {str(e)}")


# Cache management endpoints
@router.get("/cache/stats")
async def cache_stats():
    """
    Get cache statistics including key count and memory usage.
    """
    try:
        stats = await get_cache_stats()
        return {
            "success": True,
            "stats": stats,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get cache stats: {str(e)}")


@router.delete("/cache/invalidate")
async def invalidate_cache_endpoint(
    prefix: Optional[str] = Query(None, description="Cache prefix to invalidate (e.g., 'controls', 'evidence')"),
    all: bool = Query(False, description="Clear all cache entries (use with caution)"),
):
    """
    Invalidate cache entries.

    - Provide `prefix` to invalidate all keys with that prefix
    - Set `all=true` to clear all cache entries (requires confirmation)
    """
    try:
        if all:
            count = await clear_all_cache()
            return {
                "success": True,
                "message": f"Cleared all cache entries",
                "keys_deleted": count,
            }
        elif prefix:
            count = await invalidate_prefix(prefix)
            return {
                "success": True,
                "message": f"Invalidated cache entries with prefix '{prefix}'",
                "keys_deleted": count,
            }
        else:
            raise HTTPException(
                status_code=400,
                detail="Must provide either 'prefix' parameter or 'all=true'",
            )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to invalidate cache: {str(e)}")


# Infrastructure health endpoints
@router.get("/health/redis")
async def redis_health():
    """
    Get detailed Redis health information.
    """
    try:
        health = await redis_health_check()
        return {
            "success": True,
            "redis": health,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to check Redis health: {str(e)}")


@router.get("/health/celery")
async def celery_health():
    """
    Get Celery worker health information.
    """
    try:
        # Ping workers
        ping_response = celery_app.control.ping(timeout=5)

        # Get active tasks
        inspect = celery_app.control.inspect()
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}

        worker_count = len(ping_response) if ping_response else 0
        active_tasks = sum(len(tasks) for tasks in active.values())
        reserved_tasks = sum(len(tasks) for tasks in reserved.values())
        scheduled_tasks = sum(len(tasks) for tasks in scheduled.values())

        return {
            "success": True,
            "celery": {
                "status": "healthy" if worker_count > 0 else "no_workers",
                "workers": worker_count,
                "active_tasks": active_tasks,
                "reserved_tasks": reserved_tasks,
                "scheduled_tasks": scheduled_tasks,
            },
        }
    except Exception as e:
        return {
            "success": False,
            "celery": {
                "status": "unhealthy",
                "error": str(e),
            },
        }
