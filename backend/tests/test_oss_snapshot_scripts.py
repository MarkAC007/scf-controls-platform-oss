"""The OSS snapshot must ship every script that shipped files refer to.

`scripts/prepare-oss.sh` publishes a path-exact allowlist of `scripts/` files
(`OSS_SCRIPTS`) and deletes the rest. v0.32.0 shipped `docker-compose.secrets.yml`
bind-mounting `scripts/docker/with-file-secrets.sh`, and `UPGRADING.md` telling
operators to run `scripts/install.sh` and `scripts/backup.sh`, while the
publisher deleted all three: nothing in CI ran the publisher, and its own
assertion only knew the pre-#947 set. The publisher now re-derives the required
set from the shipped files at build time; this test does the same on every PR
so the next addition fails here rather than after a release.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
PREPARE_OSS = REPO_ROOT / "scripts" / "prepare-oss.sh"

pytestmark = pytest.mark.skipif(
    not PREPARE_OSS.exists(), reason="scripts/prepare-oss.sh not present"
)


def oss_scripts() -> set[str]:
    """The OSS_SCRIPTS=( ... ) allowlist, one path per line, comments stripped."""
    text = PREPARE_OSS.read_text()
    match = re.search(r"^OSS_SCRIPTS=\(\n(.*?)^\)", text, re.S | re.M)
    assert match, "OSS_SCRIPTS=( ... ) block not found in prepare-oss.sh"
    paths = set()
    for line in match.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            paths.add(line)
    assert paths, "OSS_SCRIPTS is empty"
    return paths


def shipped_compose_files() -> list[Path]:
    return sorted(REPO_ROOT.glob("docker-compose*.yml"))


def shipped_docs() -> list[Path]:
    # README.oss.md becomes the public README.md; UPGRADING.md ships as-is.
    return [p for p in (REPO_ROOT / "README.oss.md", REPO_ROOT / "UPGRADING.md") if p.exists()]


BIND_MOUNT = re.compile(r"-\s*\./(scripts/[A-Za-z0-9_./-]+):")
# A preceding slash is refused so `backend/scripts/...` (a different, fully
# shipped tree) does not match.
DOC_REF = re.compile(r"(?:^|[^A-Za-z0-9_/.-])(?:\./)?(scripts/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.(?:sh|py))")


def test_every_allowlisted_script_exists_in_the_repo():
    missing = sorted(p for p in oss_scripts() if not (REPO_ROOT / p).is_file())
    assert not missing, f"OSS_SCRIPTS lists files that do not exist: {missing}"


def test_every_compose_bind_mount_under_scripts_is_allowlisted():
    """Docker creates a directory at a missing bind-mount source; an entrypoint
    that resolves to a directory cannot start (v0.32.0 backend/celery)."""
    allowed = oss_scripts()
    referenced = {
        m.group(1)
        for f in shipped_compose_files()
        for m in BIND_MOUNT.finditer(f.read_text())
    }
    assert referenced, "expected at least one ./scripts/ bind mount in the compose files"
    assert referenced <= allowed, f"bind-mounted but not shipped: {sorted(referenced - allowed)}"


def test_every_script_the_shipped_docs_tell_operators_to_run_is_allowlisted():
    """A reference may instead resolve under backend/, the app container's
    working directory (`docker compose exec backend python scripts/x.py`);
    that tree ships whole, so only host-side references need the allowlist."""
    allowed = oss_scripts()
    referenced = {
        m.group(1) for f in shipped_docs() for m in DOC_REF.finditer(f.read_text())
    }
    assert {"scripts/install.sh", "scripts/backup.sh", "scripts/upgrade.sh"} <= referenced
    unshipped = sorted(
        r for r in referenced
        if r not in allowed and not (REPO_ROOT / "backend" / r).is_file()
    )
    assert not unshipped, f"documented but not shipped: {unshipped}"


def test_the_947_credential_layer_is_allowlisted():
    assert {
        "scripts/install.sh",
        "scripts/backup.sh",
        "scripts/docker/with-file-secrets.sh",
    } <= oss_scripts()


def test_the_publisher_and_release_tooling_are_not_allowlisted():
    for private in ("scripts/prepare-oss.sh", "scripts/draft-release-notes.py",
                    "scripts/release-anchor.sh", "scripts/db-init-entrypoint.sh"):
        assert private not in oss_scripts(), private
