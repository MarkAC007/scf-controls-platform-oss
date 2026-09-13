"""Phase 0 of bring-your-own evidence storage: the facade is complete and no
longer static (#967, ISC-1 … ISC-8).

Each test names the criterion it holds. The two structural ones — the import
graph and the absence of import-time resolution — are deliberately mechanical:
the rule they enforce previously existed only as a sentence in a docstring,
which is exactly why six callsites had drifted past it.
"""
from __future__ import annotations

import ast
import pathlib
from unittest.mock import MagicMock, patch

import pytest

from services import s3_service, storage_config, storage_service
from services.storage_config import (
    PROVIDER_AWS_S3,
    PROVIDER_GCS,
    PROVIDER_MINIO,
    PROVIDER_S3_COMPATIBLE,
    SSE_AES256,
    SSE_NONE,
    ResolvedStorageConfig,
)

BACKEND_ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Directories that are not production backend source.
_EXCLUDED_DIRS = {
    ".venv", "venv", "__pycache__", "node_modules", ".git", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "tests", "htmlcov", ".claude",
}

#: The single module allowed to import the S3 driver. The whole point of the
#: facade is that the provider choice is made in exactly one place.
_FACADE = "services/storage_service.py"


def _production_modules():
    """Every production backend .py file, as (relative path, parsed AST)."""
    for path in sorted(BACKEND_ROOT.rglob("*.py")):
        rel = path.relative_to(BACKEND_ROOT)
        if any(part in _EXCLUDED_DIRS for part in rel.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - would fail the build elsewhere
            continue
        yield rel.as_posix(), tree


def _imports_s3_service(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name in ("services.s3_service", "s3_service") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in ("services.s3_service", "s3_service"):
                return True
            # `from services import s3_service`
            if module in ("services", "") and any(
                a.name == "s3_service" for a in node.names
            ):
                return True
    return False


# ---------------------------------------------------------------------------
# ISC-1 — the facade is complete
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["put_bytes", "delete_object"])
def test_isc1_facade_exposes_the_missing_verbs(name):
    """ISC-1. These existed only on the driver, which is why four catalogue
    callsites imported it directly and why an Azure-only install could never
    have completed a catalogue import."""
    assert callable(getattr(storage_service, name))


# ---------------------------------------------------------------------------
# ISC-2 / ISC-3 — the import graph, enforced mechanically
# ---------------------------------------------------------------------------


def test_isc2_only_the_facade_imports_the_s3_driver():
    """ISC-2/3. Anything that needs object storage goes through
    ``storage_service``. Tests are exempt — testing a driver directly is
    legitimate — but no production module is."""
    offenders = [
        rel for rel, tree in _production_modules()
        if rel != _FACADE and _imports_s3_service(tree)
    ]
    assert offenders == [], (
        "These modules import services.s3_service directly. Use "
        "services.storage_service instead; if the facade lacks the verb you "
        "need, add it there rather than reaching past it: " + ", ".join(offenders)
    )


def test_isc3_the_guard_would_actually_catch_a_violation():
    """ISC-3. A guard that cannot fail is not a guard. Every import spelling
    the codebase has used must be detected."""
    for source in (
        "from services import s3_service",
        "from services.s3_service import put_bytes",
        "import services.s3_service",
        "from services.s3_service import ALLOWED_CONTENT_TYPES as x",
    ):
        assert _imports_s3_service(ast.parse(source)), source
    assert not _imports_s3_service(ast.parse("from services import storage_service"))


def test_isc2_the_facade_itself_is_the_one_importer():
    """The allow-listed module must really be the importer, or the test above
    would pass vacuously on a codebase where nothing imports the driver."""
    trees = dict(_production_modules())
    assert _FACADE in trees
    assert _imports_s3_service(trees[_FACADE])


# ---------------------------------------------------------------------------
# ISC-4 — nothing is resolved at import time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module_path",
    ["services/s3_service.py", "services/storage_service.py", "services/storage_config.py"],
)
def test_isc4_no_storage_value_is_resolved_at_import(module_path):
    """ISC-4. Reading the environment at module scope is what froze the old
    constants for the life of the process. Inside a function is fine — that is
    per-call resolution, which is the point."""
    tree = ast.parse((BACKEND_ROOT / module_path).read_text(encoding="utf-8"))
    module_level = [
        n for n in tree.body
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    for node in module_level:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name):
                combined = f"{sub.value.id}.{sub.attr}"
                assert combined not in ("os.getenv", "os.environ"), (
                    f"{module_path} reads the environment at import time "
                    f"({combined}, line {sub.lineno}). Resolve it per call."
                )


