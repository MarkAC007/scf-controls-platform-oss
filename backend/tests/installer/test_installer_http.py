"""HTTP surface of the first-run wizard: token, lockout, and the four middlewares."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from installer import RedactionFilter, redact
from installer.app import SECURITY_HEADERS, TOKEN_HEADER, create_app
from installer.writer import SENTINEL_NAME, TOKEN_NAME

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "a-real-provisioning-token-000000000000"


@pytest.fixture
def env(tmp_path):
    secrets_dir = tmp_path / "secrets"
    out_dir = tmp_path / "out"
    secrets_dir.mkdir()
    out_dir.mkdir()
    (secrets_dir / TOKEN_NAME).write_text(TOKEN + "\n")
    lockouts: list[int] = []
    app = create_app(
        secrets_path=secrets_dir,
        out_path=out_dir,
        host_dir=str(secrets_dir),
        port=PORT,
        on_lockout=lambda: lockouts.append(1),
        exit_after_provision=False,
    )
    client = TestClient(app, base_url=BASE, raise_server_exceptions=False)
    return {
        "client": client,
        "secrets": secrets_dir,
        "out": out_dir,
        "lockouts": lockouts,
        "app": app,
    }


def test_correct_token_is_accepted(env):
    response = env["client"].get("/api/status", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 200
    assert response.json()["provisioned"] is False


def test_wrong_token_is_401(env):
    response = env["client"].get("/api/status", headers={TOKEN_HEADER: "wrong"})
    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"


def test_missing_token_is_401(env):
    assert env["client"].get("/api/status").status_code == 401


def test_five_bad_tokens_trigger_lockout_without_a_sentinel(env):
    for i in range(5):
        env["client"].get("/api/status", headers={TOKEN_HEADER: f"bad{i}"})
    assert env["lockouts"] == [1], "the fifth bad token must trigger the exit path"
    assert not (env["secrets"] / SENTINEL_NAME).exists(), "lockout must not write the sentinel"


def test_token_compare_is_constant_time():
    """A prefix of the real token must not be accepted (no `startswith` compare)."""
    import inspect

    from installer.app import TokenGate

    source = inspect.getsource(TokenGate.check)
    assert "compare_digest" in source
    assert "==" not in source.replace("!=", "")


def test_host_header_that_is_not_loopback_gets_421(env):
    response = env["client"].get(
        "/api/status", headers={TOKEN_HEADER: TOKEN, "Host": "evil.test:8765"}
    )
    assert response.status_code == 421


def test_published_port_is_what_the_host_check_accepts(tmp_path):
    """install.sh publishes -p 127.0.0.1:${PORT}:8765, so the browser's Host header
    carries the host-side port, not the container's listen port (F8)."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / TOKEN_NAME).write_text(TOKEN + "\n")
    app = create_app(
        secrets_path=secrets_dir,
        out_path=tmp_path,
        host_dir=str(secrets_dir),
        port=8765,
        public_port=8766,
        exit_after_provision=False,
    )
    client = TestClient(app, base_url="http://127.0.0.1:8766", raise_server_exceptions=False)
    ok = client.get("/api/status", headers={TOKEN_HEADER: TOKEN})
    assert ok.status_code == 200
    also_ok = client.get("/api/status", headers={TOKEN_HEADER: TOKEN, "Host": "localhost:8766"})
    assert also_ok.status_code == 200
    # The container-internal port is not something a browser can ever send.
    internal = client.get("/api/status", headers={TOKEN_HEADER: TOKEN, "Host": "127.0.0.1:8765"})
    assert internal.status_code == 421


def test_localhost_host_header_is_allowed(env):
    response = env["client"].get(
        "/api/status", headers={TOKEN_HEADER: TOKEN, "Host": f"localhost:{PORT}"}
    )
    assert response.status_code == 200


def test_cross_site_request_gets_403(env):
    response = env["client"].get(
        "/api/status", headers={TOKEN_HEADER: TOKEN, "Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403


def test_cross_site_page_navigation_is_allowed_but_api_and_posts_are_not(env):
    """Opening the landing page from another site (a docs link, an extension)
    is a plain top-level GET with nothing to steal; everything else stays 403."""
    nav = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
    assert env["client"].get("/", headers=nav).status_code == 200
    # Same navigation to the API carries no token, so it is a 401, not a leak.
    assert env["client"].get("/api/status", headers=nav).status_code == 401
    # A cross-site fetch (no navigate mode) is refused even with the header.
    fetch = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", TOKEN_HEADER: TOKEN}
    assert env["client"].get("/api/status", headers=fetch).status_code == 403
    # A cross-site form POST is a navigation too, but it is not a GET.
    post_nav = {**nav, TOKEN_HEADER: TOKEN}
    assert env["client"].post("/api/provision", headers=post_nav, json={}).status_code == 403


def test_same_origin_sec_fetch_is_allowed(env):
    response = env["client"].get(
        "/api/status", headers={TOKEN_HEADER: TOKEN, "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 200


def test_token_in_the_query_string_gets_400(env):
    response = env["client"].get("/api/status?token=x", headers={TOKEN_HEADER: TOKEN})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_request"


def test_security_headers_on_the_page_and_on_a_refusal(env):
    for response in (
        env["client"].get("/"),
        env["client"].get("/api/status", headers={"Host": "evil.test:8765"}),
    ):
        for key, value in SECURITY_HEADERS.items():
            assert response.headers.get(key) == value, key


def test_index_and_assets_need_no_token(env):
    assert env["client"].get("/").status_code == 200
    assert env["client"].get("/app.js").status_code == 200
    assert env["client"].get("/app.css").status_code == 200


def test_page_loads_no_third_party_asset(env):
    """A strict CSP is only as good as the markup: every asset must be same-origin."""
    import re as _re

    body = env["client"].get("/").text
    references = _re.findall(r'(?:src|href)="([^"]+)"', body)
    assert references, "the page should reference its own stylesheet and script"
    for reference in references:
        assert reference.startswith("/"), reference
    assert "/app.js" in references and "/app.css" in references
    assert "<script" in body and "<script>" not in body, "no inline script"


def test_redaction_covers_dsn_passwords_and_password_pairs():
    assert "hunter2" not in redact("postgresql://cg:hunter2@db.example:5432/x")
    assert "hunter2" not in redact("password=hunter2")
    assert "hunter2" not in redact("PWD=hunter2")


def test_redaction_filter_rewrites_log_records(caplog):
    logger = logging.getLogger("installer-test-redaction")
    logger.addFilter(RedactionFilter())
    with caplog.at_level(logging.INFO, logger="installer-test-redaction"):
        logger.info("connecting to %s", "postgresql://cg:hunter2@db.example:5432/x")
    assert "hunter2" not in caplog.text
