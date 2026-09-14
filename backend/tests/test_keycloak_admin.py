"""Unit tests for the bundled-Keycloak admin client (#984).

No Keycloak and no network: every call goes through an `httpx.MockTransport`
handler that records the request and answers it, so the assertions are about the
exact wire shape — which realm the token comes from, that the lookup is
`exact=true`, that the password reset carries `temporary: true`.

`respx` is not a dependency here, hence the hand-rolled transport. The one
credential in this file is an obviously fake literal, and the disabled-path
tests assert that it never reaches the log.
"""
import logging

import httpx
import pytest

from services import keycloak_admin
from services.keycloak_admin import KeycloakAdminClient, KeycloakAdminError

# Obviously fake. Asserted absent from log output below.
FAKE_ADMIN_USER = "unit-test-admin"
FAKE_ADMIN_PASSWORD = "unit-test-not-a-real-password"
FAKE_ACCESS_TOKEN = "unit-test-not-a-real-token"

DISCOVERY_URL = "http://keycloak:8080/realms/scf"
ISSUER_URL = "https://sso.example.invalid/realms/customer"

CONFIG_NAMES = (
    "KC_ADMIN_USER",
    "KC_ADMIN_PASSWORD",
    "KC_ADMIN_PASSWORD_FILE",
    "OIDC_DISCOVERY_URL",
    "OIDC_ISSUER",
)


@pytest.fixture(autouse=True)
def _clean_keycloak_config(monkeypatch):
    """No inherited configuration, and no bearer token carried between tests.

    `KC_ADMIN_PASSWORD_FILE` is cleared as well as the variable: `get_secret`
    reads the file tier first, so a developer with a secrets file on disk would
    otherwise see the "missing" tests fail for a reason unrelated to the code.
    """
    for name in CONFIG_NAMES:
        monkeypatch.delenv(name, raising=False)
    keycloak_admin.reset_caches()
    yield
    keycloak_admin.reset_caches()


@pytest.fixture
def configured(monkeypatch):
    """All three inputs present, pointing at the compose-style internal URL."""
    monkeypatch.setenv("KC_ADMIN_USER", FAKE_ADMIN_USER)
    monkeypatch.setenv("KC_ADMIN_PASSWORD", FAKE_ADMIN_PASSWORD)
    monkeypatch.setenv("OIDC_DISCOVERY_URL", DISCOVERY_URL)


class Recorder:
    """A MockTransport handler: canned responses by (method, path), plus a log.

    Every request is appended to `requests` before it is answered, so a test can
    assert on ordering and on calls it did not stub.
    """

    def __init__(self, routes):
        self.routes = routes
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        handler = self.routes.get(key)
        if handler is None:  # pragma: no cover - a stub gap is a test bug
            raise AssertionError(f"unstubbed request: {request.method} {request.url}")
        if callable(handler):
            return handler(request)
        return handler

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def by_path(self, method, path):
        return [
            request
            for request in self.requests
            if request.method == method and request.url.path == path
        ]


def token_response() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": FAKE_ACCESS_TOKEN, "expires_in": 60}
    )


TOKEN_PATH = "/realms/master/protocol/openid-connect/token"
USERS_PATH = "/admin/realms/scf/users"


def make_client(recorder: Recorder) -> KeycloakAdminClient:
    """A client wired to the recorder, with credentials passed explicitly."""
    return KeycloakAdminClient(
        base_url="http://keycloak:8080",
        realm="scf",
        username=FAKE_ADMIN_USER,
        password=FAKE_ADMIN_PASSWORD,
        transport=recorder.transport,
    )


# ---------------------------------------------------------------------------
# Configuration gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "drop, expected_name",
    [
        ("KC_ADMIN_USER", "KC_ADMIN_USER"),
        ("KC_ADMIN_PASSWORD", "KC_ADMIN_PASSWORD"),
        ("OIDC_DISCOVERY_URL", "OIDC_DISCOVERY_URL"),
    ],
)
def test_disabled_when_any_input_is_missing(
    monkeypatch, caplog, configured, drop, expected_name
):
    """Each of the three inputs alone is enough to keep provisioning off.

    Dropping the discovery URL drops the issuer too, since either satisfies it.
    """
    monkeypatch.delenv(drop, raising=False)
    if drop == "OIDC_DISCOVERY_URL":
        monkeypatch.delenv("OIDC_ISSUER", raising=False)

    caplog.set_level(logging.INFO)
    assert keycloak_admin.is_enabled() is False

    reported = keycloak_admin.missing_configuration()
    assert any(expected_name in name for name in reported), reported
    assert expected_name in caplog.text
    # The gate names variables. It must never render their values.
    assert FAKE_ADMIN_PASSWORD not in caplog.text


