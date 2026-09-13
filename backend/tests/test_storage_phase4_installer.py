"""Phase 4 — the compose topology, the bundled row, and the one writer of it.

ISA 20260912-0930, criteria 33, 34, 36 and 28. The installer package's own
criteria (31, 32, 37) are in ``tests/installer/test_installer_storage.py``.

Four things are asserted here that a reading of the diff would not settle:

**The compose file is parsed, not grepped.** ``profiles: [storage]`` under the
wrong service, or a ``depends_on`` that survives two levels down, is invisible
to a substring search and fatal in practice.

**Nothing may depend on a profiled service.** Compose starts a depended-on
service *even when its profile is inactive*, so a single surviving
``depends_on: minio`` anywhere in the file would make ``--no-minio`` start
MinIO anyway and the whole phase would be theatre. The sweep is over every
service, not over the three we happen to have changed.

**The seed is tested against a session double, not a database.** What matters
is the decision — seed, or leave alone — and the exact column values written,
both of which are properties of the function rather than of Postgres.

**The one-writer rule is an AST sweep.** ``is_bundled`` exempts a row from the
loopback, RFC1918, CGNAT and ``.local`` address refusals and permits ``http``.
A test naming the functions that may write it fails when a *new* writer appears,
which a test of the existing writers never would.
"""
from __future__ import annotations

import ast
import base64
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
import yaml

import catalog_models  # noqa: F401  (mapper registry; see phase 3 tests)
from models import EvidenceStorageConfig
from services import crypto, evidence_storage_admin, storage_config

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
SECRETS_OVERLAY_PATH = REPO_ROOT / "docker-compose.secrets.yml"
MINIO_INIT_SCRIPT = REPO_ROOT / "scripts" / "docker" / "minio-init.sh"

STORAGE_PROFILE = "storage"
STORAGE_SERVICES = ("minio", "minio-init")


@pytest.fixture(autouse=True)
def compose_dns():
    """A deterministic resolver for the compose-internal hostname.

    ``minio`` is a compose service name: it resolves inside the stack's network
    and nowhere else, so leaving this to ambient DNS makes the seed tests depend
    on whether the developer's resolver invents an answer for an unknown name.
    Pinned to a private address, which is what the real one is, and restored
    afterwards — a global left mutated is how a test file poisons the ones that
    run after it.
    """
    storage_config.use_address_resolver(
        lambda host: ["172.18.0.4"] if host == "minio" else ["93.184.216.34"]
    )
    yield
    storage_config.use_address_resolver(None)


@pytest.fixture(scope="module")
def compose() -> Dict[str, Any]:
    return yaml.safe_load(COMPOSE_PATH.read_text())


@pytest.fixture(scope="module")
def overlay() -> Dict[str, Any]:
    return yaml.safe_load(SECRETS_OVERLAY_PATH.read_text())


def _depends_on(service: Dict[str, Any]) -> List[str]:
    """The names a service depends on, in either compose syntax."""
    raw = service.get("depends_on") or {}
    if isinstance(raw, list):
        return list(raw)
    return list(raw)


# ---------------------------------------------------------------------------
# Criterion 33 — the object store is optional
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", STORAGE_SERVICES)
def test_the_object_store_services_are_behind_the_storage_profile(compose, name):
    """Criterion 33.

    A profile rather than a commented-out block or an empty-credential guard,
    because the guard is what makes the other approaches impossible: `minio`
    refuses to boot on an empty MINIO_ROOT_USER (correctly — the alternative is
    a silent fall back to the built-in minioadmin), and the secrets overlay
    refuses to `config` at all when a declared secret file is missing. Not
    starting the service is the only shape that works.
    """
    service = compose["services"][name]
    assert service.get("profiles") == [STORAGE_PROFILE], (
        f"{name} must carry exactly the '{STORAGE_PROFILE}' profile"
    )


