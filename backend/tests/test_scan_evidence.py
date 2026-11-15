"""The release carries a summary of each source scan that ran on the released commit.

Two scripts in ``scripts/release`` do this:

* ``scan_summary.py`` runs inside each scan job (ci.yml, semgrep.yml,
  gitleaks.yml). It reads the scanner's own output and writes one small
  record: tool, version, commit, run, outcome, counts by severity and the
  tree-level scope. It never copies a finding's path, rule text or message.
* ``scan_evidence.py`` runs in the release workflow's scan job. It finds the
  runs of those jobs on the commit under release through the Actions API,
  downloads each record, checks it, and writes ``scan-<tool>.json`` and
  ``scan-summary.md`` for release-evidence/. A scan that is missing, failed or
  cannot be trusted is written down as such, never as a pass. Like the image
  scan gate, it reports only before ``--enforce-from`` and blocks from then.

The Actions API is played by a stand-in ``gh`` that serves canned responses,
so the collector runs exactly as in the workflow and nothing reaches GitHub.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
RELEASE_DIR = REPO_ROOT / "scripts" / "release"
SUMMARY = RELEASE_DIR / "scan_summary.py"
COLLECTOR = RELEASE_DIR / "scan_evidence.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-oss.yml"
PRIVATE_REPO = "MarkAC007/scf-controls-platform"

pytestmark = pytest.mark.skipif(not COLLECTOR.exists(), reason="scripts/release is private-only")

SHA = "4b5cb58c4065b613b3706d75ef92f4f241aa3ea0"
OTHER_SHA = "f" * 40
RUN_ID = 43737144084  # the CI run on SHA, used by the in-job summary tests
TOOLS = ["gitleaks", "osv", "semgrep", "zap", "zizmor"]
BEFORE, ENFORCE, AFTER = "2026-12-13", "2026-12-14", "2026-12-14"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


collector = _load(COLLECTOR, "scan_evidence") if COLLECTOR.exists() else None


# --- scan_summary.py: one record from the scanner's own output -----------------------


def _summarise(tmp_path: Path, tool: str, *args: str, event: str = "push") -> tuple[int, dict | None]:
    out = tmp_path / "out" / f"scan-{tool}.json"
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_SHA": SHA,
        "GITHUB_RUN_ID": str(RUN_ID),
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_EVENT_NAME": event,
    }
    run = subprocess.run([sys.executable, "-I", str(SUMMARY), tool, "--out", str(out), *args],
                         env=env, capture_output=True, text=True, timeout=60)
    record = json.loads(out.read_text()) if out.exists() else None
    return run.returncode, record


def _write(tmp_path: Path, name: str, body) -> Path:
    path = tmp_path / name
    path.write_text(body if isinstance(body, str) else json.dumps(body))
    return path


ZAP_REPORT = {
    "@programName": "ZAP",
    "@version": "2.17.0",
    "site": [{
        "@name": "http://localhost:5173",
        "alerts": [
            {"pluginid": "10055", "name": "CSP: Wildcard Directive", "riskcode": "2", "count": "2",
             "instances": [{"uri": "http://localhost:5173/secret-admin-path"}]},
            {"pluginid": "10055", "name": "CSP: style-src unsafe-inline", "riskcode": "2", "count": "3"},
            {"pluginid": "90004", "name": "COEP Header Missing", "riskcode": "1", "count": "3"},
            {"pluginid": "10109", "name": "Modern Web Application", "riskcode": "0", "count": "3"},
        ],
    }],
}


def _common(record: dict, tool: str) -> None:
    assert record["schema"] == 1 and record["tool"] == tool
    assert record["commit"] == SHA and record["run_id"] == RUN_ID and record["run_attempt"] == 1
    assert record["event"] == "push"


def test_a_clean_zap_scan_is_a_pass_with_alert_counts_by_risk(tmp_path):
    report = _write(tmp_path, "zap-report.json", ZAP_REPORT)
    status, record = _summarise(tmp_path, "zap", "--input", str(report), "--exit-code", "0")
    assert status == 0
    _common(record, "zap")
    assert record["outcome"] == "pass" and record["version"] == "2.17.0"
    assert record["counts"] == {"high": 0, "medium": 2, "low": 1, "informational": 1}
    assert record["totals"] == {"instances": 11}
    assert record["scope"] == ["http://localhost:5173"]
    assert record["reason"] is None


@pytest.mark.parametrize("code, outcome", [("1", "fail"), ("2", "fail"), ("3", "error"), ("", "error"), ("x", "error")])
def test_zap_exit_status_decides_the_outcome(tmp_path, code, outcome):
    report = _write(tmp_path, "zap-report.json", ZAP_REPORT)
    _, record = _summarise(tmp_path, "zap", "--input", str(report), "--exit-code", code)
    assert record["outcome"] == outcome
    if outcome == "error":
        assert record["reason"]


def test_a_zap_scan_with_no_report_is_an_error_not_a_pass(tmp_path):
    status, record = _summarise(tmp_path, "zap", "--input", str(tmp_path / "absent.json"), "--exit-code", "0")
    assert status == 0, "the summary is still written, so the release can say what happened"
    assert record["outcome"] == "error" and record["version"] is None
    assert record["counts"] == {} and record["scope"] == []


OSV_VERDICT = {
    "status": 0,
    "scanner": "ghcr.io/google/osv-scanner:v2.7.0@sha256:" + "4" * 64,
    "packages": {"backend/requirements.txt": 150, "webclient/package-lock.json": 577},
    "ratings": {"critical": 0, "high": 1, "medium": 6, "low": 3, "unrated": 0},
    "blocking": 0, "waived": 1, "expired_waivers": 0, "unused_waivers": 0,
    "findings": [{"package": "private-internal-thing"}],
}


def test_an_osv_verdict_becomes_counts_by_rating(tmp_path):
    verdict = _write(tmp_path, "verdict.json", OSV_VERDICT)
    _, record = _summarise(tmp_path, "osv", "--input", str(verdict), "--step-outcome", "success")
    _common(record, "osv")
    assert record["outcome"] == "pass" and record["version"] == "2.7.0"
    assert record["counts"] == {"critical": 0, "high": 1, "medium": 6, "low": 3, "unrated": 0}
    assert record["totals"] == {"packages": 727, "blocking": 0, "waived": 1, "expired_waivers": 0}
    assert record["scope"] == ["backend/requirements.txt", "webclient/package-lock.json"]
    assert "private-internal-thing" not in json.dumps(record)


@pytest.mark.parametrize("verdict_status, step, outcome", [
    (1, "failure", "fail"),
    (0, "failure", "error"),   # the policy passed but the step did not
    (None, "failure", "error"),  # the scanner never got as far as the policy
])
def test_osv_outcome_follows_the_policy_and_the_step(tmp_path, verdict_status, step, outcome):
    path = tmp_path / "verdict.json"
    if verdict_status is not None:
        path.write_text(json.dumps({**OSV_VERDICT, "status": verdict_status}))
    _, record = _summarise(tmp_path, "osv", "--input", str(path), "--step-outcome", step)
    assert record["outcome"] == outcome


def _zizmor_finding(severity: str, ident: str = "unpinned-uses", ignored: bool = False) -> dict:
    return {"ident": ident, "desc": "unpinned action reference", "url": "https://docs.zizmor.sh/",
            "determinations": {"confidence": "High", "severity": severity, "persona": "Regular"},
            "ignored": ignored,
            "locations": [{"symbolic": {"key": {"Local": {"given_path": ".github/workflows/private.yml"}}}}]}


def test_a_clean_zizmor_pass(tmp_path):
    findings = _write(tmp_path, "zizmor.json", [])
    _, record = _summarise(tmp_path, "zizmor", "--input", str(findings), "--exit-code", "0",
                           "--step-outcome", "success", "--tool-version", "zizmor 1.32.0")
    _common(record, "zizmor")
    assert record["outcome"] == "pass" and record["version"] == "1.32.0"
    assert record["counts"] == {"high": 0, "medium": 0, "low": 0, "informational": 0, "unknown": 0}
    assert record["scope"] == ["."]


def test_zizmor_findings_are_counted_by_severity_and_never_named(tmp_path):
    findings = _write(tmp_path, "zizmor.json", [
        _zizmor_finding("High"), _zizmor_finding("High", "template-injection"), _zizmor_finding("Medium"),
        _zizmor_finding("Low", ignored=True),
    ])
    _, record = _summarise(tmp_path, "zizmor", "--input", str(findings), "--exit-code", "14",
                           "--step-outcome", "failure", "--tool-version", "zizmor 1.32.0")
    assert record["outcome"] == "fail"
    assert record["counts"] == {"high": 2, "medium": 1, "low": 0, "informational": 0, "unknown": 0}
    assert record["totals"] == {"ignored": 1}
    text = json.dumps(record)
    assert "template-injection" not in text and "private.yml" not in text


@pytest.mark.parametrize("body, code, step", [
    ("not json", "0", "success"),
    ("[]", "3", "failure"),            # zizmor collected no inputs
    ("[]", "0", "failure"),            # the lint failed, the JSON pass found nothing
    ('{"findings": []}', "0", "success"),
])
def test_a_zizmor_result_that_cannot_be_read_is_an_error(tmp_path, body, code, step):
    findings = _write(tmp_path, "zizmor.json", body)
    _, record = _summarise(tmp_path, "zizmor", "--input", str(findings), "--exit-code", code,
                           "--step-outcome", step, "--tool-version", "zizmor 1.32.0")
    assert record["outcome"] == "error" and record["reason"]


def _sarif(results: list[dict], name: str = "Semgrep OSS", version: str | None = "1.184.0", rules: int = 3) -> dict:
    driver = {"name": name, "rules": [{"id": f"rule.{n}"} for n in range(rules)]}
    if version is not None:
        driver["semanticVersion"] = version
    return {"version": "2.1.0", "runs": [{"tool": {"driver": driver}, "results": results}]}


def _result(level: str = "warning", suppressed: bool = False) -> dict:
    result = {"ruleId": "python.lang.security.audit.formatted-sql-query", "level": level,
              "message": {"text": "Detected possible formatted SQL query"},
              "locations": [{"physicalLocation": {"artifactLocation": {"uri": "backend/app/private_module.py"},
                                                  "region": {"startLine": 305}}}]}
    if suppressed:
        result["suppressions"] = [{"kind": "inSource"}]
    return result


def test_a_semgrep_scan_is_reported_with_counts_by_level(tmp_path):
    sarif = _write(tmp_path, "semgrep.sarif", _sarif(
        [_result("error"), _result("warning"), _result("warning"), _result("note"), _result("warning", True)]))
    _, record = _summarise(tmp_path, "semgrep", "--input", str(sarif), "--step-outcome", "success")
    _common(record, "semgrep")
    # Nothing it finds on a push fails anything, so it is not called a pass.
    assert record["outcome"] == "reported" and record["version"] == "1.184.0"
    assert record["counts"] == {"error": 1, "warning": 2, "note": 1}
    assert record["totals"] == {"rules": 3, "suppressed_in_source": 1}
    assert record["scope"] == ["."]
    text = json.dumps(record)
    assert "private_module" not in text and "formatted-sql" not in text and "SQL" not in text


@pytest.mark.parametrize("step, body", [("failure", None), ("success", "{}"), ("success", "nope")])
def test_a_semgrep_scan_that_did_not_complete_is_an_error(tmp_path, step, body):
    path = tmp_path / "semgrep.sarif"
    if body is not None:
        path.write_text(body)
    _, record = _summarise(tmp_path, "semgrep", "--input", str(path), "--step-outcome", step)
    assert record["outcome"] == "error"


@pytest.mark.parametrize("event, scope", [
    ("push", ["git: the commits the push added"]),
    ("workflow_dispatch", ["git: the full history"]),
])
def test_a_clean_gitleaks_scan_says_which_history_it_covered(tmp_path, event, scope):
    sarif = _write(tmp_path, "results.sarif", _sarif([], name="gitleaks", version="v8.0.0", rules=208))
    _, record = _summarise(tmp_path, "gitleaks", "--input", str(sarif), "--step-outcome", "success",
                           "--tool-version", "v8.24.3", event=event)
    assert record["outcome"] == "pass"
    # The version is the binary that ran; gitleaks writes v8.0.0 into every SARIF it makes.
    assert record["version"] == "8.24.3"
    assert record["counts"] == {"leaks": 0} and record["totals"] == {"rules": 208}
    assert record["scope"] == scope


def test_a_leak_fails_and_is_only_counted(tmp_path):
    sarif = _write(tmp_path, "results.sarif", _sarif([_result("error")], name="gitleaks", rules=208))
    _, record = _summarise(tmp_path, "gitleaks", "--input", str(sarif), "--step-outcome", "failure",
                           "--tool-version", "8.24.3")
    assert record["outcome"] == "fail" and record["counts"] == {"leaks": 1}
    assert "private_module" not in json.dumps(record)


@pytest.mark.parametrize("version", ["", "gitleaks version: unknown", "8.24.3; rm -rf /"])
def test_a_version_that_is_not_a_version_is_not_recorded(tmp_path, version):
    sarif = _write(tmp_path, "results.sarif", _sarif([], name="gitleaks"))
    _, record = _summarise(tmp_path, "gitleaks", "--input", str(sarif), "--step-outcome", "success",
                           "--tool-version", version)
    assert record["version"] is None and record["outcome"] == "error"


def test_an_unknown_tool_is_a_usage_error(tmp_path):
    status, record = _summarise(tmp_path, "trivy", "--input", str(tmp_path / "x"))
    assert status == 2 and record is None


# --- scan_evidence.py: find, check and publish the records ---------------------------

STAND_IN_GH = r'''#!/usr/bin/env python3
"""gh api <path>: serves $STUB/routes.json; anything unknown is a 404."""
import json, os, sys
args = [a for a in sys.argv[1:]]
with open(os.path.join(os.environ["STUB"], "calls"), "a") as fh:
    fh.write(" ".join(args) + "\n")
if args[:1] != ["api"]:
    sys.exit("stand-in gh: only `gh api` is served")
rest = args[1:]
while rest and rest[0] == "-H":
    rest = rest[2:]
path = rest[0]
routes = json.load(open(os.path.join(os.environ["STUB"], "routes.json")))
route = routes.get(path)
if route is None:
    sys.stderr.write("gh: Not Found (HTTP 404)\n"); sys.exit(1)
if "status" in route:
    sys.stderr.write("gh: %s (HTTP %d)\n" % (route.get("message", "error"), route["status"])); sys.exit(1)
sys.stdout.buffer.write(open(os.path.join(os.environ["STUB"], route["file"]), "rb").read())
'''


def record(tool: str, **changes) -> dict:
    """What scan_summary.py writes in a passing run of that tool's job."""
    base = {
        # run_id None: Actions.run fills in the id of the run the record is attached to.
        "schema": 1, "tool": tool, "version": "1.2.3", "commit": SHA, "run_id": None, "run_attempt": 1,
        "event": "push", "outcome": "reported" if tool == "semgrep" else "pass",
        "scope": ["."], "counts": {"high": 0}, "totals": {}, "reason": None,
    }
    base.update(changes)
    return base


