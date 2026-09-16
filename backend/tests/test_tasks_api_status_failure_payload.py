"""``GET /api/tasks/status/{id}`` must not answer SUCCESS for work that failed (#1013).

Sibling of ``test_celery_task_failure_payload.py`` (#1001). That issue was about
the worker *log*; this one is about the *API*. The convention it trips over is
the same one: a task in this codebase catches its own exception, records the
failure and **returns** a payload that says so::

    return {"job_id": job_id, "status": "failed", "error": str(exc)[:500]}

No exception escaped, so Celery marks the task ``SUCCESS``. ``get_task_status``
returned ``result.status`` verbatim, so a caller polling a vendor assessment, a
recipe generation, an evidence assessment, a research run, an evidence-store
copy or a window assessment was told ``SUCCESS`` for a job that did not happen.

**Do not verify this by polling a real job and reading the response.** The
endpoint is what produces that response; a green ``SUCCESS`` is the bug, not the
evidence. Everything here drives a REAL task through Celery's eager tracer --
``task.apply(...)`` returns an ``EagerResult`` with the same ``.status``,
``.result`` and ``.info`` surface that ``celery_app.AsyncResult`` returns -- and
feeds that object to the code under test. The one exception is the
``PENDING``/``PROGRESS`` check, which an eager run can never be in: it uses a
minimal stand-in and therefore proves only that those branches pass the
attributes through unchanged, not how a live ``AsyncResult`` behaves.

Two layers are covered, because a fix in either alone would leave the defect
reachable:

* ``_describe_task_result`` -- the derivation, tested against real result
  objects for the failure payloads, the positive controls that must stay
  ``SUCCESS``, and a task that genuinely raises (whose behaviour is unchanged);
* the HTTP path -- one request through the real router, asserting the JSON body
  an operator actually receives.

The detection itself is NOT reimplemented here: the endpoint reuses
``celery_app.payload_reports_failure``, whose vocabulary (and the deliberate
exclusion of ``partial`` and ``completed_with_errors``) is owned and tested by
#1001. ``test_the_endpoint_reuses_the_shared_failure_predicate`` pins that reuse
so the two surfaces cannot drift into disagreeing about what "failed" means.
"""
from __future__ import annotations

import os
import sys

import pytest
from celery import shared_task

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api import tasks_api  # noqa: E402
from api.tasks_api import _describe_task_result  # noqa: E402

FAILURE_PAYLOAD = {"job_id": "j1", "status": "failed", "error": "boom"}


# ---------------------------------------------------------------------------
# Tasks under test. Declared at module scope so they register the way the
# shipped tasks do, and run through the same tracer the worker uses.
# ---------------------------------------------------------------------------

@shared_task(bind=True, name="tests.status_echo_payload")
def status_echo_payload(self, payload):
    """Never raises; returns whatever payload it is handed."""
    return payload


@shared_task(bind=True, name="tests.status_raise_boom")
def status_raise_boom(self):
    """Lets the exception escape, the way Celery's native failure path expects."""
    raise RuntimeError("the task itself exploded")


def _eager(payload):
    """A real ``EagerResult`` for a task that returned ``payload``."""
    return status_echo_payload.apply(args=(payload,))


# ---------------------------------------------------------------------------
# The defect
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload, expected_error",
    [
        ({"status": "failed", "error": "boom"}, "boom"),
        ({"status": "error", "message": "nope"}, "nope"),
    ],
    ids=["failed+error", "error+message"],
)
def test_a_returned_failure_is_reported_as_a_failure(payload, expected_error):
    """The whole point of #1013."""
    result = _eager(payload)
    assert result.status == "SUCCESS", (
        "precondition: Celery must consider this task successful, otherwise "
        "this test is not exercising the defect at all"
    )

    status, task_result, error = _describe_task_result(result)

    assert status == "FAILURE", (
        f"a task that returned {payload!r} was reported as {status!r}"
    )
    assert error == expected_error, (
        f"the caller was not told why: error={error!r}"
    )
    assert task_result == payload, (
        "the payload must survive so the caller keeps the detail (job_id, "
        f"partial results); got {task_result!r}"
    )


def test_a_failure_payload_with_no_reason_still_reads_as_a_failure():
    """Some tasks record the reason elsewhere and return only the verdict."""
    status, task_result, error = _describe_task_result(_eager({"status": "failed"}))

    assert status == "FAILURE"
    assert error == "Task returned a failure payload"
    assert task_result == {"status": "failed"}


