"""Celery observability — logging gate and beat liveness (#784).

Two defects, one issue, because together they were silent:

1. ``celery_app`` connected a no-op receiver to Celery's ``setup_logging``
   signal unconditionally. Being connected at all is what tells Celery to skip
   its own logging configuration, so on every deployment without Application
   Insights nothing attached a handler to root and ``logging.lastResort`` ate
   everything below WARNING. ``--loglevel=info`` was accepted and ignored.

2. The beat container's healthcheck was ``grep -q beat /proc/1/cmdline``, which
   is true of a wedged scheduler as well as a working one.

So beat could stop scheduling and neither the probe nor the logs would say so.

The env-sensitive cases run in a SUBPROCESS rather than reloading
``celery_app`` in-process: the flag and the signal connection are both frozen
at import time, and reloading a module the rest of the suite already imported
would leak state into unrelated tests.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent


def _probe_import(env_overrides, fake_azure_succeeds=None):
    """Import celery_app in a clean interpreter and report what it decided.

    ``fake_azure_succeeds`` installs a stub ``azure.monitor.opentelemetry``
    module whose ``configure_azure_monitor`` either returns (True) or raises
    (False). ``None`` installs no stub, so the real import is attempted and —
    in CI, where the package is absent — fails.
    """
    stub = ""
    if fake_azure_succeeds is not None:
        body = (
            # The real configure_azure_monitor attaches an OTel handler to root;
            # the stub must too, or "did the sanitizer land on a handler" is
            # vacuous on this path.
            "    import logging; logging.root.addHandler(logging.StreamHandler())"
            if fake_azure_succeeds
            else "    raise RuntimeError('bad connection string')"
        )
        stub = (
            "import sys, types\n"
            "pkg = types.ModuleType('azure'); pkg.__path__ = []\n"
            "mon = types.ModuleType('azure.monitor'); mon.__path__ = []\n"
            "otel = types.ModuleType('azure.monitor.opentelemetry')\n"
            "def configure_azure_monitor(**kwargs):\n"
            f"{body}\n"
            "otel.configure_azure_monitor = configure_azure_monitor\n"
            "sys.modules['azure'] = pkg\n"
            "sys.modules['azure.monitor'] = mon\n"
            "sys.modules['azure.monitor.opentelemetry'] = otel\n"
        )

    script = stub + (
        "import json, logging, sys\n"
        "sys.path.insert(0, '.')\n"
        "import celery_app as ca\n"
        "from celery.signals import setup_logging, after_setup_logger, after_setup_task_logger\n"
        # Actually run Celery's logging setup, the way a worker does at boot.
        "ca.celery_app.log.setup(loglevel=logging.INFO)\n"
        "handlers = logging.root.handlers\n"
        "print('@@' + json.dumps({\n"
        "    'otel_active': ca._OTEL_LOGGING_ACTIVE,\n"
        "    'setup_logging_receivers': len(setup_logging.receivers),\n"
        "    'after_setup_logger_receivers': len(after_setup_logger.receivers),\n"
        "    'after_setup_task_logger_receivers': len(after_setup_task_logger.receivers),\n"
        "    'beat_scheduler': getattr(ca.celery_app.conf.beat_scheduler, '__name__', ca.celery_app.conf.beat_scheduler),\n"
        "    'root_handlers': [type(h).__name__ for h in handlers],\n"
        "    'sanitizer_attached': any(\n"
        "        any(type(f).__name__ == 'LogForgingSanitizer' for f in h.filters)\n"
        "        for h in handlers\n"
        "    ),\n"
        "    'root_level': logging.getLevelName(logging.root.level),\n"
        "}))\n"
    )

    env = dict(os.environ)
    env.pop("APPLICATIONINSIGHTS_CONNECTION_STRING", None)
    env.update(env_overrides)
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"import failed:\n{proc.stdout}\n{proc.stderr}"
    line = next(l for l in proc.stdout.splitlines() if l.startswith("@@"))
    return json.loads(line[2:])


# ---------------------------------------------------------------------------
# 1. The logging gate
# ---------------------------------------------------------------------------

class TestOtelLoggingGate:
    def test_no_appinsights_leaves_celery_logging_alone(self):
        """The regression that mattered: no App Insights => Celery configures logging.

        If this fails, `--loglevel=info` silently stops working in every
        docker-compose and self-hosted install, and every INFO line the
        collectors emit disappears.
        """
        result = _probe_import({})
        assert result["otel_active"] is False
        assert result["setup_logging_receivers"] == 0

    def test_failed_configure_does_not_suppress_logging(self):
        """Guarded on the OUTCOME, not the intent.

        A connection string that is set but unusable — malformed value, azure
        package missing from the image — is exactly when the logs need to
        survive to explain the failure.
        """
        result = _probe_import(
            {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=nope"},
            fake_azure_succeeds=False,
        )
        assert result["otel_active"] is False
        assert result["setup_logging_receivers"] == 0

    def test_missing_azure_package_does_not_suppress_logging(self):
        result = _probe_import(
            {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=nope"},
            fake_azure_succeeds=None,
        )
        assert result["otel_active"] is False
        assert result["setup_logging_receivers"] == 0

    def test_successful_configure_still_preserves_the_otel_handler(self):
        """The original behaviour must survive: installs with App Insights configured still opt out."""
        result = _probe_import(
            {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc"},
            fake_azure_succeeds=True,
        )
        assert result["otel_active"] is True
        assert result["setup_logging_receivers"] == 1

    def test_preserve_receiver_still_applies_loglevel(self):
        import celery_app

        with mock.patch("logging.root") as root:
            celery_app._preserve_otel_logging(loglevel=20)
        root.setLevel.assert_called_once_with(20)

    def test_preserve_receiver_tolerates_no_loglevel(self):
        import celery_app

        celery_app._preserve_otel_logging(loglevel=None)  # must not raise


# ---------------------------------------------------------------------------
# 2. The log-forging filter has to reach the worker too
# ---------------------------------------------------------------------------

class TestSanitizerReachesTheWorker:
    """Re-enabling worker logging is what makes this matter.

    `main.py` has attached a CR/LF-escaping filter to the root handlers since
    the py/log-injection work, but the worker never imports `main`. While the
    worker's INFO logging was being discarded that was moot; it stops being
    moot in this PR.
    """

    def test_signals_are_connected(self):
        result = _probe_import({})
        assert result["after_setup_logger_receivers"] >= 1
        assert result["after_setup_task_logger_receivers"] >= 1

    def test_attached_on_the_no_appinsights_path(self):
        result = _probe_import({})
        assert result["root_handlers"], "nothing configured root logging at all"
        assert result["sanitizer_attached"] is True

    def test_attached_on_the_appinsights_path_too(self):
        """The review finding this test exists for.

        Celery sends after_setup_logger / after_setup_task_logger ONLY inside
        the `if not receivers:` branch of Logging.setup_logging_subsystem.
        Connecting `_preserve_otel_logging` to setup_logging is exactly what
        makes `receivers` truthy — so precisely when App
        Insights is configured, both signals are silent and relying on them
        alone left the worker with no CR/LF filter. Attaching from inside the
        receiver is what closes it; without this test the gap regresses
        invisibly, because every other assertion here passes either way.
        """
        result = _probe_import(
            {"APPLICATIONINSIGHTS_CONNECTION_STRING": "InstrumentationKey=abc"},
            fake_azure_succeeds=True,
        )
        assert result["otel_active"] is True
        assert result["after_setup_logger_receivers"] >= 1, "signal connected but silent"
        assert result["sanitizer_attached"] is True, (
            "App Insights path lost the log-forging filter — after_setup_logger "
            "does not fire when setup_logging has a receiver"
        )

    def test_loglevel_actually_reaches_root(self):
        """The user-visible symptom: `--loglevel=info` accepted and ignored."""
        result = _probe_import({})
        assert result["root_level"] == "INFO"

    def test_attach_is_idempotent(self):
        import logging

        from log_sanitizer import attach_to_handlers, log_forging_sanitizer

        logger = logging.getLogger("test_celery_observability.idempotent")
        logger.handlers = [logging.NullHandler()]
        attach_to_handlers(logger)
        attach_to_handlers(logger)
        assert logger.handlers[0].filters.count(log_forging_sanitizer) == 1

    def test_newlines_are_escaped_in_message_and_args(self):
        import logging

        from log_sanitizer import LogForgingSanitizer

        record = logging.LogRecord(
            "n", logging.INFO, __file__, 1, "org %s\nINJECTED", ("a\r\nb",), None
        )
        assert LogForgingSanitizer().filter(record) is True
        assert "\n" not in record.msg
        assert "\n" not in record.args[0] and "\r" not in record.args[0]

    def test_dict_args_are_escaped(self):
        import logging

        from log_sanitizer import LogForgingSanitizer

        record = logging.LogRecord("n", logging.INFO, __file__, 1, "%(x)s", None, None)
        # Assigned after construction: LogRecord.__init__ unwraps a lone mapping
        # argument, and on 3.14 raises KeyError doing so for a dict without a 0
        # key. The filter still has to handle the dict form, which is what
        # reaches it in real %(name)s-style logging.
        record.args = {"x": "a\nb"}
        LogForgingSanitizer().filter(record)
        assert record.args["x"] == "a\\nb"


# ---------------------------------------------------------------------------
# 3. Beat liveness
# ---------------------------------------------------------------------------

class TestHeartbeatScheduler:
    def test_is_a_persistent_scheduler(self):
        """Subclass, not replacement — --schedule and shelve storage must still work."""
        from celery.beat import PersistentScheduler

        from celery_beat_scheduler import HeartbeatScheduler

        assert issubclass(HeartbeatScheduler, PersistentScheduler)

    def test_tick_stamps_the_heartbeat(self):
        from celery.beat import PersistentScheduler

        from celery_beat_scheduler import HeartbeatScheduler

        scheduler = object.__new__(HeartbeatScheduler)
        scheduler.max_interval = 300
        with mock.patch.object(PersistentScheduler, "tick", return_value=42) as tick, \
             mock.patch("celery_beat_scheduler.write_heartbeat") as write:
            assert scheduler.tick() == 42
        tick.assert_called_once()
        write.assert_called_once_with(max_interval=300)

    def test_tick_passes_the_runtime_interval_not_the_class_default(self):
        """`celery beat --max-interval` and beat_max_loop_interval raise it at
        runtime; a TTL pinned to the class default would then expire under a
        perfectly healthy beat."""
        from celery.beat import PersistentScheduler

        from celery_beat_scheduler import HeartbeatScheduler

        scheduler = object.__new__(HeartbeatScheduler)
        scheduler.max_interval = 1800
        with mock.patch.object(PersistentScheduler, "tick", return_value=None), \
             mock.patch("celery_beat_scheduler.write_heartbeat") as write:
            scheduler.tick()
        write.assert_called_once_with(max_interval=1800)

    def test_a_failed_stamp_never_breaks_the_tick(self):
        """A scheduler that stops scheduling because its liveness probe threw
        would be a worse bug than the one being fixed."""
        from celery.beat import PersistentScheduler

        from celery_beat_scheduler import HeartbeatScheduler

        scheduler = object.__new__(HeartbeatScheduler)
        scheduler.max_interval = 300
        client = mock.Mock()
        client.set.side_effect = ConnectionError("redis down")
        with mock.patch.object(PersistentScheduler, "tick", return_value=7), \
             mock.patch("celery_beat_heartbeat._client", return_value=client):
            assert scheduler.tick() == 7

    def test_the_probe_module_imports_no_celery(self):
        """The healthcheck runs `python -m celery_beat_heartbeat` every 30s and
        must not pay for Celery's import tree, or it gets SIGKILLed at the
        container timeout instead of exiting 1 with a diagnostic."""
        proc = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, '.'); import celery_beat_heartbeat; "
             "print(any(m == 'celery' or m.startswith('celery.') for m in sys.modules))"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "False", "celery leaked into the probe module"


class TestHeartbeatTtl:
    def test_ttl_covers_more_than_one_missed_tick(self):
        """PersistentScheduler sleeps up to max_interval between ticks, so a TTL
        at or below it would restart a perfectly healthy beat."""
        from celery.beat import PersistentScheduler

        import celery_beat_heartbeat as hb

        assert hb.HEARTBEAT_TTL_SECONDS > 2 * PersistentScheduler.max_interval

    @pytest.mark.parametrize("interval", [300, 450, 600, 1800, 7200])
    def test_ttl_always_clears_the_actual_interval(self, interval):
        import celery_beat_heartbeat as hb

        assert hb.ttl_for_interval(interval) > 2 * interval

    @pytest.mark.parametrize("interval", [None, 0, -1])
    def test_ttl_falls_back_when_the_interval_is_unusable(self, interval):
        import celery_beat_heartbeat as hb

        assert hb.ttl_for_interval(interval) == hb.HEARTBEAT_TTL_SECONDS


class TestHeartbeatKey:
    def test_key_is_namespaced_per_deployment_and_instance(self):
        """A bare shared key is a false-healthy: two beats on one Redis DB —
        staging and prod on the same cache, blue/green, a second compose stack —
        would each keep the other's probe green."""
        import celery_beat_heartbeat as hb

        with mock.patch.dict(os.environ, {"ENVIRONMENT": "staging"}), \
             mock.patch("celery_beat_heartbeat.socket.gethostname", return_value="host-a"):
            staging = hb.heartbeat_key()
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "production"}), \
             mock.patch("celery_beat_heartbeat.socket.gethostname", return_value="host-a"):
            production = hb.heartbeat_key()
        with mock.patch.dict(os.environ, {"ENVIRONMENT": "staging"}), \
             mock.patch("celery_beat_heartbeat.socket.gethostname", return_value="host-b"):
            other_host = hb.heartbeat_key()

        assert staging != production, "two environments share one key"
        assert staging != other_host, "two beat instances share one key"
        assert all(k.startswith(hb.HEARTBEAT_KEY_PREFIX) for k in (staging, production, other_host))