class Actions:
    """The Actions API as the collector sees it, for one commit."""

    def __init__(self, tmp_path: Path):
        self.dir = tmp_path / "stub"
        self.dir.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text(STAND_IN_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IEXEC)
        self.routes: dict = {}
        self.runs: dict[str, list] = {}
        self.next_id = 1000
        self.out = tmp_path / "evidence"

    def _serve(self, path: str, body: bytes) -> None:
        name = f"r{len(self.routes)}"
        (self.dir / name).write_bytes(body)
        self.routes[path] = {"file": name}

    def fail(self, path: str, status: int) -> None:
        self.routes[path] = {"status": status}

    def run(self, workflow: str, *, sha: str = SHA, event: str = "push", status: str = "completed",
            created: str = "2026-11-15T16:44:00Z", jobs: dict[str, tuple[str, int]] | None = None,
            artifacts: dict[str, list[dict]] | None = None) -> int:
        """One run of ``workflow``: jobs maps a name to (conclusion, attempt); artifacts maps a
        name to a list of artifact specs ({"record": dict | bytes, "expired": bool})."""
        self.next_id += 1
        run_id = self.next_id
        self.runs.setdefault(workflow, []).append({
            "id": run_id, "head_sha": sha, "event": event, "status": status,
            "conclusion": "success" if status == "completed" else None, "created_at": created,
            "path": f".github/workflows/{workflow}", "run_attempt": 1,
        })
        self._serve(f"repos/{PRIVATE_REPO}/actions/runs/{run_id}/jobs?filter=latest&per_page=100", json.dumps({
            "jobs": [{"name": n, "status": "completed", "conclusion": c, "run_attempt": a}
                     for n, (c, a) in (jobs or {}).items()]}).encode())
        for name, specs in (artifacts or {}).items():
            listed = []
            for spec in specs:
                self.next_id += 1
                body = spec["record"]
                if isinstance(body, dict):
                    if body.get("run_id") is None:
                        body = {**body, "run_id": run_id}
                    body = json.dumps(body).encode()
                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as zf:
                    zf.writestr(spec.get("member", name.replace("scan-summary-", "scan-") + ".json"), body)
                self._serve(f"repos/{PRIVATE_REPO}/actions/artifacts/{self.next_id}/zip", buffer.getvalue())
                listed.append({"id": self.next_id, "name": name, "expired": spec.get("expired", False),
                               "created_at": spec.get("created", "2026-11-15T16:50:00Z")})
            self._serve(f"repos/{PRIVATE_REPO}/actions/runs/{run_id}/artifacts?name={name}&per_page=100",
                        json.dumps({"artifacts": listed}).encode())
        return run_id

    def collect(self, today: str = BEFORE, sha: str = SHA) -> subprocess.CompletedProcess:
        for workflow, runs in self.runs.items():
            path = f"repos/{PRIVATE_REPO}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100"
            if path not in self.routes:
                self._serve(path, json.dumps({"workflow_runs": [r for r in runs if r["head_sha"] == sha]}).encode())
        (self.dir / "routes.json").write_text(json.dumps(self.routes))
        return subprocess.run(
            [sys.executable, "-I", str(COLLECTOR), "--repo", PRIVATE_REPO, "--sha", sha, "--out", str(self.out),
             "--enforce-from", ENFORCE, "--today", today],
            env={"PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}", "STUB": str(self.dir)},
            capture_output=True, text=True, timeout=60)

    def published(self) -> dict[str, dict]:
        return {p.stem.removeprefix("scan-"): json.loads(p.read_text()) for p in sorted(self.out.glob("scan-*.json"))}


