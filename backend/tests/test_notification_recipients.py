"""Who gets notified, per event (#822 phase 4).

Phase 3's ``tests/test_owner_resolution.py`` pins the 1-2-3 chain itself. This
file pins the layer above it: **which tiers each notification type is allowed
to reach**, escalation as an addition rather than a fourth tier, and the two
properties that make the whole feature safe to deploy —

* a user who is both the explicit assignee and the accountable team's primary
  receives **one** notification, and
* an organisation that has created no teams resolves to **exactly** the people
  it resolves to today, at every one of the nine sites.

No database, for the same reason phase 3 gave: a rule that can only be
exercised by standing up an organisation, a roster and a control is a rule
nobody exercises.

On "byte-for-byte today's behaviour"
------------------------------------

#822 asks for a regression test that an org with no teams sees today's
notifications unchanged. That criterion sits alongside another one — escalate
at 0/+7/+30 rather than once per scheduler run — which *does* change the number
of rows a team-less organisation receives, downwards, by design. The two are
only compatible under one reading, and it is the reading taken here:
**"unchanged" is about the recipient set, not the row count.** With no teams,
tier 2 is empty, the chain falls straight through it, and every site resolves
the same people as before. That is what
:class:`TestOrganisationWithNoTeamsSeesNoChange` asserts, site by site.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.owner_resolution import (  # noqa: E402
    OWNER_TIER_ACCOUNTABLE_TEAM,
    OWNER_TIER_EXPLICIT,
    OWNER_TIER_ORG_ADMIN,
    EVENTS,
    Item,
    ROUTE_DIRECTED,
    ROUTE_ORG_ADMIN,
    ROUTE_OWNERSHIP,
    event_for,
    resolve_recipients,
)


def _uid():
    return uuid.uuid4()


def _item(**kwargs) -> Item:
    base = dict(
        item_type="control",
        item_id=_uid(),
        organization_id=_uid(),
        explicit_user_ids=(),
    )
    base.update(kwargs)
    return Item(**base)


# ---------------------------------------------------------------------------
# Routing policy — which tiers an event may reach at all
# ---------------------------------------------------------------------------

class TestRoutingPolicy:

    def test_every_one_of_the_nine_sites_has_a_registered_policy(self):
        """The nine ``Notification(...)`` creation sites, by type.

        ``catalog_reconciliation`` is one site emitting two types, which is why
        this list has ten entries for nine sites.
        """
        for key in (
            "assignment",
            "mention",
            "task_due",
            "task_overdue",
            "evidence_rejected",
            "control_ready_for_review",
            "engagement_query_raised",
            "composite_insufficient",
            "catalog_reconciliation_applied",
            "catalog_reconciliation_rolled_back",
        ):
            assert key in EVENTS, f"{key} has no routing policy"

    def test_an_unregistered_type_is_directed_not_broadcast(self):
        """A new notification type must fail quiet, not fail loud *or* wide.

        Defaulting to the ownership chain would give a brand new type the org's
        entire admin roster as its audience the first time somebody's tier 1
        was empty.
        """
        assert event_for("something_new").routing == ROUTE_DIRECTED

    def test_only_the_three_escalation_events_escalate(self):
        escalating = {key for key, event in EVENTS.items() if event.escalates}
        assert escalating == {
            "task_overdue", "evidence_rejected", "composite_insufficient",
        }


class TestDirectedEvents:
    """Assignment and @mention name their recipients. Nothing is inferred."""

    def test_a_mention_that_names_nobody_notifies_nobody(self):
        """Not the accountable team, and emphatically not every org admin.

        A typo'd handle must not page an organisation.
        """
        result = resolve_recipients(
            _item(explicit_user_ids=()),
            event_for("mention"),
            accountable_team_members=[(_uid(), "primary")],
            organization_admin_user_ids=[_uid(), _uid()],
        )

        assert result.user_ids == frozenset()
        assert result.tier is None

    def test_a_mention_reaches_exactly_the_people_named(self):
        alice, bob = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(alice, bob)),
            event_for("mention"),
            accountable_team_members=[(_uid(), "primary")],
            organization_admin_user_ids=[_uid()],
        )

        assert result.user_ids == frozenset({alice, bob})
        assert result.tier == OWNER_TIER_EXPLICIT

    def test_an_assignment_to_a_vanished_user_notifies_nobody(self):
        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("assignment"),
            organization_admin_user_ids=[_uid()],
        )

        assert result.user_ids == frozenset()


class TestOrgAdminEvents:
    """The admins are the audience, not the fallback."""

    def test_ready_for_review_goes_to_admins_even_when_the_control_has_an_owner(self):
        """Handing a review request to the person who just submitted it would
        be routing it to the one person who cannot action it."""
        owner, admin = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(owner,)),
            event_for("control_ready_for_review"),
            accountable_team_members=[(_uid(), "primary")],
            organization_admin_user_ids=[admin],
        )

        assert result.user_ids == frozenset({admin})
        assert result.tier == OWNER_TIER_ORG_ADMIN

    def test_the_actor_is_excluded_and_that_does_not_fall_through(self):
        """An org whose only admin submitted the control notifies nobody. It
        does **not** then reach past them to the control's owner."""
        actor, owner = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(owner,)),
            event_for("control_ready_for_review"),
            organization_admin_user_ids=[actor],
            exclude_user_ids=[actor],
        )

        assert result.user_ids == frozenset()
        assert result.tier == OWNER_TIER_ORG_ADMIN


