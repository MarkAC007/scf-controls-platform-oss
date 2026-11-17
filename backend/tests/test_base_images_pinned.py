# SPDX-License-Identifier: AGPL-3.0-only
"""Every third-party image the product builds on or runs is pinned by digest.

A tag such as ``postgres:15-alpine`` resolves to whatever the registry serves
on the day an operator pulls or builds, so two installs of the same release
can run different images. A ``@sha256:`` digest names one exact image. This
covers the two places the product names an image: a Dockerfile ``FROM`` line
and a Compose ``image:`` key.

The platform's own images (``ghcr.io/markac007/scf-*``) are tagged by release
and are out of scope here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILES = sorted(REPO_ROOT.glob("Dockerfile*"))
COMPOSE_FILES = sorted(REPO_ROOT.glob("docker-compose*.yml"))
UPGRADING = REPO_ROOT / "UPGRADING.md"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

DIGEST = re.compile(r"@(sha256:[0-9a-f]{64})$")
FROM_LINE = re.compile(
    r"^FROM\s+(?:--platform=\S+\s+)?(?P<image>\S+)(?:\s+AS\s+(?P<stage>\S+))?\s*$",
    re.IGNORECASE,
)
OWN_IMAGE = "ghcr.io/markac007/scf-"

pytestmark = pytest.mark.skipif(
    not DOCKERFILES or not COMPOSE_FILES,
    reason="the Dockerfiles and Compose files are not in this tree",
)


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that also reads Compose's own tags (`!override`, `!reset`)."""


def _untagged(loader, _suffix, node):
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _untagged)


def _load_compose(compose: Path) -> dict:
    loader = _ComposeLoader(compose.read_text())
    try:
        return loader.get_single_data() or {}
    finally:
        loader.dispose()


def _from_images(dockerfile: Path) -> list[tuple[int, str]]:
    """(line number, image) for every FROM that names a registry image."""
    found, stages = [], set()
    for number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
        match = FROM_LINE.match(line.strip())
        if not match:
            continue
        image = match.group("image")
        # `scratch` is the empty image; a stage name refers to an earlier FROM.
        if image != "scratch" and image not in stages:
            found.append((number, image))
        if match.group("stage"):
            stages.add(match.group("stage"))
    return found


def _compose_images(compose: Path) -> list[tuple[str, str]]:
    """(service, image) for every third-party image a Compose file runs."""
    services = _load_compose(compose).get("services") or {}
    return [
        (name, service["image"])
        for name, service in services.items()
        if "image" in service and not service["image"].startswith(OWN_IMAGE)
    ]


def _all_images() -> list[tuple[str, str]]:
    """(where, image) across every Dockerfile and Compose file."""
    found = []
    for dockerfile in DOCKERFILES:
        found += [(f"{dockerfile.name}:{n}", image) for n, image in _from_images(dockerfile)]
    for compose in COMPOSE_FILES:
        found += [(f"{compose.name}: {svc}", image) for svc, image in _compose_images(compose)]
    return found


def _repository(image: str) -> str:
    """The image without its tag or digest: `postgres`, `quay.io/keycloak/keycloak`."""
    path, _, last = image.split("@")[0].rpartition("/")
    name = last.split(":")[0]
    return f"{path}/{name}" if path else name


@pytest.mark.parametrize("dockerfile", DOCKERFILES, ids=lambda p: p.name)
def test_every_base_image_is_pinned_by_digest(dockerfile: Path):
    unpinned = [
        f"{dockerfile.name}:{number}: {image}"
        for number, image in _from_images(dockerfile)
        if not DIGEST.search(image)
    ]
    assert not unpinned, "\n".join(unpinned)


@pytest.mark.parametrize("compose", COMPOSE_FILES, ids=lambda p: p.name)
def test_every_compose_image_is_pinned_by_digest(compose: Path):
    unpinned = [
        f"{compose.name}: {service}: {image}"
        for service, image in _compose_images(compose)
        if not DIGEST.search(image)
    ]
    assert not unpinned, "\n".join(unpinned)


def test_the_scan_finds_the_images_it_is_meant_to_guard():
    # Guards the guard: if the scan stops seeing these, the tests above pass
    # on nothing. Every FROM line in the tree must be accounted for as well.
    seen = {_repository(image) for _, image in _all_images()}
    assert {"python", "node", "nginx", "postgres", "redis", "quay.io/keycloak/keycloak"} <= seen

    for dockerfile in DOCKERFILES:
        from_lines = [
            line for line in dockerfile.read_text().splitlines() if re.match(r"\s*FROM\s", line, re.IGNORECASE)
        ]
        parsed = [line for line in from_lines if FROM_LINE.match(line.strip())]
        assert len(parsed) == len(from_lines), f"{dockerfile.name}: a FROM line the scan cannot read"


def test_one_image_has_one_digest_everywhere():
    # The same image is named in several files (PostgreSQL in three Compose
    # files and a Dockerfile). A pin updated in one place only would leave the
    # product running two builds of the same thing.
    digests: dict[str, set[str]] = {}
    for _, image in _all_images():
        match = DIGEST.search(image)
        if match:
            digests.setdefault(_repository(image), set()).add(match.group(1))
    split = {repo: sorted(found) for repo, found in digests.items() if len(found) > 1}
    assert not split, f"pinned to more than one digest: {split}"


@pytest.mark.skipif(not UPGRADING.exists(), reason="UPGRADING.md is not in this tree")
def test_the_offline_pull_list_names_only_digests_the_release_uses():
    # UPGRADING.md tells air-gapped operators which images to carry across. A
    # digest listed there that no file uses any more sends them to fetch the
    # wrong image.
    in_use = {DIGEST.search(image).group(1) for _, image in _all_images() if DIGEST.search(image)}
    listed = set(re.findall(r"docker pull \S+@(sha256:[0-9a-f]{64})", UPGRADING.read_text()))
    assert listed, "UPGRADING.md no longer lists any image to pre-cache"
    assert listed <= in_use, f"listed in UPGRADING.md but used nowhere: {sorted(listed - in_use)}"


@pytest.mark.skipif(not CI.exists(), reason="ci.yml is not in this tree")
def test_ci_tests_against_the_postgres_image_the_product_ships():
    jobs = yaml.safe_load(CI.read_text())["jobs"]
    tested = {
        service["image"]
        for job in jobs.values()
        for name, service in (job.get("services") or {}).items()
        if name == "postgres"
    }
    pinned = {DIGEST.search(image).group(1) for image in tested if DIGEST.search(image)}
    if not pinned:
        pytest.skip("ci.yml does not pin its postgres service by digest yet")
    shipped = _load_compose(REPO_ROOT / "docker-compose.yml")["services"]["postgres"]["image"]
    assert pinned == {DIGEST.search(shipped).group(1)}, (
        "ci.yml tests on a different PostgreSQL image from the one docker-compose.yml ships; "
        "change both in the same pull request"
    )
