"""Phase 3 of bring-your-own evidence storage: the credential in the database.

ISA 20260912-0930, criteria 24 to 29, and 63.

None of these tests touch a network or a database. Four choices about how they
are built are worth stating up front, because each is the difference between a
test that proves something and a test that passes.

**The response and request models are asserted by their exact field set, not by
example.** A test that posted one body and checked the reply would keep passing
the day somebody adds ``secret_access_key`` to the response model. Asserting the
whole set fails on the addition itself, which is the point: the field set *is*
the security boundary here, and ``repr=False`` on a Pydantic field hides a value
from ``repr()`` while serialising it exactly as before.

**Refusal of ``is_bundled`` is proved twice** — once at the schema, where
``extra="forbid"`` turns the body into a 422, and once at the service, where
:class:`ConfigSpec` has no such field to carry. Either alone would be one
refactor away from a tenant setting the flag that exempts a row from the
loopback, RFC1918, CGNAT and ``.local`` address rules.

**The rotation test runs two resolvers and a fake clock, not one resolver and a
sleep.** Two :class:`StorageConfigResolver` instances over one row list and one
shared version counter is the honest model of an API process and a Celery
worker; a single resolver that was told to invalidate itself would prove only
that ``invalidate()`` works. The clock is monkeypatched so the two-second
convergence window is asserted rather than waited out.

**Nothing here mocks ``crypto``.** The encryption is real, under a throwaway
key, so the test that says the plaintext never reaches the row is reading an
actual Fernet token rather than a stub's return value.
"""
from __future__ import annotations

import base64
import inspect
import os
import uuid
from typing import Any, Dict, List, Optional

import pytest

# Imported for its side effect: `models.System` relates to
# `SystemCatalogTemplate`, which lives here, and SQLAlchemy configures no mapper
# until both are in the registry. These tests instantiate ORM objects.
import catalog_models  # noqa: F401
from api import evidence_storage as evidence_storage_api
from models import EvidenceStorageConfig
from rate_limiting import limiter
from services import crypto, evidence_storage_admin, s3_service, storage_config
from services import storage_service
from services.storage_config import (
    PROVIDER_AWS_S3,
    PROVIDER_MINIO,
    PROVIDER_S3_COMPATIBLE,
    ROLE_INTERNAL,
    StorageConfigError,
)

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")


# ---------------------------------------------------------------------------
# Fixtures and doubles
# ---------------------------------------------------------------------------


@pytest.fixture
def secret_key(monkeypatch):
    """A throwaway Fernet key, so the encryption under test is the real one."""
    key = base64.urlsafe_b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("SCF_SECRET_KEY", key)
    crypto.reset()
    yield key
    crypto.reset()


def _row(**overrides) -> EvidenceStorageConfig:
    """An ORM row with the defaults a database would have applied.

    Built rather than loaded because the column defaults are ``server_default``
    and never fire without a database, and a row with ``status=None`` would let
    a draft-only check pass for the wrong reason.
    """
    values: Dict[str, Any] = dict(
        id=uuid.uuid4(),
        organization_id=ORG_A,
        provider=PROVIDER_MINIO,
        bucket="evidence",
        region="us-east-1",
        endpoint_url="",
        public_endpoint="",
        path_style=True,
        sse_mode="none",
        access_key_id="AKIAEXAMPLE",
        secret_ciphertext=None,
        key_version=1,
        status=storage_config.STATUS_DRAFT,
        is_bundled=False,
        created_at=None,
        updated_at=None,
        updated_by_user_id=None,
        updated_by_label=None,
    )
    values.update(overrides)
    row = EvidenceStorageConfig()
    for name, value in values.items():
        setattr(row, name, value)
    return row


class _FakeResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row

    def scalar(self):
        return self._row

    def scalars(self):
        return self

    def all(self):
        return list(self._row or [])


class _FakeSession:
    """Records the statements a service asked for, in order.

    ``count_result`` is what a ``SELECT count(*)`` answers with, which is how
    the delete path's "evidence still lives here" refusal is driven without a
    database.
    """

    def __init__(self, row=None, count_result: int = 0):
        self.row = row
        self.count_result = count_result
        self.events: List[str] = []
        self.added: List[Any] = []
        self.deleted: List[Any] = []

    async def execute(self, statement):
        text = str(statement)
        if "count" in text.lower():
            self.events.append("count")
            return _FakeResult(self.count_result)
        self.events.append("select")
        return _FakeResult(self.row)

    def add(self, obj):
        self.events.append("add")
        self.added.append(obj)

    async def delete(self, obj):
        self.events.append("delete")
        self.deleted.append(obj)

    async def flush(self):
        self.events.append("flush")

    async def commit(self):
        self.events.append("commit")

    async def refresh(self, obj):
        self.events.append("refresh")

    async def rollback(self):
        self.events.append("rollback")


@pytest.fixture
def quiet(monkeypatch):
    """Record the cross-process announcements instead of making them."""
    events: List[str] = []
    monkeypatch.setattr(
        storage_config, "bump_version", lambda: events.append("bump_version")
    )
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "invalidate",
        lambda: events.append("invalidate"),
    )
    return events


def _spec(**overrides) -> evidence_storage_admin.ConfigSpec:
    values: Dict[str, Any] = dict(
        provider=PROVIDER_AWS_S3,
        bucket="tenant-evidence",
        region="eu-west-2",
        access_key_id="AKIAEXAMPLE",
        secret_access_key="s3cr3t-value-not-a-real-key",
    )
    values.update(overrides)
    return evidence_storage_admin.ConfigSpec(**values)


