# SPDX-License-Identifier: AGPL-3.0-only
"""What the clause 9.1 document reads: per objective, how it is measured, who
measures and evaluates it, and its stored results with the target in force
when each was measured.

``document_measurement_sync`` runs on the sync session the document generator
uses. It issues a fixed number of statements whatever the number of
objectives: the objectives, their active metrics, every metric version, the
results (one windowed query), the teams and the people. The caller keeps the
list out of the regeneration fingerprint: a new value or a new evaluation
must not mark a document stale.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from models import IsmsMetricResult, ObjectiveMetric, Team, User
from services.isms_metrics import status as status_rules
from services.isms_metrics import wording
from services.isms_metrics.library import get_measure
from services.isms_metrics.service import (
    _document_objectives_query,
    _metrics_query,
    _sorted,
    _user_name,
    document_results_sync,
    legacy_row,
    same_measure,
)

#: Live metrics: the newest stored row of each of the last 12 calendar months.
#: Logged metrics: the newest entry of each of the 12 most recent periods.
#: Milestones: the newest 12 entries (a nightly collector posts one a day).
HISTORY = 12

_FREQUENCY_TEXT = {
    "monthly": "Every month, by the responsible team",
    "quarterly": "Every quarter, by the responsible team",
    "annually": "Every year, by the responsible team",
}
_SOURCE_TEXT = {"computed": "Computed by the platform", "logged": "Logged"}


def _months_back(today: date, months: int) -> date:
    """First day of the calendar month ``months`` before ``today``'s month."""
    years, month = divmod(today.year * 12 + today.month - 1 - months, 12)
    return date(years, month + 1, 1)


def _period_text(frequency: Optional[str], kind: str, start: Optional[date]) -> Optional[str]:
    if start is None or kind == "live":
        return None
    if kind == "logged" and frequency == "monthly":
        return start.strftime("%B %Y")
    if kind == "logged" and frequency == "quarterly":
        return f"Q{(start.month - 1) // 3 + 1} {start.year}"
    if kind == "logged" and frequency == "annually":
        return str(start.year)
    return start.isoformat()


def _how(metric: Optional[ObjectiveMetric]) -> Dict[str, Optional[str]]:
    """What is measured, the method and how often, from the metric in force."""
    if metric is None or metric.kind == "unset":
        return {"what": (metric.description or None) if metric else None, "method": None, "frequency": None}
    if metric.kind == "live":
        measure = get_measure(metric.measure_key)
        if measure is None:
            return {"what": metric.description or None, "method": None, "frequency": "Every night, by the platform"}
        return {
            "what": measure.description,
            "method": f"{wording.formula_text(measure, metric.params)} Calculated by the platform from the "
                      f"{measure.source_label}.",
            "frequency": "Every night, by the platform",
        }
    if metric.kind == "logged":
        return {
            "what": metric.name or None,
            "method": metric.description or wording.metric_description(None, "logged", metric.frequency),
            "frequency": _FREQUENCY_TEXT.get(metric.frequency),
        }
    return {
        "what": metric.name or None,
        "method": metric.description or wording.metric_description(None, "milestone", None),
        "frequency": "Once, when it is done, by the due date",
    }


def _results_query(org_id: UUID, version_ids: List[UUID], today: date):
    """Every result the document prints, for every objective, in one statement."""
    month = func.date_trunc("month", IsmsMetricResult.measured_on)
    newest_in_month = func.row_number().over(
        partition_by=(ObjectiveMetric.objective_id, month),
        order_by=(IsmsMetricResult.measured_on.desc(), IsmsMetricResult.created_at.desc(),
                  IsmsMetricResult.id.desc()),
    ).label("newest_in_month")
    newest_in_period = func.row_number().over(
        partition_by=(ObjectiveMetric.objective_id, IsmsMetricResult.period_start),
        order_by=(IsmsMetricResult.created_at.desc(), IsmsMetricResult.id.desc()),
    ).label("newest_in_period")
    newest_entry = func.row_number().over(
        partition_by=ObjectiveMetric.objective_id,
        order_by=(func.coalesce(IsmsMetricResult.period_start, IsmsMetricResult.measured_on).desc(),
                  IsmsMetricResult.created_at.desc(), IsmsMetricResult.id.desc()),
    ).label("newest_entry")
    period_rank = func.dense_rank().over(
        partition_by=ObjectiveMetric.objective_id,
        order_by=IsmsMetricResult.period_start.desc().nulls_last(),
    ).label("period_rank")
    ranked = select(
        IsmsMetricResult.id.label("result_id"), ObjectiveMetric.objective_id.label("objective_id"),
        ObjectiveMetric.kind.label("kind"), IsmsMetricResult.measured_on.label("measured_on"),
        IsmsMetricResult.source.label("source"),
        newest_in_month, newest_in_period, period_rank, newest_entry,
    ).join(ObjectiveMetric, and_(
        ObjectiveMetric.id == IsmsMetricResult.objective_metric_id,
        ObjectiveMetric.organization_id == IsmsMetricResult.organization_id,
    )).where(
        IsmsMetricResult.organization_id == org_id, IsmsMetricResult.objective_metric_id.in_(version_ids),
    ).subquery()
    keep = or_(
        and_(ranked.c.kind == "live", ranked.c.newest_in_month == 1,
             ranked.c.measured_on >= _months_back(today, HISTORY - 1)),
        and_(ranked.c.kind == "logged", ranked.c.source == "logged", ranked.c.newest_in_period == 1,
             ranked.c.period_rank <= HISTORY),
        and_(ranked.c.kind == "milestone", ranked.c.newest_entry <= HISTORY),
    )
    return (
        select(IsmsMetricResult)
        .join(ranked, ranked.c.result_id == IsmsMetricResult.id)
        .where(keep)
        # Period order: a logged entry back-filled today for an earlier period sits
        # with that period, not below newer ones (part A's trend uses the same rule).
        .order_by(func.coalesce(IsmsMetricResult.period_start, IsmsMetricResult.measured_on),
                  IsmsMetricResult.created_at, IsmsMetricResult.id)
    )