class TestHeartbeatIO:
    def setup_method(self):
        import celery_beat_heartbeat as hb

        hb._client(reset=True)

    def test_write_uses_a_ttl(self):
        import celery_beat_heartbeat as hb

        client = mock.Mock()
        with mock.patch.object(hb, "_client", return_value=client):
            assert hb.write_heartbeat(max_interval=300) is True
        (key, value), kwargs = client.set.call_args
        assert key == hb.heartbeat_key() and value == "1"
        assert kwargs["ex"] == hb.ttl_for_interval(300)

    def test_write_reports_failure_without_raising(self):
        import celery_beat_heartbeat as hb

        client = mock.Mock()
        client.set.side_effect = OSError("boom")
        with mock.patch.object(hb, "_client", return_value=client):
            assert hb.write_heartbeat() is False

    def test_client_is_reused_across_ticks(self):
        """_client() runs on every tick; rebuilding meant a new pool and a new
        TCP/TLS handshake per tick, none of them ever closed."""
        import celery_beat_heartbeat as hb

        with mock.patch.dict(os.environ, {"CELERY_BROKER_URL": "redis://x:6379/1"}):
            fake = mock.Mock()
            redis_mod = mock.Mock()
            redis_mod.Redis.from_url.return_value = fake
            with mock.patch.dict(sys.modules, {"redis": redis_mod}):
                assert hb._client() is fake
                assert hb._client() is fake
            assert redis_mod.Redis.from_url.call_count == 1

    def test_client_uses_short_socket_timeouts(self):
        """write_heartbeat runs inline in beat's single-threaded tick loop —
        every second waiting on Redis is a second every due task is late."""
        import celery_beat_heartbeat as hb

        with mock.patch.dict(os.environ, {"CELERY_BROKER_URL": "redis://x:6379/1"}):
            redis_mod = mock.Mock()
            with mock.patch.dict(sys.modules, {"redis": redis_mod}):
                hb._client()
            kwargs = redis_mod.Redis.from_url.call_args.kwargs
        assert kwargs["socket_connect_timeout"] <= 2
        assert kwargs["socket_timeout"] <= 2

    def test_rediss_urls_are_normalised_like_the_app(self):
        """celery_app rewrites CELERY_BROKER_URL to disable cert verification;
        the probe does not import celery_app, so without sharing the normaliser
        it would fail the handshake against a healthy beat and ECS would replace
        a working scheduler every two minutes."""
        import celery_beat_heartbeat as hb

        with mock.patch.dict(os.environ, {"CELERY_BROKER_URL": "rediss://cache:6379/1"}):
            assert hb._redis_url() == "rediss://cache:6379/1?ssl_cert_reqs=CERT_NONE"

    def test_the_app_and_the_probe_share_one_normaliser(self):
        import celery_app
        import redis_url

        assert celery_app._fix_rediss_url is redis_url.fix_rediss_url

    def test_broker_url_is_preferred_over_redis_url(self):
        """Beat's heartbeat belongs on the broker it is already dead without."""
        import celery_beat_heartbeat as hb

        with mock.patch.dict(
            os.environ,
            {"CELERY_BROKER_URL": "redis://broker:6379/1", "REDIS_URL": "redis://cache:6379/0"},
        ):
            assert hb._redis_url() == "redis://broker:6379/1"


