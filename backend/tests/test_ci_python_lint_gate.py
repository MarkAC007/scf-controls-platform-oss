# SPDX-License-Identifier: AGPL-3.0-only
"""Every step in the Python Backend job's lint stage can fail (plan 8.3).

The job used to run a second flake8 pass with ``--exit-zero`` and a
``black --check`` step with ``continue-on-error: true``. Neither could ever
fail the check, so both looked like gates without being one (black reported
574 of 585 files as unformatted on every run). Both were removed; the flake8
pass that stops on syntax errors and undefined names stays. These tests keep
a step that cannot fail from coming back.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(not CI.exists(), reason="ci.yml is not in this tree")


def _python_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["python"]["steps"]


def test_no_python_job_step_is_allowed_to_fail():
    lenient = [s.get("name") for s in _python_steps() if s.get("continue-on-error")]
    assert lenient == [], f"steps that cannot fail the check: {lenient}"


def test_no_lint_command_is_told_to_exit_zero():
    runs = [s.get("run", "") for s in _python_steps()]
    assert not any("--exit-zero" in run for run in runs)
    assert not any("black --check" in run for run in runs), "black is not enforced; a non-failing check is not a gate"


def test_flake8_still_stops_on_syntax_errors_and_undefined_names():
    [step] = [s for s in _python_steps() if s.get("name") == "Lint with flake8"]
    assert step["run"].split() == ["flake8", ".", "--count", "--select=E9,F63,F7,F82", "--show-source", "--statistics"]
    assert "if" not in step
