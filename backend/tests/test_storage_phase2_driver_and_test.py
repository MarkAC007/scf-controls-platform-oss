"""Phase 2 of bring-your-own evidence storage: one driver, four presets, and a
real connection test.

ISA 20260912-0930, criteria 16 to 23.

None of these tests touch a network or a database. Three deliberate choices
about how they are built are worth stating, because each of them is the
difference between a test that proves something and a test that passes.

**The presets are tested for what they cause, not for what they say.** Asserting
that the Google Cloud Storage preset carries ``path_style=True`` would only
restate the table. Every preset test therefore goes through
``s3_service.client_kwargs`` and reads the addressing style, signature version,
region and endpoint that actually reach botocore.

**Google Cloud Storage cannot be reached from here, so criterion 17 is proved
by configuration and by signature shape.** A real boto3 client is built from the
GCS preset and asked to sign a request offline; the test asserts the request
line and the SigV4 credential scope that Google's S3-compatible XML API
requires. That is as far as an offline test can go, and the handoff note says
so: the live leg against Google is an operator acceptance step, not a unit test.

**Name resolution is stubbed, never live.** The address guard's whole job is to
behave differently for different answers from the resolver, and a test that
depended on real DNS would be both slow and a liar the first time it ran on a
machine with a split-horizon resolver. The rebinding test in particular needs
one hostname to answer *differently* at save time and at connect time, which no
live resolver will do on demand.
"""
from __future__ import annotations

import inspect
import uuid
from typing import List, Optional
from urllib.parse import unquote, urlparse

import boto3
import pytest
from botocore.exceptions import ClientError
from sqlalchemy.sql import Select, Update

# Imported for its side effect only: `models.System` carries a relationship to
# `SystemCatalogTemplate`, which lives here, and SQLAlchemy cannot configure any
# mapper until both modules are in the registry. Phase 3 made activation write a
# platform audit row, so this file now instantiates an ORM object and needs the
# registry complete even when it runs on its own.
import catalog_models  # noqa: F401
from api import evidence_storage as evidence_storage_api
from rate_limiting import limiter
from services import evidence_storage_admin, s3_service, storage_config
from services.storage_config import (
    PROVIDER_AWS_S3,
    PROVIDER_GCS,
    PROVIDER_MINIO,
    PROVIDER_S3_COMPATIBLE,
    ROLE_INTERNAL,
    ROLE_PROBE,
    SSE_AES256,
    SSE_NONE,
    StorageConfigError,
)

PUBLIC_ADDRESS = "93.184.216.34"
PRIVATE_ADDRESS = "10.1.2.3"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _restore_module_state():
    """Leave the resolver hook and the client cache as they were found.

    ``use_address_resolver`` and the boto3 client cache are module state shared
    with every other test in the suite. A test that swapped the resolver and
    failed before restoring it would make unrelated tests fail somewhere else
    entirely, which is the hardest kind of failure to read.
    """
    yield
    storage_config.use_address_resolver(None)
    s3_service.reset_client_cache()


def _resolver(*addresses: str):
    """A stub resolver that always answers with ``addresses``."""

    def resolve(host: str) -> List[str]:
        return list(addresses)

    return resolve


def _failing_resolver(exc: Exception = None):
    def resolve(host: str) -> List[str]:
        raise exc or OSError("Name or service not known")

    return resolve


def _config(**overrides) -> storage_config.ResolvedStorageConfig:
    """A tenant-supplied configuration unless a test says otherwise."""
    base = dict(
        config_id="cfg-phase2",
        source=storage_config.SOURCE_ORG,
        provider=PROVIDER_S3_COMPATIBLE,
        bucket="evidence-bucket",
        region="eu-west-2",
        endpoint_url="https://objects.example.net",
        path_style=True,
        access_key_id="AKIAEXAMPLE",
        secret_access_key="s3cr3t-value",
        organization_id="11111111-1111-1111-1111-111111111111",
        endpoint_is_operator_supplied=False,
    )
    base.update(overrides)
    return storage_config.ResolvedStorageConfig(**base)


def _addressing_style(kwargs: dict) -> str:
    return kwargs["config"].s3["addressing_style"]


# ---------------------------------------------------------------------------
# Criterion 16 — one driver, four presets
# ---------------------------------------------------------------------------


def test_the_preset_table_names_exactly_the_four_supported_providers():
    assert set(storage_config.PRESETS) == {
        PROVIDER_MINIO,
        PROVIDER_AWS_S3,
        PROVIDER_GCS,
        PROVIDER_S3_COMPATIBLE,
    }


def test_the_presets_are_data_and_not_subclasses():
    """Criterion 16. Four providers, one driver.

    The thing that makes this true is that every provider reaches botocore
    through the same builder. If a provider ever grew its own client builder
    this assertion is where it would show up.
    """
    for provider in storage_config.PRESETS:
        preset = storage_config.preset_for(provider)
        assert isinstance(preset, storage_config.ProviderPreset)
        assert type(preset) is storage_config.ProviderPreset

    source = inspect.getsource(s3_service._build_client)
    assert source.count("boto3.client") == 1


