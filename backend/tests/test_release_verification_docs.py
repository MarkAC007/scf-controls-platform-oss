"""The release-verification instructions describe what a release really carries.

Before this change the public README promised a source tarball with a
build-provenance attestation. No release has ever had one. It also showed an
OpenSSF Scorecard badge, but no Scorecard workflow has run. Meanwhile the image
publisher ran an attestation step under ``continue-on-error`` that cannot
succeed from a user-owned private repository.

What is true now, and pinned here:

* the publisher has no attestation step and asks for no attestation
  permissions;
* the README tells operators to verify exactly the files ``oss-release.yml``
  attests, signed by that workflow, and its ``jq`` line prints the image
  references from a record shaped like the one the release writes.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PUBLISHER = WORKFLOWS / "publish-oss-images.yml"
ORCHESTRATOR = WORKFLOWS / "release-oss.yml"
OSS_RELEASE = WORKFLOWS / "oss-release.yml"
PUBLIC_REPO = "MarkAC007/scf-controls-platform-oss"


def _public_readme() -> Path:
    # scripts/prepare-oss.sh renames README.oss.md to README.md in the snapshot.
    oss = REPO_ROOT / "README.oss.md"
    return oss if oss.exists() else REPO_ROOT / "README.md"


README = _public_readme().read_text()


def _attested_assets() -> list[str]:
    steps = yaml.safe_load(OSS_RELEASE.read_text())["jobs"]["release"]["steps"]
    attest = [s for s in steps if "actions/attest-build-provenance" in s.get("uses", "")]
    assert len(attest) == 1
    return attest[0]["with"]["subject-path"].split()


def _bash_blocks() -> list[str]:
    section = README[README.index("### Verifying a release"):README.index("## Licensing")]
    return re.findall(r"```bash\n(.*?)```", section, re.S)


@pytest.mark.skipif(not PUBLISHER.exists(), reason="publish-oss-images.yml is private-only")
def test_the_publisher_no_longer_pretends_to_attest():
    doc = yaml.safe_load(PUBLISHER.read_text())
    build = doc["jobs"]["build"]
    assert not [s for s in build["steps"] if "attest" in s.get("uses", "")]
    assert not [s for s in build["steps"] if s.get("continue-on-error")], "a swallowed failure is back"
    assert {"id-token", "attestations"}.isdisjoint(build["permissions"])
    assert "gh attestation verify oci://" not in PUBLISHER.read_text()


@pytest.mark.skipif(not ORCHESTRATOR.exists(), reason="release-oss.yml is private-only")
def test_the_image_build_is_not_granted_attestation_rights():
    images = yaml.safe_load(ORCHESTRATOR.read_text())["jobs"]["images"]
    assert {"id-token", "attestations"}.isdisjoint(images["permissions"])


def test_the_readme_promises_nothing_that_does_not_exist():
    assert "source.tar.gz" not in README
    assert "Scorecard" not in README and "securityscorecards" not in README


def test_the_readme_verifies_what_the_release_attests():
    assets = [a for a in _attested_assets() if not a.startswith("release-evidence/")]
    assert sorted(assets) == ["release-images.json", "upgrade-manifest.json"]
    download, _, _ = _bash_blocks()
    assert re.findall(r"--pattern (\S+)", download) == assets
    loop = re.search(r"for f in ([^;]+); do", download)
    assert loop and loop.group(1).split() == assets
    assert f"gh attestation verify \"$f\" --repo {PUBLIC_REPO}" in download
    signer = re.search(r"--signer-workflow (\S+)", download).group(1)
    assert signer == f"{PUBLIC_REPO}/.github/workflows/oss-release.yml"
    assert (REPO_ROOT / signer.split("/", 2)[2]).is_file()


def test_the_readme_prints_the_image_references_from_the_record(tmp_path):
    assert shutil.which("jq")
    _, pull, _ = _bash_blocks()
    jq_line = next(line for line in pull.splitlines() if line.startswith("jq "))
    be, fe = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    (tmp_path / "release-images.json").write_text(json.dumps({
        "schema": 1, "version": "9.8.7", "source": {"snapshot_sha256": "c" * 64},
        "images": [{"name": "ghcr.io/markac007/scf-backend", "digest": be},
                   {"name": "ghcr.io/markac007/scf-frontend", "digest": fe}],
    }))
    out = subprocess.run(["bash", "-c", jq_line], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == [f"ghcr.io/markac007/scf-backend@{be}", f"ghcr.io/markac007/scf-frontend@{fe}"]
    assert "docker pull ghcr.io/markac007/scf-backend@sha256:" in pull


# Plan 7.5: the evidence files the release workflow writes are the files the README verifies.
EVIDENCE_PATTERNS = ["sbom-*.cdx.json", "image-scan-gate.json", "vex-*.json"]


def test_the_readme_verifies_the_release_evidence():
    assert "release-evidence/*" in _attested_assets()
    _, _, evidence = _bash_blocks()
    assert [p.strip("'") for p in re.findall(r"--pattern (\S+)", evidence)] == EVIDENCE_PATTERNS
    loop = re.search(r"for f in ([^;]+); do", evidence)
    assert loop and loop.group(1).split() == EVIDENCE_PATTERNS
    assert '[ -e "$f" ] || continue' in evidence, "a release with no VEX documents must still verify"
    assert f"gh attestation verify \"$f\" --repo {PUBLIC_REPO}" in evidence
    assert f"--signer-workflow {PUBLIC_REPO}/.github/workflows/oss-release.yml" in evidence


@pytest.mark.skipif(not ORCHESTRATOR.exists(), reason="release-oss.yml is private-only")
def test_every_evidence_name_the_release_writes_matches_a_readme_pattern():
    import fnmatch
    steps = yaml.safe_load(ORCHESTRATOR.read_text())["jobs"]["scan"]["steps"]
    [step] = [s for s in steps if s.get("name") == "Assemble the release evidence"]
    names = re.findall(r"\$\{out\}/([\w.${}-]+)", step["run"])
    names = ["vex-a.json" if n.startswith("vex-$") else n.replace("${image}", "scf-backend") for n in names]
    assert sorted(names) == ["image-scan-gate.json", "sbom-scf-backend.cdx.json", "vex-a.json"]
    assert names, "the step writes no evidence"
    for name in names:
        assert any(fnmatch.fnmatch(name, p) for p in EVIDENCE_PATTERNS), name
