"""Every release image's SBOM is scanned with Grype and the repository's VEX.

Plan 7.2. ``scan-sbom.sh`` downloads the vulnerability database once, scans
each ``*.cdx.json`` SBOM with every OpenVEX document in ``security/vex/``, and
writes one Grype JSON report per image. It reports findings and does not judge
them (the gate is plan 7.3). It fails, so the release waits, when there is
nothing to scan, a VEX file is not OpenVEX, the database cannot be downloaded,
the database used is invalid or older than ``MAX_DB_AGE_HOURS`` (checked by the
script itself, not only by Grype), or Grype fails or writes no usable report.

The release workflow runs it in a ``scan`` job after ``images`` (dry runs too),
and ``cross_and_merge`` waits for it.

All of it runs here with a stand-in ``grype``. Nothing is downloaded, and no
real tool runs.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
RELEASE = REPO_ROOT / "scripts" / "release"
VEX = REPO_ROOT / "security" / "vex"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"

# scripts/release is not on the OSS keep-list, and neither is the release workflow.
pytestmark = pytest.mark.skipif(not RELEASE.is_dir(), reason="scripts/release is private-only")

GRYPE_STUB = r'''
import datetime, json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GRYPE_LOG"], "a") as log:
    log.write(json.dumps({"args": args, "env": {k: v for k, v in os.environ.items() if k.startswith("GRYPE_")}}) + "\n")
spec = json.load(open(os.environ["FAKE_GRYPE"]))
if args[:2] == ["db", "update"]:
    if spec.get("update_exit"):
        sys.stderr.write("unable to update vulnerability database\n"); sys.exit(spec["update_exit"])
    sys.exit(0)
target = [a for a in args if a.startswith("json=")][0].split("=", 1)[1]
report = spec.get("report", "default")
if report == "default":
    built = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=spec.get("built_hours_ago", 1))
    report = {"matches": spec.get("matches", [{"vulnerability": {"id": "CVE-2026-0001", "severity": "High"}}]),
              "descriptor": {"db": {"status": {"valid": spec.get("valid", True),
                                               "built": built.strftime("%Y-%m-%dT%H:%M:%SZ")}}}}
if report is not None:
    open(target, "w").write(report if isinstance(report, str) else json.dumps(report))
if spec.get("exit"):
    sys.stderr.write("grype: failed to scan\n"); sys.exit(spec["exit"])
'''

OPENVEX = {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "https://example.invalid/vex/1",
           "author": "test", "timestamp": "2026-11-14T00:00:00Z", "version": 1, "statements": []}


class Scan:
    """SBOMs, a VEX directory and a stand-in grype in a temporary directory."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        stub = self.bin / "grype"
        stub.write_text(f"#!{sys.executable}\n{textwrap.dedent(GRYPE_STUB)}")
        stub.chmod(0o755)
        self.sboms = tmp_path / "sbom"
        self.sboms.mkdir()
        for name in ("scf-backend", "scf-frontend"):
            (self.sboms / f"{name}.cdx.json").write_text(json.dumps({"bomFormat": "CycloneDX", "components": [{}]}))
        self.vex = tmp_path / "vex"
        self.vex.mkdir()
        self.out = tmp_path / "reports"
        self.grype = {}

    def run(self, script: Path = RELEASE / "scan-sbom.sh", extra_env: dict | None = None):
        (self.dir / "grype.json").write_text(json.dumps(self.grype))
        (self.dir / "grype.log").touch()
        env = {"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "HOME": str(self.dir),
               "FAKE_GRYPE": str(self.dir / "grype.json"), "FAKE_GRYPE_LOG": str(self.dir / "grype.log")}
        env |= extra_env or {}
        return subprocess.run(["bash", str(script), str(self.sboms), str(self.vex), str(self.out)], env=env,
                              capture_output=True, text=True, timeout=60)

    def calls(self) -> list:
        return [json.loads(line) for line in (self.dir / "grype.log").read_text().splitlines()]


