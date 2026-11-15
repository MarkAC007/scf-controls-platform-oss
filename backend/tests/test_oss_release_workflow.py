"""The public release is published only with both assets, checked and attested.

``oss-release.yml`` ships in the OSS snapshot and runs in the public repository
on every push to ``main``. It tags the version, stages a draft release, attests
``upgrade-manifest.json`` and ``release-images.json``, uploads both and only
then makes the release public.

The two shell steps are executed here against a local git remote and a
stand-in ``gh`` that keeps the release's state (missing, draft, published), so
the tests see what would have been tagged, uploaded and published. The
attestation step is an action and cannot run here; its place and its
conditions are pinned instead. Nothing reaches the network.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "oss-release.yml"

VERSION = "9.8.7"
TAG = f"v{VERSION}"
TOKEN = "not-a-real-token-value"
GOOD_RECORD = {
    "schema": 1,
    "version": VERSION,
    "source": {"snapshot_sha256": "a" * 64},
    "images": [
        {"name": "ghcr.io/markac007/scf-backend", "digest": "sha256:" + "b" * 64},
        {"name": "ghcr.io/markac007/scf-frontend", "digest": "sha256:" + "c" * 64},
    ],
}

SBOM = {"bomFormat": "CycloneDX", "specVersion": "1.6", "components": [{"type": "library", "name": "openssl"}]}
GATE = {"missing": [], "findings": [], "blocked": False, "enforcing": False, "enforce_from": "2026-12-14"}
VEX = {"@context": "https://openvex.dev/ns/v0.2.0", "statements": []}


# The source scans that ran on the released commit in the private repository, as
# scripts/release/scan_evidence.py publishes them: one scan-<tool>.json per scan and a
# scan-summary.md page. backend/tests/test_scan_evidence.py runs the real collector
# into this workflow's real check.
SCAN_TOOLS = ["gitleaks", "osv", "semgrep", "zap", "zizmor"]
SCANNED_COMMIT = "d" * 40


def scan(tool: str, **changes) -> dict:
    doc = {
        "schema": 1, "tool": tool, "scanner": tool.upper(), "version": "1.0.0", "commit": SCANNED_COMMIT,
        "workflow": "CI", "job": "A job", "run_id": 4242, "run_attempt": 1, "event": "push",
        "job_conclusion": "success", "outcome": "reported" if tool == "semgrep" else "pass",
        "policy": "What fails it.", "scope": ["."], "counts": {"high": 0, "low": 3}, "totals": {}, "reason": None,
    }
    doc.update(changes)
    return doc


MISSING_SCAN = {"version": None, "run_id": None, "run_attempt": None, "event": None, "job_conclusion": None,
                "outcome": "missing", "scope": [], "counts": {}, "totals": {}, "reason": "no completed run"}
SCAN_PAGE = f"# Source scans\n\nCommit `{SCANNED_COMMIT}`.\n"


def good_evidence(record=GOOD_RECORD) -> dict:
    """release-evidence/ as release-oss.yml writes it: name -> JSON text."""
    files = {f"sbom-{i['name'].rsplit('/', 1)[1]}.cdx.json": json.dumps(SBOM) for i in record["images"]}
    files["image-scan-gate.json"] = json.dumps(GATE)
    files.update({f"scan-{tool}.json": json.dumps(scan(tool)) for tool in SCAN_TOOLS})
    files["scan-summary.md"] = SCAN_PAGE
    return files


# Keeps one release's state in $STUB/state and every call in $STUB/calls.
STAND_IN_GH = r"""#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "$STUB/calls"
state="$(cat "$STUB/state")"
[ "${1:-}" = "release" ] || { echo "unexpected gh call: $*" >&2; exit 64; }
tag="${3:-}"
case "${2:-}" in
  view)
    [ "$state" = "missing" ] && { echo "release not found" >&2; exit 1; }
    case "$*" in *isDraft*) if [ "$state" = "draft" ]; then echo true; else echo false; fi ;; esac
    ;;
  create)
    [ "$state" = "missing" ] || { echo "release exists" >&2; exit 1; }
    case " $* " in *" --verify-tag "*) git ls-remote --exit-code origin "refs/tags/$tag" >/dev/null || { echo "tag not on remote" >&2; exit 1; } ;; esac
    case " $* " in *" --draft "*) echo draft > "$STUB/state" ;; *) echo published > "$STUB/state" ;; esac
    ;;
  upload)
    [ "$state" = "draft" ] || { echo "cannot upload to a $state release" >&2; exit 1; }
    shift 3
    for f in "$@"; do
      case "$f" in --*) ;; *) [ -f "$f" ] || { echo "no such file: $f" >&2; exit 1; }; cp "$f" "$STUB/assets/" ;; esac
    done
    ;;
  edit)
    [ "$state" = "draft" ] || { echo "nothing to publish" >&2; exit 1; }
    case " $* " in *" --draft=false "*) echo published > "$STUB/state" ;; esac
    ;;
  delete)
    echo missing > "$STUB/state"; rm -f "$STUB/assets/"*
    case " $* " in *" --cleanup-tag "*) git push -q origin ":refs/tags/$tag" >/dev/null 2>&1; git tag -d "$tag" >/dev/null 2>&1 ;; esac
    exit 0
    ;;
  *) echo "unexpected gh call: $*" >&2; exit 64 ;;
