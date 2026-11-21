# SPDX-License-Identifier: AGPL-3.0-only
"""The image scan gate judges every finding (plan 7.3).

``scripts/release/scan_gate.py`` reads the Grype reports scan-sbom.sh wrote:

- a finding on the CISA Known Exploited Vulnerabilities list blocks at any
  severity, unless it has no fix and a VEX ``not_affected`` statement covers it;
- a Critical or High finding with a fix blocks, even when a VEX statement
  covers it;
- a Critical or High finding with no fix and a VEX ``not_affected`` statement
  is waived;
- a Critical or High finding with no fix and no statement blocks (untriaged),
  unless the acceptance policy is in force. Then it is accepted, or blocks as
  exploit-likely when its EPSS score is at or above the policy's threshold;
- an image with no usable report, or a report that does not show every VEX
  document applied, blocks (missing).

The policy is ``--accept-unfixed-until`` (its review date, the last day it is
in force) with ``--epss-block-at``. Without them nothing is accepted. The
known-exploited rule holds with or without them.

Medium and lower findings, and matches Grype ignores by its own built-in rules,
are counted and never block. Decision D2: before ``--enforce-from`` the gate
reports "would block" and exits 0; from that date it exits 1.
"""

from __future__ import annotations

import datetime

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
SECURITY_MD = REPO_ROOT / "SECURITY.md"
PUBLISHER = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"

pytestmark = pytest.mark.skipif(not GATE.exists(), reason="scripts/release is private-only")

IMAGES = ["scf-backend", "scf-frontend"]
BEFORE, ENFORCE, AFTER = "2026-12-13", "2026-12-14", "2026-12-14"
# The acceptance policy: its review date is the last day it is in force.
REVIEW, PAST_REVIEW, THRESHOLD = "2027-05-21", "2027-05-22", "0.10"
POLICY = {"accept_until": REVIEW, "epss_at": THRESHOLD}
# One entry of Grype 0.121.0's `vulnerability.knownExploited`, as it reports
# CVE-2021-44228; the gate only asks whether the list is empty.
KEV_ENTRY = {"cve": "CVE-2021-44228", "vendorProject": "Apache", "product": "Log4j2", "dateAdded": "2021-12-10",
             "requiredAction": "Apply updates per vendor instructions.", "dueDate": "2021-12-24",
             "knownRansomwareCampaignUse": "Known", "urls": [], "cwes": ["CWE-20"]}


def _days_from_now(days: int) -> str:
    """A date relative to the real today, for the tests that run the workflow's own step."""
    return (datetime.datetime.now(datetime.timezone.utc).date() + datetime.timedelta(days=days)).isoformat()


def match(cve="CVE-2026-1", severity="High", state="not-fixed", versions=(), vex=None, kev=False, epss=None,
          package="openssl"):
    """A Grype match. ``epss``: one score, a list of scores, or None for no ``epss`` key at all."""
    entry = {"vulnerability": {"id": cve, "severity": severity, "fix": {"state": state, "versions": list(versions)}},
             "artifact": {"name": package, "version": "3.0.1"}}
    if kev:
        entry["vulnerability"]["knownExploited"] = [dict(KEV_ENTRY, cve=cve)]
    if epss is not None:
        scores = epss if isinstance(epss, list) else [epss]
        entry["vulnerability"]["epss"] = [{"cve": cve, "epss": s, "percentile": 0.5, "date": "2026-11-20"} for s in scores]
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

    def run(self, today=AFTER, images=" ".join(IMAGES), enforce_from=ENFORCE, accept_until=None, epss_at=None):
        policy = []
        if accept_until is not None:
            policy += ["--accept-unfixed-until", accept_until]
        if epss_at is not None:
            policy += ["--epss-block-at", epss_at]
        proc = subprocess.run([sys.executable, "-I", str(GATE), "--reports", str(self.reports), "--vex", str(self.vex),
                               "--images", images, "--enforce-from", enforce_from, "--out", str(self.out),
                               "--today", today, *policy], capture_output=True, text=True, timeout=60)
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