@pytest.fixture
def scan(tmp_path):
    return Scan(tmp_path)


# -- the committed pin and VEX directory ------------------------------------------


def test_grype_is_pinned():
    pins = {line.split()[0]: line.split() for line in (RELEASE / "tools.lock").read_text().splitlines()
            if line and not line.startswith("#")}
    name, version, sha, url = pins["grype"]
    assert re.fullmatch(r"[0-9a-f]{64}", sha)
    assert url == f"https://github.com/anchore/grype/releases/download/v{version}/grype_{version}_linux_amd64.tar.gz"


def test_every_committed_vex_document_is_openvex():
    assert (VEX / "README.md").is_file()
    for doc in VEX.glob("*.json"):
        body = json.loads(doc.read_text())
        assert body["@context"].startswith("https://openvex.dev/ns"), doc.name
        assert isinstance(body["statements"], list), doc.name


def test_the_vex_directory_stays_out_of_the_oss_snapshot():
    prepare = (REPO_ROOT / "scripts" / "prepare-oss.sh").read_text()
    assert re.search(r"^rm -rf security\s*$", prepare, re.M), "prepare-oss.sh must remove security/"
    allow = (REPO_ROOT / "scripts" / "oss-toplevel-allow.txt").read_text().split()
    assert "security" not in allow and "security/" not in allow


# -- scan-sbom.sh -------------------------------------------------------------------


def test_each_sbom_is_scanned_with_every_vex_document(scan):
    for n in (1, 2):
        (scan.vex / f"v{n}.json").write_text(json.dumps(OPENVEX))
    (scan.vex / "README.md").write_text("not a VEX document, and not read")
    proc = scan.run()
    assert proc.returncode == 0, proc.stderr
    update, *scans = scan.calls()
    assert update["args"] == ["db", "update"]
    assert update["env"]["GRYPE_DB_VALIDATE_AGE"] == "true"
    assert update["env"]["GRYPE_DB_MAX_ALLOWED_BUILT_AGE"] == "120h"
    assert update["env"]["GRYPE_DB_REQUIRE_UPDATE_CHECK"] == "true"
    assert update["env"]["GRYPE_CHECK_FOR_APP_UPDATE"] == "false"
    assert len(scans) == 2
    for call, name in zip(scans, ("scf-backend", "scf-frontend")):
        args = call["args"]
        assert args[0] == f"sbom:{scan.sboms / name}.cdx.json"
        assert f"json={scan.out / name}.grype.json" in args
        vex = [args[i + 1] for i, a in enumerate(args) if a == "--vex"]
        assert vex == [str(scan.vex / "v1.json"), str(scan.vex / "v2.json")]
        assert "--fail-on" not in args and "-f" not in args, "7.2 reports; the gate is 7.3"
        assert call["env"]["GRYPE_DB_AUTO_UPDATE"] == "false", "the database is fetched once, not per image"
        assert call["env"]["GRYPE_DB_VALIDATE_AGE"] == "true"
        assert (scan.out / f"{name}.grype.json").is_file()
    assert "VEX documents applied: 2" in proc.stdout


def test_findings_are_reported_not_judged(scan):
    scan.grype = {"matches": [{"vulnerability": {"severity": "Critical"}}, {"vulnerability": {"severity": "High"}},
                              {"vulnerability": {"severity": "Critical"}}]}
    proc = scan.run()
    assert proc.returncode == 0, proc.stderr
    assert "scf-backend: 3 findings (Critical 2, High 1)" in proc.stdout


def test_no_vex_documents_is_a_scan_without_vex(scan):
    proc = scan.run()
    assert proc.returncode == 0, proc.stderr
    assert all("--vex" not in c["args"] for c in scan.calls())
    assert "VEX documents applied: 0" in proc.stdout