def test_disabled_logs_every_missing_name_at_once(caplog):
    """Nothing configured: the operator is told all three, not just the first."""
    caplog.set_level(logging.INFO)
    assert keycloak_admin.is_enabled() is False
    assert "KC_ADMIN_USER" in caplog.text
    assert "KC_ADMIN_PASSWORD" in caplog.text
    assert "OIDC_DISCOVERY_URL" in caplog.text


def test_enabled_when_all_three_resolve(configured):
    assert keycloak_admin.missing_configuration() == []
    assert keycloak_admin.is_enabled() is True


def test_password_resolves_through_the_file_tier(monkeypatch, tmp_path):
    """`KC_ADMIN_PASSWORD_FILE` counts as configured — the compose secrets path."""
    secret_file = tmp_path / "kc_admin_password"
    secret_file.write_text(FAKE_ADMIN_PASSWORD)
    monkeypatch.setenv("KC_ADMIN_USER", FAKE_ADMIN_USER)
    monkeypatch.setenv("KC_ADMIN_PASSWORD_FILE", str(secret_file))
    monkeypatch.setenv("OIDC_DISCOVERY_URL", DISCOVERY_URL)

    assert keycloak_admin.missing_configuration() == []


def test_configuration_is_read_at_call_time(monkeypatch, configured):
    """Import froze nothing: changing the environment changes the answer."""
    assert keycloak_admin.is_enabled() is True
    monkeypatch.delenv("KC_ADMIN_PASSWORD", raising=False)
    assert keycloak_admin.is_enabled() is False


# ---------------------------------------------------------------------------
# URL and realm derivation
# ---------------------------------------------------------------------------

def test_base_url_and_realm_from_discovery_url(configured):
    assert keycloak_admin.admin_base_url() == "http://keycloak:8080"
    assert keycloak_admin.realm_name() == "scf"


def test_base_url_and_realm_from_issuer_when_discovery_unset(monkeypatch):
    monkeypatch.setenv("KC_ADMIN_USER", FAKE_ADMIN_USER)
    monkeypatch.setenv("KC_ADMIN_PASSWORD", FAKE_ADMIN_PASSWORD)
    monkeypatch.setenv("OIDC_ISSUER", ISSUER_URL)

    assert keycloak_admin.admin_base_url() == "https://sso.example.invalid"
    assert keycloak_admin.realm_name() == "customer"


def test_discovery_url_wins_over_issuer(monkeypatch):
    """The admin API is a server-side call, so the internal URL is the one used."""
    monkeypatch.setenv("KC_ADMIN_USER", FAKE_ADMIN_USER)
    monkeypatch.setenv("KC_ADMIN_PASSWORD", FAKE_ADMIN_PASSWORD)
    monkeypatch.setenv("OIDC_ISSUER", "http://localhost:8081/realms/scf")
    monkeypatch.setenv("OIDC_DISCOVERY_URL", DISCOVERY_URL)

    assert keycloak_admin.admin_base_url() == "http://keycloak:8080"


def test_trailing_slash_and_well_known_tail_are_tolerated(monkeypatch):
    monkeypatch.setenv("OIDC_DISCOVERY_URL", DISCOVERY_URL + "/")
    assert keycloak_admin.realm_name() == "scf"

    monkeypatch.setenv(
        "OIDC_DISCOVERY_URL", DISCOVERY_URL + "/.well-known/openid-configuration"
    )
    assert keycloak_admin.realm_name() == "scf"
    assert keycloak_admin.admin_base_url() == "http://keycloak:8080"


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------