def test_no_service_anywhere_depends_on_the_object_store(compose):
    """**The trap this whole phase turns on.**

    ``depends_on`` starts the named service even when that service's profile is
    inactive. One surviving reference — in backend, in a worker, in something
    added later — and ``--no-minio`` starts MinIO anyway while reporting
    success. Nothing is lost by removing it: minio declares no healthcheck, so
    the dependency was only ever `service_started`, an ordering hint rather than
    a readiness gate, and minio-init's own bounded retry is what actually waits.
    """
    offenders = {
        name: _depends_on(service)
        for name, service in compose["services"].items()
        if name not in STORAGE_SERVICES
        and any(dep in STORAGE_SERVICES for dep in _depends_on(service))
    }
    assert offenders == {}, (
        "these services would start MinIO even with the storage profile off: "
        f"{offenders}"
    )


def test_minio_init_still_depends_on_minio(compose):
    """Within the profile the ordering still matters, and is still allowed:
    both services carry the same profile, so neither can drag the other in."""
    assert "minio" in _depends_on(compose["services"]["minio-init"])


def test_the_backend_is_told_which_store_the_installer_provisioned(compose):
    """Criterion 34, the half that the compose profile cannot carry.

    ``COMPOSE_PROFILES`` is consumed by the docker CLI on the host and is never
    forwarded into a container, so the backend has no way to see whether the
    bundled MinIO is part of its own stack. This variable is that signal.
    """
    env = compose["services"]["backend"]["environment"]
    assert "EVIDENCE_STORAGE_BOOTSTRAP" in env


def test_the_bootstrap_signal_defaults_to_empty_not_to_bundled(compose):
    """The default is the safe end, deliberately.

    Defaulting to ``bundled_minio`` would make every existing install that has
    never run the installer seed a platform-scope storage row on its next boot —
    silently redirecting where evidence goes. The value arrives explicitly, from
    the installer or from scripts/upgrade.sh, or not at all.
    """
    value = compose["services"]["backend"]["environment"]["EVIDENCE_STORAGE_BOOTSTRAP"]
    assert value == "${EVIDENCE_STORAGE_BOOTSTRAP:-}"


def test_the_shared_minio_volume_still_has_its_fixed_name(compose):
    """Not a Phase 4 change — a Phase 4 guard.

    The volume is named rather than project-prefixed, so every compose project
    on the machine shares it. A throwaway acceptance stack that mounted it would
    be writing into the developer's real evidence store.
    """
    assert compose["volumes"]["minio_data"]["name"] == "cg-scf-minio-data"


# ---------------------------------------------------------------------------
# Criterion 28 — the scoped account
# ---------------------------------------------------------------------------


def test_the_provisioning_logic_lives_in_one_file_used_by_both_paths(compose, overlay):
    """The base entrypoint and the secrets overlay's full replacement must run
    the same script. Two copies of a credential-provisioning script drift, and
    the drift is invisible until an install takes the other path."""
    assert MINIO_INIT_SCRIPT.exists()

    base_entry = " ".join(compose["services"]["minio-init"]["entrypoint"])
    assert "/scf/minio-init.sh" in base_entry

    mounts = compose["services"]["minio-init"].get("volumes") or []
    assert any("minio-init.sh:/scf/minio-init.sh:ro" in m for m in mounts)

    overlay_entry = " ".join(str(p) for p in overlay["services"]["minio-init"]["entrypoint"])
    assert "/scf/minio-init.sh" in overlay_entry


def test_the_overlay_reads_the_application_pair_as_well_as_the_root_pair(overlay):
    """Criterion 28 on the secrets path. Four files, not two: the root pair to
    administer MinIO with, and the application pair to scope."""
    declared = overlay["services"]["minio-init"]["secrets"]
    for name in (
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
    ):
        assert name in declared, name


def test_minio_init_receives_the_application_credential_on_the_plain_path(compose):
    env = compose["services"]["minio-init"]["environment"]
    assert env["SCF_APP_ACCESS_KEY_ID"] == "${AWS_ACCESS_KEY_ID:-}"
    assert env["SCF_APP_SECRET_ACCESS_KEY"] == "${AWS_SECRET_ACCESS_KEY:-}"


