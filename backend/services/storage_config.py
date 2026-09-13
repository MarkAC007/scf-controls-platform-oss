"""Resolved evidence-storage configuration.

Phase 0 of the bring-your-own evidence storage work (#967).

Evidence storage settings used to be module-level constants in ``s3_service``,
read from the environment at *import* time. Three things followed from that, all
of which this module exists to undo:

* configuration could not change without restarting the process;
* ``SSE_ENABLED`` was derived from another import-time constant
  (``not AWS_ENDPOINT_URL``), so changing the endpoint could never change the
  encryption behaviour;
* the boto3 clients were module globals, so a rotated credential reached only
  the code paths that happened to rebuild a client — in practice, none of them.

Instead, every storage operation now resolves a :class:`ResolvedStorageConfig`
and carries it. The value object is deliberately a plain frozen dataclass with
no I/O of its own: it is the seam that later phases widen.

Resolution sources, in the order the resolver consults them:

``org``
    The organisation's own active configuration row.
``platform``
    The platform default configuration row — the row whose ``organization_id``
    is NULL — used for artefacts that belong to the platform rather than a
    tenant (the catalogue workbook, catalogue-upgrade diffs, reconciliation
    detail blobs).
``legacy_env``
    Synthesised from the process environment. It remains the fallback so that an
    operator who has never opened the Settings screen keeps working unchanged,
    and so that a process with no database reachable still resolves something.

Rows are read through a short-lived synchronous connection and cached for
:data:`CACHE_TTL_SECONDS`, because :func:`resolve` is called on *every* storage
operation and from synchronous code (Celery tasks) as well as from the async
API. A writer announces a change by calling :func:`bump_version`, which INCRs
the shared ``scf:storage:version`` Redis key; readers re-check that key at most
every :data:`VERSION_CHECK_INTERVAL_SECONDS`, so a change reaches a Celery
worker in about two seconds rather than in a minute. This is deliberately the
same mechanism ``services/secrets.py`` already uses, not a second invention.
Redis is best effort throughout: if it is unreachable the TTL alone governs and
nothing raises.

**The credential cache is process-local and so is the failure mode.** If the
database cannot be read at all and this process has no earlier snapshot, the
resolver falls back to the environment rather than raising, which is what keeps
the platform serving evidence on an installation that has no rows — every
installation, until Phase 4 seeds the bundled one. The trade-off is recorded
rather than hidden: once rows exist, a database outage would resolve an
organisation to the legacy store instead of refusing. A row whose secret is
present but *undecryptable* is treated differently and does raise, because there
the configuration plainly exists and silently writing those bytes somewhere else
would be worse than an error.

What later phases add here, and why the shape is what it is:

* **Phase 2** attaches a provider preset and validates ``endpoint_url``. A
  tenant-supplied endpoint is a server-side request forgery surface (ISA §7), so
  the config carries :attr:`endpoint_is_operator_supplied` to record *who* chose
  the endpoint, and :func:`assert_endpoint_allowed` is the hook the driver
  already calls on **every** operation rather than once per client — which is
  what makes ISA's "re-check at connect time, not only at save time" a change of
  one function body rather than a restructure.

Nothing in this module reads the environment at import time. That rule is
enforced mechanically by ``tests/test_storage_phase0_facade.py``.
"""
from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import socket
import time
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: Configuration sources, most specific first. See the module docstring.
SOURCE_ORG = "org"
SOURCE_PLATFORM = "platform"
SOURCE_LEGACY_ENV = "legacy_env"

#: Providers. Phase 2 turns these into presets — see :data:`PRESETS`. There is
#: still exactly one driver; a preset is data, not a subclass.
PROVIDER_AWS_S3 = "aws_s3"
PROVIDER_GCS = "gcs"
PROVIDER_MINIO = "minio"
PROVIDER_S3_COMPATIBLE = "s3_compatible"

#: Server-side encryption modes. ``none`` is not an absence of configuration —
#: it is the correct answer for MinIO, which rejects SSE-S3 without a KMS.
SSE_NONE = "none"
SSE_AES256 = "AES256"

#: The identifier used for the environment-synthesised configuration. Every
#: other config id is a row's UUID, which cannot collide with this literal.
LEGACY_ENV_CONFIG_ID = "legacy-env"

#: Row lifecycle. Only ``active`` rows are ever resolved; ``draft`` is a config
#: being filled in and tested, ``retired`` is kept because evidence files still
#: point at it.
STATUS_DRAFT = "draft"
STATUS_ACTIVE = "active"
STATUS_RETIRED = "retired"

#: Shared version key, INCRed by a writer so other processes notice. Named
#: alongside ``scf:secrets:version`` and used in exactly the same way.
REDIS_VERSION_KEY = "scf:storage:version"
CACHE_TTL_SECONDS = 60.0
VERSION_CHECK_INTERVAL_SECONDS = 2.0
REDIS_SOCKET_TIMEOUT = 0.2

_DEFAULT_REGION = "eu-west-1"
_DEFAULT_URL_EXPIRY = 900
_DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024

#: Client roles. Presigned URLs are handed to a browser, which generally cannot
#: resolve an internal container hostname, so they are signed against the
#: public endpoint. Signing is offline, so no connectivity is needed to it.
ROLE_INTERNAL = "internal"
ROLE_PRESIGN = "presign"
#: The connection-test probe. A separate role so that the probe's short
#: timeouts and capped retries never leak into the clients that serve real
#: traffic, and so a probe client is cached separately from them.
ROLE_PROBE = "probe"


class StorageConfigError(RuntimeError):
    """Raised when a resolved configuration may not be used as-is."""