def test_there_is_one_encryption_table_and_not_two():
    """``_sse_mode_for_provider`` must read the preset table rather than keep a
    second copy of it — two tables drift, and the one that drifts is the one
    nobody is looking at."""
    for provider, preset in storage_config.PRESETS.items():
        assert storage_config._sse_mode_for_provider(provider) == preset.sse_mode


@pytest.mark.parametrize(
    "provider,expected_endpoint,expected_style,expected_region,expected_sse",
    [
        (PROVIDER_MINIO, "http://minio:9000", "path", "eu-west-1", SSE_NONE),
        (PROVIDER_AWS_S3, None, "auto", "eu-west-1", SSE_AES256),
        (
            PROVIDER_GCS,
            "https://storage.googleapis.com",
            "path",
            "auto",
            SSE_NONE,
        ),
        (PROVIDER_S3_COMPATIBLE, None, "path", "eu-west-1", SSE_NONE),
    ],
)
def test_each_preset_produces_the_client_configuration_its_provider_needs(
    provider, expected_endpoint, expected_style, expected_region, expected_sse
):
    """Criterion 16, one case per preset.

    Read through ``client_kwargs`` rather than off the preset, so this asserts
    the configuration botocore is handed rather than the table it came from.
    """
    config = storage_config.config_from_preset(provider, bucket="b")
    kwargs = s3_service.client_kwargs(config)

    assert kwargs.get("endpoint_url") == expected_endpoint
    assert _addressing_style(kwargs) == expected_style
    assert kwargs["config"].signature_version == "s3v4"
    assert kwargs["region_name"] == expected_region
    assert config.sse_mode == expected_sse


def test_the_aws_preset_passes_no_endpoint_at_all():
    """An empty endpoint is not the same as a blank one: boto3 derives the AWS
    endpoint from the region, and passing ``endpoint_url=""`` would break it."""
    kwargs = s3_service.client_kwargs(
        storage_config.config_from_preset(PROVIDER_AWS_S3, bucket="b")
    )
    assert "endpoint_url" not in kwargs


def test_credentials_are_passed_explicitly_when_the_config_carries_them():
    kwargs = s3_service.client_kwargs(_config())
    assert kwargs["aws_access_key_id"] == "AKIAEXAMPLE"
    assert kwargs["aws_secret_access_key"] == "s3cr3t-value"


def test_a_config_without_credentials_defers_to_the_boto3_chain():
    """The AWS instance-role / IRSA case, and the only one where ambient
    credential resolution is intended rather than accidental."""
    kwargs = s3_service.client_kwargs(
        _config(access_key_id="", secret_access_key="")
    )
    assert "aws_access_key_id" not in kwargs
    assert "aws_secret_access_key" not in kwargs


# ---------------------------------------------------------------------------
# Criterion 17 — Google Cloud Storage
# ---------------------------------------------------------------------------


def test_the_gcs_preset_targets_the_s3_compatible_xml_api():
    """Criterion 17, part one: configuration.

    The XML API is reached at ``storage.googleapis.com`` with HMAC
    interoperability keys, path-style addressing and no SSE header — Google
    encrypts at rest unconditionally and rejects ``x-amz-server-side-encryption``
    on this API.
    """
    preset = storage_config.preset_for(PROVIDER_GCS)
    assert preset.endpoint_url == "https://storage.googleapis.com"
    assert preset.endpoint_is_fixed is True
    assert preset.path_style is True
    assert preset.sse_mode == SSE_NONE
    assert preset.requires_credentials is True

    config = storage_config.config_from_preset(PROVIDER_GCS, bucket="gcs-bucket")
    assert s3_service._sse_kwargs(config) == {}


def test_a_gcs_signed_request_has_the_shape_the_xml_api_requires():
    """Criterion 17, part two: signature shape.

    Google Cloud Storage cannot be reached from a unit test, so this signs a
    request offline and asserts what Google would receive: SigV4, the
    ``auto`` credential scope the XML API expects, and a path-style URL. The
    live leg is an operator acceptance step, recorded in the handoff note.
    """
    config = storage_config.config_from_preset(
        PROVIDER_GCS,
        bucket="gcs-bucket",
        access_key_id="GOOG1EEXAMPLE",
        secret_access_key="hmac-secret",
    )
    client = boto3.client("s3", **s3_service.client_kwargs(config))
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": "gcs-bucket", "Key": "evidence/probe.txt"},
        ExpiresIn=60,
    )

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    # Path style: the bucket is in the path, not in the hostname.
    assert parsed.hostname == "storage.googleapis.com"
    assert parsed.path == "/gcs-bucket/evidence/probe.txt"

    query = unquote(parsed.query)
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in query
    assert "/auto/s3/aws4_request" in query
    assert "GOOG1EEXAMPLE" in query
    assert "hmac-secret" not in url


# ---------------------------------------------------------------------------
# Criteria 18 to 21 — the connection test
# ---------------------------------------------------------------------------


class _FakeBody:
    def __init__(self, payload: bytes):
        self._payload = payload
        self.closed = False

    def read(self):
        return self._payload

    def close(self):
        self.closed = True


