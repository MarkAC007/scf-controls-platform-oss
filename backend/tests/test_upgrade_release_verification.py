"""scripts/upgrade.sh checks a release's attestations before it upgrades.

The public release workflow signs a build-provenance attestation for the two
files a release carries, ``upgrade-manifest.json`` and ``release-images.json``.
In Phase 0 ``upgrade.sh`` now:

* checks both with ``gh attestation verify --repo <public> --signer-workflow
  <public>/.github/workflows/oss-release.yml`` when ``gh`` is installed and
  signed in;
* validates ``release-images.json`` as a record for the target version, and
  prints the image references by digest (Helm users set ``digest:`` from them);
* reports ``verified``, ``failed`` or ``unchecked``. ``failed`` (a check ran
  and did not pass, or the record is not this release's) always stops the
  upgrade. ``unchecked`` is a warning by default and fatal with
  ``--require-verified``.

The real functions run here: the script is sourced with its ``main "$@"`` line
neutralised (the technique ``scripts/tests/test_upgrade_reexec.sh`` uses), and
``gh`` is a stand-in that treats a file as attested only when its sha256 is on
an allow-list. PATH is built from symlinks, so the runner's real ``gh`` in
/usr/bin cannot leak in.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
SCRIPT = REPO_ROOT / "scripts" / "upgrade.sh"
PUBLIC = "MarkAC007/scf-controls-platform-oss"
SIGNER = f"{PUBLIC}/.github/workflows/oss-release.yml"
VERSION = "9.8.7"
BE = "sha256:" + "a" * 64
FE = "sha256:" + "b" * 64

GH_STUB = r'''
import hashlib, json, os, sys
cfg = json.load(open(os.environ["FAKE_GH"]))
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    sys.exit(0 if cfg["authed"] else 1)
if args[:2] == ["attestation", "verify"]:
    path = args[2]
    opts = dict(zip(args[3::2], args[4::2]))
    digest = hashlib.sha256(open(path, "rb").read()).hexdigest()
    if opts.get("--repo") == cfg["repo"] and opts.get("--signer-workflow") == cfg["signer"] and digest in cfg["trusted"]:
        print("Loaded 1 attestation from GitHub API\n✓ Verification succeeded!")
        sys.exit(0)
    sys.stderr.write("Error: verifying with issuer \"sigstore.dev\"\nverification failed\n")
    sys.exit(1)
sys.stderr.write(f"stand-in gh: unexpected {args}\n"); sys.exit(97)
'''

TOOLS = ("python3", "cat", "tail", "tr", "dirname", "mktemp", "rm", "sed", "grep", "printf", "env", "head")


def _record(**over) -> dict:
    doc = {
        "schema": 1, "version": VERSION, "source": {"snapshot_sha256": "c" * 64},
        "images": [{"name": "ghcr.io/markac007/scf-backend", "digest": BE},
                   {"name": "ghcr.io/markac007/scf-frontend", "digest": FE}],
    }
    doc.update(over)
    return doc


class Host:
    """An operator's host: the release files, and what gh can do there."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.lib = tmp_path / "upgrade_lib.sh"
        text = SCRIPT.read_text()
        assert text.count('\nmain "$@"\n') == 1
        self.lib.write_text(text.replace('\nmain "$@"\n', "\n: # dispatch neutralised by the test\n"))
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for tool in TOOLS:
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)
        self.manifest = tmp_path / "upgrade-manifest.json"
        self.manifest.write_text(json.dumps({"version": VERSION, "min_upgradable_version": "1.0.0"}))
        self.images = tmp_path / "release-images.json"
        self.images.write_text(json.dumps(_record(), indent=2))
        self.gh = {"authed": True, "repo": PUBLIC, "signer": SIGNER, "trusted": []}
        self.log = tmp_path / "gh.log"
        self.with_gh(True)

    def with_gh(self, installed: bool) -> "Host":
        stub = self.bin / "gh"
        if installed and not stub.exists():
            stub.write_text(f"#!{sys.executable}\n{textwrap.dedent(GH_STUB)}")
            stub.chmod(0o755)
        if not installed and stub.exists():
            stub.unlink()
        return self

    def trust(self, *files: Path) -> "Host":
        self.gh["trusted"] += [hashlib.sha256(f.read_bytes()).hexdigest() for f in files]
        return self

    def run(self, images: Path | None = None, require: bool = False, use_images: bool = True):
        cfg = self.dir / "gh.json"
        cfg.write_text(json.dumps(self.gh))
        self.log.write_text("")
        images_arg = str(images or self.images) if use_images else ""
        body = (
            f'source "{self.lib}"\n'
            f'TARGET={VERSION}; TAG=v{VERSION}; REQUIRE_VERIFIED={1 if require else 0}\n'
            f'verify_release_files "{self.manifest}" "{images_arg}"\n'
            'echo "RESULT=${RELEASE_VERIFIED}"\n'
            "report_release_verification\n"
            'echo "CONTINUED"\n'
        )
        env = {"PATH": str(self.bin), "HOME": str(self.dir), "FAKE_GH": str(cfg), "FAKE_GH_LOG": str(self.log),
               "NO_COLOR": "1", "TERM": "dumb"}
        proc = subprocess.run([shutil.which("bash"), "--noprofile", "--norc", "-c", body], cwd=self.dir, env=env,
                              capture_output=True, text=True, timeout=60)
        return Result(proc, [json.loads(line) for line in self.log.read_text().splitlines()])


