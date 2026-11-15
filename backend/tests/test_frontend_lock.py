"""The frontend installs from one lockfile, and no install can change it.

``webclient/package-lock.json`` is the frontend's lockfile. CI and both
frontend images install from it with ``npm ci``, which installs exactly what
the lockfile pins and stops if ``package.json`` disagrees with it.

These tests keep five things true without a network:

* there is one committed lockfile in ``webclient/``, not two that can drift;
* the lockfile agrees with ``package.json`` and takes every package from the
  npm registry with an integrity hash;
* the lockfile lists every optional platform package, so ``npm ci`` finds the
  right native binary on any platform and nothing has to be added afterwards;
* CI and the Dockerfiles only run installs that cannot change the lockfile;
* the type check, the build and the tests in CI are commands that can fail.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WEBCLIENT = REPO_ROOT / "webclient"
PACKAGE_JSON = WEBCLIENT / "package.json"
PACKAGE_LOCK = WEBCLIENT / "package-lock.json"
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
FRONTEND_DOCKERFILES = ["Dockerfile.frontend", "Dockerfile.frontend.dev"]

# Lockfiles of other package managers, and the one npm file that overrides
# package-lock.json when it is present.
OTHER_LOCKFILES = ["bun.lock", "bun.lockb", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json"]

DEPENDENCY_SECTIONS = ["dependencies", "devDependencies", "optionalDependencies", "peerDependencies"]

# What the CI job must run, each as a whole line of its own. A line such as
# `npm test --if-present` or `npm run type-check || true` is not one of these.
CI_COMMANDS = ["npm ci", "npm run type-check", "npm run build", "npm test"]

# The only package-manager commands a frontend Dockerfile may run.
DOCKERFILE_COMMANDS = {"npm ci", "npm cache clean --force", "npm run build"}

PACKAGE_MANAGER_COMMAND = re.compile(r"(?<![\w./-])((?:npm|npx|bun|bunx|yarn|pnpm)\s[^;&|]*)")

pytestmark = pytest.mark.skipif(
    not (PACKAGE_LOCK.exists() and CI.exists()),
    reason="the frontend and the workflows are not in this tree",
)


def _lock() -> dict:
    return json.loads(PACKAGE_LOCK.read_text())


def _committed(path: Path) -> bool:
    """Whether a file is part of the repository, not just present on disk.

    A developer may have an ignored ``bun.lock`` from a local ``bun install``.
    In a git checkout the question is put to git; anywhere else (an unpacked
    release archive) a file that is present is a file that shipped.
    """
    git = shutil.which("git")
    if git and (REPO_ROOT / ".git").exists():
        listed = subprocess.run(
            [git, "ls-files", "--", str(path.relative_to(REPO_ROOT))],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(listed.stdout.strip())
    return path.exists()


def _logical_lines(script: str) -> list[str]:
    """Lines with backslash continuations joined, comments and blanks dropped."""
    lines = []
    for line in re.sub(r"\\\n", " ", script).splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            lines.append(" ".join(line.split()))
    return lines


def _package_manager_commands(script: str) -> list[str]:
    commands = []
    for line in _logical_lines(script):
        commands += [" ".join(found.split()) for found in PACKAGE_MANAGER_COMMAND.findall(line)]
    return commands


@pytest.mark.parametrize("name", OTHER_LOCKFILES)
def test_package_lock_is_the_only_committed_frontend_lockfile(name):
    assert not _committed(WEBCLIENT / name), (
        f"webclient/{name} is committed. CI and the images install from package-lock.json; "
        "a second lockfile is one nothing checks and nothing keeps in step."
    )


def test_the_lock_agrees_with_package_json():
    package = json.loads(PACKAGE_JSON.read_text())
    root = _lock()["packages"][""]
    for section in DEPENDENCY_SECTIONS:
        assert root.get(section, {}) == package.get(section, {}), (
            f"{section} differ between package.json and package-lock.json. "
            "Fix: (cd webclient && npm install --package-lock-only)"
        )


def test_every_locked_package_comes_from_the_registry_with_a_hash():
    lock = _lock()
    assert lock["lockfileVersion"] == 3
    entries = {path: entry for path, entry in lock["packages"].items() if path}
    assert entries, "the lockfile pins nothing"
    for path, entry in entries.items():
        assert not entry.get("link"), f"{path} is a link to a local directory"
        assert str(entry.get("resolved", "")).startswith("https://registry.npmjs.org/"), (
            f"{path} is not taken from the npm registry: {entry.get('resolved')!r}"
        )
        assert str(entry.get("integrity", "")).startswith("sha512-"), f"{path} has no sha512 integrity hash"


def test_every_optional_platform_package_is_in_the_lock():
    # npm has written lockfiles that list only the optional packages for the
    # machine that generated them (https://github.com/npm/cli/issues/4828).
    # `npm ci` from such a file leaves Rollup or esbuild without a native
    # binary on every other platform. CI used to patch that at run time with
    # an `npm install`; this check takes its place.
    packages = _lock()["packages"]
    locked = {path.rsplit("node_modules/", 1)[-1] for path in packages if path}
    missing = sorted(
        {
            f"{optional} (wanted by {path.rsplit('node_modules/', 1)[-1] or 'the project'})"
            for path, entry in packages.items()
            for optional in entry.get("optionalDependencies", {})
            if optional not in locked
        }
    )
    assert not missing, (
        f"package-lock.json does not list {missing}. Regenerate it from a clean state: "
        "(cd webclient && rm -rf node_modules package-lock.json && npm install --package-lock-only)"
    )


def test_the_type_check_script_runs_the_compiler():
    package = json.loads(PACKAGE_JSON.read_text())
    assert package["scripts"].get("type-check") == "tsc --noEmit"
    assert "typescript" in package.get("devDependencies", {})
    options = json.loads((WEBCLIENT / "tsconfig.json").read_text())["compilerOptions"]
    assert options.get("strict") is True, "tsconfig.json no longer type-checks in strict mode"


def _frontend_job() -> dict:
    return yaml.safe_load(CI.read_text())["jobs"]["typescript"]


def test_the_ci_job_runs_install_type_check_build_and_tests_as_commands_that_can_fail():
    job = _frontend_job()
    assert "continue-on-error" not in job
    lines = []
    for step in job["steps"]:
        assert "continue-on-error" not in step and "if" not in step, step.get("name")
        lines += _logical_lines(step.get("run", ""))
    for command in CI_COMMANDS:
        assert command in lines, f"the frontend job does not run `{command}` as a line of its own"
    assert lines.index("npm ci") < lines.index("npm run type-check") < lines.index("npm run build")


def test_the_ci_job_runs_no_other_package_manager_command():
    # `npm install` rewrites the lockfile, `npx` downloads whatever is newest,
    # and `--if-present` passes when the script is missing.
    script = "\n".join(step.get("run", "") for step in _frontend_job()["steps"])
    assert sorted(_package_manager_commands(script)) == sorted(CI_COMMANDS)


def test_the_ci_job_installs_with_npm_and_caches_on_the_lockfile():
    setup = [step for step in _frontend_job()["steps"] if str(step.get("uses", "")).startswith("actions/setup-node@")]
    assert len(setup) == 1
    assert setup[0]["with"]["cache-dependency-path"] == "./webclient/package-lock.json"


@pytest.mark.parametrize("name", [name for name in FRONTEND_DOCKERFILES if (REPO_ROOT / name).exists()])
def test_the_frontend_images_only_run_a_frozen_install(name):
    text = re.sub(r"\\\n", " ", (REPO_ROOT / name).read_text())
    run_lines = "\n".join(line[4:] for line in text.splitlines() if line.startswith("RUN "))
    commands = _package_manager_commands(run_lines)
    assert "npm ci" in commands, f"{name} does not install with `npm ci`"
    unexpected = sorted(set(commands) - DOCKERFILE_COMMANDS)
    assert not unexpected, f"{name} runs {unexpected}; only {sorted(DOCKERFILE_COMMANDS)} are allowed"


def test_the_production_frontend_dockerfile_is_in_this_tree():
    assert (REPO_ROOT / "Dockerfile.frontend").exists()
