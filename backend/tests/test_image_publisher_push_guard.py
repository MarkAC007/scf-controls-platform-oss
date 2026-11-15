"""The image publisher can push only an image built from the sanitized snapshot.

``publish-oss-images.yml`` has two build contexts: the OSS snapshot that
``release-oss.yml`` hands it, and a plain checkout of this private repository
for manual dry runs. Only the first has been through ``scripts/prepare-oss.sh``.
Until this guard a manual dispatch with ``push=true`` built from the private
checkout and pushed the result to the public registry under any tag.

Two things hold the door shut, and both are pinned here:

* ``workflow_dispatch`` declares no ``push`` input, so GitHub rejects a manual
  dispatch that passes one, and
* the "Determine push intent" step fails when a push is requested without a
  snapshot. That step's own shell script is executed below, so the test fails
  if the script changes, not only if its text does.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"
ORCHESTRATOR = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"

# The publisher is stripped from the OSS snapshot (the registry-push credential
# must never ship), and the public repo runs this same test file.
pytestmark = pytest.mark.skipif(
    not WORKFLOW.exists(),
    reason="publish-oss-images.yml is stripped from the OSS snapshot by scripts/prepare-oss.sh",
)


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    # PyYAML reads the bare key `on` as the boolean True.
    doc["on"] = doc.pop(True) if True in doc else doc["on"]
    return doc


def _steps() -> list[dict]:
    return _load(WORKFLOW)["jobs"]["build"]["steps"]


def _intent_step() -> dict:
    matches = [s for s in _steps() if s.get("id") == "intent"]
    assert len(matches) == 1, "expected exactly one step with id 'intent'"
    return matches[0]


def _run_intent(tmp_path: Path, push: str, snapshot: str) -> tuple[int, str]:
    output = tmp_path / "github_output"
    output.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_OUTPUT": str(output),
        "PUSH_INPUT": push,
        "SNAPSHOT_ARTIFACT": snapshot,
    }
    result = subprocess.run(
        ["bash", "-c", _intent_step()["run"]],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, output.read_text().strip()


def test_manual_dispatch_declares_no_push_input():
    inputs = _load(WORKFLOW)["on"]["workflow_dispatch"]["inputs"]
    assert "push" not in inputs
    assert "snapshot_artifact" not in inputs


def test_intent_step_reads_both_inputs_through_env():
    env = _intent_step()["env"]
    assert env["PUSH_INPUT"] == "${{ inputs.push }}"
    assert env["SNAPSHOT_ARTIFACT"] == "${{ inputs.snapshot_artifact }}"


def test_push_without_a_snapshot_fails_the_job(tmp_path):
    code, output = _run_intent(tmp_path, push="true", snapshot="")
    assert code != 0
    assert "push=true" not in output


def test_push_with_a_snapshot_is_allowed(tmp_path):
    code, output = _run_intent(tmp_path, push="true", snapshot="oss-snapshot")
    assert code == 0
    assert output == "push=true"


@pytest.mark.parametrize(
    "push, snapshot",
    [
        ("", ""),  # manual dispatch: the input does not exist
        ("false", ""),
        ("false", "oss-snapshot"),  # release dry run
        ("TRUE", ""),  # only the literal 'true' asks for a push
    ],
)
def test_anything_but_a_push_request_builds_only(tmp_path, push, snapshot):
    code, output = _run_intent(tmp_path, push=push, snapshot=snapshot)
    assert code == 0
    assert output == "push=false"


def test_every_registry_write_is_gated_on_the_intent_step():
    steps = _steps()
    gate = "steps.intent.outputs.push == 'true'"

    login = next(s for s in steps if "docker/login-action" in s.get("uses", ""))
    assert gate in login["if"]

    build = next(s for s in steps if "docker/build-push-action" in s.get("uses", ""))
    # Pushed by digest: the exporter step is the only switch, and it reads
    # nothing but the intent step's answer. A `push:` input beside it would be
    # a second switch. test_images_by_digest.py runs the exporter script.
    assert "push" not in build["with"]
    assert build["with"]["outputs"] == "${{ steps.exporter.outputs.outputs }}"
    exporter = next(s for s in steps if s.get("id") == "exporter")
    assert exporter["env"]["PUSH"] == "${{ steps.intent.outputs.push }}"

    intent_index = steps.index(_intent_step())
    assert intent_index < steps.index(login) < steps.index(exporter) < steps.index(build)


@pytest.mark.skipif(not ORCHESTRATOR.exists(), reason="release-oss.yml is not in this tree")
def test_the_orchestrator_always_names_the_snapshot():
    images = _load(ORCHESTRATOR)["jobs"]["images"]
    assert images["uses"] == "./.github/workflows/publish-oss-images.yml"
    assert images["with"]["snapshot_artifact"], "a release build must come from the snapshot"
