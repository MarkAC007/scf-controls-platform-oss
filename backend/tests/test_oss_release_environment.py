# SPDX-License-Identifier: AGPL-3.0-only
"""The public-repo token lives in the ``oss-release`` environment (plan 2.3).

Only ``cross_and_merge`` runs in that environment, and its first step refuses a
token that reaches more than the public repo: one that can read the private
source repo, one that lists any private repository, or one without push on the
public repo. The probe runs here against a stand-in ``gh``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"
PROBE = "Check the public-repo token is limited to the public repo"
PUBLIC, SOURCE = "MarkAC007/scf-controls-platform-oss", "MarkAC007/scf-controls-platform"

pytestmark = pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")


def _doc() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_only_cross_and_merge_runs_in_the_environment():
    jobs = _doc()["jobs"]
    assert {name: job.get("environment") for name, job in jobs.items() if job.get("environment")} == {
        "cross_and_merge": "oss-release"
    }


def test_the_token_is_read_only_by_cross_and_merge():
    for name, job in _doc()["jobs"].items():
        if name != "cross_and_merge":
            assert "SCF_PLATFORM_OSS_SECRET" not in yaml.safe_dump(job), name


def test_the_probe_is_the_first_step():
    steps = _doc()["jobs"]["cross_and_merge"]["steps"]
    assert steps[0]["name"] == PROBE
    assert steps[0]["env"]["GH_TOKEN"] == "${{ secrets.SCF_PLATFORM_OSS_SECRET }}"
    assert "continue-on-error" not in steps[0]


def test_the_header_says_the_environment_has_no_reviewers():
    header = WORKFLOW.read_text().split("name: Release OSS", 1)[0]
    assert "oss-release" in header and "no reviewers" in header and "`main`" in header


FAKE_GH = r"""#!/usr/bin/env bash
# Stand-in for gh: answers from FAKE_* variables.
args="$*"
case "$args" in
  *"repos/$SOURCE_REPO "*) [ "${FAKE_SEES_SOURCE:-0}" = 1 ] && { echo "$SOURCE_REPO"; exit 0; }; echo "HTTP 404" >&2; exit 1 ;;
  *"/user/repos?visibility=private"*) [ "${FAKE_LIST_FAILS:-0}" = 1 ] && exit 1; printf '%s' "${FAKE_PRIVATE:-}"; exit 0 ;;
  *"repos/$PUBLIC_REPO "*) [ "${FAKE_NO_PUBLIC:-0}" = 1 ] && exit 1; echo "${FAKE_PUSH:-true}"; exit 0 ;;
esac
echo "unexpected gh call: $args" >&2; exit 99
"""


def _probe(tmp_path: Path, token="github_pat_x", **fake) -> subprocess.CompletedProcess:
    run = _doc()["jobs"]["cross_and_merge"]["steps"][0]["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "GH_TOKEN": token, "PUBLIC_REPO": PUBLIC, "SOURCE_REPO": SOURCE}
    env |= {f"FAKE_{k.upper()}": v for k, v in fake.items()}
    return subprocess.run(["bash", "-c", run], env=env, capture_output=True, text=True, timeout=30)


def test_a_token_limited_to_the_public_repo_passes(tmp_path):
    proc = _probe(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "reaches no private repository" in proc.stdout


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"token": ""}, "is not set in the oss-release environment"),
        ({"sees_source": "1"}, f"can read {SOURCE}"),
        ({"private": "MarkAC007/a\nMarkAC007/b"}, "reaches 2 private repositories"),
        ({"list_fails": "1"}, "could not list"),
        ({"no_public": "1"}, f"cannot read {PUBLIC}"),
        ({"push": "false"}, f"no push access to {PUBLIC}"),
    ],
    ids=["no token", "classic token sees the source repo", "private repos listed", "list fails", "public repo unreadable", "no push"],
)
def test_a_token_that_reaches_more_or_less_is_refused(tmp_path, kwargs, message):
    proc = _probe(tmp_path, **kwargs)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert message in proc.stdout
    assert "unexpected gh call" not in proc.stderr