class Result:
    def __init__(self, proc, gh_calls):
        self.code = proc.returncode
        self.text = proc.stdout + proc.stderr
        self.gh_calls = gh_calls
        self.result = next((line.split("=", 1)[1] for line in proc.stdout.splitlines() if line.startswith("RESULT=")), None)
        self.continued = "CONTINUED" in proc.stdout

    @property
    def verify_calls(self):
        return [c for c in self.gh_calls if c[:2] == ["attestation", "verify"]]


pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="scripts/upgrade.sh is not in this tree")


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def _refs_printed(run) -> bool:
    return f"ghcr.io/markac007/scf-backend@{BE}" in run.text and f"ghcr.io/markac007/scf-frontend@{FE}" in run.text


# -- verified ------------------------------------------------------------------


@pytest.mark.parametrize("require", [False, True])
def test_a_release_whose_files_are_both_attested_is_verified(host, require):
    host.trust(host.manifest, host.images)
    run = host.run(require=require)
    assert run.code == 0 and run.continued, run.text
    assert run.result == "verified"
    assert "Release verified" in run.text
    assert _refs_printed(run)
    assert [c[2] for c in run.verify_calls] == [str(host.manifest), str(host.images)]
    for call in run.verify_calls:
        assert call[3:] == ["--repo", PUBLIC, "--signer-workflow", SIGNER]


# -- failed ----------------------------------------------------------------------


@pytest.mark.parametrize("require", [False, True])
@pytest.mark.parametrize("attested", ["images-only", "manifest-only", "neither"])
def test_a_file_that_is_not_attested_stops_the_upgrade(host, attested, require):
    if attested == "images-only":
        host.trust(host.images)
    elif attested == "manifest-only":
        host.trust(host.manifest)
    run = host.run(require=require)
    assert run.result == "failed"
    assert "FAILED verification" in run.text and "did not pass 'gh attestation verify'" in run.text
    # A failed check is fatal whether or not --require-verified was given.
    assert run.code != 0 and not run.continued
    assert "Nothing was changed" in run.text


@pytest.mark.parametrize("require", [False, True])
def test_a_tampered_manifest_stops_the_upgrade(host, require):
    host.trust(host.manifest, host.images)
    host.manifest.write_text(host.manifest.read_text().replace("1.0.0", "0.0.0"))
    run = host.run(require=require)
    assert run.code != 0 and not run.continued
    assert run.result == "failed"
    assert "upgrade-manifest.json did not pass" in run.text


def test_attestations_from_another_workflow_are_not_accepted(host):
    host.trust(host.manifest, host.images)
    host.gh["signer"] = f"{PUBLIC}/.github/workflows/ci.yml"
    run = host.run()
    assert run.code != 0 and run.result == "failed" and not run.continued