# ---------------------------------------------------------------------------
# Criterion 24, and decision D36 — what a request may and may not set
# ---------------------------------------------------------------------------


def test_the_reply_names_no_field_that_could_hold_a_secret():
    """The field set is the boundary. Asserting it whole is what fails on the
    day somebody adds a secret field, rather than the day somebody notices.
    """
    fields = set(evidence_storage_api.EvidenceStorageConfigResponse.model_fields)

    assert fields == {
        "id",
        "organization_id",
        "provider",
        "provider_label",
        "bucket",
        "region",
        "endpoint_url",
        "public_endpoint",
        "path_style",
        "sse_mode",
        "access_key_id",
        "secret_mask",
        "key_version",
        "status",
        "is_bundled",
        # Criterion 30. Which store this row is and who owns it, in the same
        # two names `GET /api/admin/integrations` already answers with, so one
        # chip on the client renders both screens.
        "source",
        "managed_by_operator",
        "created_at",
        "updated_at",
        "updated_by",
    }
    # Named separately so the failure message says which one arrived.
    for forbidden in (
        "secret_access_key",
        "secret_ciphertext",
        "secret",
        "password",
        "session_token",
    ):
        assert forbidden not in fields


def test_the_mask_is_a_constant_and_not_a_function_of_the_secret():
    """A mask whose length tracked the secret would leak its length, and one
    that kept a prefix would leak the prefix. It is the same eight characters
    for a four-character secret and for a hundred-character one.
    """
    short = evidence_storage_api._serialise(_row(secret_ciphertext="enc:v1:aa"))
    long = evidence_storage_api._serialise(
        _row(secret_ciphertext="enc:v1:" + "b" * 500)
    )

    assert short.secret_mask == long.secret_mask == evidence_storage_api.SECRET_MASK
    assert evidence_storage_api.SECRET_MASK == "•" * 8
    # A row with no stored secret says so, which is the one bit about the
    # secret this surface discloses.
    assert evidence_storage_api._serialise(_row()).secret_mask is None


def test_a_serialised_row_contains_no_ciphertext_anywhere():
    """Not only in the fields named above: in the whole dumped body."""
    row = _row(secret_ciphertext="enc:v1:Z0FBQUFBQm1jaXBoZXJ0ZXh0")
    dumped = evidence_storage_api._serialise(row).model_dump()

    assert "enc:v1:" not in repr(dumped)
    assert row.secret_ciphertext not in repr(dumped)


@pytest.mark.parametrize(
    "field",
    ["is_bundled", "status", "key_version", "organization_id", "secret_ciphertext"],
)
def test_the_create_body_refuses_the_five_fields_no_request_may_set(field):
    """D36. ``extra="forbid"`` is what makes this a 422 rather than a field
    that is accepted, ignored, and one ``**body.model_dump()`` away from being
    honoured. ``is_bundled`` is the one that matters: it exempts a row from the
    loopback, RFC1918, CGNAT and ``.local`` rules and permits ``http``.
    """
    body = {
        "provider": PROVIDER_AWS_S3,
        "bucket": "tenant-evidence",
        field: True,
    }
    with pytest.raises(Exception) as caught:
        evidence_storage_api.EvidenceStorageConfigCreateRequest(**body)

    assert field in str(caught.value)


@pytest.mark.parametrize(
    "field",
    ["is_bundled", "status", "key_version", "organization_id", "secret_ciphertext"],
)
def test_the_edit_body_refuses_them_too(field):
    with pytest.raises(Exception) as caught:
        evidence_storage_api.EvidenceStorageConfigUpdateRequest(**{field: True})

    assert field in str(caught.value)


def test_the_rotation_body_takes_a_credential_and_nothing_else():
    """A rotation that could also move the bucket or the endpoint would be an
    edit wearing a rotation's name, and would skip the draft-only rule.
    """
    assert set(
        evidence_storage_api.EvidenceStorageRotateRequest.model_fields
    ) == {"secret_access_key", "access_key_id"}

    with pytest.raises(Exception):
        evidence_storage_api.EvidenceStorageRotateRequest(
            secret_access_key="x", bucket="somewhere-else"
        )


def test_the_service_spec_cannot_carry_the_five_either():
    """Belt and braces, at the layer the installer and Phase 5 also call. A
    request model is a wall in front of one door; this is the door.
    """
    fields = set(evidence_storage_admin.ConfigSpec.__dataclass_fields__)

    assert fields == {
        "provider",
        "bucket",
        "region",
        "endpoint_url",
        "public_endpoint",
        "path_style",
        "access_key_id",
        "secret_access_key",
    }
    for forbidden in (
        "is_bundled",
        "status",
        "key_version",
        "organization_id",
        "secret_ciphertext",
    ):
        assert forbidden not in fields


# ---------------------------------------------------------------------------
# Criterion 24 — creating a configuration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_new_configuration_is_always_a_draft_and_never_bundled(secret_key):
    """A row that arrived active would be a store nobody had proved reachable,
    and a row that arrived bundled would be a tenant-supplied endpoint wearing
    the operator's exemption from the address policy.
    """
    session = _FakeSession()

    row = await evidence_storage_admin.create_config(session, ORG_A, _spec())

    assert row.status == storage_config.STATUS_DRAFT
    assert row.is_bundled is False
    assert row.organization_id == ORG_A
    assert row.key_version == 1


