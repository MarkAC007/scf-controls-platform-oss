"""
Celery tasks for CG SCF.
Contains background job definitions for async processing.
"""
import os
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from celery import shared_task
from celery_app import celery_app

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="tasks.example_task")
def example_task(self, data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Example background task to demonstrate Celery setup.

    Args:
        data: Dictionary containing task parameters

    Returns:
        Dictionary with task result

    Usage:
        from tasks import example_task
        result = example_task.delay({"message": "Hello"})
        # or
        result = example_task.apply_async(
            args=[{"message": "Hello"}],
            countdown=10  # Delay 10 seconds
        )
    """
    task_id = self.request.id
    logger.info(f"Starting example_task[{task_id}] with data: {data}")

    try:
        # Simulate some work
        message = data.get("message", "No message provided")
        processed_at = datetime.utcnow().isoformat()

        result = {
            "task_id": task_id,
            "status": "completed",
            "message": f"Processed: {message}",
            "processed_at": processed_at,
            "input_data": data,
        }

        logger.info(f"Completed example_task[{task_id}]")
        return result

    except Exception as e:
        logger.error(f"Error in example_task[{task_id}]: {e}")
        raise


@shared_task(bind=True, name="tasks.health_check_task")
def health_check_task(self) -> Dict[str, Any]:
    """
    Periodic health check task.
    Verifies system components are working.
    """
    task_id = self.request.id
    logger.info(f"Running health_check_task[{task_id}]")

    checks = {
        "timestamp": datetime.utcnow().isoformat(),
        "task_id": task_id,
        "celery": "healthy",
    }

    # Check Redis connectivity (Celery broker)
    try:
        from redis_client import get_redis_client
        import asyncio

        async def check_redis():
            client = await get_redis_client()
            await client.ping()
            return "healthy"

        # Run async check in sync context
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            checks["redis"] = loop.run_until_complete(check_redis())
        finally:
            loop.close()

    except Exception as e:
        checks["redis"] = f"unhealthy: {str(e)}"
        logger.warning(f"Redis health check failed: {e}")

    # Check environment
    checks["environment"] = os.getenv("ENVIRONMENT", "unknown")

    logger.info(f"Health check completed: {checks}")
    return checks


@shared_task(bind=True, name="tasks.cleanup_task")
def cleanup_task(self) -> Dict[str, Any]:
    """
    Periodic cleanup task.
    Removes expired cache entries and performs maintenance.
    """
    task_id = self.request.id
    logger.info(f"Running cleanup_task[{task_id}]")

    result = {
        "timestamp": datetime.utcnow().isoformat(),
        "task_id": task_id,
        "actions": [],
    }

    try:
        from cache import get_cache_stats
        import asyncio

        async def get_stats():
            return await get_cache_stats()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            cache_stats = loop.run_until_complete(get_stats())
            result["cache_stats"] = cache_stats
            result["actions"].append("Retrieved cache statistics")
        finally:
            loop.close()

    except Exception as e:
        result["cache_error"] = str(e)
        logger.warning(f"Failed to get cache stats: {e}")

    logger.info(f"Cleanup task completed: {result}")
    return result


@shared_task(
    bind=True,
    name="tasks.send_notification_task",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_notification_task(
    self,
    notification_type: str,
    recipient: str,
    subject: str,
    body: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Send notification (email, etc.) as background task.

    Args:
        notification_type: Type of notification (email, webhook, etc.)
        recipient: Recipient address/endpoint
        subject: Notification subject
        body: Notification body
        metadata: Additional metadata

    Returns:
        Dictionary with send result
    """
    task_id = self.request.id
    logger.info(f"Sending {notification_type} notification[{task_id}] to {recipient}")

    try:
        result = {
            "task_id": task_id,
            "notification_type": notification_type,
            "recipient": recipient,
            "subject": subject,
            "status": "sent",
            "sent_at": datetime.utcnow().isoformat(),
        }

        # Implementation would go here based on notification type
        # For now, just log and return success
        if notification_type == "email":
            # TODO: Integrate with Resend email service
            logger.info(f"Would send email to {recipient}: {subject}")
            result["status"] = "queued"
        elif notification_type == "webhook":
            # TODO: Implement webhook delivery
            logger.info(f"Would send webhook to {recipient}")
            result["status"] = "queued"
        else:
            result["status"] = "unknown_type"
            logger.warning(f"Unknown notification type: {notification_type}")

        return result

    except Exception as e:
        logger.error(f"Failed to send notification[{task_id}]: {e}")
        raise


@shared_task(bind=True, name="tasks.process_bulk_operation")
def process_bulk_operation(
    self,
    operation: str,
    items: list,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Process bulk operations asynchronously.
    Useful for batch updates, imports, exports, etc.

    Args:
        operation: Type of operation (import, export, update, etc.)
        items: List of items to process
        options: Additional options for the operation

    Returns:
        Dictionary with operation results
    """
    task_id = self.request.id
    total_items = len(items)
    logger.info(f"Starting bulk {operation}[{task_id}] with {total_items} items")

    options = options or {}
    results = {
        "task_id": task_id,
        "operation": operation,
        "total": total_items,
        "processed": 0,
        "succeeded": 0,
        "failed": 0,
        "errors": [],
        "started_at": datetime.utcnow().isoformat(),
    }

    for i, item in enumerate(items):
        try:
            # Update progress
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": i + 1,
                    "total": total_items,
                    "percent": int((i + 1) / total_items * 100),
                },
            )

            # Process item (implementation would go here)
            # For demonstration, just count as success
            results["processed"] += 1
            results["succeeded"] += 1

        except Exception as e:
            results["failed"] += 1
            results["errors"].append({
                "index": i,
                "item": str(item)[:100],  # Truncate for logging
                "error": str(e),
            })
            logger.warning(f"Failed to process item {i}: {e}")

    results["completed_at"] = datetime.utcnow().isoformat()
    logger.info(f"Completed bulk {operation}[{task_id}]: {results['succeeded']}/{total_items} succeeded")

    return results


# Convenience functions for triggering tasks
def trigger_example_task(data: Dict[str, Any], delay_seconds: int = 0):
    """Trigger an example task."""
    if delay_seconds > 0:
        return example_task.apply_async(args=[data], countdown=delay_seconds)
    return example_task.delay(data)


def trigger_notification(
    notification_type: str,
    recipient: str,
    subject: str,
    body: str,
    metadata: Optional[Dict[str, Any]] = None,
):
    """Trigger a notification task."""
    return send_notification_task.delay(
        notification_type, recipient, subject, body, metadata
    )


def trigger_bulk_operation(
    operation: str,
    items: list,
    options: Optional[Dict[str, Any]] = None,
):
    """Trigger a bulk operation task."""
    return process_bulk_operation.delay(operation, items, options)