# ---------------------------------------------------------------------------
# The ownership chain, and escalation on top of it
# ---------------------------------------------------------------------------

class TestOwnershipEvents:

    def test_an_unassigned_task_reaches_its_owning_team(self):
        """The live defect. Before #822 this task was skipped by the scheduler
        for its entire life — no due warning, no overdue warning, nothing."""
        primary, delegate = _uid(), _uid()

        result = resolve_recipients(
            _item(item_type="task", explicit_user_ids=(None,)),
            event_for("task_due"),
            accountable_team_members=[(primary, "primary"), (delegate, "delegate")],
            organization_admin_user_ids=[_uid()],
        )

        assert result.user_ids == frozenset({primary, delegate})
        assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM

    def test_a_consulted_team_is_never_on_the_routine_path(self):
        """``accountable_team_members`` is fetched from the accountable
        assignment only, so a consulted team's roster never arrives here on a
        routine event. This asserts the shape the resolver relies on: plain
        members of the team it *is* given are still not paged.

        Escalation is the exception, and it arrives by a separate argument —
        see :class:`TestConsultedTeamsHearOnlyOnEscalation`."""
        primary, plain_member = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("task_due"),
            accountable_team_members=[
                (primary, "primary"), (plain_member, "member"),
            ],
        )

        assert result.user_ids == frozenset({primary})

    def test_org_admins_are_the_last_resort_not_the_second(self):
        admin = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("task_due"),
            accountable_team_members=[],
            organization_admin_user_ids=[admin],
        )

        assert result.user_ids == frozenset({admin})
        assert result.tier == OWNER_TIER_ORG_ADMIN

    def test_nothing_anywhere_is_an_empty_answer_not_an_exception(self):
        """A raise here would take down a whole scheduler run over one
        unassigned item."""
        result = resolve_recipients(
            _item(explicit_user_ids=(None,)), event_for("task_due"),
        )

        assert result.user_ids == frozenset()
        assert result.tier is None
        assert not result


class TestTheResultIsASet:

    def test_assignee_who_is_also_team_primary_is_notified_once(self):
        """#822's acceptance criterion, and the reason the return type is a
        set rather than a list of tiers concatenated."""
        alice = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(alice,)),
            event_for("task_overdue"),
            accountable_team_members=[(alice, "primary"), (alice, "delegate")],
        )

        assert result.user_ids == frozenset({alice})
        assert len(result.user_ids) == 1


