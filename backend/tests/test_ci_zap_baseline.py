"""The ZAP baseline scan can fail, and only a written disposition quiets it (plan 8.2).

The Docker Build job boots the stack and runs a passive ZAP baseline scan of
the frontend. Every alert fails the job unless ``.github/zap/rules.tsv`` sets
its rule to IGNORE, and an IGNORE line needs a ``# disposition:`` comment
directly above it: the maintainer's reason, never generated. The rules for the
security headers can never be ignored, so removing a header fails the scan.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RULES = REPO_ROOT / ".github" / "zap" / "rules.tsv"

pytestmark = pytest.mark.skipif(not CI.exists(), reason="ci.yml is not in this tree")

# ZAP passive rules for the headers scripts/verify-prod-build.sh also demands.
HEADER_RULES = {
    "10020": "Missing Anti-clickjacking Header",
    "10021": "X-Content-Type-Options Header Missing",
    "10038": "Content Security Policy (CSP) Header Not Set",
    "10035": "Strict-Transport-Security Header",
    "10063": "Permissions Policy Header Not Set",
}


def parse_rules(text: str) -> list[dict]:
    """Each rule line, with the comment line directly above it (or None)."""
    rules, previous = [], None
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            previous = None
            continue
        if line.startswith("#"):
            previous = line
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not re.fullmatch(r"\d+", fields[0]) or fields[1] not in ("IGNORE", "WARN", "FAIL"):
            raise ValueError(f"line {number} is not '<id>\\t<IGNORE|WARN|FAIL>\\t<name>': {line!r}")
        rules.append({"id": fields[0], "action": fields[1], "name": fields[2], "comment": previous, "line": number})
        previous = None
    return rules


def check_rules(text: str) -> list[str]:
    try:
        rules = parse_rules(text)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    ids = [r["id"] for r in rules]
    problems += [f"rule {i} is listed more than once" for i in sorted({i for i in ids if ids.count(i) > 1})]
    for r in rules:
        if r["action"] != "IGNORE":
            continue
        if r["id"] in HEADER_RULES:
            problems.append(f"rule {r['id']} ({HEADER_RULES[r['id']]}) is a security header and cannot be ignored")
        comment = r["comment"] or ""
        if not comment.startswith("# disposition:") or not comment[len("# disposition:"):].strip():
            problems.append(f"line {r['line']}: IGNORE of rule {r['id']} has no '# disposition:' comment directly above it")
    return problems


def _docker_steps() -> list[dict]:
    return yaml.safe_load(CI.read_text())["jobs"]["docker"]["steps"]


def _step(name: str) -> dict:
    [step] = [s for s in _docker_steps() if s.get("name") == name]
    return step


# -- the committed rules file ------------------------------------------------------


def test_the_committed_rules_file_follows_the_rules():
    assert check_rules(RULES.read_text()) == []


# -- the checker itself --------------------------------------------------------------

GOOD = "# disposition: accepted by the maintainer because ...\n10049\tIGNORE\tStorable and Cacheable Content\n"


def test_a_dispositioned_ignore_passes():
    assert check_rules(GOOD) == []


@pytest.mark.parametrize(
    "text, message",
    [
        ("10049\tIGNORE\tStorable and Cacheable Content\n", "no '# disposition:' comment"),
        ("# disposition:   \n10049\tIGNORE\tx\n", "no '# disposition:' comment"),
        ("# some other note\n10049\tIGNORE\tx\n", "no '# disposition:' comment"),
        ("# disposition: ok\n\n10049\tIGNORE\tx\n", "no '# disposition:' comment"),
        ("# disposition: the header is set by the proxy\n10038\tIGNORE\tCSP\n", "security header and cannot be ignored"),
        ("10049\tWARN\tx\n10049\tFAIL\tx\n", "listed more than once"),
        ("10049 IGNORE x\n", "is not '<id>"),
        ("10049\tSKIP\tx\n", "is not '<id>"),
    ],
    ids=["no-comment", "blank-disposition", "other-comment", "comment-not-adjacent", "header-rule", "duplicate",
         "spaces-not-tabs", "bad-action"],
)
def test_a_rules_file_that_breaks_a_rule_is_refused(text, message):
    problems = check_rules(text)
    assert problems and any(message in p for p in problems), problems


@pytest.mark.parametrize("rule", sorted(HEADER_RULES))
def test_no_security_header_rule_can_be_ignored(rule):
    assert check_rules(f"# disposition: x\n{rule}\tIGNORE\tx\n")


# -- the workflow --------------------------------------------------------------------


def test_the_scan_runs_after_the_boot_test_and_before_tear_down():
    names = [s.get("name") for s in _docker_steps()]
    boot = names.index("Boot the stack and assert it is healthy")
    assert boot < names.index("ZAP baseline scan") < names.index("Tear down the stack")


def test_the_scan_can_fail():
    step = _step("ZAP baseline scan")
    run = step["run"]
    assert "continue-on-error" not in step
    assert "set -euo pipefail" in run
    assert "zap-baseline.py" in run and "-c rules.tsv" in run and "cp .github/zap/rules.tsv" in run
    assert not re.search(r"\s-I(\s|$)", run), "-I would let warnings pass"
    assert re.search(r'exit "\$status"\s*$', run), "the scan's own exit status is the step's"


def test_a_scan_with_no_report_fails():
    run = _step("ZAP baseline scan")["run"]
    assert "-J zap-report.json" in run
    assert re.search(r'if \[ ! -s "\$out/zap-report\.json" \]; then\n\s*echo "::error::[^\n]*"\n\s*exit 1', run)


def test_the_zap_image_is_pinned_by_digest():
    image = _step("ZAP baseline scan")["env"]["ZAP_IMAGE"]
    assert re.fullmatch(r"ghcr\.io/zaproxy/zaproxy:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", image)
    assert '"$ZAP_IMAGE"' in _step("ZAP baseline scan")["run"]


def test_the_report_is_uploaded_even_when_the_scan_fails():
    upload = _step("Upload the ZAP report")
    assert upload["if"].startswith("${{ !cancelled()")
    assert upload["with"]["path"] == "${{ runner.temp }}/zap/zap-report.*"


def test_the_scan_status_is_handed_to_the_release_evidence():
    # scripts/release/scan_summary.py reads 0/1/2/3 to tell a pass from a fail
    # from a ZAP error; the step's success or failure alone cannot.
    step = _step("ZAP baseline scan")
    assert step["id"] == "zap"
    run = step["run"]
    assert run.index('echo "status=${status}" >> "$GITHUB_OUTPUT"') < run.index('if [ ! -s "$out/zap-report.json" ]')
    summary = _step("Summarise the ZAP scan for the release evidence")
    assert summary["env"]["ZAP_STATUS"] == "${{ steps.zap.outputs.status }}"
    names = [s.get("name") for s in _docker_steps()]
    assert names.index("Upload the ZAP report") < names.index("Summarise the ZAP scan for the release evidence") \
        < names.index("Tear down the stack")
