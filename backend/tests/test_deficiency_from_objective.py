# SPDX-License-Identifier: AGPL-3.0-only
"""Raising a deficiency from an off-track security objective (clause 10.2).

The create route did not accept ``source`` or ``source_ref`` before: the
fields were dropped silently, and a deficiency with neither a control nor a
risk was a 422. These tests pin:

* the body accepts ``source`` ``security_objective`` only, with the
  objective's id in ``source_ref``; a ref without a source, a source without
  a ref, a ref that is not an id, or any other source is refused;
* raised from an objective, the deficiency needs no control or risk and
  stores both fields; an archived objective or another organisation's is a
  422; without a source the old rule (control or risk) still holds.
"""
import json
import uuid as _uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from sqlalchemy import select

from api import deficiencies as api
from api.deficiencies import DeficiencyRaise
from auth import OrgMembership
from catalog_models import SCFCatalogControl
from models import AuditLog, Organization, OrganizationMember, ScopedControl, SecurityObjective, User
from services.deficiencies import SOURCE_CAPABILITY_TEST, SOURCES

#: The refusal for a body with no control and no risk, as it stood before
#: objectives could raise deficiencies. It must not change by a byte.
NO_SUBJECT = "A deficiency is raised against a control (scf_id), a risk (risk_code), or both"

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401


class TestTheBody:
    def test_the_route_takes_the_raise_body(self):
        import inspect
        assert inspect.signature(api.create_deficiency).parameters["data"].annotation is DeficiencyRaise

    def test_an_objective_source_with_its_id_is_accepted(self):
        oid = _uuid.uuid4()
        body = DeficiencyRaise(title="Objective OBJ-01 off track: x", source="security_objective",
                               source_ref=f"  {str(oid).upper()} ")
        assert body.source == "security_objective" and body.source_ref == str(oid)

    @pytest.mark.parametrize("fields", [
        {"source": "control_at_risk", "source_ref": str(_uuid.uuid4())},
        {"source": "security_objective"},
        {"source": "security_objective", "source_ref": "OBJ-01"},
        {"source_ref": str(_uuid.uuid4())},
    ])
    def test_anything_else_is_refused(self, fields):
        with pytest.raises(ValidationError):
            DeficiencyRaise(title="t", **fields)

    @pytest.mark.parametrize("automatic", [*SOURCES, SOURCE_CAPABILITY_TEST])
    def test_a_caller_cannot_post_an_automatic_source(self, automatic):
        with pytest.raises(ValidationError):
            DeficiencyRaise(title="t", scf_id="GOV-01", source=automatic, source_ref=str(_uuid.uuid4()))

    def test_without_a_source_nothing_changes(self):
        body = DeficiencyRaise(title="t", scf_id="GOV-01")
        assert body.source is None and body.source_ref is None


@pytest_asyncio.fixture
async def db():
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
    monkeypatch.setattr(db, "commit", db.flush)
    monkeypatch.setattr(db, "rollback", db.flush)


async def _org_with_editor(db):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"def-obj-{tag}", slug=f"def-obj-{tag}")
    db.add(org)
    await db.flush()
    user = User(google_sub=f"def-obj-{tag}", email=f"def-obj-{tag}@example.invalid", display_name="P")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="editor"))
    await db.flush()
    member = OrgMembership(user=SimpleNamespace(db_id=str(user.id), email=user.email),
                           organization_id=org.id, role="editor")
    return org, member


async def _objective(db, org, archived=False):
    objective = SecurityObjective(
        organization_id=org.id, ref=f"OBJ-{_uuid.uuid4().hex[:4]}", name="Patch on time",
        objective_type="maintain", lifecycle_status="active", source="custom",
        archived_at=datetime(2026, 1, 1) if archived else None,
    )
    db.add(objective)
    await db.flush()
    return objective


async def _raise(db, org, member, **fields):
    return await api.create_deficiency(
        org.id, DeficiencyRaise(title="Objective off track", priority="high", **fields),
        Request({"type": "http", "headers": [], "method": "POST", "path": "/"}), member, db,
    )


