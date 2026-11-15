"""A release says which image bytes belong to it (release-images.json).

``release-oss.yml`` builds the images from the snapshot, checks that a stranger
can pull them, and only then writes ``release-images.json`` into the release
commit: the version, the snapshot fingerprint, and the digest of each image.

Two steps of the ``cross_and_merge`` job do that, and both are executed here,
not only read:

* "record" turns the verified digests into the file, and refuses any value that
  is not exactly the right shape;
* "push" builds the release commit. It is run against a local bare repository
  that stands in for the public one, so the test sees the commit that would
  have been pushed.

Nothing here reaches the network: the only remote the push step names is
rewritten to a directory under ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[2])
ORCHESTRATOR = REPO_ROOT / ".github" / "workflows" / "release-oss.yml"
PREPARE_OSS = REPO_ROOT / "scripts" / "prepare-oss.sh"
RECORD = "release-images.json"

# release-oss.yml and prepare-oss.sh are stripped from the OSS snapshot, and the
# public repository runs this same file.
private_only = pytest.mark.skipif(
    not ORCHESTRATOR.exists(), reason="release-oss.yml is stripped from the OSS snapshot"
)

VERSION = "9.8.7"
SNAPSHOT = "a" * 64
BACKEND = "sha256:" + "b" * 64
FRONTEND = "sha256:" + "c" * 64


def _workflow() -> dict:
    return yaml.safe_load(ORCHESTRATOR.read_text())


def _job() -> dict:
    return _workflow()["jobs"]["cross_and_merge"]


def _step(step_id: str) -> dict:
    matches = [s for s in _job()["steps"] if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id!r}"
    return matches[0]


def _base_env(tmp_path: Path) -> dict:
    for tool in ("bash", "git", "jq", "tar", "cmp"):
        assert shutil.which(tool), f"{tool} is needed to run the release steps"
    env = {k: str(v) for k, v in _workflow()["env"].items()}  # GHCR_NS, image names, ...
    env.update({"PATH": os.environ["PATH"], "HOME": str(tmp_path), "RUNNER_TEMP": str(tmp_path / "runner")})
    (tmp_path / "runner").mkdir(exist_ok=True)
    return env


def _run(script: str, env: dict) -> subprocess.CompletedProcess:
    # The flags GitHub runs a `run:` step with.
    return subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=env, capture_output=True, text=True, timeout=120,
    )


def _record(tmp_path: Path, **values) -> subprocess.CompletedProcess:
    env = _base_env(tmp_path)
    env.update({"VERSION": VERSION, "SNAPSHOT_DIGEST": SNAPSHOT, "BE_DIGEST": BACKEND, "FE_DIGEST": FRONTEND})
    env.update(values)
    return _run(_step("record")["run"], env)


def _record_file(tmp_path: Path) -> Path:
    return tmp_path / "runner" / RECORD


# --- The record step -------------------------------------------------------------------

@private_only
def test_the_record_names_the_version_the_snapshot_and_both_digests(tmp_path):
    run = _record(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert json.loads(_record_file(tmp_path).read_text()) == {
        "schema": 1,
        "version": VERSION,
        "source": {"snapshot_sha256": SNAPSHOT},
        "images": [
            {"name": "ghcr.io/markac007/scf-backend", "digest": BACKEND},
            {"name": "ghcr.io/markac007/scf-frontend", "digest": FRONTEND},
        ],
    }


@private_only
@pytest.mark.parametrize("name, value", [
    ("BE_DIGEST", ""),                              # the verify job was skipped or failed
    ("FE_DIGEST", ""),
    ("BE_DIGEST", "b" * 64),                        # no algorithm
    ("FE_DIGEST", "sha256:" + "c" * 63),            # short
    ("BE_DIGEST", "sha256:" + "B" * 64),            # not the registry's lower-case hex
    ("FE_DIGEST", "sha256:" + "c" * 64 + "\nx"),    # more than a digest
    ("BE_DIGEST", "v9.8.7"),                        # a tag is not a digest
    ("SNAPSHOT_DIGEST", ""),
    ("SNAPSHOT_DIGEST", "sha256:" + "a" * 64),
    ("VERSION", ""),
    ("VERSION", "9.8"),
    ("VERSION", "9.8.7-rc1"),
])
def test_a_value_of_the_wrong_shape_stops_the_release_and_writes_nothing(tmp_path, name, value):
    run = _record(tmp_path, **{name: value})
    assert run.returncode != 0
    assert "::error::release-images.json" in run.stdout
    assert not _record_file(tmp_path).exists()


@private_only
def test_the_file_is_read_back_and_compared_with_the_verified_digests(tmp_path):
    """Two entries under one name cannot both equal the verified digest. The
    step only notices if it reads its own output back, which is the same read
    that catches the two digests written the wrong way round."""
    run = _record(tmp_path, FRONTEND_IMAGE="scf-backend")
    assert run.returncode != 0
    assert "J4 verified" in run.stdout


@private_only
def test_the_digests_are_the_ones_this_run_verified():
    env = _step("record")["env"]
    assert env["BE_DIGEST"] == "${{ needs.images_verify.outputs.backend_digest }}"
    assert env["FE_DIGEST"] == "${{ needs.images_verify.outputs.frontend_digest }}"
    assert env["SNAPSHOT_DIGEST"] == "${{ needs.snapshot.outputs.snapshot_digest }}"
    assert env["VERSION"] == "${{ needs.preflight.outputs.version }}"
    assert {"snapshot", "images_verify"} <= set(_job()["needs"])


@private_only
def test_the_record_step_holds_no_secret_and_runs_before_the_push():
    step = _step("record")
    assert "secrets." not in yaml.safe_dump(step)
    assert set(step["env"]) == {"VERSION", "SNAPSHOT_DIGEST", "BE_DIGEST", "FE_DIGEST"}
    assert "if" not in step and "continue-on-error" not in step
    ids = [s.get("id") for s in _job()["steps"]]
    assert ids.index("record") < ids.index("push")


@private_only
def test_the_snapshot_fingerprint_is_taken_without_the_record():
    """The file is added to the commit by a later job, so the fingerprint and the
    image build context are the snapshot alone."""
    jobs = _workflow()["jobs"]
    assert RECORD not in yaml.safe_dump(jobs["snapshot"])
    assert RECORD not in yaml.safe_dump(jobs["images"])


# --- The push step, against a stand-in for the public repository -------------------------

SNAPSHOT_FILES = {
    "README.md": "new readme\n",
    "upgrade-manifest.json": '{"version": "9.8.7"}\n',
    "scripts/upgrade.sh": "#!/usr/bin/env bash\necho upgrade\n",
}


EVIDENCE = {
    "sbom-scf-backend.cdx.json": '{"bomFormat": "CycloneDX", "components": [{"name": "a"}]}\n',
    "sbom-scf-frontend.cdx.json": '{"bomFormat": "CycloneDX", "components": [{"name": "b"}]}\n',
    "image-scan-gate.json": '{"blocked": false, "findings": [], "missing": []}\n',
}


def _evidence(tmp_path: Path, files: dict | None = None) -> Path:
    """What the scan job's release-evidence artifact downloads to."""
    target = tmp_path / "runner" / "release-evidence"
    target.mkdir(parents=True, exist_ok=True)
    for name, text in (EVIDENCE if files is None else files).items():
        (target / name).write_text(text)
    return target