class TestEscalationIsAdditive:

    def test_an_escalation_reaches_the_team_even_when_someone_is_assigned(self):
        """Tier 1 wins the chain, and the accountable team is notified anyway.

        That is the point of escalation: a stalled item must surface to the
        owning team without waiting for the assignee to leave the company.
        """
        assignee, primary, delegate = _uid(), _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)),
            event_for("task_overdue"),
            accountable_team_members=[(primary, "primary"), (delegate, "delegate")],
        )

        assert result.user_ids == frozenset({assignee, primary, delegate})
        assert result.tier == OWNER_TIER_EXPLICIT, "escalation is not a tier"
        assert result.escalated_user_ids == frozenset({primary, delegate})

    def test_a_non_escalating_event_does_not_reach_past_tier_one(self):
        assignee, primary = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)),
            event_for("task_due"),
            accountable_team_members=[(primary, "primary")],
        )

        assert result.user_ids == frozenset({assignee})
        assert result.escalated_user_ids == frozenset()

    def test_the_actor_is_excluded_from_the_escalation_too(self):
        """The reviewer who rejected the evidence is on the accountable team.
        They still do not get told about their own action."""
        reviewer = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("evidence_rejected"),
            accountable_team_members=[(reviewer, "primary")],
            exclude_user_ids=[reviewer],
        )

        assert result.user_ids == frozenset()


class TestConsultedTeamsHearOnlyOnEscalation:
    """#822's Notifications note, resolved against its Escalation paragraph.

    The issue says both that consulted teams are "notified only on escalation"
    and, one paragraph later, that escalation notifies "the accountable team's
    primary and delegate" without mentioning them. Implemented literally, the
    second sentence makes the first dead spec text and leaves
    ``is_accountable = false`` meaning nothing at all for notifications.

    Resolved by the lead in favour of the note: escalation recipients are
    ``resolved ∪ accountable{primary,delegate} ∪ consulted{primary,delegate}``.
    This class is where that ruling lives, so the next person to read the issue
    and spot the contradiction finds the decision rather than re-litigating it.
    """

    def test_a_consulted_team_is_notified_on_an_escalation(self):
        assignee, accountable, consulted = _uid(), _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)),
            event_for("task_overdue"),
            accountable_team_members=[(accountable, "primary")],
            consulted_team_members=[(consulted, "primary")],
        )

        assert result.user_ids == frozenset({assignee, accountable, consulted})
        assert result.escalated_user_ids == frozenset({accountable, consulted})

    def test_a_consulted_team_is_silent_on_a_routine_event(self):
        """The volume control. A control with five consulted teams must not
        page ten extra people for an ordinary due-date warning."""
        assignee, consulted = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)),
            event_for("task_due"),
            consulted_team_members=[(consulted, "primary")],
        )

        assert result.user_ids == frozenset({assignee})
        assert result.escalated_user_ids == frozenset()

    def test_a_consulted_team_never_answers_the_chain(self):
        """Being consulted does not make you the owner.

        With no accountable team and no assignee, an unowned item falls to the
        org's admins — it does NOT fall to a consulted team. Consulted widens
        an escalation; it never resolves ownership.
        """
        consulted, admin = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("task_due"),
            consulted_team_members=[(consulted, "primary")],
            organization_admin_user_ids=[admin],
        )

        assert result.user_ids == frozenset({admin})
        assert result.tier == OWNER_TIER_ORG_ADMIN

    def test_plain_members_of_a_consulted_team_are_not_paged(self):
        """Same role filter as the accountable team. Being on a team is not
        being on point for it, on either side of the RACI split."""
        primary, plain_member = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("task_overdue"),
            consulted_team_members=[
                (primary, "primary"), (plain_member, "member"),
            ],
        )

        assert result.user_ids == frozenset({primary})

    def test_someone_on_both_teams_is_notified_once(self):
        """The result is a set. A person who is primary of the accountable
        team and delegate of a consulted one gets one notification."""
        person = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("task_overdue"),
            accountable_team_members=[(person, "primary")],
            consulted_team_members=[(person, "delegate")],
        )

        assert result.user_ids == frozenset({person})

    def test_the_actor_is_excluded_from_the_consulted_set_too(self):
        actor, other = _uid(), _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(None,)),
            event_for("evidence_rejected"),
            consulted_team_members=[(actor, "primary"), (other, "delegate")],
            exclude_user_ids=[actor],
        )

        assert result.user_ids == frozenset({other})

    def test_an_org_with_no_teams_is_unaffected_by_any_of_this(self):
        """Both team sets empty is the state of every item in an org that has
        never created a team. The escalation adds nobody."""
        assignee = _uid()

        result = resolve_recipients(
            _item(explicit_user_ids=(assignee,)),
            event_for("task_overdue"),
        )

        assert result.user_ids == frozenset({assignee})
        assert result.escalated_user_ids == frozenset()


