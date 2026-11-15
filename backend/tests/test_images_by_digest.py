"""Release images are pushed by digest; no tag exists until the release is public.

Before this change each matrix leg of ``publish-oss-images.yml`` pushed
``v<version>`` the moment its build finished. Anyone could pull that tag while
the public repository's checks were still running, and a re-dispatch in the
middle of a release moved it to new bytes.

Now:

* the build step pushes by digest only (a bare image name plus
  ``push-by-digest=true``), records the digest the builder returned, and hands
  it on as one artifact per image;
* ``images_verify`` in ``release-oss.yml`` reads those records and probes GHCR
  anonymously *by digest*, and the registry must answer with that digest;
* ``promote_latest`` writes ``v<version>``, ``sha-<commit>`` and ``latest``
  (covered in ``test_release_resume_record.py``).

The two shell steps are executed here against stand-in files and a stand-in
registry, not only read.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from tests.test_release_resume_record import (  # noqa: E402
    BACKEND_IMAGE,
    FRONTEND_IMAGE,
    ORCHESTRATOR,
    RELEASED_BE,
    RELEASED_FE,
    REPO_ROOT,
    TAG,
    World,
    _manifest,
)

PUBLISHER = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"

needs_publisher = pytest.mark.skipif(
    not PUBLISHER.exists(),
    reason="publish-oss-images.yml is stripped from the OSS snapshot by scripts/prepare-oss.sh",
)
needs_orchestrator = pytest.mark.skipif(
    not ORCHESTRATOR.exists(), reason="release-oss.yml is private-only"
)

GOOD = "sha256:" + "ab" * 32


# -- the publisher ------------------------------------------------------------


def _publisher_steps() -> list[dict]:
    return yaml.safe_load(PUBLISHER.read_text())["jobs"]["build"]["steps"]


def _publisher_step(*, step_id: str | None = None, name: str | None = None) -> dict:
    steps = [s for s in _publisher_steps() if (step_id and s.get("id") == step_id) or (name and s.get("name") == name)]
    assert len(steps) == 1, f"expected exactly one publisher step {step_id or name!r}"
    return steps[0]


@needs_publisher
def test_the_build_pushes_by_digest_and_never_by_tag():
    build = _publisher_step(step_id="build")["with"]
    # No `push:` input: with push-by-digest the exporter decides, and `push: true`
    # beside a tagged name is exactly what this change removes.
    assert "push" not in build
    assert build["tags"].strip() == "ghcr.io/${{ steps.ns.outputs.owner }}/${{ matrix.name }}"
    assert ":" not in build["tags"].split("/")[-1], "a tag on the image name would be pushed"
    # The exporter step's output is the build's only switch; its script runs below.
    assert build["outputs"] == "${{ steps.exporter.outputs.outputs }}"
    exporter = _publisher_step(step_id="exporter")
    assert exporter["env"]["PUSH"] == "${{ steps.intent.outputs.push }}"


def _choose_exporter(tmp_path: Path, push: str) -> tuple[int, dict, str]:
    out = tmp_path / "github_output"
    out.write_text("")
    env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path), "GITHUB_OUTPUT": str(out),
           "PUSH": push, "IMAGE": BACKEND_IMAGE}
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _publisher_step(step_id="exporter")["run"]],
        env=env, capture_output=True, text=True, timeout=30,
    )
    values = dict(line.split("=", 1) for line in out.read_text().splitlines())
    return proc.returncode, values, proc.stdout + proc.stderr


@needs_publisher
def test_a_real_run_pushes_by_digest_with_no_tag(tmp_path):
    code, values, _ = _choose_exporter(tmp_path, "true")
    assert code == 0
    assert values == {"outputs": "type=image,push-by-digest=true,name-canonical=true,push=true"}


@needs_publisher
def test_a_dry_run_writes_an_archive_and_reaches_no_registry(tmp_path):
    code, values, _ = _choose_exporter(tmp_path, "false")
    assert code == 0
    archive = f"{tmp_path}/oci/{BACKEND_IMAGE}.tar"
    assert values == {"outputs": f"type=oci,dest={archive}", "archive": archive}
    assert "type=image" not in values["outputs"] and "push" not in values["outputs"]
    assert (tmp_path / "oci").is_dir()


@needs_publisher
@pytest.mark.parametrize("push", ["", "True", "yes", "true\n"])
def test_an_unclear_push_intent_builds_nothing(tmp_path, push):
    code, values, text = _choose_exporter(tmp_path, push)
    assert code != 0
    assert values == {}
    assert "::error::" in text


@needs_publisher
def test_the_metadata_tags_reach_nothing():
    for step in _publisher_steps():
        assert "steps.meta.outputs.tags" not in yaml.safe_dump(step), step.get("name")


@needs_publisher
def test_every_leg_uploads_the_digest_it_pushed():
    names = [s.get("name") or s.get("id") for s in _publisher_steps()]
    assert names.index("Build and push") < names.index("Record the pushed digest") < names.index("Upload the pushed digest")
    record = _publisher_step(step_id="digest")
    assert record["env"] == {"DIGEST": "${{ steps.build.outputs.digest }}", "IMAGE": "${{ matrix.name }}"}
    upload = _publisher_step(name="Upload the pushed digest")
    assert upload["uses"].startswith("actions/upload-artifact@")
    assert len(upload["uses"].split("@")[1].split()[0]) == 40, "pinned by full commit sha"
    assert upload["with"]["name"] == "image-digest-${{ matrix.name }}"
    assert upload["with"]["path"] == "${{ runner.temp }}/image-digest/${{ matrix.name }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is True


def _record_digest(tmp_path: Path, digest: str) -> tuple[subprocess.CompletedProcess, Path]:
    env = {"PATH": os.environ["PATH"], "RUNNER_TEMP": str(tmp_path), "DIGEST": digest, "IMAGE": BACKEND_IMAGE}
    proc = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _publisher_step(step_id="digest")["run"]],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return proc, tmp_path / "image-digest" / BACKEND_IMAGE


@needs_publisher
def test_the_digest_the_builder_returned_is_recorded(tmp_path):
    proc, file = _record_digest(tmp_path, GOOD)
    assert proc.returncode == 0, proc.stderr
    assert file.read_text() == GOOD + "\n"


@needs_publisher
@pytest.mark.parametrize("digest", ["", "sha256:" + "ab" * 31, "sha256:" + "AB" * 32, "sha512:" + "ab" * 32,
                                    GOOD + "\nsha256:" + "cd" * 32, "latest"])
def test_a_build_that_returned_no_digest_records_nothing(tmp_path, digest):
    proc, file = _record_digest(tmp_path, digest)
    assert proc.returncode != 0
    assert "::error::" in proc.stdout + proc.stderr
    assert not file.exists()


# -- images_verify -------------------------------------------------------------


@needs_orchestrator
@needs_publisher
def test_images_verify_downloads_what_the_publisher_uploads():
    steps = yaml.safe_load(ORCHESTRATOR.read_text())["jobs"]["images_verify"]["steps"]
    download = steps[0]
    assert download["uses"].startswith("actions/download-artifact@")
    assert len(download["uses"].split("@")[1].split()[0]) == 40, "pinned by full commit sha"
    assert download["with"] == {
        "pattern": "image-digest-*",
        "path": "${{ runner.temp }}/image-digest",
        "merge-multiple": True,
    }
    uploaded = _publisher_step(name="Upload the pushed digest")["with"]
    assert uploaded["name"].startswith("image-digest-")
    # merge-multiple puts each per-image file straight under `path`, where the
    # verify step looks for it by image name.
    assert uploaded["path"].endswith("/image-digest/${{ matrix.name }}")
    assert [s.get("id") for s in steps].index("verify") == 1


class Pushed:
    """The registry after the build legs, and the digests they recorded."""

    def __init__(self, tmp_path: Path):
        self.world = World(tmp_path)
        self.dir = tmp_path / "runner-temp"
        (self.dir / "image-digest").mkdir(parents=True)
        for image, (text, digest) in ((BACKEND_IMAGE, RELEASED_BE), (FRONTEND_IMAGE, RELEASED_FE)):
            self.world.state["images"][image]["blobs"][digest] = text
            self.write(image, digest + "\n")

    def write(self, image: str, content: str | None) -> "Pushed":
        file = self.dir / "image-digest" / image
        if content is None:
            file.unlink(missing_ok=True)
        else:
            file.write_text(content)
        return self

    def run(self):
        return self.world.run("images_verify", "verify", RUNNER_TEMP=str(self.dir))


def _heads(run) -> list:
    return [c[1:] for c in run.calls("head")]


@needs_orchestrator
def test_the_recorded_digests_are_found_by_digest(tmp_path):
    run = Pushed(tmp_path).run()
    assert run.code == 0, run.text
    assert run.outputs == {"backend_digest": RELEASED_BE[1], "frontend_digest": RELEASED_FE[1]}
    assert _heads(run) == [[BACKEND_IMAGE, RELEASED_BE[1]], [FRONTEND_IMAGE, RELEASED_FE[1]]]
    assert run.calls("put") == []


@needs_orchestrator
def test_the_version_tag_is_never_asked_for(tmp_path):
    pushed = Pushed(tmp_path)
    # A stale v<version> tag naming other bytes must not matter, nor be read.
    other = _manifest("someone else's backend")
    pushed.world.image(BACKEND_IMAGE, TAG, other).image(FRONTEND_IMAGE, TAG, other)
    run = pushed.run()
    assert run.code == 0, run.text
    assert all(ref != TAG for _, ref in _heads(run))
    assert run.outputs["backend_digest"] == RELEASED_BE[1]


@needs_orchestrator
@pytest.mark.parametrize("image", ["backend", "frontend"])
@pytest.mark.parametrize(
    "content",
    [None, "", "\n", "sha256:" + "ab" * 31 + "\n", GOOD + "\n" + GOOD + "\n", "  " + GOOD + "\n", "latest\n"],
    ids=["missing", "empty", "blank", "short", "two-lines", "padded", "a-tag"],
)
def test_a_missing_or_malformed_record_stops_before_the_registry_is_asked(tmp_path, image, content):
    name = BACKEND_IMAGE if image == "backend" else FRONTEND_IMAGE
    run = Pushed(tmp_path).write(name, content).run()
    assert run.code != 0, run.text
    assert "::error::" in run.text and name in run.text
    assert run.calls("head") == [] and run.calls("token") == []
    assert run.outputs == {}


@needs_orchestrator
def test_a_registry_that_answers_with_another_digest_is_not_trusted(tmp_path):
    pushed = Pushed(tmp_path)
    # A registry (or a proxy) that answers 200 for the digest but names others.
    pushed.world.state["images"][BACKEND_IMAGE]["tags"][RELEASED_BE[1]] = _manifest("other bytes")[1]
    pushed.world.state["images"][BACKEND_IMAGE]["blobs"].pop(RELEASED_BE[1])
    run = pushed.run()
    assert run.code != 0, run.text
    assert "GHCR answered" in run.text
    assert run.outputs == {}


@needs_orchestrator
@pytest.mark.parametrize("code", ["401", "403"])
def test_a_private_package_fails_at_once_with_the_fix(tmp_path, code):
    pushed = Pushed(tmp_path)
    pushed.world.state["images"][FRONTEND_IMAGE]["codes"][RELEASED_FE[1]] = code
    run = pushed.run()
    assert run.code != 0
    assert "PRIVATE" in run.text and FRONTEND_IMAGE in run.text
    assert len(_heads(run)) == 2, "it should not keep polling a private package"


@needs_orchestrator
def test_digests_that_never_appear_time_out(tmp_path):
    pushed = Pushed(tmp_path)
    pushed.world.state["images"][BACKEND_IMAGE]["blobs"].pop(RELEASED_BE[1])
    run = pushed.run()
    assert run.code != 0
    assert "were not anonymously pullable" in run.text
    assert run.outputs == {}