class _FakeS3Client:
    """A stand-in store. Records calls and answers however a test asks it to."""

    def __init__(self, *, put=None, get=None, delete=None, stored=None):
        self.calls: List[str] = []
        self._put = put
        self._get = get
        self._delete = delete
        self._stored = stored if stored is not None else s3_service.PROBE_BODY

    def _answer(self, name, behaviour, default):
        self.calls.append(name)
        if isinstance(behaviour, Exception):
            raise behaviour
        return default if behaviour is None else behaviour

    def put_object(self, **kwargs):
        self.last_put = kwargs
        return self._answer(
            "put", self._put, {"ResponseMetadata": {"HTTPStatusCode": 200}}
        )

    def get_object(self, **kwargs):
        return self._answer(
            "get",
            self._get,
            {
                "ResponseMetadata": {"HTTPStatusCode": 200},
                "Body": _FakeBody(self._stored),
            },
        )

    def delete_object(self, **kwargs):
        return self._answer(
            "delete", self._delete, {"ResponseMetadata": {"HTTPStatusCode": 204}}
        )


def _client_error(status: int, message: str) -> ClientError:
    return ClientError(
        {
            "Error": {"Code": "AccessDenied", "Message": message},
            "ResponseMetadata": {"HTTPStatusCode": status},
        },
        "PutObject",
    )


def _install_client(monkeypatch, client) -> None:
    """Put ``client`` behind the probe, and let the address step succeed.

    The probe validates the endpoint address before it builds a client, so a
    test about put/get/delete has to give the resolver a public answer or it
    never reaches the steps it is about.
    """
    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS))
    monkeypatch.setattr(s3_service, "_client", lambda config, role=None: client)


def _steps_by_name(report: dict) -> dict:
    return {step["name"]: step for step in report["steps"]}


def test_a_successful_probe_reports_put_get_and_delete_in_order(monkeypatch):
    """Criterion 18, in its offline form. The live leg against the bundled
    MinIO is recorded in the handoff note."""
    client = _FakeS3Client()
    _install_client(monkeypatch, client)

    report = s3_service.probe_round_trip(_config(), "org-1")

    assert report["success"] is True
    assert [step["name"] for step in report["steps"]] == list(s3_service.PROBE_STEPS)
    assert all(step["ok"] for step in report["steps"])
    assert client.calls == ["put", "get", "delete"]


def test_the_probe_writes_under_the_organisations_own_probe_prefix():
    org_id = str(uuid.uuid4())
    key = s3_service.probe_key(org_id)
    assert key.startswith(f"evidence/{org_id}/.probe/")
    assert key != s3_service.probe_key(org_id)


def test_the_probe_deletes_what_it_wrote_even_when_the_read_fails(monkeypatch):
    """A probe that left its object behind would litter a tenant's bucket with
    one unreadable file per failed attempt."""
    client = _FakeS3Client(get=_client_error(403, "Access Denied for probe key"))
    _install_client(monkeypatch, client)

    report = s3_service.probe_round_trip(_config(), "org-1")

    assert report["success"] is False
    assert "delete" in client.calls
    steps = _steps_by_name(report)
    assert steps["put"]["ok"] is True
    assert steps["get"]["ok"] is False


def _assert_one_entry_per_step(report: dict) -> None:
    """Every probe report holds exactly one entry per step name, in order.

    Asserted on the **raw list**, never through ``_steps_by_name``: that helper
    is a name-keyed dict, so a duplicated step silently collapses into it and
    the last entry wins. The defect this guards against was exactly that — the
    cleanup delete appended a second ``delete`` row next to a ``NotAttempted``
    one, and the dict helper could not see it.
    """
    names = [step["name"] for step in report["steps"]]
    assert names == list(s3_service.PROBE_STEPS), names
    assert len(report["steps"]) == len(set(names)) == len(s3_service.PROBE_STEPS)


def test_the_cleanup_delete_is_reported_once_not_twice(monkeypatch):
    """Criterion 19. A step reported twice is not a per-step result.

    The delete runs in a ``finally`` after the failed get has already decided
    the probe is over. Its real outcome must **replace** what the report would
    otherwise say about the delete, not sit beside it: an administrator shown
    one row saying the delete was never attempted and another saying it
    returned 204 cannot tell whether their bucket has a stray object in it.
    """
    client = _FakeS3Client(get=_client_error(403, "Access Denied for probe key"))
    _install_client(monkeypatch, client)

    report = s3_service.probe_round_trip(_config(), "org-1")

    _assert_one_entry_per_step(report)
    assert [step["name"] for step in report["steps"]] == [
        "address",
        "put",
        "get",
        "delete",
    ]

    delete = report["steps"][-1]
    assert delete["ok"] is True
    assert delete["status_code"] == 204
    assert "error_class" not in delete
    assert client.calls == ["put", "get", "delete"]
    assert report["success"] is False


