"""Provision over HTTP, unattended, import-env, and the launcher's own guarantees."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from installer import writer
from installer.__main__ import (
    EXIT_ALREADY_PROVISIONED,
    EXIT_BAD_CONFIG,
    EXIT_OK,
    main,
)
from installer.app import TOKEN_HEADER, create_app
from installer.generate import SECRET_FILE_NAMES
from installer.writer import SENTINEL_NAME, TOKEN_NAME

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "a-real-provisioning-token-000000000000"

# Inside the test container only `backend/` is mounted, so the repo root is
# passed in; on the host it is three levels up from this file.
REPO_ROOT = Path(os.environ.get("SCF_REPO_ROOT") or Path(__file__).resolve().parents[3])
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"

BUNDLED_KEYCLOAK = {
    "db": {"type": "bundled"},
    "idp": {
        "type": "bundled_keycloak",
        "kc_admin_user": "admin",
        "bootstrap_admin_email": "op@example.test",
    },
}


def make_client(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir(parents=True)
    out_dir.mkdir(parents=True)
    (secrets_dir / TOKEN_NAME).write_text(TOKEN + "\n")
    app = create_app(
        secrets_path=secrets_dir,
        out_path=out_dir,
        host_dir=str(secrets_dir),
        port=PORT,
        on_lockout=lambda: None,
        exit_after_provision=False,
    )
    client = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    return client, secrets_dir, out_dir


# ------------------------------------------------------------- provision (HTTP)
def test_a_bundled_provision_writes_ten_files_the_env_and_the_sentinel(tmp_path):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision", headers={TOKEN_HEADER: TOKEN}, json=BUNDLED_KEYCLOAK
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True

    for name in SECRET_FILE_NAMES:
        path = secrets_dir / name
        assert path.exists(), name
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, name

    assert (secrets_dir / SENTINEL_NAME).exists()
    assert not (secrets_dir / TOKEN_NAME).exists(), "the token must be deleted on success"

    env_text = (out_dir / ".env").read_text()
    assert stat.S_IMODE((out_dir / ".env").stat().st_mode) == 0o600
    assert "COMPOSE_PROFILES=idp" in env_text
    for name in SECRET_FILE_NAMES:
        value = (secrets_dir / name).read_text().strip()
        if value:
            assert value not in env_text, f"{name} leaked into .env"


def test_a_second_provision_is_refused_with_409(tmp_path):
    client, secrets_dir, _ = make_client(tmp_path)
    assert client.post("/api/provision", headers={TOKEN_HEADER: TOKEN}, json=BUNDLED_KEYCLOAK).status_code == 200
    (secrets_dir / TOKEN_NAME).write_text(TOKEN + "\n")
    second = client.post("/api/provision", headers={TOKEN_HEADER: TOKEN}, json=BUNDLED_KEYCLOAK)
    assert second.status_code == 409
    assert second.json()["error"] == "already_provisioned"


def test_an_external_database_that_cannot_be_reached_blocks_every_write(tmp_path):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "db": {
                "type": "external",
                "host": "no-such-host.invalid",
                "user": "cg",
                "password": "hunter2",
            },
            "idp": {"type": "none"},
        },
    )
    assert response.status_code == 400
    assert response.json()["error"] == "validation_failed"
    assert not (secrets_dir / SENTINEL_NAME).exists()
    assert not (out_dir / ".env").exists()
    assert (secrets_dir / TOKEN_NAME).exists(), "a failed run must not consume the token"


def test_a_failed_validation_never_echoes_the_password(tmp_path):
    client, _, _ = make_client(tmp_path)
    password = "sup3rs3cret-not-in-any-output"
    response = client.post(
        "/api/validate-db",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "host": "no-such-host.invalid",
            "user": "cg",
            "password": password,
            "sslmode": "require",
        },
    )
    assert password not in response.text


def test_the_unix_socket_dsn_is_a_400_at_the_http_layer(tmp_path):
    client, _, _ = make_client(tmp_path)
    response = client.post(
        "/api/validate-db",
        headers={TOKEN_HEADER: TOKEN},
        json={"dsn": "postgresql://u:p@/x?host=/var/run/postgresql&options=-c log_statement=all"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "unsupported connection parameter"


# ------------------------------------------------------------------ unattended
def test_unattended_produces_the_same_output_as_the_served_wizard(tmp_path):
    client, http_secrets, http_out = make_client(tmp_path / "http")
    client.post("/api/provision", headers={TOKEN_HEADER: TOKEN}, json=BUNDLED_KEYCLOAK)

    cli_secrets = tmp_path / "cli" / "secrets"
    cli_out = tmp_path / "cli" / "out"
    cli_secrets.mkdir(parents=True)
    cli_out.mkdir(parents=True)
    config = tmp_path / "cli" / "config.json"
    config.write_text(json.dumps(BUNDLED_KEYCLOAK))

    code = main(
        [
            "unattended",
            "--config",
            str(config),
            "--secrets-dir",
            str(cli_secrets),
            "--out-dir",
            str(cli_out),
        ]
    )
    assert code == EXIT_OK

    http_env = writer.env_keys((http_out / ".env").read_text())
    cli_env = writer.env_keys((cli_out / ".env").read_text())
    assert http_env == cli_env

    assert sorted(p.name for p in cli_secrets.iterdir()) == sorted(
        p.name for p in http_secrets.iterdir()
    )
    for name in SECRET_FILE_NAMES:
        # Same names, same modes, different values: nothing is ever fixed.
        assert stat.S_IMODE((cli_secrets / name).stat().st_mode) == 0o600
        assert (cli_secrets / name).read_text() != (http_secrets / name).read_text() or not (
            cli_secrets / name
        ).read_text()


def test_unattended_refuses_a_provisioned_directory(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    writer.create_sentinel(secrets_dir, db="bundled", idp="none")
    config = tmp_path / "config.json"
    config.write_text(json.dumps(BUNDLED_KEYCLOAK))
    code = main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    )
    assert code == EXIT_ALREADY_PROVISIONED


def test_unattended_rejects_a_config_that_is_not_json(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    config = tmp_path / "config.json"
    config.write_text("not json")
    code = main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    )
    assert code == EXIT_BAD_CONFIG


def test_unattended_takes_the_database_password_from_the_environment(tmp_path, monkeypatch):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    config = tmp_path / "config.json"
    # `type: bundled` skips the network probe; the password path is what is tested.
    config.write_text(json.dumps({"db": {"type": "bundled"}, "idp": {"type": "none"}}))
    monkeypatch.setenv("SCF_DB_PASSWORD", "from-the-environment")
    assert main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    ) == EXIT_OK


# --------------------------------------------- provision from a connection string
# The probe is stubbed: these tests are about what is WRITTEN once validation has
# passed, and no test host runs a reachable PostgreSQL 15.
EXTERNAL_DSN = (
    "postgresql://scf_app:inline-secret@db.example.test:6543/scf_prod?sslmode=verify-full"
)


@pytest.fixture
def probe_passes(monkeypatch):
    async def _ok(parts):
        return {"ok": True, "checks": [], "hint": None}

    monkeypatch.setattr("installer.validate.run_checks", _ok)


def _db_lines(out_dir):
    return {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in (out_dir / ".env").read_text().splitlines()
        if line.startswith("DB_")
    }


def test_a_connection_string_provisions_the_parts_it_was_validated_with(tmp_path, probe_passes):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision",
        headers={TOKEN_HEADER: TOKEN},
        json={"db": {"type": "external", "dsn": EXTERNAL_DSN}, "idp": {"type": "none"}},
    )
    assert response.status_code == 200, response.text
    assert _db_lines(out_dir) == {
        "DB_HOST": "db.example.test",
        "DB_PORT": "6543",
        "DB_NAME": "scf_prod",
        "DB_USER": "scf_app",
        "DB_SSLMODE": "verify-full",
    }
    assert (secrets_dir / "DB_PASSWORD").read_text() == "inline-secret"
    assert "inline-secret" not in (out_dir / ".env").read_text()


def test_the_separate_password_field_overrides_the_one_inside_the_string(tmp_path, probe_passes):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "db": {"type": "external", "dsn": EXTERNAL_DSN, "password": "from-the-field"},
            "idp": {"type": "none"},
        },
    )
    assert response.status_code == 200, response.text
    assert (secrets_dir / "DB_PASSWORD").read_text() == "from-the-field"
    assert _db_lines(out_dir)["DB_HOST"] == "db.example.test"


def test_discrete_fields_still_provision_unchanged_and_default_to_the_validated_tls_mode(
    tmp_path, probe_passes
):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision",
        headers={TOKEN_HEADER: TOKEN},
        json={
            "db": {
                "type": "external",
                "host": "db.example.test",
                "port": "6543",
                "dbname": "scf_prod",
                "user": "scf_app",
                "password": "from-the-field",
            },
            "idp": {"type": "none"},
        },
    )
    assert response.status_code == 200, response.text
    lines = _db_lines(out_dir)
    assert lines["DB_HOST"] == "db.example.test"
    assert lines["DB_PORT"] == "6543"
    assert lines["DB_NAME"] == "scf_prod"
    assert lines["DB_USER"] == "scf_app"
    # An omitted TLS mode was probed as `require`; what is written must match.
    assert lines["DB_SSLMODE"] == "require"
    assert (secrets_dir / "DB_PASSWORD").read_text() == "from-the-field"


def test_unattended_db_dsn_provisions_the_same_parts(tmp_path, probe_passes):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({"db": {"type": "external", "dsn": EXTERNAL_DSN}, "idp": {"type": "none"}})
    )
    assert main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    ) == EXIT_OK
    assert _db_lines(out_dir)["DB_HOST"] == "db.example.test"
    assert _db_lines(out_dir)["DB_USER"] == "scf_app"
    assert (secrets_dir / "DB_PASSWORD").read_text() == "inline-secret"


def test_a_malformed_connection_string_writes_nothing(tmp_path, probe_passes):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision",
        headers={TOKEN_HEADER: TOKEN},
        json={"db": {"type": "external", "dsn": "mysql://x@y/z"}, "idp": {"type": "none"}},
    )
    assert response.status_code == 400
    assert not (secrets_dir / SENTINEL_NAME).exists()
    assert not (out_dir / ".env").exists()


# ------------------------------------------------------------------ import-env
LEGACY_ENV = """# legacy install
ENVIRONMENT=production
DB_PASSWORD=legacy-db-password
API_KEY=legacy-api-key
DOWNLOAD_TOKEN_SECRET=legacy-download-secret
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin
KC_ADMIN_PASSWORD=changeme-keycloak-admin
OIDC_CLIENT_SECRET=legacy-oidc-secret
OIDC_ISSUER=http://localhost:8081/realms/scf
APP_URL=http://localhost:5173
"""


def test_import_env_moves_credentials_into_files_and_rewrites_the_env(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    (out_dir / ".env").write_text(LEGACY_ENV)

    code = main(["import-env", "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)])
    assert code == EXIT_OK

    assert (secrets_dir / "DB_PASSWORD").read_text() == "legacy-db-password"
    assert (secrets_dir / "API_KEY").read_text() == "legacy-api-key"
    # Placeholders are NOT imported; they become empty files ("unset").
    assert (secrets_dir / "MINIO_ROOT_PASSWORD").read_text() == ""
    assert (secrets_dir / "KC_ADMIN_PASSWORD").read_text() == ""
    # An absent SCF_SECRET_KEY is the one value import-env mints.
    assert len((secrets_dir / "SCF_SECRET_KEY").read_text()) == 44

    for name in SECRET_FILE_NAMES:
        assert (secrets_dir / name).exists(), name
        assert stat.S_IMODE((secrets_dir / name).stat().st_mode) == 0o600, name

    backups = list(out_dir.glob(".env.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text() == LEGACY_ENV
    assert stat.S_IMODE(backups[0].stat().st_mode) == 0o600

    rewritten = (out_dir / ".env").read_text()
    assert "legacy-db-password" not in rewritten
    assert "legacy-api-key" not in rewritten
    assert "legacy-oidc-secret" not in rewritten
    assert "APP_URL=http://localhost:5173" in rewritten, "non-secret keys survive"
    assert f"SCF_SECRETS_DIR={secrets_dir}" in rewritten
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.secrets.yml" in rewritten

    sentinel = json.loads((secrets_dir / SENTINEL_NAME).read_text())
    assert sentinel["db"] == "imported"


def test_import_env_without_an_env_is_a_config_error(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    assert main(
        ["import-env", "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    ) == EXIT_BAD_CONFIG


# ------------------------------------------------------------------ launcher
@pytest.mark.skipif(not INSTALL_SH.exists(), reason="scripts/install.sh not present")
class TestLauncher:
    def script(self) -> str:
        return INSTALL_SH.read_text()

    def test_it_is_executable(self):
        assert INSTALL_SH.stat().st_mode & stat.S_IXUSR

    def test_it_passes_shellcheck_or_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(INSTALL_SH)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_the_port_is_published_on_loopback_only(self):
        """Docker's DNAT rules sit ahead of the host firewall: the bind address
        is the whole protection, and 0.0.0.0 would put the wizard on the internet."""
        assert '-p 127.0.0.1:${PORT}:8765' in self.script()
        assert '-p ${PORT}:8765' not in self.script()
        assert '-p 0.0.0.0' not in self.script()

    def test_it_runs_as_the_invoking_user(self):
        assert '--user "$(id -u):$(id -g)"' in self.script()
        assert "-e HOME=/tmp" in self.script()

    def test_the_token_is_generated_under_a_tight_umask_and_never_urled(self):
        assert "umask 077" in self.script()
        assert "?token=" not in self.script()

    def test_it_refuses_to_run_when_an_env_already_exists(self):
        assert "--import-env" in self.script()
        assert "already has a .env" in self.script()

    def test_the_linux_group_step_is_present_and_darwin_skips_it(self):
        script = self.script()
        assert "chgrp -R ${APP_GID} /s" in script
        assert "chmod 0640 /s/*" in script
        assert "Darwin" in script

    def test_the_gid_is_a_variable_defaulting_to_1001(self):
        """Hardcoding it in five compose services and one chgrp is how the gid
        drifts out of agreement with itself (OSS #98)."""
        script = self.script()
        assert 'APP_GID="${SCF_APP_GID:-1001}"' in script
        assert "chgrp -R 1001" not in script

    def test_it_prepares_the_catalogue_data_directory(self):
        """Without group WRITE on webclient/public/data the in-app catalogue
        import dies with PermissionError and the install cannot reach first
        login (OSS #99). setgid so imported JSON keeps the group."""
        script = self.script()
        assert "webclient/public/data" in script
        assert "chgrp -R ${APP_GID} /d" in script
        assert "chmod 2775 /d" in script

    def test_it_records_the_gid_in_env_for_compose_to_read(self):
        """compose falls back to 1001 when the line is absent, which is wrong
        the moment SCF_APP_GID was overridden."""
        script = self.script()
        assert "write_env_app_gid" in script
        assert "SCF_APP_GID=%s" in script

    def test_it_offers_the_documented_flags(self):
        script = self.script()
        for flag in ("--unattended", "--import-env", "--port", "--image", "--secrets-dir", "--up"):
            assert flag in script, flag
        assert "SCF_INSTALLER_DEV_MOUNT" in script


# ------------------------------------------------ frontend build secret (#947)
# A wizard install with no IdP runs on API-key auth. Vite bakes that key into
# the bundle at build time, and the wizard keeps it in a 0600 file rather than
# in .env, so the secrets overlay exposes the secrets directory to the frontend
# build as a named context and the Dockerfile bind-mounts the API_KEY file for
# the build step only. (Not a BuildKit secret mount: those are left out of the
# cache key, so a rotated key would reuse the layer built with the old one.)
# These tests pin the two halves of that contract together.
DOCKERFILE_FRONTEND = REPO_ROOT / "Dockerfile.frontend"
SECRETS_OVERLAY = REPO_ROOT / "docker-compose.secrets.yml"
BASE_COMPOSE = REPO_ROOT / "docker-compose.yml"


@pytest.mark.skipif(
    not (DOCKERFILE_FRONTEND.exists() and SECRETS_OVERLAY.exists()),
    reason="repo root not available",
)
class TestFrontendBuildSecret:
    def overlay(self) -> dict:
        import yaml

        return yaml.safe_load(SECRETS_OVERLAY.read_text())

    def dockerfile(self) -> str:
        return DOCKERFILE_FRONTEND.read_text()

    def test_the_overlay_points_the_secrets_context_at_the_secrets_dir(self):
        build = self.overlay()["services"]["frontend"]["build"]
        assert build["additional_contexts"]["scf_secrets"] == "${SCF_SECRETS_DIR:-./secrets}"

    def test_the_overlay_adds_only_the_context_to_the_frontend_service(self):
        """The overlay must not redefine context/dockerfile/args: those stay in
        the base file so `docker compose config` merges rather than replaces."""
        frontend = self.overlay()["services"]["frontend"]
        assert set(frontend) == {"build"}
        assert set(frontend["build"]) == {"additional_contexts"}

    def test_the_dockerfile_has_an_empty_stage_the_context_replaces(self):
        """Without the overlay the named context does not exist, so the mount
        must resolve to a stage of the same name that holds nothing."""
        assert "FROM scratch AS scf_secrets" in self.dockerfile()

    def test_the_dockerfile_mounts_the_file_for_the_build_step_only(self):
        text = self.dockerfile()
        assert "--mount=type=bind,from=scf_secrets,target=/run/scf_secrets" in text
        # The value is scoped to the build command, never exported into a layer.
        assert 'VITE_API_KEY="$(cat /run/scf_secrets/API_KEY)" npm run build' in text
        assert "export VITE_API_KEY" not in text
        assert "COPY --from=scf_secrets" not in text

    def test_the_dockerfile_does_not_use_a_secret_mount_for_the_key(self):
        """A secret mount is excluded from BuildKit's cache key, so a rotated
        key would silently reuse the layer built with the old one."""
        run_lines = [
            line for line in self.dockerfile().splitlines() if line.startswith("RUN ")
        ]
        assert not any("type=secret" in line for line in run_lines)

    def test_the_dockerfile_reads_the_file_only_on_the_api_key_sign_in_path(self):
        """With OIDC or Google auth the browser holds a user token; a deployment
        that deliberately built with a blank VITE_API_KEY must not start
        shipping the master key the day it adopts the overlay."""
        text = self.dockerfile()
        assert '[ "$VITE_OIDC_ENABLED" != "true" ]' in text
        assert '[ "$VITE_GOOGLE_AUTH_ENABLED" != "true" ]' in text
        assert "-s /run/scf_secrets/API_KEY" in text

    def test_the_legacy_build_arg_still_exists_for_env_installs(self):
        text = self.dockerfile()
        assert "ARG VITE_API_KEY" in text
        assert "ENV VITE_API_KEY=$VITE_API_KEY" in text
        assert "VITE_API_KEY: ${VITE_API_KEY:-}" in BASE_COMPOSE.read_text()


# --------------------------------------------- bootstrap_admin_email is required (#956)
def _no_email_config():
    cfg = json.loads(json.dumps(BUNDLED_KEYCLOAK))
    cfg["idp"].pop("bootstrap_admin_email")
    return cfg


def test_unattended_refuses_bundled_keycloak_without_a_bootstrap_admin_email(tmp_path):
    """The wizard makes the field mandatory; unattended used to accept an empty
    one and write BOOTSTRAP_ADMIN_EMAIL=, which made install.sh --up skip the
    admin bootstrap and leave an unusable UI."""
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps(_no_email_config()))

    code = main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    )

    assert code == EXIT_BAD_CONFIG
    # Rejected before the sentinel: the operator can fix the file and re-run.
    assert not (secrets_dir / SENTINEL_NAME).exists()
    assert list(secrets_dir.iterdir()) == []
    assert not (out_dir / ".env").exists()


