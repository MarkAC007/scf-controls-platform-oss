"""The update automation keeps the rules the pins depend on.

Pinned actions and images only stay current if something proposes the next
pin. ``.github/dependabot.yml`` does that. These tests hold it to the rules it
was introduced with:

* every update waits out a cooldown, so a release published an hour ago is
  never proposed;
* one dependency per pull request, never a grouped bulk update;
* everything that is pinned is covered;
* a version cap in a Python requirements file has an ignore rule beside it, so
  the cap is not proposed away;
* the file stays out of the public snapshot, and no second update tool is
  configured beside it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".github" / "dependabot.yml"
PREPARE_OSS = REPO_ROOT / "scripts" / "prepare-oss.sh"
MINIMUM_COOLDOWN_DAYS = 7

# The .in files each `uv` entry updates.
UV_SOURCES = {
    "/backend": ["backend/requirements.in", "backend/requirements-dev.in"],
    "/scripts": ["scripts/requirements-importer.in"],
}
# A line that only caps a package: `name<version`, with no lower bound or pin.
CAP = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)<(\d+(?:\.\d+)*)\s*(?:#.*)?$")

# The public snapshot does not carry the file, so there is nothing to check there.
pytestmark = pytest.mark.skipif(not CONFIG.exists(), reason="dependabot.yml is not in this tree")


def _updates() -> list[dict]:
    return yaml.safe_load(CONFIG.read_text())["updates"]


def _label(entry: dict) -> str:
    return f"{entry['package-ecosystem']} {entry['directory']}"


def test_the_config_is_version_2_with_updates():
    doc = yaml.safe_load(CONFIG.read_text())
    assert doc["version"] == 2
    assert doc["updates"]


def test_every_update_waits_out_the_cooldown():
    too_soon = [
        _label(entry)
        for entry in _updates()
        if (entry.get("cooldown") or {}).get("default-days", 0) < MINIMUM_COOLDOWN_DAYS
    ]
    assert not too_soon, f"no cooldown of at least {MINIMUM_COOLDOWN_DAYS} days: {too_soon}"


def test_no_dependency_is_excluded_from_the_cooldown():
    # `cooldown.exclude` lets named dependencies skip the wait, and
    # `semver-*-days` can set a shorter one for a class of update.
    for entry in _updates():
        cooldown = entry["cooldown"]
        assert "exclude" not in cooldown, _label(entry)
        shorter = {k: v for k, v in cooldown.items() if k.endswith("-days") and v < MINIMUM_COOLDOWN_DAYS}
        assert not shorter, f"{_label(entry)}: {shorter}"


def test_updates_are_never_grouped():
    grouped = [_label(entry) for entry in _updates() if "groups" in entry]
    assert not grouped, f"one dependency per pull request; grouped: {grouped}"


def test_everything_that_is_pinned_is_covered():
    covered = {(entry["package-ecosystem"], entry["directory"]) for entry in _updates()}
    assert {
        ("github-actions", "/"),
        ("docker", "/"),
        ("docker-compose", "/"),
        ("uv", "/backend"),
        ("uv", "/scripts"),
        ("pip", "/.github/requirements"),
        ("npm", "/webclient"),
    } <= covered
    assert len(covered) == len(_updates()), "the same ecosystem and directory is listed twice"


def test_locks_compiled_by_uv_are_not_updated_by_the_pip_ecosystem():
    # The pip ecosystem would recompile these locks with pip-compile, which
    # resolves for one platform and drops the markers the others need.
    covered = {(entry["package-ecosystem"], entry["directory"]) for entry in _updates()}
    assert not {("pip", "/backend"), ("pip", "/scripts")} & covered


@pytest.mark.parametrize(
    ("ecosystem", "directory", "manifest"),
    [
        ("uv", "/backend", "backend/requirements.in"),
        ("uv", "/backend", "backend/requirements.txt"),
        ("uv", "/backend", "backend/requirements-dev.txt"),
        ("uv", "/scripts", "scripts/requirements-importer.in"),
        ("uv", "/scripts", "scripts/requirements-importer.txt"),
        ("pip", "/.github/requirements", ".github/requirements/uv.txt"),
        ("pip", "/.github/requirements", ".github/requirements/zizmor.txt"),
        ("npm", "/webclient", "webclient/package-lock.json"),
        ("docker", "/", "Dockerfile.backend"),
        ("docker-compose", "/", "docker-compose.yml"),
    ],
)
def test_each_covered_directory_holds_the_file_it_is_there_for(ecosystem, directory, manifest):
    # An entry that points at a directory with nothing in it produces no
    # updates and no error.
    assert (REPO_ROOT / manifest).is_file(), f"{ecosystem} {directory}: {manifest} is missing"


def _caps(directory: str) -> dict[str, list[str]]:
    """What the ignore rules must say for the caps in a directory's .in files."""
    caps = {}
    for source in UV_SOURCES[directory]:
        for line in (REPO_ROOT / source).read_text().splitlines():
            match = CAP.match(line.strip())
            if match:
                caps[match.group(1).lower()] = [f">={match.group(2)}"]
    return caps


def test_the_caps_this_test_looks_for_are_there():
    # If the caps were rewritten in a form CAP does not match, the next test
    # would compare nothing with nothing and pass.
    assert _caps("/backend") and _caps("/scripts")


@pytest.mark.parametrize("directory", sorted(UV_SOURCES))
def test_every_version_cap_has_an_ignore_rule_and_every_rule_a_cap(directory):
    # A cap (`numpy<2.5`) holds a package at the last release line that supports
    # the Python the images run; see the block at the end of
    # backend/requirements.in. Without an ignore rule Dependabot proposes
    # raising the cap, and the lock that results pins the package twice, which
    # Dependabot then cannot update. A rule left behind after its cap is removed
    # would hold the package back for no reason.
    entry = next(e for e in _updates() if e["package-ecosystem"] == "uv" and e["directory"] == directory)
    ignored = {rule["dependency-name"]: rule.get("versions") for rule in entry.get("ignore", [])}
    assert ignored == _caps(directory), (
        f"uv {directory}: the ignore rules {ignored} do not match the caps {_caps(directory)} "
        f"in {UV_SOURCES[directory]}"
    )


def test_a_postgres_major_version_is_never_proposed():
    # A PostgreSQL major upgrade needs a dump and restore; it must arrive as a
    # planned release, not as a routine update.
    for entry in _updates():
        if entry["package-ecosystem"] not in ("docker", "docker-compose"):
            continue
        rules = [r for r in entry.get("ignore", []) if r["dependency-name"] == "postgres"]
        assert rules, _label(entry)
        assert "version-update:semver-major" in rules[0]["update-types"], _label(entry)


def test_no_second_update_tool_is_configured():
    # renovate.json was removed with this change: the app was never installed,
    # and its rules would have merged updates automatically with no cooldown.
    for name in ("renovate.json", "renovate.json5", ".renovaterc", ".renovaterc.json", ".github/renovate.json"):
        assert not (REPO_ROOT / name).exists(), f"{name} configures a second update tool"


@pytest.mark.skipif(not PREPARE_OSS.exists(), reason="prepare-oss.sh is not in this tree")
def test_the_config_does_not_ship_to_the_public_snapshot():
    text = PREPARE_OSS.read_text()
    assert "rm -f  .github/dependabot.yml" in text, "prepare-oss.sh no longer removes the file"
    start = text.index("for p in catalog-source")
    loop = text[start : text.index("; do", start)]
    assert ".github/dependabot.yml" in loop, "the file is not in the snapshot's forbidden-path check"