def _git(cwd: Path, *args: str) -> str:
    env = {
        "PATH": os.environ["PATH"], "HOME": str(cwd), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


def _public_repo(tmp_path: Path, *, with_record: bool = True) -> Path:
    """A bare repository with a `main` that holds a file the snapshot drops and,
    unless told otherwise, the previous release's record."""
    bare = tmp_path / "remotes" / "local" / "public.git"
    bare.mkdir(parents=True)
    _git(bare, "init", "-q", "--bare", "-b", "main")
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_text("old readme\n")
    (seed / "dropped.txt").write_text("gone in this release\n")
    if with_record:
        (seed / RECORD).write_text('{"version": "9.8.6"}\n')
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "previous release")
    _git(seed, "push", "-q", str(bare), "main")
    return bare


def _archive(tmp_path: Path, files: dict) -> None:
    target = tmp_path / "runner" / "archive"
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(target / "oss-snapshot.tar.gz", "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"./{name}")
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))


def _push(tmp_path: Path, *, evidence: bool = True) -> subprocess.CompletedProcess:
    env = _base_env(tmp_path)
    if evidence and not (tmp_path / "runner" / "release-evidence").exists():
        _evidence(tmp_path)
    output = tmp_path / "github_output"
    output.write_text("")
    env.update({
        "GH_TOKEN": "not-a-token", "VERSION": VERSION, "PRIVATE_SHA": "f" * 40,
        "GITHUB_OUTPUT": str(output),
        # Not the real public repository, and github.com is rewritten to a local
        # directory: if the rewrite ever stopped working the step would look for
        # a repository that does not exist, and fail.
        "PUBLIC_REPO": "local/public",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.file://{tmp_path / 'remotes'}/.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://github.com/",
    })
    return _run(_step("push")["run"], env)