@pytest.mark.asyncio
async def test_the_secret_is_encrypted_before_the_row_is_built(secret_key):
    """Encrypted before, not after: a missing key must refuse the write rather
    than leave a half-made row behind. And the plaintext must not survive
    anywhere on the row.
    """
    plaintext = "an-actual-looking-secret-value"
    session = _FakeSession()

    row = await evidence_storage_admin.create_config(
        session, ORG_A, _spec(secret_access_key=plaintext)
    )

    assert row.secret_ciphertext
    assert row.secret_ciphertext.startswith(crypto.PREFIX)
    assert plaintext not in row.secret_ciphertext
    assert crypto.decrypt(row.secret_ciphertext) == plaintext

    column_values = repr(
        {
            name: getattr(row, name)
            for name in EvidenceStorageConfig.__table__.columns.keys()
        }
    )
    assert plaintext not in column_values


@pytest.mark.asyncio
async def test_a_missing_encryption_key_refuses_the_write(monkeypatch):
    """409 in the same shape the Integrations screen already handles, and no
    row written — the encryption happens before the row is constructed.
    """
    monkeypatch.delenv("SCF_SECRET_KEY", raising=False)
    crypto.reset()
    session = _FakeSession()

    with pytest.raises(crypto.SecretKeyMissing):
        await evidence_storage_admin.create_config(session, ORG_A, _spec())

    assert session.added == []
    assert "commit" not in session.events


@pytest.mark.asyncio
async def test_the_address_is_validated_before_anything_is_written(secret_key):
    """Save-time validation, not activation-time. An administrator finds out
    that an endpoint is refused when they save it.
    """
    # Restored on the way out. This used to leak: `use_address_resolver` is
    # process-global, so every later test in the same pytest process resolved
    # ANY hostname to the metadata address and got a refusal it never asked
    # for. Phase 4 worked around it locally; D46 carried it to here.
    storage_config.use_address_resolver(lambda host: ["169.254.169.254"])
    try:
        session = _FakeSession()

        with pytest.raises(StorageConfigError):
            await evidence_storage_admin.create_config(
                session,
                ORG_A,
                _spec(
                    provider=PROVIDER_S3_COMPATIBLE,
                    endpoint_url="https://metadata.example.com",
                ),
            )

        assert session.added == []
        assert "commit" not in session.events
    finally:
        storage_config.use_address_resolver(None)


@pytest.mark.asyncio
async def test_an_unknown_provider_is_refused_by_name(secret_key):
    session = _FakeSession()

    with pytest.raises(evidence_storage_admin.StorageConfigSpecError) as caught:
        await evidence_storage_admin.create_config(
            session, ORG_A, _spec(provider="dropbox")
        )

    assert "dropbox" in str(caught.value)


# ---------------------------------------------------------------------------
# Criterion 24 — editing, and the draft-only rule
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status", [storage_config.STATUS_ACTIVE, storage_config.STATUS_RETIRED]
)
@pytest.mark.asyncio
async def test_only_a_draft_may_be_edited(secret_key, status):
    """An active row is where evidence is being written right now; a retired
    one may be the only place some evidence can still be read from. Editing
    either in place would move bytes that already exist.
    """
    row = _row(status=status)
    session = _FakeSession(row)

    with pytest.raises(evidence_storage_admin.StorageConfigImmutable):
        await evidence_storage_admin.update_config(session, row, _spec())

    assert "commit" not in session.events


@pytest.mark.asyncio
async def test_an_omitted_secret_leaves_the_stored_one_alone(secret_key):
    """The Settings screen renders the mask and submits the form unchanged.
    Treating that as "clear the credential" would blank it on every save.
    """
    original = crypto.encrypt("the-original-secret")
    row = _row(secret_ciphertext=original)
    session = _FakeSession(row)

    await evidence_storage_admin.update_config(
        session, row, _spec(secret_access_key="")
    )

    assert row.secret_ciphertext == original


def test_a_partial_edit_keeps_every_field_it_does_not_name():
    """``model_fields_set`` rather than ``is not None``: an explicit null and
    an omitted field are different intents, and only the omitted one keeps the
    stored value.
    """
    row = _row(bucket="original-bucket", region="eu-west-2", path_style=True)
    body = evidence_storage_api.EvidenceStorageConfigUpdateRequest(
        bucket="new-bucket"
    )

    merged = evidence_storage_api._merged_spec(row, body)

    assert merged.bucket == "new-bucket"
    assert merged.region == "eu-west-2"
    assert merged.path_style is True
    assert merged.provider == row.provider
    # Omitted secret is an empty string, which the service reads as "leave it".
    assert merged.secret_access_key == ""


# ---------------------------------------------------------------------------
# Criterion 29 — rotation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rotation_bumps_the_key_version_and_re_encrypts(secret_key, quiet):
    row = _row(
        status=storage_config.STATUS_DRAFT,
        secret_ciphertext=crypto.encrypt("old-secret"),
        key_version=1,
    )
    session = _FakeSession(row)

    await evidence_storage_admin.rotate_secret(session, row, "brand-new-secret")

    assert row.key_version == 2
    assert crypto.decrypt(row.secret_ciphertext) == "brand-new-secret"
    assert quiet == ["bump_version", "invalidate"]


@pytest.mark.asyncio
async def test_rotation_announces_only_after_the_commit(secret_key, quiet):
    """A version bump that raced a rollback would tell every worker to re-read
    a row that was never written.
    """
    row = _row(secret_ciphertext=crypto.encrypt("old-secret"))
    session = _FakeSession(row)

    original_commit = session.commit

    async def commit():
        assert quiet == [], "announced before the commit"
        await original_commit()

    session.commit = commit

    await evidence_storage_admin.rotate_secret(session, row, "brand-new-secret")

    assert quiet == ["bump_version", "invalidate"]