def test_a_cleanup_delete_that_fails_is_reported_once_with_its_real_outcome(
    monkeypatch,
):
    """The same single-entry rule when the cleanup itself fails.

    This is the case where a stray object really is left behind, so the one
    delete row has to carry the failure rather than a ``NotAttempted``
    placeholder that would read as "nothing was written".
    """
    client = _FakeS3Client(
        get=_client_error(403, "denied"),
        delete=_client_error(403, "denied"),
    )
    _install_client(monkeypatch, client)

    report = s3_service.probe_round_trip(_config(), "org-1")

    _assert_one_entry_per_step(report)
    delete = report["steps"][-1]
    assert delete["ok"] is False
    assert delete["status_code"] == 403
    assert delete["error_class"] == "ClientError"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({}, id="all-steps-succeed"),
        pytest.param({"put": _client_error(403, "denied")}, id="put-fails"),
        pytest.param({"get": _client_error(403, "denied")}, id="get-fails"),
        pytest.param({"stored": b"something else"}, id="content-mismatch"),
        pytest.param({"delete": _client_error(403, "denied")}, id="delete-fails"),
        pytest.param(
            {"get": _client_error(403, "denied"), "delete": _client_error(500, "boom")},
            id="get-and-delete-fail",
        ),
    ],
)
def test_every_probe_path_reports_each_step_exactly_once(monkeypatch, kwargs):
    """Criterion 19, as a property of every path rather than of one of them.

    The report is built once, after the cleanup has run, so no caller can ever
    hold a list that is still being appended to.
    """
    _install_client(monkeypatch, _FakeS3Client(**kwargs))

    report = s3_service.probe_round_trip(_config(), "org-1")

    _assert_one_entry_per_step(report)
    for step in report["steps"]:
        assert set(step) <= {"name", "ok", "status_code", "error_class"}


def test_a_refused_address_still_reports_each_step_exactly_once():
    """The earliest exit of all: no client is ever built, so nothing can run in
    the cleanup, and the report is still four rows."""
    storage_config.use_address_resolver(_resolver("169.254.169.254"))

    report = s3_service.probe_round_trip(
        _config(endpoint_url="https://metadata.example.com"), "org-1"
    )

    _assert_one_entry_per_step(report)
    assert [step["ok"] for step in report["steps"]] == [False, False, False, False]


def test_a_read_that_returns_different_bytes_is_a_failure(monkeypatch):
    """A store that accepts a write and answers the read with something else is
    not a working evidence store, even though every call returned 200."""
    _install_client(monkeypatch, _FakeS3Client(stored=b"not what was written"))

    report = s3_service.probe_round_trip(_config(), "org-1")

    assert report["success"] is False
    assert _steps_by_name(report)["get"]["error_class"] == "ContentMismatch"


def test_steps_that_were_not_reached_are_reported_rather_than_omitted(monkeypatch):
    """Criterion 19. The caller renders the whole sequence, so a probe that
    stopped early says which steps it never tried."""
    _install_client(monkeypatch, _FakeS3Client(put=_client_error(403, "nope")))

    report = s3_service.probe_round_trip(_config(), "org-1")

    steps = _steps_by_name(report)
    assert set(steps) == set(s3_service.PROBE_STEPS)
    assert steps["put"]["status_code"] == 403
    assert steps["get"]["error_class"] == "NotAttempted"
    assert steps["delete"]["error_class"] == "NotAttempted"


def test_a_refused_address_stops_the_probe_at_the_first_step():
    """Criterion 22 reaching into criterion 18: the probe is an outbound
    connection like any other and goes through the address guard."""
    storage_config.use_address_resolver(_resolver("169.254.169.254"))

    report = s3_service.probe_round_trip(
        _config(endpoint_url="https://metadata.example.com"), "org-1"
    )

    steps = _steps_by_name(report)
    assert report["success"] is False
    assert steps["address"]["ok"] is False
    assert steps["address"]["error_class"] == "StorageConfigError"
    assert steps["put"]["error_class"] == "NotAttempted"


def test_a_probe_report_carries_no_body_no_url_and_no_credential(monkeypatch):
    """Criterion 21, the anti-criterion.

    The reply goes to an organisation administrator who chose the endpoint.
    Echoing anything the far end sent back would make this a read oracle for
    every service the backend can reach.
    """
    secret_body = "SECRET-RESPONSE-BODY-a3f9"
    _install_client(
        monkeypatch,
        _FakeS3Client(put=_client_error(500, f"<Error>{secret_body}</Error>")),
    )
    config = _config(endpoint_url="https://objects.example.net")

    report = s3_service.probe_round_trip(config, "org-1")

    rendered = repr(report)
    assert secret_body not in rendered
    assert config.secret_access_key not in rendered
    assert config.access_key_id not in rendered
    assert "objects.example.net" not in rendered
    for step in report["steps"]:
        assert set(step) <= {"name", "ok", "status_code", "error_class"}


def test_the_probe_client_alone_gets_short_timeouts_and_capped_retries():
    """Criterion 18. Short timeouts belong on the probe path only: putting them
    on the upload path would turn a slow but working store into failed evidence
    uploads."""
    config = _config()
    probe = s3_service.client_kwargs(config, ROLE_PROBE)["config"]
    internal = s3_service.client_kwargs(config, ROLE_INTERNAL)["config"]

    assert probe.connect_timeout == s3_service.PROBE_CONNECT_TIMEOUT
    assert probe.read_timeout == s3_service.PROBE_READ_TIMEOUT
    assert probe.retries == {"max_attempts": s3_service.PROBE_MAX_ATTEMPTS}

    assert internal.connect_timeout != s3_service.PROBE_CONNECT_TIMEOUT
    assert internal.retries is None


