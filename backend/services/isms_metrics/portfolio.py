# SPDX-License-Identifier: AGPL-3.0-only
"""The cross-client view (phase 3, B3): one row per organisation the caller can open.

Read from stored rows only, so the cost does not grow with the number of
organisations and no library calculator runs:

* the organisations are exactly ``auth.get_accessible_org_ids``, never ids
  from the request, and every query filters on that list;
* five statements: organisation names, unarchived objectives, their metric
  versions, the newest computed row per version, and the newest logged row
  per version (plus the two statements ``get_accessible_org_ids`` itself
  makes for a signed-in person);
* each objective's status is ``derive_status`` over the stored value with
  today's lifecycle, target and dates, and the counts are ``summarise``, so
  the columns mean what the dashboard strip means;
* a live metric with no stored row is ``not_measured``, and makes the row
  ``stale``; so does a newest row older than yesterday (the nightly run
  writes one a day);
* nothing is seeded, written or committed. An organisation with no
  objectives shows zeros.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import IsmsMetricResult, ObjectiveMetric, Organization, SecurityObjective
from services.isms_metrics import status as status_rules
from services.isms_metrics.library import get_measure

#: Statements per call for a signed-in person with at least one organisation:
#: two in ``get_accessible_org_ids`` and five here.
STATEMENTS = 7


def _same_measure(a: ObjectiveMetric, b: ObjectiveMetric) -> bool:
    """Whether a row stored under version ``b`` measures what version ``a`` measures."""
    if a.kind != b.kind:
        return False
    if a.kind == "live":
        return a.measure_key == b.measure_key and (a.params or {}) == (b.params or {})
    return a.unit == b.unit


def _logged_order(row: IsmsMetricResult) -> Tuple:
    """``_newest_logged_query``'s order: period first (no period last), then created, then id."""
    return (row.period_start is not None, row.period_start or date.min, row.created_at or datetime.min,
            str(row.id))


def _computed_order(row: IsmsMetricResult) -> Tuple:
    return (row.measured_on, row.created_at or datetime.min, str(row.id))


async def _newest_per_version(db: AsyncSession, org_ids: List[UUID], version_ids: List[UUID], source: str):
    """The newest row of ``source`` for each version (one statement, ``DISTINCT ON``)."""
    if not version_ids:
        return {}
    order = ((IsmsMetricResult.measured_on.desc(),) if source == "computed" else
             (IsmsMetricResult.period_start.desc().nulls_last(),))
    rows = (await db.execute(
        select(IsmsMetricResult).where(
            IsmsMetricResult.organization_id.in_(org_ids),
            IsmsMetricResult.objective_metric_id.in_(version_ids),
            IsmsMetricResult.source == source,
        ).distinct(IsmsMetricResult.objective_metric_id).order_by(
            IsmsMetricResult.objective_metric_id, *order, IsmsMetricResult.created_at.desc(),
            IsmsMetricResult.id.desc(),
        )
    )).scalars().all()
    return {r.objective_metric_id: r for r in rows}


def _status(objective: SecurityObjective, metric: Optional[ObjectiveMetric], row: Optional[IsmsMetricResult],
            today: date) -> str:
    def judge(value, denominator, period, has_logged):
        return status_rules.derive_status(
            objective.lifecycle_status, objective.objective_type, objective.due_date, metric,
            value, denominator, period, has_logged, today,
        )

    unmeasured = status_rules.closed_status(objective.lifecycle_status) or status_rules.NOT_MEASURED
    if metric is None or metric.kind == "unset":
        return unmeasured
    value = None if row is None or row.value is None else float(row.value)
    if metric.kind == "live":
        if get_measure(metric.measure_key) is None:
            return unmeasured
        status = judge(value, row.denominator if row else None, None, False)
        if row is None and status not in status_rules.CLOSED and status != status_rules.PROPOSED:
            return status_rules.NOT_MEASURED  # no stored row: not measured, never "insufficient data"
        return status
    return judge(value, None, row.period_start if row else None, row is not None)


async def portfolio(db: AsyncSession, org_ids: List[UUID], today: date) -> Dict[str, Any]:
    """The ``Portfolio`` shape for ``org_ids`` (the caller's accessible organisations). Writes nothing."""
    from services.isms_metrics.service import summarise

    generated_at = datetime.now(timezone.utc).replace(microsecond=0)
    ids = list(dict.fromkeys(org_ids))
    if not ids:
        return {"items": [], "generated_at": generated_at}
    names = dict((await db.execute(select(Organization.id, Organization.name).where(Organization.id.in_(ids)))).all())
    ids = [i for i in ids if i in names]
    objectives = (await db.execute(select(SecurityObjective).where(
        SecurityObjective.organization_id.in_(ids), SecurityObjective.archived_at.is_(None),
    ))).scalars().all()
    versions = (await db.execute(select(ObjectiveMetric).where(
        ObjectiveMetric.organization_id.in_(ids),
        ObjectiveMetric.objective_id.in_([o.id for o in objectives]),
    ))).scalars().all()
    in_force = {v.objective_id: v for v in versions if v.active_to is None}
    by_objective: Dict[UUID, List[ObjectiveMetric]] = {}
    for v in versions:  # grouped once: the loop below is linear, not objectives times versions
        by_objective.setdefault(v.objective_id, []).append(v)
    computed = await _newest_per_version(db, ids, [v.id for v in versions if v.kind == "live"], "computed")
    logged = await _newest_per_version(
        db, ids, [v.id for v in versions if v.kind in ("logged", "milestone")], "logged")

    per_org: Dict[UUID, List[Dict[str, Any]]] = {i: [] for i in ids}
    newest_day: Dict[UUID, Optional[date]] = {i: None for i in ids}
    stale = {i: False for i in ids}
    yesterday = today - timedelta(days=1)
    for objective in objectives:
        metric = in_force.get(objective.id)
        row = None
        if metric is not None and metric.kind in ("live", "logged", "milestone"):
            pool, order = (computed, _computed_order) if metric.kind == "live" else (logged, _logged_order)
            candidates = [pool[v.id] for v in by_objective.get(objective.id, ())
                          if v.id in pool and _same_measure(metric, v)]
            row = max(candidates, key=order) if candidates else None
        org = objective.organization_id
        per_org[org].append({"archived_at": None, "reading": {"status": _status(objective, metric, row, today)}})
        if row is not None and (newest_day[org] is None or row.measured_on > newest_day[org]):
            newest_day[org] = row.measured_on
        if (metric is not None and metric.kind == "live"
                and objective.lifecycle_status not in status_rules.CLOSED
                and (row is None or row.measured_on < yesterday)):
            stale[org] = True

    items = [{
        "organization_id": i, "organization_name": names[i], **summarise(per_org[i]),
        "last_measured_on": newest_day[i], "stale": stale[i],
    } for i in ids]
    items.sort(key=lambda it: (it["organization_name"].lower(), str(it["organization_id"])))
    return {"items": items, "generated_at": generated_at}