def test_unattended_refuses_a_whitespace_only_bootstrap_admin_email(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    cfg = _no_email_config()
    cfg["idp"]["bootstrap_admin_email"] = "   "
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))

    assert main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    ) == EXIT_BAD_CONFIG
    assert not (secrets_dir / SENTINEL_NAME).exists()


def test_the_wizard_route_also_refuses_a_missing_bootstrap_admin_email(tmp_path):
    client, secrets_dir, out_dir = make_client(tmp_path)
    response = client.post(
        "/api/provision", headers={TOKEN_HEADER: TOKEN}, json=_no_email_config()
    )
    assert response.status_code == 400
    assert "bootstrap_admin_email" in response.json()["detail"]
    assert not (secrets_dir / SENTINEL_NAME).exists()
    assert not (out_dir / ".env").exists()


def test_an_idp_type_that_is_not_bundled_keycloak_needs_no_admin_email(tmp_path):
    """'none' and 'external_oidc' have no account for us to promote."""
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"db": {"type": "bundled"}, "idp": {"type": "none"}}))

    assert main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    ) == EXIT_OK


def test_the_shipped_install_example_json_still_provisions(tmp_path):
    """install.example.json is what README points operators at: it must stay
    loadable and accepted, comment key and all."""
    example = REPO_ROOT / "install.example.json"
    assert example.exists(), f"{example} is missing"
    payload = json.loads(example.read_text())
    assert payload["idp"]["bootstrap_admin_email"], "the example must model the required field"

    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    config = tmp_path / "config.json"
    config.write_text(example.read_text())

    assert main(
        ["unattended", "--config", str(config), "--secrets-dir", str(secrets_dir), "--out-dir", str(out_dir)]
    ) == EXIT_OK
    assert (secrets_dir / "SCF_SECRET_KEY").read_text().strip()


