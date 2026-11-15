"""The vulnerability scan blocks what it should, and cannot pass by not running.

``scripts/osv-scan.sh`` runs OSV-Scanner over the lockfiles in a container and
hands the result to ``scripts/osv_policy.py``, which decides. These tests hold
the two files, and the places that call them, to the rules they came with:

* a high or critical finding with a fixed version blocks, and so does a finding
  nobody has rated; anything else is reported;
* a waiver needs an id, a reason and a date, and blocks once the date passes;
* a scan that did not run, or did not cover every lockfile, is a failure;
* the scanner image is one exact digest and is given only the lockfiles;
* every lockfile the product installs from is scanned, and the docs site's
  bun.lock pins what its scanned package-lock.json pins;
* a tree the public snapshot removes is skipped only when it is absent whole;
* the CI job, the nightly run and the release step exist and cannot be skipped.

Nothing here uses the network or Docker. The script is run against a stand-in
``docker`` that replays canned scanner output.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytest.importorskip("tomllib", reason="scripts/osv_policy.py needs Python 3.11 or later")

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = REPO_ROOT / "scripts" / "osv-scan.sh"
POLICY_SCRIPT = REPO_ROOT / "scripts" / "osv_policy.py"
WAIVER_FILE = REPO_ROOT / "osv-scanner.toml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CI = WORKFLOWS / "ci.yml"
NIGHTLY = WORKFLOWS / "osv-nightly.yml"
RELEASE = WORKFLOWS / "release-oss.yml"
PREPARE_OSS = REPO_ROOT / "scripts" / "prepare-oss.sh"
TOPLEVEL_ALLOW = REPO_ROOT / "scripts" / "oss-toplevel-allow.txt"

# The backend image carries backend/ alone; there is nothing to check there.
pytestmark = pytest.mark.skipif(
    not (SCAN_SCRIPT.exists() and CI.exists()), reason="the repository root is not in this tree"
)
# The nightly workflow, the release workflow and the snapshot builder stay in
# the private repository. Where the snapshot builder is present, so must they be.
private_only = pytest.mark.skipif(not PREPARE_OSS.exists(), reason="private-repository files are not in this tree")

TODAY = dt.date(2026, 11, 13)
LOCKFILES = [
    "backend/requirements.txt",
    "backend/requirements-dev.txt",
    "scripts/requirements-importer.txt",
    "webclient/package-lock.json",
    "docs-site/package-lock.json",
]
# Trees scripts/prepare-oss.sh removes from the public snapshot, whole. The scan
# skips a lockfile under one of these when the tree is absent, and only then.
PRIVATE_TREES = ["docs-site"]
SKIPPABLE = [lockfile for lockfile in LOCKFILES if lockfile.split("/", 1)[0] in PRIVATE_TREES]
ALWAYS = [lockfile for lockfile in LOCKFILES if lockfile not in SKIPPABLE]


def _load_policy():
    spec = importlib.util.spec_from_file_location("osv_policy", POLICY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


policy = _load_policy() if POLICY_SCRIPT.exists() else None


# --- Building scanner output ----------------------------------------------------


def _vuln(identifier, *, label=None, fixed=("2.0.0",), aliases=(), package="demo", range_type="ECOSYSTEM"):
    events = [{"introduced": "0"}] + [{"fixed": version} for version in fixed]
    entry = {
        "id": identifier,
        "aliases": list(aliases),
        "affected": [{"package": {"name": package}, "ranges": [{"type": range_type, "events": events}]}],
    }
    if label:
        entry["database_specific"] = {"severity": label}
    return entry


def _package(*vulns, score="", name="demo", version="1.0.0"):
    """One package with one finding group made of ``vulns`` (none: a clean package)."""
    entry = {"package": {"name": name, "version": version, "ecosystem": "PyPI"}}
    if vulns:
        entry["vulnerabilities"] = list(vulns)
        ids = [vuln["id"] for vuln in vulns]
        entry["groups"] = [{"ids": ids, "aliases": ids, "max_severity": score}]
    return entry


def _output(*packages, lockfiles=LOCKFILES, prefix=""):
    """Scanner output: ``packages`` in the first lockfile, one clean package in each of the rest."""
    results = []
    for index, lockfile in enumerate(lockfiles):
        found = list(packages) if index == 0 else []
        results.append({"source": {"path": prefix + lockfile, "type": "lockfile"}, "packages": found or [_package()]})
    return {"results": results}


def _waivers(*entries):
    lines = []
    for identifier, until, reason in entries:
        lines += ["[[IgnoredVulns]]", f'id = "{identifier}"', f"ignoreUntil = {until}", f'reason = "{reason}"', ""]
    return "\n".join(lines)


def _run(tmp_path, output, waivers="", expect=LOCKFILES, extra=()):
    results = tmp_path / "results.json"
    if output is not None:
        results.write_text(output if isinstance(output, str) else json.dumps(output))
    config = tmp_path / "osv-scanner.toml"
    if waivers is not None:
        config.write_text(waivers)
    args = ["--results", str(results), "--waivers", str(config), "--today", TODAY.isoformat(), *extra]
    for lockfile in expect:
        args += ["--expect", lockfile]
    return policy.main(args)


REASON = "The fixed release drops Python 3.11; moving with the image upgrade."


# --- What blocks ----------------------------------------------------------------


def test_a_clean_scan_passes(tmp_path, capsys):
    assert _run(tmp_path, _output()) == 0
    assert "Nothing blocks." in capsys.readouterr().out


@pytest.mark.parametrize("score", ["7.0", "8.8", "9.0", "10.0"])
def test_a_high_or_critical_finding_with_a_fix_blocks(tmp_path, capsys, score):
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa"), score=score))) == 1
    out = capsys.readouterr().out
    assert "### Blocking: 1" in out and "GHSA-aaaa" in out and "2.0.0" in out


@pytest.mark.parametrize("score", ["0.1", "3.9", "4.0", "6.9"])
def test_a_low_or_medium_finding_is_reported_and_does_not_block(tmp_path, capsys, score):
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa"), score=score))) == 0
    assert "### Reported, not blocking: 1" in capsys.readouterr().out


def test_a_high_finding_with_no_fix_is_reported_and_does_not_block(tmp_path, capsys):
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa", fixed=()), score="9.8"))) == 0
    assert "critical (9.8) | none" in capsys.readouterr().out


def test_a_fix_for_another_package_or_a_commit_is_not_a_fix(tmp_path):
    other = _vuln("GHSA-aaaa", package="something-else")
    commit = _vuln("GHSA-bbbb", range_type="GIT", package="demo2")
    assert _run(tmp_path, _output(_package(other, score="9.8"), _package(commit, score="9.8", name="demo2"))) == 0


def test_a_finding_with_no_rating_at_all_blocks_when_a_fix_exists(tmp_path, capsys):
    assert _run(tmp_path, _output(_package(_vuln("PYSEC-2026-1"), score=""))) == 1
    assert "unrated" in capsys.readouterr().out


@pytest.mark.parametrize("label, status", [("CRITICAL", 1), ("HIGH", 1), ("MODERATE", 0), ("LOW", 0)])
def test_the_advisory_label_counts_when_there_is_no_score(tmp_path, label, status):
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa", label=label), score=""))) == status


def test_the_worse_of_the_score_and_the_label_wins(tmp_path):
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa", label="HIGH"), score="5.0"))) == 1
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa", label="LOW"), score="9.1"))) == 1


def test_a_finding_in_any_lockfile_blocks(tmp_path):
    output = _output()
    output["results"][-1]["packages"] = [_package(_vuln("GHSA-aaaa"), score="9.8")]
    assert _run(tmp_path, output) == 1


# --- Waivers --------------------------------------------------------------------


def test_a_waiver_in_date_lets_the_finding_through_and_says_so(tmp_path, capsys):
    output = _output(_package(_vuln("GHSA-aaaa"), score="9.8"))
    assert _run(tmp_path, output, _waivers(("GHSA-aaaa", "2026-12-31", REASON))) == 0
    out = capsys.readouterr().out
    assert "### Waived: 1" in out and REASON in out


def test_a_waiver_matches_an_alias_of_the_finding(tmp_path):
    output = _output(_package(_vuln("GHSA-aaaa", aliases=["CVE-2026-0001"]), score="9.8"))
    assert _run(tmp_path, output, _waivers(("CVE-2026-0001", "2026-12-31", REASON))) == 0


def test_a_waiver_covers_only_the_finding_it_names(tmp_path):
    other = _package(_vuln("GHSA-bbbb", package="demo2"), score="9.8", name="demo2")
    output = _output(_package(_vuln("GHSA-aaaa"), score="9.8"), other)
    assert _run(tmp_path, output, _waivers(("GHSA-aaaa", "2026-12-31", REASON))) == 1


def test_a_waiver_holds_on_its_last_day_and_blocks_the_day_after(tmp_path, capsys):
    output = _output(_package(_vuln("GHSA-aaaa"), score="9.8"))
    assert _run(tmp_path, output, _waivers(("GHSA-aaaa", TODAY.isoformat(), REASON))) == 0
    capsys.readouterr()
    yesterday = (TODAY - dt.timedelta(days=1)).isoformat()
    assert _run(tmp_path, output, _waivers(("GHSA-aaaa", yesterday, REASON))) == 1
    out = capsys.readouterr().out
    # Both are named: the waiver that ran out, and the finding it no longer covers.
    assert "past their date" in out and "### Blocking: 1 high or critical" in out and "### Waived" not in out


def test_an_expired_waiver_blocks_even_when_its_finding_is_gone(tmp_path):
    assert _run(tmp_path, _output(), _waivers(("GHSA-gone", "2026-01-01", REASON))) == 1


def test_a_waiver_that_matches_nothing_is_reported_and_does_not_block(tmp_path, capsys):
    assert _run(tmp_path, _output(), _waivers(("GHSA-gone", "2026-12-31", REASON))) == 0
    assert "match nothing" in capsys.readouterr().out


@pytest.mark.parametrize(
    "toml",
    [
        '[[IgnoredVulns]]\nid = "GHSA-aaaa"\nreason = "%s"\n' % REASON,  # no date
        '[[IgnoredVulns]]\nid = "GHSA-aaaa"\nignoreUntil = "2026-12-31"\nreason = "%s"\n' % REASON,  # date as text
        '[[IgnoredVulns]]\nid = "GHSA-aaaa"\nignoreUntil = 2026-12-31\n',  # no reason
        '[[IgnoredVulns]]\nid = "GHSA-aaaa"\nignoreUntil = 2026-12-31\nreason = "ok"\n',  # a reason that says nothing
        '[[IgnoredVulns]]\nignoreUntil = 2026-12-31\nreason = "%s"\n' % REASON,  # no id
        '[[IgnoredVulns]]\nid = "GHSA-aaaa"\nignoreUntil = 2026-12-31\nreason = "%s"\nforever = true\n' % REASON,
        _waivers(("GHSA-aaaa", "2026-12-31", REASON), ("GHSA-aaaa", "2027-12-31", REASON)),  # the same id twice
        '[[PackageOverrides]]\nname = "demo"\nignore = true\n',  # a scanner setting this policy does not apply
        "GoVersionOverride = '1.22'\n",
        "[[IgnoredVulns]\n",  # not TOML
    ],
)
def test_a_waiver_file_that_is_not_understood_fails_the_scan(tmp_path, toml):
    # Even with nothing to waive: a rule that looks like it applies and does not is worse than none.
    assert _run(tmp_path, _output(), toml) == 2


def test_a_missing_waiver_file_fails_the_scan(tmp_path):
    assert _run(tmp_path, _output(), waivers=None) == 2


def test_the_committed_waiver_file_is_one_the_policy_reads():
    # Whether a committed waiver is still in date is the scan's job, not this test's:
    # a date passing should fail the "OSV Scan" job, not the unit tests.
    for waiver in policy.load_waivers(WAIVER_FILE):
        assert waiver.id and waiver.reason and isinstance(waiver.until, dt.date)


# --- A scan that did not happen ---------------------------------------------------


@pytest.mark.parametrize("output", [None, "", "not json", "[]", '{"results": null}', "{}"])
def test_missing_or_unreadable_scanner_output_fails(tmp_path, output):
    assert _run(tmp_path, output) == 2


def test_the_policy_refuses_to_run_without_a_lockfile_to_look_for(tmp_path):
    # With nothing expected, empty output would read as a clean scan.
    with pytest.raises(SystemExit) as stop:
        _run(tmp_path, _output(), expect=[])
    assert stop.value.code == 2


def test_output_that_misses_a_lockfile_fails(tmp_path, capsys):
    assert _run(tmp_path, _output(lockfiles=LOCKFILES[:-1])) == 2
    assert LOCKFILES[-1] in capsys.readouterr().err


def test_a_lockfile_with_no_packages_read_fails(tmp_path):
    output = _output()
    output["results"][1]["packages"] = []
    assert _run(tmp_path, output) == 2


def test_the_container_path_prefix_is_removed_before_lockfiles_are_matched(tmp_path, capsys):
    assert _run(tmp_path, _output(prefix="/src/"), extra=["--strip-prefix", "/src/"]) == 0
    assert "/src/" not in capsys.readouterr().out
    assert _run(tmp_path, _output(prefix="/src/")) == 2  # without it, nothing matches


def test_the_report_is_also_written_to_the_job_summary(tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert _run(tmp_path, _output(_package(_vuln("GHSA-aaaa"), score="9.8"))) == 1
    assert "GHSA-aaaa" in summary.read_text()


# --- The verdict as JSON, for the release evidence -----------------------------------
# scripts/release/scan_summary.py turns this into the release's scan-osv.json. It
# carries counts and lockfile paths only: never a package, an advisory or a waiver.


def _verdict(tmp_path, output, waivers="", scanner="ghcr.io/google/osv-scanner:v9.9.9@sha256:" + "a" * 64):
    path = tmp_path / "verdict.json"
    status = _run(tmp_path, output, waivers, extra=["--json", str(path), "--scanner", scanner])
    return status, json.loads(path.read_text())


def test_the_verdict_counts_findings_by_rating(tmp_path):
    output = _output(_package(_vuln("GHSA-aaaa"), score="9.8"), _package(_vuln("GHSA-bbbb", fixed=()), name="other", score="5.0"))
    status, verdict = _verdict(tmp_path, output)
    assert status == 1 and verdict["status"] == 1
    assert verdict["ratings"] == {"critical": 1, "high": 0, "medium": 1, "low": 0, "unrated": 0}
    assert verdict["blocking"] == 1 and verdict["waived"] == 0
    assert verdict["expired_waivers"] == 0 and verdict["unused_waivers"] == 0
    assert verdict["scanner"].startswith("ghcr.io/google/osv-scanner:v9.9.9@")
    assert sorted(verdict["packages"]) == sorted(LOCKFILES)
    assert "GHSA" not in json.dumps(verdict) and "demo" not in json.dumps(verdict)


def test_the_verdict_counts_waivers(tmp_path):
    output = _output(_package(_vuln("GHSA-aaaa"), score="9.8"))
    status, verdict = _verdict(tmp_path, output, _waivers(("GHSA-aaaa", "2026-12-31", REASON), ("GHSA-zzzz", "2026-01-01", REASON)))
    assert status == 1, "the expired waiver blocks"
    assert verdict["waived"] == 1 and verdict["blocking"] == 0 and verdict["expired_waivers"] == 1
    assert "GHSA" not in json.dumps(verdict)


def test_without_json_no_verdict_file_is_written(tmp_path):
    assert _run(tmp_path, _output()) == 0
    assert not (tmp_path / "verdict.json").exists()


def test_a_scan_that_cannot_be_trusted_writes_no_verdict(tmp_path):
    path = tmp_path / "verdict.json"
    assert _run(tmp_path, "not json", extra=["--json", str(path)]) == 2
    assert not path.exists(), "no verdict means the scan did not complete"


# --- The script, run against a stand-in for docker -----------------------------------

BASH = shutil.which("bash") or "/bin/bash"

STAND_IN_DOCKER = r"""#!/usr/bin/env bash
# Records how it was called and what it was given, then replays canned output.
printf '%s\n' "$@" > "$STUB_LOG/args"
for arg in "$@"; do
  case "$arg" in *:/src:ro) host="${arg%:/src:ro}" ;; esac
