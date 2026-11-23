# SPDX-License-Identifier: AGPL-3.0-only
"""The public subprocessor list on the trust portal, derived from vendors (#1321).

A vendor is listed on ``GET /public/trust/{slug}`` when ``is_subprocessor`` is
true and its status is live (``active`` or ``approved``). Each entry carries
exactly ``name``, ``purpose`` and ``location`` and nothing else from the vendor
row. The switch and the two public fields are set from a signed-in session
only: an API key (the per-user key or the platform-wide one) is refused when
it tries to change them, on create or on update. Every flip is audited.

The structural half needs no database. The database half calls the real
endpoint functions on a transaction that is always rolled back, and skips
(never passes) when no Postgres resolves.
"""
import json
import uuid as _uuid
from datetime import date
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import String, Text, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

import redis_client
from api import trust_portal as trust_api
from api import vendors as vendors_api
from auth import OrgMembership
from models import AuditLog, Organization, User, Vendor
from schemas import TrustPortalSubprocessor, VendorCreate, VendorUpdate

from test_journey_service import DATABASE_URL, requires_postgres

PUBLIC_FIELDS = {"is_subprocessor", "subprocessor_purpose", "subprocessor_location"}
NOT_LIVE = ("prospect", "under_review", "suspended", "offboarded")


# ---------------------------------------------------------------------------
# Structural: no database
# ---------------------------------------------------------------------------


def test_the_public_entry_has_exactly_three_fields():
    assert set(TrustPortalSubprocessor.model_fields) == {"name", "purpose", "location"}


def test_the_query_selects_only_the_three_public_columns():
    query = trust_api._build_subprocessor_query(_uuid.uuid4())
    assert [c.name for c in query.selected_columns] == [
        "name", "subprocessor_purpose", "subprocessor_location",
    ]
    sql = str(query.compile(compile_kwargs={"literal_binds": True}))
    assert "is_subprocessor" in sql
    assert "'active'" in sql and "'approved'" in sql
    for status in NOT_LIVE:
        assert f"'{status}'" not in sql


def test_only_active_and_approved_count_as_live():
    assert set(trust_api.SUBPROCESSOR_LIVE_STATUSES) == {"active", "approved"}


def test_an_explicit_null_switch_is_refused_by_the_schema():
    with pytest.raises(ValidationError):
        VendorUpdate.model_validate({"is_subprocessor": None})
    # leaving it out, and clearing the two text fields, are both fine
    assert "is_subprocessor" not in VendorUpdate().model_dump(exclude_unset=True)
    cleared = VendorUpdate.model_validate({"subprocessor_purpose": None, "subprocessor_location": None})
    assert cleared.model_dump(exclude_unset=True) == {
        "subprocessor_purpose": None, "subprocessor_location": None,
    }


def test_a_new_vendor_is_not_a_subprocessor_by_default():
    assert VendorCreate(name="x").is_subprocessor is False


# ---------------------------------------------------------------------------
# Database-backed: the real endpoints on a rolled-back transaction
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db():
    """A session on a transaction that is always rolled back."""
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect():
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        await engine.dispose()
        pytest.skip(f"database not reachable: {exc}")
    session = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


@pytest.fixture
def no_commit(db, monkeypatch):
    """Endpoints commit; inside the rolled-back test transaction that is a flush."""
    monkeypatch.setattr(db, "commit", db.flush)


class _NoCache:
    """Stands in for Redis: never a hit, so every call computes from the database."""

    def __init__(self):
        self.written = {}

    async def get(self, key):
        return None

    async def setex(self, key, ttl, value):
        self.written[key] = value


@pytest.fixture
def no_cache(monkeypatch):
    cache = _NoCache()

    async def _client():
        return cache

    monkeypatch.setattr(redis_client, "get_redis_client", _client)
    return cache


async def _org(db, enabled=True):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(
        name=f"sub-{tag}", slug=f"sub-{tag}",
        settings={"is_trust_portal_enabled": enabled},
    )
    db.add(org)
    await db.flush()
    return org