# CI runs `pytest` from the repo root, where backend/pytest.ini's
# asyncio_mode=auto is not picked up; mark each async test explicitly.
@pytest.mark.asyncio
async def test_token_request_targets_master_realm_with_admin_cli():
    recorder = Recorder({("POST", TOKEN_PATH): token_response()})

    token = await make_client(recorder).get_token()

    assert token == FAKE_ACCESS_TOKEN
    request = recorder.by_path("POST", TOKEN_PATH)[0]
    body = request.content.decode()
    assert "client_id=admin-cli" in body
    assert "grant_type=password" in body


@pytest.mark.asyncio
async def test_token_is_cached_across_calls():
    """One token request, not one per admin call."""
    recorder = Recorder({("POST", TOKEN_PATH): token_response()})
    client = make_client(recorder)

    await client.get_token()
    await client.get_token()

    assert len(recorder.by_path("POST", TOKEN_PATH)) == 1


@pytest.mark.asyncio
async def test_rejected_token_raises_with_step_token():
    recorder = Recorder({("POST", TOKEN_PATH): httpx.Response(401, json={})})

    with pytest.raises(KeycloakAdminError) as excinfo:
        await make_client(recorder).get_token()

    assert excinfo.value.step == "token"
    assert excinfo.value.status_code == 401
    assert FAKE_ADMIN_PASSWORD not in str(excinfo.value)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_uses_the_exact_email_filter():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(
                200, json=[{"id": "kc-1", "email": "sam@example.invalid"}]
            ),
        }
    )

    found = await make_client(recorder).find_user_by_email("sam@example.invalid")

    assert found is not None and found["id"] == "kc-1"
    request = recorder.by_path("GET", USERS_PATH)[0]
    assert request.url.params["email"] == "sam@example.invalid"
    assert request.url.params["exact"] == "true"
    assert request.headers["authorization"] == f"Bearer {FAKE_ACCESS_TOKEN}"


@pytest.mark.asyncio
async def test_lookup_ignores_a_row_whose_email_differs():
    """Belt and braces against a server that substring-matches anyway."""
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(
                200, json=[{"id": "kc-9", "email": "sam@example.invalid.au"}]
            ),
        }
    )

    assert await make_client(recorder).find_user_by_email("sam@example.invalid") is None


@pytest.mark.asyncio
async def test_lookup_matches_case_insensitively():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(
                200, json=[{"id": "kc-2", "email": "Sam@Example.Invalid"}]
            ),
        }
    )

    found = await make_client(recorder).find_user_by_email("sam@example.invalid")
    assert found is not None and found["id"] == "kc-2"


# ---------------------------------------------------------------------------
# ensure_user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_user_creates_and_returns_the_location_id():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(200, json=[]),
            ("POST", USERS_PATH): httpx.Response(
                201,
                headers={
                    "Location": "http://keycloak:8080/admin/realms/scf/users/kc-new"
                },
            ),
        }
    )

    user_id, created = await make_client(recorder).ensure_user("new@example.invalid")

    assert (user_id, created) == ("kc-new", True)
    payload = recorder.by_path("POST", USERS_PATH)[0].content.decode()
    assert '"username":"new@example.invalid"' in payload.replace(" ", "")
    assert '"enabled":true' in payload.replace(" ", "")
    assert '"emailVerified":true' in payload.replace(" ", "")
    assert "UPDATE_PASSWORD" in payload


@pytest.mark.asyncio
async def test_ensure_user_returns_existing_without_creating():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(
                200, json=[{"id": "kc-old", "email": "old@example.invalid"}]
            ),
        }
    )

    user_id, created = await make_client(recorder).ensure_user("old@example.invalid")

    assert (user_id, created) == ("kc-old", False)
    # No POST to users at all: an account someone may already be using.
    assert recorder.by_path("POST", USERS_PATH) == []


@pytest.mark.asyncio
async def test_ensure_user_resolves_a_409_race_by_looking_up_again():
    """Two invites at once: the loser must not fail, and must not own the user."""
    lookups = {"count": 0}

    def get_users(request):
        lookups["count"] += 1
        if lookups["count"] == 1:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200, json=[{"id": "kc-raced", "email": "race@example.invalid"}]
        )

    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): get_users,
            ("POST", USERS_PATH): httpx.Response(409, json={}),
        }
    )

    user_id, created = await make_client(recorder).ensure_user("race@example.invalid")

    assert (user_id, created) == ("kc-raced", False)
    assert lookups["count"] == 2