done
[ -n "${host:-}" ] && (cd "$host" && find . -type f | LC_ALL=C sort) > "$STUB_LOG/mounted"
[ -n "${STUB_RESULTS:-}" ] && cat "$STUB_RESULTS"
exit "${STUB_STATUS:-0}"
"""


def _tree(tmp_path, *, without=None, without_tree=None):
    root = tmp_path / "tree"
    for lockfile in LOCKFILES:
        if without_tree and lockfile.startswith(f"{without_tree}/"):
            continue
        # The directory stays even when its lockfile is left out: a lockfile
        # missing from a tree that is there is a failure, never a skip.
        (root / lockfile).parent.mkdir(parents=True, exist_ok=True)
        (root / lockfile).parent.joinpath("package.json").write_text("{}\n")
        if lockfile != without:
            (root / lockfile).write_text("placeholder\n")
    (root / "osv-scanner.toml").write_text("")
    (root / ".env").write_text("SECRET=do-not-mount\n")
    (root / "backend" / "private.py").write_text("# not a lockfile\n")
    return root


def _scan(tmp_path, *, status=0, output=None, root=None, with_docker=True, verdict=None):
    log = tmp_path / "log"
    log.mkdir(exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if with_docker:
        docker = bin_dir / "docker"
        docker.write_text(STAND_IN_DOCKER)
        docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
        # The stand-in first, so a real docker further along is never reached; then
        # this interpreter, so the script's python3 is one that has tomllib.
        path = os.pathsep.join([str(bin_dir), str(Path(sys.executable).parent), "/usr/bin", "/bin"])
    else:
        # No system directory at all: a runner has a real docker in /usr/bin, and
        # this test must not find it. The script needs only `dirname` to get as far
        # as looking for docker.
        (bin_dir / "dirname").symlink_to(shutil.which("dirname"))
        path = str(bin_dir)
    env = {
        "PATH": path,
        "STUB_LOG": str(log),
        "STUB_STATUS": str(status),
        "HOME": str(tmp_path),
    }
    if verdict is not None:
        env["OSV_VERDICT_JSON"] = str(verdict)
    if output is not None:
        canned = tmp_path / "canned.json"
        canned.write_text(json.dumps(output))
        env["STUB_RESULTS"] = str(canned)
    run = subprocess.run(
        [BASH, str(SCAN_SCRIPT), "--root", str(root or _tree(tmp_path))],
        env=env, capture_output=True, text=True, timeout=60,
    )
    return run, log


def _python3_on_path_is_this_one() -> bool:
    return (Path(sys.executable).parent / "python3").exists()


needs_python3 = pytest.mark.skipif(not _python3_on_path_is_this_one(), reason="no python3 beside this interpreter")


@needs_python3
def test_the_script_passes_a_clean_scan(tmp_path):
    run, _ = _scan(tmp_path, status=0, output=_output(prefix="/src/"))
    assert run.returncode == 0, run.stderr
    assert "Nothing blocks." in run.stdout and "backend/requirements.txt (1)" in run.stdout


@needs_python3
def test_the_script_writes_the_verdict_when_asked(tmp_path):
    verdict = tmp_path / "verdict.json"
    run, _ = _scan(tmp_path, status=0, output=_output(prefix="/src/"), verdict=verdict)
    assert run.returncode == 0, run.stderr
    doc = json.loads(verdict.read_text())
    assert doc["status"] == 0 and doc["scanner"].startswith("ghcr.io/google/osv-scanner:v")
    assert doc["packages"]["backend/requirements.txt"] == 1


@needs_python3
def test_the_script_fails_on_a_blocking_finding(tmp_path):
    output = _output(_package(_vuln("GHSA-aaaa"), score="9.1"), prefix="/src/")
    run, _ = _scan(tmp_path, status=1, output=output)
    assert run.returncode == 1 and "GHSA-aaaa" in run.stdout


@needs_python3
def test_findings_that_do_not_block_leave_the_script_passing(tmp_path):
    # The scanner exits 1 for any finding. The policy decides, not that status.
    output = _output(_package(_vuln("GHSA-aaaa"), score="5.0"), prefix="/src/")
    run, _ = _scan(tmp_path, status=1, output=output)
    assert run.returncode == 0, run.stdout + run.stderr


@pytest.mark.parametrize("status", [2, 127, 137])
def test_a_scanner_that_does_not_finish_fails_the_script(tmp_path, status):
    # Even with output that looks clean: the status says the scan did not complete.
    run, _ = _scan(tmp_path, status=status, output=_output(prefix="/src/"))
    assert run.returncode == 2 and "did not complete" in run.stderr


@needs_python3
def test_a_scanner_that_says_clean_and_prints_nothing_fails_the_script(tmp_path):
    run, _ = _scan(tmp_path, status=0, output=None)
    assert run.returncode == 2


@pytest.mark.parametrize("lockfile", LOCKFILES)
def test_a_missing_lockfile_fails_the_script_before_anything_runs(tmp_path, lockfile):
    run, log = _scan(tmp_path, root=_tree(tmp_path, without=lockfile))
    assert run.returncode == 2 and lockfile in run.stderr
    assert not (log / "args").exists(), "the scanner must not run on a partial set"


@needs_python3
@pytest.mark.parametrize("tree", PRIVATE_TREES)
def test_a_tree_the_snapshot_removes_is_skipped_when_absent_and_says_so(tmp_path, tree):
    rest = [lockfile for lockfile in LOCKFILES if not lockfile.startswith(f"{tree}/")]
    run, log = _scan(tmp_path, status=0, output=_output(lockfiles=rest, prefix="/src/"),
                     root=_tree(tmp_path, without_tree=tree))
    assert run.returncode == 0, run.stdout + run.stderr
    assert f"{tree}/ absent" in run.stderr
    args = (log / "args").read_text().splitlines()
    scanned = [args[index + 1].split(":", 1)[1] for index, arg in enumerate(args) if arg == "--lockfile"]
    assert scanned == [f"/src/{lockfile}" for lockfile in rest]


@needs_python3
def test_a_skipped_tree_is_not_expected_by_the_policy_and_a_present_one_is(tmp_path):
    # With the tree present, output that leaves its lockfile out is a scan that
    # did not cover everything.
    run, _ = _scan(tmp_path, status=0, output=_output(lockfiles=ALWAYS, prefix="/src/"))
    assert run.returncode == 2
    assert SKIPPABLE[0] in run.stderr


@pytest.mark.parametrize("tree", sorted({lockfile.split("/", 1)[0] for lockfile in ALWAYS}))
def test_a_tree_the_snapshot_keeps_is_never_skipped(tmp_path, tree):
    root = _tree(tmp_path)
    shutil.rmtree(root / tree)
    run, log = _scan(tmp_path, root=root)
    assert run.returncode == 2 and f"missing lockfile: {tree}/" in run.stderr
    assert not (log / "args").exists()


def test_no_docker_fails_the_script(tmp_path):
    run, _ = _scan(tmp_path, with_docker=False)
    assert run.returncode == 2 and "docker is not installed" in run.stderr


@needs_python3
def test_the_scanner_is_given_the_lockfiles_and_nothing_else(tmp_path):
    run, log = _scan(tmp_path, status=0, output=_output(prefix="/src/"))
    assert run.returncode == 0, run.stderr
    mounted = (log / "mounted").read_text().split()
    assert mounted == sorted(f"./{lockfile}" for lockfile in LOCKFILES)
    assert not [path for path in mounted if path.endswith("/package.json")], "only lockfiles are mounted"
    args = (log / "args").read_text().splitlines()
    volumes = [args[index + 1] for index, arg in enumerate(args) if arg in ("-v", "--volume")]
    assert len(volumes) == 1 and volumes[0].endswith(":/src:ro"), volumes
    assert str(tmp_path / "tree") not in volumes[0], "the tree itself must not be mounted"
    assert not [arg for arg in args if arg.startswith(("--mount", "--env", "-e", "--privileged", "--network"))]


@needs_python3
def test_the_scanner_is_asked_for_every_package_in_every_lockfile(tmp_path):
    _, log = _scan(tmp_path, status=0, output=_output(prefix="/src/"))
    args = (log / "args").read_text().splitlines()
    assert "--all-packages" in args and args[args.index("--format") + 1] == "json"
    scanned = [args[index + 1].split(":", 1)[1] for index, arg in enumerate(args) if arg == "--lockfile"]
    assert scanned == [f"/src/{lockfile}" for lockfile in LOCKFILES]
    # No scanner-side config: the policy file is the one place a finding is waived.
    assert not [arg for arg in args if arg.startswith("--config")]


# --- The pin, the lockfile list and the callers ----------------------------------------

IMAGE = re.compile(
    r'^OSV_SCANNER_IMAGE="(ghcr\.io/google/osv-scanner:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64})"$', re.MULTILINE
)


def test_the_scanner_image_is_one_exact_version_and_digest():
    text = SCAN_SCRIPT.read_text()
    assert len(IMAGE.findall(text)) == 1, "one image, written as tag@sha256:digest"
    assert len(re.findall(r"^OSV_SCANNER_IMAGE=", text, re.MULTILINE)) == 1, "assigned once, not overridable"
    assert "${OSV_SCANNER_IMAGE:-" not in text


@needs_python3
def test_the_image_the_script_runs_is_the_pinned_one(tmp_path):
    _, log = _scan(tmp_path, status=0, output=_output(prefix="/src/"))
    assert IMAGE.search(SCAN_SCRIPT.read_text()).group(1) in (log / "args").read_text().splitlines()


def _script_lockfiles() -> list[str]:
    block = re.search(r"^LOCKFILES=\(\n(.*?)^\)", SCAN_SCRIPT.read_text(), re.MULTILINE | re.DOTALL).group(1)
    return [entry.split(":", 1)[1] for entry in re.findall(r'"([^"]+)"', block)]


def _script_private_trees() -> list[str]:
    block = re.search(r"^PRIVATE_TREES=\(\n(.*?)^\)", SCAN_SCRIPT.read_text(), re.MULTILINE | re.DOTALL).group(1)
    return re.findall(r'"([^"]+)"', block)


def test_the_script_scans_the_lockfiles_these_tests_know():
    assert _script_lockfiles() == LOCKFILES
    assert _script_private_trees() == PRIVATE_TREES


@private_only
def test_a_tree_the_scan_may_skip_is_one_the_snapshot_removes_whole():
    # Otherwise "absent" could mean "deleted by mistake", and the scan would pass
    # on a tree it never read.
    removed = set()
    for line in PREPARE_OSS.read_text().splitlines():
        words = line.split("#")[0].split()
        if words[:2] == ["rm", "-rf"]:
            removed.update(words[2:])
    assert set(PRIVATE_TREES) <= removed, sorted(set(PRIVATE_TREES) - removed)


def _bun_lock_pins(path: Path) -> set[str]:
    # bun.lock is JSON with trailing commas. Each package entry starts "name@version".
    text = re.sub(r",(\s*[}\]])", r"\1", path.read_text())
    return {entry[0] for entry in json.loads(text)["packages"].values()}


def _package_lock_pins(path: Path) -> set[str]:
    packages = json.loads(path.read_text())["packages"]
    return {
        f"{entry.get('name') or key.rsplit('node_modules/', 1)[1]}@{entry['version']}"
        for key, entry in packages.items()
        if key
    }


def test_the_docs_site_builds_from_the_versions_the_scan_reads():
    # The site is built from bun.lock (deploy-docs.yml: bun install
    # --frozen-lockfile). The scan reads package-lock.json, which bun.lock is
    # migrated from. A change to one without the other would scan one tree and
    # ship another, so the two must pin exactly the same packages.
    site = REPO_ROOT / "docs-site"
    if not site.is_dir():
        pytest.skip("docs-site/ is not in this tree (public snapshot)")
    bun, npm = _bun_lock_pins(site / "bun.lock"), _package_lock_pins(site / "package-lock.json")
    assert bun and npm, "read nothing from one of the lockfiles"
    assert bun == npm, {"only in bun.lock": sorted(bun - npm), "only in package-lock.json": sorted(npm - bun)}


def test_every_lockfile_the_product_installs_from_is_scanned():
    # A compiled Python lock is a .txt with the .in it is compiled from beside it.
    python_locks = {
        str(source.with_suffix(".txt").relative_to(REPO_ROOT))
        for directory in ("backend", "scripts")
        for source in (REPO_ROOT / directory).glob("requirements*.in")
    }
    node_locks = {
        str(path.relative_to(REPO_ROOT))
        for directory in ("webclient", "docs-site")
        for path in (REPO_ROOT / directory).glob("*lock*")
        if path.name in {"package-lock.json", "bun.lock", "yarn.lock", "pnpm-lock.yaml"}
    }
    # docs-site/bun.lock is held to docs-site/package-lock.json by the test above.
    node_locks.discard("docs-site/bun.lock")
    present = [lockfile for lockfile in LOCKFILES if lockfile in ALWAYS or (REPO_ROOT / lockfile.split("/", 1)[0]).is_dir()]
    assert python_locks | node_locks == set(present)
    for lockfile in present:
        assert (REPO_ROOT / lockfile).is_file(), lockfile


def _unskippable(job: dict, where: str) -> None:
    # A job or step with `if:` or `needs:` can be skipped, and a skipped job passes.
    assert "if" not in job and "needs" not in job and "continue-on-error" not in job, where
    for step in job["steps"]:
        assert "continue-on-error" not in step and "if" not in step, f"{where}: {step.get('name')}"


def _runs(job: dict) -> list[str]:
    return [step["run"].strip() for step in job["steps"] if "run" in step]


EVIDENCE_STEPS = ["Summarise the scan for the release evidence", "Upload the scan summary"]


def test_the_ci_job_runs_the_scan_and_can_fail():
    job = yaml.safe_load(CI.read_text())["jobs"]["osv-scan"]
    [scan] = [step for step in job["steps"] if "scripts/osv-scan.sh" in step.get("run", "")]
    index = job["steps"].index(scan)
    # The scan and everything before it cannot be skipped; after it come only the
    # two steps that record its result for the release evidence.
    _unskippable({**job, "steps": job["steps"][: index + 1]}, "ci.yml osv-scan")
    assert [step.get("name") for step in job["steps"][index + 1:]] == EVIDENCE_STEPS
    # The whole step: nothing after the script that could swallow its status.
    assert scan["run"].strip() == "scripts/osv-scan.sh"
    assert scan["env"] == {"OSV_VERDICT_JSON": "${{ runner.temp }}/osv-verdict.json"}
    assert job["permissions"] == {"contents": "read"}


@private_only
def test_the_nightly_run_is_scheduled_and_runs_the_same_script():
    doc = yaml.safe_load(NIGHTLY.read_text())
    triggers = doc[True] if True in doc else doc["on"]  # YAML reads a bare `on` as true
    assert triggers["schedule"] and "workflow_dispatch" in triggers
    assert doc["permissions"] == {"contents": "read"}
    (job,) = doc["jobs"].values()
    _unskippable(job, "osv-nightly.yml")
    assert _runs(job) == ["scripts/osv-scan.sh"]


@private_only
def test_the_release_scans_the_snapshot_before_any_image_is_built():
    jobs = yaml.safe_load(RELEASE.read_text())["jobs"]
    steps = jobs["snapshot"]["steps"]
    names = [step.get("name", "") for step in steps]
    scan = [step for step in steps if "scripts/osv-scan.sh" in step.get("run", "")]
    assert len(scan) == 1, "one scan step in the snapshot job"
    step = scan[0]
    assert "continue-on-error" not in step and "if" not in step
    commands = [line.strip() for line in step["run"].splitlines() if line.strip() and not line.strip().startswith("#")]
    assert commands == ["set -euo pipefail", "bash scripts/osv-scan.sh --root oss-out"]
    built = names.index("Build the sanitized snapshot")
    packed = names.index("Fingerprint and pack the snapshot")
    assert built < steps.index(step) < packed, "after the snapshot exists, before it is handed on"
    assert "snapshot" in jobs["images"]["needs"], "images are only built from a snapshot that passed"


@private_only
def test_the_public_snapshot_ships_what_its_ci_job_needs():
    # ci.yml ships, so the files its "OSV Scan" job runs must ship with it.
    block = re.search(r"^OSS_SCRIPTS=\(\n(.*?)^\)", PREPARE_OSS.read_text(), re.MULTILINE | re.DOTALL).group(1)
    shipped = {line.split("#")[0].strip() for line in block.splitlines()} - {""}
    assert {"scripts/osv-scan.sh", "scripts/osv_policy.py"} <= shipped
    allowed = {line.split("#")[0].strip() for line in TOPLEVEL_ALLOW.read_text().splitlines()} - {""}
    assert "osv-scanner.toml" in allowed
