# SPDX-License-Identifier: AGPL-3.0-only
"""Release tools are pinned by SHA-256, and every release image gets an SBOM.

Plan 7.1. ``scripts/release/tools.lock`` pins each tool the release uses
(name, version, SHA-256 of the download, https URL). ``fetch-tool.sh`` installs
a tool only when the download matches its pin, and never runs it.
``make-sbom.sh`` runs Syft and fails unless the result is a CycloneDX document
that lists at least one component. The image publisher runs both on every
build leg, against the pushed digest on a real run and the OCI archive on a dry
run, and uploads the SBOM as ``sbom-<image>``.

All of it runs here with a stand-in ``curl`` (serving local files) and a
stand-in ``syft``. Nothing is downloaded, and no real tool runs.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
RELEASE = REPO_ROOT / "scripts" / "release"
PUBLISHER = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"

# scripts/release is not on the OSS keep-list, and neither is the publisher.
pytestmark = pytest.mark.skipif(not RELEASE.is_dir(), reason="scripts/release is private-only")

CURL_STUB = r'''
import json, os, shutil, sys
args = sys.argv[1:]
out = args[args.index("-o") + 1]
url = args[-1]
served = json.load(open(os.environ["FAKE_DOWNLOADS"]))
with open(os.environ["FAKE_CURL_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if url not in served:
    sys.stderr.write("curl: (22) The requested URL returned error: 404\n"); sys.exit(22)
shutil.copyfile(served[url], out)
'''

SYFT_STUB = r'''
import json, os, sys
with open(os.environ["FAKE_SYFT_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\n")
spec = json.load(open(os.environ["FAKE_SYFT"]))
target = [a for a in sys.argv[1:] if a.startswith("cyclonedx-json=")][0].split("=", 1)[1]
if spec.get("write") is not None:
    open(target, "w").write(spec["write"])
if spec.get("exit"):
    sys.stderr.write("syft: could not read the image\n"); sys.exit(spec["exit"])
'''


def _tarball(path: Path, members: dict) -> str:
    """members: name -> bytes, or ("link", target)."""
    with tarfile.open(path, "w:gz") as tar:
        for name, body in members.items():
            info = tarfile.TarInfo(name)
            if isinstance(body, tuple):
                info.type, info.linkname = tarfile.SYMTYPE, body[1]
                tar.addfile(info)
            else:
                info.size, info.mode = len(body), 0o644
                tar.addfile(info, io.BytesIO(body))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Kit:
    """A copy of scripts/release with its own lock file, and stand-in tools."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.release = tmp_path / "release"
        shutil.copytree(RELEASE, self.release)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for name, body in (("curl", CURL_STUB), ("syft", SYFT_STUB)):
            stub = self.bin / name
            stub.write_text(f"#!{sys.executable}\n{textwrap.dedent(body)}")
            stub.chmod(0o755)
        self.downloads = {}
        self.syft = {"write": json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6",
                                          "components": [{"type": "library", "name": "openssl"}]})}
        self.binary = b"#!/bin/sh\necho stand-in syft\n"
        self.url = "https://github.com/anchore/syft/releases/download/v9.9.9/syft_9.9.9_linux_amd64.tar.gz"
        self.sha = self.serve(self.url, {"syft": self.binary, "README.md": b"readme"})
        self.lock(f"syft 9.9.9 {self.sha} {self.url}")

    def serve(self, url: str, members: dict) -> str:
        path = self.dir / f"dl{len(self.downloads)}.tar.gz"
        self.downloads[url] = str(path)
        return _tarball(path, members)

    def lock(self, *lines: str) -> "Kit":
        (self.release / "tools.lock").write_text("# test lock\n" + "\n".join(lines) + "\n")
        return self

    def env(self) -> dict:
        (self.dir / "downloads.json").write_text(json.dumps(self.downloads))
        (self.dir / "syft.json").write_text(json.dumps(self.syft))
        for log in ("curl.log", "syft.log"):
            (self.dir / log).touch()
        return {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.dir),
                "FAKE_DOWNLOADS": str(self.dir / "downloads.json"), "FAKE_CURL_LOG": str(self.dir / "curl.log"),
                "FAKE_SYFT": str(self.dir / "syft.json"), "FAKE_SYFT_LOG": str(self.dir / "syft.log")}

    def fetch(self, name: str = "syft"):
        dest = self.dir / "tools"
        proc = subprocess.run(["bash", str(self.release / "fetch-tool.sh"), name, str(dest)], env=self.env(),
                              capture_output=True, text=True, timeout=60)
        return proc, dest / name

    def sbom(self, source: str = "registry:ghcr.io/o/i@sha256:" + "a" * 64, out: Path | None = None):
        out = out or self.dir / "out.cdx.json"
        proc = subprocess.run(["bash", str(self.release / "make-sbom.sh"), source, str(out)], env=self.env(),
                              capture_output=True, text=True, timeout=60)
        return proc, out

    def calls(self, tool: str) -> list:
        return [json.loads(line) for line in (self.dir / f"{tool}.log").read_text().splitlines()]