def test_the_policy_names_the_evidence_bucket_and_grants_no_admin_action():
    """The policy is read out of the script and parsed as JSON, so that a
    resource that quietly became ``arn:aws:s3:::*`` fails here rather than in a
    penetration test."""
    text = MINIO_INIT_SCRIPT.read_text()
    start = text.index("{\n  \"Version\"")
    end = text.index("POLICY\n", start)
    body = text[start:end].replace("$BUCKET", "evidence")
    policy = __import__("json").loads(body)

    resources = [r for stmt in policy["Statement"] for r in stmt["Resource"]]
    assert set(resources) == {
        "arn:aws:s3:::evidence",
        "arn:aws:s3:::evidence/*",
    }

    actions = [a for stmt in policy["Statement"] for a in stmt["Action"]]
    assert all(a.startswith("s3:") for a in actions)
    assert not any("admin" in a.lower() for a in actions)
    assert not any(a == "s3:*" or a.endswith(":*") for a in actions), actions
    # Everything the S3 driver actually calls.
    for needed in ("s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"):
        assert needed in actions, needed


def test_an_upgraded_install_whose_app_key_is_the_root_user_is_skipped_not_failed():
    """UPGRADE SAFETY, stated where it is implemented.

    Installs provisioned before this phase have AWS_ACCESS_KEY_ID byte-identical
    to MINIO_ROOT_USER. ``mc admin user add`` on the root user fails, and this
    one-shot runs on the `up` path of a running install — failing there would
    stop a stack from coming up over a credential shape that is merely old.
    """
    text = MINIO_INIT_SCRIPT.read_text()
    assert 'if [ "$SCF_APP_ACCESS_KEY_ID" = "$SCF_ROOT_USER" ]; then' in text
    guard = text[text.index('if [ "$SCF_APP_ACCESS_KEY_ID" = "$SCF_ROOT_USER" ]'):]
    guard = guard[: guard.index("\nfi\n")]
    assert "exit 0" in guard
    assert "exit 1" not in guard


# ---------------------------------------------------------------------------
# Criterion 36 — the bundled platform row
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class FakeSession:
    """Enough AsyncSession for the seed: it adds, flushes, commits, refreshes."""

    def __init__(self, existing=None, flush_error: Optional[Exception] = None):
        self._existing = existing
        self._flush_error = flush_error
        self.added: List[Any] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, *_args, **_kwargs):
        return FakeResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        if self._flush_error is not None:
            raise self._flush_error
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _obj):
        return None


@pytest.fixture
def secret_key(monkeypatch):
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SCF_SECRET_KEY", key)
    crypto.reset()
    yield key
    crypto.reset()


@pytest.fixture
def bundled_env(monkeypatch, secret_key):
    monkeypatch.setenv("EVIDENCE_STORAGE_BOOTSTRAP", "bundled_minio")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "scopedappuser00000001")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "scoped-app-secret")
    monkeypatch.setenv("EVIDENCE_BUCKET", "evidence")
    monkeypatch.setenv("EVIDENCE_PUBLIC_ENDPOINT", "http://localhost:9000")
    # The audit row goes through the platform audit trail, which wants a real
    # session; the decision under test is the seed, not the logging.
    monkeypatch.setattr(
        evidence_storage_admin, "_audit", _noop_audit, raising=True
    )
    yield


async def _noop_audit(*_args, **_kwargs):
    return None


@pytest.fixture
def no_version_bump(monkeypatch):
    calls: List[int] = []
    monkeypatch.setattr(storage_config, "bump_version", lambda: calls.append(1))
    return calls