@pytest.mark.asyncio
async def test_rotation_never_clears_a_credential(secret_key):
    """An empty rotation would take the store offline silently. Deleting the
    configuration is the explicit way to stop using it.
    """
    row = _row(secret_ciphertext=crypto.encrypt("old-secret"))
    session = _FakeSession(row)

    with pytest.raises(evidence_storage_admin.StorageConfigSpecError):
        await evidence_storage_admin.rotate_secret(session, row, "   ")

    assert crypto.decrypt(row.secret_ciphertext) == "old-secret"


@pytest.mark.asyncio
async def test_rotating_an_active_row_probes_with_the_new_credential_first(
    secret_key, quiet, monkeypatch
):
    """An active row is the store evidence is being written to right now.
    Committing a credential that does not work would take it offline and the
    administrator would find out from a failed upload, not from the button.
    """
    seen: List[str] = []

    async def probe(config, scope):
        seen.append(config.secret_access_key)
        return {"success": False, "steps": [{"name": "put", "ok": False}]}

    monkeypatch.setattr(evidence_storage_admin, "run_connection_probe", probe)

    row = _row(
        status=storage_config.STATUS_ACTIVE,
        secret_ciphertext=crypto.encrypt("old-secret"),
    )
    session = _FakeSession(row)

    with pytest.raises(evidence_storage_admin.StorageActivationError) as caught:
        await evidence_storage_admin.rotate_secret(session, row, "new-secret")

    assert seen == ["new-secret"], "the probe ran with the old credential"
    assert "put" in str(caught.value)
    # Nothing was written and nothing was announced.
    assert crypto.decrypt(row.secret_ciphertext) == "old-secret"
    assert row.key_version == 1
    assert "commit" not in session.events
    assert quiet == []


@pytest.mark.asyncio
async def test_a_draft_is_not_probed_on_rotation(secret_key, quiet, monkeypatch):
    """Activation will probe it, and a draft resolves for nobody in the
    meantime. A retired row is not probed either: its store may legitimately be
    unreachable while its credential still needs rotating so old evidence stays
    readable.
    """
    probed: List[str] = []

    async def probe(config, scope):
        probed.append(scope)
        return {"success": True, "steps": []}

    monkeypatch.setattr(evidence_storage_admin, "run_connection_probe", probe)

    for status_value in (
        storage_config.STATUS_DRAFT,
        storage_config.STATUS_RETIRED,
    ):
        row = _row(
            status=status_value, secret_ciphertext=crypto.encrypt("old-secret")
        )
        await evidence_storage_admin.rotate_secret(
            _FakeSession(row), row, "new-secret"
        )

    assert probed == []


# ---------------------------------------------------------------------------
# Criterion 29 — the rotation reaches every process
# ---------------------------------------------------------------------------


class _Clock:
    """A monotonic clock a test can move. Two seconds of convergence is
    asserted rather than waited out, so the suite stays fast and the window is
    a number in the test rather than a sleep that hides it.
    """

    def __init__(self):
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _stored(secret: str, version: str) -> storage_config.StoredConfigRow:
    return storage_config.StoredConfigRow(
        config_id="cfg-rotating",
        organization_id=str(ORG_A),
        provider=PROVIDER_AWS_S3,
        bucket="tenant-evidence",
        region="eu-west-2",
        endpoint_url="",
        public_endpoint="",
        path_style=False,
        sse_mode="AES256",
        access_key_id="AKIAEXAMPLE",
        secret_access_key=secret,
        key_version=version,
    )


@pytest.mark.asyncio
async def test_a_rotation_reaches_a_second_process_within_the_check_interval(
    monkeypatch,
):
    """Criterion 29. Two resolvers over one row list and one version counter is
    the honest model of the API process and a Celery worker: nobody calls
    ``invalidate()`` on the worker's resolver, and it converges anyway because
    it re-reads the shared version key.

    A single resolver told to invalidate itself would prove only that
    ``invalidate()`` works, which was never in doubt.
    """
    clock = _Clock()
    monkeypatch.setattr(storage_config.time, "monotonic", clock)

    version = {"value": "1"}
    monkeypatch.setattr(storage_config, "_read_version", lambda: version["value"])
    monkeypatch.setattr(
        storage_config,
        "_incr_version",
        lambda: version.__setitem__("value", str(int(version["value"]) + 1)),
    )

    rows = [_stored("old-secret", "1")]
    api_process = storage_config.StorageConfigResolver(loader=lambda: list(rows))
    worker_process = storage_config.StorageConfigResolver(loader=lambda: list(rows))

    before_api = api_process.resolve(str(ORG_A))
    before_worker = worker_process.resolve(str(ORG_A))
    assert before_api.credential_version == before_worker.credential_version == "1"
    old_key = before_worker.client_cache_key(ROLE_INTERNAL)

    # -- the rotation, in the API process only --------------------------
    rows[0] = _stored("new-secret", "2")
    api_process.bump_version()

    # The worker has not been touched, and before the check interval elapses it
    # is still entitled to its snapshot.
    clock.advance(storage_config.VERSION_CHECK_INTERVAL_SECONDS / 2)
    assert worker_process.resolve(str(ORG_A)).credential_version == "1"

    # Past the interval it re-reads the version key, sees the change, and
    # reloads. Nothing invalidated it.
    clock.advance(storage_config.VERSION_CHECK_INTERVAL_SECONDS)
    after = worker_process.resolve(str(ORG_A))

    assert after.credential_version == "2"
    assert after.secret_access_key == "new-secret"

    new_key = after.client_cache_key(ROLE_INTERNAL)
    assert new_key != old_key, "the worker would have reused its cached client"
    # Both halves of the key changed: the version alone would be enough, and
    # the fingerprint alone would be enough. Having both is what makes a
    # rotation that forgot to bump the version still produce a new client.
    assert after.credential_version != before_worker.credential_version
    assert after.credential_fingerprint != before_worker.credential_fingerprint