@pytest.fixture
def kit(tmp_path):
    return Kit(tmp_path)


# -- the committed pins --------------------------------------------------------


def test_every_committed_pin_is_complete():
    lines = [line for line in (RELEASE / "tools.lock").read_text().splitlines() if line and not line.startswith("#")]
    names = [line.split()[0] for line in lines]
    assert "syft" in names and len(names) == len(set(names))
    for line in lines:
        name, version, sha, url = line.split()
        assert re.fullmatch(r"[0-9a-f]{64}", sha), name
        assert url.startswith("https://github.com/") and f"v{version}/" in url, name


# -- fetch-tool.sh ---------------------------------------------------------------


def test_a_download_that_matches_its_pin_is_installed(kit):
    proc, installed = kit.fetch()
    assert proc.returncode == 0, proc.stderr
    assert installed.read_bytes() == kit.binary
    assert installed.stat().st_mode & 0o777 == 0o755
    assert not (installed.parent / "README.md").exists(), "only the named binary is installed"
    [call] = kit.calls("curl")
    assert call[-1] == kit.url and call[:2] == ["--proto", "=https"]


def test_a_download_that_does_not_match_its_pin_is_not_installed(kit):
    kit.serve(kit.url, {"syft": b"#!/bin/sh\necho something else\n"})
    proc, installed = kit.fetch()
    assert proc.returncode != 0
    assert "Not installed" in proc.stderr and kit.sha in proc.stderr
    assert not installed.exists()


@pytest.mark.parametrize(
    "lines, message",
    [
        ([], "is not pinned"),
        (["syft 9.9.9 {sha} {url}", "syft 9.9.9 {sha} {url}"], "more than once"),
        (["syft 9.9.9 {sha} {url} extra"], "extra fields"),
        (["syft 9.9.9 {short} {url}"], "not 64 hex"),
        (["syft 9.9.9 {sha} http://github.com/anchore/syft/v9.9.9/syft.tar.gz"], "over https from github.com"),
        (["syft 9.9.9 {sha} https://example.com/v9.9.9/syft.tar.gz"], "over https from github.com"),
        (["syft 9.9.8 {sha} {url}"], "does not name version"),
    ],
    ids=["unpinned", "twice", "extra", "short-sha", "http", "other-host", "version-mismatch"],
)
def test_a_pin_that_is_not_exact_installs_nothing(kit, lines, message):
    kit.lock(*(line.format(sha=kit.sha, short=kit.sha[:63], url=kit.url) for line in lines))
    proc, installed = kit.fetch()
    assert proc.returncode != 0
    assert message in proc.stderr
    assert not installed.exists()
    assert kit.calls("curl") == [], "nothing is downloaded on a bad pin"


@pytest.mark.parametrize("members", [{"README.md": b"x"}, {"syft": ("link", "/bin/sh")}], ids=["no-binary", "symlink"])
def test_a_tarball_without_a_plain_binary_installs_nothing(kit, members):
    kit.lock(f"syft 9.9.9 {kit.serve(kit.url, members)} {kit.url}")
    proc, installed = kit.fetch()
    assert proc.returncode != 0
    assert not installed.exists() and not installed.is_symlink()


def test_a_failed_download_installs_nothing(kit):
    kit.downloads.clear()
    proc, installed = kit.fetch()
    assert proc.returncode != 0
    assert not installed.exists()


# -- make-sbom.sh ----------------------------------------------------------------


def test_a_cyclonedx_sbom_with_components_is_accepted(kit):
    proc, out = kit.sbom()
    assert proc.returncode == 0, proc.stderr
    assert "1 components" in proc.stdout
    [call] = kit.calls("syft")
    assert call[:2] == ["scan", "registry:ghcr.io/o/i@sha256:" + "a" * 64]
    assert f"cyclonedx-json={out}" in call