def test_isc4_the_old_module_constants_are_gone():
    """The specific names the ISA enumerates must no longer exist, or a caller
    could still import a frozen value."""
    for name in (
        "EVIDENCE_BUCKET", "AWS_REGION", "EVIDENCE_URL_EXPIRY",
        "EVIDENCE_MAX_FILE_SIZE", "AWS_ENDPOINT_URL", "EVIDENCE_PUBLIC_ENDPOINT",
        "SSE_ENABLED", "_s3_client", "_s3_presign_client",
    ):
        assert not hasattr(s3_service, name), f"s3_service.{name} still exists"
    assert not hasattr(storage_service, "EVIDENCE_URL_EXPIRY")


def test_isc4_changing_the_environment_changes_the_next_call(monkeypatch):
    """The behavioural half of ISC-4: no restart needed."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "first-bucket")
    assert storage_config.resolve_platform().bucket == "first-bucket"
    monkeypatch.setenv("EVIDENCE_BUCKET", "second-bucket")
    assert storage_config.resolve_platform().bucket == "second-bucket"


# ---------------------------------------------------------------------------
# ISC-5 — SSE derives from the resolved config
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint,expected_provider,expected_sse",
    [
        ("", PROVIDER_AWS_S3, SSE_AES256),
        ("http://minio:9000", PROVIDER_MINIO, SSE_NONE),
        ("https://storage.googleapis.com", PROVIDER_GCS, SSE_NONE),
        ("https://bucket.storage.googleapis.com", PROVIDER_GCS, SSE_NONE),
        # A look-alike host must NOT classify as GCS: only the exact host or a
        # subdomain of it does.
        ("https://evilstorage.googleapis.com", PROVIDER_S3_COMPATIBLE, SSE_NONE),
        ("https://objects.example.net", PROVIDER_S3_COMPATIBLE, SSE_NONE),
    ],
)
def test_isc5_sse_follows_the_resolved_provider(
    monkeypatch, endpoint, expected_provider, expected_sse
):
    """ISC-5. The old ``SSE_ENABLED = not AWS_ENDPOINT_URL`` was computed from
    another import-time constant, so changing the endpoint could never change
    the encryption mode. Now it follows the provider the config resolved to."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "b")
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)
    config = storage_config.resolve_platform()
    assert config.provider == expected_provider
    assert config.sse_mode == expected_sse
    assert config.sse_enabled is (expected_sse != SSE_NONE)


def test_isc5_the_same_process_can_change_its_mind_about_sse(monkeypatch):
    """The behaviour the old code structurally could not produce."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "b")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "")
    assert storage_config.resolve_platform().sse_enabled is True
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    assert storage_config.resolve_platform().sse_enabled is False


def test_isc5_the_write_path_actually_asks_for_the_resolved_mode():
    """Derivation is worthless if the operation ignores it."""
    aws = _config(endpoint_url="", sse_mode=SSE_AES256)
    minio = _config(endpoint_url="http://minio:9000", sse_mode=SSE_NONE)

    with patch.object(s3_service, "_client") as mock_client:
        client = MagicMock()
        mock_client.return_value = client
        s3_service.put_bytes("k", b"x", "application/json", "org-1", config=aws)
        assert client.put_object.call_args.kwargs["ServerSideEncryption"] == SSE_AES256

        client.reset_mock()
        s3_service.put_bytes("k", b"x", "application/json", "org-1", config=minio)
        assert "ServerSideEncryption" not in client.put_object.call_args.kwargs


# ---------------------------------------------------------------------------
# ISC-6 / ISC-7 / ISC-8 — client cache identity and credentials
# ---------------------------------------------------------------------------


def _config(**overrides) -> ResolvedStorageConfig:
    base = dict(
        config_id="cfg-1",
        source=storage_config.SOURCE_LEGACY_ENV,
        provider=PROVIDER_S3_COMPATIBLE,
        bucket="test-bucket",
        region="eu-west-1",
        endpoint_url="https://objects.example.net",
        path_style=True,
        sse_mode=SSE_NONE,
        access_key_id="AKIAEXAMPLE",
        secret_access_key="secret-one",
    )
    base.update(overrides)
    return ResolvedStorageConfig(**base)


@pytest.fixture(autouse=True)
def _clean_client_cache():
    s3_service.reset_client_cache()
    yield
    s3_service.reset_client_cache()


def test_isc6_the_same_config_reuses_one_client():
    """ISC-6. Caching is still worth having — it just must not be a module
    global that outlives the configuration that produced it."""
    config = _config()
    with patch.object(s3_service.boto3, "client", side_effect=lambda *a, **k: MagicMock()):
        assert s3_service._client(config) is s3_service._client(config)


def test_isc6_a_different_config_gets_a_different_client():
    """A provider switch must not reuse the previous provider's client."""
    with patch.object(s3_service.boto3, "client", side_effect=lambda *a, **k: MagicMock()):
        first = s3_service._client(_config(config_id="cfg-1"))
        second = s3_service._client(_config(config_id="cfg-2"))
    assert first is not second