def test_a_rotated_configuration_does_not_reuse_the_cached_client():
    """The cache key is what forces the rebuild, so it is asserted against the
    real cache rather than by reading the key tuple twice.
    """
    s3_service.reset_client_cache()

    before = storage_config.config_from_row(_stored("old-secret", "1"))
    after = storage_config.config_from_row(_stored("new-secret", "2"))

    client_before = s3_service._client(before, ROLE_INTERNAL)
    client_after = s3_service._client(after, ROLE_INTERNAL)

    assert client_before is not client_after
    assert before.client_cache_key(ROLE_INTERNAL) in s3_service._client_cache

    # And the process-local sweep a writer performs drops the old one outright,
    # so nothing can hand it back even by key collision.
    storage_service.invalidate()
    assert before.client_cache_key(ROLE_INTERNAL) not in s3_service._client_cache
    assert s3_service._client_cache == {}


# ---------------------------------------------------------------------------
# Retire and delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retiring_leaves_the_row_so_old_evidence_stays_readable(quiet):
    row = _row(status=storage_config.STATUS_ACTIVE)
    session = _FakeSession(row)

    await evidence_storage_admin.retire_config(session, row)

    assert row.status == storage_config.STATUS_RETIRED
    assert session.deleted == []
    assert quiet == ["bump_version", "invalidate"]


@pytest.mark.asyncio
async def test_an_active_configuration_cannot_be_deleted():
    """Deleting the store an organisation is writing to is never what someone
    meant. Retiring first is one call that makes the intent explicit.
    """
    row = _row(status=storage_config.STATUS_ACTIVE)
    session = _FakeSession(row)

    with pytest.raises(evidence_storage_admin.StorageConfigImmutable):
        await evidence_storage_admin.delete_config(session, row)

    assert session.deleted == []


@pytest.mark.asyncio
async def test_a_configuration_evidence_still_lives_under_is_refused_with_a_count():
    """The foreign key is ON DELETE RESTRICT, so the database would refuse it
    too — as an IntegrityError that reaches an administrator as a 500 and says
    nothing about what to do next. Counting first turns it into a 409 naming
    how many files are in the way.
    """
    row = _row(status=storage_config.STATUS_RETIRED)
    session = _FakeSession(row, count_result=7)

    with pytest.raises(evidence_storage_admin.StorageConfigInUse) as caught:
        await evidence_storage_admin.delete_config(session, row)

    assert caught.value.file_count == 7
    assert "7" in str(caught.value)
    assert session.deleted == []


@pytest.mark.asyncio
async def test_an_unreferenced_retired_configuration_deletes(quiet):
    row = _row(status=storage_config.STATUS_RETIRED)
    session = _FakeSession(row, count_result=0)

    await evidence_storage_admin.delete_config(session, row)

    assert session.deleted == [row]
    assert quiet == ["bump_version", "invalidate"]


# ---------------------------------------------------------------------------
# Criterion 24 — who may call these, and cross-tenant anti-enumeration (D34)
# ---------------------------------------------------------------------------


WRITE_HANDLERS = [
    "create_evidence_storage_config",
    "update_evidence_storage_config",
    "activate_evidence_storage_config",
    "rotate_evidence_storage_secret",
    "retire_evidence_storage_config",
    "delete_evidence_storage_config",
]
READ_HANDLERS = [
    "list_evidence_storage_configs",
    "get_evidence_storage_config",
    "read_effective_evidence_storage_config",
]


@pytest.mark.parametrize("name", WRITE_HANDLERS + READ_HANDLERS)
def test_every_endpoint_requires_an_organisation_administrator(name):
    """Asserted at the dependency's closure rather than by calling it, because
    ``require_org_role("editor")`` and ``require_org_role("admin")`` are the
    same object to any test that only checks the name.
    """
    handler = getattr(evidence_storage_api, name)
    dependency = inspect.signature(handler).parameters["membership"].default.dependency

    assert dependency.__qualname__.startswith("require_org_role")
    assert list(dependency.__code__.co_freevars) == ["min_role"]
    assert [cell.cell_contents for cell in dependency.__closure__] == ["admin"]


@pytest.mark.parametrize("name", WRITE_HANDLERS)
def test_every_mutation_is_rate_limited(name):
    """Each of these either dials an address the caller chose or writes a row.
    slowapi's registry is name-mangled, which is why it is read this way.
    """
    marked = getattr(limiter, "_Limiter__marked_for_limiting")
    assert f"api.evidence_storage.{name}" in marked


@pytest.mark.parametrize("name", READ_HANDLERS)
def test_the_read_endpoints_are_rate_limited_too(name):
    marked = getattr(limiter, "_Limiter__marked_for_limiting")
    assert f"api.evidence_storage.{name}" in marked


@pytest.mark.asyncio
async def test_another_tenants_configuration_is_a_404_and_not_a_403():
    """D34. A 403 would confirm the id exists, which turns any of these routes
    into an oracle for enumerating other tenants' configuration ids.
    """
    from fastapi import HTTPException

    foreign = _row(organization_id=ORG_B)
    session = _FakeSession(foreign)

    with pytest.raises(HTTPException) as caught:
        await evidence_storage_api._writable_row(ORG_A, foreign.id, session)

    assert caught.value.status_code == 404