# -- known to be exploited ---------------------------------------------------------


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"])
@pytest.mark.parametrize("state, versions", [("fixed", ["3.0.2"]), ("not-fixed", []), ("wont-fix", [])])
@pytest.mark.parametrize("policy", [{}, POLICY], ids=["no-policy", "policy"])
def test_a_known_exploited_finding_blocks_at_any_severity(gate, severity, state, versions, policy):
    gate.report("scf-backend", [match(severity=severity, state=state, versions=versions, kev=True, epss=0.0001)])
    proc, summary = gate.run(**policy)
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["known-exploited"]
    assert summary["findings"][0]["known_exploited"] is True


def test_a_statement_sets_aside_a_known_exploited_finding_only_when_it_has_no_fix(gate):
    gate.report("scf-backend", ignored=[match(cve="CVE-2026-1", state="not-fixed", vex="not_affected", kev=True)])
    gate.report("scf-frontend", ignored=[match(cve="CVE-2026-2", state="fixed", versions=["3.0.2"],
                                                vex="not_affected", kev=True)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 1
    assert {f["id"]: f["outcome"] for f in summary["findings"]} == {"CVE-2026-1": "waived", "CVE-2026-2": "known-exploited"}


@pytest.mark.parametrize("severity", ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"])
def test_a_known_exploited_finding_set_aside_by_a_statement_is_listed_as_waived(gate, severity):
    """At any severity, so the summary names it; a Medium one must not vanish into `reported`."""
    gate.report("scf-backend", ignored=[match(cve="CVE-2026-9", severity=severity, state="wont-fix",
                                               vex="not_affected", kev=True)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 0, proc.stdout
    assert gate.outcomes(summary) == ["waived"]
    assert "| scf-backend | CVE-2026-9 |" in (gate.out / "gate-summary.md").read_text()


def test_an_empty_known_exploited_list_is_not_exploited(gate):
    entry = match(severity="Medium")
    entry["vulnerability"]["knownExploited"] = []
    gate.report("scf-backend", [entry])
    proc, summary = gate.run()
    assert proc.returncode == 0
    assert gate.outcomes(summary) == ["reported"]
    assert summary["findings"][0]["known_exploited"] is False


def test_a_match_grype_ignores_by_its_own_rules_stays_ignored_when_exploited(gate):
    gate.report("scf-backend", ignored=[match(severity="Critical", vex="", kev=True)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 0
    assert gate.outcomes(summary) == ["ignored-by-scanner"]


# -- the acceptance policy ---------------------------------------------------------


@pytest.mark.parametrize("state", ["not-fixed", "wont-fix", "unknown"])
@pytest.mark.parametrize("severity", ["Critical", "High"])
@pytest.mark.parametrize("epss", [0.0, 0.0063, 0.0999, [0.01, 0.09]], ids=["zero", "low", "just-under", "two-scores"])
def test_an_unfixed_finding_is_accepted_while_the_policy_is_in_force(gate, state, severity, epss):
    gate.report("scf-backend", [match(severity=severity, state=state, epss=epss)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 0, proc.stdout
    assert gate.outcomes(summary) == ["accepted"]
    assert summary["blocked"] is False
    assert "Image scan gate: PASS" in proc.stdout


@pytest.mark.parametrize("epss", [0.10, 0.1001, 0.97, 1.0, [0.01, 0.5]], ids=["at", "just-over", "high", "one", "two-scores"])
def test_an_unfixed_finding_at_or_above_the_epss_threshold_blocks(gate, epss):
    gate.report("scf-frontend", [match(state="wont-fix", epss=epss)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["exploit-likely"]


def test_a_finding_with_no_score_yet_is_accepted_when_the_report_has_scores(gate):
    """A new CVE has no EPSS score for a day or so; the rest of the report shows the scanner still supplies them."""
    gate.report("scf-backend", [match(cve="CVE-2026-1", state="wont-fix"),
                                match(cve="CVE-2026-2", severity="Low", state="wont-fix", epss=0.0002)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 0, proc.stdout
    assert {f["id"]: f["outcome"] for f in summary["findings"]} == {"CVE-2026-1": "accepted", "CVE-2026-2": "reported"}
    assert "Accepted findings with no EPSS score yet: 1." in (gate.out / "gate-summary.md").read_text()


@pytest.mark.parametrize("shape", ["no-epss-key", "empty-list", "not-numbers"])
def test_a_report_with_no_scores_at_all_cannot_be_accepted_from(gate, shape):
    """The scanner or its database stopped supplying EPSS: the policy has nothing to go on."""
    entries = [match(cve="CVE-2026-1", state="wont-fix"), match(cve="CVE-2026-2", severity="Medium")]
    for entry in entries:
        if shape == "empty-list":
            entry["vulnerability"]["epss"] = []
        elif shape == "not-numbers":
            entry["vulnerability"]["epss"] = [{"epss": "0.01"}, {"percentile": 0.2}]
    gate.report("scf-backend", entries)
    gate.report("scf-frontend", [match(cve="CVE-2026-3", state="wont-fix", epss=0.001)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 1, proc.stdout
    assert [m["image"] for m in summary["missing"]] == ["scf-backend"]
    assert "no EPSS score" in summary["missing"][0]["reason"]
    assert [(f["image"], f["outcome"]) for f in summary["findings"]] == [("scf-frontend", "accepted")]


def test_a_report_with_no_scores_is_judged_as_before_when_nothing_would_be_accepted(gate):
    """No policy, or nothing the policy would accept: the missing scores decide nothing."""
    gate.report("scf-backend", [match(cve="CVE-2026-1", state="wont-fix")])
    gate.report("scf-frontend", [match(cve="CVE-2026-2", severity="Medium"),
                                 match(cve="CVE-2026-3", state="fixed", versions=["3.0.2"])])
    proc, summary = gate.run()
    assert summary["missing"] == []
    assert sorted(gate.outcomes(summary)) == ["fixable", "reported", "untriaged"]
    gate.report("scf-backend", [])
    proc, summary = gate.run(**POLICY)
    assert summary["missing"] == [] and sorted(gate.outcomes(summary)) == ["fixable", "reported"]


def test_the_epss_threshold_is_the_one_given(gate):
    gate.report("scf-frontend", [match(state="wont-fix", epss=0.3)])
    assert gate.outcomes(gate.run(accept_until=REVIEW, epss_at="0.5")[1]) == ["accepted"]
    assert gate.outcomes(gate.run(accept_until=REVIEW, epss_at="0.3")[1]) == ["exploit-likely"]


def test_the_policy_changes_nothing_for_a_finding_that_has_a_fix_or_is_medium(gate):
    gate.report("scf-backend", [match(cve="CVE-2026-1", state="fixed", versions=["3.0.2"], epss=0.0001),
                                match(cve="CVE-2026-2", severity="Medium", state="not-fixed", epss=0.99)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 1
    assert {f["id"]: f["outcome"] for f in summary["findings"]} == {"CVE-2026-1": "fixable", "CVE-2026-2": "reported"}


def test_a_statement_sets_aside_an_exploit_likely_finding(gate):
    gate.report("scf-backend", ignored=[match(state="not-fixed", vex="not_affected", epss=0.9)])
    proc, summary = gate.run(**POLICY)
    assert proc.returncode == 0, proc.stdout
    assert gate.outcomes(summary) == ["waived"]


@pytest.mark.parametrize("today, outcome, code", [(REVIEW, "accepted", 0), (PAST_REVIEW, "untriaged", 1)],
                         ids=["on-the-review-date", "the-day-after"])
def test_the_policy_stops_accepting_the_day_after_its_review_date(gate, today, outcome, code):
    gate.report("scf-backend", [match(state="wont-fix", epss=0.001)])
    proc, summary = gate.run(today=today, **POLICY)
    assert proc.returncode == code, proc.stdout
    assert gate.outcomes(summary) == [outcome]
    assert summary["policy"] == {"accept_unfixed_until": REVIEW, "epss_block_at": 0.1, "in_force": code == 0}
    if code:
        assert f"review date ({REVIEW}) has passed" in proc.stdout
        assert "SCAN_GATE_ACCEPT_UNFIXED_UNTIL" in proc.stdout


def test_without_the_policy_an_unfixed_finding_is_untriaged_whatever_its_score(gate):
    """No flags: the gate as it was before the policy existed."""
    gate.report("scf-backend", [match(state="wont-fix", epss=0.0001)])
    proc, summary = gate.run()
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["untriaged"]
    assert summary["policy"] is None
    assert "No acceptance policy was given" in proc.stdout


@pytest.mark.parametrize(
    "args",
    [{"accept_until": REVIEW}, {"epss_at": THRESHOLD}, {"accept_until": REVIEW, "epss_at": ""},
     {"accept_until": "", "epss_at": THRESHOLD}, {"accept_until": "next year", "epss_at": THRESHOLD},
     {"accept_until": REVIEW, "epss_at": "0"}, {"accept_until": REVIEW, "epss_at": "-0.1"},
     {"accept_until": REVIEW, "epss_at": "1.01"}, {"accept_until": REVIEW, "epss_at": "10"},
     {"accept_until": REVIEW, "epss_at": "10%"}, {"accept_until": REVIEW, "epss_at": "nan"},
     {"accept_until": REVIEW, "epss_at": "inf"}, {"accept_until": "2027-12-16", "epss_at": THRESHOLD},
     {"accept_until": "2099-12-31", "epss_at": THRESHOLD}],
    ids=["date-only", "threshold-only", "empty-threshold", "empty-date", "bad-date", "zero", "negative", "over-one",
         "a-percentage-as-a-number", "a-percent-sign", "nan", "inf", "a-review-date-367-days-away", "a-review-date-never"],
)
def test_an_unclear_policy_is_a_usage_error_not_a_guess(gate, args):
    gate.report("scf-backend", [match(state="wont-fix", epss=0.001)])
    proc, summary = gate.run(**args)
    assert proc.returncode == 2, proc.stdout
    assert summary is None


def test_a_review_date_a_year_away_is_the_furthest_allowed(gate):
    gate.report("scf-backend", [match(state="wont-fix", epss=0.001)])
    proc, summary = gate.run(today=AFTER, accept_until="2027-12-15", epss_at=THRESHOLD)  # 366 days after 2026-12-14
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert gate.outcomes(summary) == ["accepted"]


def test_the_threshold_is_shown_as_given_not_rounded(gate):
    gate.report("scf-backend", [match(state="wont-fix", epss=0.124)])
    proc, summary = gate.run(accept_until=REVIEW, epss_at="0.125")
    assert gate.outcomes(summary) == ["accepted"]
    assert "12.5% or higher" in (gate.out / "gate-summary.md").read_text()


def test_both_policy_values_empty_is_no_policy(gate):
    """An unset workflow value arrives as an empty string; it must not accept anything."""
    gate.report("scf-backend", [match(state="wont-fix", epss=0.001)])
    proc, summary = gate.run(accept_until="", epss_at="")
    assert proc.returncode == 1
    assert gate.outcomes(summary) == ["untriaged"] and summary["policy"] is None


# -- the summary -------------------------------------------------------------------


def test_the_summary_lists_a_vulnerability_once_per_image_with_every_package(gate):
    gate.report("scf-backend", [match(cve="CVE-2026-7", state="wont-fix", epss=0.0063, package=p)
                                for p in ("libmount1", "libblkid1", "util-linux")] +
                               [match(cve="CVE-2026-8", state="fixed", versions=["3.0.2"])])
    gate.report("scf-frontend", [match(cve="CVE-2026-7", state="wont-fix", package="libmount1", epss=0.01)])
    proc, summary = gate.run(**POLICY)
    md = (gate.out / "gate-summary.md").read_text()
    assert "no EPSS score yet" not in md
    assert f"in force until {REVIEW}" in md and "10% or higher" in md
    assert "| accepted | 4 | 1 |" in md and "| fixable | 1 | 1 |" in md
    rows = [line for line in md.splitlines() if "CVE-2026-" in line]
    assert rows == [
        "| scf-backend | CVE-2026-8 | High | openssl 3.0.1 | 3.0.2 | n/a | fixable |",
        "| scf-backend | CVE-2026-7 | High | libblkid1 3.0.1; libmount1 3.0.1; util-linux 3.0.1 | wont-fix | 0.63% | accepted |",
        "| scf-frontend | CVE-2026-7 | High | libmount1 3.0.1 | wont-fix | 1.00% | accepted |",
    ], "what blocks is listed first"
    by_package = {f["package"]: f for f in summary["findings"] if f["image"] == "scf-backend" and f["id"] == "CVE-2026-7"}
    assert by_package["util-linux 3.0.1"]["epss"] == 0.0063 and by_package["util-linux 3.0.1"]["known_exploited"] is False


def test_a_score_that_is_not_a_number_is_no_score(gate):
    entry = match(state="wont-fix")
    entry["vulnerability"]["epss"] = [{"epss": "0.9"}, {"epss": True}, "0.9", {"percentile": 0.9}]
    gate.report("scf-backend", [entry, match(cve="CVE-2026-2", severity="Low", epss=0.0002)])
    proc, summary = gate.run(**POLICY)
    assert summary["findings"][0]["epss"] is None
    assert gate.outcomes(summary) == ["accepted", "reported"]


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


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_acceptance_policy_is_declared_once_and_reaches_the_gate():
    text = WORKFLOW.read_text()
    env = yaml.safe_load(text)["env"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", env["SCAN_GATE_ACCEPT_UNFIXED_UNTIL"])
    assert 0 < float(env["SCAN_GATE_EPSS_BLOCK_AT"]) <= 1
    for name in ("SCAN_GATE_ACCEPT_UNFIXED_UNTIL", "SCAN_GATE_EPSS_BLOCK_AT"):
        assert len(re.findall(rf"^\s*{name}:", text, re.M)) == 1, f"{name} is set in one place"
    step = _step(_scan_job(), "Gate the findings")
    assert step["env"]["ACCEPT_UNFIXED_UNTIL"] == "${{ env.SCAN_GATE_ACCEPT_UNFIXED_UNTIL }}"
    assert step["env"]["EPSS_BLOCK_AT"] == "${{ env.SCAN_GATE_EPSS_BLOCK_AT }}"
    assert '--accept-unfixed-until "$ACCEPT_UNFIXED_UNTIL"' in step["run"]
    assert '--epss-block-at "$EPSS_BLOCK_AT"' in step["run"]


@pytest.mark.skipif(not (WORKFLOW.exists() and SECURITY_MD.exists()), reason="release-oss.yml is private-only")
def test_the_published_policy_states_the_threshold_the_gate_uses():
    threshold = float(yaml.safe_load(WORKFLOW.read_text())["env"]["SCAN_GATE_EPSS_BLOCK_AT"])
    section = SECURITY_MD.read_text().split("## Vulnerabilities in the release images")[1].split("\n## ")[0]
    assert f"EPSS score of {threshold * 100:g}% or higher" in section
    for outcome_word in ("`accepted`", "Known Exploited Vulnerabilities", "`policy`", "`enforcing`"):
        assert outcome_word in section


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_exploit_data_check_runs_between_the_scan_and_the_gate():
    job = _scan_job()
    names = [s.get("name") for s in job["steps"]]
    assert (names.index("Scan the SBOMs") < names.index("Check the scanner reports exploit data")
            < names.index("Gate the findings"))
    step = _step(job, "Check the scanner reports exploit data")
    assert "continue-on-error" not in step and "if" not in step
    assert "check-exploit-data.sh" in step["run"] and "set -euo pipefail" in step["run"]


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
@pytest.mark.parametrize("check_exit", [0, 1, 3])
def test_the_exploit_data_step_fails_when_the_check_fails(tmp_path, check_exit):
    """Run the workflow's own step, as GitHub does (`bash -e`), with a check that exits as told."""
    tools = tmp_path / "workspace" / "release-tools" / "scripts" / "release"
    tools.mkdir(parents=True)
    ran = tmp_path / "ran"
    (tools / "check-exploit-data.sh").write_text(f"#!/bin/sh\ntouch '{ran}'\nexit {check_exit}\n")
    (tools / "check-exploit-data.sh").chmod(0o755)
    (tmp_path / "runner").mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path / "runner")}
    step = _step(_scan_job(), "Check the scanner reports exploit data")
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step["run"]], cwd=tmp_path / "workspace",
                          env=env, capture_output=True, text=True, timeout=60)
    assert ran.exists(), "the step did not run the check"
    assert proc.returncode == check_exit, proc.stdout + proc.stderr


@pytest.mark.skipif(not (WORKFLOW.exists() and SECURITY_MD.exists()), reason="release-oss.yml is private-only")
def test_the_images_take_the_distribution_fixes_the_published_policy_says_they_take():
    """SECURITY.md tells the public each image takes every published fix when it is built."""
    assert "it takes every fix the distribution has published" in SECURITY_MD.read_text()
    backend = (REPO_ROOT / "Dockerfile.backend").read_text()
    frontend = (REPO_ROOT / "Dockerfile.frontend").read_text()
    assert re.search(r"^\s*(RUN\s+)?(DEBIAN_FRONTEND=noninteractive\s+)?apt-get upgrade -y\b", backend, re.M)
    # A bare upgrade of everything, not a named list of packages.
    assert re.search(r"^\s*(RUN\s+)?apk upgrade --no-cache\s*(&&|\\?$)", frontend, re.M)


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
           "IMAGES": " ".join(IMAGES), "ENFORCE_FROM": enforce_from,
           "ACCEPT_UNFIXED_UNTIL": _days_from_now(30), "EPSS_BLOCK_AT": THRESHOLD}
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step["run"]], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert (gate.dir / "summary.md").read_text().startswith("### Image scan gate:")
    assert (runner / "scan" / "gate-summary.json").is_file()


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
@pytest.mark.parametrize("review_in_days, code, outcome", [(30, 0, "accepted"), (-1, 1, "untriaged")],
                         ids=["policy-in-force", "policy-past-review"])
def test_the_gate_step_applies_the_policy_it_is_given(gate, review_in_days, code, outcome):
    """The workflow's own step, with an unfixed High finding and nothing else."""
    work = gate.dir / "workspace"
    tools = work / "release-tools"
    (tools / "scripts" / "release").mkdir(parents=True)
    (tools / "scripts" / "release" / "scan_gate.py").write_text(GATE.read_text())
    (tools / "security").mkdir()
    gate.vex.rename(tools / "security" / "vex")
    runner = gate.dir / "runner"
    runner.mkdir()
    gate.reports.rename(runner / "scan")
    doc = json.loads((runner / "scan" / "scf-backend.grype.json").read_text())
    doc["matches"] = [match(state="wont-fix", epss=0.0063)]
    (runner / "scan" / "scf-backend.grype.json").write_text(json.dumps(doc))
    step = _step(_scan_job(), "Gate the findings")
    env = {"PATH": f"{Path(sys.executable).parent}{os.pathsep}/usr/bin:/bin", "HOME": str(gate.dir),
           "RUNNER_TEMP": str(runner), "GITHUB_STEP_SUMMARY": str(gate.dir / "summary.md"),
           "IMAGES": " ".join(IMAGES), "ENFORCE_FROM": "2000-01-01",
           "ACCEPT_UNFIXED_UNTIL": _days_from_now(review_in_days), "EPSS_BLOCK_AT": THRESHOLD}
    proc = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step["run"]], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == code, proc.stdout + proc.stderr
    summary = json.loads((runner / "scan" / "gate-summary.json").read_text())
    assert [f["outcome"] for f in summary["findings"]] == [outcome]