# ---------------------------------------------------------------------------
# The upgrade-safety property
# ---------------------------------------------------------------------------

class TestOrganisationWithNoTeamsSeesNoChange:
    """#822: "An org with no teams receives byte-for-byte today's notifications."

    Read as: the *recipient set* is unchanged. Each case below states today's
    hand-derived expression as ``expected`` and asserts the chain agrees, with
    tier 2 empty — which is what an organisation that has never created a team
    always supplies.
    """

    NO_TEAMS: list = []

    @pytest.mark.parametrize(
        "event_key,explicit,admins,exclude,expected_names",
        [
            # assignment: the named user, full stop.
            ("assignment", ("alice",), ("admin",), (), {"alice"}),
            # mention: the named users, minus the commenter.
            ("mention", ("alice", "bob"), ("admin",), ("bob",), {"alice"}),
            # task_due / task_overdue: the assignee (previously, and the
            # `continue` meant nobody at all when it was None).
            ("task_due", ("alice",), ("admin",), (), {"alice"}),
            ("task_overdue", ("alice",), ("admin",), (), {"alice"}),
            # evidence_rejected: assigned_user_id or owner_user_id, minus the
            # reviewer. The call site passes the coalesce, so tier 1 is one id.
            ("evidence_rejected", ("alice",), ("admin",), (), {"alice"}),
            # composite_insufficient: owner and assignee, falling back to
            # admins. Both of those were already in the old expression.
            ("composite_insufficient", ("alice", "bob"), ("admin",), (), {"alice", "bob"}),
            ("composite_insufficient", (None, None), ("admin",), (), {"admin"}),
            # the three admin-audience events.
            ("control_ready_for_review", ("alice",), ("admin",), (), {"admin"}),
            ("engagement_query_raised", ("alice",), ("admin",), (), {"admin"}),
            ("catalog_reconciliation_applied", (), ("admin",), (), {"admin"}),
        ],
    )
    def test_recipients_match_the_pre_822_expression(
        self, event_key, explicit, admins, exclude, expected_names,
    ):
        names = {}

        def named(label):
            if label is None:
                return None
            return names.setdefault(label, _uid())

        result = resolve_recipients(
            _item(explicit_user_ids=tuple(named(n) for n in explicit)),
            event_for(event_key),
            accountable_team_members=self.NO_TEAMS,
            organization_admin_user_ids=[named(n) for n in admins],
            exclude_user_ids=[named(n) for n in exclude],
        )

        assert result.user_ids == frozenset(named(n) for n in expected_names)

    def test_escalation_adds_nobody_when_there_is_no_accountable_team(self):
        """The escalating events are where a silent volume increase would
        show up first, so this is asserted separately from the table above."""
        alice = _uid()

        for event_key in ("task_overdue", "evidence_rejected", "composite_insufficient"):
            result = resolve_recipients(
                _item(explicit_user_ids=(alice,)),
                event_for(event_key),
                accountable_team_members=self.NO_TEAMS,
            )
            assert result.escalated_user_ids == frozenset(), event_key
            assert result.user_ids == frozenset({alice}), event_key