def _branch(bare: Path) -> str:
    return _git(bare, "for-each-ref", "--format=%(objectname)", f"refs/heads/release/v{VERSION}")


@private_only
@pytest.mark.parametrize("with_record", [True, False], ids=["a later release", "the first release with a record"])
def test_the_release_commit_carries_the_record_that_was_checked(tmp_path, with_record):
    bare = _public_repo(tmp_path, with_record=with_record)
    _archive(tmp_path, SNAPSHOT_FILES)
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr

    head = _branch(bare)
    assert head and f"head_sha={head}" in (tmp_path / "github_output").read_text()
    assert _git(bare, "rev-parse", f"{head}^") == _git(bare, "rev-parse", "main")
    # The snapshot, plus the record and the evidence, and nothing the snapshot dropped.
    tree = set(_git(bare, "ls-tree", "-r", "--name-only", head).splitlines())
    assert tree == set(SNAPSHOT_FILES) | {RECORD} | {f"release-evidence/{name}" for name in EVIDENCE}
    for name, text in EVIDENCE.items():
        assert _git(bare, "show", f"{head}:release-evidence/{name}") == text.strip()
    committed = _git(bare, "show", f"{head}:{RECORD}")
    assert committed == _record_file(tmp_path).read_text().strip()
    assert json.loads(committed)["images"][0]["digest"] == BACKEND
    assert json.loads(committed)["version"] == VERSION  # not the previous release's file


@private_only
def test_a_snapshot_that_brings_its_own_record_is_refused(tmp_path):
    bare = _public_repo(tmp_path)
    _archive(tmp_path, {**SNAPSHOT_FILES, RECORD: '{"version": "0.0.0"}\n'})
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path)
    assert run.returncode != 0
    assert "the snapshot already contains release-images.json" in run.stdout
    assert _branch(bare) == ""


@private_only
def test_without_a_record_nothing_is_pushed(tmp_path):
    bare = _public_repo(tmp_path)
    _archive(tmp_path, SNAPSHOT_FILES)
    run = _push(tmp_path)  # the record step did not run
    assert run.returncode != 0
    assert _branch(bare) == ""


@private_only
def test_a_record_left_out_of_the_commit_stops_the_push(tmp_path):
    """An ignore rule in the snapshot drops the file from the commit silently, the
    first time: once the file is tracked in the public repository git keeps adding it."""
    bare = _public_repo(tmp_path, with_record=False)
    _archive(tmp_path, {**SNAPSHOT_FILES, ".gitignore": "release-*.json\n"})
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path)
    assert run.returncode != 0
    assert "missing from the release commit" in run.stdout
    assert _branch(bare) == ""


@private_only
def test_a_snapshot_that_brings_its_own_evidence_is_refused(tmp_path):
    bare = _public_repo(tmp_path)
    _archive(tmp_path, {**SNAPSHOT_FILES, "release-evidence/sbom-scf-backend.cdx.json": "{}\n"})
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path)
    assert run.returncode != 0
    assert "the snapshot already contains release-evidence/" in run.stdout
    assert _branch(bare) == ""