@pytest.mark.asyncio
async def test_the_bundled_row_is_seeded_with_the_shape_the_resolver_needs(
    bundled_env, no_version_bump
):
    """Criterion 36, every column at once.

    Asserted as a whole rather than one property per test, because the row is
    only correct as a set: a platform-scope minio row with the right bucket but
    ``is_bundled=False`` is refused by the address policy, and one with
    ``is_bundled=True`` but ``status='draft'`` is never resolved at all.
    """
    session = FakeSession(existing=None)

    row = await evidence_storage_admin.seed_bundled_platform_config(session)

    assert row is not None
    assert row.organization_id is None            # platform scope
    assert row.provider == storage_config.PROVIDER_MINIO
    assert row.is_bundled is True
    assert row.status == storage_config.STATUS_ACTIVE
    assert row.endpoint_url == storage_config.BUNDLED_MINIO_ENDPOINT
    assert row.public_endpoint == "http://localhost:9000"
    assert row.bucket == "evidence"
    assert row.path_style is True
    assert row.sse_mode == "none"
    assert row.access_key_id == "scopedappuser00000001"
    assert session.committed is True


@pytest.mark.asyncio
async def test_the_stored_secret_is_ciphertext_that_round_trips(
    bundled_env, no_version_bump
):
    """Real encryption under a throwaway key, so this reads an actual Fernet
    token rather than a stub's return value."""
    row = await evidence_storage_admin.seed_bundled_platform_config(
        FakeSession(existing=None)
    )

    assert row.secret_ciphertext
    assert "scoped-app-secret" not in row.secret_ciphertext
    assert crypto.decrypt(row.secret_ciphertext) == "scoped-app-secret"


@pytest.mark.asyncio
async def test_seeding_announces_itself_to_every_other_process(
    bundled_env, no_version_bump
):
    """A Celery worker caches its resolved snapshot. Without the version bump it
    would keep resolving to the legacy environment for up to a minute after the
    row exists."""
    await evidence_storage_admin.seed_bundled_platform_config(FakeSession(existing=None))
    assert no_version_bump == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", ["", "none", "bundled", "BUNDLED_MINIO"])
async def test_nothing_is_seeded_unless_the_installer_said_bundled_minio(
    bundled_env, no_version_bump, monkeypatch, signal
):
    monkeypatch.setenv("EVIDENCE_STORAGE_BOOTSTRAP", signal)
    session = FakeSession(existing=None)

    assert await evidence_storage_admin.seed_bundled_platform_config(session) is None
    assert session.added == []
    assert no_version_bump == []


@pytest.mark.asyncio
async def test_an_existing_platform_row_is_never_written_over(
    bundled_env, no_version_bump
):
    """Any status, not only active. A retired or draft platform row is an
    operator's decision, and re-seeding over it would silently redirect evidence
    back to the bundled store."""
    session = FakeSession(existing=(uuid.uuid4(),))

    assert await evidence_storage_admin.seed_bundled_platform_config(session) is None
    assert session.added == []
    assert no_version_bump == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"])
async def test_no_row_is_written_without_a_credential_to_put_in_it(
    bundled_env, no_version_bump, monkeypatch, missing
):
    """A row with no credential resolves to a store nothing can write to, which
    is worse than no row: the legacy-environment fallback at least reports
    itself honestly as unconfigured (D42)."""
    monkeypatch.setenv(missing, "")
    session = FakeSession(existing=None)

    assert await evidence_storage_admin.seed_bundled_platform_config(session) is None
    assert session.added == []


@pytest.mark.asyncio
async def test_a_concurrent_seed_is_absorbed_rather_than_failing_the_boot(
    bundled_env, no_version_bump
):
    """Two replicas boot together; the partial unique index lets exactly one
    win. The loser must not take the application down with it."""
    from sqlalchemy.exc import IntegrityError

    session = FakeSession(
        existing=None, flush_error=IntegrityError("insert", {}, Exception("dup"))
    )

    assert await evidence_storage_admin.seed_bundled_platform_config(session) is None
    assert session.rolled_back is True
    assert session.committed is False
    assert no_version_bump == []


