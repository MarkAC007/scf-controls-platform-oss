"""The Docker Build job's boot test must be able to fail.

Until this gate the job ran ``docker compose up -d``, slept ten seconds and
carried ``continue-on-error: true``. Postgres exited on every run because no
database password was set, the step exited 1, and the required Docker Build
check still passed. The step now waits for the stack to become healthy and
asserts on the backend and the frontend; these tests keep it from being turned
back into a step that cannot fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(not CI.exists(), reason="ci.yml is not in this tree")


def _docker_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["docker"]["steps"]


def _boot_steps() -> list[dict]:
    return [s for s in _docker_steps() if "docker compose up" in s.get("run", "")]


def test_exactly_one_step_boots_the_stack_and_it_waits_for_health():
    steps = _boot_steps()
    assert len(steps) == 1, [s.get("name") for s in steps]
    run = steps[0]["run"]
    assert "docker compose up -d --wait" in run
    assert "set -euo pipefail" in run
    assert "sleep" not in run, "a fixed sleep is not a readiness check"


def test_the_boot_step_cannot_fail_silently():
    step = _boot_steps()[0]
    assert not step.get("continue-on-error"), "the boot test must fail the job"
    assert "|| true" not in step["run"]


def test_the_boot_step_asserts_on_the_backend_and_the_frontend():
    run = _boot_steps()[0]["run"]
    assert "http://localhost:8000/health" in run
    assert "curl --fail" in run
    assert "scripts/verify-prod-build.sh http://localhost:5173" in run


def test_the_stack_is_always_torn_down_with_its_volume():
    downs = [s for s in _docker_steps() if "docker compose down" in s.get("run", "")]
    assert len(downs) == 1
    assert "always()" in downs[0]["if"]
    assert "--volumes" in downs[0]["run"]