class TestProbeVerdict:
    """Three outcomes, not two — see the module docstring for why UNKNOWN is
    reported healthy rather than failing closed."""

    def setup_method(self):
        import celery_beat_heartbeat as hb

        hb._client(reset=True)

    def test_key_present_is_fresh(self):
        import celery_beat_heartbeat as hb

        client = mock.Mock()
        client.exists.return_value = 1
        with mock.patch.object(hb, "_client", return_value=client):
            assert hb.heartbeat_status() == hb.FRESH
            assert hb.main() == 0

    def test_redis_answered_and_the_key_is_gone_is_a_failure(self):
        """The case the probe exists for: broker fine, scheduler not."""
        import celery_beat_heartbeat as hb

        client = mock.Mock()
        client.exists.return_value = 0
        with mock.patch.object(hb, "_client", return_value=client):
            assert hb.heartbeat_status() == hb.STALE
            assert hb.main() == 1

    def test_unreachable_redis_is_inconclusive_not_a_failure(self):
        """This probe is an ECS healthcheck on an `essential = true` container.
        Failing closed would turn a 90-second broker blip into ECS stopping and
        replacing a beat that was never at fault. Restarting beat cannot fix
        Redis, and broker death is already covered by the redis service's own
        healthcheck and the worker's `celery inspect ping`."""
        import celery_beat_heartbeat as hb

        client = mock.Mock()
        client.exists.side_effect = ConnectionError("redis down")
        with mock.patch.object(hb, "_client", return_value=client):
            assert hb.heartbeat_status() == hb.UNKNOWN
            assert hb.main() == 0

    def test_no_client_is_inconclusive(self):
        import celery_beat_heartbeat as hb

        with mock.patch.object(hb, "_client", return_value=None):
            assert hb.heartbeat_status() == hb.UNKNOWN
            assert hb.write_heartbeat() is False

    def test_a_dead_socket_drops_the_cached_client(self):
        """The managed Redis force-reboots and leaves clients holding dead
        sockets; a cached client must not pin a broken connection forever."""
        import celery_beat_heartbeat as hb

        client = mock.Mock()
        client.exists.side_effect = ConnectionError("dead socket")
        with mock.patch.object(hb, "_client", wraps=lambda **kw: None if kw.get("reset") else client) as c:
            hb.heartbeat_status()
        assert any(call.kwargs.get("reset") for call in c.call_args_list), (
            "cached client was not reset after a connection error"
        )


