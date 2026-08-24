"""Review-workflow rules: constrained transitions and segregation of duties.

Covers ISC-75..77 of the #789 audit lane. Pure functions, no DB — the
endpoint wiring is asserted separately in ``test_attested_ksi_sql.py`` and
in the API tests.
"""
from __future__ import annotations

import os
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.review_workflow import (  # noqa: E402
    ALLOWED_TRANSITIONS,
    SOD_REFUSAL_DETAIL,
    VALID_REVIEW_STATUSES,
    reviewer_is_sole_uploader,
    transition_allowed,
    transition_error,
)


class TestTransitionTable:
    def test_every_state_has_a_row(self):
        assert set(ALLOWED_TRANSITIONS) == set(VALID_REVIEW_STATUSES)

    def test_every_target_is_a_valid_status(self):
        for targets in ALLOWED_TRANSITIONS.values():
            assert targets <= VALID_REVIEW_STATUSES

    def test_no_state_is_a_dead_end(self):
        # A state nothing can leave would strand a review permanently.
        for state, targets in ALLOWED_TRANSITIONS.items():
            assert targets - {state}, f"{state} has no way out"


class TestConstrainedTransitions:
    def test_rejected_cannot_become_approved_in_one_step(self):
        # The whole point of ISC-77: today's audit row records a rejection
        # turning into an approval with nothing in between.
        assert transition_allowed("rejected", "approved") is False

    def test_rejected_can_go_back_for_revision(self):
        assert transition_allowed("rejected", "needs_revision") is True

    def test_needs_revision_can_be_approved(self):
        # The route from rejected to approved stays open; it just has to
        # be walked rather than jumped.
        assert transition_allowed("needs_revision", "approved") is True

    def test_approved_can_be_withdrawn(self):
        assert transition_allowed("approved", "needs_revision") is True
        assert transition_allowed("approved", "rejected") is True

    def test_fresh_row_can_go_anywhere_meaningful(self):
        for target in ("approved", "rejected", "needs_revision"):
            assert transition_allowed("not_reviewed", target) is True

    @pytest.mark.parametrize("state", sorted(VALID_REVIEW_STATUSES))
    def test_same_state_is_idempotent(self, state):
        assert transition_allowed(state, state) is True

    def test_missing_current_state_is_treated_as_not_reviewed(self):
        # Rows predating the review workflow carry NULL. Refusing every
        # transition on them would strand them forever.
        assert transition_allowed(None, "approved") is True

    def test_unknown_current_state_does_not_crash(self):
        assert transition_allowed("banana", "approved") is True


class TestTransitionError:
    def test_names_both_states(self):
        message = transition_error("rejected", "approved")
        assert "rejected" in message and "approved" in message

    def test_names_the_route_that_is_open(self):
        message = transition_error("rejected", "approved")
        assert "needs_revision" in message

    def test_explains_why_for_the_edge_that_matters(self):
        message = transition_error("rejected", "approved")
        assert "one step" in message

    def test_generic_refusal_still_lists_permitted_targets(self):
        message = transition_error("approved", "not_reviewed")
        assert "needs_revision" in message and "rejected" in message


class TestSegregationOfDuties:
    def test_sole_uploader_reviewing_own_evidence_is_refused(self):
        me = uuid4()
        assert reviewer_is_sole_uploader([me], me) is True

    def test_repeated_self_uploads_are_still_sole(self):
        me = uuid4()
        assert reviewer_is_sole_uploader([me, me, me], me) is True

    def test_one_other_contributor_is_enough(self):
        me, colleague = uuid4(), uuid4()
        assert reviewer_is_sole_uploader([me, colleague], me) is False

    def test_reviewer_who_uploaded_nothing_is_independent(self):
        assert reviewer_is_sole_uploader([uuid4(), uuid4()], uuid4()) is False

    def test_empty_window_is_not_a_violation(self):
        # There is nothing to be sole owner of.
        assert reviewer_is_sole_uploader([], uuid4()) is False

    def test_deleted_uploader_does_not_vouch_for_the_reviewer(self):
        # NULL uploader (user deleted). An absent name is not an
        # independent pair of eyes, so it must not rescue the reviewer.
        me = uuid4()
        assert reviewer_is_sole_uploader([me, None], me) is True

    def test_only_null_uploaders_is_not_a_violation(self):
        assert reviewer_is_sole_uploader([None, None], uuid4()) is False


class TestRefusalMessage:
    def test_names_the_rule(self):
        assert "Segregation of duties" in SOD_REFUSAL_DETAIL

    def test_names_the_setting_that_turns_it_off(self):
        # ISC-76 — an explanatory error, not a bare 403.
        assert "require_reviewer_independence" in SOD_REFUSAL_DETAIL

    def test_offers_the_other_way_forward(self):
        assert "another member" in SOD_REFUSAL_DETAIL
