"""Phase 7 of bring-your-own evidence storage: the readiness gate and /health.

ISA 20260912-0930, criteria 52, 53 and 54.

The gate moved. It used to stand in front of organisation creation and answer
503, which made the only sensible install sequence impossible: bring up a
`--no-minio` stack, sign in, and you could not create the organisation from
inside which a store is configured. It now stands at the two places a missing
store genuinely stops someone — presign and read — and it names the screen.

Three choices about how these are built.

**The org-creation criterion is asserted on both paths at once.** The two
paths disagreed for months precisely because each had its own test. The test
here reads both functions' behaviour under one unconfigured resolver, so a
future edit that re-adds the refusal to either one fails.

**The refusal is asserted by status AND by what the body says.** A test that
only checked 409 would pass against a body that names no remedy, which is the
defect being fixed rather than the one being introduced. The screen name is
asserted as a substring of the rendered message.

**/health is asserted to make no network call.** The whole design claim is
that it does not dial the store; a test of the returned shape alone would pass
against an implementation that round-trips to S3 on every Docker healthcheck.
The driver module is replaced by an object that raises on attribute access.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import uuid
from dataclasses import replace
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Response

import catalog_models  # noqa: F401  (mapper registry — see the Phase 6 note)
import models  # noqa: F401  (mapper registry — see the Phase 6 note)
from services import storage_config, storage_service


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _config(**overrides) -> storage_config.ResolvedStorageConfig:
    base = storage_config.ResolvedStorageConfig(
        config_id="cfg-1",
        source=storage_config.SOURCE_PLATFORM,
        provider=storage_config.PROVIDER_MINIO,
        bucket="evidence",
    )
    return replace(base, **overrides)


_UNCONFIGURED = _config(
    config_id="legacy-env",
    source=storage_config.SOURCE_LEGACY_ENV,
    provider=storage_config.PROVIDER_AWS_S3,
    bucket="",
)


# ---------------------------------------------------------------------------
# ISC 52 — org creation no longer 503s on unconfigured storage
# ---------------------------------------------------------------------------

class _PastTheStorageCheck(Exception):
    """Raised by the stub session at the first query AFTER the storage check.

    Seeing it is the assertion. A refusal standing in front of the check would
    have come out of the route as an ``HTTPException`` and this would never be
    reached; an exception anywhere else in the preamble would be some other
    type. The sentinel is the only way past.
    """


class _ApiKeyUser:
    """API-key auth, so the subscription preamble is skipped and the storage
    check is the only thing between the call and the sentinel query."""

    auth_method = "api_key"
    db_id = "11111111-1111-1111-1111-111111111111"
    email = "probe@example.invalid"


def _session_that_stops_at(table: str):
    """An `AsyncSession` stub that answers nothing and raises at `table`."""
    db = AsyncMock()

    async def execute(stmt, *args, **kwargs):
        if table in str(stmt):
            raise _PastTheStorageCheck(table)
        return MagicMock(
            **{"scalar_one_or_none.return_value": None, "scalar.return_value": 0}
        )

    db.execute = AsyncMock(side_effect=execute)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _http_request(path: str = "/probe"):
    from fastapi import Request

    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("127.0.0.1", 1234),
    }

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    return Request(scope, receive)


def _drive(coro):
    """Run a route coroutine and return whatever came out of it — the sentinel,
    an `HTTPException`, or a result."""
    from fastapi import HTTPException

    try:
        return "returned", asyncio.run(coro)
    except _PastTheStorageCheck as sentinel:
        return "past-the-check", sentinel
    except HTTPException as exc:
        return "refused", exc


def test_neither_org_creation_path_refuses_when_no_store_is_configured(
    monkeypatch, caplog
):
    """The criterion, asserted on both paths in one test (ISC 52).

    `api/provisioning.py` raised 503 and `api/organizations.py` only warned,
    for the same condition — so the same user could be refused through one
    entry point and served through the other. Whatever a later edit does, it
    has to do it to both.

    **Driven, not read.** An earlier version of this test sliced
    `inspect.getsource` and asserted `"503" not in` the slice, which is a
    statement about the characters in the file and not about what the function
    does: it passes against a refusal raised from a helper, or spelled with a
    constant, or moved four lines up. Here both real coroutines are called
    with nothing configured, and each is required to reach a query that sits
    *after* its storage check. A refusal in front of the check surfaces as an
    `HTTPException` and fails the assertion.
    """
    from api import organizations, provisioning
    from schemas import OrganizationCreate, SyncRequest

    monkeypatch.setattr(storage_config, "resolve_platform", lambda: _UNCONFIGURED)
    assert storage_service.is_configured() is False

    with caplog.at_level(logging.WARNING):
        # `api/organizations.py`: the slug lookup is the first query after the
        # check, so reaching `organizations` means the check let it through.
        outcome, detail = _drive(
            organizations.create_organization(
                request=_http_request(),
                org_data=OrganizationCreate(name="Probe", slug="probe"),
                user=_ApiKeyUser(),
                db=_session_that_stops_at("organizations"),
            )
        )
        assert outcome == "past-the-check", f"create_organization: {detail}"

        # `api/provisioning.py`: the admin-membership lookup is the first query
        # after its check.
        outcome, detail = _drive(
            provisioning.sync_subscription(
                request=_http_request("/provisioning/sync"),
                response=Response(),
                sync_data=SyncRequest(email="probe@example.invalid", planTier="free"),
                user=_ApiKeyUser(),
                db=_session_that_stops_at("organization_members"),
            )
        )
        assert outcome == "past-the-check", f"sync_subscription: {detail}"

    # Proceeding silently would be the other way to fail an operator: the
    # warning is the only trace a `--no-minio` install leaves of why uploads
    # will refuse later. Asserted on both paths, and on the text, because the
    # message used to name `AZURE_STORAGE_ACCOUNT_KEY`, which by then selected
    # nothing at all.
    warnings = [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "Evidence storage not configured" in r.getMessage()
    ]
    assert len(warnings) == 2, warnings
    assert any(_ApiKeyUser.db_id in w for w in warnings), warnings
    assert any("Evidence storage" in w for w in warnings), warnings
    assert not any("AZURE_STORAGE_ACCOUNT_KEY" in w for w in warnings), warnings


def test_the_provisioning_path_says_where_the_store_is_configured(monkeypatch):
    """The warning is the only thing an operator reading logs will see, so it
    has to point somewhere. It used to name two environment variables, one of
    which (Azure) no longer selects anything at all."""
    from api import provisioning

    source = inspect.getsource(provisioning.sync_subscription)
    marker = source.split("storage_configured()")[1][:600]
    assert "Evidence storage" in marker
    assert "AZURE_STORAGE_ACCOUNT_KEY" not in marker


# ---------------------------------------------------------------------------
# ISC 53 — one refusal, one status, naming the screen
# ---------------------------------------------------------------------------

def test_the_refusal_names_the_settings_screen():
    from api.storage_gate import storage_not_configured

    exc = storage_not_configured()
    assert exc.status_code == 409
    assert isinstance(exc.detail, dict)
    assert exc.detail["error"] == "evidence_storage_not_configured"
    message = exc.detail["message"]
    assert "Settings" in message and "Evidence storage" in message
    # No credential, bucket or endpoint may be built into a refusal a
    # not-yet-authorised caller can provoke.
    for forbidden in ("bucket", "endpoint", "AWS_", "MINIO_", "key"):
        assert forbidden not in message, forbidden


def test_the_unconfigured_condition_carries_a_type_the_api_can_single_out():
    """`StorageNotConfigured` is a ValueError subclass on purpose.

    Everything written before Phase 7 catches `ValueError`; if this stopped
    being one, those call sites would start letting it through as a 500.
    """
    assert issubclass(storage_config.StorageNotConfigured, ValueError)


class _Membership:
    """The one thing the presign route reads off a membership."""

    class user:  # noqa: D106
        db_id = "11111111-1111-1111-1111-111111111111"


class _UploadRequest:
    filename = "policy.pdf"
    content_type = "application/pdf"
    file_size_bytes = 1024


def _call_presign(monkeypatch, signer):
    """Drive the real route function, not a transcription of it."""
    from fastapi import HTTPException

    from api import evidence_files

    monkeypatch.setattr(evidence_files, "generate_upload_presigned_post", signer)
    try:
        return asyncio.run(
            evidence_files.get_upload_url(
                org_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
                evidence_id="EV-1",
                request=_UploadRequest(),
                membership=_Membership(),
            )
        ), None
    except HTTPException as exc:
        return None, exc


def test_an_unconfigured_org_cannot_upload_and_is_told_where_to_go(monkeypatch):
    """ISC 53, on the route itself.

    The refusal is raised from the signer the same way the real driver raises
    it, and the route's answer is read off the exception it produces.
    """

    def _unconfigured(**kwargs):
        raise storage_config.StorageNotConfigured("Evidence storage not configured")

    result, exc = _call_presign(monkeypatch, _unconfigured)
    assert result is None
    assert exc.status_code == 409
    assert exc.detail["error"] == "evidence_storage_not_configured"
    assert "Settings" in exc.detail["message"]
    assert "Evidence storage" in exc.detail["message"]


def test_a_configured_org_is_unaffected(monkeypatch):
    """The other half of the criterion. A gate that refuses everything would
    pass the test above."""
    monkeypatch.setattr(
        "api.evidence_files.mint_upload_ticket", lambda **kw: "ticket"
    )

    def _signed(**kwargs):
        return {
            "method": "POST",
            "provider": "minio",
            "url": "https://example.invalid/evidence",
            "fields": {"key": "k"},
            "object_key": "k",
            "expires_in": 900,
        }

    result, exc = _call_presign(monkeypatch, _signed)
    assert exc is None
    assert result.method == "POST"
    assert result.s3_key == "k"


def test_a_bad_argument_is_still_the_callers_own_400(monkeypatch):
    """Collapsing both ValueError cases into one status was the easy fix and
    the wrong one: it would tell an administrator to go configure storage
    when what they did was upload a .exe."""

    def _rejects(**kwargs):
        raise ValueError("Content type application/x-msdownload is not allowed")

    result, exc = _call_presign(monkeypatch, _rejects)
    assert result is None
    assert exc.status_code == 400
    assert "not allowed" in str(exc.detail)


def test_the_subclass_is_caught_before_its_base(monkeypatch):
    """Ordering is the whole mechanism: `except ValueError` placed first would
    swallow the subclass and make the 409 unreachable.

    Driven twice with the **same message** and only the exception type
    differing, so nothing here can pass on a handler that discriminates on the
    text rather than the type — and handlers in the wrong order collapse both
    answers onto 400, which the inequality catches.
    """
    message = "Evidence storage is not configured for this organisation"

    def _subclass(**kwargs):
        raise storage_config.StorageNotConfigured(message)

    def _base(**kwargs):
        raise ValueError(message)

    _, refused = _call_presign(monkeypatch, _subclass)
    _, plain = _call_presign(monkeypatch, _base)

    assert refused.status_code == 409  # not 400
    assert refused.detail["error"] == "evidence_storage_not_configured"
    assert plain.status_code == 400
    assert plain.status_code != refused.status_code


def test_the_read_path_answers_the_same_status_as_the_write_path():
    """D46's carry-forward: one condition had two answers, 400 and 503."""
    from api import evidence_files
    from api.storage_gate import storage_not_configured

    download = inspect.getsource(evidence_files.download_evidence_file)
    assert "storage_not_configured()" in download
    # Nothing in this module answers the unconfigured condition with a 503 any
    # more. Checked across the whole module, not just this route, because the
    # disagreement D46 found was BETWEEN two routes.
    module = inspect.getsource(evidence_files)
    assert "status_code=503" not in module
    assert storage_not_configured().status_code == 409


