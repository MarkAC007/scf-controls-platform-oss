# SPDX-License-Identifier: AGPL-3.0-only
"""The Docker Build job must not hand a git credential to the steps that build.

The job's checkout used to pass the ``GH_PAT`` personal token (to fetch a
``catalog`` submodule that no longer exists) and left it in the git config,
where every later step could read it. Those steps run Dockerfiles and
dependency installs. The checkout now uses the job's own token and does not
persist it, and a step right after it fails the job if a credential is found.
The step's own shell script is executed below, so the test fails if the script
stops detecting a credential, not only if its text changes.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ASSERT_STEP = "Assert no git credential was persisted"

pytestmark = pytest.mark.skipif(not CI.exists(), reason="ci.yml is not in this tree")


def _docker_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["docker"]["steps"]


def _checkout() -> dict:
    matches = [s for s in _docker_steps() if "actions/checkout" in s.get("uses", "")]
    assert len(matches) == 1, "expected exactly one checkout in the Docker Build job"
    return matches[0]


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, timeout=30)


def _run_assertion(repo: Path) -> subprocess.CompletedProcess:
    step = next(s for s in _docker_steps() if s.get("name") == ASSERT_STEP)
    env = {"PATH": os.environ["PATH"], "HOME": str(repo.parent), "GIT_CONFIG_NOSYSTEM": "1"}
    return subprocess.run(
        ["bash", "-c", step["run"]], cwd=repo, env=env, capture_output=True, text=True, timeout=30
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    _git(path, "init", "--quiet")
    return path


def test_the_checkout_uses_the_job_token_and_does_not_persist_it():
    settings = _checkout()["with"]
    assert settings["persist-credentials"] is False
    assert "token" not in settings, "the job's own token is enough for the clone"
    assert "submodules" not in settings


def test_ci_does_not_read_the_personal_token():
    assert "secrets.GH_PAT" not in CI.read_text()


def test_the_assertion_runs_right_after_the_checkout_and_can_fail():
    steps = _docker_steps()
    step = steps[steps.index(_checkout()) + 1]
    assert step.get("name") == ASSERT_STEP
    assert not step.get("continue-on-error")
    assert "if" not in step


def test_the_assertion_passes_on_a_clean_repository(repo: Path):
    result = _run_assertion(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_assertion_fails_on_a_persisted_credential(repo: Path):
    _git(repo, "config", "--local", "http.https://github.com/.extraheader", "AUTHORIZATION: basic x")
    result = _run_assertion(repo)
    assert result.returncode != 0
    assert "git credential persisted" in result.stdout


def test_the_assertion_fails_on_a_credential_in_an_included_file(repo: Path):
    included = repo.parent / "included.gitconfig"
    included.write_text('[http "https://github.com/"]\n\textraheader = AUTHORIZATION: basic x\n')
    _git(repo, "config", "--local", "include.path", str(included))
    result = _run_assertion(repo)
    assert result.returncode != 0
