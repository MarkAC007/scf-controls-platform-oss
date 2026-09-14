"""The backend's Keycloak admin credentials must actually reach the containers (#984).

`docker-compose.yml` has no `env_file:` for the Python services — every variable
is named explicitly in an allow-list. A credential the code reads through
`services.secrets.get_secret` therefore resolves to nothing unless it also
appears in that list, and the failure is silent: invite-time provisioning just
stays switched off while the operator sees a variable they set in `.env`.

That is the same class of defect as #782 (an override wired to nothing), so it
gets the same treatment: assert the wiring, not the intent.

Two files, because there are two supported ways to supply the password.

  * `docker-compose.yml`   — `${KC_ADMIN_PASSWORD:-}` interpolated from `.env`.
  * `docker-compose.secrets.yml` — the file-secrets overlay, where the value is
    mounted at `/run/secrets/KC_ADMIN_PASSWORD` and the environment carries only
    the *path* in `KC_ADMIN_PASSWORD_FILE`. `get_secret` prefers `{NAME}_FILE`,
    so the plaintext variable is blanked rather than interpolated — otherwise
    compose would resolve it on the host and `docker inspect` would show it.

Three services, not one: `backend` serves the invite endpoint, and the celery
services share the backend's env block and run the tasks that touch the same
code paths. Wiring only the API would work until the first time provisioning
moved to a task.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE = REPO_ROOT / "docker-compose.yml"
COMPOSE_SECRETS = REPO_ROOT / "docker-compose.secrets.yml"

# The services that share the backend's environment block and so must all be
# able to resolve the admin credentials.
SERVICES = ("backend", "celery-worker", "celery-beat")

SECRET_PATH = "/run/secrets/KC_ADMIN_PASSWORD"


def _services(path: Path) -> dict:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict), f"{path.name} did not parse into a mapping"
    return data["services"]


@pytest.fixture(scope="module")
def base_services() -> dict:
    return _services(COMPOSE)


@pytest.fixture(scope="module")
def overlay_services() -> dict:
    return _services(COMPOSE_SECRETS)


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize(
    "var",
    ["KC_ADMIN_USER", "KC_ADMIN_PASSWORD"],
)
def test_base_compose_forwards_kc_admin_credentials(base_services, service, var):
    """Both names appear in the allow-list, in the same form idp-init uses."""
    environment = base_services[service]["environment"]
    assert isinstance(environment, dict), (
        f"{service}.environment is a list, not a mapping; this test and the "
        "surrounding block both assume the mapping form"
    )
    assert var in environment, (
        f"{var} is missing from the {service} service's environment in "
        f"{COMPOSE.name}. docker-compose.yml uses an explicit allow-list with no "
        "env_file, so setting it in .env would never reach the container and "
        "invite-time provisioning would stay silently disabled."
    )
    assert environment[var] == "${" + var + ":-}", (
        f"{service}.environment.{var} is {environment[var]!r}; it must be "
        f"'${{{var}:-}}' — an empty default, so the no-profile stack is "
        "unchanged and an unset variable means 'provisioning off' rather than "
        "a parse error."
    )


@pytest.mark.parametrize("service", SERVICES)
def test_secrets_overlay_mounts_the_admin_password(overlay_services, service):
    """The overlay grants the secret to each service that reads it."""
    secrets = overlay_services[service].get("secrets", [])
    assert "KC_ADMIN_PASSWORD" in secrets, (
        f"the {service} service does not list KC_ADMIN_PASSWORD in its secrets "
        f"in {COMPOSE_SECRETS.name}, so {SECRET_PATH} would not exist inside the "
        "container and KC_ADMIN_PASSWORD_FILE would point at nothing."
    )


@pytest.mark.parametrize("service", SERVICES)
def test_secrets_overlay_points_the_backend_at_the_file(overlay_services, service):
    """`{NAME}_FILE` carries the path, and the plaintext variable is blanked.

    Blanking matters as much as setting the path. Compose interpolates
    `${KC_ADMIN_PASSWORD:-}` on the HOST, so leaving the base value in place
    under this overlay would put the password into `docker inspect` output no
    matter what the file tier says — exactly the trap documented on
    DATABASE_URL in the same file.
    """
    environment = overlay_services[service]["environment"]
    assert environment.get("KC_ADMIN_PASSWORD_FILE") == SECRET_PATH, (
        f"{service}.environment.KC_ADMIN_PASSWORD_FILE is "
        f"{environment.get('KC_ADMIN_PASSWORD_FILE')!r}, expected {SECRET_PATH!r}"
    )
    assert environment.get("KC_ADMIN_PASSWORD") == "", (
        f"{service}.environment.KC_ADMIN_PASSWORD is "
        f"{environment.get('KC_ADMIN_PASSWORD')!r} under the secrets overlay; it "
        "must be blanked so the host-interpolated value cannot reach "
        "`docker inspect`."
    )


def test_admin_password_is_closed_to_the_database_tier():
    """The invariant the whole design rests on, pinned where it can be seen.

    `get_secret` checks membership of NEVER_DB_NAMES structurally, so a row
    planted in `integration_secrets` naming KC_ADMIN_PASSWORD cannot become the
    credential that controls the realm.
    """
    from services.secrets import NEVER_DB_NAMES, TIER3_NAMES

    assert "KC_ADMIN_PASSWORD" in NEVER_DB_NAMES
    assert "KC_ADMIN_PASSWORD" not in TIER3_NAMES