def all_green(actions: Actions, **overrides) -> None:
    """Every scan ran on SHA and passed; ``overrides`` replaces one tool's record."""
    ci = {"zap": "Docker Build", "osv": "OSV Scan", "zizmor": "Workflow Lint"}

    def art(tool):
        return [{"record": overrides.get(tool, record(tool))}]

    actions.run("ci.yml", jobs={job: ("success", 1) for job in ci.values()},
                artifacts={f"scan-summary-{tool}": art(tool) for tool in ci})
    actions.run("semgrep.yml", jobs={"Static Analysis": ("success", 1)},
                artifacts={"scan-summary-semgrep": art("semgrep")})
    actions.run("gitleaks.yml", jobs={"Detect Secrets": ("success", 1)},
                artifacts={"scan-summary-gitleaks": art("gitleaks")})


def test_every_scan_on_the_commit_is_published_with_where_it_ran(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    run = actions.collect()
    assert run.returncode == 0, run.stdout + run.stderr
    published = actions.published()
    assert sorted(published) == TOOLS
    assert sorted(p.name for p in actions.out.iterdir()) == sorted(
        [f"scan-{t}.json" for t in TOOLS] + ["scan-summary.md"])
    zap = published["zap"]
    assert zap["outcome"] == "pass" and zap["commit"] == SHA and zap["version"] == "1.2.3"
    assert zap["workflow"] == "CI" and zap["job"] == "Docker Build" and zap["job_conclusion"] == "success"
    assert zap["run_id"] > 0 and zap["run_attempt"] == 1 and zap["scanner"] and zap["policy"]
    assert published["semgrep"]["outcome"] == "reported"
    assert published["gitleaks"]["workflow"] == "Gitleaks Secret Scan"
    assert published["semgrep"]["workflow"] == "Semgrep SAST"


def test_only_known_fields_are_published(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions, zap=record("zap", findings=[{"uri": "http://localhost:5173/private-path"}],
                                  scope=["http://localhost:5173"]))
    assert actions.collect().returncode == 0
    zap = actions.published()["zap"]
    assert "findings" not in zap and "private-path" not in json.dumps(zap)
    assert sorted(zap) == sorted(collector.PUBLISHED_FIELDS)


def test_the_summary_page_names_every_scan_and_the_commit(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    assert actions.collect().returncode == 0
    page = (actions.out / "scan-summary.md").read_text()
    assert page.splitlines()[0] == "# Source scans"
    assert SHA in page
    for name in ("ZAP baseline", "OSV-Scanner", "zizmor", "Semgrep", "gitleaks"):
        assert name in page
    assert "| pass |" in page and "| reported |" in page
    assert "would block" not in page


def test_a_scan_with_no_run_on_the_commit_is_published_as_missing(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    run = actions.collect(today=BEFORE)
    assert run.returncode == 0, "report only before the enforcement date"
    gitleaks = actions.published()["gitleaks"]
    assert gitleaks["outcome"] == "missing" and gitleaks["run_id"] is None and gitleaks["version"] is None
    assert "no completed" in gitleaks["reason"]
    assert gitleaks["commit"] == SHA and gitleaks["counts"] == {} and gitleaks["scope"] == []
    page = (actions.out / "scan-summary.md").read_text()
    assert "would block" in page and ENFORCE in page
    assert "::warning::" in run.stdout


def test_from_the_enforcement_date_a_missing_scan_blocks(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    run = actions.collect(today=AFTER)
    assert run.returncode == 1
    assert "BLOCKED" in (actions.out / "scan-summary.md").read_text()
    assert len(actions.published()) == 5, "the evidence is still written, so the reasons can be read"


def test_from_the_enforcement_date_a_clean_set_passes(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    assert actions.collect(today=AFTER).returncode == 0


def test_a_failed_job_that_left_no_summary_is_missing_and_says_how_the_job_ended(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["ci.yml"] = []
    actions.run("ci.yml", jobs={"Docker Build": ("failure", 1), "OSV Scan": ("success", 1),
                                "Workflow Lint": ("success", 1)},
                artifacts={"scan-summary-osv": [{"record": record("osv")}],
                           "scan-summary-zizmor": [{"record": record("zizmor")}]})
    assert actions.collect().returncode == 0
    zap = actions.published()["zap"]
    assert zap["outcome"] == "missing" and zap["job_conclusion"] == "failure"
    assert "failure" in zap["reason"]


def test_a_recorded_failure_is_published_as_a_failure(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["ci.yml"] = []
    actions.run("ci.yml", jobs={"Docker Build": ("success", 1), "OSV Scan": ("failure", 1),
                                "Workflow Lint": ("success", 1)},
                artifacts={f"scan-summary-{t}": [{"record": record(t, outcome="fail" if t == "osv" else "pass")}]
                           for t in ("zap", "osv", "zizmor")})
    assert actions.collect(today=AFTER).returncode == 1
    assert actions.published()["osv"]["outcome"] == "fail"


@pytest.mark.parametrize("conclusion", ["skipped", "cancelled"])
def test_a_job_that_did_not_run_to_the_end_is_missing(tmp_path, conclusion):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["semgrep.yml"] = []
    actions.run("semgrep.yml", jobs={"Static Analysis": (conclusion, 1)},
                artifacts={"scan-summary-semgrep": [{"record": record("semgrep")}]})
    assert actions.collect().returncode == 0
    semgrep = actions.published()["semgrep"]
    assert semgrep["outcome"] == "missing" and conclusion in semgrep["reason"]


def test_a_pass_from_a_job_that_failed_is_not_believed(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    actions.run("gitleaks.yml", jobs={"Detect Secrets": ("failure", 1)},
                artifacts={"scan-summary-gitleaks": [{"record": record("gitleaks")}]})
    actions.collect()
    gitleaks = actions.published()["gitleaks"]
    assert gitleaks["outcome"] == "error" and "failure" in gitleaks["reason"]


@pytest.mark.parametrize("changes, words", [
    ({"commit": OTHER_SHA}, "commit"),
    ({"run_id": 1}, "run"),
    ({"run_attempt": 2}, "attempt"),
    ({"tool": "zap"}, "not a scan summary"),
    ({"schema": 2}, "not a scan summary"),
    ({"outcome": "great"}, "not a scan summary"),
    ({"counts": {"high": -1}}, "not a scan summary"),
    ({"counts": {"high": "0"}}, "not a scan summary"),
    ({"scope": ["line one\nline two"]}, "not a scan summary"),
    ({"version": "1.0 <script>"}, "not a scan summary"),
])
def test_a_summary_that_does_not_belong_to_this_run_is_an_error(tmp_path, changes, words):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    bad = {**record("gitleaks"), **changes}
    actions.run("gitleaks.yml", jobs={"Detect Secrets": ("success", 1)},
                artifacts={"scan-summary-gitleaks": [{"record": bad}]})
    actions.collect()
    gitleaks = actions.published()["gitleaks"]
    assert gitleaks["outcome"] == "error", gitleaks
    assert words in gitleaks["reason"]


@pytest.mark.parametrize("body", [b"not json", b"[]", b"{}"])
def test_a_summary_that_is_not_json_or_not_a_record_is_an_error(tmp_path, body):
    actions = Actions(tmp_path)
    all_green(actions, zizmor=body)
    actions.collect()
    assert actions.published()["zizmor"]["outcome"] == "error"


def test_an_archive_without_the_record_is_an_error(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    actions.run("gitleaks.yml", jobs={"Detect Secrets": ("success", 1)},
                artifacts={"scan-summary-gitleaks": [{"record": b"{}", "member": "other.json"}]})
    actions.collect()
    assert actions.published()["gitleaks"]["outcome"] == "error"


def test_an_expired_summary_is_missing(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["semgrep.yml"] = []
    actions.run("semgrep.yml", jobs={"Static Analysis": ("success", 1)},
                artifacts={"scan-summary-semgrep": [{"record": record("semgrep"), "expired": True}]})
    actions.collect()
    semgrep = actions.published()["semgrep"]
    assert semgrep["outcome"] == "missing" and "expired" in semgrep["reason"]


def test_pull_request_runs_and_unfinished_runs_do_not_count(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    actions.run("gitleaks.yml", event="pull_request", jobs={"Detect Secrets": ("success", 1)},
                artifacts={"scan-summary-gitleaks": [{"record": record("gitleaks")}]})
    actions.run("gitleaks.yml", status="in_progress", created="2026-11-15T17:00:00Z",
                jobs={"Detect Secrets": ("success", 1)})
    actions.collect()
    assert actions.published()["gitleaks"]["outcome"] == "missing"


def test_the_latest_run_on_the_commit_is_the_one_that_counts(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    run_id = actions.next_id + 1
    actions.run("semgrep.yml", event="workflow_dispatch", created="2026-11-15T18:00:00Z",
                jobs={"Static Analysis": ("success", 1)},
                artifacts={"scan-summary-semgrep": [{"record": record("semgrep", event="workflow_dispatch")}]})
    assert actions.collect().returncode == 0
    semgrep = actions.published()["semgrep"]
    assert semgrep["run_id"] == run_id and semgrep["event"] == "workflow_dispatch"


def test_gitleaks_counts_only_push_runs(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    push_id = actions.next_id
    actions.run("gitleaks.yml", event="workflow_dispatch", created="2026-11-15T18:00:00Z",
                jobs={"Detect Secrets": ("failure", 1)},
                artifacts={"scan-summary-gitleaks": [{"record": record(
                    "gitleaks", event="workflow_dispatch", outcome="fail", scope=["git: the full history"])}]})
    assert actions.collect().returncode == 0
    gitleaks = actions.published()["gitleaks"]
    assert gitleaks["event"] == "push" and gitleaks["outcome"] == "pass"
    assert gitleaks["run_id"] < push_id


def test_gitleaks_with_only_a_manual_run_is_missing(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["gitleaks.yml"] = []
    actions.run("gitleaks.yml", event="workflow_dispatch", jobs={"Detect Secrets": ("success", 1)},
                artifacts={"scan-summary-gitleaks": [{"record": record("gitleaks", event="workflow_dispatch")}]})
    actions.collect()
    gitleaks = actions.published()["gitleaks"]
    assert gitleaks["outcome"] == "missing" and "no completed push run" in gitleaks["reason"]


def test_a_re_run_publishes_the_latest_attempt(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.runs["semgrep.yml"] = []
    actions.run("semgrep.yml", jobs={"Static Analysis": ("success", 2)}, artifacts={"scan-summary-semgrep": [
        {"record": record("semgrep", run_attempt=1), "created": "2026-11-15T16:50:00Z"},
        {"record": record("semgrep", run_attempt=2), "created": "2026-11-15T17:50:00Z"},
    ]})
    assert actions.collect().returncode == 0
    semgrep = actions.published()["semgrep"]
    assert semgrep["outcome"] == "reported" and semgrep["run_attempt"] == 2


@pytest.mark.parametrize("status", [500, 403])
def test_an_actions_api_failure_stops_the_collection_rather_than_guessing(tmp_path, status):
    actions = Actions(tmp_path)
    all_green(actions)
    actions.fail(f"repos/{PRIVATE_REPO}/actions/workflows/semgrep.yml/runs?head_sha={SHA}&per_page=100", status)
    run = actions.collect()
    assert run.returncode == 2
    assert not actions.out.exists() or not list(actions.out.iterdir()), "no partial evidence is left behind"


def test_the_collector_refuses_a_commit_that_is_not_a_full_sha(tmp_path):
    actions = Actions(tmp_path)
    all_green(actions)
    assert actions.collect(sha="main").returncode == 2


# --- the wiring: every scan job writes what the collector looks for ------------------


def _jobs(workflow: str) -> dict:
    return yaml.safe_load((WORKFLOWS / workflow).read_text())["jobs"]


@pytest.mark.parametrize("tool", TOOLS)
def test_each_scan_job_writes_and_uploads_the_summary_the_collector_reads(tool):
    source = collector.TOOLS[tool]
    doc = yaml.safe_load((WORKFLOWS / source["workflow_file"]).read_text())
    assert doc["name"] == source["workflow"]
    [job] = [j for j in doc["jobs"].values() if j.get("name") == source["job"]]
    steps = job["steps"]
    summarise = [s for s in steps if f"scripts/release/scan_summary.py {tool} " in " ".join(s.get("run", "").split())]
    assert len(summarise) == 1, f"{source['job']} must summarise {tool} once"
    upload = [s for s in steps if "actions/upload-artifact@" in s.get("uses", "")
              and s.get("with", {}).get("name") == f"scan-summary-{tool}"]
    assert len(upload) == 1, f"{source['job']} must upload scan-summary-{tool}"
    assert steps.index(summarise[0]) < steps.index(upload[0])
    for step in (summarise[0], upload[0]):
        # The scripts and the summaries are private-only; the public mirror skips both steps.
        assert step["if"].startswith("${{ !cancelled()"), step["name"]
        assert f"github.repository == '{PRIVATE_REPO}'" in step["if"], step["name"]
        assert "continue-on-error" not in step
    assert upload[0]["with"]["path"].endswith(f"scan-{tool}.json")
    assert upload[0]["with"]["if-no-files-found"] == "error"
    assert upload[0]["with"]["overwrite"] is True


def test_the_release_collects_the_summaries_for_the_commit_it_releases():
    job = yaml.safe_load(RELEASE.read_text())["jobs"]["scan"]
    assert job["permissions"] == {"contents": "read", "actions": "read"}
    names = [s.get("name") for s in job["steps"]]
    [step] = [s for s in job["steps"] if "scan_evidence.py" in s.get("run", "")]
    assert step["env"]["SHA"] == "${{ needs.preflight.outputs.sha }}"
    assert step["env"]["ENFORCE_FROM"] == "${{ env.SCAN_GATE_ENFORCE_FROM }}"
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "continue-on-error" not in step and "if" not in step
    assert re.search(r'exit "\$status"\s*$', step["run"]), "the collector's own status is the step's"
    assert names.index(step["name"]) < names.index("Assemble the release evidence")
    sparse = [s for s in job["steps"] if "actions/checkout@" in s.get("uses", "")][0]["with"]["sparse-checkout"]
    assert "scripts/release" in sparse.split()


# --- the collector's output is what the public release accepts ---------------------
# The public oss-release.yml checks release-evidence/ with its own jq rules. Run the
# real collector, put its files into release-evidence/, and run that real check.
release_harness = _load(Path(__file__).with_name("test_oss_release_workflow.py"), "oss_release_harness")


def _stage_collected(tmp_path: Path, actions: Actions) -> subprocess.CompletedProcess:
    evidence = {name: body for name, body in release_harness.good_evidence().items() if not name.startswith("scan-")}
    evidence.update({p.name: p.read_text() for p in actions.out.iterdir()})
    return release_harness.Release(tmp_path / "public", evidence=evidence).stage()


@pytest.mark.parametrize("break_one", ["none", "missing", "fail", "error"])
def test_what_the_collector_writes_the_public_release_accepts(tmp_path, break_one):
    actions = Actions(tmp_path)
    if break_one == "fail":
        all_green(actions, osv=record("osv", outcome="fail"))
    elif break_one == "error":
        all_green(actions, zap=record("zap", outcome="error", version=None, reason="ZAP reported an error"))
    else:
        all_green(actions)
    if break_one == "missing":
        actions.runs["gitleaks.yml"] = []
    assert actions.collect(today=BEFORE).returncode == 0
    run = _stage_collected(tmp_path, actions)
    assert run.returncode == 0, run.stdout + run.stderr
