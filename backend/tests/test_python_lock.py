"""The Python dependencies are installed from hash-locked files, and only from them.

Each ``.in`` file says what the project asks for. The ``.txt`` beside it is
compiled by ``scripts/lock-python-deps.sh`` and pins every package, direct and
transitive, to one version and its sha256 hashes. The images and CI install
the ``.txt`` files with ``pip install --require-hashes``.

These tests keep five things true without a network:

* every entry in a lock is one exact version with hashes;
* a lock pins each package once;
* a lock covers what its ``.in`` file asks for;
* the Dockerfiles and the CI job install from the locks, with hash checking,
  and name no package on the command line;
* the CI job that recompiles the locks cannot be skipped or allowed to fail.

Whether a lock is exactly what a recompile produces is not checked here: that
needs PyPI, and is the "Python Lock" job in ``ci.yml``.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[2]
CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"
UV_REQUIREMENTS = REPO_ROOT / ".github" / "requirements" / "uv.txt"
LOCK_SCRIPT = REPO_ROOT / "scripts" / "lock-python-deps.sh"

# (input, lock), relative to the repository root.
LOCKS = [
    ("backend/requirements.in", "backend/requirements.txt"),
    ("backend/requirements-dev.in", "backend/requirements-dev.txt"),
    ("scripts/requirements-importer.in", "scripts/requirements-importer.txt"),
]
RUNTIME_LOCK = "backend/requirements.txt"
DEV_LOCK = "backend/requirements-dev.txt"

# The one package PyPI publishes as source only. Everything else is a wheel.
BUILT_FROM_SOURCE = {"pyclamd"}

HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")
PIP_FLAGS_WITH_A_VALUE = {"-r", "--only-binary", "--no-binary"}
PIP_FLAGS_ALLOWED = {"--no-cache-dir", "--require-hashes", "--no-build-isolation"}

pytestmark = pytest.mark.skipif(
    not (CI.exists() and (REPO_ROOT / RUNTIME_LOCK).exists()),
    reason="the repository root is not in this tree",
)


def _logical_lines(text: str) -> list[str]:
    """Lines with backslash continuations joined and comments dropped."""
    joined = re.sub(r"\\\n", " ", text)
    lines = []
    for line in joined.splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            lines.append(" ".join(line.split()))
    return lines


def _lock_entries(path: str) -> list[tuple[str, Version, str]]:
    """(name, version, the whole logical line) for each entry in a lock."""
    entries = []
    for line in _logical_lines((REPO_ROOT / path).read_text()):
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)", line)
        assert match, f"{path}: not a `name==version` pin: {line[:80]}"
        entries.append((canonicalize_name(match.group(1)), Version(match.group(2)), line))
    return entries


def _asked_for(path: str) -> list[Requirement]:
    """The requirements an ``.in`` file lists, without its `-c` and `-r` lines."""
    wanted = []
    for line in (REPO_ROOT / path).read_text().splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith(("#", "-")):
            wanted.append(Requirement(line))
    return wanted


@pytest.mark.parametrize("path", [lock for _, lock in LOCKS])
def test_every_lock_entry_is_one_version_with_hashes(path):
    entries = _lock_entries(path)
    assert entries, f"{path} pins nothing"
    for name, _, line in entries:
        assert HASH.search(line), f"{path}: {name} has no sha256 hash"
        # A URL or a version-control reference is not covered by a PyPI hash.
        assert "://" not in line and " @ " not in line, f"{path}: {name} is not a registry pin"


@pytest.mark.parametrize("path", [lock for _, lock in LOCKS])
def test_no_lock_pins_a_package_twice(path):
    # The locks resolve for Python 3.11 and every later version. When the newest
    # release of a package has dropped 3.11, uv pins the package twice: an older
    # version for 3.11 and a newer one for the rest, each with a marker. The
    # images only ever install the first. Dependabot cannot update a lock that
    # pins a package twice: every update to that lock fails, including updates
    # to unrelated packages.
    versions: dict[str, list[str]] = {}
    for name, version, _ in _lock_entries(path):
        versions.setdefault(name, []).append(str(version))
    twice = {name: found for name, found in versions.items() if len(found) > 1}
    assert not twice, (
        f"{path} pins {twice}. Cap each package in the .in file at the last release line that "
        "supports Python 3.11 (see the block at the end of backend/requirements.in), add the "
        "matching ignore rule to .github/dependabot.yml if this tree has one, and run scripts/lock-python-deps.sh"
    )


@pytest.mark.parametrize("source, lock", LOCKS)
def test_the_lock_covers_what_its_input_asks_for(source, lock):
    pinned: dict[str, list[Version]] = {}
    for name, version, _ in _lock_entries(lock):
        pinned.setdefault(name, []).append(version)
    wanted = _asked_for(source)
    assert wanted, f"{source} asks for nothing"
    for requirement in wanted:
        name = canonicalize_name(requirement.name)
        assert name in pinned, f"{source} asks for {name}; {lock} does not pin it. Run scripts/lock-python-deps.sh"
        assert any(
            requirement.specifier.contains(version, prereleases=True) for version in pinned[name]
        ), f"{lock} pins {name} {pinned[name]}, outside `{requirement}`. Run scripts/lock-python-deps.sh"


@pytest.mark.parametrize("source, lock", LOCKS)
def test_the_lock_records_the_command_that_compiles_it(source, lock):
    # Dependabot reads these options from the header when it updates a lock,
    # and the script's check compares the header too.
    header = (REPO_ROOT / lock).read_text().splitlines()[1]
    expected = (
        f"uv pip compile {Path(source).name} --universal --python-version 3.11 "
        f"--generate-hashes -o {Path(lock).name}"
    )
    assert header.strip("# ").strip() == expected


def test_the_two_backend_locks_agree_on_shared_packages():
    runtime = {name: version for name, version, _ in _lock_entries(RUNTIME_LOCK)}
    for name, version, _ in _lock_entries(DEV_LOCK):
        if name in runtime:
            assert version == runtime[name], f"{name}: {DEV_LOCK} has {version}, {RUNTIME_LOCK} has {runtime[name]}"


def test_ci_has_a_pinned_build_backend_for_the_source_only_package():
    # CI installs the dev lock first; its setuptools is what builds pyclamd.
    assert "setuptools" in {name for name, _, _ in _lock_entries(DEV_LOCK)}


def _pip_installs(script: str) -> list[list[str]]:
    """The argument list of every `pip install` in a shell script."""
    installs = []
    for line in _logical_lines(script):
        words = shlex.split(line)
        if "pip" in words and "install" in words:
            installs.append(words[words.index("install") + 1 :])
        else:
            assert "pip install" not in line, f"unparsed pip install: {line}"
    return installs


def _check_install(arguments: list[str], where: str) -> dict[str, list[str]]:
    """Assert an install takes packages only from lock files, with hashes."""
    values: dict[str, list[str]] = {flag: [] for flag in PIP_FLAGS_WITH_A_VALUE}
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word in PIP_FLAGS_WITH_A_VALUE:
            values[word].append(arguments[index + 1])
            index += 2
            continue
        # Anything else is either a known flag or a package named on the
        # command line, which the hashes in the lock would not cover.
        assert word in PIP_FLAGS_ALLOWED, f"{where}: unexpected `{word}` in pip install"
        index += 1
    assert "--require-hashes" in arguments, f"{where}: pip install without --require-hashes"
    assert values["--only-binary"] == [":all:"], f"{where}: pip install must be wheels-only"
    assert values["-r"], f"{where}: pip install without a lock file"
    assert set(values["--no-binary"]) <= BUILT_FROM_SOURCE, f"{where}: {values['--no-binary']} built from source"
    if values["--no-binary"]:
        # Without this pip downloads a build backend, unpinned, at install time.
        assert "--no-build-isolation" in arguments, f"{where}: source build with a downloaded build backend"
    return values


def _dockerfile_run_lines(name: str) -> str:
    text = re.sub(r"\\\n", " ", (REPO_ROOT / name).read_text())
    return "\n".join(line[4:] for line in text.splitlines() if line.startswith("RUN "))


def test_the_backend_image_installs_from_its_lock():
    installs = _pip_installs(_dockerfile_run_lines("Dockerfile.backend"))
    assert len(installs) == 1, installs
    values = _check_install(installs[0], "Dockerfile.backend")
    assert values["-r"] == ["requirements.txt"]
    assert values["--no-binary"] == ["pyclamd"]


def test_the_importer_image_installs_from_its_lock():
    installs = _pip_installs(_dockerfile_run_lines("Dockerfile.importer"))
    assert len(installs) == 1, installs
    values = _check_install(installs[0], "Dockerfile.importer")
    assert values["-r"] == ["/app/requirements-importer.txt"]
    assert values["--no-binary"] == []


def _job_script(job: str) -> str:
    steps = yaml.safe_load(CI.read_text())["jobs"][job]["steps"]
    return "\n".join(step.get("run", "") for step in steps)


def test_the_ci_test_job_installs_only_from_the_locks():
    installs = _pip_installs(_job_script("python"))
    assert installs, "the Python job no longer installs anything"
    files = []
    for arguments in installs:
        files += _check_install(arguments, "ci.yml python job")["-r"]
    # Dev tools first: that lock carries the setuptools that builds pyclamd.
    assert files == [DEV_LOCK, RUNTIME_LOCK]


def test_the_lock_check_job_runs_on_every_trigger_and_can_fail():
    job = yaml.safe_load(CI.read_text())["jobs"]["python-lock"]
    # A job with `if:` or `needs:` can be skipped, and a skipped job passes.
    assert "if" not in job and "needs" not in job and "continue-on-error" not in job
    for step in job["steps"]:
        assert "continue-on-error" not in step and "if" not in step, step.get("name")
    script = _job_script("python-lock")
    assert "scripts/lock-python-deps.sh --check" in script
    installs = _pip_installs(script)
    assert len(installs) == 1, installs
    assert _check_install(installs[0], "ci.yml python-lock job")["-r"] == [".github/requirements/uv.txt"]


def test_the_uv_requirements_file_locks_one_version_by_hash():
    lines = _logical_lines(UV_REQUIREMENTS.read_text())
    assert len(lines) == 1, "uv has no Python dependencies: one entry is the whole install"
    words = lines[0].split()
    assert re.fullmatch(r"uv==\d+\.\d+\.\d+", words[0]), words[0]
    hashes = words[1:]
    assert hashes, "no hashes: pip would refuse the install"
    for entry in hashes:
        assert HASH.fullmatch(entry), entry
    assert len(set(hashes)) == len(hashes), "the same hash is listed twice"


def test_the_lock_script_compiles_every_lock_these_tests_know():
    script = LOCK_SCRIPT.read_text()
    for source, lock in LOCKS:
        directory, name = source.split("/")
        assert f'"{directory}:{name}:{Path(lock).name}"' in script, f"{lock} is not in scripts/lock-python-deps.sh"
