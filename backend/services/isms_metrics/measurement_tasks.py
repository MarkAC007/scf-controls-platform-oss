# SPDX-License-Identifier: AGPL-3.0-only
"""Measurement tasks for logged metrics (phase 3, B2): derived at read time.

A task is not a stored row. It is the same rule the reading and the morning
reminder already use, read as a to-do:

* one task per **active**, unarchived objective whose metric in force is
  ``logged`` with a calendar frequency (monthly, quarterly, annually);
* only the **current** period has a task (no task for a missed earlier one);
* it is open (``not_started``) until the newest logged entry, by the
  reading's order (newest period, then newest written), is for the current
  period or a later one, and done (``completed``) once it is; done tasks
  are returned only when asked for. After a change to a longer frequency a
  November entry still counts for the quarter that began in October, as
  the reading judges it;
* ``due_date`` is the last day of the period, so the task for a period goes
  when the period ends and the next one appears on its first day;
* ``mine`` is true for exactly the people the measurement reminder goes to
  (:func:`services.isms_metrics.reminders.recipients_for`).

Reading tasks writes nothing and raises no notification: the reminder sweep
stays the only notifier. No library calculator runs; only logged metrics
are read. Every query is scoped to the organisation. The statement count
does not grow with the number of tasks for one owner and team: recipients
are resolved once per distinct owner and team (see :func:`_mine`).
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional, Set, Tuple
from uuid import UUID

from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import IsmsMetricResult, ObjectiveMetric, SecurityObjective
from services.isms_metrics import status as status_rules
from services.isms_metrics import wording

#: Statements for one call whose tasks share one owner and one team (tested):
#: objectives, versions, which have entries, recent rows, team and people names,
#: the owner's membership, then one ``recipients_for`` (membership, team, admins).
STATEMENTS_ONE_OWNER = 10
NOT_STARTED = "not_started"
COMPLETED = "completed"

_PERIOD_WORD = {"monthly": "month", "quarterly": "quarter", "annually": "year"}


def task_id(objective_id: UUID, period: date) -> str:
    return f"measurement:{objective_id}:{period.isoformat()}"


def task_title(ref: str, name: str, frequency: str) -> str:
    return f'Log this {_PERIOD_WORD[frequency]}\'s value for {ref} "{name}"'


async def _logged_objectives(db: AsyncSession, org_id: UUID) -> List[Tuple[SecurityObjective, ObjectiveMetric]]:
    """Active, unarchived objectives whose metric in force is logged on a calendar frequency."""
    rows = (await db.execute(
        select(SecurityObjective, ObjectiveMetric).join(
            ObjectiveMetric,
            (ObjectiveMetric.objective_id == SecurityObjective.id)
            & (ObjectiveMetric.organization_id == SecurityObjective.organization_id),
        ).where(
            SecurityObjective.organization_id == org_id,
            SecurityObjective.lifecycle_status == "active",
            SecurityObjective.archived_at.is_(None),
            ObjectiveMetric.active_to.is_(None),
            ObjectiveMetric.kind == "logged",
            ObjectiveMetric.frequency.in_(status_rules.PERIOD_FREQUENCIES),
        ).order_by(SecurityObjective.ref)
    )).all()
    return [(o, m) for o, m in rows]


async def _entries(
    db: AsyncSession, org_id: UUID, pairs: List[Tuple[SecurityObjective, ObjectiveMetric]], today: date,
) -> Tuple[Set[UUID], Dict[UUID, IsmsMetricResult]]:
    """Which objectives have any logged entry, and each one's newest entry when it closes the task.

    Entries count across the objective's metric versions of the same kind and
    unit, as the reading does (``service._newest_logged_query``): a target
    change opens a new version but does not undo a value already logged.
    "Any entry" reads version ids only; whole rows are read only from the
    earliest current period on, since nothing older can close a task.
    """
    if not pairs:
        return set(), {}
    active = {o.id: m for o, m in pairs}
    versions = (await db.execute(select(ObjectiveMetric.id, ObjectiveMetric.objective_id, ObjectiveMetric.unit).where(
        ObjectiveMetric.organization_id == org_id, ObjectiveMetric.objective_id.in_(list(active)),
        ObjectiveMetric.kind == "logged",
    ))).all()
    owner_of = {v.id: v.objective_id for v in versions if v.unit == active[v.objective_id].unit}
    if not owner_of:
        return set(), {}
    current = {o.id: status_rules.period_start(m.frequency, today) for o, m in pairs}
    scope = (
        IsmsMetricResult.organization_id == org_id,
        IsmsMetricResult.objective_metric_id.in_(list(owner_of)),
        IsmsMetricResult.source == "logged",
    )
    with_entries = (await db.execute(select(distinct(IsmsMetricResult.objective_metric_id)).where(*scope))).scalars()
    logged: Set[UUID] = {owner_of[v] for v in with_entries}
    rows = (await db.execute(select(IsmsMetricResult).where(
        *scope, IsmsMetricResult.period_start >= min(current.values()),
    ).order_by(  # the reading's order: service._newest_logged_query
        IsmsMetricResult.period_start.desc().nulls_last(), IsmsMetricResult.created_at.desc(),
        IsmsMetricResult.id.desc(),
    ))).scalars().all()
    newest: Dict[UUID, IsmsMetricResult] = {}
    for row in rows:
        newest.setdefault(owner_of[row.objective_metric_id], row)
    return logged, {oid: row for oid, row in newest.items() if row.period_start >= current[oid]}


async def _mine(db: AsyncSession, caller_id: Optional[UUID], pairs) -> Dict[UUID, bool]:
    """Whether the measurement reminder for each objective goes to the caller.

    ``recipients_for`` is what decides; it runs once per distinct input it
    depends on. Those are the owner while still a member (checked once per
    owner), the owning team, and, when there is no owning team, the
    objective itself: tier 2 then falls back to the objective's own
    accountable team assignment, so such objectives are never shared.
    The organisation and the event are the same for every task.
    """
    from services.isms_metrics.reminders import MEASUREMENT_DUE, _still_member, recipients_for

    if caller_id is None:
        return {o.id: False for o, _ in pairs}
    member: Dict[UUID, bool] = {}
    answers: Dict[Tuple[Any, ...], bool] = {}
    out: Dict[UUID, bool] = {}
    for objective, _ in pairs:
        owner = objective.owner_user_id
        if owner is not None and owner not in member:
            member[owner] = await _still_member(db, objective.organization_id, owner)
        key = (owner if owner is not None and member[owner] else None, objective.owning_team_id,
               objective.id if objective.owning_team_id is None else None)
        if key not in answers:
            answers[key] = caller_id in await recipients_for(db, objective, MEASUREMENT_DUE)
        out[objective.id] = answers[key]
    return out


async def measurement_tasks(
    db: AsyncSession, org_id: UUID, caller_id: Optional[UUID], today: date,
    assigned_to_me: bool = False, include_done: bool = False,
) -> List[Dict[str, Any]]:
    """The organisation's measurement tasks for today, due date first. Writes nothing."""
    from services.isms_metrics.service import _names

    pairs = await _logged_objectives(db, org_id)
    logged, newest = await _entries(db, org_id, pairs, today)
    shown = [(o, m) for o, m in pairs if include_done or o.id not in newest]
    mine_of = await _mine(db, caller_id, shown)
    teams, users = await _names(db, [o.owning_team_id for o, _ in pairs], [o.owner_user_id for o, _ in pairs],
                                org_id=org_id)
    out: List[Dict[str, Any]] = []
    for objective, metric in pairs:
        entry = newest.get(objective.id)
        if entry is not None and not include_done:
            continue
        mine = mine_of[objective.id]
        if assigned_to_me and not mine:
            continue
        period = status_rules.period_start(metric.frequency, today)
        out.append({
            "id": task_id(objective.id, period), "objective_id": objective.id, "ref": objective.ref,
            "title": task_title(objective.ref, objective.name, metric.frequency),
            "metric_name": metric.name or "", "unit": metric.unit, "frequency": metric.frequency,
            "period_start": period, "due_date": status_rules.period_end(metric.frequency, today),
            "status": COMPLETED if entry is not None else NOT_STARTED,
            "first": objective.id not in logged, "mine": mine,
            "owning_team_id": objective.owning_team_id, "owning_team_name": teams.get(objective.owning_team_id),
            "owner_name": users.get(objective.owner_user_id),
            "completed_date": entry.measured_on if entry is not None else None,
            "value_text": (wording.value_text(metric.unit, entry.value, metric.kind, metric.target_value,
                                              metric.warn_value) if entry is not None else None),
        })
    out.sort(key=lambda t: (t["due_date"], t["ref"]))
    return out