@pytest.mark.asyncio
async def test_the_seed_reads_its_credential_file_aware(
    bundled_env, no_version_bump, monkeypatch, tmp_path
):
    """The secrets overlay passes AWS_ACCESS_KEY_ID_FILE, not the value. Reading
    os.environ directly would seed an empty credential on exactly the installs
    that followed the hardening guidance."""
    key_file = tmp_path / "AWS_ACCESS_KEY_ID"
    key_file.write_text("fromthefile000000001\n")
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID_FILE", str(key_file))

    row = await evidence_storage_admin.seed_bundled_platform_config(
        FakeSession(existing=None)
    )

    assert row is not None
    assert row.access_key_id == "fromthefile000000001"


def test_the_seed_goes_through_the_provider_preset():
    """Not a second hand-written copy of the minio settings. Source-level,
    because the values it produces are also producible by accident."""
    source = (BACKEND_ROOT / "services" / "evidence_storage_admin.py").read_text()
    body = source[source.index("async def seed_bundled_platform_config"):]
    assert "config_from_preset" in body
    assert "validate_config_for_save" in body


# ---------------------------------------------------------------------------
# D36 — exactly one writer of is_bundled
# ---------------------------------------------------------------------------

#: The only function permitted to write a truthy ``is_bundled``.
THE_ONE_WRITER = "seed_bundled_platform_config"

#: Top-level entries under ``backend/`` that are NOT part of the application's
#: writable surface, each with the reason it is out. Everything else is swept.
#:
#: An allow-list of directories is how this oracle failed its first review: it
#: named ``"tasks"``, which is not a directory (the Celery task modules are
#: top-level ``tasks_*.py``), so an ``is_dir()`` guard skipped it silently, and
#: ``collectors/`` and ``middleware/`` were never listed at all. A planted
#: writer in any of the three passed. The surface is now everything minus a
#: justified deny-list, and ``test_the_sweep_covers_every_package_...`` below
#: fails when a new package appears that is in neither.
EXCLUDED: Dict[str, str] = {
    "tests": (
        "test code, never shipped or imported by the running app; the fixtures "
        "in this very file construct is_bundled=True rows on purpose"
    ),
    "alembic": (
        "one-shot migration revisions, applied by the operator at deploy time "
        "and unreachable from a request; a revision that set the flag would be "
        "a schema change under review, not a second runtime writer"
    ),
    "migrations": "the legacy migration directory, same reasoning as alembic",
    ".venv": (
        "third-party packages. Present in the main clone and absent in a git "
        "worktree, so sweeping it would make this oracle's verdict depend on "
        "which checkout happened to run it"
    ),
    "__pycache__": "compiled artefacts, not the source they were built from",
    "node_modules": "vendored javascript dependencies, never imported by Python",
    "fixtures-local": (
        "gitignored local scratch (.gitignore: backend/fixtures-local/) — "
        "absent from a fresh clone, so sweeping it would make the verdict "
        "depend on the developer's working tree"
    ),
}


def _swept_roots() -> List[Path]:
    """Every top-level entry under ``backend/`` that is part of the sweep.

    Top-level ``.py`` files included individually: ``tasks_assessment.py`` and
    friends live here, not in a package, which is exactly what the old
    directory allow-list missed.
    """
    return sorted(
        entry
        for entry in BACKEND_ROOT.iterdir()
        if entry.name not in EXCLUDED
        and (entry.is_dir() or entry.suffix == ".py")
    )


def _python_files() -> List[Path]:
    files: List[Path] = []
    for entry in _swept_roots():
        if entry.is_dir():
            files.extend(
                f
                for f in sorted(entry.rglob("*.py"))
                if not any(part in EXCLUDED for part in f.relative_to(BACKEND_ROOT).parts)
            )
        else:
            files.append(entry)
    return sorted(set(files))


