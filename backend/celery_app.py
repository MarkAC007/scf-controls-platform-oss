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
# The setup_logging signal below prevents Celery from stripping this handler,
# but ONLY when that handler actually exists — see _OTEL_LOGGING_ACTIVE.
# ---------------------------------------------------------------------------

#: True only when configure_azure_monitor() ran to completion and therefore
#: left a handler on the root logger.
#:
#: This is deliberately keyed on the OUTCOME, not on the intent. Guarding on
#: `_appinsights_conn` being set would still suppress Celery's logging setup
#: when the azure package is missing from the image or the connection string is
#: malformed — exactly the moments when you most need the logs to explain why.
_OTEL_LOGGING_ACTIVE = False

_appinsights_conn = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
if _appinsights_conn:
    try:
        from azure.monitor.opentelemetry import configure_azure_monitor
        configure_azure_monitor(connection_string=_appinsights_conn)
        _OTEL_LOGGING_ACTIVE = True
        logger.info("Azure Application Insights configured")
    except Exception as e:
        logger.warning("Failed to configure Application Insights: %s", e)

# Celery configuration from environment
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/1")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

# For rediss:// (TLS) URLs, Celery requires ssl_cert_reqs parameter.
# Lives in redis_url.py so the beat healthcheck — which deliberately does not
# import this module — normalises identically. Without that, the probe would
# verify certificates against an endpoint beat itself connects to with
# verification off, and fail the handshake against a healthy scheduler (#784).
from redis_url import fix_rediss_url as _fix_rediss_url  # noqa: E402

CELERY_BROKER_URL = _fix_rediss_url(CELERY_BROKER_URL)
CELERY_RESULT_BACKEND = _fix_rediss_url(CELERY_RESULT_BACKEND)

# Also update os.environ so Celery's internal env var reads get the fixed URLs
os.environ["CELERY_BROKER_URL"] = CELERY_BROKER_URL
os.environ["CELERY_RESULT_BACKEND"] = CELERY_RESULT_BACKEND