def test_an_empty_error_string_falls_through_to_message():
    payload = {"status": "failed", "error": "", "message": "disk full"}
    status, _, error = _describe_task_result(_eager(payload))

    assert status == "FAILURE"
    assert error == "disk full"


def test_the_error_text_is_truncated():
    """``TaskStatusResponse.error`` is a summary line, not a log dump."""
    payload = {"status": "failed", "error": "x" * 900}
    _, _, error = _describe_task_result(_eager(payload))

    assert len(error) == 500, f"error was {len(error)} chars"


def test_a_non_string_reason_is_still_rendered():
    """``error`` is sometimes an exception object or a dict, not a string."""
    payload = {"status": "error", "error": {"code": 502}}
    status, _, error = _describe_task_result(_eager(payload))

    assert status == "FAILURE"
    assert isinstance(error, str) and "502" in error


# ---------------------------------------------------------------------------
# Positive controls -- the fix must not turn successful work into failures
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        {"status": "completed"},
        {"status": "completed", "job_id": "j1", "controls": 42},
        {"status": "partial"},
        {"status": "completed_with_errors", "failed": 3},
        {"processed": 10},
    ],
    ids=lambda p: repr(p)[:40],
)
def test_success_payloads_are_still_reported_as_success(payload):
    status, task_result, error = _describe_task_result(_eager(payload))

    assert status == "SUCCESS", f"payload {payload!r} was wrongly failed"
    assert error is None
    assert task_result == payload


@pytest.mark.parametrize("payload", [[1, 2, 3], "done", None, 42], ids=repr)
def test_non_dict_payloads_are_not_failures(payload):
    """Tasks return lists, strings and ``None``; none of those is a failure."""
    status, task_result, error = _describe_task_result(_eager(payload))

    assert status == "SUCCESS"
    assert error is None
    assert task_result == payload


def test_a_task_that_raises_is_unchanged():
    """Celery's native failure path must keep reporting exactly as before."""
    result = status_raise_boom.apply()
    assert result.status == "FAILURE", "precondition: Celery marks this FAILURE"

    status, task_result, error = _describe_task_result(result)

    assert status == "FAILURE"
    assert error == "the task itself exploded"
    assert task_result is None


def test_pending_and_progress_are_unchanged():
    """The branches this change must not touch.

    An eager run is never PENDING or PROGRESS, so this is the one place a
    stand-in is used; it shows the branches pass status and info through
    untouched and nothing more (see the module docstring).
    """

    class _Result:
        """Only for the states an EagerResult cannot be in."""

        def __init__(self, status, info):
            self.status = status
            self.result = info
            self.info = info

    status, task_result, error = _describe_task_result(_Result("PENDING", None))
    assert (status, task_result, error) == ("PENDING", None, None)

    progress = {"current": 3, "total": 10}
    status, task_result, error = _describe_task_result(_Result("PROGRESS", progress))
    assert (status, task_result, error) == ("PROGRESS", progress, None)


def test_the_endpoint_reuses_the_shared_failure_predicate():
    """One definition of "failed" for the worker log and for the API.

    If this fails, the endpoint has grown a private copy of the vocabulary and
    the two surfaces will disagree the moment #1001's set changes.
    """
    from celery_app import payload_reports_failure

    assert tasks_api.payload_reports_failure is payload_reports_failure


# ---------------------------------------------------------------------------
# The HTTP path -- what an operator actually receives
# ---------------------------------------------------------------------------

def test_the_endpoint_returns_failure_over_http(monkeypatch):
    """Through the real router, with the real response model."""
    from fastapi.testclient import TestClient

    import main
    from auth import require_auth

    result = _eager(FAILURE_PAYLOAD)
    monkeypatch.setattr(
        tasks_api.celery_app, "AsyncResult", lambda task_id: result, raising=True
    )

    main.app.dependency_overrides[require_auth] = lambda: None
    try:
        client = TestClient(main.app)
        response = client.get("/api/tasks/status/abc-123")
    finally:
        main.app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "FAILURE", body
    assert body["error"] == "boom", body
    assert body["result"] == FAILURE_PAYLOAD, body
    assert body["task_id"] == "abc-123", body
