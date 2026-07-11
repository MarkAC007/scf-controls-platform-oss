"""
Celery application configuration for CG SCF.
Provides background task processing capabilities.
"""
import os
import logging
from celery import Celery
from celery.schedules import crontab
from kombu import Queue, Exchange

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Azure Application Insights — initialize at module level so the OpenTelemetry
# logging handler is attached to the root logger BEFORE Celery starts.
# The setup_logging signal below prevents Celery from stripping this handler.
# ---------------------------------------------------------------------------
_appinsights_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
if _appinsights_conn:
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=_appinsights_conn)
        logger.info("Azure Application Insights configured")
    except Exception as e:
        logger.warning("Failed to configure Application Insights: %s", e)

# Celery configuration from environment
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# For rediss:// (TLS) URLs, Celery requires ssl_cert_reqs parameter
def _fix_rediss_url(url: str) -> str:
    """Append ssl_cert_reqs=CERT_NONE for ElastiCache TLS connections."""
    if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}ssl_cert_reqs=CERT_NONE"
    return url

CELERY_BROKER_URL = _fix_rediss_url(CELERY_BROKER_URL)
CELERY_RESULT_BACKEND = _fix_rediss_url(CELERY_RESULT_BACKEND)

# Also update os.environ so Celery's internal env var reads get the fixed URLs
os.environ["CELERY_BROKER_URL"] = CELERY_BROKER_URL
os.environ["CELERY_RESULT_BACKEND"] = CELERY_RESULT_BACKEND

# Create Celery application
celery_app = Celery(
    "scf_tasks",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "tasks",
        "tasks_research",
        "tasks_vendor_assessment",
        "tasks_recipe_generation",
        "tasks_cdm",
        "tasks_assessment",
        "tasks_window_assessment",
        "tasks_catalog",
        "services.composite_service",
    ],
)

# Configure SSL for rediss:// connections
_use_tls = CELERY_BROKER_URL.startswith("rediss://")
if _use_tls:
    import ssl
    _ssl_conf = {"ssl_cert_reqs": ssl.CERT_NONE}
    celery_app.conf.update(
        broker_use_ssl=_ssl_conf,
        redis_backend_use_ssl=_ssl_conf,
    )

# Broker + result-backend transport options.
# health_check_interval pings the underlying Redis socket every N seconds; when
# Redis force-reboots on the Azure side (Basic SKU has no replication, so its
# backend can recycle and leave clients holding dead sockets), the next health
# check detects the stale socket and reconnects transparently rather than
# blocking task enqueue indefinitely. socket_keepalive helps middleboxes (Front
# Door, NSGs) keep idle connections alive between bursts.
_REDIS_TRANSPORT_OPTS = {
    "socket_connect_timeout": 5,
    "socket_timeout": 30,
    "socket_keepalive": True,
    "retry_on_timeout": True,
    "health_check_interval": 30,
}