@pytest.mark.asyncio
async def test_a_configuration_that_does_not_exist_answers_identically():
    """Same status and same body, so the two cases are indistinguishable."""
    from fastapi import HTTPException

    missing = _FakeSession(None)
    foreign = _FakeSession(_row(organization_id=ORG_B))

    with pytest.raises(HTTPException) as absent:
        await evidence_storage_api._writable_row(ORG_A, uuid.uuid4(), missing)
    with pytest.raises(HTTPException) as not_yours:
        await evidence_storage_api._writable_row(ORG_A, uuid.uuid4(), foreign)

    assert absent.value.status_code == not_yours.value.status_code == 404
    assert absent.value.detail == not_yours.value.detail


@pytest.mark.asyncio
async def test_the_platform_row_is_not_writable_through_an_organisation_url():
    """The platform-scope row holds the catalogue workbook and reconciliation
    artefacts for every tenant. No request arriving through one organisation's
    URL may write to it, platform administrator or not — that surface is the
    platform admin one.
    """
    from fastapi import HTTPException

    platform_row = _row(organization_id=None)
    session = _FakeSession(platform_row)

    with pytest.raises(HTTPException) as caught:
        await evidence_storage_api._writable_row(ORG_A, platform_row.id, session)

    assert caught.value.status_code == 404


def test_the_literal_test_path_is_declared_before_the_id_path():
    """Route order, asserted rather than assumed. ``/evidence-storage/test``
    declared after ``/evidence-storage/{config_id}`` would be parsed as a
    configuration id and answered with a 422 about an invalid UUID.
    """
    paths = [
        getattr(route, "path", "")
        for route in evidence_storage_api.router.routes
    ]
    literal = paths.index("/organizations/{org_id}/evidence-storage/test")
    parameterised = [
        i
        for i, path in enumerate(paths)
        if path == "/organizations/{org_id}/evidence-storage/{config_id}"
    ]

    assert parameterised, "the id route is missing"
    assert literal < min(parameterised)


# ---------------------------------------------------------------------------
# Criteria 25 to 28 — where a storage credential may come from
# ---------------------------------------------------------------------------


def test_no_storage_credential_can_be_set_from_the_database():
    """Criteria 25 to 27. ``TIER3_NAMES`` is the closed list of names the
    database tier will answer for, and ``get_secret`` tests membership of it
    before consulting the provider at all. No storage credential is on it, so
    the database tier is structurally unreachable for every one of them —
    evidence storage credentials live in ``evidence_storage_configs``, per
    organisation, not in the name-keyed global table.
    """
    from services import secrets

    storage_names = {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AZURE_STORAGE_ACCOUNT_KEY",
        "AZURE_STORAGE_ACCOUNT_NAME",
        "MINIO_ROOT_USER",
        "MINIO_ROOT_PASSWORD",
    }

    assert storage_names & set(secrets.TIER3_NAMES) == set()


def test_the_minio_root_credentials_stay_host_only():
    """Criterion 28's database half. The bundled MinIO's root pair is the
    account that can create and delete buckets and rewrite policy; it is on the
    deny-list so no code path can put it in, or read it from, the database.
    """
    from services import secrets

    assert "MINIO_ROOT_USER" in secrets.NEVER_DB_NAMES
    assert "MINIO_ROOT_PASSWORD" in secrets.NEVER_DB_NAMES


def test_no_storage_code_path_reads_the_minio_root_credentials():
    """Criteria 27 and 28, code half. Asserted by reading the storage modules
    rather than by exercising them, because a path that reads the root account
    only in an error branch would still be a path that reads the root account.

    Phase 4 owns the other half: provisioning a scoped MinIO account for the
    application and pointing the bundled configuration at it, so that the pair
    the backend holds is not the root pair.

    Reading a variable means naming it, so the check is for the *name* as a
    live string constant. Docstrings are excluded through the syntax tree
    rather than by a text rule, and comments never reach the tree at all: prose
    saying the root account must not be read is not a read of it.
    """
    import ast
    import pathlib

    services_dir = pathlib.Path(storage_config.__file__).parent
    modules = [
        services_dir / "storage_config.py",
        services_dir / "storage_service.py",
        services_dir / "s3_service.py",
        services_dir / "evidence_storage_admin.py",
        pathlib.Path(evidence_storage_api.__file__),
    ]

    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                body = getattr(node, "body", None)
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                assert "MINIO_ROOT_" not in node.value, (
                    f"{module.name}:{node.lineno}"
                )


def test_the_legacy_environment_path_resolves_through_file_aware_lookup():
    """Criterion 63. ``resolve_from_env`` reads the AWS pair through
    ``services.secrets.get_secret``, which understands the ``{NAME}_FILE``
    convention itself. That is what let the two shim lines come out of
    ``scripts/docker/with-file-secrets.sh``: the secrets overlay still mounts
    both files and sets both ``*_FILE`` variables, and the difference is only
    who reads them.
    """
    source = inspect.getsource(storage_config.resolve_from_env)

    assert "_legacy_credential" in source
    assert "os.getenv(\"AWS_SECRET_ACCESS_KEY\")" not in source

    legacy = inspect.getsource(storage_config._legacy_credential)
    assert "get_secret" in legacy