def _is_literal_false(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value is False


#: The ORM model whose column is the thing being guarded. Constructing the
#: read-only value objects (``StoredConfigRow``) or the API response model from
#: a row that already has the flag is not a write of it, and flagging those
#: would make the oracle noisy enough that somebody would loosen it.
ORM_MODEL = "EvidenceStorageConfig"


def _called_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_bundled_writes(files: Optional[List[Path]] = None) -> List[tuple]:
    """Every place that writes a non-``False`` ``is_bundled`` onto an ORM row.

    Returned as ``(path, lineno, enclosing function name)``.

    Two spellings are writes: a keyword argument to ``EvidenceStorageConfig(...)``
    or to a ``.values(...)`` update, and an attribute assignment
    ``row.is_bundled = ...``. A literal ``False`` is not a write in the sense
    that matters — ``create_config`` pins it there on purpose, and that pinning
    is itself part of the guarantee.
    """
    writes: List[tuple] = []

    for path in files if files is not None else _python_files():
        tree = ast.parse(path.read_text())

        # Map each interesting node to the innermost function containing it, in
        # one pass over the same tree the nodes came from. Re-parsing to look a
        # node up again would compare objects from two different trees and match
        # nothing, which is how an oracle silently passes for ever.
        enclosing: Dict[int, str] = {}

        def walk(node: ast.AST, current: Optional[str]) -> None:
            for child in ast.iter_child_nodes(node):
                name = current
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = child.name
                enclosing[id(child)] = name or "<module>"
                walk(child, name)

        enclosing[id(tree)] = "<module>"
        walk(tree, None)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _called_name(node) in (ORM_MODEL, "values"):
                for kw in node.keywords:
                    if kw.arg == "is_bundled" and not _is_literal_false(kw.value):
                        writes.append((path, node.lineno, enclosing.get(id(node), "<module>")))
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Attribute)
                        and target.attr == "is_bundled"
                        and not _is_literal_false(node.value)
                    ):
                        writes.append((path, node.lineno, enclosing.get(id(node), "<module>")))
    return writes


def _raw_sql_touching_is_bundled() -> List[str]:
    """The escape hatch the AST sweep cannot see: a hand-written statement.

    ``text("UPDATE evidence_storage_configs SET is_bundled = true")`` is a
    perfectly good second writer and is invisible to every check above.
    """
    offenders = []
    for path in _python_files():
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            sql = node.value.upper()
            if "IS_BUNDLED" in sql and ("UPDATE " in sql or "INSERT " in sql):
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}")
    return offenders


def test_only_the_seed_function_ever_writes_is_bundled():
    """D36, as a sweep rather than a review.

    ``is_bundled`` is the single flag that exempts a row from the tenant half of
    the address policy: the loopback, RFC1918, CGNAT and ``.local`` refusals and
    the ban on ``http``. A second writer reachable from a request would be a
    request that can point the backend at the operator's own network, which is
    ISA section 7's threat exactly. A test listing today's writers would keep
    passing when a new one appeared; this one fails on the appearance.
    """
    offenders = [
        f"{path.relative_to(BACKEND_ROOT)}:{lineno} in {function}"
        for path, lineno, function in _is_bundled_writes()
        if function != THE_ONE_WRITER
    ]

    assert offenders == [], (
        "is_bundled must be written only by "
        f"{THE_ONE_WRITER}; also written at: {offenders}"
    )
    # And it is written SOMEWHERE, or the sweep is agreeing with itself.
    assert any(f == THE_ONE_WRITER for _p, _l, f in _is_bundled_writes())
    assert _raw_sql_touching_is_bundled() == []


