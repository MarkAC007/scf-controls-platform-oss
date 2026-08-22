"""What a regeneration records about itself.

Two records, two questions. The Change History table *inside* the document says
what moved in the organisation's inputs, and has to be known before the model is
called because the model writes the row. The stored ``change_summary`` says what
the merge did to this document, and is only knowable after the merge. Neither
substitutes for the other, and until now both said nothing: every regeneration
row read "Revised — updated control data", and the version index recorded no
summary at all.
"""
from services.doc_gen.pipeline import _change_note, _version_change_summary
from services.doc_gen.tier2 import (
    GENERIC_REVISION_NOTE,
    _change_history_instruction,
)

EXISTING_ROWS = "| 1.0 | 2026-01-04 | CISO function | Initial issue |"


class TestChangeHistoryInstruction:
    def test_first_generation_still_reads_initial_issue(self):
        out = _change_history_instruction("", "1.0", "2026-08-22")
        assert "| 1.0 | 2026-08-22 | CISO function | Initial issue |" in out

    def test_existing_rows_are_carried_forward_verbatim(self):
        out = _change_history_instruction(
            EXISTING_ROWS, "2.0", "2026-08-22", "controls 3 added")
        assert EXISTING_ROWS in out
        assert "VERBATIM" in out

    def test_the_new_row_says_what_actually_changed(self):
        out = _change_history_instruction(
            EXISTING_ROWS, "2.0", "2026-08-22", "controls 3 added, 1 removed")
        assert (
            "| 2.0 | 2026-08-22 | CISO function | "
            "Revised — controls 3 added, 1 removed |"
        ) in out

    def test_it_falls_back_rather_than_asserting_an_empty_change(self):
        # Better a generic row than one claiming a delta nobody could name.
        out = _change_history_instruction(EXISTING_ROWS, "2.0", "2026-08-22", "")
        assert GENERIC_REVISION_NOTE in out

    def test_the_note_never_leaks_into_a_first_generation_row(self):
        out = _change_history_instruction("", "1.0", "2026-08-22", "controls 3 added")
        assert "controls 3 added" not in out
        assert "Initial issue" in out


class TestChangeNote:
    def test_joins_every_named_reason(self):
        assert _change_note(
            ["controls 3 added", "catalog 2025.2 → 2025.3"]
        ) == "controls 3 added; catalog 2025.2 → 2025.3"

    def test_first_generation_is_not_a_revision_note(self):
        # ``describe_change`` says "first generation" when there is nothing to
        # compare against, which is not a description of a change.
        assert _change_note(["first generation"]) == ""

    def test_nothing_nameable_yields_nothing(self):
        assert _change_note([]) == ""
        assert _change_note(["", None]) == ""


class TestVersionChangeSummary:
    def test_records_the_merge_tallies_and_the_control_count(self):
        out = _version_change_summary({"updated": 2, "unchanged": 9}, 41, False)
        assert out == {
            "counts": {"updated": 2, "unchanged": 9},
            "control_count": 41,
            "initial": False,
        }

    def test_zero_tallies_are_dropped(self):
        # "0 conflicts" is not news, and storing it invites a renderer to say so.
        out = _version_change_summary({"updated": 1, "conflict": 0}, 41, False)
        assert out["counts"] == {"updated": 1}

    def test_a_first_generation_is_marked_as_one(self):
        out = _version_change_summary({"new": 12}, 34, True)
        assert out["initial"] is True

    def test_absent_counts_do_not_raise(self):
        assert _version_change_summary({}, 0, False)["counts"] == {}