def test_a_file_backed_aws_credential_is_resolved_without_the_shim(tmp_path, monkeypatch):
    """The behaviour the previous test asserts structurally, exercised: with
    only ``AWS_SECRET_ACCESS_KEY_FILE`` set and the variable itself empty —
    which is exactly what the secrets overlay produces — the configuration
    still carries the credential.
    """
    from services import secrets

    secret_file = tmp_path / "aws_secret"
    secret_file.write_text("from-a-mounted-file\n", encoding="utf-8")
    key_file = tmp_path / "aws_key"
    key_file.write_text("AKIAFROMFILE\n", encoding="utf-8")

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID_FILE", str(key_file))
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY_FILE", str(secret_file))
    secrets.reset_caches()

    config = storage_config.resolve_from_env(str(ORG_A))

    assert config.access_key_id == "AKIAFROMFILE"
    assert config.secret_access_key == "from-a-mounted-file"


def test_a_provider_that_needs_an_endpoint_will_not_silently_become_amazon():
    """An empty endpoint means "Amazon S3, derived from the region". A MinIO or
    generic S3 row saved without one would quietly become an Amazon row pointed
    at a bucket that is not there, and the first failure would be an upload.
    """
    # MinIO falls back to the preset's own endpoint, so the mistake arrives as
    # an address refusal naming the scheme rather than as a wrong store.
    resolved = evidence_storage_admin.resolved_from_spec(
        _spec(provider=PROVIDER_MINIO, endpoint_url=""),
        config_id="c",
        organization_id=str(ORG_A),
    )
    assert resolved.endpoint_url == storage_config.BUNDLED_MINIO_ENDPOINT

    # The generic preset has no endpoint to fall back to, so it is refused.
    with pytest.raises(evidence_storage_admin.StorageConfigSpecError) as caught:
        evidence_storage_admin.resolved_from_spec(
            _spec(provider=PROVIDER_S3_COMPATIBLE, endpoint_url=""),
            config_id="c",
            organization_id=str(ORG_A),
        )
    assert "endpoint" in str(caught.value).lower()

    # Amazon S3 alone is allowed to have none.
    amazon = evidence_storage_admin.resolved_from_spec(
        _spec(provider=PROVIDER_AWS_S3, endpoint_url=""),
        config_id="c",
        organization_id=str(ORG_A),
    )
    assert amazon.endpoint_url == ""


# ---------------------------------------------------------------------------
# Criterion 30 — where the store came from, and who manages it
# ---------------------------------------------------------------------------
#
# The repair these cover closes the half of criterion 30 that was missing: the
# environment store already kept working, but nothing on this API said so.
# Phase 5 renders a "Managed by operator" chip, and it needs two things to do
# it — the provenance of a row it is listing, and the provenance of whatever is
# actually in force when the organisation has no row at all. Without the second
# one an organisation with zero rows gets `{"items": []}` and the screen has
# nothing to say, which is the state every existing installation is in.


def _env_resolver(monkeypatch, rows):
    """A resolver over a fixed row list, with the shared version key stubbed.

    ``_read_version`` would otherwise reach for redis. Stubbing it keeps this a
    unit test while leaving the resolution order itself — org, platform,
    environment — entirely real: these tests assert the production resolver's
    answer, not a re-implementation of it.
    """
    monkeypatch.setattr(storage_config, "_read_version", lambda: None)
    return storage_config.StorageConfigResolver(loader=lambda: list(rows))


def _platform_stored(**overrides) -> storage_config.StoredConfigRow:
    values: Dict[str, Any] = dict(
        config_id=str(uuid.uuid4()),
        organization_id=None,
        provider=PROVIDER_MINIO,
        bucket="platform-evidence",
        region="us-east-1",
        endpoint_url="https://minio.example.test",
        public_endpoint="",
        path_style=True,
        sse_mode="none",
        access_key_id="AKIAPLATFORM",
        secret_access_key="platform-secret-value",
        key_version="4",
    )
    values.update(overrides)
    return storage_config.StoredConfigRow(**values)


def _org_stored(**overrides) -> storage_config.StoredConfigRow:
    return _platform_stored(
        **{
            "organization_id": str(ORG_A),
            "provider": PROVIDER_AWS_S3,
            "bucket": "tenant-evidence",
            "region": "eu-west-2",
            "endpoint_url": "",
            "path_style": False,
            "sse_mode": "AES256",
            "access_key_id": "AKIATENANT",
            "secret_access_key": "tenant-secret-value",
            "key_version": "2",
            **overrides,
        }
    )


def test_a_row_carries_its_own_scope_as_its_source():
    """An organisation's row is theirs to edit; the platform row is not.

    This is a property of the row's scope, not of a resolution — which is why
    it is on the row response and not only on the effective read. Every write
    verb already answers 404 for the platform row through an organisation URL,
    so the client is being told what the API will already enforce.
    """
    org = evidence_storage_api._serialise(_row(organization_id=ORG_A))
    platform = evidence_storage_api._serialise(_row(organization_id=None))

    assert (org.source, org.managed_by_operator) == ("org", False)
    assert (platform.source, platform.managed_by_operator) == ("platform", True)
    # The two names are the resolver's own constants, not a second vocabulary.
    assert org.source == storage_config.SOURCE_ORG
    assert platform.source == storage_config.SOURCE_PLATFORM


def test_create_list_and_get_all_render_the_same_row_model():
    """All three go through one serialiser and one response model, so the two
    fields cannot be present on one screen's call and absent on another's.
    """
    routes = {
        route.name: route
        for route in evidence_storage_api.router.routes
        if hasattr(route, "name")
    }

    assert (
        routes["create_evidence_storage_config"].response_model
        is evidence_storage_api.EvidenceStorageConfigResponse
    )
    assert (
        routes["get_evidence_storage_config"].response_model
        is evidence_storage_api.EvidenceStorageConfigResponse
    )
    listed = evidence_storage_api.EvidenceStorageConfigListResponse.model_fields[
        "items"
    ].annotation
    assert listed == List[evidence_storage_api.EvidenceStorageConfigResponse]