def _result_entry(row: IsmsMetricResult, version: ObjectiveMetric, users: Dict[UUID, str]) -> Dict[str, Any]:
    """One stored result, judged against the metric version it was measured under."""
    if version.kind == "milestone":
        target = version.target_text or "Done by the due date"
    else:
        target = wording.target_label(version.unit, version.direction, version.target_value, version.warn_value) \
            or version.target_text or None
    # Only a logged row names a collector, as the API's result list does.
    collector = (row.detail or {}).get("collector") if row.source == "logged" else None
    return {
        "measured_on": row.measured_on.isoformat(),
        "period": _period_text(version.frequency, version.kind, row.period_start),
        "value_text": wording.value_text(version.unit, row.value, version.kind, version.target_value,
                                         version.warn_value),
        "target": target,
        "status": status_rules.STATUS_LABELS.get(row.status, row.status),
        "source": _SOURCE_TEXT.get(row.source, row.source),
        "collector": collector or None,
        "entered_by": users.get(row.entered_by_user_id) if row.entered_by_user_id else None,
        "note": row.note or None,
    }


def document_measurement_sync(
    session: Session, org_id, today: Optional[date] = None, current: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """One entry per document objective (unarchived, not proposed), in reference order.

    ``current`` is ``document_results_sync``'s list when the caller already
    has it (the context builder does); the current status is taken from it so
    the two documents never disagree.
    """
    today = today or date.today()
    org_id = org_id if isinstance(org_id, UUID) else UUID(str(org_id))
    objectives = _sorted(session.execute(_document_objectives_query(org_id)).scalars().all())
    if not objectives:
        return []
    if current is None:
        current = document_results_sync(session, org_id, today)
    now_by_ref = {c["ref"]: c for c in current}
    ids = [o.id for o in objectives]
    active = {m.objective_id: m for m in session.execute(_metrics_query(org_id, ids)).scalars().all()}
    versions = {
        v.id: v for v in session.execute(select(ObjectiveMetric).where(
            ObjectiveMetric.organization_id == org_id, ObjectiveMetric.objective_id.in_(ids),
        )).scalars().all()
    }
    # Only versions measuring the same thing as the metric in force (contract A1):
    # a change of target keeps the history; a change of measure starts a new one.
    same_ids = [v.id for v in versions.values()
                if v.kind != "unset" and same_measure(v, active.get(v.objective_id))]
    rows = session.execute(_results_query(org_id, same_ids, today)).scalars().all() if same_ids else []

    team_ids = {o.owning_team_id for o in objectives if o.owning_team_id}
    teams = {
        t.id: t.name for t in session.execute(
            select(Team).where(Team.id.in_(team_ids), Team.organization_id == org_id)
        ).scalars().all()
    } if team_ids else {}
    user_ids = {o.last_evaluated_by_user_id for o in objectives if o.last_evaluated_by_user_id}
    user_ids |= {r.entered_by_user_id for r in rows if r.entered_by_user_id}
    # By id alone, as service._names does: the record of who evaluated or logged
    # keeps its name after that person leaves the organisation.
    users = {
        u.id: _user_name(u) for u in session.execute(select(User).where(User.id.in_(user_ids))).scalars().all()
    } if user_ids else {}

    results: Dict[UUID, List[Dict[str, Any]]] = {}
    for row in rows:
        version = versions[row.objective_metric_id]
        results.setdefault(version.objective_id, []).append(_result_entry(row, version, users))

    out = []
    for o in objectives:
        metric = active.get(o.id)
        how = _how(metric)
        now = now_by_ref.get(o.ref) or {}
        measured = bool(now.get("measured_on"))
        if metric is not None and metric.kind == "live":
            as_of = now.get("measured_on")
        elif measured and status_rules.closed_status(o.lifecycle_status) is None:
            as_of = today.isoformat()  # logged and milestone metrics are judged today
        else:
            as_of = None
        out.append({
            "ref": o.ref, "objective": o.name, "metric": legacy_row(o, metric, None)["measure"] or None,
            "what_is_measured": how["what"], "method": how["method"],
            "kind": metric.kind if metric is not None else "unset", "frequency_text": how["frequency"],
            "measured_by": teams.get(o.owning_team_id) or (o.owner_text or "").strip() or None,
            "evaluation_method": o.evaluation_method or None,
            "review_date": o.review_date.isoformat() if o.review_date else None,
            "evaluated_by": users.get(o.last_evaluated_by_user_id) if o.last_evaluated_by_user_id else None,
            "evaluated_on": o.last_evaluated_at.date().isoformat() if o.last_evaluated_at else None,
            "evaluation_note": o.last_evaluation_note or None,
            "status": now.get("status"), "status_as_of": as_of,
            "results": results.get(o.id, []),
        })
    return out