# ---------------------------------------------------------------------------
# Criterion 18 — the endpoint requires authentication and is rate limited
# ---------------------------------------------------------------------------


def test_the_connection_test_endpoint_requires_an_organisation_admin():
    """Criterion 18. The response *shape* follows the two infrastructure health
    endpoints in ``api/tasks_api.py``; the authentication deliberately does
    not, because those are undecorated and this one dials an address the
    caller supplied."""
    handler = evidence_storage_api.test_evidence_storage
    dependency = inspect.signature(handler).parameters["membership"].default.dependency

    assert dependency.__qualname__.startswith("require_org_role")
    assert list(dependency.__code__.co_freevars) == ["min_role"]
    assert [cell.cell_contents for cell in dependency.__closure__] == ["admin"]


def test_the_connection_test_endpoint_is_rate_limited():
    """Each call is an outbound connection to an address the caller chose."""
    marked = getattr(limiter, "_Limiter__marked_for_limiting")
    assert "api.evidence_storage.test_evidence_storage" in marked


def test_the_reply_schema_carries_no_field_that_could_echo_the_far_end():
    """Criterion 21 again, at the schema. A field added here later would be
    filled from the probe report, so the shape is asserted rather than trusted.
    """
    assert set(evidence_storage_api.EvidenceStorageTestStep.model_fields) == {
        "name",
        "ok",
        "status_code",
        "error_class",
    }
    assert set(evidence_storage_api.EvidenceStorageTestResponse.model_fields) == {
        "success",
        "config_id",
        "steps",
    }


# ---------------------------------------------------------------------------
# Criterion 20 — a configuration cannot go active until its test passes
# ---------------------------------------------------------------------------


class _FakeRow:
    """Enough of an ``EvidenceStorageConfig`` for the activation path."""

    def __init__(self, organization_id=None, endpoint_url="", **overrides):
        self.id = uuid.uuid4()
        self.organization_id = organization_id
        self.provider = PROVIDER_AWS_S3
        self.bucket = "evidence-bucket"
        self.region = "eu-west-2"
        self.endpoint_url = endpoint_url
        self.public_endpoint = ""
        self.path_style = False
        self.sse_mode = SSE_AES256
        self.access_key_id = "AKIAEXAMPLE"
        self.secret_ciphertext = ""
        self.key_version = "1"
        self.is_bundled = False
        self.status = storage_config.STATUS_DRAFT
        self.updated_by_user_id = None
        self.updated_by_label = ""
        for key, value in overrides.items():
            setattr(self, key, value)


class _FakeResult:
    def __init__(self, row, count: int = 0):
        self._row = row
        self._count = count

    def scalar_one_or_none(self):
        return self._row

    def scalar_one(self):
        # The unstamped-file count the legacy-env branch of `activate_config`
        # asks for. Zero unless a test says otherwise, so every test written
        # before that branch existed keeps its old meaning.
        return self._count


class _FakeSession:
    """Records what the activation asked the database to do, and in what order.

    The row's status is captured at the moment each statement is issued, which
    is what makes the retire-before-activate ordering observable.
    """

    def __init__(self, row, unstamped_files: int = 0):
        self.row = row
        self.events: List[tuple] = []
        self.unstamped_files = unstamped_files

    async def execute(self, statement):
        if isinstance(statement, Select):
            # A count read is recorded under its own name so the ordering
            # assertions stay about the statements that WRITE.
            name = "count" if "count(" in str(statement) else "select"
            self.events.append((name, self.row.status if self.row else None))
            return _FakeResult(self.row, self.unstamped_files)
        assert isinstance(statement, Update)
        self.events.append(("retire", self.row.status))
        return None

    def add(self, obj):
        # Phase 3 added the platform audit row. It is recorded here so the
        # ordering assertions below can prove it lands inside the same
        # transaction as the status change rather than after it.
        self.events.append(("audit", self.row.status if self.row else None))

    async def commit(self):
        self.events.append(("commit", self.row.status))

    async def refresh(self, row):
        self.events.append(("refresh", row.status))


@pytest.fixture
def _quiet_announcements(monkeypatch):
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


def _probe_result(success: bool, failing_step: str = "put"):
    def probe(config, org_id):
        steps = []
        for name in s3_service.PROBE_STEPS:
            ok = success or name != failing_step
            steps.append({"name": name, "ok": ok})
            if not ok:
                break
        return {"success": success, "steps": steps}

    return probe


@pytest.mark.asyncio
async def test_a_configuration_whose_probe_fails_cannot_be_activated(
    monkeypatch, _quiet_announcements
):
    """Criterion 20, the gate itself.

    It lives on the service rather than on an endpoint so that Phase 3's write
    API and Phase 5's Settings screen both reach it, and so that a seeding
    script cannot walk around it.
    """
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "probe_round_trip",
        _probe_result(False, failing_step="put"),
    )
    row = _FakeRow()
    session = _FakeSession(row)

    with pytest.raises(evidence_storage_admin.StorageActivationError) as excinfo:
        await evidence_storage_admin.activate_config(session, row.id, actor="tester")

    assert "put" in str(excinfo.value)
    assert excinfo.value.report["success"] is False
    assert row.status == storage_config.STATUS_DRAFT
    assert [name for name, _ in session.events] == ["select"]
    assert _quiet_announcements == []