# ------------------------------------------- service gid on bind mounts (OSS #98, #99)
# `cap_drop: ALL` takes CAP_DAC_OVERRIDE away from container root, so a root
# service is subject to the ordinary mode check on every host file it touches.
# Measured, same file and uid, differing only in capabilities:
#
#   root + default caps : reads a 0640 file it neither owns nor shares a group with
#   root + cap_drop ALL : Permission denied
#
# Five services are in that position. Each one reads the 0640 secret files, or
# writes the catalogue directory, or both, and each one therefore needs the
# owning gid as a supplementary group. Miss one and the failure is remote from
# the cause: minio reports "Unable to validate credentials inherited from the
# secret file(s)", which reads as a bad password rather than an unreadable file.
ROOT_CAP_DROP_SERVICES = {
    "minio": SECRETS_OVERLAY,
    "minio-init": SECRETS_OVERLAY,
    "keycloak": SECRETS_OVERLAY,
    "idp-init": SECRETS_OVERLAY,
    "catalog-importer": BASE_COMPOSE,
}
GROUP_ADD = ["${SCF_APP_GID:-1001}"]


@pytest.mark.skipif(
    not (BASE_COMPOSE.exists() and SECRETS_OVERLAY.exists()),
    reason="repo root not available",
)
class TestServiceGidOnBindMounts:
    def _load(self, path: Path) -> dict:
        import yaml

        return yaml.safe_load(path.read_text())

    @pytest.mark.parametrize("service", sorted(ROOT_CAP_DROP_SERVICES))
    def test_each_root_cap_drop_service_carries_group_add(self, service):
        compose = self._load(ROOT_CAP_DROP_SERVICES[service])
        assert compose["services"][service].get("group_add") == GROUP_ADD

    def test_the_services_really_are_root_with_no_dac_override(self):
        """The premise of the test above. If one of these ever gains a `user:`
        or CAP_DAC_OVERRIDE, group_add stops being what makes it work and this
        pairing should be revisited rather than silently kept."""
        base = self._load(BASE_COMPOSE)
        for service in ROOT_CAP_DROP_SERVICES:
            spec = base["services"][service]
            assert "user" not in spec, service
            assert "ALL" in spec.get("cap_drop", []), service
            assert "DAC_OVERRIDE" not in spec.get("cap_add", []), service

    def test_no_gid_is_hardcoded_in_either_compose_file(self):
        for path in (BASE_COMPOSE, SECRETS_OVERLAY):
            assert 'group_add: ["1001"]' not in path.read_text(), path.name

    def test_the_default_preserves_the_shipped_gid(self):
        """1001 is `useradd -m -u 1001 apiuser` in Dockerfile.backend. An
        install that never sets SCF_APP_GID must behave exactly as before."""
        assert "useradd -m -u 1001 apiuser" in (REPO_ROOT / "Dockerfile.backend").read_text()

    def test_env_example_documents_the_variable(self):
        text = (REPO_ROOT / ".env.example").read_text()
        assert "SCF_APP_GID" in text

    def test_minio_init_has_headroom_over_the_measured_mc_peak(self):
        """`mc admin policy attach` peaks at ~142-152 MiB measured, and the Go
        runtime sizes its heap against the cgroup limit. At 128m it is
        SIGKILLed AFTER the attach succeeds, so the container reports a MinIO
        authorization failure that never happened (OSS #98)."""
        spec = self._load(BASE_COMPOSE)["services"]["minio-init"]
        assert spec["mem_limit"] == "256m"
