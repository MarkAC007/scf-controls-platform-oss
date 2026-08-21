"""Lifecycle state machine and its RBAC gate."""
import pytest

from services.doc_gen.lifecycle import (
    LIFECYCLE_STATUSES,
    VALID_TRANSITIONS,
    TransitionError,
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