def _flag_enabled(env_name: str, default: str) -> bool:
    """Beat gate: enabled unless the env var is explicitly false/0."""
    return os.getenv(env_name, default).strip().lower() not in ("false", "0")

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
        "tasks_reconciliation",
        "tasks_updates",
        "tasks_automation",
        "tasks_doc_gen",
        "tasks_evidence_integrity",
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
# Redis restarts (container restart, `docker compose restart redis`, host
# reboot) clients can be left holding dead sockets, and the next health check
# detects the stale socket and reconnects transparently rather than blocking
# task enqueue indefinitely. socket_keepalive keeps idle connections alive
# between bursts when Redis is reached across a network hop.
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
    # Without this, a running task reads as PENDING from AsyncResult — the
    # status endpoints cannot tell "in progress" from "no such task". With
    # acks_late a SIGKILLed worker can leave STARTED behind until
    # result_expires purges it; that is the acceptable trade.
    task_track_started=True,

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
        # Separate from "cdm" on purpose: a hosted classification call of tens
        # of seconds sitting on the cdm queue head-of-line-blocks every ingest
        # queued behind it.
        Queue("cdm_intent", Exchange("cdm_intent"), routing_key="cdm_intent"),
        Queue("evidence_assessment", Exchange("evidence_assessment"), routing_key="evidence_assessment"),
        Queue("evidence_window", Exchange("evidence_window"), routing_key="evidence_window"),
        Queue("evidence_composite", Exchange("evidence_composite"), routing_key="evidence_composite"),
        # Catalog imports + the staged upgrade flow (tasks_catalog.py). Its own
        # queue so a long apply cannot head-of-line-block default work; already
        # in the compose worker's -Q list (docker-compose.yml:206).
        Queue("catalog", Exchange("catalog"), routing_key="catalog"),
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
        # Route automation jobs to default deliberately: default is the one
        # queue every Celery worker consumes out of the box, so these jobs
        # still run even under a worker started as a bare
        # `celery -A celery_app worker` with no -Q list (an operator
        # debugging, or a compose override that trims the -Q list).
        "tasks_automation.generate_evidence_tasks_task": {"queue": "default"},
        "tasks_automation.notify_due_tasks_task": {"queue": "default"},
        "tasks_automation.notify_overdue_tasks_task": {"queue": "default"},
        # Document generation routes to default for the same reason as the
        # automation tasks above: a dedicated queue only works if every worker
        # launch command remembers to subscribe to it. A queue that works with
        # the stock compose -Q list and is silently dead under any other
        # worker invocation is worse than sharing default.
        "doc_gen.generate": {"queue": "default"},
        # Evidence integrity verification routes to default for the same reason
        # again (#57). This one matters most of the three: a malware scan whose
        # queue no running worker consumes would show a green control on the
        # dashboard while nothing was ever scanned.
        "tasks_evidence_integrity.verify_evidence_file_task": {"queue": "default"},
        "tasks_evidence_integrity.sweep_unverified_evidence_task": {"queue": "default"},
        "cdm.classify_intent": {"queue": "cdm_intent"},
        # Catalog upgrade flow (WP1b). catalog.import routes itself via its
        # task decorator (queue="catalog"); the upgrade tasks route here.
        "catalog.upgrade_stage": {"queue": "catalog"},
        "catalog.upgrade_apply": {"queue": "catalog"},
        "catalog.upgrade_revert": {"queue": "catalog"},
        "catalog.cleanup_workbooks": {"queue": "catalog"},
        # Per-org reconciliation (WP2c, tasks_reconciliation.py): same queue —
        # org applies serialise behind platform catalog work by design.
        "org.reconcile_apply": {"queue": "catalog"},
        "org.reconcile_rollback": {"queue": "catalog"},
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
        # (which run 07:00-08:40 UTC the previous day). Defaults ON; set
        # WINDOW_ASSESSMENT_NIGHTLY_ENABLED=false/0 to opt out.
        **(
            {
                "nightly-window-refresh": {
                    "task": "tasks_window_assessment.nightly_window_refresh_task",
                    "schedule": crontab(hour=4, minute=0),
                }
            }
            if _flag_enabled("WINDOW_ASSESSMENT_NIGHTLY_ENABLED", "true")
            else {}
        ),
        # Daily GRC automation cadence: generate evidence tasks at 01:00 UTC so
        # fresh tasks are visible to the 07:00 due-notifier the same morning;
        # run overdue notifications at 07:15 so notifier sweeps do not contend.
        **(
            {
                "evidence-task-generation-daily": {
                    "task": "tasks_automation.generate_evidence_tasks_task",
                    "schedule": crontab(hour=1, minute=0),
                },
                "task-due-notifications-daily": {
                    "task": "tasks_automation.notify_due_tasks_task",
                    "schedule": crontab(hour=7, minute=0),
                },
                "task-overdue-notifications-daily": {
                    "task": "tasks_automation.notify_overdue_tasks_task",
                    "schedule": crontab(hour=7, minute=15),
                },
            }
            if _flag_enabled("TASK_AUTOMATION_ENABLED", "true")
            else {}
        ),
        # Evidence integrity backlog drain (#57). Every file that predates the
        # verification feature sits at hash_verification_status='pending'; this
        # tick hands the oldest batch to the verifier. Hourly rather than daily
        # because a large backlog should drain in days, not months, and because
        # each tick is bounded by EVIDENCE_INTEGRITY_SWEEP_BATCH and the
        # verifier's own rate_limit. Once the backlog is empty the query returns
        # no rows against a partial index that has shrunk to nothing, so the
        # steady-state cost of leaving this on is a no-op every hour.
        **(
            {
                "evidence-integrity-sweep-hourly": {
                    "task": "tasks_evidence_integrity.sweep_unverified_evidence_task",
                    "schedule": crontab(minute=20),
                }
            }
            if _flag_enabled("EVIDENCE_INTEGRITY_SWEEP_ENABLED", "true")
            else {}
        ),
        # Catalog upgrade workbook retention (plan §4.2.7): keep the last 5
        # runs' stashed workbooks, delete older ones nightly. Diff details are
        # never cleaned up — they are the platform revert anchor.
        "catalog-workbook-cleanup-daily": {
            "task": "catalog.cleanup_workbooks",
            "schedule": crontab(hour=3, minute=30),
        },
        # Daily platform update check (upgrade design Part B) — polls the public
        # GitHub Releases API at 02:00 UTC and caches the result in Redis for the
        # /api/version endpoint. Gated by SCF_UPDATE_CHECK, but INVERTED relative
        # to the window-refresh flag: included unless explicitly disabled, so the
        # opt-out (air-gapped installs) is the only way to switch it off.
        **(
            {
                "update-check-daily": {
                    "task": "tasks_updates.check_latest_release",
                    "schedule": crontab(hour=2, minute=0),
                }
            }
            if os.getenv("SCF_UPDATE_CHECK", "true").strip().lower() not in ("false", "0")
            else {}
        ),
    },
)


