"""The image scan gate judges every Critical or High finding (plan 7.3).

``scripts/release/scan_gate.py`` reads the Grype reports scan-sbom.sh wrote:

- a finding with a fix blocks, even when a VEX statement covers it;
- a finding with no fix and a VEX ``not_affected`` statement is waived;
- a finding with no fix and no statement blocks (untriaged);
- an image with no usable report, or a report that does not show every VEX
  document applied, blocks (missing).

Medium and lower findings, and matches Grype ignores by its own built-in rules,
are counted and never block. Decision D2: before ``--enforce-from`` the gate
reports "would block" and exits 0; from that date it exits 1.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
GATE = REPO_ROOT / "scripts" / "release" / "scan_gate.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"
PUBLISHER = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"

pytestmark = pytest.mark.skipif(not GATE.exists(), reason="scripts/release is private-only")

IMAGES = ["scf-backend", "scf-frontend"]
BEFORE, ENFORCE, AFTER = "2026-12-13", "2026-12-14", "2026-12-14"


def match(cve="CVE-2026-1", severity="High", state="not-fixed", versions=(), vex=None):
    entry = {"vulnerability": {"id": cve, "severity": severity, "fix": {"state": state, "versions": list(versions)}},
             "artifact": {"name": "openssl", "version": "3.0.1"}}
    if vex is not None:
        entry["appliedIgnoreRules"] = [{"vex-status": vex, "namespace": "vex"}] if vex else [{"namespace": "x"}]
    return entry


class Gate:
    def __init__(self, tmp_path: Path):
        self.dir = tmp_path
        self.reports = tmp_path / "reports"
        self.reports.mkdir()
        self.vex = tmp_path / "vex"
        self.vex.mkdir()
        self.out = tmp_path / "out"
        for image in IMAGES:
            self.report(image)

    def report(self, image, matches=(), ignored=(), vex_docs=None, raw=None):
        path = self.reports / f"{image}.grype.json"
        if raw is not None:
            path.write_text(raw); return
        docs = vex_docs if vex_docs is not None else [str(self.vex / p.name) for p in sorted(self.vex.glob("*.json"))]
        doc = {"matches": list(matches), "descriptor": {"configuration": {"vex-documents": docs}}}
        if ignored:
            doc["ignoredMatches"] = list(ignored)
        path.write_text(json.dumps(doc))

    def add_vex(self, name="a.json"):
        (self.vex / name).write_text(json.dumps({"@context": "https://openvex.dev/ns/v0.2.0", "statements": []}))
        for image in IMAGES:  # clean reports that applied the new set
            self.report(image)

    def run(self, today=AFTER, images=" ".join(IMAGES), enforce_from=ENFORCE):
        proc = subprocess.run([sys.executable, "-I", str(GATE), "--reports", str(self.reports), "--vex", str(self.vex),
                               "--images", images, "--enforce-from", enforce_from, "--out", str(self.out),
                               "--today", today], capture_output=True, text=True, timeout=60)
        summary = json.loads((self.out / "gate-summary.json").read_text()) if (self.out / "gate-summary.json").exists() else None
        return proc, summary

    def outcomes(self, summary):
        return sorted(f["outcome"] for f in summary["findings"])


@pytest.fixture
def gate(tmp_path):
    return Gate(tmp_path)


def test_a_clean_scan_passes(gate):
    proc, summary = gate.run()
    assert proc.returncode == 0, proc.stderr
    assert summary["blocked"] is False and summary["enforcing"] is True
    assert "Image scan gate: PASS" in proc.stdout
    assert (gate.out / "gate-summary.md").read_text().startswith("### Image scan gate: PASS")


@pytest.mark.parametrize("severity", ["Critical", "High"])
def test_a_fixable_critical_or_high_blocks(gate, severity):
    gate.report("scf-backend", [match(severity=severity, state="fixed", versions=["3.0.2"])])
    proc, summary = gate.run()
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["fixable"]
    assert "3.0.2" in (gate.out / "gate-summary.md").read_text()


def test_a_vex_statement_does_not_waive_a_finding_that_has_a_fix(gate):
    gate.report("scf-backend", ignored=[match(state="fixed", versions=["3.0.2"], vex="not_affected")])
    proc, summary = gate.run()
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["fixable"]


@pytest.mark.parametrize("state", ["not-fixed", "wont-fix", "unknown", ""])
def test_no_fix_and_a_not_affected_statement_is_waived(gate, state):
    gate.report("scf-backend", ignored=[match(state=state, vex="not_affected")])
    proc, summary = gate.run()
    assert proc.returncode == 0, proc.stdout
    assert gate.outcomes(summary) == ["waived"]


@pytest.mark.parametrize("state", ["not-fixed", "wont-fix", "unknown"])
def test_no_fix_and_no_statement_blocks(gate, state):
    gate.report("scf-frontend", [match(severity="Critical", state=state)])
    proc, summary = gate.run()
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["untriaged"]


def test_a_vex_fixed_status_is_not_a_waiver(gate):
    gate.report("scf-backend", ignored=[match(state="not-fixed", vex="fixed")])
    proc, summary = gate.run()
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["untriaged"]


@pytest.mark.parametrize("severity", ["Medium", "Low", "Negligible", "Unknown"])
def test_lower_severities_are_reported_not_blocking(gate, severity):
    gate.report("scf-backend", [match(severity=severity, state="fixed", versions=["9"])])
    proc, summary = gate.run()
    assert proc.returncode == 0
    assert gate.outcomes(summary) == ["reported"]


def test_matches_grype_ignores_by_its_own_rules_do_not_block(gate):
    gate.report("scf-backend", ignored=[match(severity="Critical", state="fixed", versions=["1"], vex="")])
    proc, summary = gate.run()
    assert proc.returncode == 0
    assert gate.outcomes(summary) == ["ignored-by-scanner"]


@pytest.mark.parametrize(
    "setup, reason",
    [
        ("no-report", "no scan report"),
        ("not-json", "not readable JSON"),
        ("no-matches", "no matches list"),
        ("bad-ignored", "ignoredMatches is not a list"),
        ("no-vex-config", "does not say which VEX documents"),
        ("vex-not-applied", "VEX applied"),
        ("no-vex-dir", "does not exist"),
    ],
)
def test_a_missing_or_incomplete_report_blocks(gate, setup, reason):
    if setup == "no-report":
        (gate.reports / "scf-frontend.grype.json").unlink()
    elif setup == "not-json":
        gate.report("scf-frontend", raw="{")
    elif setup == "no-matches":
        gate.report("scf-frontend", raw=json.dumps({"descriptor": {"configuration": {"vex-documents": []}}}))
    elif setup == "bad-ignored":
        gate.report("scf-frontend", raw=json.dumps({"matches": [], "ignoredMatches": {},
                                                    "descriptor": {"configuration": {"vex-documents": []}}}))
    elif setup == "no-vex-config":
        gate.report("scf-frontend", raw=json.dumps({"matches": [], "descriptor": {}}))
    elif setup == "vex-not-applied":
        gate.add_vex()
        gate.report("scf-frontend", vex_docs=[])
    elif setup == "no-vex-dir":
        gate.vex.rmdir()
    proc, summary = gate.run()
    assert proc.returncode == 1
    assert summary["missing"] and reason in json.dumps(summary["missing"])


def test_every_vex_document_applied_passes(gate):
    gate.add_vex("a.json")
    gate.add_vex("b.json")
    proc, summary = gate.run()
    assert proc.returncode == 0, proc.stdout


def test_before_the_enforce_date_a_block_is_reported_not_enforced(gate):
    gate.report("scf-backend", [match(state="fixed", versions=["3.0.2"])])
    proc, summary = gate.run(today=BEFORE)
    assert proc.returncode == 0
    assert summary["blocked"] is True and summary["enforcing"] is False
    assert "REPORT ONLY: would block" in proc.stdout and ENFORCE in proc.stdout
    assert "::warning::" in proc.stdout


@pytest.mark.parametrize("args", [{"images": " "}, {"enforce_from": "soon"}, {"enforce_from": "2026-13-01"}])
def test_unclear_arguments_are_a_usage_error(gate, args):
    proc, summary = gate.run(**args)
    assert proc.returncode == 2
    assert summary is None


# -- the release workflow ------------------------------------------------------------


def _scan_job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["scan"]


def _step(job: dict, name: str) -> dict:
    [step] = [s for s in job["steps"] if s.get("name") == name]
    return step


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_gate_runs_after_the_scan_and_its_summary_is_uploaded_even_when_it_blocks():
    job = _scan_job()
    names = [s.get("name") for s in job["steps"]]
    assert names.index("Scan the SBOMs") < names.index("Gate the findings") < names.index("Upload the scan reports")
    step = _step(job, "Gate the findings")
    assert "continue-on-error" not in step
    assert "scan_gate.py" in step["run"] and "--enforce-from" in step["run"]
    assert '"$GITHUB_STEP_SUMMARY"' in step["run"]
    upload = _step(job, "Upload the scan reports")
    assert upload.get("if") == "${{ !cancelled() }}"
    paths = upload["with"]["path"].split("\n")
    assert "${{ runner.temp }}/scan/*.grype.json" in paths and "${{ runner.temp }}/scan/gate-summary.*" in paths


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_enforce_date_is_declared_once_and_is_a_date():
    env = yaml.safe_load(WORKFLOW.read_text())["env"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", env["SCAN_GATE_ENFORCE_FROM"])
    assert _step(_scan_job(), "Gate the findings")["env"]["ENFORCE_FROM"] == "${{ env.SCAN_GATE_ENFORCE_FROM }}"


@pytest.mark.skipif(not (WORKFLOW.exists() and PUBLISHER.exists()), reason="workflows are private-only")
def test_the_gate_expects_exactly_the_images_the_publisher_builds():
    matrix = yaml.safe_load(PUBLISHER.read_text())["jobs"]["build"]["strategy"]["matrix"]["include"]
    wf_env = yaml.safe_load(WORKFLOW.read_text())["env"]
    images = re.sub(r"\$\{\{ env\.(\w+) \}\}", lambda m: wf_env[m.group(1)], _step(_scan_job(), "Gate the findings")["env"]["IMAGES"])
    assert images.split() == [m["name"] for m in matrix]


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
@pytest.mark.parametrize("enforce_from, blocked, code", [("2000-01-01", True, 1), ("2999-01-01", True, 0),
                                                         ("2000-01-01", False, 0)],
                         ids=["enforcing-block", "report-only-block", "enforcing-pass"])
def test_the_gate_step_fails_only_when_enforcing_and_always_writes_the_summary(gate, enforce_from, blocked, code):
    """Run the workflow's own step, as GitHub does (`bash -e`)."""
    work = gate.dir / "workspace"
    tools = work / "release-tools"
    (tools / "scripts" / "release").mkdir(parents=True)
    (tools / "scripts" / "release" / "scan_gate.py").write_text(GATE.read_text())
    (tools / "security").mkdir()
    gate.vex.rename(tools / "security" / "vex")
    runner = gate.dir / "runner"
    runner.mkdir()
    gate.reports.rename(runner / "scan")
    if blocked:
        doc = json.loads((runner / "scan" / "scf-backend.grype.json").read_text())
        doc["matches"] = [match(state="fixed", versions=["3.0.2"])]
        (runner / "scan" / "scf-backend.grype.json").write_text(json.dumps(doc))
    step = _step(_scan_job(), "Gate the findings")
    env = {"PATH": f"{Path(sys.executable).parent}{os.pathsep}/usr/bin:/bin", "HOME": str(gate.dir),
           "RUNNER_TEMP": str(runner), "GITHUB_STEP_SUMMARY": str(gate.dir / "summary.md"),
           "IMAGES": " ".join(IMAGES), "ENFORCE_FROM": enforce_from}
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step["run"]], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert (gate.dir / "summary.md").read_text().startswith("### Image scan gate:")
    assert (runner / "scan" / "gate-summary.json").is_file()
