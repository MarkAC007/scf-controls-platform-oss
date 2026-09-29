"""Who may mint and revoke API keys, and what a key may do once its owner changes.

Editors could not create API keys: the endpoint required org admin, although
the UI offered the form to everyone and said the key "will inherit your
current role". Revoke had been raised to admin (#296), which left its own
"non-admins revoke only their own keys" branch unreachable.

Now:

* create and revoke require editor; list stays viewer;
* a key carries its creator's role, so an editor's key is an editor key;
* an editor can revoke only their own keys;
* the role frozen on a key is a ceiling — the owner's current access to the
  key's org caps it, and an owner with no access left gets a 401.
"""
from __future__ import annotations

import hashlib
import os
import sys
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth as auth_mod  # noqa: E402
import catalog_models  # noqa: E402,F401 — registers SystemCatalogTemplate for the ApiKey mapper
from api import api_keys as api_keys_mod  # noqa: E402
from schemas import ApiKeyCreate  # noqa: E402

# CI runs pytest from the repository root, where backend/pytest.ini's
# asyncio_mode=auto does not apply — mark explicitly.
pytestmark = pytest.mark.asyncio

ORG = uuid.uuid4()
OWNER = uuid.uuid4()
OTHER = uuid.uuid4()


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return SimpleNamespace(all=lambda: self._value)


class _ScriptedDB:
    """Answers each ``execute`` with the next scripted value, in order."""

    def __init__(self, *answers):
        self._answers = list(answers)
        self.added = []
        self.executed = 0

    async def execute(self, _stmt):
        self.executed += 1
        return _Result(self._answers.pop(0))

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        pass

    async def refresh(self, obj):
        # Stand in for the server default the real refresh would load.
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.utcnow()


def _request():
    return SimpleNamespace(headers={}, state=SimpleNamespace())


def _membership(role: str, user_id=OWNER):
    user = auth_mod.User(user_id="sub", email="u@example.invalid",
                         auth_method="google", db_id=str(user_id))
    return auth_mod.OrgMembership(user=user, organization_id=ORG, role=role)


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    async def _log(**_kw):
        return None
    monkeypatch.setattr(api_keys_mod, "log_entity_changes", _log)


# ── Route gates ─────────────────────────────────────────────────────────────

def _route(method: str):
    for route in api_keys_mod.router.routes:
        if method in route.methods:
            return route
    raise AssertionError(f"no {method} route")


@pytest.mark.parametrize("method,expected", [
    ("POST", "editor"),
    ("DELETE", "editor"),
    ("GET", "viewer"),
])
async def test_route_minimum_role(monkeypatch, method, expected):
    """Invoke each route's real membership dependency and read the role it asks for."""
    seen = {}

    async def _auth(request, credentials, db):
        return "caller"

    async def _verify(org_id, user, db, min_role):
        seen["min_role"] = min_role
        return "membership"

    monkeypatch.setattr(auth_mod, "require_auth", _auth)
    monkeypatch.setattr(auth_mod, "verify_org_membership", _verify)

    dep = next(d for d in _route(method).dependant.dependencies if d.name == "membership")
    await dep.call(request=None, org_id=ORG, credentials=None, db=None)
    assert seen["min_role"] == expected


# ── Create ──────────────────────────────────────────────────────────────────

async def test_editor_creates_an_editor_key():
    db = _ScriptedDB()
    created = await api_keys_mod.create_api_key(
        org_id=ORG, body=ApiKeyCreate(name="mcp"), request=_request(),
        membership=_membership("editor"), db=db,
    )
    assert created.role == "editor"
    assert created.plaintext_key.startswith("scf_")
    (stored,) = db.added
    assert stored.role == "editor"
    assert stored.user_id == OWNER


# ── Revoke ──────────────────────────────────────────────────────────────────

def _key(user_id, role="editor"):
    return SimpleNamespace(id=uuid.uuid4(), user_id=user_id, organization_id=ORG,
                           name="k", key_prefix="scf_abcd", role=role,
                           is_active=True, expires_at=None, last_used_at=None)


async def test_editor_revokes_own_key():
    key = _key(OWNER)
    await api_keys_mod.revoke_api_key(
        org_id=ORG, key_id=key.id, request=_request(),
        membership=_membership("editor"), db=_ScriptedDB(key),
    )
    assert key.is_active is False


async def test_editor_cannot_revoke_someone_elses_key():
    key = _key(OTHER)
    with pytest.raises(HTTPException) as exc:
        await api_keys_mod.revoke_api_key(
            org_id=ORG, key_id=key.id, request=_request(),
            membership=_membership("editor"), db=_ScriptedDB(key),
        )
    assert exc.value.status_code == 403
    assert key.is_active is True


async def test_admin_revokes_any_key():
    key = _key(OTHER)
    await api_keys_mod.revoke_api_key(
        org_id=ORG, key_id=key.id, request=_request(),
        membership=_membership("admin"), db=_ScriptedDB(key),
    )
    assert key.is_active is False


# ── Using a key after the owner's access changes ───────────────────────────

TOKEN = "scf_" + "a" * 36


def _stored_key(role: str):
    return SimpleNamespace(
        key_prefix=TOKEN[:8], key_hash=hashlib.sha256(TOKEN.encode()).hexdigest(),
        is_active=True, expires_at=None, last_used_at=None,
        user_id=OWNER, organization_id=ORG, role=role,
    )


def _owner():
    return SimpleNamespace(id=OWNER, google_sub="sub", email="u@example.invalid",
                           display_name="U")


async def test_key_works_at_its_own_role_while_owner_unchanged():
    db = _ScriptedDB([_stored_key("editor")], _owner(), "editor")
    user = await auth_mod.validate_user_api_key(TOKEN, db)
    assert user._api_key_role == "editor"


async def test_demoted_owner_caps_the_key():
    db = _ScriptedDB([_stored_key("admin")], _owner(), "viewer")
    user = await auth_mod.validate_user_api_key(TOKEN, db)
    assert user._api_key_role == "viewer"


async def test_promoted_owner_does_not_raise_the_key():
    db = _ScriptedDB([_stored_key("editor")], _owner(), "admin")
    user = await auth_mod.validate_user_api_key(TOKEN, db)
    assert user._api_key_role == "editor"


async def test_active_consultant_owner_is_honoured():
    # No direct membership, active consultant relationship at editor.
    db = _ScriptedDB([_stored_key("editor")], _owner(), None, "editor")
    user = await auth_mod.validate_user_api_key(TOKEN, db)
    assert user._api_key_role == "editor"


async def test_removed_owner_key_is_rejected():
    db = _ScriptedDB([_stored_key("admin")], _owner(), None, None)
    with pytest.raises(HTTPException) as exc:
        await auth_mod.validate_user_api_key(TOKEN, db)
    assert exc.value.status_code == 401


async def test_capped_role_is_what_org_checks_enforce():
    """End to end through verify_org_membership: a demoted owner's admin key
    can no longer pass an admin gate."""
    db = _ScriptedDB([_stored_key("admin")], _owner(), "editor")
    user = await auth_mod.validate_user_api_key(TOKEN, db)
    ok = await auth_mod.verify_org_membership(ORG, user, db, "editor")
    assert ok.role == "editor"
    with pytest.raises(HTTPException) as exc:
        await auth_mod.verify_org_membership(ORG, user, db, "admin")
    assert exc.value.status_code == 403