# ---------------------------------------------------------------------------
# Beat liveness (#784).
#
# Set here rather than as a `-S` flag on the command line so the configuration
# travels with the app instead of having to be remembered in every launcher —
# docker-compose.yml's celery-beat service, and any beat an operator starts by
# hand.
#
# An explicit `-S`/`--scheduler` still overrides this — correct precedence for
# an operator. If you add a `--scheduler` flag anywhere, you are opting that
# deployment out of beat liveness; tests/test_celery_observability.py fails
# the build if you do.
#
# The CLASS, not a "module.Class" string: celery resolves the string form with
# kombu's symbol_by_name, which uses plain importlib and — unlike the
# `import_from_cwd` that `-A celery_app` gets — does NOT put the working
# directory on the path. A string here therefore starts beat only where
# PYTHONPATH already contains the backend dir. The image sets PYTHONPATH=/app
# so containers were safe, but `celery -A celery_app beat` run straight from
# backend/ would have died with ModuleNotFoundError. symbol_by_name returns a
# non-string unchanged, so passing the class is both supported and immune.
# ---------------------------------------------------------------------------
from celery_beat_scheduler import HeartbeatScheduler  # noqa: E402

celery_app.conf.beat_scheduler = HeartbeatScheduler


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
# Prevent Celery from overriding the OpenTelemetry logging handler — but only
# when there IS one (#784).
#
# The mechanism here is easy to misread: merely having a receiver connected to
# `setup_logging` is what tells Celery to skip its own logging configuration.
# The receiver's body is irrelevant to that decision. So connecting this
# unconditionally meant that on every install without Application Insights —
# the default for a docker-compose install — Celery skipped
# its setup, nothing else attached a handler to root, and Python fell back to
# `logging.lastResort`: stderr, WARNING and above, no formatter.
#
# The visible symptom was that `--loglevel=info` was accepted and did nothing.
# Every INFO line the tasks emit — which collector ran, which evidence was
# assessed, which window was refreshed — was discarded, and the only trace a
# scheduled job left behind was its absence.
#
# Registering conditionally hands the default case back to Celery, which is
# strictly better than a hand-rolled StreamHandler: it installs Celery's own
# task-aware formatter, so lines carry the task name and id.
# ---------------------------------------------------------------------------
from celery.signals import setup_logging  # noqa: E402
from log_sanitizer import attach_to_handlers as _attach_log_sanitizer  # noqa: E402


def _preserve_otel_logging(loglevel=None, **kwargs):
    """Skip Celery's logging setup to preserve the OpenTelemetry handler.

    Connected only when `_OTEL_LOGGING_ACTIVE` — see above.
    """
    if loglevel is not None:
        logging.root.setLevel(loglevel)
    # Celery emits after_setup_logger / after_setup_task_logger only inside the
    # `if not receivers:` branch of Logging.setup_logging_subsystem — i.e. only
    # when nothing is connected to setup_logging. Connecting THIS receiver is
    # what suppresses them, so on an install with App Insights configured (the
    # only case this receiver is connected) the worker would otherwise still
    # have no CR/LF filter: precisely the gap the block below claims to close.
    # Attach it here too; the OTel handler is already on root by this point.
    _attach_log_sanitizer()


if _OTEL_LOGGING_ACTIVE:
    setup_logging.connect(_preserve_otel_logging)
else:
    logger.debug(
        "Application Insights not configured — leaving Celery's own logging "
        "setup in place so --loglevel is honoured."
    )


# ---------------------------------------------------------------------------
# Log-forging defence for the worker processes.
#
# `main.py` has attached a CR/LF-escaping filter to the root handlers since the
# py/log-injection remediation, but the Celery worker does not import `main` —
# so the worker never had it. That was invisible while the worker's INFO
# logging was being thrown away; re-enabling that logging above is what makes
# it matter, so it is fixed here rather than deferred.
#
# after_setup_logger / after_setup_task_logger fire AFTER Celery has installed
# its own handlers, which is when there is something to attach to. Both are
# needed: Celery gives task loggers their own handler.
#
# These cover the NO-App-Insights path only. Celery sends them from inside the
# `if not receivers:` branch of Logging.setup_logging_subsystem, so they are
# silent exactly when `_preserve_otel_logging` is connected — which is why that
# receiver attaches the filter itself. Both paths are asserted by
# tests/test_celery_observability.py::TestSanitizerReachesTheWorker.
# ---------------------------------------------------------------------------
from celery.signals import after_setup_logger, after_setup_task_logger  # noqa: E402


@after_setup_logger.connect
def _sanitize_worker_logger(logger=None, **kwargs):
    _attach_log_sanitizer(logger)


@after_setup_task_logger.connect
def _sanitize_task_logger(logger=None, **kwargs):
    _attach_log_sanitizer(logger)
