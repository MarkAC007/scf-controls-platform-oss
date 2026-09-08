"""``require_auth`` must publish the caller on ``request.state.user`` (#922).

The audit middleware and ``detect_action_source`` read ``request.state.user``
because they run outside dependency injection. Before this fix nothing set
it in production code, so the middleware skipped every mutation and every
row that *was* written said ``action_source='system'``.

Two layers here:

* Unit: build a bare Starlette request, stub the token check, call the real
  ``require_auth`` and the dependencies layered on it, and read the state
  back.
* Static sweep: every call to ``require_auth(`` in the backend must pass the
  request through. A single caller that forgot would silently fall back to
  the old behaviour on that route, and no unit test would notice — this is
  the external oracle for the class of edit that #922 required.
"""
from __future__ import annotations

import os
import pathlib
import re
import sys

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402
from services.audit_service import detect_action_source  # noqa: E402

BACKEND = pathlib.Path(__file__).resolve().parent.parent



def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({
        "type": "http", "method": "POST", "path": "/api/x",
        "headers": raw, "query_string": b"", "client": ("127.0.0.1", 1),
    })


def _creds() -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials="stub")


@pytest.fixture
def stub_user(monkeypatch):
    user = auth_mod.User(user_id="u1", email="u1@example.invalid",
                         auth_method="google", db_id="00000000-0000-0000-0000-000000000001")

    async def _authenticate(credentials, db):
        return user

    monkeypatch.setattr(auth_mod, "_authenticate", _authenticate)
    return user


class TestRequireAuthPublishesTheCaller:
    # Explicit, not inherited. `backend/pytest.ini` sets asyncio_mode=auto, but
    # CI runs pytest from the repository root, where that file is not the
    # config and every async test would otherwise error with "async def
    # functions are not natively supported". Marked per class rather than per
    # module so the synchronous sweep below does not carry a pointless mark.
    pytestmark = pytest.mark.asyncio

    async def test_state_user_is_the_authenticated_user(self, stub_user):
        request = _request()
        assert getattr(request.state, "user", None) is None
        returned = await auth_mod.require_auth(request, _creds(), db=None)
        assert returned is stub_user
        assert request.state.user is stub_user

    async def test_the_middleware_can_now_attribute_the_write(self, stub_user):
        """The whole point: with state set, a Google caller reads as 'ui'."""
        request = _request()
        assert detect_action_source(request) == "system"  # before auth
        await auth_mod.require_auth(request, _creds(), db=None)
        assert detect_action_source(request) == "ui"

    async def test_an_mcp_api_key_caller_reads_as_mcp(self, monkeypatch):
        user = auth_mod.User(user_id="u2", email="u2@example.invalid",
                             auth_method="user_api_key", db_id="00000000-0000-0000-0000-000000000002")

        async def _authenticate(credentials, db):
            return user

        monkeypatch.setattr(auth_mod, "_authenticate", _authenticate)
        request = _request({"user-agent": "mcp-server-scf/3.0.0"})
        await auth_mod.require_auth(request, _creds(), db=None)
        assert detect_action_source(request) == "mcp"

    async def test_a_failed_login_leaves_state_untouched(self, monkeypatch):
        async def _authenticate(credentials, db):
            raise HTTPException(status_code=401, detail="nope")

        monkeypatch.setattr(auth_mod, "_authenticate", _authenticate)
        request = _request()
        with pytest.raises(HTTPException):
            await auth_mod.require_auth(request, _creds(), db=None)
        assert getattr(request.state, "user", None) is None

    async def test_optional_auth_publishes_too(self, stub_user):
        request = _request({"authorization": "Bearer stub"})
        returned = await auth_mod.optional_auth(request, db=None)
        assert returned is stub_user
        assert request.state.user is stub_user


class TestEveryCallerPassesTheRequestThrough:
    """Static oracle over the source: no caller may call the old shape."""

    _CALL = re.compile(r"\brequire_auth\((?!\s*\))")

    def _sources(self):
        files = [BACKEND / "auth.py", *sorted((BACKEND / "api").glob("*.py")),
                 *sorted((BACKEND / "services").glob("*.py"))]
        for path in files:
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith(("def ", "async def ", "#")):
                    continue
                if "Depends(require_auth)" in line or "Security(require_auth)" in line:
                    continue  # FastAPI injects `request` itself for these
                if self._CALL.search(line):
                    yield path.relative_to(BACKEND), lineno, stripped

    def test_the_sweep_sees_the_known_callers(self):
        found = {(str(p), n) for p, n, _ in self._sources()}
        assert any(p == "auth.py" for p, _ in found), "sweep is blind — it found no callers in auth.py"
        assert len(found) >= 7, found

    def test_no_dependency_resolves_a_user_behind_require_auths_back(self):
        """The other half of the sweep: nothing may bypass ``require_auth``.

        ``validate_google_token``, ``validate_api_key`` and
        ``validate_user_api_key`` each turn a token into a ``User``. They are
        internals of ``_authenticate``; wiring one straight into a route as a
        dependency would authenticate the caller without ever touching
        ``request.state``, which is exactly the shape of the #922 defect. The
        require_* family is the only supported way in, and every member of it
        reaches ``require_auth``.
        """
        bypasses = re.compile(
            r"(?:Depends|Security)\(\s*(validate_google_token|validate_api_key|validate_user_api_key)\b"
        )
        offenders = []
        for path in [BACKEND / "auth.py", *sorted((BACKEND / "api").glob("*.py"))]:
            for lineno, line in enumerate(path.read_text().splitlines(), 1):
                if bypasses.search(line):
                    offenders.append(f"{path.relative_to(BACKEND)}:{lineno}: {line.strip()}")

        assert not offenders, (
            "a route authenticates through a token validator directly, so "
            "request.state.user is never set on it:\n" + "\n".join(offenders)
        )

    def test_no_direct_call_omits_the_request(self):
        offenders = [
            f"{p}:{n}: {line}" for p, n, line in self._sources()
            if not re.search(r"require_auth\(\s*request\b", line)
        ]
        assert not offenders, "callers still using the pre-#922 signature:\n" + "\n".join(offenders)