def test_an_organisations_own_active_row_is_what_is_in_force(monkeypatch):
    """Criterion 30, the `org` leg. The resolver is the real one."""
    row = _org_stored()
    resolver = _env_resolver(monkeypatch, [row, _platform_stored()])

    effective = evidence_storage_api._effective(resolver.resolve(str(ORG_A)))

    assert effective.source == "org"
    assert effective.managed_by_operator is False
    assert effective.configured is True
    assert str(effective.config_id) == row.config_id
    assert effective.bucket == "tenant-evidence"
    assert effective.region == "eu-west-2"
    assert effective.key_version == 2
    # An empty address field is absent, not an empty string.
    assert effective.endpoint_url is None


def test_an_organisation_with_no_row_of_its_own_is_on_the_operators_store(
    monkeypatch,
):
    """Criterion 30, the `platform` leg. The organisation is still tagged onto
    the value object — that is the key prefix — but the *source* is the
    platform's, and an organisation administrator cannot edit that row.
    """
    platform = _platform_stored()
    resolver = _env_resolver(monkeypatch, [platform])

    resolved = resolver.resolve(str(ORG_A))
    effective = evidence_storage_api._effective(resolved)

    assert resolved.organization_id == str(ORG_A), "the key prefix must survive"
    assert effective.source == "platform"
    assert effective.managed_by_operator is True
    assert str(effective.config_id) == platform.config_id
    assert effective.bucket == "platform-evidence"
    assert effective.key_version == 4


def test_with_no_rows_at_all_the_environment_is_the_labelled_fallback(
    monkeypatch,
):
    """Criterion 30, the `legacy_env` leg, and the whole point of the repair.

    This is the state every installation that predates the feature is in: zero
    rows, evidence still being written, and — before this — an API that said
    nothing about where. The environment keeps working (D40 leaves the resolver
    order alone) and is now *labelled*: operator-managed, with no row behind it
    to point a client at.
    """
    monkeypatch.setenv("EVIDENCE_BUCKET", "legacy-evidence")
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://minio:9000")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    resolver = _env_resolver(monkeypatch, [])

    effective = evidence_storage_api._effective(resolver.resolve(str(ORG_A)))

    assert effective.source == "legacy_env"
    assert effective.managed_by_operator is True
    assert effective.configured is True
    assert effective.config_id is None, "there is no row to point at"
    assert effective.key_version is None, "an environment credential has none"
    assert effective.bucket == "legacy-evidence"
    assert effective.endpoint_url == "http://minio:9000"


def test_an_installation_that_has_configured_nothing_says_so_rather_than_none(
    monkeypatch,
):
    """There is no ``none`` source, and this test is why.

    ``resolve`` cannot produce one: with no rows it falls through to
    ``resolve_from_env``, which synthesises a ``legacy_env`` configuration
    whether or not the environment names a bucket. Reporting ``none`` here
    would be inventing a state the resolver does not have. What is reported
    instead is the true one — the environment is what would be consulted, and
    it names no bucket — and ``configured`` is the flag the Settings screen
    keys its blank-and-editable state off.
    """
    monkeypatch.delenv("EVIDENCE_BUCKET", raising=False)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "")
    resolver = _env_resolver(monkeypatch, [])

    effective = evidence_storage_api._effective(resolver.resolve(str(ORG_A)))

    assert effective.source == "legacy_env"
    assert effective.managed_by_operator is True
    assert effective.configured is False
    assert effective.bucket is None
    assert effective.config_id is None


def test_the_effective_read_carries_no_credential_at_all(monkeypatch):
    """Criterion 42, on the new surface.

    Two assertions, because either alone is weak. The field set is checked
    against the names a credential could hide behind — this surface carries no
    mask and no access key id, so ``secret`` is forbidden outright rather than
    excepted — and the serialised body is checked against a planted secret, so
    a field added later under an innocent name still fails here.
    """
    fields = set(
        evidence_storage_api.EvidenceStorageEffectiveResponse.model_fields
    )
    for forbidden in ("secret", "ciphertext", "password", "key_id", "token"):
        assert not any(forbidden in name for name in fields), fields

    planted = "PLAINTEXT-EFFECTIVE-CANARY-4b2e1"
    resolver = _env_resolver(
        monkeypatch, [_org_stored(secret_access_key=planted)]
    )
    resolved = resolver.resolve(str(ORG_A))
    assert resolved.secret_access_key == planted, "positive control"

    body = evidence_storage_api._effective(resolved).model_dump_json()
    assert planted not in body
    assert "AKIATENANT" not in body, "not even the access key id"


def test_the_literal_effective_path_is_matched_as_itself_and_not_as_an_id():
    """Route precedence, proved by matching rather than by reading the source.

    Declared after ``/evidence-storage/{config_id}``, the literal would be
    parsed as a configuration id and answered with a 422 about an invalid UUID
    — an endpoint that exists and is unreachable. Starlette picks the first
    route that matches, so the honest assertion is which route a real request
    scope resolves to.
    """
    from starlette.routing import Match

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/organizations/{ORG_A}/evidence-storage/effective",
        "path_params": {},
        "root_path": "",
        "headers": [],
    }

    matched = None
    for route in evidence_storage_api.router.routes:
        if route.matches(scope)[0] is Match.FULL:
            matched = route
            break

    assert matched is not None, "no route matches the effective path"
    assert matched.name == "read_effective_evidence_storage_config"
    assert matched.path == "/organizations/{org_id}/evidence-storage/effective"
