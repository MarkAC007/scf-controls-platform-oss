"""Pure tests for the engagement evidence review transition table.

No database. services.engagement_evidence_review.validate_transition is the
only place the table is decided; allowed_targets and note_required must agree
with it.
"""
from __future__ import annotations

import itertools
import os
import sys
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.engagement_evidence_review import (  # noqa: E402
    ACTOR_AUDITOR,
    ACTOR_ORG,
    STATUSES,
    ReviewActor,
    TransitionError,
    allowed_targets,
    note_required,
    validate_transition,
)


OPEN, IN_PROGRESS, ACTION_REQUIRED, SUBMITTED, ACCEPTED = (
    "open", "in_progress", "action_required", "submitted", "accepted",
)

AUDITOR_EDGES = [
    (OPEN, IN_PROGRESS),
    (IN_PROGRESS, ACTION_REQUIRED),
    (IN_PROGRESS, ACCEPTED),
    (SUBMITTED, IN_PROGRESS),
    (SUBMITTED, ACTION_REQUIRED),
    (SUBMITTED, ACCEPTED),
    (ACCEPTED, IN_PROGRESS),
]
ORG_EDGES = [(ACTION_REQUIRED, SUBMITTED)]
ALL_EDGES = AUDITOR_EDGES + ORG_EDGES

NOTE_EDGES = {
    (IN_PROGRESS, ACTION_REQUIRED),
    (ACTION_REQUIRED, SUBMITTED),
    (SUBMITTED, ACTION_REQUIRED),
    (ACCEPTED, IN_PROGRESS),
}


def external_auditor(**kw):
    return ReviewActor(user_id=uuid4(), is_auditor=True, auditor_kind="external", org_role=None, **kw)


def internal_auditor(**kw):
    kw.setdefault("org_role", "viewer")
    return ReviewActor(user_id=uuid4(), is_auditor=True, auditor_kind="internal", **kw)


def org_member(role="editor"):
    return ReviewActor(user_id=uuid4(), is_auditor=False, auditor_kind=None, org_role=role)


def _submit_kwargs():
    return {"note": "Uploaded the Q3 export", "linked_artifact_count": 1}


def _kwargs_for(edge):
    """Arguments that satisfy every note/artifact rule for the edge."""
    if edge == (ACTION_REQUIRED, SUBMITTED):
        return _submit_kwargs()
    if edge in NOTE_EDGES:
        return {"note": "reason"}
    return {}


def test_statuses_are_the_five_in_board_order():
    assert STATUSES == (OPEN, IN_PROGRESS, ACTION_REQUIRED, SUBMITTED, ACCEPTED)