esac
"""


def _workflow() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text())
    doc["on"] = doc.pop(True) if True in doc else doc["on"]  # PyYAML reads `on` as True
    return doc


def _steps() -> list[dict]:
    return _workflow()["jobs"]["release"]["steps"]


def _named(fragment: str) -> dict:
    matches = [s for s in _steps() if fragment in s.get("name", "")]
    assert len(matches) == 1, f"expected exactly one step named like {fragment!r}"
    return matches[0]


def _stage_step() -> dict:
    matches = [s for s in _steps() if s.get("id") == "stage"]
    assert len(matches) == 1
    return matches[0]


def _attest_step() -> dict:
    matches = [s for s in _steps() if "actions/attest" in s.get("uses", "")]
    assert len(matches) == 1, "expected exactly one attestation step"
    return matches[0]


def _publish_step() -> dict:
    return _named("publish the release")


GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(cwd), **GIT_ENV}
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=check).stdout.strip()


class Release:
    """A checkout of a stand-in public repository, and the release's state."""

    def __init__(self, tmp_path: Path, *, version=VERSION, manifest=True, record=GOOD_RECORD,
                 state="missing", tagged=False, package=True, evidence="default"):
        for tool in ("bash", "git", "jq"):
            assert shutil.which(tool), f"{tool} is needed to run the release steps"
        tmp_path.mkdir(parents=True, exist_ok=True)
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.origin.mkdir()
        _git(self.origin, "init", "-q", "--bare", "-b", "main")
        self.work = tmp_path / "work"
        self.work.mkdir()
        _git(self.work, "init", "-q", "-b", "main")
        _git(self.work, "remote", "add", "origin", str(self.origin))
        (self.work / "README.md").write_text("public\n")
        if package:
            (self.work / "webclient").mkdir()
            (self.work / "webclient" / "package.json").write_text(json.dumps({"version": version}))
        if manifest:
            (self.work / "upgrade-manifest.json").write_text(json.dumps({"version": version}) + "\n")
        if record is not None:
            text = record if isinstance(record, str) else json.dumps(record, indent=2) + "\n"
            (self.work / "release-images.json").write_text(text)
        if evidence == "default":
            evidence = good_evidence()
        if evidence is not None:
            (self.work / "release-evidence").mkdir()
            for name, body in evidence.items():
                path = self.work / "release-evidence" / name
                if body is None:
                    path.mkdir()
                else:
                    path.write_text(body)
        _git(self.work, "add", "-A")
        _git(self.work, "commit", "-q", "-m", f"Release {TAG}")
        _git(self.work, "push", "-q", "origin", "main")
        if tagged:
            _git(self.work, "tag", "-a", TAG, "-m", "earlier run")
            _git(self.work, "push", "-q", "origin", TAG)

        self.stub = tmp_path / "stub"
        (self.stub / "assets").mkdir(parents=True)
        (self.stub / "state").write_text(state + "\n")
        (self.stub / "calls").write_text("")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(STAND_IN_GH)
        gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
        self.output = tmp_path / "github_output"
        self.output.write_text("")
        self.env = {
            "PATH": os.pathsep.join([str(bin_dir), os.environ["PATH"]]), "HOME": str(tmp_path),
            "STUB": str(self.stub), "GH_TOKEN": TOKEN, "GITHUB_OUTPUT": str(self.output), **GIT_ENV,
        }

    def _run(self, step: dict, **extra) -> subprocess.CompletedProcess:
        # The flags GitHub runs a `shell: bash` step with.
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            cwd=self.work, env={**self.env, **extra}, capture_output=True, text=True, timeout=120,
        )

    def stage(self) -> subprocess.CompletedProcess:
        return self._run(_stage_step())

    def publish(self) -> subprocess.CompletedProcess:
        return self._run(_publish_step(), TAG=self.outputs().get("tag", ""))

    def outputs(self) -> dict:
        return dict(line.split("=", 1) for line in self.output.read_text().splitlines() if "=" in line)

    def state(self) -> str:
        return (self.stub / "state").read_text().strip()

    def calls(self) -> list[str]:
        return [c.removeprefix("release ") for c in (self.stub / "calls").read_text().splitlines()]

    def verbs(self) -> list[str]:
        return [c.split()[0] for c in self.calls()]

    def remote_tag(self) -> str:
        return _git(self.origin, "for-each-ref", "--format=%(objecttype) %(*objectname)", f"refs/tags/{TAG}")

    def assets(self) -> dict:
        return {p.name: p.read_bytes() for p in (self.stub / "assets").iterdir()}


