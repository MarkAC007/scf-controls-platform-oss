"""The resolution chain, expressed in SQL, for the read paths (#822 phase 4).

Invariant 7 of #822: **the read path ships with the write path.** A column
written and never read is the exact defect the issue exists to fix — evidence
assignment already shipped a column that no queue query consumed, and the queue
sat permanently empty, which reads as "no work" rather than "broken".

So the same rule that decides who gets *notified* about an item has to decide
whose *queue* it appears in. In Python that rule is
:func:`services.owner_resolution.resolve_recipients`; here it is the same rule
as a WHERE clause, because a work queue cannot fetch every task in the
organisation and resolve them one at a time.

Two implementations of one rule is a standing risk, and it is deliberate rather
than accidental: the alternative is either resolving in Python over the whole
table, or pushing the notification recipient set through the database on every
scheduler run. ``tests/test_responsibility_filters.py`` pins the two together by
asserting the same fixtures produce the same answer through both paths — that
test is the reason it is safe to have two.

**Tier 1 exclusivity is the subtle part.** A task explicitly assigned to Alice
does **not** appear in the accountable team primary's queue, because tier 1
won and the chain stopped. The team branches below therefore all require the
explicit assignment to be absent, exactly as ``resolve_owners`` requires an
empty tier 1 before it consults tier 2. Without that, marking a team accountable
would quietly add every one of its items to two more people's queues, which is
the silent volume increase #822 forbids.

**Consulted teams are absent**, as they are from the notification path. Being
consulted means the item shows in the team's view; it does not mean the item is
on your personal list of things to do.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from sqlalchemy import and_, literal, or_, select

from models import (
    EvidenceCollectionTask,
    EvidenceTeamAssignment,
    Team,
    TeamMember,
)
from services.owner_resolution import ACCOUNTABLE_MEMBERSHIP_ROLES

#: A tuple, because ``IN`` wants a sequence and the frozenset the resolver uses
#: has no defined order. Same two roles, one source.
_ACCOUNTABLE_ROLES = tuple(sorted(ACCOUNTABLE_MEMBERSHIP_ROLES))


def _is_accountable_member(team_id_column: Any, org_id_column: Any, user_id: UUID):
    """EXISTS: ``user_id`` is the primary or delegate of the named team.

    The organisation travels with the predicate rather than being trusted from
    ``team_id`` alone. The composite foreign keys already make a cross-tenant
    membership row unrepresentable, so this is defence in depth — and it lets
    the planner use ``team_members``' organisation column instead of reaching
    the team first.
    """
    return (
        select(literal(1))
        .select_from(TeamMember)
        .where(
            and_(
                TeamMember.team_id == team_id_column,
                TeamMember.organization_id == org_id_column,
                TeamMember.user_id == user_id,
                TeamMember.membership_role.in_(_ACCOUNTABLE_ROLES),
            )
        )
        .exists()
    )


def _is_accountable_member_of_evidence(evidence_id_column: Any, org_id_column: Any, user_id: UUID):
    """EXISTS: ``user_id`` is primary or delegate of the evidence item's
    **accountable** team. This is what an inheriting task resolves through."""
    return (
        select(literal(1))
        .select_from(EvidenceTeamAssignment)
        .join(
            TeamMember,
            and_(
                TeamMember.team_id == EvidenceTeamAssignment.team_id,
                TeamMember.organization_id == EvidenceTeamAssignment.organization_id,
            ),
        )
        .where(
            and_(
                EvidenceTeamAssignment.evidence_tracking_id == evidence_id_column,
                EvidenceTeamAssignment.organization_id == org_id_column,
                EvidenceTeamAssignment.is_accountable.is_(True),
                TeamMember.user_id == user_id,
                TeamMember.membership_role.in_(_ACCOUNTABLE_ROLES),
            )
        )
        .exists()
    )


def my_task_filter(user_id: UUID):
    """Tasks ``user_id`` is responsible for, by the same chain that notifies them.

    Three disjoint branches, mirroring
    :func:`services.owner_resolution._tier_two_stmt`:

    1. **Assigned to me.** Tier 1, unchanged, and the only branch an
       organisation with no teams ever matches — which is what makes turning
       this on invisible to such an organisation.
    2. **Unassigned, and I am the primary or delegate of its owning team.**
       The ``owning_team_id`` override: setup, collection and review on one
       evidence item are routinely three different functions.
    3. **Unassigned and inheriting, and I am the primary or delegate of its
       parent evidence item's accountable team.** The common case, with no
       user effort and nothing copied down onto the task to drift.

    Tier 3 (org admins) is deliberately **not** here. Falling a work queue
    through to "every administrator" would put every unowned task in the
    organisation on the queue of everyone who could fix that — which is not a
    queue, it is the unfiltered list with extra steps. Tier 3 remains the
    last resort for *notifying* somebody that an item has no owner.
    """
    assigned_to_me = EvidenceCollectionTask.assigned_user_id == user_id
    unassigned = EvidenceCollectionTask.assigned_user_id.is_(None)

    return or_(
        assigned_to_me,
        and_(
            unassigned,
            EvidenceCollectionTask.owning_team_id.isnot(None),
            _is_accountable_member(
                EvidenceCollectionTask.owning_team_id,
                EvidenceCollectionTask.organization_id,
                user_id,
            ),
        ),
        and_(
            unassigned,
            EvidenceCollectionTask.owning_team_id.is_(None),
            _is_accountable_member_of_evidence(
                EvidenceCollectionTask.evidence_tracking_id,
                EvidenceCollectionTask.organization_id,
                user_id,
            ),
        ),
    )


def my_item_filter(
    spec,
    item_id_column: Any,
    *,
    organization_id: UUID,
    user_id: UUID,
    owner_column: Any,
    assignee_column: Any,
):
    """Controls or evidence items ``user_id`` is responsible for.

    ``spec`` is a :class:`~services.team_assignments.TeamAssignmentSpec`, so
    the same function serves the controls list and the evidence list — and
    phase 5's risks and vendors by registering them, not by being edited.

    The team branch requires **both** explicit columns to be empty, because
    that is what ``resolve_owners`` requires before it reaches tier 2. An item
    that still names a person is that person's, and marking a team accountable
    must not silently move it onto two more queues.
    """
    unowned = and_(owner_column.is_(None), assignee_column.is_(None))

    accountable_member = (
        select(literal(1))
        .select_from(spec.model)
        .join(
            TeamMember,
            and_(
                TeamMember.team_id == spec.model.team_id,
                TeamMember.organization_id == spec.model.organization_id,
            ),
        )
        .where(
            and_(
                spec.item_column == item_id_column,
                spec.model.organization_id == organization_id,
                spec.model.is_accountable.is_(True),
                TeamMember.user_id == user_id,
                TeamMember.membership_role.in_(_ACCOUNTABLE_ROLES),
            )
        )
        .exists()
    )

    return or_(
        owner_column == user_id,
        assignee_column == user_id,
        and_(unowned, accountable_member),
    )