# ---------------------------------------------------------------------------
# One test per edge, allowed for the right actor
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("edge", AUDITOR_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_auditor_edge_allowed_for_external_auditor(edge):
    assert validate_transition(*edge, external_auditor(), **_kwargs_for(edge)) == ACTOR_AUDITOR


@pytest.mark.parametrize("edge", AUDITOR_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_auditor_edge_allowed_for_internal_auditor(edge):
    assert validate_transition(*edge, internal_auditor(), **_kwargs_for(edge)) == ACTOR_AUDITOR


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_org_edge_allowed_for_org_editor_or_admin(role):
    edge = (ACTION_REQUIRED, SUBMITTED)
    assert validate_transition(*edge, org_member(role), **_submit_kwargs()) == ACTOR_ORG


def test_internal_auditor_who_is_org_editor_can_submit_as_org():
    actor = internal_auditor(org_role="editor")
    assert validate_transition(ACTION_REQUIRED, SUBMITTED, actor, **_submit_kwargs()) == ACTOR_ORG


# ---------------------------------------------------------------------------
# Wrong side -> 403
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["viewer", "editor", "admin", None])
@pytest.mark.parametrize("edge", AUDITOR_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_auditor_edge_refused_for_org_only_actor(edge, role):
    actor = ReviewActor(user_id=uuid4(), is_auditor=False, auditor_kind=None, org_role=role)
    with pytest.raises(TransitionError) as exc:
        validate_transition(*edge, actor, **_kwargs_for(edge))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "actor",
    [external_auditor(), internal_auditor(org_role="viewer"), internal_auditor(org_role=None)],
    ids=["external", "internal-viewer", "internal-nonmember"],
)
def test_org_edge_refused_for_auditor_who_is_not_org_editor(actor):
    with pytest.raises(TransitionError) as exc:
        validate_transition(ACTION_REQUIRED, SUBMITTED, actor, **_submit_kwargs())
    assert exc.value.status_code == 403


def test_org_viewer_cannot_submit():
    with pytest.raises(TransitionError) as exc:
        validate_transition(ACTION_REQUIRED, SUBMITTED, org_member("viewer"), **_submit_kwargs())
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Moves not in the table -> 409
# ---------------------------------------------------------------------------

def _is_valid_for_someone(current, target):
    """True when some actor with satisfied note rules may make the move."""
    for actor in (external_auditor(), org_member("admin")):
        try:
            validate_transition(current, target, actor, note="n", linked_artifact_count=1)
            return True
        except TransitionError as e:
            if e.status_code == 409:
                return False
    return False


def test_full_matrix_has_exactly_the_eight_edges():
    valid = {
        (c, t) for c, t in itertools.product(STATUSES, STATUSES)
        if _is_valid_for_someone(c, t)
    }
    assert valid == set(ALL_EDGES)
    assert len(valid) == 8


@pytest.mark.parametrize(
    "current,target",
    [(c, t) for c, t in itertools.product(STATUSES, STATUSES) if (c, t) not in ALL_EDGES],
)
def test_every_move_outside_the_table_is_409_for_every_actor(current, target):
    for actor in (external_auditor(), internal_auditor(org_role="admin"), org_member("admin")):
        with pytest.raises(TransitionError) as exc:
            validate_transition(current, target, actor, note="n", linked_artifact_count=1)
        assert exc.value.status_code == 409


@pytest.mark.parametrize("status", STATUSES)
def test_same_status_move_is_409(status):
    with pytest.raises(TransitionError) as exc:
        validate_transition(status, status, external_auditor(), note="n")
    assert exc.value.status_code == 409


@pytest.mark.parametrize("current,target", [("bogus", OPEN), (OPEN, "bogus"), ("", ""), (None, OPEN)])
def test_unknown_status_is_409(current, target):
    with pytest.raises(TransitionError) as exc:
        validate_transition(current, target, external_auditor(), note="n")
    assert exc.value.status_code == 409


def test_409_comes_before_403():
    # A move not in the table is 409 even for an actor who could not make any move.
    with pytest.raises(TransitionError) as exc:
        validate_transition(OPEN, ACCEPTED, org_member("viewer"))
    assert exc.value.status_code == 409


def test_403_comes_before_422():
    # Wrong side with a missing note reports the side, not the note.
    with pytest.raises(TransitionError) as exc:
        validate_transition(IN_PROGRESS, ACTION_REQUIRED, org_member("admin"), note=None)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Note rules -> 422
# ---------------------------------------------------------------------------

def _right_actor(edge):
    return org_member("editor") if edge in ORG_EDGES else external_auditor()


@pytest.mark.parametrize("edge", sorted(NOTE_EDGES), ids=lambda e: f"{e[0]}->{e[1]}")
@pytest.mark.parametrize("note", [None, "", "   ", "\n\t "], ids=["none", "empty", "spaces", "whitespace"])
def test_note_requiring_edge_without_note_is_422(edge, note):
    with pytest.raises(TransitionError) as exc:
        validate_transition(*edge, _right_actor(edge), note=note, linked_artifact_count=1)
    assert exc.value.status_code == 422


@pytest.mark.parametrize(
    "edge", [e for e in ALL_EDGES if e not in NOTE_EDGES], ids=lambda e: f"{e[0]}->{e[1]}"
)
def test_edge_without_note_rule_passes_without_note(edge):
    assert validate_transition(*edge, _right_actor(edge), note=None) == ACTOR_AUDITOR


# ---------------------------------------------------------------------------
# Submit: response note + artifact or reason
# ---------------------------------------------------------------------------

def test_submit_with_note_and_one_artifact_passes():
    assert validate_transition(
        ACTION_REQUIRED, SUBMITTED, org_member(), note="Attached", linked_artifact_count=1
    ) == ACTOR_ORG


def test_submit_with_note_reason_and_no_artifact_passes():
    assert validate_transition(
        ACTION_REQUIRED, SUBMITTED, org_member(), note="See reason",
        linked_artifact_count=0, no_artifact_reason="The control is manual; walkthrough booked",
    ) == ACTOR_ORG


@pytest.mark.parametrize("reason", [None, "", "   "], ids=["none", "empty", "spaces"])
def test_submit_with_note_no_artifact_no_reason_is_422(reason):
    with pytest.raises(TransitionError) as exc:
        validate_transition(
            ACTION_REQUIRED, SUBMITTED, org_member(), note="Here you go",
            linked_artifact_count=0, no_artifact_reason=reason,
        )
    assert exc.value.status_code == 422


def test_submit_with_artifact_but_no_note_is_422():
    with pytest.raises(TransitionError) as exc:
        validate_transition(ACTION_REQUIRED, SUBMITTED, org_member(), note=" ", linked_artifact_count=3)
    assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# Internal auditor self-accept (segregation of duties)
# ---------------------------------------------------------------------------

ACCEPT_EDGES = [(IN_PROGRESS, ACCEPTED), (SUBMITTED, ACCEPTED)]


@pytest.mark.parametrize("edge", ACCEPT_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_internal_auditor_cannot_accept_evidence_they_own(edge):
    with pytest.raises(TransitionError) as exc:
        validate_transition(*edge, internal_auditor(owns_evidence=True))
    assert exc.value.status_code == 403
    assert "segregation of duties" in exc.value.detail.lower()
    assert "own" in exc.value.detail.lower()


@pytest.mark.parametrize("edge", ACCEPT_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_internal_auditor_cannot_accept_evidence_of_their_accountable_team(edge):
    with pytest.raises(TransitionError) as exc:
        validate_transition(*edge, internal_auditor(in_accountable_team=True))
    assert exc.value.status_code == 403
    assert "segregation of duties" in exc.value.detail.lower()
    assert "team" in exc.value.detail.lower()


@pytest.mark.parametrize("edge", ACCEPT_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_internal_auditor_can_accept_when_neither_flag_is_set(edge):
    assert validate_transition(*edge, internal_auditor()) == ACTOR_AUDITOR


@pytest.mark.parametrize("edge", ACCEPT_EDGES, ids=lambda e: f"{e[0]}->{e[1]}")
def test_external_auditor_with_flags_is_not_blocked(edge):
    actor = external_auditor(owns_evidence=True, in_accountable_team=True)
    assert validate_transition(*edge, actor) == ACTOR_AUDITOR


@pytest.mark.parametrize(
    "edge", [e for e in AUDITOR_EDGES if e[1] != ACCEPTED], ids=lambda e: f"{e[0]}->{e[1]}"
)
def test_internal_auditor_can_make_other_auditor_moves_on_owned_evidence(edge):
    actor = internal_auditor(owns_evidence=True, in_accountable_team=True)
    assert validate_transition(*edge, actor, **_kwargs_for(edge)) == ACTOR_AUDITOR


# ---------------------------------------------------------------------------
# allowed_targets agrees with validate_transition
# ---------------------------------------------------------------------------

ACTORS = {
    "external": external_auditor(),
    "external-flags": external_auditor(owns_evidence=True, in_accountable_team=True),
    "internal": internal_auditor(),
    "internal-owns": internal_auditor(owns_evidence=True),
    "internal-team": internal_auditor(in_accountable_team=True),
    "internal-editor-owns": internal_auditor(org_role="editor", owns_evidence=True),
    "org-viewer": org_member("viewer"),
    "org-editor": org_member("editor"),
    "org-admin": org_member("admin"),
    "nobody": ReviewActor(user_id=uuid4(), is_auditor=False, auditor_kind=None, org_role=None),
}


@pytest.mark.parametrize("actor_name", list(ACTORS))
@pytest.mark.parametrize("current", STATUSES)
def test_allowed_targets_agrees_with_validate_transition(current, actor_name):
    actor = ACTORS[actor_name]
    expected = []
    for target in STATUSES:
        try:
            # Satisfy note rules: allowed_targets ignores them.
            validate_transition(current, target, actor, note="n", linked_artifact_count=1)
            expected.append(target)
        except TransitionError:
            pass
    assert allowed_targets(current, actor) == expected


def test_allowed_targets_examples():
    assert allowed_targets(SUBMITTED, external_auditor()) == [IN_PROGRESS, ACTION_REQUIRED, ACCEPTED]
    assert allowed_targets(SUBMITTED, internal_auditor(owns_evidence=True)) == [IN_PROGRESS, ACTION_REQUIRED]
    assert allowed_targets(ACTION_REQUIRED, org_member("editor")) == [SUBMITTED]
    assert allowed_targets(ACTION_REQUIRED, org_member("viewer")) == []
    assert allowed_targets(OPEN, org_member("admin")) == []


# ---------------------------------------------------------------------------
# note_required
# ---------------------------------------------------------------------------

def test_note_required_exactly_for_note_edges():
    required = {
        (c, t) for c, t in itertools.product(STATUSES, STATUSES) if note_required(c, t)
    }
    assert required == NOTE_EDGES


def test_note_required_false_for_unknown_statuses():
    assert note_required("bogus", OPEN) is False


# ---------------------------------------------------------------------------
# One person cannot be on both sides of the same card
# ---------------------------------------------------------------------------

def test_internal_auditor_who_responded_for_the_org_cannot_accept_that_response():
    actor = internal_auditor(org_role="editor", responded_as_org=True)
    with pytest.raises(TransitionError) as exc:
        validate_transition(SUBMITTED, ACCEPTED, actor)
    assert exc.value.status_code == 403
    assert "you submitted the organisation's response" in exc.value.detail


def test_external_auditor_is_not_subject_to_the_self_accept_rule():
    actor = ReviewActor(
        user_id=uuid4(), is_auditor=True, auditor_kind="external", org_role=None,
        owns_evidence=True, in_accountable_team=True, responded_as_org=True,
    )
    assert validate_transition(SUBMITTED, ACCEPTED, actor) == ACTOR_AUDITOR


def test_actor_for_card_sets_the_self_accept_flags_per_card():
    from services.engagement_evidence_review import actor_for_card, allowed_targets

    team, other_team, mine, theirs = uuid4(), uuid4(), uuid4(), uuid4()
    base = internal_auditor(team_ids=frozenset({team}), responded_review_ids=frozenset({mine}))

    owned = actor_for_card(base, review_id=theirs, owner_user_ids=(base.user_id, None))
    assert owned.owns_evidence and ACCEPTED not in allowed_targets(SUBMITTED, owned)

    on_team = actor_for_card(base, review_id=theirs, accountable_team_id=team)
    assert on_team.in_accountable_team and ACCEPTED not in allowed_targets(SUBMITTED, on_team)

    responded = actor_for_card(base, review_id=mine, accountable_team_id=other_team)
    assert responded.responded_as_org and ACCEPTED not in allowed_targets(SUBMITTED, responded)

    free = actor_for_card(base, review_id=theirs, owner_user_ids=(uuid4(),), accountable_team_id=other_team)
    assert not (free.owns_evidence or free.in_accountable_team or free.responded_as_org)
    assert ACCEPTED in allowed_targets(SUBMITTED, free)


def test_actor_for_card_blocks_on_any_accountable_team():
    """An internal auditor on the second accountable team is blocked on the
    card too, as the server-side check blocks them (L1)."""
    from services.engagement_evidence_review import actor_for_card, allowed_targets

    first, second = uuid4(), uuid4()
    base = internal_auditor(team_ids=frozenset({second}))
    card = actor_for_card(base, review_id=uuid4(), accountable_team_id=first,
                          accountable_team_ids=(first, second))
    assert card.in_accountable_team
    assert ACCEPTED not in allowed_targets(SUBMITTED, card)

    free = actor_for_card(base, review_id=uuid4(), accountable_team_id=first,
                          accountable_team_ids=(first,))
    assert not free.in_accountable_team


def test_actor_for_card_leaves_everyone_else_alone():
    from services.engagement_evidence_review import actor_for_card

    external = ReviewActor(user_id=uuid4(), is_auditor=True, auditor_kind="external", org_role=None)
    assert actor_for_card(external, review_id=uuid4(), owner_user_ids=(external.user_id,)) is external
    member = org_member("admin")
    assert actor_for_card(member, review_id=uuid4(), owner_user_ids=(member.user_id,)) is member
