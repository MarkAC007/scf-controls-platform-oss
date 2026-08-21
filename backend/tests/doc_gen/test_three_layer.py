"""Three-layer merge -- the engine the whole feature rests on.

One test per cell of the decision matrix, plus the two structural outcomes.
If any of these go red, regeneration is destroying customer edits.
"""
import pytest

from services.doc_gen.section_parser import parse_markdown_sections, to_section_rows
from services.doc_gen.three_layer import (
    CONFLICT_MARKER,
    NEW_SECTION_MARKER,
    PENDING_RETIREMENT_MARKER,
    STATUS_CONFLICT,
    STATUS_HUMAN_PRESERVED,
    STATUS_NEW,
    STATUS_PENDING_RETIREMENT,
    STATUS_UNCHANGED,
    STATUS_UPDATED,
    build_merged_document,
    collect_human_edits,
    detect_human_edits,
    resolve_section,
    strip_markers,
    three_way_merge,
)

V1 = "## Purpose\nOriginal purpose.\n\n## Scope\nOriginal scope.\n"


def first_run(generated=V1):
    """Simulate a first generation and return (merged_content, section_rows)."""
    result = three_way_merge(generated, None, None)
    return result.merged_content, result.sections


def status_of(result, section_id):
    return next(e.status for e in result.manifest if e.section_id == section_id)


def row_for(rows, section_id):
    return next(r for r in rows if r["section_id"] == section_id)


class TestFirstGeneration:
    def test_every_section_is_new(self):
        result = three_way_merge(V1, None, None)
        assert {e.status for e in result.manifest} == {STATUS_NEW}

    def test_content_is_returned_untouched(self):
        assert three_way_merge(V1, None, None).merged_content == V1

    def test_no_conflicts_are_possible(self):
        assert three_way_merge(V1, None, None).conflict_count == 0