@pytest.mark.asyncio
async def test_activation_retires_the_previous_row_before_activating_this_one(
    monkeypatch, _quiet_announcements
):
    """Criterion 20. The partial unique indexes permit one active row per
    scope, so activating before retiring would trip the index rather than
    replace the row — and both statements must be one transaction so a crash
    between them cannot leave a scope with no active configuration.
    """
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "probe_round_trip",
        _probe_result(True),
    )
    row = _FakeRow(organization_id=uuid.uuid4())
    session = _FakeSession(row)

    result = await evidence_storage_admin.activate_config(
        session, row.id, actor="tester"
    )

    assert result is row
    assert row.status == storage_config.STATUS_ACTIVE
    names = [name for name, _ in session.events]
    # The `count` is the unstamped-file read the legacy-env guard performs; it
    # writes nothing, so it is filtered out of the ordering claim rather than
    # allowed to blur it.
    assert [n for n in names if n != "count"] == [
        "select",
        "retire",
        "audit",
        "commit",
        "refresh",
    ]
    # The retirement was issued while this row was still a draft, and exactly
    # one commit closed both statements.
    assert dict(
        (name, status) for name, status in session.events if name == "retire"
    )["retire"] == storage_config.STATUS_DRAFT
    assert names.count("commit") == 1


@pytest.mark.asyncio
async def test_the_change_is_announced_only_after_the_commit(
    monkeypatch, _quiet_announcements
):
    """A version bump that raced a rollback would tell every Celery worker to
    re-read rows that were never written."""
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "probe_round_trip",
        _probe_result(True),
    )
    row = _FakeRow()
    session = _FakeSession(row)

    committed: List[str] = []
    original_commit = session.commit

    async def commit():
        assert _quiet_announcements == [], "announced before the commit"
        committed.append("commit")
        await original_commit()

    session.commit = commit

    await evidence_storage_admin.activate_config(session, row.id)

    assert committed == ["commit"]
    assert _quiet_announcements == ["bump_version", "invalidate"]


@pytest.mark.asyncio
async def test_a_missing_row_is_an_error_and_not_a_silent_no_op():
    session = _FakeSession(None)
    with pytest.raises(evidence_storage_admin.StorageActivationError):
        await evidence_storage_admin.activate_config(session, uuid.uuid4())


@pytest.mark.asyncio
async def test_a_configuration_naming_a_refused_address_cannot_be_activated(
    monkeypatch, _quiet_announcements
):
    """Criterion 22 at the save-time call site. The probe is never even run."""
    probed: List[str] = []
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "probe_round_trip",
        lambda config, org_id: probed.append("probed") or _probe_result(True)(
            config, org_id
        ),
    )
    storage_config.use_address_resolver(_resolver("169.254.169.254"))
    row = _FakeRow(
        organization_id=uuid.uuid4(),
        provider=PROVIDER_S3_COMPATIBLE,
        endpoint_url="https://metadata.example.com",
    )
    session = _FakeSession(row)

    with pytest.raises(StorageConfigError):
        await evidence_storage_admin.activate_config(session, row.id)

    assert probed == []
    assert row.status == storage_config.STATUS_DRAFT


# ---------------------------------------------------------------------------
# Criteria 22 and 23 — the endpoint address guard
# ---------------------------------------------------------------------------


REJECTED_ADDRESSES = [
    ("loopback v4", "127.0.0.1"),
    ("loopback v4, alternate", "127.42.0.9"),
    ("RFC1918 ten-dot", "10.0.0.5"),
    ("RFC1918 172.16/12", "172.16.31.7"),
    ("RFC1918 192.168/16", "192.168.1.10"),
    ("link-local v4 / metadata", "169.254.169.254"),
    ("CGNAT 100.64/10", "100.64.3.4"),
    ("unspecified v4", "0.0.0.0"),
    ("multicast v4", "224.0.0.1"),
    ("loopback v6", "::1"),
    ("link-local v6", "fe80::1"),
    ("unique-local v6", "fd00::1234"),
    ("unspecified v6", "::"),
    ("IPv4-mapped metadata", "::ffff:169.254.169.254"),
    ("IPv4-mapped RFC1918", "::ffff:10.0.0.5"),
]


@pytest.mark.parametrize(
    "label,address", REJECTED_ADDRESSES, ids=[case[0] for case in REJECTED_ADDRESSES]
)
def test_a_tenant_endpoint_resolving_to_a_private_address_is_refused(label, address):
    """Criterion 22, one case per class of address.

    The resolver is stubbed: the point is what the guard does with an answer,
    not what any particular hostname happens to resolve to today.
    """
    storage_config.use_address_resolver(_resolver(address))
    config = _config(endpoint_url="https://store.example.com")

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(config)


@pytest.mark.parametrize(
    "label,address", REJECTED_ADDRESSES, ids=[case[0] for case in REJECTED_ADDRESSES]
)
def test_a_numeric_ip_literal_goes_through_the_same_checks(label, address):
    """A literal in the URL must not skip the policy that a name goes through.

    The resolver is made to explode so that a literal reaching it would fail
    loudly rather than pass silently.
    """
    storage_config.use_address_resolver(
        _failing_resolver(AssertionError("a literal must not be resolved"))
    )
    host = f"[{address}]" if ":" in address else address
    config = _config(endpoint_url=f"https://{host}:9000")

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(config)