async def _user(db):
    tag = _uuid.uuid4().hex[:10]
    user = User(google_sub=f"sub-{tag}", email=f"sub-{tag}@example.invalid")
    db.add(user)
    await db.flush()
    return user


def _caller(org, user, auth_method):
    return OrgMembership(
        user=SimpleNamespace(db_id=str(user.id), email=user.email, auth_method=auth_method),
        organization_id=org.id,
        role="editor",
    )


def _request(caller=None):
    req = Request({"type": "http", "headers": [], "method": "POST", "path": "/"})
    if caller is not None:
        req.state.user = caller.user
    return req


async def _vendor(db, org, **fields):
    vendor = Vendor(organization_id=org.id, name=fields.pop("name", f"v-{_uuid.uuid4().hex[:8]}"), **fields)
    db.add(vendor)
    await db.flush()
    return vendor


async def _portal(db, org):
    """Call the route the way FastAPI would, past the rate limiter, and serialise it."""
    out = await trust_api.get_trust_portal.__wrapped__(org.slug, None, None, db)
    return json.loads(out.model_dump_json())


@requires_postgres
@pytest.mark.asyncio
class TestWhoAppears:

    async def test_switch_off_never_appears(self, db, no_cache):
        org = await _org(db)
        await _vendor(db, org, name="Off Active", status="active", is_subprocessor=False)
        await _vendor(db, org, name="Off Approved", status="approved", is_subprocessor=False)
        body = await _portal(db, org)
        assert body["subprocessors"] == []

    @pytest.mark.parametrize("status", NOT_LIVE)
    async def test_a_vendor_that_is_not_live_never_appears(self, db, no_cache, status):
        org = await _org(db)
        await _vendor(db, org, name=f"On {status}", status=status, is_subprocessor=True,
                      subprocessor_purpose="Hosting", subprocessor_location="EU")
        await _vendor(db, org, name=f"Off {status}", status=status, is_subprocessor=False)
        body = await _portal(db, org)
        assert body["subprocessors"] == []

    async def test_active_and_approved_appear_sorted_by_name(self, db, no_cache):
        org = await _org(db)
        await _vendor(db, org, name="zeta Mail", status="approved", is_subprocessor=True,
                      subprocessor_purpose="Email delivery", subprocessor_location="United States")
        await _vendor(db, org, name="Acme Cloud", status="active", is_subprocessor=True,
                      subprocessor_purpose="Hosting", subprocessor_location="EU (Frankfurt)")
        await _vendor(db, org, name="Middle Prospect", status="prospect", is_subprocessor=True)
        body = await _portal(db, org)
        assert body["subprocessors"] == [
            {"name": "Acme Cloud", "purpose": "Hosting", "location": "EU (Frankfurt)"},
            {"name": "zeta Mail", "purpose": "Email delivery", "location": "United States"},
        ]

    async def test_another_organisations_vendor_never_appears(self, db, no_cache):
        org = await _org(db)
        other = await _org(db)
        await _vendor(db, other, name="Elsewhere Ltd", status="active", is_subprocessor=True)
        body = await _portal(db, org)
        assert body["subprocessors"] == []

    async def test_an_empty_purpose_or_location_is_null(self, db, no_cache):
        org = await _org(db)
        await _vendor(db, org, name="Bare Vendor", status="active", is_subprocessor=True,
                      subprocessor_purpose="", subprocessor_location=None)
        body = await _portal(db, org)
        assert body["subprocessors"] == [{"name": "Bare Vendor", "purpose": None, "location": None}]

    async def test_no_other_vendor_field_reaches_the_response(self, db, no_cache):
        org = await _org(db)
        marker = f"LEAK{_uuid.uuid4().hex[:8]}"
        texts = {}
        for col in Vendor.__table__.columns:
            if col.name in {"name", "status", "subprocessor_purpose", "subprocessor_location"}:
                continue
            if isinstance(col.type, (String, Text)):
                limit = getattr(col.type, "length", None) or 200
                texts[col.name] = f"{marker}{col.name}"[:limit]
        vendor = await _vendor(
            db, org, name="Listed Vendor", status="active", is_subprocessor=True,
            subprocessor_purpose="Hosting", subprocessor_location="EU",
            contract_start_date=date(2031, 7, 19), contract_end_date=date(2032, 8, 21),
            contract_value=918273.64, risk_score=23, **texts,
        )
        # every sensitive text column really carries the marker (the sweep is not empty)
        assert {"description", "contact_email", "contact_name", "contact_phone", "data_location",
                "flow_down_requirements", "shared_responsibility_note", "data_classification",
                "risk_level", "criticality", "website", "category"} <= set(texts)
        raw = json.dumps(await _portal(db, org))
        # positive control: the vendor is in the list, so the absences below mean something
        assert "Listed Vendor" in raw and "Hosting" in raw
        assert marker not in raw
        assert str(vendor.id) not in raw
        for leaked in ("918273", "2031-07-19", "2032-08-21"):
            assert leaked not in raw
        [entry] = json.loads(raw)["subprocessors"]
        assert set(entry) == {"name", "purpose", "location"}

    async def test_the_cached_body_carries_the_list(self, db, no_cache):
        org = await _org(db)
        await _vendor(db, org, name="Cached Co", status="active", is_subprocessor=True)
        await _portal(db, org)
        [stored] = no_cache.written.values()
        assert json.loads(stored)["subprocessors"] == [{"name": "Cached Co", "purpose": None, "location": None}]