_INBOX_SECRET = "phase7-inbox-secret"
_INBOX_ORG = uuid.uuid4()
_INBOX_EVIDENCE = "ERL-IAM-001"


def _inbox_endpoint():
    endpoint = MagicMock()
    endpoint.id = uuid.uuid4()
    endpoint.organization_id = _INBOX_ORG
    endpoint.is_active = True
    endpoint.secret = _INBOX_SECRET
    endpoint.allowed_evidence_ids = None
    endpoint.last_delivery_at = None
    endpoint.delivery_count = 0
    return endpoint


def _inbox_request(body: bytes, endpoint):
    """A real ASGI request carrying a valid signature — harness style follows
    tests/test_evidence_inbox_integrity.py."""
    from fastapi import Request

    signature = "sha256=" + hmac.new(
        _INBOX_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/organizations/{_INBOX_ORG}/evidence/{_INBOX_EVIDENCE}/inbox",
        "query_string": b"",
        "headers": [
            (b"content-type", b"application/json"),
            (b"x-scf-webhook-id", str(endpoint.id).encode()),
            (b"x-scf-signature", signature.encode()),
        ],
        "client": ("127.0.0.1", 1234),
    }

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _inbox_session(endpoint):
    db = AsyncMock()

    async def execute(stmt, *args, **kwargs):
        sql = str(stmt)
        if "webhook_endpoints" in sql:
            return MagicMock(**{"scalar_one_or_none.return_value": endpoint})
        return MagicMock(**{"scalar_one_or_none.return_value": None})

    db.execute = AsyncMock(side_effect=execute)
    added = []
    db.add = MagicMock(side_effect=added.append)
    db.added = added

    async def flush():
        for obj in added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    db.flush = AsyncMock(side_effect=flush)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def test_the_inbox_write_refuses_the_same_way_rather_than_recording_a_200():
    """A webhook delivery into an organisation with no store used to be
    swallowed by the route's catch-all and reported as a 200 whose body said
    "Processing failed" — a status that tells the sender to look at their own
    payload.

    **Driven, not read.** The real route is called over a real signed request,
    with only the payload writer replaced — by the same exception the real
    driver raises. Asserting on the source text of the handlers instead would
    pass against a handler that catches in the right order and then returns a
    200 anyway, which is precisely the defect being fixed.
    """
    from fastapi import HTTPException

    from api import evidence_inbox

    endpoint = _inbox_endpoint()
    db = _inbox_session(endpoint)
    body = json.dumps({"source": "test", "data": {"status": "compliant"}}).encode()

    def _no_store(**kwargs):
        raise storage_config.StorageNotConfigured("Evidence storage not configured")

    with patch.object(evidence_inbox, "write_inbox_payload", _no_store):
        with pytest.raises(HTTPException) as raised:
            asyncio.run(
                evidence_inbox.ingest_evidence(
                    request=_inbox_request(body, endpoint),
                    response=Response(),
                    org_id=_INBOX_ORG,
                    evidence_id=_INBOX_EVIDENCE,
                    db=db,
                )
            )

    # The sender gets the refusal, not a 200 whose body says "Processing
    # failed" — the status is the only part a machine reads.
    exc = raised.value
    assert exc.status_code == 409
    assert exc.detail["error"] == "evidence_storage_not_configured"
    assert "Settings" in exc.detail["message"]
    assert "Evidence storage" in exc.detail["message"]

    # And the delivery row is still written and committed, carrying the same
    # remedy, so a refused delivery is findable afterwards rather than lost
    # with the response.
    deliveries = [o for o in db.added if hasattr(o, "error_message")]
    assert len(deliveries) == 1, deliveries
    delivery = deliveries[0]
    assert delivery.status == "failed"
    assert delivery.error_message == exc.detail["message"]
    assert db.commit.await_count >= 1