@pytest.mark.parametrize(
    "hostname",
    ["printer.local", "api.localhost", "localhost", "metadata.google.internal"],
)
def test_a_tenant_endpoint_naming_an_internal_only_domain_is_refused(hostname):
    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS))
    config = _config(endpoint_url=f"https://{hostname}")

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(config)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://objects.example.net",
        "ftp://objects.example.net",
        "file:///etc/passwd",
        "gopher://objects.example.net",
        "objects.example.net",
    ],
)
def test_a_tenant_endpoint_on_any_scheme_but_https_is_refused(endpoint):
    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS))

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(_config(endpoint_url=endpoint))


def test_an_endpoint_that_embeds_credentials_is_refused():
    """``https://user:pass@host`` is both a credential leak into every log line
    and a classic parser-confusion trick."""
    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS))

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(
            _config(endpoint_url="https://someone:secret@objects.example.net")
        )


def test_a_tenant_endpoint_that_will_not_resolve_is_refused():
    """Nothing to check means the rest of the policy is unenforced, so the
    answer is no."""
    storage_config.use_address_resolver(_failing_resolver())

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(_config())


def test_a_resolver_that_answers_with_no_address_refuses_a_tenant_endpoint():
    storage_config.use_address_resolver(_resolver())

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(_config())


def test_one_private_answer_among_several_is_enough_to_refuse():
    """A host with both a public and a private record is the cheap version of a
    rebind: the guard must refuse on any answer, not on the first one."""
    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS, PRIVATE_ADDRESS))

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(_config())


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://s3.amazonaws.com",
        "https://s3.eu-west-2.amazonaws.com",
        "https://storage.googleapis.com",
        "https://objects.example.net",
        "https://objects.example.net:8443",
    ],
)
def test_a_public_https_endpoint_is_accepted(endpoint):
    """Criterion 22's other half. A guard that refused everything would pass
    every rejection test and ship a broken product."""
    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS))
    storage_config.assert_endpoint_allowed(_config(endpoint_url=endpoint))


def test_an_ipv6_public_literal_is_accepted():
    storage_config.use_address_resolver(
        _failing_resolver(AssertionError("a literal must not be resolved"))
    )
    storage_config.assert_endpoint_allowed(
        _config(endpoint_url="https://[2606:2800:220:1:248:1893:25c8:1946]")
    )


def test_an_empty_endpoint_is_the_aws_endpoint_and_needs_no_validation():
    storage_config.use_address_resolver(
        _failing_resolver(AssertionError("nothing to resolve"))
    )
    storage_config.assert_endpoint_allowed(
        _config(provider=PROVIDER_AWS_S3, endpoint_url="")
    )


# -- the bundled exemption, and its limits ----------------------------------


def test_the_bundled_minio_endpoint_is_allowed_to_the_operator():
    """The compose-internal MinIO: ``http`` on a name that does not resolve
    outside the compose network."""
    storage_config.use_address_resolver(_failing_resolver())
    storage_config.assert_endpoint_allowed(
        _config(
            provider=PROVIDER_MINIO,
            endpoint_url=storage_config.BUNDLED_MINIO_ENDPOINT,
            endpoint_is_operator_supplied=True,
        )
    )


def test_the_same_bundled_url_supplied_by_a_tenant_is_refused():
    """The exemption is a property of *who supplied the endpoint*, not of the
    URL. This is the pair of assertions that says so."""
    storage_config.use_address_resolver(_failing_resolver())

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(
            _config(
                provider=PROVIDER_MINIO,
                endpoint_url=storage_config.BUNDLED_MINIO_ENDPOINT,
                endpoint_is_operator_supplied=False,
            )
        )


def test_an_operator_endpoint_on_the_operators_own_network_is_allowed():
    """An operator pointing their own installation at their own MinIO is not an
    attacker, which is the whole reason the operator column exists."""
    storage_config.use_address_resolver(_resolver("192.168.10.20"))
    storage_config.assert_endpoint_allowed(
        _config(
            endpoint_url="http://minio.internal:9000",
            endpoint_is_operator_supplied=True,
        )
    )


@pytest.mark.parametrize(
    "address",
    ["169.254.169.254", "fe80::1", "::ffff:169.254.169.254", "224.0.0.1", "0.0.0.0"],
)
def test_a_metadata_or_link_local_address_is_refused_for_every_row(address):
    """**The invariant.**

    Link-local, multicast and unspecified addresses are refused for every row
    whatever its flags. Before this phase ``is_bundled`` bypassed the validator
    entirely, so a bundled row naming 169.254.169.254 was accepted; the
    exemption now covers only the ``http`` scheme and the compose-internal
    private address of the bundled MinIO host.

    No tenant-facing path may ever set ``is_bundled``. That constraint binds
    Phase 3 and Phase 5 and is stated in the handoff note.
    """
    storage_config.use_address_resolver(_resolver(address))

    with pytest.raises(StorageConfigError):
        storage_config.assert_endpoint_allowed(
            _config(
                endpoint_url="http://minio.internal:9000",
                endpoint_is_operator_supplied=True,
            )
        )