# ---------------------------------------------------------------------------
# Provider presets — data, not subclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderPreset:
    """What a provider choice means, as a value.

    Three of the four providers speak S3 natively and the fourth — Google Cloud
    Storage — exposes an S3-compatible XML API with HMAC keys, so boto3 reaches
    all four by varying *these fields* rather than by growing a second driver.
    That is ISC 16: one driver, four presets.
    """

    provider: str
    #: Human label for the Settings dropdown (Phase 5).
    label: str
    #: The endpoint the driver dials. Empty means "AWS S3's own endpoint",
    #: which boto3 derives from the region — and which has no tenant-controlled
    #: address at all, so no forgery surface.
    endpoint_url: str
    #: True when the endpoint is a property of the provider rather than
    #: something an operator supplies. Phase 3/5 render the field read-only.
    endpoint_is_fixed: bool
    #: Whether to address buckets as ``endpoint/bucket/key``. Virtual-host
    #: addressing needs the bucket name to be a legal DNS label and needs a
    #: wildcard certificate, which is why every non-AWS preset is path-style.
    path_style: bool
    #: Always SigV4. Pinned rather than left to botocore's default so that the
    #: signature a GCS or generic endpoint receives is not a function of the
    #: installed botocore version.
    signature_version: str
    #: Server-side encryption. ``none`` is the correct answer for MinIO and for
    #: GCS over the XML API, both of which reject SSE-S3 without a KMS.
    sse_mode: str
    #: Region used when the row names none. SigV4 needs *a* region in the
    #: credential scope even where the provider ignores it.
    default_region: str
    #: Whether an access key pair is required. False for AWS alone, because an
    #: instance role or IRSA supplies credentials ambiently there.
    requires_credentials: bool


class StorageNotConfigured(ValueError):
    """No evidence store applies here.

    A ``ValueError`` subclass on purpose: every caller written before Phase 7
    catches ``ValueError`` and must keep working. What it adds is a type the
    API layer can single out, so "there is no store" gets the one actionable
    409 naming the Settings screen (``api/storage_gate.py``, ISC 53) while a
    genuinely bad argument keeps its own 400.
    """


#: The bundled MinIO, as compose names it. The one endpoint for which the
#: ``http`` scheme is expected rather than tolerated.
BUNDLED_MINIO_ENDPOINT = "http://minio:9000"

PRESETS: Dict[str, ProviderPreset] = {
    PROVIDER_MINIO: ProviderPreset(
        provider=PROVIDER_MINIO,
        # Not "MinIO (bundled)" (D49 F6): the preset describes a KIND of
        # store, and an organisation pointing at its own MinIO would read as
        # bundled under a "Managed in app" chip. Whether a store is the one
        # this installer put in the stack is carried by `source` and
        # `is_bundled` on the effective read, which is where the UI takes it
        # from.
        label="MinIO",
        endpoint_url=BUNDLED_MINIO_ENDPOINT,
        endpoint_is_fixed=False,
        path_style=True,
        signature_version="s3v4",
        sse_mode=SSE_NONE,
        default_region=_DEFAULT_REGION,
        requires_credentials=True,
    ),
    PROVIDER_AWS_S3: ProviderPreset(
        provider=PROVIDER_AWS_S3,
        label="Amazon S3",
        endpoint_url="",
        endpoint_is_fixed=True,
        path_style=False,
        signature_version="s3v4",
        sse_mode=SSE_AES256,
        default_region=_DEFAULT_REGION,
        requires_credentials=False,
    ),
    PROVIDER_GCS: ProviderPreset(
        provider=PROVIDER_GCS,
        label="Google Cloud Storage",
        # The S3-compatible XML API. Reached with an HMAC key pair created
        # under "Interoperability" in the Cloud Storage settings; a service
        # account JSON key will NOT work here (ISA R6).
        endpoint_url="https://storage.googleapis.com",
        endpoint_is_fixed=True,
        path_style=True,
        signature_version="s3v4",
        sse_mode=SSE_NONE,
        # GCS encrypts at rest unconditionally and does not accept an
        # ``x-amz-server-side-encryption`` header over the XML API.
        default_region="auto",
        requires_credentials=True,
    ),
    PROVIDER_S3_COMPATIBLE: ProviderPreset(
        provider=PROVIDER_S3_COMPATIBLE,
        label="Other S3-compatible",
        endpoint_url="",
        endpoint_is_fixed=False,
        path_style=True,
        signature_version="s3v4",
        sse_mode=SSE_NONE,
        default_region=_DEFAULT_REGION,
        requires_credentials=True,
    ),
}


def preset_for(provider: str) -> ProviderPreset:
    """The preset for ``provider``, falling back to the generic one.

    An unrecognised provider resolves to the generic S3-compatible preset
    rather than raising: the database ``CHECK`` constraint already refuses a
    value outside the vocabulary, so reaching here with one means a row written
    before that constraint existed, and path-style SigV4 against the row's own
    endpoint is the safest reading of it.
    """
    return PRESETS.get(provider, PRESETS[PROVIDER_S3_COMPATIBLE])