class TestSchedulerIsSelected:
    def test_conf_points_at_the_heartbeat_scheduler(self):
        result = _probe_import({})
        assert result["beat_scheduler"] == "HeartbeatScheduler"

    def test_conf_holds_the_class_not_a_dotted_string(self):
        """celery resolves a string with kombu's symbol_by_name, which uses
        plain importlib and does NOT add the working directory — unlike the
        import_from_cwd that `-A celery_app` gets. A string here starts beat
        only where PYTHONPATH already contains backend/; the class always
        resolves."""
        import celery_app
        from celery_beat_scheduler import HeartbeatScheduler

        assert celery_app.celery_app.conf.beat_scheduler is HeartbeatScheduler

    def test_celery_accepts_what_we_configured(self):
        from celery.utils.imports import symbol_by_name

        import celery_app
        from celery_beat_scheduler import HeartbeatScheduler

        assert symbol_by_name(celery_app.celery_app.conf.beat_scheduler) is HeartbeatScheduler


# ---------------------------------------------------------------------------
# 4. Class sweep over the deployment definitions
#
# The platform ships one deployment: docker-compose.yml. Historically there
# were three more copies of the process-name probe in cloud launcher files
# (since removed), and one of them pinned --scheduler, which would have
# silently excluded that environment from the fix. Assert the class, not the
# instance, so any launcher added later gets listed here and swept too.
# ---------------------------------------------------------------------------

