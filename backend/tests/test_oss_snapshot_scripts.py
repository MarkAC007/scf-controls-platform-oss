# SPDX-License-Identifier: AGPL-3.0-only
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
import subprocess
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


# --- Top-level allow-list -----------------------------------------------------
# Everything else in the publisher removes paths somebody listed as private. A
# new top-level file or directory that nobody listed would ship. The publisher
# therefore also asserts that every entry left in the snapshot root is named in
# scripts/oss-toplevel-allow.txt. The check is a shell function; the tests below
# run that function's own text, so they fail if its behaviour changes.

TOPLEVEL_ALLOW = REPO_ROOT / "scripts" / "oss-toplevel-allow.txt"


def toplevel_allow_list() -> list[str]:
    names = []
    for line in TOPLEVEL_ALLOW.read_text().splitlines():
        line = re.sub(r"(^|\s+)#.*$", "", line).strip()
        if line:
            names.append(line)
    return names


def _unlisted_function() -> str:
    match = re.search(r"^unlisted_toplevel_entries\(\) \{.*?^\}\n", PREPARE_OSS.read_text(), re.S | re.M)
    assert match, "unlisted_toplevel_entries() not found in prepare-oss.sh"
    return match.group(0)


def _unlisted(snapshot: Path, allow_file: Path) -> tuple[int, list[str]]:
    result = subprocess.run(
        ["bash", "-c", _unlisted_function() + '\nunlisted_toplevel_entries "$1" "$2"\n', "bash",
         str(snapshot), str(allow_file)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, result.stdout.splitlines()


def _snapshot_of_allowed_entries(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    for name in toplevel_allow_list():
        (snapshot / name).write_text("")
    return snapshot


def test_a_snapshot_of_only_allowlisted_entries_passes(tmp_path):
    code, unlisted = _unlisted(_snapshot_of_allowed_entries(tmp_path), TOPLEVEL_ALLOW)
    assert code == 0
    assert unlisted == []


def test_an_unknown_top_level_directory_fails_and_names_itself(tmp_path):
    snapshot = _snapshot_of_allowed_entries(tmp_path)
    (snapshot / "internal-notes").mkdir()
    (snapshot / "internal-notes" / "plan.md").write_text("private")
    code, unlisted = _unlisted(snapshot, TOPLEVEL_ALLOW)
    assert code == 0
    assert unlisted == ["internal-notes"]


def test_unknown_files_and_dotfiles_are_named_too(tmp_path):
    snapshot = _snapshot_of_allowed_entries(tmp_path)
    (snapshot / ".internal-config").write_text("")
    (snapshot / "NOTES.md").write_text("")
    (snapshot / "backend-old").mkdir()  # a prefix of a listed name is not a match
    _, unlisted = _unlisted(snapshot, TOPLEVEL_ALLOW)
    assert unlisted == [".internal-config", "NOTES.md", "backend-old"]


def test_only_the_snapshot_root_is_checked(tmp_path):
    snapshot = tmp_path / "snapshot"
    (snapshot / "backend" / "anything" / "deeper").mkdir(parents=True)
    _, unlisted = _unlisted(snapshot, TOPLEVEL_ALLOW)
    assert unlisted == []


@pytest.mark.parametrize("content", [None, "", "# only a comment\n\n"])
def test_a_missing_or_empty_allow_list_is_an_error_not_a_pass(tmp_path, content):
    snapshot = _snapshot_of_allowed_entries(tmp_path)
    allow_file = tmp_path / "allow.txt"
    if content is not None:
        allow_file.write_text(content)
    code, _ = _unlisted(snapshot, allow_file)
    assert code != 0


def test_the_publisher_fails_the_snapshot_on_an_unlisted_entry():
    """The function only prints; the safety assertions must turn each name and
    an unreadable list into a failed snapshot, before the final abort check."""
    text = PREPARE_OSS.read_text()
    tail = text[text.index('echo "==> Safety assertions'):]
    block = re.search(
        r'^if unlisted="\$\(unlisted_toplevel_entries \. "\$TOPLEVEL_ALLOW_FILE"\)"; then\n(.*?)^else\n(.*?)^fi\n',
        tail, re.S | re.M,
    )
    assert block, "the safety assertions do not call unlisted_toplevel_entries on the snapshot root"
    assert "fail=1" in block.group(1) and "$entry" in block.group(1)
    assert "fail=1" in block.group(2)
    assert tail.index(block.group(0)) < tail.index('if [ "$fail" -ne 0 ]; then')
    assert 'TOPLEVEL_ALLOW_FILE="$ROOT/scripts/oss-toplevel-allow.txt"' in text


def test_the_allow_list_names_nothing_the_publisher_forbids():
    """Listing a private path here must not be a way to quiet the check."""
    text = PREPARE_OSS.read_text()
    tail = text[text.index('echo "==> Safety assertions'):]
    loop = re.search(r"^for p in (.*?); do$", tail, re.S | re.M)
    assert loop, "forbidden-path loop not found in the safety assertions"
    forbidden = {p for p in loop.group(1).replace("\\\n", " ").split() if "/" not in p}
    assert {"docs", "catalog-source", "CLAUDE.md", "openwiki"} <= forbidden
    forbidden |= {"RELEASE_META.yml", "README.oss.md", "planning", "feedback_screenshots", "Dockerfile.db-init"}
    allowed = toplevel_allow_list()
    assert not forbidden & set(allowed), sorted(forbidden & set(allowed))
    assert not [n for n in allowed if n.startswith("PENTEST_REPORT_")]
    assert len(allowed) == len(set(allowed)), "duplicate entry in oss-toplevel-allow.txt"
    assert not [n for n in allowed if "/" in n or "*" in n], "entries are exact top-level names"


DEVELOPMENT_LEFTOVERS = {
    ".superpowers", "assets", ".trigger", ".repodocs.yaml",
    "docker-compose.dev-demo.yml", "test_control_updates.sh", "test_evidence_updates.sh",
}


def test_the_development_leftovers_the_check_found_are_stripped_and_forbidden():
    """They were public through v1.3.1 only because nothing listed them."""
    text = PREPARE_OSS.read_text()
    head, tail = text.split('echo "==> Safety assertions', 1)
    removed = set()
    for line in head.splitlines():
        match = re.match(r"\s*rm -r?f\s+(.*)", line)
        if match:
            removed |= set(match.group(1).split("#", 1)[0].split())
    assert DEVELOPMENT_LEFTOVERS <= removed, sorted(DEVELOPMENT_LEFTOVERS - removed)
    loop = re.search(r"^for p in (.*?); do$", tail, re.S | re.M)
    forbidden = set(loop.group(1).replace("\\\n", " ").split())
    assert DEVELOPMENT_LEFTOVERS <= forbidden, sorted(DEVELOPMENT_LEFTOVERS - forbidden)
    assert not DEVELOPMENT_LEFTOVERS & set(toplevel_allow_list())


def test_no_shipped_document_names_a_stripped_development_file():
    for doc in shipped_docs():
        for name in ("docker-compose.dev-demo.yml", "test_control_updates.sh", "test_evidence_updates.sh"):
            assert name not in doc.read_text(), f"{doc.name} names {name}, which the snapshot does not ship"


def test_the_allow_list_itself_does_not_ship():
    assert "scripts/oss-toplevel-allow.txt" not in oss_scripts()