# ---------------------------------------------------------------------------
# ISC 54 — /health reports an evidence-storage component
# ---------------------------------------------------------------------------

class _Explodes:
    """Any attribute access is a network call that should not happen."""

    def __getattr__(self, name):  # pragma: no cover - the failure is the point
        raise AssertionError(f"/health reached the storage driver: {name}")


@pytest.mark.parametrize(
    "config,expected_status,expected_source,expected_provider",
    [
        (
            _config(is_bundled=True),
            "ok",
            "bundled",
            storage_config.PROVIDER_MINIO,
        ),
        (
            _config(provider=storage_config.PROVIDER_AWS_S3, is_bundled=False),
            "ok",
            "platform",
            storage_config.PROVIDER_AWS_S3,
        ),
        (
            _config(
                config_id="legacy-env",
                source=storage_config.SOURCE_LEGACY_ENV,
                provider=storage_config.PROVIDER_AWS_S3,
            ),
            "ok",
            "legacy_env",
            storage_config.PROVIDER_AWS_S3,
        ),
        (_UNCONFIGURED, "unconfigured", "none", None),
    ],
    ids=["bundled", "platform", "legacy_env", "none"],
)
def test_health_reports_the_platform_effective_state(
    monkeypatch, config, expected_status, expected_source, expected_provider
):
    monkeypatch.setattr(storage_config, "resolve_platform", lambda: config)
    component = storage_service.evidence_storage_health()
    assert component["status"] == expected_status
    assert component["source"] == expected_source
    assert component["provider"] == expected_provider