# --- A new version ---------------------------------------------------------------------

def test_a_new_version_is_tagged_and_staged_as_a_draft_and_nothing_is_public_yet(tmp_path):
    rel = Release(tmp_path)
    run = rel.stage()
    assert run.returncode == 0, run.stdout + run.stderr
    assert rel.outputs() == {"publish": "true", "tag": TAG}
    assert rel.remote_tag() == f"tag {_git(rel.work, 'rev-parse', 'HEAD')}"  # annotated, at this commit
    assert rel.state() == "draft"
    assert not {"upload", "edit"} & set(rel.verbs())
    assert rel.assets() == {}


def test_both_assets_go_up_before_the_release_is_made_public(tmp_path):
    rel = Release(tmp_path)
    assert rel.stage().returncode == 0
    run = rel.publish()
    assert run.returncode == 0, run.stdout + run.stderr
    assert rel.state() == "published"
    assert rel.assets() == {
        "upgrade-manifest.json": (rel.work / "upgrade-manifest.json").read_bytes(),
        "release-images.json": (rel.work / "release-images.json").read_bytes(),
        **{p.name: p.read_bytes() for p in (rel.work / "release-evidence").iterdir()},
    }
    assert set(rel.assets()) >= {"sbom-scf-backend.cdx.json", "sbom-scf-frontend.cdx.json", "image-scan-gate.json"}
    verbs = rel.verbs()
    assert verbs.index("upload") < verbs.index("edit")


def test_an_asset_that_cannot_be_uploaded_leaves_the_release_a_draft(tmp_path):
    rel = Release(tmp_path)
    assert rel.stage().returncode == 0
    (rel.work / "release-images.json").unlink()
    assert rel.publish().returncode != 0
    assert rel.state() == "draft"


def test_the_token_reaches_git_only_through_the_environment(tmp_path):
    rel = Release(tmp_path)
    assert rel.stage().returncode == 0
    config = (rel.work / ".git" / "config").read_text()
    assert "password=${GH_TOKEN}" in config and TOKEN not in config


# --- Assets that are missing or wrong ----------------------------------------------------

BACKEND = "ghcr.io/markac007/scf-backend"


def _with_images(*images) -> dict:
    return {**GOOD_RECORD, "images": list(images)}


BAD_RECORDS = {
    "another version's record": {**GOOD_RECORD, "version": "9.8.6"},
    "no version": {k: v for k, v in GOOD_RECORD.items() if k != "version"},
    "no images": _with_images(),
    "images is not a list": {**GOOD_RECORD, "images": {"scf-backend": "sha256:" + "b" * 64}},
    "a tag where a digest belongs": _with_images({"name": BACKEND, "digest": TAG}),
    "an upper-case digest": _with_images({"name": BACKEND, "digest": "sha256:" + "B" * 64}),
    "a short digest": _with_images({"name": BACKEND, "digest": "sha256:" + "b" * 63}),
    "one good image and one without a digest": _with_images(GOOD_RECORD["images"][0], {"name": "ghcr.io/markac007/scf-frontend"}),
    "an image from another registry": _with_images({"name": "docker.io/x/scf-backend", "digest": "sha256:" + "b" * 64}),
    "not JSON": "release-images\n",
    "an empty file": "",
}


