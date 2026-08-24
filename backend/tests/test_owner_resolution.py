"""Unit tests for the owner-resolution fallback chain (#822 phase 3).

No database. That is the point: the chain is the piece of #822 that replaces
nine hand-rolled recipient expressions in ``services/notifications.py``, and a
rule that can only be exercised by standing up an organisation, a team, a
roster and a control is a rule nobody will exercise. The tiers are pure data
in, set out, so every branch — including the one where every tier is empty —
is a three-line test.

The chain under test:

    1. Explicit user assignment on the item
    2. The accountable team's primary AND delegate, both, in parallel
    3. Organisation admins

"First non-empty tier wins" is the whole contract, plus the detail that tier 2
is *both* accountable roles rather than the primary alone.
"""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.owner_resolution import (  # noqa: E402
    OWNER_TIER_ACCOUNTABLE_TEAM,
    OWNER_TIER_EXPLICIT,
    OWNER_TIER_ORG_ADMIN,
    OwnerResolution,
    resolve_owners,
)


def _uid():
    return uuid.uuid4()


# ---------------------------------------------------------------------------
# Tier 1 — explicit user assignment
# ---------------------------------------------------------------------------

def test_explicit_assignment_wins_over_every_later_tier():
    assignee, primary, delegate, admin = _uid(), _uid(), _uid(), _uid()

    result = resolve_owners(
        explicit_user_ids=[assignee],
        accountable_team_members=[(primary, "primary"), (delegate, "delegate")],
        organization_admin_user_ids=[admin],
    )

    assert result.tier == OWNER_TIER_EXPLICIT
    assert result.user_ids == frozenset({assignee})


def test_explicit_tier_takes_every_explicit_id_not_just_the_first():
    """`assigned_user_id` *and* `owner_user_id` are both tier 1 on a control."""
    assigned, owner = _uid(), _uid()

    result = resolve_owners(explicit_user_ids=[assigned, owner])

    assert result.tier == OWNER_TIER_EXPLICIT
    assert result.user_ids == frozenset({assigned, owner})


def test_null_explicit_ids_do_not_count_as_a_populated_tier():
    """The whole reason the chain exists: SET NULL emptied tier 1."""
    primary = _uid()

    result = resolve_owners(
        explicit_user_ids=[None, None],
        accountable_team_members=[(primary, "primary")],
    )

    assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
    assert result.user_ids == frozenset({primary})


# ---------------------------------------------------------------------------
# Tier 2 — the accountable team, primary AND delegate
# ---------------------------------------------------------------------------

def test_accountable_team_returns_primary_and_delegate_in_parallel():
    """Both, not primary-then-delegate-on-escalation.

    A primary on annual leave would otherwise be a silent single point of
    failure, which is the exact failure mode teams exist to remove.
    """
    primary, delegate = _uid(), _uid()

    result = resolve_owners(
        accountable_team_members=[(primary, "primary"), (delegate, "delegate")],
    )

    assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
    assert result.user_ids == frozenset({primary, delegate})


def test_plain_team_members_are_not_recipients():
    """Being on the team is not being accountable for it."""
    primary, plain = _uid(), _uid()

    result = resolve_owners(
        accountable_team_members=[(primary, "primary"), (plain, "member")],
    )

    assert result.user_ids == frozenset({primary})


def test_a_team_of_plain_members_only_is_an_empty_tier():
    """It falls through rather than notifying the whole roster."""
    admin = _uid()

    result = resolve_owners(
        accountable_team_members=[(_uid(), "member"), (_uid(), "member")],
        organization_admin_user_ids=[admin],
    )

    assert result.tier == OWNER_TIER_ORG_ADMIN
    assert result.user_ids == frozenset({admin})


def test_delegate_alone_is_enough_to_hold_tier_two():
    delegate, admin = _uid(), _uid()

    result = resolve_owners(
        accountable_team_members=[(delegate, "delegate")],
        organization_admin_user_ids=[admin],
    )

    assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
    assert result.user_ids == frozenset({delegate})


