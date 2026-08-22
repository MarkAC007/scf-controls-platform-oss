"""Lifecycle state machine and its RBAC gate."""
import pytest

from services.doc_gen.lifecycle import (
    LIFECYCLE_STATUSES,
    VALID_TRANSITIONS,
    TransitionError,
    apply_lifecycle_status,
    status_label,
    available_transitions,
    can_transition,
    required_role,
    transition_label,
    transition_on_edit,
    validate_transition,
)


class TestStateMachine:
    @pytest.mark.parametrize(
        "src,dst",
        [
            ("draft", "in_review"),
            ("in_review", "approved"),
            ("in_review", "draft"),
            ("approved", "published"),
            ("approved", "in_review"),
            ("published", "in_review"),
        ],
    )
    def test_permitted_transitions(self, src, dst):
        assert can_transition(src, dst) is True

    @pytest.mark.parametrize(
        "src,dst",
        [
            ("draft", "approved"),
            ("draft", "published"),
            ("in_review", "published"),
            ("published", "draft"),
            ("published", "approved"),
            ("approved", "draft"),
        ],
    )
    def test_refused_transitions(self, src, dst):
        assert can_transition(src, dst) is False

    def test_every_status_has_an_entry(self):
        assert set(VALID_TRANSITIONS) == set(LIFECYCLE_STATUSES)

    def test_every_target_is_a_known_status(self):
        for targets in VALID_TRANSITIONS.values():
            for target in targets:
                assert target in LIFECYCLE_STATUSES


class TestRbacGate:
    def test_approval_requires_admin(self):
        assert required_role("in_review", "approved") == "admin"
        with pytest.raises(TransitionError, match="admin role"):
            validate_transition("in_review", "approved", "editor")
        validate_transition("in_review", "approved", "admin")

    def test_publish_requires_admin(self):
        with pytest.raises(TransitionError, match="admin role"):
            validate_transition("approved", "published", "editor")
        validate_transition("approved", "published", "admin")

    def test_submit_for_review_needs_only_editor(self):
        validate_transition("draft", "in_review", "editor")

    def test_viewer_can_do_nothing(self):
        for src, targets in VALID_TRANSITIONS.items():
            for dst in targets:
                with pytest.raises(TransitionError):
                    validate_transition(src, dst, "viewer")

    def test_unknown_transition_defaults_to_admin(self):
        assert required_role("draft", "published") == "admin"


class TestValidation:
    def test_illegal_move_is_refused_with_the_legal_set(self):
        with pytest.raises(TransitionError, match="in_review"):
            validate_transition("draft", "published", "admin")

    def test_unknown_status_is_refused(self):
        with pytest.raises(TransitionError, match="Unknown document status"):
            validate_transition("draft", "archived", "admin")

    def test_no_op_transition_is_refused(self):
        with pytest.raises(TransitionError, match="already"):
            validate_transition("draft", "draft", "admin")


class TestAvailableTransitions:
    def test_editor_sees_only_what_they_may_do(self):
        options = available_transitions("in_review", "editor")
        assert [o["to_status"] for o in options] == ["draft"]

    def test_admin_sees_both(self):
        options = available_transitions("in_review", "admin")
        assert sorted(o["to_status"] for o in options) == ["approved", "draft"]

    def test_options_carry_ui_labels(self):
        options = available_transitions("draft", "editor")
        assert options[0]["label"] == "Submit for Review"

    def test_viewer_sees_nothing(self):
        assert available_transitions("draft", "viewer") == []


class TestLabels:
    def test_known_labels(self):
        assert transition_label("in_review", "approved") == "Approve Document"
        assert transition_label("published", "in_review") == "Request Re-review"

    def test_unknown_label_falls_back_readably(self):
        assert transition_label("a", "b") == "a to b"


class TestTransitionOnEdit:
    def test_editing_an_approved_document_returns_it_to_review(self):
        # An edit must not launder itself through a stale sign-off.
        assert transition_on_edit("approved") == "in_review"

    def test_editing_a_published_document_returns_it_to_review(self):
        assert transition_on_edit("published") == "in_review"

    def test_editing_a_draft_changes_nothing(self):
        assert transition_on_edit("draft") is None
        assert transition_on_edit("in_review") is None


# ---------------------------------------------------------------------------
# The Document Control status cell
#
# The generated document carried the literal "Draft" forever: the prompt writes
# it once and nothing afterwards touched the prose, so a policy that had been
# reviewed, approved and published still introduced itself as a draft to the
# auditor reading it. ``apply_lifecycle_status`` owns that one cell.
# ---------------------------------------------------------------------------

DOC_CONTROL = """# Access Control Policy

## 1. Document Control

| Field | Value |
| --- | --- |
| **Document ID** | POL-AC-001 |
| **Version** | 2.0 |
| **Status** | Draft |
| **Owner** | CISO function |
| **Next Review** | 2027-01-01 |

## 2. Purpose

This policy states the organisation's position.
"""

