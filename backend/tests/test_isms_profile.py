"""ISMS profile registers: validation, tolerant reads and starter rows."""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.isms_profile import SECTIONS, _validate, serialise


class TestValidate:
    def test_rows_get_a_stable_id_and_stripped_text(self):
        (row,) = _validate("objectives", [{"objective": "  Patch critical vulnerabilities  "}])
        assert row["objective"] == "Patch critical vulnerabilities"
        assert row["id"]
        again = _validate("objectives", [row])
        assert again[0]["id"] == row["id"]

    def test_a_bad_row_is_a_422_naming_the_row_and_field(self):
        with pytest.raises(HTTPException) as exc:
            _validate("objectives", [{"objective": "ok"}, {"due_date": "31/01/2027"}])
        assert exc.value.status_code == 422
        assert exc.value.detail.startswith("Row 2: due_date")

    def test_a_register_must_be_a_list(self):
        with pytest.raises(HTTPException) as exc:
            _validate("context_issues", {"issue": "x"})
        assert exc.value.status_code == 422

    def test_unknown_categories_are_refused(self):
        with pytest.raises(HTTPException):
            _validate("context_issues", [{"category": "astrological"}])

    def test_the_scope_is_an_object_with_defaults(self):
        scope = _validate("scope", {"boundaries": "UK operations"})
        assert scope["boundaries"] == "UK operations"
        assert scope["locations"] == [] and scope["exclusions"] == []

    def test_every_section_has_a_schema(self):
        assert set(SECTIONS) == {
            "scope", "context_issues", "interested_parties", "objectives",
            "communication_plan", "audit_programme",
        }


class TestSerialise:
    def test_no_stored_profile_reads_as_empty(self):
        out = serialise(None)
        assert out.objectives == [] and out.scope.boundaries == ""

    def test_one_unreadable_row_does_not_hide_the_others(self):
        profile = SimpleNamespace(
            scope={}, context_issues=[{"issue": "ok"}, {"kind": "sideways"}],
            interested_parties=[], objectives=[], communication_plan=[],
            audit_programme=[], updated_at=None,
        )
        out = serialise(profile)
        assert [i.issue for i in out.context_issues] == ["ok"]


@pytest.mark.asyncio
async def test_starter_rows_are_editable_drafts_not_saved():
    from api.isms_profile import isms_profile_starters

    rows = await isms_profile_starters(org_id=None, section="communication_plan", membership=None)
    assert len(rows) >= 5
    assert all(r["id"] and r["what"] for r in rows)
    parties = await isms_profile_starters(org_id=None, section="interested_parties", membership=None)
    assert any(p["party"].startswith("Regulators") for p in parties)
