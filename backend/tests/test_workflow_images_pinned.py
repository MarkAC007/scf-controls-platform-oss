"""Every container image a workflow runs is pinned by digest.

A tag such as ``postgres:16``, or no tag at all, resolves to whatever the
registry serves on the day. A ``@sha256:`` digest names one exact image. This
covers the three places a workflow names an image: a job ``container``, a
``services`` entry, and an ``*_IMAGE`` variable handed to ``docker run``.

The test database is also held to the PostgreSQL major the product ships, so
CI does not pass on a version operators do not run.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))
COMPOSE = REPO_ROOT / "docker-compose.yml"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")

pytestmark = pytest.mark.skipif(not WORKFLOWS, reason="no workflows in this tree")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def _images(workflow: Path) -> list[tuple[str, str]]:
    """(where, image) for every image the workflow names."""
    doc = _load(workflow)
    found = []

    def env_images(where: str, env) -> None:
        for key, value in (env or {}).items():
            # A registry reference contains a slash or a colon; a bare image
            # *name* used to build a reference elsewhere does not.
            if key.endswith("_IMAGE") and isinstance(value, str) and re.search(r"[/:]", value):
                found.append((f"{where} env {key}", value))

    env_images("workflow", doc.get("env"))
    for job_name, job in (doc.get("jobs") or {}).items():
        env_images(f"job {job_name}", job.get("env"))
        container = job.get("container")
        if isinstance(container, str):
            found.append((f"job {job_name} container", container))
        elif isinstance(container, dict) and "image" in container:
            found.append((f"job {job_name} container", container["image"]))
        for service_name, service in (job.get("services") or {}).items():
            found.append((f"job {job_name} service {service_name}", service["image"]))
        for step in job.get("steps") or []:
            env_images(f"job {job_name} step", step.get("env"))
            uses = step.get("uses", "")
            if uses.startswith("docker://"):
                found.append((f"job {job_name} step uses", uses))
    return found


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_image_is_pinned_by_digest(workflow: Path):
    unpinned = [
        f"{workflow.name}: {where}: {image}"
        for where, image in _images(workflow)
        if "${{" not in image and not DIGEST.search(image)
    ]
    assert not unpinned, "\n".join(unpinned)


def test_the_scan_finds_the_images_it_is_meant_to_guard():
    # Guards the guard: if the scan stops seeing these, the test above passes
    # on nothing.
    seen = {image.split("@")[0].split(":")[0] for wf in WORKFLOWS for _, image in _images(wf)}
    ci = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    if ci.exists():
        assert "postgres" in seen


@pytest.mark.skipif(not COMPOSE.exists(), reason="docker-compose.yml is not in this tree")
def test_ci_tests_on_the_postgres_major_the_product_ships():
    ci = REPO_ROOT / ".github" / "workflows" / "ci.yml"
    if not ci.exists():
        pytest.skip("ci.yml is not in this tree")
    shipped = _load(COMPOSE)["services"]["postgres"]["image"]
    tested = [image for where, image in _images(ci) if "service postgres" in where]
    assert tested, "ci.yml has no postgres service"

    def major(image: str) -> str:
        return re.match(r"postgres:(\d+)", image).group(1)

    assert {major(image) for image in tested} == {major(shipped)}