@pytest.mark.parametrize(
    "spec, message",
    [
        ({"write": json.dumps({"bomFormat": "CycloneDX", "components": []})}, "lists no components"),
        ({"write": json.dumps({"bomFormat": "CycloneDX"})}, "lists no components"),
        ({"write": json.dumps({"spdxVersion": "SPDX-2.3", "packages": [{}]})}, "not a CycloneDX document"),
        ({"write": "not json"}, "no readable SBOM"),
        ({"write": None}, "no readable SBOM"),
        ({"exit": 1}, ""),
        # A run that wrote a plausible document and then failed is not trusted.
        ({"exit": 1, "write": json.dumps({"bomFormat": "CycloneDX", "components": [{"name": "half"}]})}, ""),
    ],
    ids=["empty", "no-components", "spdx", "not-json", "nothing-written", "syft-failed", "syft-failed-after-writing"],
)
def test_an_sbom_that_inventories_nothing_fails(kit, spec, message):
    kit.syft = spec
    proc, _ = kit.sbom()
    assert proc.returncode != 0
    assert message in proc.stdout + proc.stderr


def test_a_stale_sbom_from_an_earlier_run_is_not_reused(kit):
    _, out = kit.sbom()
    kit.syft = {"write": None}
    proc, _ = kit.sbom(out=out)
    assert proc.returncode != 0
    assert not out.exists()


# -- the publisher ----------------------------------------------------------------


def _publisher_steps() -> list[dict]:
    return yaml.safe_load(PUBLISHER.read_text())["jobs"]["build"]["steps"]


def _named(name: str) -> dict:
    [step] = [s for s in _publisher_steps() if s.get("name") == name]
    return step


@pytest.mark.skipif(not PUBLISHER.exists(), reason="publish-oss-images.yml is private-only")
def test_every_leg_produces_and_uploads_its_sbom():
    names = [s.get("name") for s in _publisher_steps()]
    order = ["Build and push", "Record the pushed digest", "Check out the release tools", "Produce the SBOM", "Upload the SBOM"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    checkout = _named("Check out the release tools")["with"]
    assert checkout["sparse-checkout"] == "scripts/release" and checkout["persist-credentials"] is False
    assert checkout["path"] == "release-tools"
    upload = _named("Upload the SBOM")
    assert len(upload["uses"].split("@")[1].split()[0]) == 40
    assert upload["with"]["name"] == "sbom-${{ matrix.name }}"
    assert upload["with"]["path"] == "${{ runner.temp }}/sbom/${{ matrix.name }}.cdx.json"
    assert upload["with"]["if-no-files-found"] == "error"
    assert not [s for s in _publisher_steps() if s.get("continue-on-error")]


@pytest.mark.skipif(not PUBLISHER.exists(), reason="publish-oss-images.yml is private-only")
@pytest.mark.parametrize("push", ["true", "false"])
def test_the_sbom_reads_the_pushed_digest_or_the_dry_run_archive(kit, push):
    work = kit.dir / "workspace"
    tools = work / "release-tools" / "scripts" / "release"
    shutil.copytree(kit.release, tools)
    step = _named("Produce the SBOM")
    digest = "sha256:" + "c" * 64
    env = kit.env() | {
        "RUNNER_TEMP": str(kit.dir / "runner"), "PUSH": push, "DIGEST": digest, "IMAGE": "scf-backend",
        "IMAGE_REF": "ghcr.io/markac007/scf-backend", "ARCHIVE": "/runner/oci/scf-backend.tar",
    }
    # Only the stand-in syft may answer: the step puts RUNNER_TEMP/tools first
    # on PATH, so the fetched stand-in binary would shadow it. Serve the stand-in.
    kit.binary = (kit.bin / "syft").read_bytes()
    kit.lock(f"syft 9.9.9 {kit.serve(kit.url, {'syft': kit.binary})} {kit.url}")
    shutil.copy(kit.release / "tools.lock", tools / "tools.lock")
    env = env | {"FAKE_DOWNLOADS": kit.env()["FAKE_DOWNLOADS"]}
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    [call] = kit.calls("syft")
    want = f"registry:ghcr.io/markac007/scf-backend@{digest}" if push == "true" else "oci-archive:/runner/oci/scf-backend.tar"
    assert call[1] == want
    assert (kit.dir / "runner" / "sbom" / "scf-backend.cdx.json").exists()