@pytest.mark.parametrize(
    "setup, grype, message",
    [
        ("no-sboms", {}, "no SBOM"),
        ("no-vex-dir", {}, "does not exist"),
        ("vex-not-json", {}, "not readable JSON"),
        ("vex-not-openvex", {}, "not an OpenVEX document"),
        ("vex-no-statements", {}, "no statements list"),
        ("", {"update_exit": 1}, "could not be downloaded"),
        ("", {"exit": 1}, ""),
        # A run that wrote a plausible report and then failed is not trusted.
        ("", {"exit": 1, "matches": []}, ""),
        ("", {"report": "not json"}, "no readable scan report"),
        ("", {"report": None}, "no readable scan report"),
        ("", {"report": {"descriptor": {}}}, "no matches list"),
        ("", {"valid": False}, "is not valid"),
        ("", {"built_hours_ago": 121}, "more than 120h"),
        ("", {"report": {"matches": [], "descriptor": {"db": {"status": {"valid": True}}}}}, "does not say when"),
        ("", {"report": {"matches": [], "descriptor": {"db": {"status": {"valid": True, "built": "2026-11-14T00:00:00"}}}}},
         "no time zone"),
    ],
    ids=["no-sboms", "no-vex-dir", "vex-not-json", "vex-not-openvex", "vex-no-statements", "db-download-failed",
         "grype-failed", "grype-failed-after-writing", "report-not-json", "no-report", "no-matches", "db-invalid",
         "db-stale", "db-age-unknown", "db-age-naive"],
)
def test_a_scan_that_cannot_be_trusted_fails(scan, setup, grype, message):
    if setup == "no-sboms":
        shutil.rmtree(scan.sboms); scan.sboms.mkdir()
    elif setup == "no-vex-dir":
        shutil.rmtree(scan.vex)
    elif setup == "vex-not-json":
        (scan.vex / "bad.json").write_text("{")
    elif setup == "vex-not-openvex":
        (scan.vex / "bad.json").write_text(json.dumps({"statements": []}))
    elif setup == "vex-no-statements":
        (scan.vex / "bad.json").write_text(json.dumps({k: v for k, v in OPENVEX.items() if k != "statements"}))
    scan.grype = grype
    proc = scan.run()
    assert proc.returncode != 0, proc.stdout
    assert message in proc.stdout + proc.stderr
    if setup or "update_exit" in grype:
        assert [c for c in scan.calls() if c["args"][:2] != ["db", "update"]] == [], "nothing is scanned"


@pytest.mark.parametrize("hours, built, ok", [("24", 23, True), ("24", 25, False)])
def test_the_database_age_limit_is_configurable(scan, hours, built, ok):
    scan.grype = {"built_hours_ago": built}
    proc = scan.run(extra_env={"MAX_DB_AGE_HOURS": hours})
    assert (proc.returncode == 0) is ok, proc.stdout + proc.stderr
    assert scan.calls()[0]["env"]["GRYPE_DB_MAX_ALLOWED_BUILT_AGE"] == f"{hours}h"


@pytest.mark.parametrize("hours", ["0", "-1", "1.5", "24h", " "])
def test_an_unclear_age_limit_scans_nothing(scan, hours):
    proc = scan.run(extra_env={"MAX_DB_AGE_HOURS": hours})
    assert proc.returncode != 0
    assert scan.calls() == []


def test_a_stale_report_from_an_earlier_run_is_not_reused(scan):
    assert scan.run().returncode == 0
    scan.grype = {"report": None}
    proc = scan.run()
    assert proc.returncode != 0
    assert not (scan.out / "scf-backend.grype.json").exists()


# -- the release workflow -------------------------------------------------------------


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def _needs(job: dict) -> list:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else needs