@private_only
def test_without_the_evidence_nothing_is_pushed(tmp_path):
    bare = _public_repo(tmp_path)
    _archive(tmp_path, SNAPSHOT_FILES)
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path, evidence=False)  # the artifact was not downloaded
    assert run.returncode != 0
    assert _branch(bare) == ""


@private_only
@pytest.mark.parametrize("ignore, message", [
    ("release-evidence/image-scan-gate.json\n", "is not the set of files the scan job produced"),
    ("release-evidence/\n", "is not the set of files the scan job produced"),
], ids=["one file ignored", "the directory ignored"])
def test_evidence_left_out_of_the_commit_stops_the_push(tmp_path, ignore, message):
    bare = _public_repo(tmp_path)
    _archive(tmp_path, {**SNAPSHOT_FILES, ".gitignore": ignore})
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path)
    assert run.returncode != 0
    assert message in run.stdout
    assert _branch(bare) == ""


@private_only
def test_evidence_that_changes_on_the_way_into_the_commit_stops_the_push(tmp_path):
    """A clean/smudge or eol rule in the snapshot's .gitattributes rewrites a file as
    it is committed: the committed bytes must be the scan job's bytes."""
    bare = _public_repo(tmp_path)
    _archive(tmp_path, {**SNAPSHOT_FILES, ".gitattributes": "release-evidence/*.json text eol=crlf\n"})
    _evidence(tmp_path, {**EVIDENCE, "image-scan-gate.json": '{"blocked": false,\r\n"findings": [], "missing": []}\r\n'})
    assert _record(tmp_path).returncode == 0
    run = _push(tmp_path)
    assert run.returncode != 0
    assert "differs from the scan job's file" in run.stdout
    assert _branch(bare) == ""


# --- What keeps the file out of builds and out of the source tree ------------------------

def test_the_repository_does_not_ignore_the_record(tmp_path):
    """The shipped .gitignore becomes the public repository's: a rule matching
    the file would be the case above."""
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q")
    shutil.copy(REPO_ROOT / ".gitignore", work / ".gitignore")
    ignored = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", RECORD], cwd=work,
        env={"PATH": os.environ["PATH"], "HOME": str(work), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
    )
    assert ignored.returncode == 1, "release-images.json matches a .gitignore rule"
    for name in EVIDENCE:
        ignored = subprocess.run(
            ["git", "check-ignore", "-q", "--no-index", f"release-evidence/{name}"], cwd=work,
            env={"PATH": os.environ["PATH"], "HOME": str(work), "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"},
        )
        assert ignored.returncode == 1, f"release-evidence/{name} matches a .gitignore rule"


def test_the_record_is_kept_out_of_every_docker_build_context():
    lines = [line.strip() for line in (REPO_ROOT / ".dockerignore").read_text().splitlines()]
    assert RECORD in lines
    assert f"!{RECORD}" not in lines and "!*.json" not in lines
    assert "release-evidence" in lines and not any(line.startswith("!release-evidence") for line in lines)


@private_only
@pytest.mark.parametrize("name", [RECORD, "release-evidence"])
@pytest.mark.parametrize("present, expected", [(True, "1"), (False, "0")])
def test_the_source_tree_cannot_supply_a_record(tmp_path, name, present, expected):
    text = PREPARE_OSS.read_text()
    tail = text[text.index('echo "==> Safety assertions'):]
    lines = [line for line in tail.splitlines() if line.startswith(f"[ -e {name} ] && ")]
    assert len(lines) == 1, f"the safety assertions do not forbid {name}"
    assert tail.index(lines[0]) < tail.index('if [ "$fail" -ne 0 ]; then')
    # The line itself, run in a directory that does or does not hold the file.
    if present:
        (tmp_path / name).mkdir() if name == "release-evidence" else (tmp_path / name).write_text("{}")
    run = subprocess.run(
        ["bash", "-c", f'fail=0\n{lines[0]}\necho "fail=$fail"'],
        cwd=tmp_path, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True, timeout=30,
    )
    assert run.stdout.strip().splitlines()[-1] == f"fail={expected}"
    allow = (REPO_ROOT / "scripts" / "oss-toplevel-allow.txt").read_text().splitlines()
    assert name not in [line.split("#", 1)[0].strip() for line in allow]
