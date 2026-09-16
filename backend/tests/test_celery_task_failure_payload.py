"""A task's RETURN VALUE decides how its completion is logged (#1001, #1018).

A task that returns a failure must not be logged as a success (#1001), and a
task that returns a *partial* success must not be logged as a plain success
either (#1018) — it gets a ``WARNING`` carrying the task's own message and the
integer counts from its payload, so an operator reading ``celery-worker`` can
see at a glance that the run did not fully succeed.

Celery calls ``Task.on_success`` whenever no exception escaped the task body.
That is not the same thing as the work having succeeded. The established
convention in this codebase is for a task to catch its own exception, record
the failure in the database and **return** a payload that says so::

    return {"job_id": job_id, "status": "failed", "error": str(exc)[:500]}

``BaseTask.on_success`` logged ``Task {name}[{id}] succeeded`` without ever
looking at ``retval``, and ``celery_app.Task = BaseTask`` makes that the base
class for every task in the application. So a failed vendor assessment left
nothing behind but a success line, platform-wide.

**Do not verify this from ``docker compose logs``.** The defect is what writes
those logs; a green log line is the bug, not the evidence. Everything here
asserts on the records the handler emits, driven through Celery's own eager
tracer so the handler is invoked the way the worker invokes it — not by
calling ``on_success`` by hand, which would prove only that a method exists.

Both declaration styles are exercised on purpose: ``@shared_task`` resolves its
base class from the *current* app at finalisation, ``@celery_app.task`` from
the app object directly. The repo uses both, so the reach of the fix depends on
both picking up ``BaseTask``.
"""
from __future__ import annotations

import logging

import pytest
from celery import shared_task

from celery_app import BaseTask, celery_app

CELERY_APP_LOGGER = "celery_app"


# --------------------------------------------------------------------------
# Tasks under test. Declared at module scope so they are registered exactly
# the way the shipped tasks are; each simply returns the payload it is given.
# --------------------------------------------------------------------------

#: The partial-success statuses the echo tasks DECLARE (#1018). Declaring is
#: the point: a task that says nothing keeps the plain success line for these
#: same words, which ``echo_payload_undeclared`` below exists to prove.
PARTIAL_STATUSES_UNDER_TEST = ["partial", "completed_with_errors"]


@shared_task(
    bind=True,
    name="tests.echo_payload_shared",
    partial_statuses=frozenset(PARTIAL_STATUSES_UNDER_TEST),
)
def echo_payload_shared(self, payload):
    """A task that never raises and returns whatever payload it is handed."""
    return payload


@celery_app.task(
    bind=True,
    name="tests.echo_payload_app",
    partial_statuses=frozenset(PARTIAL_STATUSES_UNDER_TEST),
)
def echo_payload_app(self, payload):
    """The same, declared through ``celery_app.task`` rather than shared_task."""
    return payload


@shared_task(bind=True, name="tests.echo_payload_undeclared")
def echo_payload_undeclared(self, payload):
    """Declares no partial statuses — like the evidence-assessment tasks, whose
    ``status`` is a verdict on the evidence, not on the run."""
    return payload


ECHO_TASKS = [echo_payload_shared, echo_payload_app]
ECHO_IDS = ["shared_task", "celery_app.task"]


def _run(task, payload, caplog):
    """Run ``task`` eagerly and return the records its handler emitted."""
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=CELERY_APP_LOGGER):
        result = task.apply(args=(payload,))
    result.get()  # re-raises if the task body itself blew up
    return [r for r in caplog.records if r.name == CELERY_APP_LOGGER]


def _succeeded(records):
    return [r for r in records if "succeeded" in r.getMessage()]


def _failures(records):
    return [r for r in records if r.levelno >= logging.ERROR]


# --------------------------------------------------------------------------
# The defect
# --------------------------------------------------------------------------

