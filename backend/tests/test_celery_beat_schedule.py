"""Tests for Celery beat wiring of scheduled GRC automation jobs."""
from __future__ import annotations

import os
from unittest import mock

import pytest
from celery.schedules import crontab

# celery_app freezes beat_schedule at import time, so the gating flags must be
# unset BEFORE the import for these tests to assert the shipped defaults. The
# patch.dict restores the developer's real environment once the import is done,
# so this file leaves no env mutation behind for the rest of the session.
with mock.patch.dict(os.environ):
    os.environ.pop("TASK_AUTOMATION_ENABLED", None)
    os.environ.pop("WINDOW_ASSESSMENT_NIGHTLY_ENABLED", None)

    from celery_app import _flag_enabled, celery_app  # noqa: E402
    import tasks_automation  # noqa: E402


AUTOMATION_TASKS = {
    "evidence-task-generation-daily": {
        "task": "tasks_automation.generate_evidence_tasks_task",
        "hour": 1,
        "minute": 0,
    },
    "task-due-notifications-daily": {
        "task": "tasks_automation.notify_due_tasks_task",
        "hour": 7,
        "minute": 0,
    },
    "task-overdue-notifications-daily": {
        "task": "tasks_automation.notify_overdue_tasks_task",
        "hour": 7,
        "minute": 15,
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _assert_crontab(schedule: crontab, hour: int, minute: int) -> None:
    assert isinstance(schedule, crontab)
    assert schedule.hour == {hour}
    assert schedule.minute == {minute}


# ---------------------------------------------------------------------------
# Beat schedule
# ---------------------------------------------------------------------------
def test_automation_beat_entries_present_with_expected_tasks_and_crontabs() -> None:
    beat_schedule = celery_app.conf.beat_schedule

    for entry_name, expected in AUTOMATION_TASKS.items():
        entry = beat_schedule[entry_name]
        assert entry["task"] == expected["task"]
        _assert_crontab(entry["schedule"], expected["hour"], expected["minute"])


def test_nightly_window_refresh_present_by_default() -> None:
    entry = celery_app.conf.beat_schedule["nightly-window-refresh"]

    assert entry["task"] == "tasks_window_assessment.nightly_window_refresh_task"
    _assert_crontab(entry["schedule"], 4, 0)


def test_automation_tasks_registered_after_importing_module() -> None:
    for expected in AUTOMATION_TASKS.values():
        assert expected["task"] in celery_app.tasks


def test_automation_task_routes_use_default_queue() -> None:
    task_routes = celery_app.conf.task_routes

    for expected in AUTOMATION_TASKS.values():
        assert task_routes[expected["task"]] == {"queue": "default"}


def test_tasks_automation_included_by_celery_app() -> None:
    assert "tasks_automation" in celery_app.conf.include


# ---------------------------------------------------------------------------
# Env flag parsing
# ---------------------------------------------------------------------------
def test_flag_enabled_honours_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEST_FLAG", raising=False)

    assert _flag_enabled("TEST_FLAG", "true") is True
    assert _flag_enabled("TEST_FLAG", "false") is False


@pytest.mark.parametrize("value", ["false", "FALSE", "0", " false "])
def test_flag_enabled_explicit_false_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("TEST_FLAG", value)

    assert _flag_enabled("TEST_FLAG", "true") is False


@pytest.mark.parametrize("value", ["true", "1", "anything else"])
def test_flag_enabled_truthy_and_other_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("TEST_FLAG", value)

    assert _flag_enabled("TEST_FLAG", "false") is True


# ---------------------------------------------------------------------------
# Runtime guards
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("task", "task_name"),
    [
        (tasks_automation.generate_evidence_tasks_task, "generate_evidence_tasks_task"),
        (tasks_automation.notify_due_tasks_task, "notify_due_tasks_task"),
        (tasks_automation.notify_overdue_tasks_task, "notify_overdue_tasks_task"),
    ],
)
def test_automation_tasks_noop_when_runtime_flag_disabled(
    monkeypatch: pytest.MonkeyPatch,
    task,
    task_name: str,
) -> None:
    monkeypatch.setenv("TASK_AUTOMATION_ENABLED", "false")

    def _fail_if_database_path_runs(*args, **kwargs):
        raise AssertionError("async database path must not run when automation is disabled")

    monkeypatch.setattr(tasks_automation.asyncio, "run", _fail_if_database_path_runs)

    result = task.apply(args=()).get()

    assert result == {
        "status": "disabled",
        "task": task_name,
        "disabled_by": "TASK_AUTOMATION_ENABLED",
    }