DEPLOYMENT_FILES = [
    "docker-compose.yml",
]

#: Files that define a beat liveness probe today.
FILES_WITH_A_BEAT_PROBE = [
    "docker-compose.yml",
]


def _deployment_path(relpath):
    """Resolve a DEPLOYMENT_FILES entry, failing loudly if it is gone.

    Every entry ships in every distribution (the OSS snapshot included), so an
    absent entry is a rename nobody propagated — the failure #784 landed this
    file to prevent — never a distribution difference.
    """
    path = REPO_ROOT / relpath
    if path.exists():
        return path
    pytest.fail(f"{relpath} moved — update DEPLOYMENT_FILES")


#: A healthcheck line, in any of the three syntaxes in play (compose YAML,
#: an ECS task-definition `healthCheck`, and the compose heredoc in user-data).
#: Matching on this rather than stripping comments is deliberate: a naive
#: "drop anything starting with #" pass also drops heredoc bodies and YAML
#: values that legitimately begin with #, which would let a real violation
#: through the only guard three deployment files have.
PROBE_LINE = re.compile(r"CMD-SHELL", re.IGNORECASE)


def _probe_lines(path: Path):
    """Lines that actually define a container probe command."""
    return [l for l in path.read_text().splitlines() if PROBE_LINE.search(l)]


