"""Phase 7: the backup script's two storage decisions, executed.

ISA 20260912-0930, criteria 55 and 56, plus the two D46 carry-forwards.

These run the SHELL, not a description of it. `scripts/backup.sh` and
`scripts/upgrade.sh` both end in `main "$@"`, so they cannot be sourced; each
test extracts the function under test plus the helpers it calls and runs that
in a scratch directory with a scratch HOME. That is the only way to cover the
case Phase 4's verifier found the hard way — `derive_volume_name` has a
compose-file fallback, so a decision that LOOKS like it aborts actually returns
a fixed volume name and tars an empty directory that passes every integrity
check. Reading the source would have agreed with the wrong answer.

Four things are pinned here.

**The bundled signal is exercised across the shapes an install can be in**,
not just the two the happy path produces: value in .env, value in a secrets
file, empty in either, absent from both, and the D46 R2 case where neither
names a directory but the installer's default one exists.

**The two scripts' copies are compared byte for byte.** They are duplicated on
purpose (neither can source the other), and a divergence means backup.sh and
upgrade.sh disagree about whether an install has an object store — which is
exactly the failure that produces a green backup holding nothing.

**The external-store warning is proved to be unconditional.** It is emitted on
a bundled install too: a tarred volume does not cover an organisation that
brought its own store, and "we backed up the evidence volume" is precisely the
sentence that would stop someone looking.

**The empty-key case is executed, not read.** A `.env` with a bare
`EVIDENCE_STORAGE_BOOTSTRAP=` used to gain a second line; the assertion counts
the lines in the file afterwards.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BACKUP_SH = REPO / "scripts" / "backup.sh"
UPGRADE_SH = REPO / "scripts" / "upgrade.sh"

pytestmark = pytest.mark.skipif(
    not BACKUP_SH.exists(),
    reason="scripts/ is not present in this checkout (backend container mount)",
)


def _extract(script: Path, name: str) -> str:
    """One top-level shell function, from `name() {` to the closing brace."""
    text = script.read_text()
    match = re.search(rf"^{re.escape(name)}\(\) \{{.*?^\}}$", text, re.S | re.M)
    assert match, f"{name} not found in {script.name}"
    return match.group(0)


_STUBS = """
set -uo pipefail
info() { printf 'INFO %s\\n' "$*"; }
warn() { printf 'WARN %s\\n' "$*"; }
success() { printf 'OK %s\\n' "$*"; }
die() { printf 'DIE %s\\n' "$*"; exit 1; }
"""


def _run(body: str, cwd: Path, home: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    environ = dict(os.environ)
    environ["HOME"] = str(home)
    environ.pop("SCF_SECRETS_DIR", None)
    if env:
        environ.update(env)
    return subprocess.run(
        ["bash", "-c", body],
        cwd=str(cwd),
        env=environ,
        capture_output=True,
        text=True,
    )


def _signal_harness() -> str:
    return "\n".join(
        [
            _STUBS,
            _extract(BACKUP_SH, "env_file_value"),
            _extract(BACKUP_SH, "resolve_secrets_dir"),
            _extract(BACKUP_SH, "bundled_object_store"),
            'if bundled_object_store; then echo BUNDLED; else echo NONE; fi',
        ]
    )


# ---------------------------------------------------------------------------
# ISC 55 — a --no-minio install skips the volume tar, and only that install
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "env_lines,secret_files,expected",
    [
        (["MINIO_ROOT_USER=scfadmin"], {}, "BUNDLED"),
        (["MINIO_ROOT_USER="], {}, "NONE"),
        ([], {}, "NONE"),
        (["SCF_SECRETS_DIR={secrets}"], {"MINIO_ROOT_USER": "scfadmin"}, "BUNDLED"),
        (["SCF_SECRETS_DIR={secrets}"], {"MINIO_ROOT_USER": ""}, "NONE"),
        (["SCF_SECRETS_DIR={secrets}"], {}, "NONE"),
        # A .env value wins over an empty file — last-wins, matching compose.
        (
            ["MINIO_ROOT_USER=scfadmin", "SCF_SECRETS_DIR={secrets}"],
            {"MINIO_ROOT_USER": ""},
            "BUNDLED",
        ),
    ],
    ids=[
        "env-value",
        "env-empty",
        "env-absent",
        "secrets-file",
        "secrets-file-empty",
        "secrets-file-missing",
        "env-wins-over-empty-file",
    ],
)
def test_the_bundled_signal_across_every_install_shape(
    tmp_path, env_lines, secret_files, expected
):
    home = tmp_path / "home"
    secrets = tmp_path / "secrets"
    home.mkdir()
    secrets.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text(
        "\n".join(line.format(secrets=secrets) for line in env_lines) + "\n"
    )
    for name, value in secret_files.items():
        (secrets / name).write_text(value)

    result = _run(_signal_harness(), work, home)
    assert result.stdout.strip().splitlines()[-1] == expected, result.stderr


def test_the_no_minio_install_skips_the_tar_rather_than_tarring_an_empty_volume():
    """The decision is taken BEFORE the volume name is derived (ISC 55).

    Deriving first and skipping later is the trap: `derive_volume_name` falls
    back to the compose file's fixed `name:` key, `docker run -v` creates that
    volume when it is absent or attaches ANOTHER install's, and the result is
    an 86-byte archive that passes both integrity checks.
    """
    text = BACKUP_SH.read_text()
    decide = text.index("bundled_object_store && bundled_store=1")
    derive = text.index('minio_vol="$(derive_volume_name')
    assert decide < derive
    # And the derive is inside the bundled branch, not before it.
    between = text[decide:derive]
    assert "if (( bundled_store == 1 )); then" in between


# ---------------------------------------------------------------------------
# D46 R2 — the installer's default directory is found
# ---------------------------------------------------------------------------

def test_an_install_that_names_no_secrets_dir_still_finds_the_installers_own(tmp_path):
    """D46 R2, executed.

    `scripts/install.sh:27` defaults to `$HOME/.scf/secrets`. An operator who
    tidied SCF_SECRETS_DIR out of .env, or who runs the backup from a shell
    without it exported, left this answering NONE on an install that bundles
    an object store — and a silently skipped evidence backup on exactly the
    install with the most to lose.
    """
    home = tmp_path / "home"
    default = home / ".scf" / "secrets"
    default.mkdir(parents=True)
    (default / "MINIO_ROOT_USER").write_text("scfadmin")
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("POSTGRES_DB=cg_scf\n")

    result = _run(_signal_harness(), work, home)
    assert result.stdout.strip().splitlines()[-1] == "BUNDLED", result.stderr


def test_the_fallback_does_not_fire_on_an_empty_default_directory(tmp_path):
    """The mutation that would make the fix wrong: returning the path whether
    or not it holds anything would make every legacy .env install claim a
    secrets directory it does not have, and the credential tarball would be
    built from an empty folder."""
    home = tmp_path / "home"
    (home / ".scf" / "secrets").mkdir(parents=True)
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text("POSTGRES_DB=cg_scf\n")

    body = "\n".join(
        [
            _STUBS,
            _extract(BACKUP_SH, "env_file_value"),
            _extract(BACKUP_SH, "resolve_secrets_dir"),
            'printf "[%s]\\n" "$(resolve_secrets_dir)"',
        ]
    )
    result = _run(body, work, home)
    assert result.stdout.strip().splitlines()[-1] == "[]", result.stdout


# ---------------------------------------------------------------------------
# The two copies must not drift
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["env_file_value", "resolve_secrets_dir", "bundled_object_store"])
def test_the_two_scripts_agree_byte_for_byte(name):
    """Neither script can source the other, so these are duplicated on purpose.
    A divergence means backup.sh and upgrade.sh disagree about whether an
    install has an object store."""
    assert _extract(BACKUP_SH, name) == _extract(UPGRADE_SH, name)


# ---------------------------------------------------------------------------
# ISC 56 — organisations on their own store are outside this backup
# ---------------------------------------------------------------------------

def _count_harness(psql_stdout: str, psql_rc: int = 0) -> str:
    return "\n".join(
        [
            _STUBS,
            f'compose() {{ printf "%b" "{psql_stdout}"; return {psql_rc}; }}',
            "derive_pg() { printf cg; }",
            _extract(BACKUP_SH, "external_store_orgs"),
            'printf "[%s]\\n" "$(external_store_orgs)"',
        ]
    )


@pytest.mark.parametrize(
    "stdout,rc,expected",
    [
        ("3\\n", 0, "[3]"),
        ("0\\n", 0, "[0]"),
        ("", 1, "[unknown]"),
        ('ERROR:  relation \\"evidence_storage_configs\\" does not exist', 1, "[unknown]"),
    ],
    ids=["three", "none", "psql-failed", "table-absent"],
)
def test_the_external_store_count_degrades_to_unknown(tmp_path, stdout, rc, expected):
    """A count that cannot be trusted must not be printed as a number. The
    table does not exist on a pre-Phase-1 install and postgres may not be up,
    and both of those must still produce the warning — just without a figure."""
    work = tmp_path / "work"
    work.mkdir()
    result = _run(_count_harness(stdout, rc), work, tmp_path)
    assert result.stdout.strip().splitlines()[-1] == expected, result.stdout


def test_the_external_store_warning_fires_on_a_bundled_install_too():
    """ISC 56's whole point.

    A bundled install is not covered just because its volume was tarred: an
    organisation that brought its own store keeps its evidence somewhere this
    script has never heard of. Emitting the warning only in the no-MinIO branch
    would leave the most dangerous case — a mixed estate — silent.
    """
    text = BACKUP_SH.read_text()
    warning = text.index("keep their evidence in a store of their OWN")
    # Not inside the `else` of the bundled-store branch: the call that feeds it
    # sits after that whole if/else block closes.
    call = text.index('external_orgs="$(external_store_orgs)"')
    skip_branch = text.index("SKIPPING the evidence backup")
    tar_branch = text.index("Backing up MinIO evidence volume")
    assert tar_branch < skip_branch < call < warning


def test_the_warning_says_what_the_database_dump_does_and_does_not_hold():
    """The dangerous misreading is "the backup ran, so the evidence is safe".
    The rows describing those files ARE captured; the files are not."""
    text = BACKUP_SH.read_text()
    block = text[text.index('external_orgs="$(external_store_orgs)"') :][:2500]
    assert "does not capture the files" in block
    assert "Settings, Evidence storage" in block
    # And the zero case says so rather than staying silent, so an operator can
    # tell "nothing outside" from "we did not look".
    assert "No organisation is on an evidence store of its own" in block


# ---------------------------------------------------------------------------
# D46 R1 — a present-but-empty key is filled, not duplicated
# ---------------------------------------------------------------------------

def _bootstrap_harness() -> str:
    return "\n".join(
        [
            _STUBS,
            _extract(UPGRADE_SH, "env_file_value"),
            _extract(UPGRADE_SH, "resolve_secrets_dir"),
            _extract(UPGRADE_SH, "bundled_object_store"),
            _extract(UPGRADE_SH, "ensure_storage_profile"),
            "ensure_storage_profile",
        ]
    )


@pytest.mark.parametrize(
    "initial,expected_value",
    [
        ("EVIDENCE_STORAGE_BOOTSTRAP=", "bundled_minio"),
        ("EVIDENCE_STORAGE_BOOTSTRAP=none", "none"),
        ("", "bundled_minio"),
    ],
    ids=["empty-key-filled", "existing-value-kept", "key-absent-appended"],
)
def test_the_bootstrap_key_is_never_duplicated(tmp_path, initial, expected_value):
    """D46 R1, executed rather than read.

    The old test was on the value, so a bare `EVIDENCE_STORAGE_BOOTSTRAP=`
    line — what a .env.example copied verbatim gives you — gained a second
    definition. Compose takes the last one so the behaviour was right, but a
    .env with two definitions of one key is a thing an operator has to reason
    about at 3am.
    """
    work = tmp_path / "work"
    work.mkdir()
    lines = ["MINIO_ROOT_USER=scfadmin", "COMPOSE_PROFILES=storage"]
    if initial:
        lines.append(initial)
    (work / ".env").write_text("\n".join(lines) + "\n")

    result = _run(_bootstrap_harness(), work, tmp_path)
    assert result.returncode == 0, result.stderr

    env_text = (work / ".env").read_text()
    definitions = [
        line for line in env_text.splitlines()
        if line.startswith("EVIDENCE_STORAGE_BOOTSTRAP=")
    ]
    assert len(definitions) == 1, env_text
    assert definitions[0] == f"EVIDENCE_STORAGE_BOOTSTRAP={expected_value}", env_text
    # No editor backup left behind.
    assert not (work / ".env.upgrade-bak").exists()