@requires_postgres
@pytest.mark.asyncio
class TestPortalDisabled:

    async def test_disabled_portal_is_404_even_with_subprocessors(self, db, no_cache):
        org = await _org(db, enabled=False)
        await _vendor(db, org, name="Hidden Co", status="active", is_subprocessor=True)
        with pytest.raises(HTTPException) as exc:
            await _portal(db, org)
        assert exc.value.status_code == 404


@requires_postgres
@pytest.mark.asyncio
class TestWhoMayPublish:

    @pytest_asyncio.fixture
    async def world(self, db, no_commit, monkeypatch):
        async def _room(org_id, db):
            return True

        monkeypatch.setattr(vendors_api, "check_vendor_limit", _room)
        org = await _org(db)
        user = await _user(db)
        return SimpleNamespace(org=org, user=user)

    @pytest.mark.parametrize("key", ["user_api_key", "api_key"])
    @pytest.mark.parametrize("field,value", [
        ("is_subprocessor", True),
        ("subprocessor_purpose", "Hosting"),
        ("subprocessor_location", "EU"),
    ])
    async def test_an_api_key_cannot_create_a_listed_vendor(self, db, world, key, field, value):
        caller = _caller(world.org, world.user, key)
        name = f"k-{_uuid.uuid4().hex[:8]}"
        with pytest.raises(HTTPException) as exc:
            await vendors_api.create_vendor(
                world.org.id, VendorCreate(name=name, **{field: value}), _request(caller), caller, db,
            )
        assert exc.value.status_code == 403
        assert field in exc.value.detail
        rows = (await db.execute(select(Vendor).where(Vendor.organization_id == world.org.id))).scalars().all()
        assert rows == []

    @pytest.mark.parametrize("key", ["user_api_key", "api_key"])
    @pytest.mark.parametrize("field,value", [
        ("is_subprocessor", True),
        ("subprocessor_purpose", "Hosting"),
        ("subprocessor_location", "EU"),
    ])
    async def test_an_api_key_cannot_change_the_public_fields(self, db, world, key, field, value):
        vendor = await _vendor(db, world.org, status="active")
        caller = _caller(world.org, world.user, key)
        with pytest.raises(HTTPException) as exc:
            await vendors_api.update_vendor(
                world.org.id, vendor.id, VendorUpdate(**{field: value}), _request(caller), caller, db,
            )
        assert exc.value.status_code == 403
        assert field in exc.value.detail
        await db.refresh(vendor)
        assert vendor.is_subprocessor is False
        assert vendor.subprocessor_purpose is None and vendor.subprocessor_location is None

    async def test_an_api_key_cannot_turn_the_switch_off_either(self, db, world):
        vendor = await _vendor(db, world.org, status="active", is_subprocessor=True,
                               subprocessor_purpose="Hosting", subprocessor_location="EU")
        caller = _caller(world.org, world.user, "user_api_key")
        with pytest.raises(HTTPException) as exc:
            await vendors_api.update_vendor(
                world.org.id, vendor.id,
                VendorUpdate(is_subprocessor=False, subprocessor_location=None),
                _request(caller), caller, db,
            )
        assert exc.value.status_code == 403
        assert "is_subprocessor" in exc.value.detail and "subprocessor_location" in exc.value.detail
        await db.refresh(vendor)
        assert vendor.is_subprocessor is True and vendor.subprocessor_location == "EU"

    async def test_an_api_key_may_still_edit_other_fields_and_echo_the_public_ones(self, db, world):
        vendor = await _vendor(db, world.org, status="active", is_subprocessor=True,
                               subprocessor_purpose="Hosting", subprocessor_location="EU")
        caller = _caller(world.org, world.user, "user_api_key")
        out = await vendors_api.update_vendor(
            world.org.id, vendor.id,
            # a read-modify-write client sends the public fields back unchanged
            VendorUpdate(description="Renewed for 2027", is_subprocessor=True,
                         subprocessor_purpose="Hosting", subprocessor_location="EU"),
            _request(caller), caller, db,
        )
        assert out.description == "Renewed for 2027"
        assert out.is_subprocessor is True

    @pytest.mark.parametrize("key", ["user_api_key", "api_key"])
    async def test_an_api_key_cannot_rename_a_vendor_whose_switch_is_on(self, db, world, no_cache, key):
        vendor = await _vendor(db, world.org, name="Listed Ltd", status="active", is_subprocessor=True)
        caller = _caller(world.org, world.user, key)
        with pytest.raises(HTTPException) as exc:
            await vendors_api.update_vendor(
                world.org.id, vendor.id, VendorUpdate(name="Anything a key likes"),
                _request(caller), caller, db,
            )
        assert exc.value.status_code == 403
        assert "name" in exc.value.detail
        await db.refresh(vendor)
        assert vendor.name == "Listed Ltd"
        assert [s["name"] for s in (await _portal(db, world.org))["subprocessors"]] == ["Listed Ltd"]

    @pytest.mark.parametrize("status", ["active", "approved"])
    async def test_an_api_key_cannot_move_a_switched_on_vendor_into_a_live_status(self, db, world, no_cache, status):
        vendor = await _vendor(db, world.org, status="prospect", is_subprocessor=True)
        caller = _caller(world.org, world.user, "user_api_key")
        with pytest.raises(HTTPException) as exc:
            await vendors_api.update_vendor(
                world.org.id, vendor.id, VendorUpdate(status=status), _request(caller), caller, db,
            )
        assert exc.value.status_code == 403
        assert "status" in exc.value.detail
        await db.refresh(vendor)
        assert vendor.status == "prospect"
        assert (await _portal(db, world.org))["subprocessors"] == []

    async def test_an_api_key_may_take_a_listed_vendor_out_of_a_live_status(self, db, world, no_cache):
        vendor = await _vendor(db, world.org, status="active", is_subprocessor=True)
        caller = _caller(world.org, world.user, "user_api_key")
        out = await vendors_api.update_vendor(
            world.org.id, vendor.id, VendorUpdate(status="suspended"), _request(caller), caller, db,
        )
        assert out.status == "suspended"
        assert (await _portal(db, world.org))["subprocessors"] == []

    async def test_an_api_key_may_rename_and_move_a_vendor_whose_switch_is_off(self, db, world):
        vendor = await _vendor(db, world.org, status="prospect")
        caller = _caller(world.org, world.user, "user_api_key")
        out = await vendors_api.update_vendor(
            world.org.id, vendor.id, VendorUpdate(name=f"r-{_uuid.uuid4().hex[:8]}", status="active"),
            _request(caller), caller, db,
        )
        assert out.status == "active" and out.name.startswith("r-")

    async def test_an_api_key_may_echo_the_name_and_status_of_a_listed_vendor(self, db, world):
        vendor = await _vendor(db, world.org, name="Echo Ltd", status="approved", is_subprocessor=True)
        caller = _caller(world.org, world.user, "user_api_key")
        out = await vendors_api.update_vendor(
            world.org.id, vendor.id,
            VendorUpdate(name="Echo Ltd", status="active", description="Still here"),
            _request(caller), caller, db,
        )
        assert (out.name, out.status, out.description) == ("Echo Ltd", "active", "Still here")

    async def test_a_session_can_rename_a_listed_vendor(self, db, world):
        vendor = await _vendor(db, world.org, name="Old Name Ltd", status="active", is_subprocessor=True)
        caller = _caller(world.org, world.user, "oidc")
        out = await vendors_api.update_vendor(
            world.org.id, vendor.id, VendorUpdate(name="New Name Ltd"), _request(caller), caller, db,
        )
        assert out.name == "New Name Ltd"

    @pytest.mark.parametrize("method", ["a_method_added_later", None])
    async def test_a_caller_that_is_not_a_known_session_is_refused(self, db, world, method):
        vendor = await _vendor(db, world.org, status="active")
        caller = _caller(world.org, world.user, method)
        with pytest.raises(HTTPException) as exc:
            await vendors_api.update_vendor(
                world.org.id, vendor.id, VendorUpdate(is_subprocessor=True), _request(caller), caller, db,
            )
        assert exc.value.status_code == 403
        await db.refresh(vendor)
        assert vendor.is_subprocessor is False

    async def test_an_api_key_may_create_an_unlisted_vendor(self, db, world):
        caller = _caller(world.org, world.user, "user_api_key")
        out = await vendors_api.create_vendor(
            world.org.id, VendorCreate(name=f"k-{_uuid.uuid4().hex[:8]}", subprocessor_purpose=""),
            _request(caller), caller, db,
        )
        assert out.is_subprocessor is False

    async def test_a_session_can_create_a_listed_vendor(self, db, world):
        caller = _caller(world.org, world.user, "google")
        out = await vendors_api.create_vendor(
            world.org.id,
            VendorCreate(name=f"s-{_uuid.uuid4().hex[:8]}", status="active", is_subprocessor=True,
                         subprocessor_purpose="Hosting", subprocessor_location="EU"),
            _request(caller), caller, db,
        )
        assert out.is_subprocessor is True
        assert (out.subprocessor_purpose, out.subprocessor_location) == ("Hosting", "EU")

    async def test_a_session_turns_the_switch_and_each_flip_is_audited(self, db, world):
        vendor = await _vendor(db, world.org, status="active")
        caller = _caller(world.org, world.user, "oidc")
        on = await vendors_api.update_vendor(
            world.org.id, vendor.id,
            VendorUpdate(is_subprocessor=True, subprocessor_purpose="Payroll", subprocessor_location="UK"),
            _request(caller), caller, db,
        )
        assert on.is_subprocessor is True
        off = await vendors_api.update_vendor(
            world.org.id, vendor.id, VendorUpdate(is_subprocessor=False), _request(caller), caller, db,
        )
        assert off.is_subprocessor is False

        rows = (await db.execute(
            select(AuditLog).where(
                AuditLog.organization_id == world.org.id,
                AuditLog.entity_id == vendor.id,
            )
        )).scalars().all()
        # one transaction, so both rows share changed_at: compare as a set, not in order
        flips = sorted((r.old_value, r.new_value) for r in rows if r.field_name == "is_subprocessor")
        assert flips == [("false", "true"), ("true", "false")]
        texts = {r.field_name: r.new_value for r in rows if r.field_name != "is_subprocessor"}
        assert texts == {"subprocessor_purpose": json.dumps("Payroll"), "subprocessor_location": json.dumps("UK")}
        for r in rows:
            assert r.changed_by_user_id == world.user.id
            assert r.changed_at is not None
            assert r.action_source == "ui"