#: Tier 1: no Document Control block, and a per-control Status *column* whose
#: rows must survive untouched -- a pattern matching "Status" anywhere in a row
#: would rewrite a hundred of them into nonsense.
SOA = """# Statement of Applicability

| SCF ID | Control | Status | Owner |
| --- | --- | --- | --- |
| IAC-01 | Identity Management | Implemented | IT |
| IAC-02 | Access Enforcement | In Progress | IT |
"""


class TestStatusLabel:
    @pytest.mark.parametrize(
        "status,expected",
        [
            ("draft", "Draft"),
            ("in_review", "In Review"),
            ("approved", "Approved"),
            ("published", "Published"),
        ],
    )
    def test_every_status_has_a_reader_facing_label(self, status, expected):
        assert status_label(status) == expected

    def test_unknown_status_degrades_rather_than_raising(self):
        # Called on the export path: a status the map has not caught up with
        # must not take the document down.
        assert status_label("awaiting_legal") == "Awaiting Legal"

    def test_absent_status_is_empty(self):
        assert status_label(None) == ""
        assert status_label("") == ""


class TestApplyLifecycleStatus:
    def test_writes_the_live_status_into_the_control_table(self):
        out = apply_lifecycle_status(DOC_CONTROL, "published")
        assert "| **Status** | Published |" in out
        assert "Draft" not in out

    def test_renders_the_label_not_the_machine_state(self):
        out = apply_lifecycle_status(DOC_CONTROL, "in_review")
        assert "| **Status** | In Review |" in out
        assert "in_review" not in out

    def test_changes_exactly_one_line(self):
        out = apply_lifecycle_status(DOC_CONTROL, "approved")
        before, after = DOC_CONTROL.split("\n"), out.split("\n")
        assert len(before) == len(after)
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        assert len(differing) == 1
        assert "Status" in before[differing[0]]

    def test_leaves_every_other_control_row_byte_identical(self):
        out = apply_lifecycle_status(DOC_CONTROL, "approved")
        for row in ("| **Document ID** | POL-AC-001 |",
                    "| **Version** | 2.0 |",
                    "| **Owner** | CISO function |",
                    "| **Next Review** | 2027-01-01 |"):
            assert row in out

    def test_is_idempotent(self):
        once = apply_lifecycle_status(DOC_CONTROL, "approved")
        assert apply_lifecycle_status(once, "approved") == once

    def test_round_trips_back_to_draft(self):
        # "Return to Draft" is a real transition; the cell must move both ways.
        published = apply_lifecycle_status(DOC_CONTROL, "published")
        assert apply_lifecycle_status(published, "draft") == DOC_CONTROL

    def test_tier_one_document_is_untouched(self):
        # No Document Control block at all -- a no-op is the complete and
        # correct behaviour, not a defensive fallback.
        assert apply_lifecycle_status(SOA, "published") == SOA

    def test_never_rewrites_a_per_control_status_column(self):
        out = apply_lifecycle_status(SOA, "approved")
        assert "| IAC-01 | Identity Management | Implemented | IT |" in out
        assert "| IAC-02 | Access Enforcement | In Progress | IT |" in out

    def test_a_control_status_column_after_a_control_block_is_still_safe(self):
        combined = DOC_CONTROL + "\n" + SOA
        out = apply_lifecycle_status(combined, "approved")
        assert "| **Status** | Approved |" in out
        assert "| IAC-01 | Identity Management | Implemented | IT |" in out

    def test_a_deleted_status_row_is_never_re_added(self):
        # Its absence is a choice the document's owner made. Silently reversing
        # it would be the overreach the generator has just stopped committing.
        stripped = "\n".join(
            line for line in DOC_CONTROL.split("\n")
            if "**Status**" not in line
        )
        assert apply_lifecycle_status(stripped, "published") == stripped

    def test_stops_at_the_next_heading(self):
        # A "Status" row belonging to some later section is not this document's
        # lifecycle status.
        doc = (
            "## Document Control\n\n| Field | Value |\n| --- | --- |\n"
            "| **Owner** | CISO |\n\n## Appendix\n\n| Status | Notes |\n"
            "| --- | --- |\n"
        )
        assert apply_lifecycle_status(doc, "published") == doc

    def test_matches_a_numbered_heading(self):
        assert "Approved" in apply_lifecycle_status(DOC_CONTROL, "approved")

    def test_matches_an_unbolded_status_cell(self):
        doc = DOC_CONTROL.replace("| **Status** |", "| Status |")
        assert "| Status | Approved |" in apply_lifecycle_status(doc, "approved")

    @pytest.mark.parametrize("status", [None, ""])
    def test_no_status_is_a_no_op(self, status):
        assert apply_lifecycle_status(DOC_CONTROL, status) == DOC_CONTROL

    def test_empty_markdown_is_a_no_op(self):
        assert apply_lifecycle_status("", "published") == ""
