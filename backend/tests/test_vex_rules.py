"""Every VEX waiver is written, owned and in date (plan 7.4).

``scripts/release/check_vex.py`` checks every ``not_affected`` statement in
``security/vex/``: the document names an author, the statement names its
vulnerability and products, carries an OpenVEX justification, the
maintainer's impact statement, a timestamp with a time zone, and a
``review_by`` date after it that has not passed.

This file is also the CI check: ``test_the_committed_vex_documents_follow_the_rules``
runs the script on the real directory, so an expired or unowned statement
fails the backend tests on every pull request. The release scan job runs the
same script before it scans.

The statements here are test fixtures with placeholder text. Real
justifications are written by the maintainer, never generated.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
CHECK = REPO_ROOT / "scripts" / "release" / "check_vex.py"
VEX = REPO_ROOT / "security" / "vex"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"

pytestmark = pytest.mark.skipif(not CHECK.exists(), reason="scripts/release is private-only")

TODAY = "2026-11-20"
STATEMENT = {
    "vulnerability": {"name": "CVE-2026-0001"},
    "products": [{"@id": "pkg:deb/debian/openssl@3.0.1"}],
    "status": "not_affected",
    "justification": "vulnerable_code_not_in_execute_path",
    "impact_statement": "test fixture text",
    "timestamp": "2026-11-14T10:00:00Z",
    "review_by": "2027-02-14",
}
DOC = {"@context": "https://openvex.dev/ns/v0.2.0", "@id": "https://example.invalid/vex/1", "author": "Test Maintainer",
       "timestamp": "2026-11-14T10:00:00Z", "version": 1, "statements": [STATEMENT]}


def run(vex_dir: Path, today: str = TODAY):
    args = [sys.executable, "-I", str(CHECK), str(vex_dir)] + (["--today", today] if today else [])
    return subprocess.run(args, capture_output=True, text=True, timeout=60)


def write(tmp_path: Path, doc) -> Path:
    vex = tmp_path / "vex"
    vex.mkdir(exist_ok=True)
    (vex / "doc.json").write_text(doc if isinstance(doc, str) else json.dumps(doc))
    return vex


def test_the_committed_vex_documents_follow_the_rules():
    proc = run(VEX, today="")
    assert proc.returncode == 0, proc.stderr


def test_a_complete_statement_in_date_passes(tmp_path):
    proc = run(write(tmp_path, DOC))
    assert proc.returncode == 0, proc.stderr
    assert "1 not_affected statements checked" in proc.stdout


def test_an_empty_directory_passes(tmp_path):
    (tmp_path / "vex").mkdir()
    assert run(tmp_path / "vex").returncode == 0


@pytest.mark.parametrize("status", ["affected", "fixed", "under_investigation"])
def test_only_not_affected_statements_are_held_to_the_waiver_rules(tmp_path, status):
    doc = copy.deepcopy(DOC)
    doc["statements"] = [{"vulnerability": {"name": "CVE-2026-0002"}, "status": status}]
    assert run(write(tmp_path, doc)).returncode == 0


def _broken(change):
    doc = copy.deepcopy(DOC)
    change(doc, doc["statements"][0])
    return doc


@pytest.mark.parametrize(
    "change, message",
    [
        (lambda d, s: d.pop("author"), "has no author"),
        (lambda d, s: d.update(author="  "), "has no author"),
        (lambda d, s: s.pop("vulnerability"), "no vulnerability.name"),
        (lambda d, s: s.update(products=[]), "no products"),
        (lambda d, s: s.pop("justification"), "justification is not one of"),
        (lambda d, s: s.update(justification="we_checked"), "justification is not one of"),
        (lambda d, s: s.pop("impact_statement"), "no impact_statement"),
        (lambda d, s: s.update(impact_statement=""), "no impact_statement"),
        (lambda d, s: s.pop("timestamp"), "no timestamp"),
        (lambda d, s: s.update(timestamp="2026-11-14T10:00:00"), "no timestamp with a time zone"),
        (lambda d, s: s.update(timestamp="2026-11-21T10:00:00Z", review_by="2027-01-01"), "in the future"),
        (lambda d, s: s.pop("review_by"), "no review_by"),
        (lambda d, s: s.update(review_by="next year"), "no review_by"),
        (lambda d, s: s.update(review_by="2026-11-14"), "is not after the timestamp"),
        (lambda d, s: s.update(review_by="2026-11-19"), "expired"),
    ],
    ids=["no-author", "blank-author", "no-vulnerability", "no-products", "no-justification", "bad-justification",
         "no-impact", "blank-impact", "no-timestamp", "naive-timestamp", "future-timestamp", "no-review-by",
         "bad-review-by", "review-by-not-after", "expired"],
)
def test_a_statement_that_breaks_a_rule_fails(tmp_path, change, message):
    proc = run(write(tmp_path, _broken(change)))
    assert proc.returncode == 1
    assert message in proc.stderr
    assert "doc.json statement 0" in proc.stderr, "the failure names the file and statement"


def test_the_review_by_day_itself_is_still_in_date(tmp_path):
    doc = _broken(lambda d, s: s.update(review_by=TODAY))
    assert run(write(tmp_path, doc)).returncode == 0
    assert run(write(tmp_path, doc), today="2026-11-21").returncode == 1


@pytest.mark.parametrize("doc, message", [("{", "not readable JSON"), ({"statements": []}, "not an OpenVEX document"),
                                          ({"@context": "https://openvex.dev/ns/v0.2.0"}, "no statements list"),
                                          ({**DOC, "statements": ["x"]}, "not an object")])
def test_a_file_that_is_not_a_usable_openvex_document_fails(tmp_path, doc, message):
    proc = run(write(tmp_path, doc))
    assert proc.returncode == 1
    assert message in proc.stderr


def test_one_bad_statement_among_good_ones_fails(tmp_path):
    doc = copy.deepcopy(DOC)
    doc["statements"].append({**STATEMENT, "vulnerability": {"name": "CVE-2026-0003"}, "review_by": "2026-01-01"})
    proc = run(write(tmp_path, doc))
    assert proc.returncode == 1
    assert "CVE-2026-0003" in proc.stderr and "CVE-2026-0001" not in proc.stderr


@pytest.mark.parametrize("args", [["/nonexistent/vex"], [None, "--today", "soon"]])
def test_unclear_arguments_are_a_usage_error(tmp_path, args):
    (tmp_path / "vex").mkdir()
    args = [str(tmp_path / "vex") if a is None else a for a in args]
    proc = subprocess.run([sys.executable, "-I", str(CHECK), *args], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 2


@pytest.mark.skipif(not WORKFLOW.exists(), reason="release-oss.yml is private-only")
def test_the_release_scan_checks_the_vex_rules_before_it_scans():
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["scan"]["steps"]
    names = [s.get("name") for s in steps]
    assert names.index("Check the VEX statements") < names.index("Scan the SBOMs")
    [step] = [s for s in steps if s.get("name") == "Check the VEX statements"]
    assert step["run"].split() == ["python3", "release-tools/scripts/release/check_vex.py", "release-tools/security/vex"]
    assert "continue-on-error" not in step and "if" not in step