class TestDecisionMatrix:
    """The four-cell core: did the generated layer move, did the human layer?"""

    def test_neither_changed_is_unchanged(self):
        merged, rows = first_run()
        result = three_way_merge(V1, merged, rows)
        assert status_of(result, "purpose") == STATUS_UNCHANGED
        assert status_of(result, "scope") == STATUS_UNCHANGED

    def test_only_human_changed_is_human_preserved(self):
        merged, rows = first_run()
        edits = {"scope": "My hand-written scope."}
        result = three_way_merge(V1, merged, rows, human_edits=edits)
        assert status_of(result, "scope") == STATUS_HUMAN_PRESERVED
        assert "My hand-written scope." in result.merged_content
        assert "Original scope." not in result.merged_content

    def test_only_generated_changed_is_updated(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope\nRegenerated scope.\n"
        result = three_way_merge(v2, merged, rows)
        assert status_of(result, "scope") == STATUS_UPDATED
        assert "Regenerated scope." in result.merged_content

    def test_both_changed_is_conflict_and_human_text_survives(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope\nRegenerated scope.\n"
        edits = {"scope": "My hand-written scope."}
        result = three_way_merge(v2, merged, rows, human_edits=edits)
        assert status_of(result, "scope") == STATUS_CONFLICT
        # On conflict the human wins in the operative document.
        assert "My hand-written scope." in result.merged_content
        assert "Regenerated scope." not in result.merged_content
        assert CONFLICT_MARKER in result.merged_content

    def test_untouched_sections_are_unaffected_by_a_conflict_elsewhere(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope\nRegenerated scope.\n"
        result = three_way_merge(v2, merged, rows, human_edits={"scope": "mine"})
        assert status_of(result, "purpose") == STATUS_UNCHANGED


class TestStructuralOutcomes:
    def test_section_absent_before_is_new(self):
        merged, rows = first_run()
        v2 = V1 + "\n## Service Accounts\nNewly scoped.\n"
        result = three_way_merge(v2, merged, rows)
        assert status_of(result, "service-accounts") == STATUS_NEW
        assert NEW_SECTION_MARKER in result.merged_content

    def test_section_dropped_from_generation_is_pending_retirement(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n"
        result = three_way_merge(v2, merged, rows)
        assert status_of(result, "scope") == STATUS_PENDING_RETIREMENT

    def test_retiring_section_content_is_never_deleted(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n"
        result = three_way_merge(v2, merged, rows)
        # A policy clause vanishing silently is an audit finding.
        assert "Original scope." in result.merged_content
        assert PENDING_RETIREMENT_MARKER in result.merged_content

    def test_a_human_edit_on_a_retiring_section_is_also_preserved(self):
        merged, rows = first_run()
        rows = [
            {**r, "human_edited": True, "edited_content": "kept"}
            if r["section_id"] == "scope"
            else r
            for r in rows
        ]
        merged_with_edit = build_merged_document(V1, {"scope": "kept"})
        result = three_way_merge("## Purpose\nOriginal purpose.\n", merged_with_edit, rows)
        retired = row_for(result.sections, "scope")
        assert retired["status"] == STATUS_PENDING_RETIREMENT
        assert retired["human_edited"] is True


class TestPreservationAcrossRegeneration:
    def test_seven_edits_survive_and_one_conflict_asks(self):
        """The ratio that is the product: most edits survive silently."""
        v1 = "".join(f"## S{i}\nGenerated {i}.\n\n" for i in range(8))
        merged, rows = first_run(v1)
        edits = {f"s{i}": f"Human {i}." for i in range(8)}
        # Only section 3 is regenerated differently.
        v2 = "".join(
            f"## S{i}\n{'Regenerated 3.' if i == 3 else f'Generated {i}.'}\n\n"
            for i in range(8)
        )
        result = three_way_merge(v2, merged, rows, human_edits=edits)
        assert result.counts[STATUS_HUMAN_PRESERVED] == 7
        assert result.counts[STATUS_CONFLICT] == 1
        for i in range(8):
            assert f"Human {i}." in result.merged_content

    def test_headings_come_from_the_new_generation(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope of Application\nOriginal scope.\n"
        result = three_way_merge(v2, merged, rows, human_edits={"scope": "mine"})
        # The old heading is gone; the section is treated as new + retiring.
        assert "## Scope of Application" in result.merged_content


class TestSectionRows:
    def test_rows_record_the_merge_status(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope\nRegenerated scope.\n"
        result = three_way_merge(v2, merged, rows, human_edits={"scope": "mine"})
        assert row_for(result.sections, "scope")["status"] == STATUS_CONFLICT
        assert row_for(result.sections, "scope")["human_edited"] is True

    def test_last_generated_hash_tracks_the_generated_layer_not_the_merged(self):
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope\nRegenerated scope.\n"
        result = three_way_merge(v2, merged, rows, human_edits={"scope": "mine"})
        scope = row_for(result.sections, "scope")
        generated_scope = next(
            s for s in parse_markdown_sections(v2) if s.section_id == "scope"
        )
        assert scope["last_generated_hash"] == generated_scope.content_hash
        # ...and it is NOT the hash of what is in the merged document.
        assert scope["content_hash"] != scope["last_generated_hash"]

    def test_a_second_regeneration_does_not_reopen_a_settled_section(self):
        """Regression: the merged doc carries markers; hashes must account for them."""
        merged, rows = first_run()
        v2 = "## Purpose\nOriginal purpose.\n\n## Scope\nRegenerated scope.\n"
        first = three_way_merge(v2, merged, rows, human_edits={"scope": "mine"})
        second = three_way_merge(
            v2, first.merged_content, first.sections, human_edits={"scope": "mine"}
        )
        # Generated layer did not move between run 2 and run 3.
        assert status_of(second, "scope") == STATUS_HUMAN_PRESERVED
        assert status_of(second, "purpose") == STATUS_UNCHANGED


class TestHumanEditHelpers:
    def test_collect_reads_edits_off_stored_rows(self):
        rows = [
            {"section_id": "a", "human_edited": True, "edited_content": "x"},
            {"section_id": "b", "human_edited": False, "edited_content": None},
        ]
        assert collect_human_edits(rows) == {"a": "x"}

    def test_detect_finds_out_of_band_edits(self):
        merged, rows = first_run()
        tampered = merged.replace("Original scope.", "Edited outside the app.")
        assert detect_human_edits(tampered, rows) == {"scope": "Edited outside the app."}

    def test_detect_returns_nothing_when_untouched(self):
        merged, rows = first_run()
        assert detect_human_edits(merged, rows) == {}

    def test_strip_markers_removes_merge_comments(self):
        assert strip_markers(f"{CONFLICT_MARKER}\n\nreal text") == "real text"


class TestBuildMergedDocument:
    def test_no_edits_returns_generated_unchanged(self):
        assert build_merged_document(V1, {}) == V1

    def test_edits_replace_only_the_section_body(self):
        out = build_merged_document(V1, {"scope": "replaced"})
        assert "replaced" in out
        assert "Original purpose." in out
        assert "Original scope." not in out


class TestResolveSection:
    def test_keep_mine_settles_as_human_preserved(self):
        row = {"section_id": "s", "status": STATUS_CONFLICT, "human_edited": True,
               "edited_content": "mine", "content_hash": "a", "last_generated_hash": "b"}
        assert resolve_section(row, "keep_mine")["status"] == STATUS_HUMAN_PRESERVED

    def test_take_generated_drops_the_human_layer(self):
        row = {"section_id": "s", "status": STATUS_CONFLICT, "human_edited": True,
               "edited_content": "mine", "content_hash": "a", "last_generated_hash": "b"}
        out = resolve_section(row, "take_generated", generated_content="theirs")
        assert out["status"] == STATUS_UPDATED
        assert out["human_edited"] is False
        assert out["edited_content"] is None
        assert out["content_hash"] == "b"

    def test_take_generated_without_content_is_refused(self):
        with pytest.raises(ValueError):
            resolve_section({"section_id": "s"}, "take_generated")

    def test_unknown_choice_is_refused(self):
        with pytest.raises(ValueError):
            resolve_section({"section_id": "s"}, "whatever")


class TestNestedSectionIdentity:
    """Regeneration must not lose a nested section's identity.

    Every other fixture in this file is flat -- ``## Purpose`` / ``## Scope`` --
    so a re-parse of the merged document reproduces each ``section_id``
    verbatim and the identity question never arises. Real generated policies
    are nested, and that is where the merge and the re-parse disagree.

    The shape that breaks: a nested section retires while **its parent
    survives**, and another top-level heading follows the parent. The retiree
    is appended at the end of the document at its original depth, so it
    re-parses under the *last* heading rather than its own -- ``roles.scope``
    becomes ``review.scope``. Retiring a whole branch does NOT reproduce this,
    because parent and child are appended together and the tree is preserved.
    """

    V1 = (
        "## Purpose\nWhy.\n\n"
        "## Roles\nWho.\n\n"
        "### Scope\nScope text.\n\n"
        "## Review\nAnnual.\n"
    )
    # "### Scope" has left scope; "## Roles" survives and "## Review" follows it.
    V2 = "## Purpose\nWhy.\n\n## Roles\nWho.\n\n## Review\nAnnual.\n"

    def edited_first_run(self):
        """First generation with a human edit on the section about to retire."""
        rows = three_way_merge(self.V1, None, None).sections
        rows = [
            {**r, "human_edited": True, "edited_content": "Hand-written scope."}
            if r["section_id"] == "roles.scope"
            else r
            for r in rows
        ]
        merged = build_merged_document(self.V1, {"roles.scope": "Hand-written scope."})
        return merged, rows

    def test_the_manifest_already_knows_the_right_identity(self):
        """Guards the premise: the merge is right, the persisted rows are not."""
        merged, rows = first_run(self.V1)
        result = three_way_merge(self.V2, merged, rows)
        assert status_of(result, "roles.scope") == STATUS_PENDING_RETIREMENT

    def test_retired_nested_section_keeps_its_stored_id(self):
        merged, rows = first_run(self.V1)
        result = three_way_merge(self.V2, merged, rows)
        ids = {r["section_id"] for r in result.sections}
        assert "roles.scope" in ids, f"identity lost; rows carry {sorted(ids)}"
        assert "review.scope" not in ids, "section was re-parented by the re-parse"

    def test_retired_nested_section_persists_its_status(self):
        merged, rows = first_run(self.V1)
        result = three_way_merge(self.V2, merged, rows)
        assert row_for(result.sections, "roles.scope")["status"] == (
            STATUS_PENDING_RETIREMENT
        )

    def test_human_edit_on_a_retired_nested_section_survives(self):
        merged, rows = self.edited_first_run()
        result = three_way_merge(self.V2, merged, rows)
        retired = row_for(result.sections, "roles.scope")
        assert retired["human_edited"] is True
        assert retired["edited_content"] == "Hand-written scope."

    def test_retired_nested_section_keeps_its_generated_hash(self):
        merged, rows = first_run(self.V1)
        before = row_for(rows, "roles.scope")["last_generated_hash"]
        result = three_way_merge(self.V2, merged, rows)
        assert row_for(result.sections, "roles.scope")["last_generated_hash"] == before

    def test_identity_survives_a_second_regeneration(self):
        merged, rows = self.edited_first_run()
        first = three_way_merge(self.V2, merged, rows)
        second = three_way_merge(self.V2, first.merged_content, first.sections)
        assert "roles.scope" in {r["section_id"] for r in second.sections}
        assert row_for(second.sections, "roles.scope")["human_edited"] is True

    def test_retirement_marker_is_not_duplicated_on_every_regeneration(self):
        """The retirement branch re-renders content that already holds a marker.

        Left alone it appends another one each run, so a document regenerated
        five times carries five copies of the same comment.
        """
        merged, rows = first_run(self.V1)
        result = three_way_merge(self.V2, merged, rows)
        for _ in range(2):
            result = three_way_merge(self.V2, result.merged_content, result.sections)
        assert result.merged_content.count(PENDING_RETIREMENT_MARKER) == 1

    def test_the_retired_clause_text_is_never_lost(self):
        merged, rows = first_run(self.V1)
        result = three_way_merge(self.V2, merged, rows)
        for _ in range(2):
            result = three_way_merge(self.V2, result.merged_content, result.sections)
        assert "Scope text." in result.merged_content