@pytest.mark.asyncio
async def test_create_failure_raises_with_step_create():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(200, json=[]),
            ("POST", USERS_PATH): httpx.Response(500, json={}),
        }
    )

    with pytest.raises(KeycloakAdminError) as excinfo:
        await make_client(recorder).ensure_user("boom@example.invalid")

    assert excinfo.value.step == "create"
    assert excinfo.value.status_code == 500
    assert "create" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Password and delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_set_temporary_password_sends_temporary_true():
    path = f"{USERS_PATH}/kc-new/reset-password"
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("PUT", path): httpx.Response(204),
        }
    )

    await make_client(recorder).set_temporary_password("kc-new", "temp-not-a-secret")

    body = recorder.by_path("PUT", path)[0].content.decode().replace(" ", "")
    assert '"type":"password"' in body
    assert '"temporary":true' in body
    assert '"value":"temp-not-a-secret"' in body


@pytest.mark.asyncio
async def test_set_password_failure_raises_with_step_set_password():
    path = f"{USERS_PATH}/kc-new/reset-password"
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("PUT", path): httpx.Response(400, json={}),
        }
    )

    with pytest.raises(KeycloakAdminError) as excinfo:
        await make_client(recorder).set_temporary_password("kc-new", "temp-not-a-secret")

    assert excinfo.value.step == "set_password"


@pytest.mark.asyncio
async def test_delete_treats_404_as_success():
    path = f"{USERS_PATH}/kc-gone"
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("DELETE", path): httpx.Response(404, json={}),
        }
    )

    await make_client(recorder).delete_user("kc-gone")

    assert len(recorder.by_path("DELETE", path)) == 1


@pytest.mark.asyncio
async def test_delete_failure_raises_with_step_delete():
    path = f"{USERS_PATH}/kc-1"
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("DELETE", path): httpx.Response(500, json={}),
        }
    )

    with pytest.raises(KeycloakAdminError) as excinfo:
        await make_client(recorder).delete_user("kc-1")

    assert excinfo.value.step == "delete"
    assert excinfo.value.status_code == 500


# ---------------------------------------------------------------------------
# Module-level conveniences
# ---------------------------------------------------------------------------

def test_generated_password_is_random_and_long_enough():
    first = KeycloakAdminClient.generate_temporary_password()
    second = KeycloakAdminClient.generate_temporary_password()
    assert first != second
    assert len(first) >= 16


@pytest.mark.asyncio
async def test_provision_user_sets_a_password_only_for_a_new_account():
    reset_path = f"{USERS_PATH}/kc-new/reset-password"
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(200, json=[]),
            ("POST", USERS_PATH): httpx.Response(
                201,
                headers={
                    "Location": "http://keycloak:8080/admin/realms/scf/users/kc-new"
                },
            ),
            ("PUT", reset_path): httpx.Response(204),
        }
    )

    result = await keycloak_admin.provision_user(
        "new@example.invalid", client=make_client(recorder)
    )

    assert result.user_id == "kc-new"
    assert result.created is True
    assert result.temporary_password
    assert result.temporary_password in recorder.by_path("PUT", reset_path)[
        0
    ].content.decode()


@pytest.mark.asyncio
async def test_provision_user_leaves_an_existing_account_alone():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(
                200, json=[{"id": "kc-old", "email": "old@example.invalid"}]
            ),
        }
    )

    result = await keycloak_admin.provision_user(
        "old@example.invalid", client=make_client(recorder)
    )

    assert result == keycloak_admin.ProvisionResult(
        user_id="kc-old", created=False, temporary_password=None
    )


@pytest.mark.asyncio
async def test_find_user_id_returns_none_when_absent():
    recorder = Recorder(
        {
            ("POST", TOKEN_PATH): token_response(),
            ("GET", USERS_PATH): httpx.Response(200, json=[]),
        }
    )

    found = await keycloak_admin.find_user_id(
        "nobody@example.invalid", client=make_client(recorder)
    )
    assert found is None