@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
@pytest.mark.parametrize("status", ["failed", "error"])
def test_failure_payload_is_not_logged_as_a_success(task, status, caplog):
    """The whole point of #1001: a returned failure must read as a failure."""
    records = _run(task, {"status": status, "error": "boom"}, caplog)

    assert not _succeeded(records), (
        f"a task returning status={status!r} was logged as a SUCCESS: "
        f"{[r.getMessage() for r in records]}"
    )
    failures = _failures(records)
    assert failures, (
        f"a task returning status={status!r} recorded no failure at all: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    message = failures[0].getMessage()
    assert task.name in message, f"the failure line does not name the task: {message}"
    assert status in message, f"the failure line does not carry the status: {message}"


def test_the_failure_line_carries_the_error_text(caplog):
    """An operator reading the log needs the reason, not just the verdict."""
    records = _run(
        echo_payload_shared,
        {"status": "failed", "error": "Could not resolve authentication method"},
        caplog,
    )
    failures = _failures(records)
    assert failures, "no failure recorded"
    assert "Could not resolve authentication method" in failures[0].getMessage()


def test_message_key_is_used_when_there_is_no_error_key(caplog):
    """``tasks_assessment`` and the storage copy report under ``message``."""
    records = _run(
        echo_payload_shared,
        {"status": "error", "message": "Evidence file not found"},
        caplog,
    )
    failures = _failures(records)
    assert failures, "no failure recorded"
    assert "Evidence file not found" in failures[0].getMessage()


# --------------------------------------------------------------------------
# Positive controls — the fix must not turn every task into a failure
# --------------------------------------------------------------------------

@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
@pytest.mark.parametrize(
    "payload",
    [
        {"status": "completed", "job_id": "j1"},
        {"status": "complete"},
        {"status": "success"},
        {"status": "healthy"},
        {"status": "verified"},
        # "partial" and "completed_with_errors" are deliberately absent: since
        # #1018 they are WARNINGs, not plain successes. They are covered by
        # the partial-success section further down.
        # Statuses that describe an outcome rather than a breakage.
        {"status": "rate_limited"},
        {"status": "no_releases"},
        {"status": "skipped_deleted"},
        {"status": "not_found"},
        # Payloads with no status key at all, and non-dict returns.
        {"processed": 10, "succeeded": 10},
        None,
        "done",
        [1, 2, 3],
        42,
    ],
    ids=lambda p: repr(p)[:40],
)
def test_success_payloads_still_log_a_success(task, payload, caplog):
    records = _run(task, payload, caplog)

    assert _succeeded(records), (
        f"payload {payload!r} lost its success line: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    assert not _failures(records), (
        f"payload {payload!r} was wrongly recorded as a failure: "
        f"{[r.getMessage() for r in _failures(records)]}"
    )


def test_a_status_that_is_not_a_string_is_not_a_failure(caplog):
    """``status`` is a count or an object in payloads that are not task verdicts."""
    records = _run(echo_payload_shared, {"status": {"failed": 2}}, caplog)
    assert _succeeded(records)
    assert not _failures(records)


def test_failure_detection_is_case_and_whitespace_insensitive(caplog):
    records = _run(echo_payload_shared, {"status": " FAILED "}, caplog)
    assert not _succeeded(records)
    assert _failures(records)


# --------------------------------------------------------------------------
# Partial successes (#1018) — neither a failure nor a plain success
# --------------------------------------------------------------------------

#: Shaped like what ``copy_evidence_store`` actually returns, plus the
#: ``message`` it writes onto its run record — integer counts an operator
#: wants on the line, and a ``source_retired`` bool that must NOT be rendered
#: as a count (``isinstance(True, int)`` is true in Python).
COPY_PAYLOAD = {
    "status": "completed_with_errors",
    "copied": 3,
    "failed": 1,
    "skipped": 0,
    "message": "Copied 3 file(s); 1 failed; 0 already in the target store.",
}

#: Shaped like what ``research_aggregator`` returns: no counts at all, and the
#: useful detail lives under ``summary``.
RESEARCH_PAYLOAD = {
    "status": "partial",
    "summary": "2 breach(es) found via HIBP",
    "overall_risk_signal": "medium",
}


def _warnings(records):
    return [r for r in records if r.levelno == logging.WARNING]


@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
@pytest.mark.parametrize("status", PARTIAL_STATUSES_UNDER_TEST)
def test_partial_payload_is_not_logged_as_a_plain_success(task, status, caplog):
    """#1018: `succeeded` is a lie for a run that only partly worked."""
    records = _run(task, {"status": status}, caplog)

    assert not _succeeded(records), (
        f"a task returning status={status!r} was logged as a plain SUCCESS: "
        f"{[r.getMessage() for r in records]}"
    )
    warnings = _warnings(records)
    assert len(warnings) == 1, (
        f"expected exactly one WARNING for status={status!r}, got: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    assert not _failures(records), (
        f"status={status!r} is a partial success, not a failure: "
        f"{[r.getMessage() for r in _failures(records)]}"
    )
    message = warnings[0].getMessage()
    assert task.name in message, f"the warning does not name the task: {message}"
    assert status in message, f"the warning does not carry the status: {message}"


@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
def test_the_warning_carries_the_copy_counts_and_message(task, caplog):
    """The storage-copy shape: the operator needs the numbers, not a verdict."""
    records = _run(task, COPY_PAYLOAD, caplog)

    warnings = _warnings(records)
    assert len(warnings) == 1, (
        f"expected exactly one WARNING: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    message = warnings[0].getMessage()
    for fragment in ("copied=3", "failed=1", "skipped=0", COPY_PAYLOAD["message"]):
        assert fragment in message, (
            f"the warning is missing {fragment!r}: {message}"
        )


@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
def test_the_warning_carries_the_research_summary_and_no_counts(task, caplog):
    """The research shape: a summary and no integers, so no counts segment."""
    records = _run(task, RESEARCH_PAYLOAD, caplog)

    warnings = _warnings(records)
    assert len(warnings) == 1, (
        f"expected exactly one WARNING: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    message = warnings[0].getMessage()
    assert RESEARCH_PAYLOAD["summary"] in message, (
        f"the warning does not carry the summary: {message}"
    )
    assert "=" not in message.split(RESEARCH_PAYLOAD["summary"], 1)[1], (
        f"a payload with no integer values grew a counts segment: {message}"
    )


def test_a_bool_is_not_rendered_as_a_count(caplog):
    """``isinstance(True, int)`` — ``source_retired`` is not a count."""
    records = _run(
        echo_payload_shared,
        {"status": "completed_with_errors", "copied": 2, "source_retired": True},
        caplog,
    )
    message = _warnings(records)[0].getMessage()
    assert "copied=2" in message, message
    assert "source_retired" not in message, (
        f"a bool was rendered as a count: {message}"
    )


@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
def test_a_completed_payload_still_logs_the_plain_success_line(task, caplog):
    """Positive control: the ordinary path is untouched by #1018."""
    records = _run(task, {"status": "completed"}, caplog)

    assert _succeeded(records), (
        f"the plain success line was lost: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    assert all(r.levelno == logging.INFO for r in _succeeded(records)), (
        "the success line stopped being INFO"
    )
    assert not _warnings(records), (
        f"a completed payload produced a WARNING: "
        f"{[r.getMessage() for r in _warnings(records)]}"
    )


@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
def test_a_failed_payload_still_logs_an_error_and_no_warning(task, caplog):
    """Positive control: #1001's ERROR branch is unchanged by #1018."""
    records = _run(task, {"status": "failed"}, caplog)

    assert _failures(records), (
        f"the failure line was lost: "
        f"{[(r.levelname, r.getMessage()) for r in records]}"
    )
    assert not _warnings(records), (
        f"a failure payload was downgraded to a WARNING: "
        f"{[r.getMessage() for r in _warnings(records)]}"
    )
    assert not _succeeded(records)


def test_partial_detection_is_case_and_whitespace_insensitive(caplog):
    records = _run(echo_payload_shared, {"status": " PARTIAL "}, caplog)
    assert not _succeeded(records)
    assert len(_warnings(records)) == 1


def test_a_non_string_status_is_not_a_partial(caplog):
    """Same tolerance as the failure check: a dict status is not a verdict."""
    records = _run(echo_payload_shared, {"status": {"partial": 2}}, caplog)
    assert _succeeded(records)
    assert not _warnings(records)


@pytest.mark.parametrize("status", PARTIAL_STATUSES_UNDER_TEST)
def test_an_undeclared_task_keeps_the_plain_success_line(status, caplog):
    """The regression the per-task declaration exists to prevent: "partial" is
    the evidence VERDICT returned by ``tasks_assessment.assess_evidence_task``
    and ``tasks_window_assessment.assess_window_task`` on runs that fully
    succeeded. A task that declares no partial statuses must log "succeeded"
    and nothing at WARNING, whatever word its payload carries."""
    records = _run(
        echo_payload_undeclared,
        {"status": status, "gap_count": 2, "processing_time_ms": 4321},
        caplog,
    )
    assert _succeeded(records), [r.getMessage() for r in records]
    assert not _warnings(records), [r.getMessage() for r in _warnings(records)]
    assert not _failures(records)


def test_basetask_declares_nothing_by_default():
    assert BaseTask.partial_statuses == frozenset()
    assert echo_payload_undeclared.partial_statuses == frozenset()


@pytest.mark.parametrize(
    "module_name, task_name, expected",
    [
        ("tasks_research", "tasks_research.research_aggregator", {"partial"}),
        (
            "tasks_evidence_storage_copy",
            "tasks_evidence_storage_copy.copy_evidence_store",
            {"completed_with_errors"},
        ),
        ("tasks_assessment", "tasks_assessment.assess_evidence_task", set()),
        ("tasks_window_assessment", "tasks_window_assessment.assess_window_task", set()),
    ],
)
def test_the_shipped_tasks_declare_exactly_what_the_issue_intends(
    module_name, task_name, expected
):
    """The two producers named in #1018 opt in; the two assessment tasks, whose
    "partial" is a verdict, must not. Pinned against the registry so a renamed
    status or a dropped declaration fails here, not in an operator's log."""
    __import__(module_name)
    task = celery_app.tasks.get(task_name)
    assert task is not None, f"{task_name} is not registered"
    assert set(task.partial_statuses) == expected
    if module_name == "tasks_evidence_storage_copy":
        from tasks_evidence_storage_copy import STATE_COMPLETED_WITH_ERRORS

        assert STATE_COMPLETED_WITH_ERRORS in task.partial_statuses


# --------------------------------------------------------------------------
# Reach — the claim that makes this platform-wide rather than a two-task bug
# --------------------------------------------------------------------------

def test_basetask_is_the_app_default():
    """If this stops holding, every assertion above tests only a test task."""
    assert celery_app.Task is BaseTask


@pytest.mark.parametrize("task", ECHO_TASKS, ids=ECHO_IDS)
def test_both_declaration_styles_inherit_basetask(task):
    assert isinstance(task, BaseTask), (
        f"{task.name} does not inherit BaseTask, so the handler never runs for it"
    )


@pytest.mark.parametrize(
    "module_name, task_name",
    [
        ("tasks_vendor_assessment", "tasks_vendor_assessment.run_vendor_assessment"),
        ("tasks_recipe_generation", "tasks_recipe_generation.run_recipe_generation"),
    ],
)
def test_the_two_tasks_named_in_the_issue_inherit_basetask(module_name, task_name):
    """The shipped tasks, not stand-ins: import the module, look in the registry."""
    __import__(module_name)
    task = celery_app.tasks.get(task_name)
    assert task is not None, (
        f"{task_name} is not registered — the task was renamed and this test "
        f"has stopped checking anything. Registered: "
        f"{sorted(n for n in celery_app.tasks if not n.startswith('celery.'))}"
    )
    assert isinstance(task, BaseTask)