def test_health_answers_error_without_quoting_the_error(monkeypatch):
    """An undecryptable secret is the case that actually happens (D27 keeps a
    database outage away from here). The message names a configuration id and
    a scope, and /health has no reader anyone vouched for."""

    def _boom():
        raise storage_config.StorageConfigError(
            "Evidence storage configuration 078ff68a (platform scope) has a "
            "stored secret that cannot be decrypted"
        )

    monkeypatch.setattr(storage_config, "resolve_platform", _boom)
    component = storage_service.evidence_storage_health()
    assert component["status"] == "error"
    assert component["source"] == "none"
    assert component["provider"] is None
    assert component["error"] == "StorageConfigError"
    assert "078ff68a" not in repr(component)
    assert "decrypt" not in repr(component)


def test_health_carries_no_bucket_endpoint_or_credential(monkeypatch):
    """/health is unauthenticated — no dependency, no token, reachable by
    anyone who can reach the port, because the container healthcheck and the
    load balancer both poll it. Everything it says must be safe to say to a
    stranger."""
    config = _config(
        bucket="acme-prod-evidence",
        endpoint_url="https://s3.eu-west-2.amazonaws.com",
        public_endpoint="https://cdn.acme.example",
        region="eu-west-2",
        access_key_id="AKIAEXAMPLE0000",
        secret_access_key="s3cr3t",
    )
    monkeypatch.setattr(storage_config, "resolve_platform", lambda: config)
    rendered = repr(storage_service.evidence_storage_health())
    for leak in (
        "acme-prod-evidence",
        "amazonaws.com",
        "cdn.acme.example",
        "eu-west-2",
        "AKIAEXAMPLE0000",
        "s3cr3t",
    ):
        assert leak not in rendered, leak
    assert set(storage_service.evidence_storage_health()) == {
        "status",
        "source",
        "provider",
    }