@pytest.mark.parametrize("kwargs, message", [
    ({"record": None}, "release-images.json is missing from the snapshot"),
    ({"manifest": False}, "upgrade-manifest.json is missing from the snapshot"),
    *[({"record": record}, "release-images.json does not describe") for record in BAD_RECORDS.values()],
], ids=["no record", "no manifest", *BAD_RECORDS])
@pytest.mark.parametrize("tagged, state", [(False, "missing"), (True, "draft")], ids=["new", "repairing a draft"])
def test_a_missing_or_wrong_asset_rolls_the_release_back(tmp_path, kwargs, message, tagged, state):
    rel = Release(tmp_path, tagged=tagged, state=state, **kwargs)
    run = rel.stage()
    assert run.returncode != 0
    assert message in run.stdout
    assert rel.outputs() == {}, "the later steps must not run"
    assert rel.state() == "missing" and rel.remote_tag() == "", "the draft and the tag are rolled back"
    assert rel.calls()[-1] == f"delete {TAG} --yes --cleanup-tag"


# --- Release evidence (plan 7.5) -----------------------------------------------------

def _evidence(**changes) -> dict:
    files = good_evidence()
    for name, body in changes.items():
        name = name.replace("__", ".").replace("_", "-")
        if body is ...:
            files.pop(name)
        else:
            files[name] = body
    return files


BAD_EVIDENCE = {
    "no folder": (None, "release-evidence/ is missing"),
    "no backend SBOM": (_evidence(sbom_scf_backend__cdx__json=...), "exactly one SBOM per image"),
    "an SBOM for an image the record does not name": (
        {**good_evidence(), "sbom-scf-worker.cdx.json": json.dumps(SBOM)}, "exactly one SBOM per image"),
    "an SBOM with no components": (
        _evidence(sbom_scf_frontend__cdx__json=json.dumps({**SBOM, "components": []})), "not a CycloneDX SBOM"),
    "an SPDX SBOM": (_evidence(sbom_scf_frontend__cdx__json=json.dumps({"spdxVersion": "SPDX-2.3"})), "not a CycloneDX SBOM"),
    "no gate result": (_evidence(image_scan_gate__json=...), "image-scan-gate.json is missing"),
    "a gate result that is not one": (_evidence(image_scan_gate__json="{}"), "not an image scan gate result"),
    "a VEX file that is not OpenVEX": ({**good_evidence(), "vex-a.json": "{}"}, "is not an OpenVEX document"),
    "no summary for one scan": (_evidence(scan_zap__json=...), "the five source scan summaries"),
    "a summary for a scan that is not one of the five": (
        {**good_evidence(), "scan-trivy.json": json.dumps(scan("trivy"))}, "the five source scan summaries"),
    "a summary filed under another scan's name": (
        _evidence(scan_zap__json=json.dumps(scan("osv"))), "scan-zap.json is not a source scan summary"),
    "an outcome that is not one": (
        _evidence(scan_osv__json=json.dumps(scan("osv", outcome="fine"))), "scan-osv.json is not a source scan summary"),
    "a pass with no version": (
        _evidence(scan_osv__json=json.dumps(scan("osv", version=None))), "scan-osv.json is not a source scan summary"),
    "a count that is not a number": (
        _evidence(scan_osv__json=json.dumps(scan("osv", counts={"high": "0"}))), "is not a source scan summary"),
    "a missing scan with no reason": (
        _evidence(scan_zizmor__json=json.dumps(scan("zizmor", **{**MISSING_SCAN, "reason": None}))),
        "scan-zizmor.json is not a source scan summary"),
    "a short commit": (
        _evidence(scan_zap__json=json.dumps(scan("zap", commit="abc123"))), "scan-zap.json is not a source scan summary"),
    "scans of two commits": (
        _evidence(scan_zap__json=json.dumps(scan("zap", commit="e" * 40))), "do not all describe one commit"),
    "not JSON": (_evidence(scan_semgrep__json="{"), "scan-semgrep.json is not a source scan summary"),
    "no summary page": (_evidence(scan_summary__md=...), "scan-summary.md is missing or does not describe"),
    "a page with another heading": (
        _evidence(scan_summary__md="# Notes\n" + SCANNED_COMMIT), "scan-summary.md is missing or does not describe"),
    "a page for another commit": (
        _evidence(scan_summary__md="# Source scans\n\n" + "e" * 40), "scan-summary.md is missing or does not describe"),
    "a stray file": ({**good_evidence(), "notes.txt": "x"}, "not evidence"),
    "a folder inside": ({**good_evidence(), "vex-dir.json": None}, "not evidence"),
}