# ---------------------------------------------------------------------------
# Tier 3 — organisation admins
# ---------------------------------------------------------------------------

def test_falls_through_to_org_admins_when_nothing_else_is_set():
    a, b = _uid(), _uid()

    result = resolve_owners(organization_admin_user_ids=[a, b])

    assert result.tier == OWNER_TIER_ORG_ADMIN
    assert result.user_ids == frozenset({a, b})


# ---------------------------------------------------------------------------
# The all-empty case
# ---------------------------------------------------------------------------

def test_all_tiers_empty_returns_an_empty_result_and_does_not_raise():
    """An organisation with no admins at all is reachable, so this must not throw.

    The caller's correct response is to send nothing; a raise here would take
    down a whole scheduler run over one unassigned item.
    """
    result = resolve_owners()

    assert result == OwnerResolution(user_ids=frozenset(), tier=None)
    assert not result


def test_all_tiers_present_but_every_value_null_is_also_empty():
    result = resolve_owners(
        explicit_user_ids=[None],
        accountable_team_members=[(None, "primary"), (None, None)],
        organization_admin_user_ids=[None],
    )

    assert result.user_ids == frozenset()
    assert result.tier is None


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_someone_who_is_both_assignee_and_primary_appears_once():
    both = _uid()

    result = resolve_owners(
        explicit_user_ids=[both, both],
        accountable_team_members=[(both, "primary")],
    )

    assert result.user_ids == frozenset({both})
    assert len(result.user_ids) == 1


def test_the_same_user_holding_primary_and_delegate_is_deduplicated():
    solo = _uid()

    result = resolve_owners(
        accountable_team_members=[(solo, "primary"), (solo, "delegate")],
    )

    assert result.user_ids == frozenset({solo})


# ---------------------------------------------------------------------------
# Exclusion — "do not notify the person who just acted"
# ---------------------------------------------------------------------------

def test_exclusion_applies_to_the_winning_tier_without_escalating_to_the_next():
    """Excluding the actor must not page the whole team in their place.

    Tier selection happens on the unfiltered tiers, then the exclusion is
    subtracted from whichever tier won. The alternative — filtering first, so
    an empty tier falls through — turns "Alice edited the control she owns"
    into a notification for Alice's entire accountable team, which is
    amplification, not delivery. Today's behaviour at notifications.py:451 is
    to skip, and skipping is preserved.
    """
    actor, primary = _uid(), _uid()

    result = resolve_owners(
        explicit_user_ids=[actor],
        accountable_team_members=[(primary, "primary")],
        exclude_user_ids=[actor],
    )

    assert result.tier == OWNER_TIER_EXPLICIT
    assert result.user_ids == frozenset()


def test_exclusion_leaves_the_other_recipients_of_the_winning_tier():
    actor, delegate = _uid(), _uid()

    result = resolve_owners(
        accountable_team_members=[(actor, "primary"), (delegate, "delegate")],
        exclude_user_ids=[actor],
    )

    assert result.tier == OWNER_TIER_ACCOUNTABLE_TEAM
    assert result.user_ids == frozenset({delegate})


def test_excluding_nobody_is_the_default():
    someone = _uid()
    assert resolve_owners(explicit_user_ids=[someone]).user_ids == frozenset({someone})


# ---------------------------------------------------------------------------
# Shape guarantees the callers depend on
# ---------------------------------------------------------------------------

def test_result_is_immutable_so_a_caller_cannot_corrupt_a_shared_set():
    result = resolve_owners(explicit_user_ids=[_uid()])

    assert isinstance(result.user_ids, frozenset)
    with pytest.raises(AttributeError):
        result.user_ids = frozenset()


def test_accepts_generators_not_just_lists():
    """Callers pass query results, which are iterators."""
    ids = [_uid(), _uid()]

    result = resolve_owners(explicit_user_ids=(i for i in ids))

    assert result.user_ids == frozenset(ids)