def _beat_command_lines(path: Path):
    """Lines that actually invoke `celery ... beat`, comments excluded by
    requiring both the executable and the subcommand on the same line."""
    lines = []
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//"):
            continue
        if "celery" in line and re.search(r'["\s]beat["\s,\]]', line):
            lines.append(line)
    return lines


@pytest.mark.parametrize("relpath", DEPLOYMENT_FILES)
def test_no_process_name_beat_probe_survives(relpath):
    """`grep -q beat /proc/1/cmdline` asserts PID 1's command line, which does
    not change when the process behind it stops working."""
    path = _deployment_path(relpath)
    offenders = [l for l in _probe_lines(path) if "/proc/1/cmdline" in l]
    assert not offenders, (
        f"{relpath} still probes beat by process name; use "
        f"`python -m celery_beat_heartbeat`:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("relpath", DEPLOYMENT_FILES)
def test_no_deployment_pins_the_scheduler(relpath):
    """An explicit --scheduler overrides celery_app.conf.beat_scheduler.

    A since-removed cloud launcher pinned celery.beat:PersistentScheduler, so
    without this the heartbeat would have shipped everywhere except the
    environment it exists for.
    """
    path = _deployment_path(relpath)
    offenders = [
        l for l in _beat_command_lines(path)
        if re.search(r'--scheduler\b|["\s]-S["\s]', l)
    ]
    assert not offenders, (
        f"{relpath} pins beat's scheduler, which overrides "
        f"celery_app.conf.beat_scheduler:\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize("relpath", FILES_WITH_A_BEAT_PROBE)
def test_each_beat_probe_uses_the_heartbeat(relpath):
    """Asserted per file rather than by counting.

    A count would fail the day someone adds a probe to a newly listed
    launcher — and would report it as "a beat container was added or
    removed", which is exactly backwards.
    """
    path = _deployment_path(relpath)
    beat_probes = [
        l for l in _probe_lines(path)
        if "celery_beat_heartbeat" in l or "/proc/1/cmdline" in l or " beat" in l
    ]
    assert beat_probes, f"{relpath} no longer defines a beat probe — was it removed?"
    assert all("celery_beat_heartbeat" in l for l in beat_probes), (
        f"{relpath} has a beat probe that does not use the heartbeat:\n"
        + "\n".join(beat_probes)
    )