def test_health_never_dials_the_store(monkeypatch):
    """The design claim, asserted rather than described.

    Docker polls /health every 30 seconds for the life of the container. A
    round trip per poll is a traffic generator against a customer's object
    store, and one blip would restart a healthy backend.
    """
    import services.s3_service as s3_service

    monkeypatch.setattr(storage_config, "resolve_platform", lambda: _config())
    monkeypatch.setattr(s3_service, "_build_client", _Explodes())
    monkeypatch.setattr(s3_service, "get_client", _Explodes(), raising=False)
    component = storage_service.evidence_storage_health()
    assert component["status"] == "ok"


def test_health_never_raises(monkeypatch):
    """A health endpoint that can 500 is not a health endpoint."""

    def _boom():
        raise RuntimeError("anything at all")

    monkeypatch.setattr(storage_config, "resolve_platform", _boom)
    assert storage_service.evidence_storage_health()["status"] == "error"


def test_the_health_route_includes_the_component_and_does_not_await_a_probe():
    import main

    source = inspect.getsource(main.health_check)
    assert '"evidence_storage": storage_status' in source
    assert "evidence_storage_health()" in source
    # Not awaited: it does no I/O, and awaiting it would be a lie about that.
    assert "await evidence_storage_health" not in source
    # Unconfigured is not degraded — a fresh --no-minio install is working.
    assert 'storage_status.get("status") == "error"' in source
    assert 'storage_status.get("status") == "unconfigured"' not in source


def test_the_health_route_takes_no_authentication():
    """Asserted, because adding a dependency here would break the container
    healthcheck and the load balancer silently — both poll without a token."""
    import main

    signature = inspect.signature(main.health_check)
    assert list(signature.parameters) == []