@requires_postgres
@pytest.mark.asyncio
class TestRaising:
    async def test_raised_from_an_objective_it_needs_no_control_and_stores_the_link(self, db, no_commit):
        org, member = await _org_with_editor(db)
        objective = await _objective(db, org)
        out = await _raise(db, org, member, source="security_objective", source_ref=str(objective.id))
        assert (out.source, out.source_ref) == ("security_objective", str(objective.id))
        assert out.scf_id is None and out.risk_code is None and out.status == "open"

    async def test_an_archived_objective_is_a_422(self, db, no_commit):
        org, member = await _org_with_editor(db)
        objective = await _objective(db, org, archived=True)
        with pytest.raises(HTTPException) as exc:
            await _raise(db, org, member, source="security_objective", source_ref=str(objective.id))
        assert exc.value.status_code == 422

    async def test_another_organisations_objective_reads_exactly_as_an_unknown_one(self, db, no_commit):
        org, member = await _org_with_editor(db)
        other, _ = await _org_with_editor(db)
        foreign = await _objective(db, other)
        details = []
        for ref in (str(foreign.id), str(_uuid.uuid4())):
            with pytest.raises(HTTPException) as exc:
                await _raise(db, org, member, source="security_objective", source_ref=ref)
            details.append((exc.value.status_code, exc.value.detail))
        assert details[0] == details[1] and details[0][0] == 422

    async def test_without_a_source_the_old_refusal_stands_byte_for_byte(self, db, no_commit):
        org, member = await _org_with_editor(db)
        with pytest.raises(HTTPException) as exc:
            await _raise(db, org, member)
        assert (exc.value.status_code, exc.value.detail) == (422, NO_SUBJECT)

    async def test_with_a_source_a_control_is_still_resolved_as_today(self, db, no_commit):
        org, member = await _org_with_editor(db)
        objective = await _objective(db, org)
        scf = await db.scalar(
            select(SCFCatalogControl.scf_id).where(SCFCatalogControl.status == "active")
            .order_by(SCFCatalogControl.scf_id).limit(1)
        )
        if scf is None:  # pragma: no cover - environment dependent
            pytest.skip("catalogue is empty")
        with pytest.raises(HTTPException) as exc:  # not in scope: refused as it is without a source
            await _raise(db, org, member, source="security_objective", source_ref=str(objective.id), scf_id=scf)
        assert (exc.value.status_code, exc.value.detail) == (422, f"{scf} is not a control this organisation has in scope")
        control = ScopedControl(organization_id=org.id, scf_id=scf, selected=True, implementation_status="not_started")
        db.add(control)
        await db.flush()
        out = await _raise(db, org, member, source="security_objective", source_ref=str(objective.id),
                           scf_id=scf.lower())
        assert (out.scf_id, out.scoped_control_id, out.source) == (scf, control.id, "security_objective")

    async def test_the_stored_row_and_its_audit_carry_the_source(self, db, no_commit):
        org, member = await _org_with_editor(db)
        objective = await _objective(db, org)
        out = await _raise(db, org, member, source="security_objective", source_ref=str(objective.id))
        row = await db.get(api.VendorActionItem, out.id)
        assert (row.source, row.source_ref, row.vendor_id) == ("security_objective", str(objective.id), None)
        audit = {r.field_name: r for r in (await db.execute(select(AuditLog).where(
            AuditLog.organization_id == org.id, AuditLog.entity_id == out.id,
        ))).scalars().all()}
        assert json.loads(audit["source"].new_value) == "security_objective"
        assert json.loads(audit["source_ref"].new_value) == str(objective.id)
        assert audit["source"].action == "create"

    async def test_one_with_no_control_or_risk_is_listed_and_counted_as_unlinked(self, db, no_commit):
        org, member = await _org_with_editor(db)
        objective = await _objective(db, org)
        out = await _raise(db, org, member, source="security_objective", source_ref=str(objective.id))

        async def listed(**filters):
            args = dict(status=None, open_only=False, scf_id=None, domain=None, risk_code=None,
                        capability_test_id=None)
            args.update(filters)
            return [d.id for d in await api.list_deficiencies(org.id, membership=member, db=db, **args)]

        assert await listed() == [out.id]
        assert await listed(open_only=True) == [out.id]
        assert await listed(status="open") == [out.id]
        summary = await api.deficiency_summary(org.id, membership=member, db=db)
        assert summary["unlinked_open"] == 1
