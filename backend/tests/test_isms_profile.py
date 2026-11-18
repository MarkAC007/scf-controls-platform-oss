# SPDX-License-Identifier: AGPL-3.0-only
"""ISMS profile registers: validation, tolerant reads and starter rows.

Objectives are no longer a saveable section: they are read from the
``security_objectives`` table (see ``test_security_objectives_api.py``).
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from typing import get_args

from api.isms_profile import SECTIONS, Section, _validate, serialise


class TestValidate:
    def test_rows_get_a_stable_id_and_stripped_text(self):
        (row,) = _validate("context_issues", [{"issue": "  Patch critical vulnerabilities  "}])
        assert row["issue"] == "Patch critical vulnerabilities"
        assert row["id"]
        again = _validate("context_issues", [row])
        assert again[0]["id"] == row["id"]

    def test_a_bad_row_is_a_422_naming_the_row_and_field(self):
        with pytest.raises(HTTPException) as exc:
            _validate("audit_programme", [{"area": "ok"}, {"status": "someday"}])
        assert exc.value.status_code == 422
        assert exc.value.detail.startswith("Row 2: status")

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
            "scope", "context_issues", "interested_parties",
            "communication_plan", "audit_programme",
        }

    def test_objectives_are_not_a_saveable_section(self):
        # The PUT path parameter is this Literal, so /isms-profile/objectives is a 422.
        assert set(get_args(Section)) == set(SECTIONS)
        assert "objectives" not in get_args(Section)


class TestSerialise:
    def test_no_stored_profile_reads_as_empty(self):
        out = serialise(None)
        assert out.objectives == [] and out.scope.boundaries == ""

    def test_objectives_come_from_the_rows_passed_not_the_stored_column(self):
        profile = SimpleNamespace(
            scope={}, context_issues=[], interested_parties=[],
            objectives=[{"id": "old", "objective": "Stale JSON row"}],
            communication_plan=[], audit_programme=[], updated_at=None,
        )
        assert serialise(profile).objectives == []
        legacy = [{"id": "1d227053a47e", "ref": "OBJ-01", "objective": "From the table", "measure": "",
                   "target": "", "owner": "Security", "resources": "", "due_date": None,
                   "evaluation": "", "status": "in_progress", "planned_actions": "", "type": "maintain",
                   "review_date": None, "clauses": []}]
        (row,) = serialise(profile, legacy).objectives
        assert (row.id, row.objective, row.owner, row.status) == (
            "1d227053a47e", "From the table", "Security", "in_progress")

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