def test_the_sweep_covers_every_package_that_could_hold_a_writer():
    """Exhaustiveness. The sweep is only worth its assertion if it sweeps.

    Every top-level entry under ``backend/`` that contains Python at all is
    either swept or named in ``EXCLUDED`` with a reason. A package added
    tomorrow is therefore covered by default, and a package deliberately left
    out has to say why in the diff that leaves it out.
    """
    unaccounted = []
    for entry in sorted(BACKEND_ROOT.iterdir()):
        if entry.name in EXCLUDED:
            continue
        has_python = entry.suffix == ".py" if entry.is_file() else (
            entry.is_dir() and any(entry.rglob("*.py"))
        )
        if has_python and entry not in _swept_roots():
            unaccounted.append(entry.name)

    assert unaccounted == [], (
        "these hold Python but are neither swept nor excluded with a reason: "
        f"{unaccounted}. Add them to the sweep, or to EXCLUDED with the reason."
    )
    # Every exclusion carries a non-trivial reason, so the deny-list cannot be
    # grown by appending a bare name.
    assert all(len(reason) > 20 for reason in EXCLUDED.values()), EXCLUDED


def test_the_sweep_reaches_the_places_it_once_missed():
    """The regression pin for the first review's finding.

    Planted ``is_bundled=True`` writers in a top-level ``tasks_*.py``, in
    ``collectors/`` and in ``middleware/`` were all missed by the directory
    allow-list this replaced. Naming real files here fails loudly if the sweep
    is ever narrowed back.
    """
    swept = set(_python_files())
    must_reach = [
        BACKEND_ROOT / "main.py",
        BACKEND_ROOT / "tasks.py",
        BACKEND_ROOT / "tasks_assessment.py",
        BACKEND_ROOT / "models.py",
        BACKEND_ROOT / "collectors" / "registry.py",
        BACKEND_ROOT / "middleware" / "audit_middleware.py",
        BACKEND_ROOT / "cli" / "admin.py",
        BACKEND_ROOT / "api" / "evidence_storage.py",
        BACKEND_ROOT / "services" / "evidence_storage_admin.py",
        BACKEND_ROOT / "installer" / "writer.py",
    ]
    missing = [str(f.relative_to(BACKEND_ROOT)) for f in must_reach if f not in swept]
    assert missing == [], f"the sweep no longer reaches: {missing}"

    # Every top-level tasks_*.py, not just the one named above — these are the
    # modules the dead ``"tasks"`` directory entry was meant to cover.
    task_modules = sorted(BACKEND_ROOT.glob("tasks_*.py"))
    assert task_modules, "expected top-level tasks_*.py modules"
    assert all(m in swept for m in task_modules)

    # And the exclusions really are excluded, or the sweep would flag the
    # deliberate is_bundled=True rows in this file's own fixtures.
    assert not any(
        "tests" in f.relative_to(BACKEND_ROOT).parts for f in swept
    )


def test_the_oracle_would_actually_catch_a_second_writer(tmp_path, monkeypatch):
    """The sweep's own positive control.

    An AST oracle that silently matched nothing would pass this suite for ever
    while proving nothing. This plants a second writer in a swept directory and
    asserts the sweep names it.
    """
    planted = tmp_path / "_phase4_oracle_probe.py"
    planted.write_text(
        "from models import EvidenceStorageConfig\n"
        "def sneaky():\n"
        "    row = EvidenceStorageConfig(is_bundled=True)\n"
        "    row.is_bundled = True\n"
        "    return row\n"
    )

    found = _is_bundled_writes([planted])

    assert sorted((lineno, function) for _path, lineno, function in found) == [
        (3, "sneaky"),
        (4, "sneaky"),
    ], found
    # And the harmless spellings are NOT flagged, or the sweep is a blanket ban
    # on the identifier rather than a rule about writing it.
    harmless = tmp_path / "_phase4_oracle_harmless.py"
    harmless.write_text(
        "from models import EvidenceStorageConfig\n"
        "def pinned():\n"
        "    return EvidenceStorageConfig(is_bundled=False)\n"
        "def reading(row):\n"
        "    return {'is_bundled': row.is_bundled}\n"
    )
    assert _is_bundled_writes([harmless]) == []


def test_the_seed_is_not_reachable_from_any_http_route():
    """Nothing in the API layer imports or calls it. The installer's row is not
    a thing a request can ask for."""
    for path in sorted((BACKEND_ROOT / "api").rglob("*.py")):
        assert THE_ONE_WRITER not in path.read_text(), path