@pytest.mark.parametrize("evidence, message", BAD_EVIDENCE.values(), ids=BAD_EVIDENCE)
def test_missing_or_wrong_evidence_rolls_the_release_back(tmp_path, evidence, message):
    rel = Release(tmp_path, evidence=evidence)
    run = rel.stage()
    assert run.returncode != 0
    assert message in run.stdout
    assert rel.outputs() == {}
    assert rel.state() == "missing" and rel.remote_tag() == ""


def test_a_missing_or_failed_scan_is_published_as_it_was_recorded(tmp_path):
    # The private side decides whether that holds the release; here it is evidence.
    rel = Release(tmp_path, evidence=_evidence(
        scan_zizmor__json=json.dumps(scan("zizmor", **MISSING_SCAN)),
        scan_osv__json=json.dumps(scan("osv", outcome="fail", reason=None))))
    assert rel.stage().returncode == 0
    assert rel.publish().returncode == 0
    assert {"scan-zizmor.json", "scan-osv.json", "scan-summary.md"} <= set(rel.assets())


def test_vex_documents_are_published_with_the_evidence(tmp_path):
    rel = Release(tmp_path, evidence={**good_evidence(), "vex-a.json": json.dumps(VEX)})
    assert rel.stage().returncode == 0
    assert rel.publish().returncode == 0
    assert "vex-a.json" in rel.assets()


# --- Re-runs ---------------------------------------------------------------------------

def test_a_published_release_is_left_alone(tmp_path):
    rel = Release(tmp_path, tagged=True, state="published")
    run = rel.stage()
    assert run.returncode == 0 and "already published" in run.stdout
    assert rel.outputs() == {}
    assert set(rel.verbs()) == {"view"}


@pytest.mark.parametrize("state", ["draft", "missing"])
def test_a_run_that_died_after_tagging_is_picked_up(tmp_path, state):
    rel = Release(tmp_path, tagged=True, state=state)
    before = rel.remote_tag()
    run = rel.stage()
    assert run.returncode == 0, run.stdout + run.stderr
    assert rel.outputs() == {"publish": "true", "tag": TAG}
    assert rel.remote_tag() == before
    assert ("create" in rel.verbs()) == (state == "missing")
    assert rel.publish().returncode == 0 and rel.state() == "published"


def test_a_tree_without_the_web_client_is_not_a_release(tmp_path):
    rel = Release(tmp_path, package=False)
    run = rel.stage()
    assert run.returncode == 0 and rel.outputs() == {} and rel.calls() == []


# --- The attestation, and the shape of the job -------------------------------------------

def test_the_assets_are_attested_between_the_draft_and_publication():
    steps = _steps()
    order = [steps.index(_stage_step()), steps.index(_attest_step()), steps.index(_publish_step())]
    assert order == sorted(order) and len(set(order)) == 3
    attest = _attest_step()
    assert re.fullmatch(r"actions/attest-build-provenance@[0-9a-f]{40}", attest["uses"])
    assert attest["with"]["subject-path"].split() == ["upgrade-manifest.json", "release-images.json", "release-evidence/*"]
    assert set(attest["with"]) == {"subject-path"}


def test_a_failed_attestation_stops_publication():
    gate = "steps.stage.outputs.publish == 'true'"
    assert _attest_step()["if"] == gate and _publish_step()["if"] == gate
    for step in _steps():
        assert "continue-on-error" not in step, step.get("name")
    assert "continue-on-error" not in _workflow()["jobs"]["release"]
    # Only the last step uploads or publishes: nothing public happens before the attestation.
    last = _steps()[-1]
    assert last == _publish_step()
    for step in _steps()[:-1]:
        assert "gh release upload" not in step.get("run", "") and "--draft=false" not in step.get("run", "")