def _step(job: dict, name: str) -> dict:
    [step] = [s for s in job["steps"] if s.get("name") == name]
    return step


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_scan_job_runs_after_the_images_on_every_run():
    jobs = _jobs()
    job = jobs["scan"]
    assert set(_needs(job)) >= {"preflight", "images"}
    assert "dry_run" not in str(job.get("if", "")), "dry runs are scanned too"
    # actions: read lets scan_evidence.py read the commit's scan-summary-* artifacts.
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    assert not [s for s in job["steps"] if s.get("continue-on-error")]
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[0-9a-f]{40}", step["uses"].split("@")[1].split()[0]), step["name"]
    checkout = _step(job, "Check out the release tools and VEX documents")["with"]
    assert checkout["persist-credentials"] is False
    assert set(checkout["sparse-checkout"].split()) == {"scripts/release", "security/vex"}
    download = _step(job, "Download the SBOMs")["with"]
    assert download["pattern"] == "sbom-*" and download["merge-multiple"] is True
    upload = _step(job, "Upload the scan reports")["with"]
    assert upload["if-no-files-found"] == "error"
    assert "scan" in _needs(jobs["cross_and_merge"]), "nothing is published before the scan has run"


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_release_summary_reports_the_scan():
    final = _jobs()["finalize"]
    assert "scan" in _needs(final)
    step = _step(final, "Write the release summary")
    assert step["env"]["R_SCAN"] == "${{ needs.scan.result }}"
    assert '| scan | ${R_SCAN} |' in step["run"]
    [loop] = [line for line in step["run"].splitlines() if line.strip().startswith("for r in")]
    assert '"$R_SCAN"' in loop, "a failed scan offers the resume instructions"


def _run_scan_step(scan: Scan):
    """Run the workflow's own scan step, with a fetched stand-in grype."""
    work = scan.dir / "workspace"
    tools = work / "release-tools"
    shutil.copytree(RELEASE, tools / "scripts" / "release")
    (tools / "security" / "vex").mkdir(parents=True)
    (tools / "security" / "vex" / "a.json").write_text(json.dumps(OPENVEX))
    runner = scan.dir / "runner"
    shutil.copytree(scan.sboms, runner / "sbom")
    # fetch-tool.sh is tested in test_release_tools.py; here it is replaced by
    # a copy of the stand-in so the step's own wiring is what runs.
    fetch = tools / "scripts" / "release" / "fetch-tool.sh"
    fetch.write_text('#!/usr/bin/env bash\nset -euo pipefail\nmkdir -p "$2"\n'
                     f'[ "$1" = grype ] && cp {scan.bin / "grype"} "$2/grype"\n')
    fetch.chmod(0o755)
    step = _step(_jobs()["scan"], "Scan the SBOMs")
    (scan.dir / "grype.json").write_text(json.dumps(scan.grype))
    (scan.dir / "grype.log").touch()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(scan.dir), "RUNNER_TEMP": str(runner),
           "GITHUB_STEP_SUMMARY": str(scan.dir / "summary.md"),
           "FAKE_GRYPE": str(scan.dir / "grype.json"), "FAKE_GRYPE_LOG": str(scan.dir / "grype.log")}
    # python3 must resolve for the script's checks; keep the interpreter's dir.
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
    # GitHub runs a step with no `shell:` as `bash -e {0}`: no pipefail unless the step sets it.
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step["run"]], cwd=work,
                          env=env, capture_output=True, text=True, timeout=60)
    return proc, runner


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_scan_step_scans_the_downloaded_sboms(scan):
    proc, runner = _run_scan_step(scan)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    scans = [c for c in scan.calls() if c["args"][:2] != ["db", "update"]]
    assert len(scans) == 2
    assert all("--vex" in c["args"] for c in scans)
    assert (runner / "scan" / "scf-backend.grype.json").is_file()
    assert "scf-backend: 1 findings" in (scan.dir / "summary.md").read_text()


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
@pytest.mark.parametrize("grype", [{"update_exit": 1}, {"valid": False}], ids=["db-download-failed", "db-invalid"])
def test_a_failed_scan_fails_the_step(scan, grype):
    """The step pipes the scan through tee; the pipe must not hide its failure."""
    scan.grype = grype
    proc, _ = _run_scan_step(scan)
    assert proc.returncode != 0, proc.stdout
