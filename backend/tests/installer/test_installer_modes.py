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
        assert "chgrp -R 1001 /s" in script
        assert "chmod 0640 /s/*" in script
        assert "Darwin" in script

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