def test_the_job_holds_only_the_permissions_it_uses_and_keeps_no_credential():
    doc = _workflow()
    assert doc["permissions"] == {}
    assert list(doc["jobs"]) == ["release"]
    job = doc["jobs"]["release"]
    assert job["permissions"] == {"contents": "write", "id-token": "write", "attestations": "write"}
    assert job["if"] == "github.repository == 'MarkAC007/scf-controls-platform-oss'"
    checkout = [s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@")]
    assert len(checkout) == 1 and checkout[0]["with"]["persist-credentials"] is False
    secrets = set(re.findall(r"secrets\.([A-Za-z0-9_]+)", WORKFLOW.read_text()))
    assert secrets == {"GITHUB_TOKEN"}
    assert "zizmor: ignore" not in WORKFLOW.read_text()


# --- The two repositories agree on the record --------------------------------------------

ORCHESTRATOR = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"


@pytest.mark.skipif(not ORCHESTRATOR.exists(), reason="release-oss.yml is stripped from the OSS snapshot")
def test_the_record_the_release_workflow_writes_is_the_record_this_workflow_accepts(tmp_path):
    """release-oss.yml writes release-images.json in the private repository; this
    workflow checks it in the public one. The file is produced by the real step
    and handed to the real check, so the two cannot drift apart unnoticed."""
    private = yaml.safe_load(ORCHESTRATOR.read_text())
    record_step = [s for s in private["jobs"]["cross_and_merge"]["steps"] if s.get("id") == "record"]
    assert len(record_step) == 1
    runner = tmp_path / "runner"
    runner.mkdir()
    env = {k: str(v) for k, v in private["env"].items()}
    env.update({
        "PATH": os.environ["PATH"], "RUNNER_TEMP": str(runner), "VERSION": VERSION, "SNAPSHOT_DIGEST": "a" * 64,
        "BE_DIGEST": "sha256:" + "b" * 64, "FE_DIGEST": "sha256:" + "c" * 64,
    })
    written = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", record_step[0]["run"]],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert written.returncode == 0, written.stdout + written.stderr

    rel = Release(tmp_path / "public", record=(runner / "release-images.json").read_text())
    run = rel.stage()
    assert run.returncode == 0, run.stdout + run.stderr
    assert rel.outputs() == {"publish": "true", "tag": TAG}


@pytest.mark.skipif(not ORCHESTRATOR.exists(), reason="release-oss.yml is stripped from the OSS snapshot")
def test_the_evidence_the_release_workflow_assembles_is_the_evidence_this_workflow_accepts(tmp_path):
    """The scan job's real step assembles release-evidence/; this workflow's real
    check accepts it."""
    private = yaml.safe_load(ORCHESTRATOR.read_text())
    [step] = [s for s in private["jobs"]["scan"]["steps"] if s.get("name") == "Assemble the release evidence"]
    runner = tmp_path / "runner"
    for d in ("sbom", "scan", "source-scans"):
        (runner / d).mkdir(parents=True)
    for name, body in good_evidence().items():
        if name.startswith("scan-"):
            (runner / "source-scans" / name).write_text(body)
    for image in ("scf-backend", "scf-frontend"):
        (runner / "sbom" / f"{image}.cdx.json").write_text(json.dumps(SBOM))
    (runner / "scan" / "gate-summary.json").write_text(json.dumps(GATE))
    work = tmp_path / "scanwork"
    (work / "release-tools" / "security" / "vex").mkdir(parents=True)
    (work / "release-tools" / "security" / "vex" / "a.json").write_text(json.dumps(VEX))
    env = {k: str(v) for k, v in private["env"].items()}
    images = re.sub(r"\$\{\{ env\.(\w+) \}\}", lambda m: env[m.group(1)], step["env"]["IMAGES"])
    built = subprocess.run(["bash", "--noprofile", "--norc", "-e", "-c", step["run"]], cwd=work,
                           env={"PATH": os.environ["PATH"], "RUNNER_TEMP": str(runner), "IMAGES": images},
                           capture_output=True, text=True, timeout=60)
    assert built.returncode == 0, built.stdout + built.stderr
    files = {p.name: p.read_text() for p in (runner / "release-evidence").iterdir()}
    assert sorted(files) == sorted(["image-scan-gate.json", "sbom-scf-backend.cdx.json", "sbom-scf-frontend.cdx.json",
                                    "vex-a.json", "scan-summary.md", *(f"scan-{t}.json" for t in SCAN_TOOLS)])
    rel = Release(tmp_path / "public", evidence=files)
    run = rel.stage()
    assert run.returncode == 0, run.stdout + run.stderr
