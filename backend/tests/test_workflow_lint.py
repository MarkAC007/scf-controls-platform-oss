# SPDX-License-Identifier: AGPL-3.0-only
"""The workflow lint stays wired the way it was introduced.

``ci.yml`` runs zizmor over every workflow in the repository and fails on any
finding. That only holds while the job itself is not weakened. These tests
keep four things true:

* the job runs on every trigger and cannot be skipped or allowed to fail;
* zizmor is installed from the hash-locked requirements file and nowhere else;
* it runs offline, and a workflow it cannot parse fails the job;
* a finding is excused only by a comment on the line, and the comment says why.

The lint itself is not run here: zizmor is not a backend dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
REQUIREMENTS = REPO_ROOT / ".github" / "requirements" / "zizmor.txt"
CONFIG = REPO_ROOT / ".github" / "zizmor.yml"
JOB = "workflow-lint"

WAIVER = re.compile(r"zizmor:\s*ignore\[")
# `# zizmor: ignore[rule,other-rule] ` followed by the reason.
WAIVER_WITH_REASON = re.compile(r"# zizmor: ignore\[[a-z0-9-]+(?:,[a-z0-9-]+)*\] (?P<reason>\S.*)$")
MINIMUM_REASON_LENGTH = 20

pytestmark = pytest.mark.skipif(not CI.exists(), reason="ci.yml is not in this tree")


def _job() -> dict:
    jobs = yaml.safe_load(CI.read_text())["jobs"]
    assert JOB in jobs, f"ci.yml has no `{JOB}` job"
    return jobs[JOB]


LINT_STEP = "Lint the workflows"
# After the lint, and only after it, the job records its result for the release
# evidence (scripts/release/scan_evidence.py). Those steps never decide the job.
EVIDENCE_STEPS = ["Summarise the lint for the release evidence", "Upload the scan summary"]


def _gating_steps() -> list[dict]:
    """The steps up to and including the lint."""
    steps = _job()["steps"]
    names = [step.get("name") for step in steps]
    assert LINT_STEP in names, f"the job has no `{LINT_STEP}` step"
    return steps[: names.index(LINT_STEP) + 1]


def _run_lines(steps: list[dict] | None = None) -> list[str]:
    """Every shell line the steps run, with line continuations joined."""
    lines = []
    for step in _job()["steps"] if steps is None else steps:
        script = step.get("run", "").replace("\\\n", " ")
        lines += [" ".join(line.split()) for line in script.splitlines() if line.strip()]
    return lines


def _lint_commands() -> list[str]:
    return [line for line in _run_lines(_gating_steps()) if line.split()[0] == "zizmor"]


def test_the_lint_runs_on_every_trigger_and_can_fail():
    job = _job()
    # A job with `if:` or `needs:` can be skipped, and a skipped job satisfies
    # a required check.
    assert "if" not in job, "the lint job must not be conditional"
    assert "needs" not in job, "the lint job must not wait on another job"
    assert "continue-on-error" not in job
    for step in _gating_steps():
        assert "continue-on-error" not in step, step.get("name")
        assert "if" not in step, step.get("name")
    after = [step.get("name") for step in job["steps"][len(_gating_steps()):]]
    assert after == EVIDENCE_STEPS, "nothing but the evidence steps may follow the lint"
    for step in job["steps"][len(_gating_steps()):]:
        assert "continue-on-error" not in step, step.get("name")


def test_the_evidence_pass_reads_the_same_inputs_the_same_way():
    [step] = [step for step in _job()["steps"] if step.get("name") == EVIDENCE_STEPS[0]]
    lines = [" ".join(line.split()) for line in step["run"].replace("\\\n", " ").splitlines()]
    [json_pass] = [line for line in lines if line.startswith("zizmor ") and "--format json" in line]
    [lint] = _lint_commands()
    # The same command, with only the output format added.
    assert json_pass.split(" >")[0].replace(" --format json", "").split("||")[0].strip() == lint
    assert step["env"]["LINT_OUTCOME"] == "${{ steps.lint.outcome }}"


def test_zizmor_is_installed_only_from_the_hash_locked_file():
    installs = [line for line in _run_lines() if "pip install" in line]
    assert installs, "the job no longer installs zizmor"
    for line in installs:
        assert "--require-hashes" in line, line
        assert "--only-binary :all:" in line, line
        assert "-r .github/requirements/zizmor.txt" in line, line
        # Nothing named on the command line: a package given there is not
        # covered by the hashes in the file.
        assert not re.search(r"pip install .*\bzizmor(==|\s|$)", line), line


def test_the_lint_runs_offline_and_strict_over_the_whole_repository():
    commands = _lint_commands()
    assert len(commands) == 1, f"expected one zizmor command, found {commands}"
    words = commands[0].split()
    assert "--offline" in words, "online audits need a token and change with the network"
    assert "--strict-collection" in words, "an unparseable workflow would be skipped, not failed"
    assert words[-1] == ".", "the lint must cover the whole repository"
    # Flags that turn findings off or down.
    for flag in ("--min-severity", "--min-confidence", "--no-exit-codes", "--no-config", "--fix"):
        assert not any(word.startswith(flag) for word in words), flag


def test_the_requirements_file_locks_one_version_by_hash():
    text = REQUIREMENTS.read_text()
    requirement = " ".join(
        line.rstrip("\\").strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )
    words = requirement.split()
    assert re.fullmatch(r"zizmor==\d+\.\d+\.\d+", words[0]), words[0]
    hashes = words[1:]
    assert hashes, "no hashes: pip would refuse the install"
    for entry in hashes:
        assert re.fullmatch(r"--hash=sha256:[0-9a-f]{64}", entry), entry
    assert len(set(hashes)) == len(hashes), "the same hash is listed twice"


def test_every_action_must_be_pinned_by_commit():
    rules = yaml.safe_load(CONFIG.read_text())["rules"]
    assert rules["unpinned-uses"]["config"]["policies"] == {"*": "hash-pin"}


def test_no_rule_is_switched_off_or_waived_in_the_config_file():
    # A waiver in the config file has no room for a reason and stays behind
    # when the line it excused is gone. Waivers are comments on the line.
    for name, rule in yaml.safe_load(CONFIG.read_text())["rules"].items():
        assert not rule.get("disable"), f"{name} is disabled"
        assert "ignore" not in rule, f"{name} is waived in the config file"


def _waivers() -> list[tuple[str, str]]:
    found = []
    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        for number, line in enumerate(workflow.read_text().splitlines(), start=1):
            if WAIVER.search(line):
                found.append((f"{workflow.name}:{number}", line))
    return found


def test_every_waiver_says_why():
    unexplained = []
    for where, line in _waivers():
        match = WAIVER_WITH_REASON.search(line)
        if not match or len(match.group("reason").strip()) < MINIMUM_REASON_LENGTH:
            unexplained.append(f"{where}: {line.strip()}")
    assert not unexplained, "waiver without a reason:\n" + "\n".join(unexplained)


def test_the_waiver_scan_finds_the_waivers_it_is_meant_to_check():
    # Guards the guard: ci.yml carries waivers of its own, so a scan that
    # finds none has stopped reading the files.
    assert any(where.startswith("ci.yml:") for where, _ in _waivers())