def test_isc6_the_presign_role_is_cached_separately():
    """The presign client signs against the public endpoint, so it is a
    different client for the same config — and must stay one."""
    config = _config(public_endpoint="https://evidence.example.com")
    with patch.object(s3_service.boto3, "client", side_effect=lambda *a, **k: MagicMock()):
        internal = s3_service._client(config, storage_config.ROLE_INTERNAL)
        presign = s3_service._client(config, storage_config.ROLE_PRESIGN)
    assert internal is not presign


def test_isc6_the_cache_is_bounded():
    """A worker that rotates repeatedly must not accumulate clients forever."""
    with patch.object(s3_service.boto3, "client", side_effect=lambda *a, **k: MagicMock()):
        for i in range(s3_service._CLIENT_CACHE_MAX + 10):
            s3_service._client(_config(config_id=f"cfg-{i}"))
    assert len(s3_service._client_cache) <= s3_service._CLIENT_CACHE_MAX


def test_isc7_credentials_are_passed_to_boto3_explicitly():
    """ISC-7. ``_build_client`` used to pass none at all, leaving boto3's
    ambient chain to resolve them and bake them into a cached client. Neither
    AWS key was read anywhere in backend source."""
    with patch.object(s3_service.boto3, "client") as mock_boto:
        s3_service._client(_config(access_key_id="AKIA1", secret_access_key="s1"))
    kwargs = mock_boto.call_args.kwargs
    assert kwargs["aws_access_key_id"] == "AKIA1"
    assert kwargs["aws_secret_access_key"] == "s1"
    assert kwargs["endpoint_url"] == "https://objects.example.net"
    assert kwargs["region_name"] == "eu-west-1"


def test_isc7_a_session_token_is_forwarded_when_present():
    with patch.object(s3_service.boto3, "client") as mock_boto:
        s3_service._client(_config(session_token="tok"))
    assert mock_boto.call_args.kwargs["aws_session_token"] == "tok"


def test_isc7_an_empty_credential_still_defers_to_the_ambient_chain():
    """The one case where ambient resolution is intended rather than accidental:
    an IAM instance role or IRSA deployment stores no credential at all."""
    with patch.object(s3_service.boto3, "client") as mock_boto:
        s3_service._client(_config(access_key_id="", secret_access_key=""))
    kwargs = mock_boto.call_args.kwargs
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs


def test_isc8_a_rotated_secret_reaches_a_non_signing_operation():
    """ISC-8, and the regression today's Azure code would fail.

    ``put_object`` is a *non-signing* operation: it goes over the wire with the
    credential baked into the client. Azure's ``_get_blob_service_client``
    interpolates the key into a connection string and caches it forever, so a
    rotated key reached only the offline SAS-signing paths. The identical trap
    existed for S3. Rotating mid-test and asserting the second write is made by
    a client built with the *new* secret is what proves it is closed.
    """
    built = []

    def _record(*args, **kwargs):
        built.append(kwargs)
        return MagicMock()

    with patch.object(s3_service.boto3, "client", side_effect=_record):
        s3_service.put_bytes(
            "k", b"x", "application/json", "org-1",
            config=_config(secret_access_key="secret-one"),
        )
        s3_service.put_bytes(
            "k", b"x", "application/json", "org-1",
            config=_config(secret_access_key="secret-two"),
        )

    assert [c["aws_secret_access_key"] for c in built] == ["secret-one", "secret-two"], (
        "The rotated secret did not reach the second write — the client cache "
        "is not keyed by credential identity."
    )


def test_isc8_rotation_also_reaches_tagging_and_deletion():
    """Every operation, not just the one the regression was written against."""
    for call in (
        lambda cfg: s3_service.tag_evidence_object("k", "org-1", config=cfg),
        lambda cfg: s3_service.delete_object("k", config=cfg),
        lambda cfg: s3_service.write_inbox_payload("k", b"{}", "org-1", config=cfg),
    ):
        s3_service.reset_client_cache()
        built = []
        with patch.object(
            s3_service.boto3, "client",
            side_effect=lambda *a, **k: (built.append(k), MagicMock())[1],
        ):
            call(_config(secret_access_key="old"))
            call(_config(secret_access_key="new"))
        assert [c["aws_secret_access_key"] for c in built] == ["old", "new"]


def test_isc8_an_unchanged_secret_does_not_rebuild_the_client():
    """The cache must still be a cache — otherwise this test would pass by
    rebuilding on every call and ISC-6 would be meaningless."""
    built = []
    with patch.object(
        s3_service.boto3, "client",
        side_effect=lambda *a, **k: (built.append(k), MagicMock())[1],
    ):
        s3_service.put_bytes("k", b"x", "application/json", "o", config=_config())
        s3_service.put_bytes("k", b"x", "application/json", "o", config=_config())
    assert len(built) == 1