def test_a_refusal_names_the_class_of_address_and_never_the_address():
    """A refusal that quoted the resolved IP would answer "what is behind this
    name" for anyone who can save a configuration — the same read-oracle
    problem the probe report avoids."""
    storage_config.use_address_resolver(_resolver(PRIVATE_ADDRESS))

    with pytest.raises(StorageConfigError) as excinfo:
        storage_config.assert_endpoint_allowed(_config())

    message = str(excinfo.value)
    assert PRIVATE_ADDRESS not in message
    assert "private" in message


# -- save time and connect time ---------------------------------------------


def test_the_save_time_call_site_applies_the_same_policy():
    """Criterion 22 requires the check at save as well as at connect."""
    storage_config.use_address_resolver(_resolver(PRIVATE_ADDRESS))

    with pytest.raises(StorageConfigError):
        storage_config.validate_config_for_save(_config())

    storage_config.use_address_resolver(_resolver(PUBLIC_ADDRESS))
    storage_config.validate_config_for_save(_config())


def test_a_name_that_rebinds_after_the_save_is_refused_at_connect_time():
    """Criterion 23.

    One hostname, two answers: public when the configuration was saved, private
    when the driver dials it. The guard runs inside ``_client`` *before* the
    client cache is consulted, so the second call is re-checked rather than
    served a cached client — which is exactly how a DNS rebind would otherwise
    get through.
    """
    config = _config(endpoint_url="https://rebind.example.com")

    answers = {"value": [PUBLIC_ADDRESS]}
    storage_config.use_address_resolver(lambda host: list(answers["value"]))

    # Save time: the name answers publicly and the configuration is accepted.
    storage_config.validate_config_for_save(config)

    # First connect, still public: a client is built and cached.
    first = s3_service._client(config)
    assert first is s3_service._client(config)

    # The name rebinds to the internal network.
    answers["value"] = [PRIVATE_ADDRESS]

    with pytest.raises(StorageConfigError):
        s3_service._client(config)


def test_the_guard_runs_before_the_client_cache_and_not_inside_the_builder():
    """The placement is the mechanism, so it is asserted rather than assumed. A
    check moved into ``_build_client`` would be skipped for the entire life of
    a cached client."""
    source = inspect.getsource(s3_service._client)
    guard = source.index("assert_endpoint_allowed")
    cache = source.index("_client_cache.get")
    assert guard < cache
    assert "assert_endpoint_allowed" not in inspect.getsource(s3_service._build_client)


# ---------------------------------------------------------------------------
# Repair, finding 4 — rows written under `legacy_env` are never stamped
#
# `_row_id_or_none` returns None for the environment-synthesised configuration,
# because it has no row, and the stamping UPDATE was then skipped entirely. On
# an installation with evidence and no platform row, every pre-existing file
# stayed NULL through the activation and afterwards resolved by organisation —
# that is, to the NEW store, which does not hold its bytes.
#
# Refusing is the honest answer. There is no correct stamp available, and the
# alternative is a silent, unrecoverable mis-resolution of every existing file.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activation_refuses_when_legacy_env_leaves_files_unstamped(
    monkeypatch, _quiet_announcements
):
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "probe_round_trip",
        _probe_result(True),
    )
    monkeypatch.setattr(
        evidence_storage_admin.storage_config,
        "resolve",
        lambda org: type(
            "R", (), {"config_id": storage_config.LEGACY_ENV_CONFIG_ID}
        )(),
    )
    row = _FakeRow(organization_id=uuid.uuid4())
    session = _FakeSession(row, unstamped_files=4)

    with pytest.raises(evidence_storage_admin.StorageActivationError) as excinfo:
        await evidence_storage_admin.activate_config(session, row.id, actor="tester")

    # The count is a named field, not a number buried in prose, so the screen
    # can be actionable without parsing the sentence.
    assert excinfo.value.details == {"unstamped_files": 4}
    # And nothing was written: no retire, no activate, no commit.
    assert row.status == storage_config.STATUS_DRAFT
    assert [name for name, _ in session.events if name not in ("select", "count")] == []
    assert _quiet_announcements == []


@pytest.mark.asyncio
async def test_activation_proceeds_under_legacy_env_when_no_file_is_unstamped(
    monkeypatch, _quiet_announcements
):
    """The other branch. A fresh organisation with no evidence has nothing to
    mis-resolve, so the refusal would be obstruction rather than protection."""
    monkeypatch.setattr(
        evidence_storage_admin.storage_service,
        "probe_round_trip",
        _probe_result(True),
    )
    monkeypatch.setattr(
        evidence_storage_admin.storage_config,
        "resolve",
        lambda org: type(
            "R", (), {"config_id": storage_config.LEGACY_ENV_CONFIG_ID}
        )(),
    )
    row = _FakeRow(organization_id=uuid.uuid4())
    session = _FakeSession(row, unstamped_files=0)

    result = await evidence_storage_admin.activate_config(
        session, row.id, actor="tester"
    )

    assert result is row
    assert row.status == storage_config.STATUS_ACTIVE