@dataclass(frozen=True)
class ResolvedStorageConfig:
    """One fully-resolved evidence store, as a value.

    Every field that used to be a module constant in ``s3_service`` lives here.
    The object is frozen so that a config handed to a driver cannot be mutated
    underneath a cached client — the cache key is derived from these fields, and
    a mutable config would make that key a lie.
    """

    #: Identity of the configuration itself. ``legacy-env`` for the synthesised
    #: environment config; a database row id from Phase 1 onwards.
    config_id: str
    #: Which source produced this. One of the ``SOURCE_*`` constants.
    source: str
    #: Which provider preset this is. One of the ``PROVIDER_*`` constants.
    provider: str

    bucket: str = ""
    region: str = _DEFAULT_REGION
    #: Empty means "AWS S3 default endpoint". Any other value is an
    #: S3-compatible store and is the SSRF surface Phase 2 validates.
    endpoint_url: str = ""
    #: Externally reachable endpoint used only when signing browser-facing URLs.
    #: Empty means "sign against :attr:`endpoint_url`".
    public_endpoint: str = ""
    path_style: bool = False
    #: One of the ``SSE_*`` constants. Derived from the provider, never from
    #: another import-time constant.
    sse_mode: str = SSE_NONE

    #: An identifier, not a secret. Empty means "let boto3 resolve credentials
    #: from its ambient chain" — which is how an IAM instance role or IRSA still
    #: works, and the only case in which credentials are not passed explicitly.
    access_key_id: str = ""
    secret_access_key: str = field(default="", repr=False)
    session_token: str = field(default="", repr=False)
    #: Bumped by a rotation. Phase 1 fills this from the row's ``key_version``;
    #: for the environment source it is left empty and the credential
    #: fingerprint carries the same weight. Both are in the client cache key, so
    #: a rotation that forgets to bump the version still gets a fresh client.
    credential_version: str = ""

    url_expiry: int = _DEFAULT_URL_EXPIRY
    max_file_size: int = _DEFAULT_MAX_FILE_SIZE

    #: ``None`` means platform scope. Phase 1 populates this from the row.
    organization_id: Optional[str] = None

    #: True when the endpoint came from the operator's own environment or from
    #: the bundled installer, rather than from a tenant administrator typing it
    #: into a form. Phase 2's SSRF validation keys off this: an operator who
    #: points their own install at their own network is not an attacker, but an
    #: org admin supplying an arbitrary URL is exactly ISA §7's threat.
    endpoint_is_operator_supplied: bool = True

    #: True only for the store the installer provisioned inside this stack —
    #: the bundled MinIO. Distinct from
    #: :attr:`endpoint_is_operator_supplied`, which is also true for the
    #: synthesised environment config; this one is the row's own ``is_bundled``
    #: column and nothing else. Phase 7 exposes it on the effective read so the
    #: Settings card can stop inferring "bundled" from
    #: ``source == 'platform' and provider == 'minio'`` (D48) — an inference
    #: that was sound only while the installer was the sole writer of platform
    #: rows, which is not a property anyone should have to keep true.
    is_bundled: bool = False

    # -- derived ---------------------------------------------------------

    @property
    def is_configured(self) -> bool:
        """Whether this config names somewhere to actually put bytes."""
        return bool(self.bucket)

    @property
    def sse_enabled(self) -> bool:
        """Whether to request server-side encryption on writes."""
        return self.sse_mode != SSE_NONE

    @property
    def credential_fingerprint(self) -> str:
        """A short digest of the credential triple — never the credential.

        This is what makes ISC-8 hold. A rotated secret produces a different
        fingerprint, a different cache key, and therefore a newly-built client
        for *every* operation, not only for the offline signing paths that
        happened to rebuild one.
        """
        material = "\x00".join(
            (self.access_key_id, self.secret_access_key, self.session_token)
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def endpoint_for(self, role: str) -> str:
        """The endpoint a client in ``role`` should talk to."""
        if role == ROLE_PRESIGN and self.public_endpoint:
            return self.public_endpoint
        return self.endpoint_url

    def client_cache_key(self, role: str) -> Tuple:
        """Identity of a boto3 client built from this config.

        Includes the credential fingerprint as well as ``credential_version``
        deliberately — see :attr:`credential_fingerprint`.
        """
        return (
            self.config_id,
            self.credential_version,
            self.credential_fingerprint,
            role,
            self.endpoint_for(role),
            self.region,
            self.path_style,
        )


# ---------------------------------------------------------------------------
# Endpoint safety — the Phase 2 seam
# ---------------------------------------------------------------------------


#: Host suffixes that never name a public object store. ``.internal`` is GCP's
#: metadata domain, ``.local`` is mDNS, ``.localhost`` is reserved for loopback.
BLOCKED_HOST_SUFFIXES = (".local", ".localhost", ".internal")
BLOCKED_HOST_NAMES = ("localhost",)

#: Schemes the driver will dial. A tenant gets ``https`` alone; the operator's
#: own install keeps ``http`` because the bundled MinIO is reached over it
#: inside the compose network, where there is no transport to protect.
TENANT_ALLOWED_SCHEMES = ("https",)
OPERATOR_ALLOWED_SCHEMES = ("http", "https")


def _resolve_host_addresses(host: str) -> List[str]:
    """Every A and AAAA record for ``host``.

    Split out so a test can substitute a resolver and make the same hostname
    answer differently at save time and at connect time — which is the only way
    to demonstrate ISC 23 without a live DNS server.
    """
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


#: The resolver the address guard uses. Replaced only by tests, via
#: :func:`use_address_resolver`.
_address_resolver: Callable[[str], List[str]] = _resolve_host_addresses


def use_address_resolver(resolver: Optional[Callable[[str], List[str]]]) -> None:
    """Test hook: replace name resolution. ``None`` restores the real one."""
    global _address_resolver
    _address_resolver = resolver or _resolve_host_addresses


def _parse_ip_literal(host: str):
    """The host as an IP address, or ``None`` when it is a name.

    A bracketed IPv6 literal arrives from :func:`urlparse` already unwrapped,
    and a scope id (``fe80::1%eth0``) is stripped before parsing.
    """
    candidate = host.split("%", 1)[0]
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        return None


def _address_rejection(address, operator_supplied: bool) -> Optional[str]:
    """Why ``address`` may not be dialled, or ``None`` if it may.

    The ordering is the policy. Link-local, multicast and the unspecified
    address are refused for **every** configuration whatever its flags, because
    no evidence store is ever reachable at one of them and 169.254.169.254 is
    the cloud instance metadata service — the first thing a forged request goes
    looking for. Loopback and private ranges are refused only for a
    tenant-supplied endpoint: an operator pointing their own installation at
    their own network is not an attacker.
    """
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            # ::ffff:169.254.169.254 is the metadata service wearing a hat.
            address = mapped

    if address.is_link_local:
        return "link-local"
    if address.is_multicast:
        return "multicast"
    if address.is_unspecified:
        return "unspecified"

    if address.is_loopback:
        return None if operator_supplied else "loopback"
    if address.is_private:
        # Covers RFC1918 and IPv6 unique-local fc00::/7.
        return None if operator_supplied else "private"
    if operator_supplied:
        return None
    if not address.is_global:
        # Carrier-grade NAT 100.64.0.0/10 and the assorted reserved blocks,
        # none of which `is_private` reports.
        return "not publicly routable"
    return None


def assert_endpoint_allowed(config: ResolvedStorageConfig) -> None:
    """Refuse a configuration whose endpoint must not be dialled (ISA §7).

    An organisation administrator who can supply an ``endpoint_url`` can make
    the backend issue requests to an address they choose. That is server-side
    request forgery, and on a cloud host the first target is the instance
    metadata service at 169.254.169.254. This is the function that stops it.

    **The driver calls this on every operation, before the client cache is
    consulted** (``s3_service._client``). That placement is what gives ISC 23:
    a hostname that answered with a public address when the configuration was
    saved is resolved again at connect time, so a DNS rebinding between the two
    is caught rather than walked through. Do not move the call site into the
    client *builder* — a cached client would then never be re-checked.

    The rules, by who supplied the endpoint:

    ===================  ====================  ===============================
    Rule                 Tenant-supplied       Operator-supplied
    ===================  ====================  ===============================
    scheme               ``https`` only        ``http`` or ``https``
    credentials in URL   refused               refused
    ``.local`` etc.      refused               allowed
    loopback / RFC1918   refused               allowed
    CGNAT / reserved     refused               allowed
    link-local, 0.0.0.0  **refused**           **refused**
    multicast            **refused**           **refused**
    resolution failure   refused               tolerated
    ===================  ====================  ===============================

    The operator column is deliberately narrower than the flag used to be.
    Before this phase ``is_bundled`` bypassed the validator completely, so a
    bundled row naming 169.254.169.254 was accepted; now the metadata address is
    refused for every row regardless of flags, and the exemption covers only the
    ``http`` scheme and the compose-internal private address of the bundled
    MinIO host. **No tenant-facing path may ever set ``is_bundled``** — it is a
    security-relevant column, not a UI hint, and that constraint binds Phase 3
    and Phase 5.

    A resolution failure is tolerated for an operator-supplied endpoint because
    ``minio`` does not resolve outside the compose network and a transient
    resolver hiccup must not take evidence storage down; the connection itself
    then fails on its own, exactly as it did before this phase. For a
    tenant-supplied endpoint a name that will not resolve is refused, because
    there is nothing to check and admitting it would leave the rest of the
    policy unenforced.
    """
    endpoint = (config.endpoint_url or "").strip()
    if not endpoint:
        # AWS S3's own endpoint, derived by boto3 from the region. There is no
        # tenant-controlled address here, so there is nothing to validate.
        return

    _assert_endpoint_url_allowed(
        endpoint,
        operator_supplied=config.endpoint_is_operator_supplied,
        config_id=config.config_id,
    )


def _assert_endpoint_url_allowed(
    endpoint: str,
    *,
    operator_supplied: bool,
    config_id: str = "",
) -> None:
    """The body of :func:`assert_endpoint_allowed`, on a bare URL string.

    Error messages name the *class* of the problem and never the address that
    was resolved. Echoing the resolved IP back to the caller would turn a
    refusal into a read oracle for the internal network, which is the same
    reason the connection probe reports steps rather than response bodies.
    """
    parsed = urlparse(endpoint)
    scheme = (parsed.scheme or "").lower()

    allowed_schemes = (
        OPERATOR_ALLOWED_SCHEMES if operator_supplied else TENANT_ALLOWED_SCHEMES
    )
    if scheme not in allowed_schemes:
        raise StorageConfigError(
            f"Storage endpoint scheme {scheme or '(none)'!r} is not allowed. "
            f"Use {' or '.join(allowed_schemes)}."
        )

    if parsed.username or parsed.password:
        raise StorageConfigError(
            "Storage endpoint must not embed credentials in the URL."
        )

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise StorageConfigError("Storage endpoint names no host.")

    if not operator_supplied:
        if host in BLOCKED_HOST_NAMES or host.endswith(BLOCKED_HOST_SUFFIXES):
            raise StorageConfigError(
                f"Storage endpoint host {host!r} names an internal-only "
                "domain and is not allowed."
            )

    literal = _parse_ip_literal(host)
    if literal is not None:
        reason = _address_rejection(literal, operator_supplied)
        if reason:
            _refuse(host, reason, config_id)
        return

    try:
        addresses = _address_resolver(host)
    except Exception as exc:  # noqa: BLE001 — any resolver failure is the same
        if operator_supplied:
            logger.debug(
                "Storage endpoint host %s did not resolve (%s); allowing "
                "because the endpoint is operator-supplied",
                host,
                type(exc).__name__,
            )
            return
        raise StorageConfigError(
            f"Storage endpoint host {host!r} could not be resolved."
        ) from None

    if not addresses:
        if operator_supplied:
            return
        raise StorageConfigError(
            f"Storage endpoint host {host!r} could not be resolved."
        )

    for raw in addresses:
        parsed_address = _parse_ip_literal(str(raw))
        if parsed_address is None:
            # A resolver that answered with something that is not an address.
            raise StorageConfigError(
                f"Storage endpoint host {host!r} could not be resolved."
            )
        reason = _address_rejection(parsed_address, operator_supplied)
        if reason:
            _refuse(host, reason, config_id)


def _refuse(host: str, reason: str, config_id: str) -> None:
    logger.warning(
        "Refusing evidence storage endpoint for config %s: host %s is %s",
        config_id or "(unsaved)",
        host,
        reason,
    )
    raise StorageConfigError(
        f"Storage endpoint host {host!r} resolves to a {reason} address, "
        "which the platform will not connect to."
    )


def validate_config_for_save(config: ResolvedStorageConfig) -> None:
    """Address checks at save time, as well as at connect time.

    Identical policy to :func:`assert_endpoint_allowed` — this is the named
    save-time call site the security surface asks for, so that a configuration
    naming a refused address is rejected when it is written rather than only
    when it is first used. Both call sites matter: the save-time one gives the
    administrator an immediate, actionable error, and the connect-time one is
    what survives a DNS change made afterwards.
    """
    assert_endpoint_allowed(config)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning("%s is not an integer (%r) — using %d", name, raw, default)
        return default


def _provider_for_endpoint(endpoint_url: str) -> str:
    """Classify an endpoint into a provider preset.

    Only used by the environment synthesis. From Phase 1 the provider is a
    column and is not guessed.
    """
    if not endpoint_url:
        return PROVIDER_AWS_S3
    host = (urlparse(endpoint_url).hostname or "").lower()
    # Exact host or a subdomain of it. A bare ``endswith`` would also accept
    # ``evilstorage.googleapis.com`` (CodeQL py/incomplete-url-substring-sanitization).
    if host == "storage.googleapis.com" or host.endswith(".storage.googleapis.com"):
        return PROVIDER_GCS
    # The bundled compose service is reachable as the hostname ``minio``.
    if host == "minio" or host.startswith("minio."):
        return PROVIDER_MINIO
    return PROVIDER_S3_COMPATIBLE


def _sse_mode_for_provider(provider: str) -> str:
    """The encryption mode a provider preset wants.

    This is ISC-5. It reproduces the behaviour the old
    ``SSE_ENABLED = not AWS_ENDPOINT_URL`` produced — AWS S3 gets SSE-S3,
    everything else gets none, because MinIO rejects SSE-S3 without a KMS — but
    it now derives that from the *resolved provider* rather than from another
    module constant, so a config that changes its endpoint changes its
    encryption mode with it.

    Phase 2 makes the preset the single table this reads from, so that adding a
    provider cannot leave two places disagreeing about its encryption mode.
    A stored row still overrides it: ``sse_mode`` is a column, and
    :func:`config_from_row` passes the row's value straight through.
    """
    return preset_for(provider).sse_mode


def _legacy_credential(name: str) -> str:
    """One legacy storage credential, resolved file-first.

    The three credential *names* here go through ``services.secrets.get_secret``
    rather than ``os.getenv``; everything else in this function is a setting
    rather than a secret and stays on ``os.getenv``.

    Why it matters: ``get_secret`` understands the ``{NAME}_FILE`` convention,
    so a credential mounted as a file under the secrets overlay is read straight
    off disk. Before this, the only thing that put those two values in the
    process environment was a two-line shim in
    ``scripts/docker/with-file-secrets.sh``, which existed solely because boto3
    reads ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` from the environment
    and nowhere else. The driver now passes credentials to boto3 explicitly, so
    that shim has no remaining consumer and its two lines are gone.

    None of these names is in ``services.secrets.TIER3_NAMES``, and
    ``get_secret`` tests that membership *before* it so much as calls the
    database provider, so the database tier is structurally unreachable for all
    three. The AWS key pair is additionally on the ``NEVER_DB_NAMES`` deny-list.
    The resolution order here is therefore file, then environment — which is
    exactly what the shim produced, in the same order, one layer further out.
    """
    from services.secrets import get_secret

    return get_secret(name) or ""


def resolve_from_env(organization_id: Optional[str] = None) -> ResolvedStorageConfig:
    """Synthesise a configuration from the process environment.

    Note ``AWS_ENDPOINT_URL`` and ``EVIDENCE_PUBLIC_ENDPOINT`` use the
    ``${VAR-default}`` form in compose — *no colon* — so an explicitly empty
    value is honoured rather than replaced by a default. That is the documented
    way to say "use real AWS S3 instead of the bundled MinIO", and reading them
    with ``os.getenv(name, "")`` preserves the distinction.

    Credentials are the exception: they resolve through
    :func:`_legacy_credential`, which is file-aware. No MinIO *root* credential
    is read here or anywhere else on a storage code path — ``MINIO_ROOT_USER``
    and ``MINIO_ROOT_PASSWORD`` are the object store's own root account and stay
    host-only.
    """
    endpoint_url = (os.getenv("AWS_ENDPOINT_URL") or "").strip()
    provider = _provider_for_endpoint(endpoint_url)
    region = (os.getenv("AWS_DEFAULT_REGION") or "").strip() or _DEFAULT_REGION

    return ResolvedStorageConfig(
        config_id=LEGACY_ENV_CONFIG_ID,
        source=SOURCE_LEGACY_ENV,
        provider=provider,
        bucket=(os.getenv("EVIDENCE_BUCKET") or "").strip(),
        region=region,
        endpoint_url=endpoint_url,
        public_endpoint=(os.getenv("EVIDENCE_PUBLIC_ENDPOINT") or "").strip(),
        # Path-style addressing whenever a custom endpoint is in play, which is
        # exactly what the previous _build_client did.
        path_style=bool(endpoint_url),
        sse_mode=_sse_mode_for_provider(provider),
        access_key_id=_legacy_credential("AWS_ACCESS_KEY_ID").strip(),
        secret_access_key=_legacy_credential("AWS_SECRET_ACCESS_KEY"),
        session_token=_legacy_credential("AWS_SESSION_TOKEN"),
        credential_version="",
        url_expiry=_int_env("EVIDENCE_URL_EXPIRY", _DEFAULT_URL_EXPIRY),
        max_file_size=_int_env("EVIDENCE_MAX_FILE_SIZE", _DEFAULT_MAX_FILE_SIZE),
        organization_id=organization_id,
        endpoint_is_operator_supplied=True,
    )


# ---------------------------------------------------------------------------
# Stored rows
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredConfigRow:
    """One ``evidence_storage_configs`` row, with its secret already decrypted.

    Decryption happens at load time, per row, so that one organisation's
    undecryptable credential cannot stop every other organisation resolving.
    A row that failed to decrypt is kept — with :attr:`secret_undecryptable`
    set — rather than dropped, because dropping it would make the organisation
    fall silently through to the platform or legacy store and put its evidence
    somewhere nobody asked for.
    """

    config_id: str
    organization_id: Optional[str]
    provider: str
    bucket: str
    region: str
    endpoint_url: str
    public_endpoint: str
    path_style: bool
    sse_mode: str
    access_key_id: str
    secret_access_key: str = field(default="", repr=False)
    key_version: str = ""
    is_bundled: bool = False
    secret_undecryptable: bool = False


#: ``{organization_id: row}`` for org-scoped rows, plus the platform row.
_Snapshot = Tuple[Dict[str, StoredConfigRow], Optional[StoredConfigRow]]

_SELECT_ACTIVE_CONFIGS = (
    "SELECT id, organization_id, provider, bucket, region, endpoint_url, "
    "public_endpoint, path_style, sse_mode, access_key_id, secret_ciphertext, "
    "key_version, is_bundled "
    "FROM evidence_storage_configs WHERE status = 'active'"
)


def load_active_rows() -> List[StoredConfigRow]:
    """Every active configuration row, as value objects.

    Runs on a short-lived synchronous connection for the same reason
    ``services.integration_secrets._load_all`` does: this is reached from
    synchronous Celery code as well as from the async API.

    Raises on a database failure. The caller decides what an unreadable
    database means — see :meth:`StorageConfigResolver._snapshot`.
    """
    from sqlalchemy import create_engine, text

    import db_url

    engine = create_engine(
        db_url.get_sync_database_url(),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(_SELECT_ACTIVE_CONFIGS)).fetchall()
    finally:
        engine.dispose()

    return [_row_from_record(record) for record in rows]


#: One row by primary key, whatever its status. The resolver's snapshot query
#: filters to ``active``; this deliberately does not, because an evidence file
#: keeps pointing at the configuration its bytes were written under long after
#: that configuration is retired. Reading it by id is the only way that file
#: stays readable.
_SELECT_CONFIG_BY_ID = (
    "SELECT id, organization_id, provider, bucket, region, endpoint_url, "
    "public_endpoint, path_style, sse_mode, access_key_id, secret_ciphertext, "
    "key_version, is_bundled "
    "FROM evidence_storage_configs WHERE id = :config_id"
)


def load_row_by_id(config_id: str) -> Optional[StoredConfigRow]:
    """One configuration row by id, of any status, or ``None``.

    Same short-lived synchronous connection as :func:`load_active_rows`, for
    the same reason: this is reached from Celery as well as from the API.

    Raises on a database failure, like :func:`load_active_rows`. The caller
    decides what an unreadable database means.
    """
    from sqlalchemy import create_engine, text

    import db_url

    engine = create_engine(
        db_url.get_sync_database_url(),
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
    )
    try:
        with engine.connect() as conn:
            record = conn.execute(
                text(_SELECT_CONFIG_BY_ID), {"config_id": str(config_id)}
            ).fetchone()
    finally:
        engine.dispose()

    return _row_from_record(record) if record is not None else None


def _row_from_record(record) -> StoredConfigRow:
    (
        config_id,
        organization_id,
        provider,
        bucket,
        region,
        endpoint_url,
        public_endpoint,
        path_style,
        sse_mode,
        access_key_id,
        secret_ciphertext,
        key_version,
        is_bundled,
    ) = record

    secret = ""
    undecryptable = False
    if secret_ciphertext:
        from services import crypto

        try:
            secret = crypto.decrypt(secret_ciphertext)
        except Exception:  # noqa: BLE001 — DecryptError, SecretKeyMissing, invalid key
            # The ciphertext itself is never logged, and neither is the scope's
            # credential. Only the fact, and which config it belongs to.
            logger.warning(
                "Evidence storage config %s has a secret that cannot be "
                "decrypted with any configured key",
                config_id,
            )
            undecryptable = True

    return StoredConfigRow(
        config_id=str(config_id),
        organization_id=str(organization_id) if organization_id else None,
        provider=(provider or "").strip() or PROVIDER_S3_COMPATIBLE,
        bucket=(bucket or "").strip(),
        region=(region or "").strip() or _DEFAULT_REGION,
        endpoint_url=(endpoint_url or "").strip(),
        public_endpoint=(public_endpoint or "").strip(),
        path_style=bool(path_style),
        sse_mode=_validated_sse_mode(sse_mode, config_id),
        access_key_id=(access_key_id or "").strip(),
        secret_access_key=secret,
        key_version=str(key_version) if key_version is not None else "",
        is_bundled=bool(is_bundled),
        secret_undecryptable=undecryptable,
    )


def stored_row_from_orm(row: Any) -> StoredConfigRow:
    """Build a :class:`StoredConfigRow` from an ORM ``EvidenceStorageConfig``.

    Duck-typed on purpose: this module is a leaf that every storage operation
    imports, and pulling ``models`` in would drag the ORM into every Celery
    task's storage path. The caller hands over anything carrying the column
    attributes, and decryption stays in the one place that does it.

    Used by the connection test, which must be able to probe a ``draft`` row —
    a row the resolver deliberately never loads.
    """
    return _row_from_record(
        (
            row.id,
            row.organization_id,
            row.provider,
            row.bucket,
            row.region,
            row.endpoint_url,
            row.public_endpoint,
            row.path_style,
            row.sse_mode,
            row.access_key_id,
            row.secret_ciphertext,
            row.key_version,
            row.is_bundled,
        )
    )


def config_from_preset(provider: str, **overrides) -> ResolvedStorageConfig:
    """A configuration seeded from a provider preset.

    The preset supplies endpoint, addressing, signature and encryption; the
    caller supplies bucket, region and credentials. Phase 3 creates rows from
    this, Phase 4 seeds the bundled one from it, and the unit tests assert that
    each preset produces the boto3 client configuration its provider needs.

    ``endpoint_is_operator_supplied`` defaults to **False** — the safe end. A
    caller that genuinely is the installer must say so explicitly.
    """
    preset = preset_for(provider)
    base = dict(
        config_id=overrides.pop("config_id", "preset"),
        source=overrides.pop("source", SOURCE_PLATFORM),
        provider=preset.provider,
        endpoint_url=preset.endpoint_url,
        path_style=preset.path_style,
        sse_mode=preset.sse_mode,
        region=preset.default_region,
        endpoint_is_operator_supplied=False,
    )
    base.update(overrides)
    return ResolvedStorageConfig(**base)


def _validated_sse_mode(value: Optional[str], config_id) -> str:
    mode = (value or "").strip() or SSE_NONE
    if mode not in (SSE_NONE, SSE_AES256):
        logger.warning(
            "Evidence storage config %s has an unrecognised sse_mode %r — "
            "treating it as %s",
            config_id,
            mode,
            SSE_NONE,
        )
        return SSE_NONE
    return mode


def config_from_row(row: StoredConfigRow) -> ResolvedStorageConfig:
    """Turn a stored row into the value object every operation carries.

    ``url_expiry`` and ``max_file_size`` are deliberately *not* columns: they
    are presentation limits that belong to the deployment, not to the store,
    and they keep coming from the environment.

    ``public_endpoint`` does **not** fall back to ``EVIDENCE_PUBLIC_ENDPOINT``.
    That variable describes the bundled MinIO, and inheriting it onto an
    organisation's own cloud bucket would sign browser-facing URLs against the
    wrong host. A row that needs a distinct public endpoint carries one.
    """
    if row.secret_undecryptable:
        scope = row.organization_id or "platform"
        raise StorageConfigError(
            f"Evidence storage configuration {row.config_id} ({scope} scope) "
            "has a stored secret that cannot be decrypted with any configured "
            "SCF_SECRET_KEY. Refusing to fall back to a different store."
        )

    return ResolvedStorageConfig(
        config_id=row.config_id,
        source=SOURCE_PLATFORM if row.organization_id is None else SOURCE_ORG,
        provider=row.provider,
        bucket=row.bucket,
        region=row.region,
        endpoint_url=row.endpoint_url,
        public_endpoint=row.public_endpoint,
        path_style=row.path_style,
        sse_mode=row.sse_mode,
        access_key_id=row.access_key_id,
        secret_access_key=row.secret_access_key,
        session_token="",
        credential_version=row.key_version,
        url_expiry=_int_env("EVIDENCE_URL_EXPIRY", _DEFAULT_URL_EXPIRY),
        max_file_size=_int_env("EVIDENCE_MAX_FILE_SIZE", _DEFAULT_MAX_FILE_SIZE),
        organization_id=row.organization_id,
        # Only the installer-provisioned store counts as operator-supplied. A
        # row an administrator typed into the Settings screen is exactly the
        # threat in ISA section 7, so it gets the tenant column of the policy
        # table in `assert_endpoint_allowed`: https only, no internal-only
        # domain, no loopback, private, CGNAT or otherwise non-routable
        # address, and no name that will not resolve. `is_bundled` is therefore
        # a security-relevant column and no tenant-facing path may set it.
        endpoint_is_operator_supplied=row.is_bundled,
        is_bundled=bool(row.is_bundled),
    )


# ---------------------------------------------------------------------------
# Cross-process invalidation (best effort, never fatal)
# ---------------------------------------------------------------------------

_redis_client = None
_redis_unavailable = False


def _get_redis():
    """A lazily built sync Redis client, or None when Redis is unusable."""
    global _redis_client, _redis_unavailable
    if _redis_unavailable:
        return None
    if _redis_client is not None:
        return _redis_client
    try:
        import redis  # imported lazily so tests need no broker

        _redis_client = redis.Redis.from_url(
            os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            socket_connect_timeout=REDIS_SOCKET_TIMEOUT,
        )
    except Exception:  # pragma: no cover - import/URL problems are not fatal
        _redis_unavailable = True
        return None
    return _redis_client


def _read_version() -> Optional[str]:
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(REDIS_VERSION_KEY)
    except Exception:
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _incr_version() -> None:
    client = _get_redis()
    if client is None:
        return
    try:
        client.incr(REDIS_VERSION_KEY)
    except Exception:
        logger.debug("Could not bump the storage version key", exc_info=True)


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


class StorageConfigResolver:
    """Resolves a configuration for an organisation, with a versioned cache.

    A class rather than module state so that a test can hold two of them and
    watch one observe what the other wrote — which is the only honest way to
    demonstrate that a Celery worker converges on a change made by the API.
    """

    def __init__(self, loader: Optional[Callable[[], List[StoredConfigRow]]] = None):
        self._loader = loader or load_active_rows
        self._by_id_loader: Callable[[str], Optional[StoredConfigRow]] = load_row_by_id
        self._orgs: Dict[str, StoredConfigRow] = {}
        self._platform: Optional[StoredConfigRow] = None
        #: Rows fetched by id, including retired ones. Same lifetime as the
        #: snapshot: `invalidate()` clears both, so a rotation reaches a file's
        #: own configuration as fast as it reaches the organisation's.
        self._by_id: Dict[str, Optional[StoredConfigRow]] = {}
        self._loaded = False
        self._loaded_at = 0.0
        self._version: Optional[str] = None
        self._version_checked_at = 0.0
        self._load_failure_warned = False

    # -- cache -----------------------------------------------------------

    def invalidate(self) -> None:
        """Forget this process's snapshot. The next resolve re-reads."""
        self._loaded = False
        self._loaded_at = 0.0
        self._version_checked_at = 0.0
        self._by_id = {}

    def bump_version(self) -> None:
        """Announce a change to every process, then drop the local snapshot."""
        _incr_version()
        self.invalidate()

    def _snapshot(self) -> _Snapshot:
        now = time.monotonic()
        stale = not self._loaded or (now - self._loaded_at) >= CACHE_TTL_SECONDS

        if not stale and (now - self._version_checked_at) >= VERSION_CHECK_INTERVAL_SECONDS:
            self._version_checked_at = now
            version = _read_version()
            if version is not None and version != self._version:
                self._version = version
                stale = True

        if not stale:
            return self._orgs, self._platform

        try:
            rows = self._loader() or []
        except Exception as exc:  # noqa: BLE001 — degrade to env, never raise
            # A table that does not exist yet on a pre-migration database, a
            # database that is still starting, an unreachable host. Keep the
            # previous snapshot if there is one; otherwise resolve from the
            # environment, which is what every installation does today.
            if not self._load_failure_warned:
                self._load_failure_warned = True
                # Type only: no DSN, no credential, ever reaches this line.
                logger.warning(
                    "Evidence storage configuration could not be read from the "
                    "database (%s); resolving from the environment instead",
                    type(exc).__name__,
                )
            # Marked loaded so the failure is cached for the TTL. Without this
            # every single storage operation would open a fresh connection to a
            # database that is not answering, which turns one outage into a
            # connection storm.
            self._loaded = True
            self._loaded_at = now
            self._version_checked_at = now
            return self._orgs, self._platform

        orgs: Dict[str, StoredConfigRow] = {}
        platform: Optional[StoredConfigRow] = None
        for row in rows:
            if row.organization_id is None:
                platform = row
            else:
                orgs[row.organization_id] = row

        self._orgs = orgs
        self._platform = platform
        self._loaded = True
        self._loaded_at = now
        self._version_checked_at = now
        self._version = _read_version()
        self._load_failure_warned = False
        return orgs, platform

    # -- resolution ------------------------------------------------------

    def resolve(self, organization_id: Optional[str] = None) -> ResolvedStorageConfig:
        """The configuration an operation for ``organization_id`` should use.

        Order is the organisation's own active row, then the platform row, then
        the environment synthesis. An organisation that has never configured a
        store therefore keeps using whatever the platform uses, which is what
        makes this phase invisible to an existing installation.
        """
        orgs, platform = self._snapshot()

        if organization_id is not None:
            row = orgs.get(str(organization_id))
            if row is not None:
                return config_from_row(row)

        if platform is not None:
            config = config_from_row(platform)
            if organization_id is None:
                return config
            # A platform row serving an organisation keeps the organisation on
            # the value object: it is the metadata tag and the key prefix, and
            # losing it here would mis-tag every object written for that org.
            return replace(config, organization_id=str(organization_id))

        return resolve_from_env(organization_id)

    def resolve_platform(self) -> ResolvedStorageConfig:
        """The configuration for platform-scope artefacts.

        The catalogue workbook, catalogue-upgrade diffs and reconciliation
        detail blobs belong to the platform, not to a tenant, and must keep
        resolving without an organisation in scope.
        """
        _, platform = self._snapshot()
        if platform is not None:
            return config_from_row(platform)
        return resolve_from_env(None)

    def resolve_for_file(
        self,
        organization_id: Optional[str],
        storage_config_id: Optional[str],
    ) -> ResolvedStorageConfig:
        """Where one stored object's bytes actually are.

        ``storage_config_id`` is authoritative when it is set, **whatever the
        row's status**, and it beats resolution by organisation. That is the
        whole point of the column: during a copy, and after one, a file must
        keep reading from the store it was written to rather than from
        whichever store the organisation happens to be using now.

        ``None`` means the file predates per-file storage, so it resolves the
        way everything resolved before — :meth:`resolve`.

        A ``storage_config_id`` naming a row that no longer exists falls back
        to :meth:`resolve` with a warning rather than raising: the foreign key
        is ``ON DELETE RESTRICT``, so this should be unreachable, and refusing
        the read would be a worse answer than trying the organisation's store.
        An undecryptable secret still raises, exactly as it does everywhere
        else — falling back there would write or read somewhere nobody asked
        for.
        """
        if storage_config_id is None:
            return self.resolve(organization_id)

        key = str(storage_config_id)
        # Runs the version check and TTL expiry, so a bumped version drops the
        # by-id cache too. The snapshot itself is unused here.
        self._snapshot()

        if key in self._by_id:
            row = self._by_id[key]
        else:
            try:
                row = self._by_id_loader(key)
            except Exception as exc:  # noqa: BLE001 — degrade, never raise
                logger.warning(
                    "Evidence storage configuration %s could not be read from "
                    "the database (%s); resolving by organisation instead",
                    key,
                    type(exc).__name__,
                )
                return self.resolve(organization_id)
            self._by_id[key] = row

        if row is None:
            logger.warning(
                "Evidence file names storage configuration %s, which no longer "
                "exists; resolving by organisation instead",
                key,
            )
            return self.resolve(organization_id)

        config = config_from_row(row)
        if row.organization_id is None and organization_id is not None:
            # A platform row serving an organisation keeps the organisation on
            # the value object, for the same reason `resolve` does: it is the
            # metadata tag and the key prefix.
            return replace(config, organization_id=str(organization_id))
        return config


#: The resolver every caller shares. Tests that need two processes' worth of
#: state build their own instances instead of reaching in here.
_default_resolver = StorageConfigResolver()


def resolve(organization_id: Optional[str] = None) -> ResolvedStorageConfig:
    """The configuration an operation for ``organization_id`` should use."""
    return _default_resolver.resolve(organization_id)


def resolve_platform() -> ResolvedStorageConfig:
    """The configuration for platform-scope artefacts."""
    return _default_resolver.resolve_platform()


def resolve_for_file(
    organization_id: Optional[str],
    storage_config_id: Optional[str],
) -> ResolvedStorageConfig:
    """Where one stored object's bytes actually are. See
    :meth:`StorageConfigResolver.resolve_for_file`."""
    return _default_resolver.resolve_for_file(organization_id, storage_config_id)


def invalidate() -> None:
    """Drop this process's cached configuration snapshot."""
    _default_resolver.invalidate()


def bump_version() -> None:
    """Announce a configuration change to every process.

    Called by whoever writes a configuration row. The INCR is what lets a
    Celery worker notice inside a couple of seconds instead of a minute.
    """
    _default_resolver.bump_version()


def use_loader(loader: Optional[Callable[[], List[StoredConfigRow]]]) -> None:
    """Replace where the shared resolver reads rows from. ``None`` restores the
    database.

    This exists for tests: the unit suite has no database, and a resolver that
    opened a connection on every call would either be slow or would silently
    reach whichever Postgres happened to be listening on the developer's
    machine. Tests that need two processes' worth of state build their own
    :class:`StorageConfigResolver` instead.
    """
    _default_resolver._loader = loader or load_active_rows
    _default_resolver.invalidate()


def use_row_by_id_loader(
    loader: Optional[Callable[[str], Optional[StoredConfigRow]]],
) -> None:
    """Replace where the shared resolver reads a row by id. ``None`` restores
    the database. Sibling of :func:`use_loader`, and there for the same reason.
    """
    _default_resolver._by_id_loader = loader or load_row_by_id
    _default_resolver.invalidate()


def reset_caches() -> None:
    """Test hook: forget every cached value, including the Redis handle."""
    global _redis_client, _redis_unavailable
    _redis_client = None
    _redis_unavailable = False
    _default_resolver._version = None
    _default_resolver._orgs = {}
    _default_resolver._platform = None
    _default_resolver._by_id = {}
    _default_resolver._load_failure_warned = False
    _default_resolver.invalidate()