@pytest.mark.parametrize(
    "record",
    [
        "not json",
        json.dumps(_record(schema=2)),
        json.dumps(_record(version="9.8.6")),
        json.dumps(_record(images={"ghcr.io/markac007/scf-backend": BE})),
        json.dumps(_record(images=[{"name": "ghcr.io/markac007/scf-backend", "digest": BE}])),
        json.dumps(_record(images=_record()["images"] + [{"name": "ghcr.io/markac007/scf-backend", "digest": FE}])),
        json.dumps(_record(images=[{"name": "ghcr.io/markac007/scf-backend", "digest": BE + "\n"},
                                   {"name": "ghcr.io/markac007/scf-frontend", "digest": FE}])),
        json.dumps(_record(images=[{"name": "ghcr.io/markac007/scf-backend", "digest": "latest"},
                                   {"name": "ghcr.io/markac007/scf-frontend", "digest": FE}])),
        json.dumps(["not", "a", "record"]),
    ],
    ids=["not-json", "schema", "version", "images-keyed", "one-image", "duplicate", "newline", "tag", "list"],
)
def test_a_record_that_is_not_this_releases_fails_before_gh_is_asked(host, record):
    host.images.write_text(record)
    host.trust(host.manifest, host.images)  # even an attested bad record is not used
    run = host.run()
    assert run.result == "failed", run.text
    assert run.code != 0 and not run.continued
    assert run.verify_calls == []
    assert not _refs_printed(run)


# -- unchecked -------------------------------------------------------------------


@pytest.mark.parametrize("why", ["no-record", "no-gh", "gh-signed-out"])
def test_a_check_that_cannot_run_is_reported_not_passed(host, why):
    host.trust(host.manifest, host.images)
    if why == "no-gh":
        host.with_gh(False)
    if why == "gh-signed-out":
        host.gh["authed"] = False
    run = host.run(use_images=why != "no-record")
    assert run.result == "unchecked"
    assert "NOT verified" in run.text and "--require-verified" in run.text
    assert run.code == 0 and run.continued
    assert run.verify_calls == []
    # The digests still come from a valid record, verified or not.
    assert _refs_printed(run) == (why != "no-record")

    strict = host.run(require=True, use_images=why != "no-record")
    assert strict.code != 0 and not strict.continued
    assert "could not be verified" in strict.text


def test_a_record_path_that_does_not_exist_is_unchecked(host):
    run = host.run(images=host.dir / "elsewhere" / "release-images.json")
    assert run.result == "unchecked"
    assert "no release-images.json" in run.text


# -- wiring ----------------------------------------------------------------------


def test_phase_0_verifies_after_the_manifest_is_validated_and_before_the_tag_check():
    text = SCRIPT.read_text()
    body = text[text.index("\ndo_upgrade() {"):]
    order = [
        body.index('m_version="$(manifest_field version)"'),
        body.index('images_file="$(dirname "$MANIFEST_OPT")/release-images.json"'),
        body.index('/releases/download/${TAG}/release-images.json'),
        body.index('verify_release_files "$MANIFEST_FILE" "$images_file"'),
        body.index("report_release_verification"),
        body.index('step "Phase 0 — Verify tag ${TAG} exists"'),
    ]
    assert order == sorted(order)


def _cli(tmp_path, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=tmp_path, capture_output=True, text=True, timeout=60,
                          env={"PATH": os.environ["PATH"], "HOME": str(tmp_path), "NO_COLOR": "1"})


def test_the_flag_is_accepted_and_documented(tmp_path):
    # Options are parsed before any prerequisite is checked, so an unknown one
    # after the flag proves the flag itself was accepted.
    run = _cli(tmp_path, "--require-verified", "--no-such-flag")
    assert run.returncode != 0
    assert "unknown option: --no-such-flag" in run.stderr
    assert "--require-verified" not in run.stderr
    help_lines = _cli(tmp_path, "--help").stdout.splitlines()
    usage = [line for line in help_lines if "scripts/upgrade.sh vX.Y.Z" in line]
    assert usage and "[--require-verified]" in usage[0]
    # The option has its own entry in the option list, not only the usage line.
    assert [line.strip() for line in help_lines].count("--require-verified") == 1
    assert "gh attestation verify" in "\n".join(help_lines)
