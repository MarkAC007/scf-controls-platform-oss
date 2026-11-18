# SPDX-License-Identifier: AGPL-3.0-only
"""Clause 9.2: a structured planned date on each internal audit.

These tests pin:

* ``planned_date`` on an audit programme row: optional, ``YYYY-MM-DD``, a
  real calendar date, blank read as none; a row saved before the field
  existed still validates; a bad date is a 422 naming the row and field;
* a save through the PUT route returns the date, and a GET reads it back;
* the Internal Audit Programme document prints the date when one is set,
  else the period text, and setting a date changes the inputs digest that
  drives regeneration.
"""
import copy
import uuid as _uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from api import isms_profile as profile_api
from api.isms_profile import AuditEntry, _validate
from auth import OrgMembership
from models import Organization, OrganizationMember, User
from services.doc_gen import iso_core
from services.doc_gen.context import OrganisationContext

from test_journey_service import DATABASE_URL, requires_postgres  # noqa: F401

OLD_ROW = {"id": "a1", "area": "Clause 4 and Incident Response", "criteria": "Clause 4", "planned_period": "2027 Q1",
           "auditor": "External", "status": "planned", "findings": ""}


class TestTheField:
    def test_an_old_row_without_a_planned_date_still_validates(self):
        (row,) = _validate("audit_programme", [OLD_ROW])
        assert row["planned_date"] is None and row["planned_period"] == "2027 Q1"

    def test_a_planned_date_is_kept(self):
        (row,) = _validate("audit_programme", [{**OLD_ROW, "planned_date": "2027-02-15"}])
        assert row["planned_date"] == "2027-02-15" and row["planned_period"] == "2027 Q1"

    @pytest.mark.parametrize("blank", ["", "   ", None])
    def test_a_blank_planned_date_reads_as_none(self, blank):
        assert AuditEntry(planned_date=blank).planned_date is None

    @pytest.mark.parametrize("bad", ["2027-2-15", "15/02/2027", "2027-02-30", "2027-13-01", "next spring"])
    def test_a_bad_date_is_a_422_naming_the_row_and_field(self, bad):
        with pytest.raises(HTTPException) as exc:
            _validate("audit_programme", [OLD_ROW, {**OLD_ROW, "id": "a2", "planned_date": bad}])
        assert exc.value.status_code == 422
        assert exc.value.detail.startswith("Row 2: planned_date")


def _ctx(audits):
    return OrganisationContext(
        organization_id="org", name="Acme Ltd", generated_at="2026-11-18T12:00:00+00:00",
        domains=[], all_controls=[], total_scoped_controls=0, total_domains=0,
        isms_profile={"audit_programme": audits},
    )


class TestTheDocument:
    def test_the_programme_prints_the_planned_date_when_set_else_the_period(self):
        dated = {**OLD_ROW, "planned_date": "2027-02-15"}
        undated = {**OLD_ROW, "id": "a2", "area": "Access Control", "planned_period": "2027 Q3"}
        out = iso_core.render_internal_audit_programme(_ctx([dated, undated]))
        assert "| Clause 4 and Incident Response | 2027-02-15 | External |" in out
        assert "| Access Control | 2027 Q3 | External |" in out
        assert "| Clause 4 and Incident Response | 2027 Q1 |" not in out

    def test_an_old_row_prints_as_before(self):
        out = iso_core.render_internal_audit_programme(_ctx([OLD_ROW]))
        assert "| Clause 4 and Incident Response | 2027 Q1 | External |" in out

    def test_setting_a_date_changes_the_inputs_digest(self):
        before = iso_core.inputs_digest(_ctx([OLD_ROW]))
        dated = copy.deepcopy(OLD_ROW)
        dated["planned_date"] = "2027-02-15"
        assert iso_core.inputs_digest(_ctx([dated])) != before


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


@requires_postgres
@pytest.mark.asyncio
async def test_a_saved_planned_date_is_returned_and_read_back(db, no_commit):
    tag = _uuid.uuid4().hex[:10]
    org = Organization(name=f"isms-audit-{tag}", slug=f"isms-audit-{tag}")
    db.add(org)
    await db.flush()
    user = User(google_sub=f"isms-audit-{tag}", email=f"isms-audit-{tag}@example.invalid", display_name="P")
    db.add(user)
    await db.flush()
    db.add(OrganizationMember(organization_id=org.id, user_id=user.id, role="editor"))
    await db.flush()
    member = OrgMembership(user=SimpleNamespace(db_id=str(user.id), email=user.email),
                           organization_id=org.id, role="editor")

    saved = await profile_api.update_isms_profile_section(
        org_id=org.id, section="audit_programme",
        payload=profile_api.SectionUpdate(data=[{**OLD_ROW, "planned_date": "2027-02-15"}, OLD_ROW | {"id": "a2"}]),
        request=Request({"type": "http", "headers": [], "method": "PUT", "path": "/"}),
        membership=member, db=db,
    )
    assert [a.planned_date for a in saved.audit_programme] == ["2027-02-15", None]
    read = await profile_api.get_isms_profile(org_id=org.id, membership=member, db=db)
    assert [a.planned_date for a in read.audit_programme] == ["2027-02-15", None]