# ---------------------------------------------------------------------------
# Runtime invalidation — the backend choice can change without a restart
# ---------------------------------------------------------------------------


def test_the_backend_verdict_is_not_frozen_for_the_life_of_the_process(monkeypatch):
    """``_BACKEND`` was memoised on first call with no reset function; only
    tests ever cleared it, so changing storage meant editing .env and
    restarting the stack."""
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)
    storage_service.reset_backend_cache()
    assert storage_service.get_backend() == "none"

    monkeypatch.setenv("EVIDENCE_BUCKET", "now-configured")
    assert storage_service.get_backend() == "s3", (
        "the backend verdict did not change after the configuration did"
    )


def test_invalidate_clears_the_client_cache(monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "b")
    with patch.object(s3_service.boto3, "client", side_effect=lambda *a, **k: MagicMock()):
        s3_service._client(_config())
        assert s3_service._client_cache
        storage_service.invalidate()
    assert not s3_service._client_cache


# ---------------------------------------------------------------------------
# R8 — the expiry reported to a client is the one that was signed
# ---------------------------------------------------------------------------


def test_r8_the_presign_result_carries_the_expiry_that_was_signed(monkeypatch):
    monkeypatch.setenv("EVIDENCE_URL_EXPIRY", "1234")
    config = _config()
    config = ResolvedStorageConfig(
        **{**config.__dict__, "url_expiry": 1234}
    )
    with patch.object(s3_service, "_client") as mock_client:
        client = MagicMock()
        client.generate_presigned_post.return_value = {"url": "u", "fields": {}}
        mock_client.return_value = client
        result = s3_service.generate_upload_presigned_post(
            "org-1", "a.pdf", "application/pdf", config=config
        )
    assert result["expires_in"] == 1234
    assert client.generate_presigned_post.call_args.kwargs["ExpiresIn"] == 1234


def test_r8_the_expiry_is_not_frozen_at_import(monkeypatch):
    monkeypatch.setenv("EVIDENCE_URL_EXPIRY", "60")
    assert storage_config.resolve_platform().url_expiry == 60
    monkeypatch.setenv("EVIDENCE_URL_EXPIRY", "120")
    assert storage_config.resolve_platform().url_expiry == 120


def test_a_nonsense_expiry_falls_back_rather_than_crashing(monkeypatch):
    monkeypatch.setenv("EVIDENCE_URL_EXPIRY", "not-a-number")
    assert storage_config.resolve_platform().url_expiry == 900


# ---------------------------------------------------------------------------
# The Phase 2 seam
# ---------------------------------------------------------------------------


def test_the_endpoint_hook_runs_on_every_operation_not_once_per_client():
    """ISA §7 requires address validation to re-run at connect time. A check
    placed in the client *builder* would be skipped for the whole life of a
    cached client, which is how a DNS rebind gets through. Phase 2 only has to
    fill in the body."""
    config = _config()
    with patch.object(s3_service, "assert_endpoint_allowed") as hook:
        with patch.object(s3_service.boto3, "client", side_effect=lambda *a, **k: MagicMock()):
            s3_service._client(config)
            s3_service._client(config)  # served from cache
    assert hook.call_count == 2


def test_a_tenant_supplied_endpoint_fails_closed_until_phase_2():
    """Phase 0 implements no address checks. If a later phase lands a
    tenant-supplied config before the validator exists, the result must be a
    refusal rather than an open request-forgery hole."""
    config = _config(endpoint_is_operator_supplied=False)
    with pytest.raises(storage_config.StorageConfigError):
        storage_config.assert_endpoint_allowed(config)


def test_an_operator_supplied_endpoint_is_permitted():
    storage_config.assert_endpoint_allowed(_config(endpoint_is_operator_supplied=True))


# ---------------------------------------------------------------------------
# Platform scope still resolves without an organisation
# ---------------------------------------------------------------------------


def test_platform_scope_resolves_with_no_organisation(monkeypatch):
    """The catalogue workbook, upgrade diffs and reconciliation blobs belong to
    the platform. Phase 1 must not be able to force them to be org-scoped."""
    monkeypatch.setenv("EVIDENCE_BUCKET", "b")
    config = storage_config.resolve_platform()
    assert config.organization_id is None
    assert config.bucket == "b"


def test_an_org_scoped_resolution_carries_its_organisation(monkeypatch):
    monkeypatch.setenv("EVIDENCE_BUCKET", "b")
    assert storage_config.resolve("org-7").organization_id == "org-7"


def test_a_secret_is_never_in_a_config_repr():
    """A resolved config ends up in log lines and tracebacks."""
    rendered = repr(_config(secret_access_key="super-secret", session_token="tok"))
    assert "super-secret" not in rendered
    assert "tok" not in rendered