celery_app.conf.update(
    broker_transport_options=_REDIS_TRANSPORT_OPTS,
    result_backend_transport_options=_REDIS_TRANSPORT_OPTS,
    # Connection retry on startup (already default in 5.x but be explicit).
    broker_connection_retry_on_startup=True,
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,

    # Task execution settings
    task_acks_late=True,  # Acknowledge after task completion
    task_reject_on_worker_lost=True,  # Requeue if worker dies
    task_time_limit=600,  # 10 minute hard limit
    task_soft_time_limit=540,  # 9 minute soft limit (raises exception)

    # Worker settings
    worker_prefetch_multiplier=1,  # One task at a time per worker
    worker_max_tasks_per_child=100,  # Restart worker after 100 tasks (memory leak prevention)

    # Result backend settings
    result_expires=3600,  # Results expire after 1 hour
    result_extended=True,  # Store additional metadata

    # Retry settings
    task_default_retry_delay=60,  # 1 minute between retries
    task_max_retries=3,

    # Queue configuration
    task_queues=(
        Queue("default", Exchange("default"), routing_key="default"),
        Queue("high_priority", Exchange("high_priority"), routing_key="high_priority"),
        Queue("low_priority", Exchange("low_priority"), routing_key="low_priority"),
        Queue("tprm_research", Exchange("tprm_research"), routing_key="tprm_research"),
        Queue("dpsia", Exchange("dpsia"), routing_key="dpsia"),
        Queue("cdm", Exchange("cdm"), routing_key="cdm"),
        Queue("evidence_assessment", Exchange("evidence_assessment"), routing_key="evidence_assessment"),
        Queue("evidence_window", Exchange("evidence_window"), routing_key="evidence_window"),
        Queue("evidence_composite", Exchange("evidence_composite"), routing_key="evidence_composite"),
    ),
    task_default_queue="default",
    task_default_exchange="default",
    task_default_routing_key="default",

    # Task routing
    task_routes={
        "tasks.example_task": {"queue": "default"},
        "tasks.send_notification_task": {"queue": "high_priority"},
        "tasks.cleanup_task": {"queue": "low_priority"},
        "tasks_research.research_vendor_orchestrator": {"queue": "tprm_research"},
        "tasks_research.research_hibp": {"queue": "tprm_research"},
        "tasks_research.research_cisa_kev": {"queue": "tprm_research"},
        "tasks_research.research_cve_nvd": {"queue": "tprm_research"},
        "tasks_research.research_regulatory": {"queue": "tprm_research"},
        "tasks_research.research_aggregator": {"queue": "tprm_research"},
        "tasks_vendor_assessment.run_vendor_assessment": {"queue": "dpsia"},
        "tasks_assessment.assess_evidence_task": {"queue": "evidence_assessment"},
        "tasks_window_assessment.assess_window_task": {"queue": "evidence_window"},
        "tasks_window_assessment.nightly_window_refresh_task": {"queue": "evidence_window"},
        "services.composite_service.recompute_control_composite_task": {"queue": "evidence_composite"},
        "services.composite_service.backfill_all_composites_task": {"queue": "evidence_composite"},
    },

    # Beat scheduler (for periodic tasks)
    beat_schedule={
        "health-check-every-5-minutes": {
            "task": "tasks.health_check_task",
            "schedule": 300.0,  # 5 minutes
        },
        "cleanup-expired-cache-hourly": {
            "task": "tasks.cleanup_task",
            "schedule": 3600.0,  # 1 hour
        },
        # Nightly window refresh — runs at 04:00 UTC, after daily collectors
        # (which run 07:00-08:40 UTC the previous day). Gated by
        # WINDOW_ASSESSMENT_NIGHTLY_ENABLED so the job can be flipped off
        # without a code change.
        **(
            {
                "nightly-window-refresh": {
                    "task": "tasks_window_assessment.nightly_window_refresh_task",
                    "schedule": crontab(hour=4, minute=0),
                }
            }
            if os.getenv("WINDOW_ASSESSMENT_NIGHTLY_ENABLED", "false").lower() == "true"
            else {}
        ),
    },
)


def get_celery_app() -> Celery:
    """
    Get the Celery application instance.
    """
    return celery_app


# Task base class with common functionality
class BaseTask(celery_app.Task):
    """
    Base task class with error handling and logging.
    """
    abstract = True

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds."""
        logger.info(f"Task {self.name}[{task_id}] succeeded")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails."""
        logger.error(f"Task {self.name}[{task_id}] failed: {exc}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried."""
        logger.warning(f"Task {self.name}[{task_id}] retrying: {exc}")


# Export for use in tasks.py
celery_app.Task = BaseTask


# ---------------------------------------------------------------------------
# Prevent Celery from overriding the OpenTelemetry logging handler.
# Connecting to setup_logging tells Celery to skip its own logging config.
# We apply the --loglevel setting ourselves so Celery log level still works.
# ---------------------------------------------------------------------------
from celery.signals import setup_logging  # noqa: E402


@setup_logging.connect
def _preserve_otel_logging(loglevel=None, **kwargs):
    """Skip Celery's logging setup to preserve the OpenTelemetry handler."""
    if loglevel is not None:
        logging.root.setLevel(loglevel)
