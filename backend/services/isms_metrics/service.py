# SPDX-License-Identifier: AGPL-3.0-only
"""Security objectives and ISMS metrics: the database work.

Used by the API, the nightly task and the document generator, so the rules
live here once:

* Objectives are created ``proposed`` and archived, never deleted. Refs
  (``OBJ-01`` ...) are handed out one at a time under a transaction-level
  advisory lock on the organisation, which also makes seeding the starters
  safe under two calls at once.
* A metric change closes the active ``objective_metrics`` row and opens a
  new one, so every stored result points at the measure and target it was
  judged against. On a confirmed objective only an admin may change it, and
  the change stamps who set the target.
* Results are never changed or removed. A logged correction is a new row
  for the same period; the newest row for a period is the one that counts.
* ``read_objective`` is the one reading function: live metrics run their
  calculator, logged and milestone metrics read their newest result row.

No function here commits. Each adds its rows and its audit rows to the
session and flushes; the caller commits (or rolls back) the whole change.
Rule failures raise ``ObjectiveError`` carrying a plain sentence and the HTTP
status a router should answer with.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from models import (
    IsmsMetricResult,
    ObjectiveMetric,
    OrganizationMember,
    SecurityObjective,
    Team,
    User,
    VendorActionItem,
)
from services import deficiencies
from services.audit_service import create_audit_entry, log_entity_changes
from services.isms_metrics import status as status_rules
from services.isms_metrics import wording
from services.isms_metrics.library import (
    GROUPS, MEASURES, breakdown as run_breakdown, get_measure, match_score, measure as run_measure, resolve_params,
)
from services.isms_metrics.schemas import ConfirmBody, MetricSpec, ObjectiveCreate, ObjectiveUpdate
from services.isms_metrics.starters import STARTERS, Starter

logger = logging.getLogger(__name__)

ENTITY_TYPE = "security_objective"
RESULT_ENTITY_TYPE = "isms_metric_result"

TRACKED_FIELDS = frozenset({
    "name", "planned_actions", "resources", "evaluation_method", "objective_type",
    "owning_team_id", "owner_user_id", "owner_text", "due_date", "review_date",
    "lifecycle_status", "iso_clauses", "archived_at", "last_evaluation_note",
})
RESULT_TRACKED_FIELDS = frozenset({"objective_metric_id", "value", "period_start", "status", "note", "source"})

#: First half of the two-key advisory lock: "ISMS objectives".
_LOCK_CLASS = 0x15B0
_REF = re.compile(r"^OBJ-(\d+)$")
_RESULTS_LIMIT = 100
_LEGACY_STATUS = {"active": "in_progress", "achieved": "achieved", "missed": "missed"}


# ---------------------------------------------------------------------------
# Errors and the caller
# ---------------------------------------------------------------------------


class ObjectiveError(Exception):
    """A rule failure: ``message`` is a plain sentence, ``status_code`` the HTTP answer."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _not_found() -> ObjectiveError:
    return ObjectiveError("Security objective not found", 404)


@dataclass(frozen=True)
class Actor:
    """Who is acting, for the audit rows; the API and system callers share one path."""

    user_id: Optional[UUID] = None
    action_source: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[UUID] = None
    #: Where a logged value came from, stored on the result row (see :func:`result_origin`).
    #: Never taken from ``action_source``, which a header can set.
    origin: Optional[str] = None

    @classmethod
    def system(cls) -> "Actor":
        return cls(action_source="system", origin="system")

    @classmethod
    def from_request(cls, request: Any, user_id: Optional[UUID], caller: Any = None) -> "Actor":
        from services.audit_service import detect_action_source, get_client_ip, get_request_id, get_user_agent
        return cls(
            user_id=user_id, action_source=detect_action_source(request), ip_address=get_client_ip(request),
            user_agent=get_user_agent(request), request_id=get_request_id(request),
            origin=result_origin(request, caller),
        )

    def audit(self) -> Dict[str, Any]:
        return {
            "changed_by_user_id": self.user_id, "action_source": self.action_source,
            "ip_address": self.ip_address, "user_agent": self.user_agent, "request_id": self.request_id,
        }


def _now() -> datetime:
    return datetime.utcnow()


def _float(value: Any) -> Optional[float]:
    return None if value is None else float(value)


def tracked_values(objective: SecurityObjective) -> Dict[str, Any]:
    return {f: getattr(objective, f) for f in TRACKED_FIELDS}


def metric_values(metric: Optional[ObjectiveMetric]) -> Optional[Dict[str, Any]]:
    if metric is None:
        return None
    return {
        "id": str(metric.id), "kind": metric.kind, "measure_key": metric.measure_key, "params": metric.params,
        "name": metric.name, "unit": metric.unit, "direction": metric.direction,
        "target_value": _float(metric.target_value), "warn_value": _float(metric.warn_value),
        "frequency": metric.frequency, "target_text": metric.target_text,
    }


async def _audit_objective(db, objective, action, actor: Actor, old_values: Dict[str, Any]) -> None:
    await log_entity_changes(
        db=db, organization_id=objective.organization_id, entity_type=ENTITY_TYPE, entity_id=objective.id,
        action=action, old_values=old_values, new_values=tracked_values(objective),
        tracked_fields=TRACKED_FIELDS, **actor.audit(),
    )


async def _audit_metric(db, objective, actor: Actor, old: Optional[ObjectiveMetric], new: ObjectiveMetric) -> None:
    await create_audit_entry(
        db=db, organization_id=objective.organization_id, entity_type=ENTITY_TYPE, entity_id=objective.id,
        action="update", field_name="metric",
        old_value=json.dumps(metric_values(old), sort_keys=True, default=str) if old is not None else None,
        new_value=json.dumps(metric_values(new), sort_keys=True, default=str), **actor.audit(),
    )


# ---------------------------------------------------------------------------
# Locking and refs
# ---------------------------------------------------------------------------


def _lock_key(org_id: UUID) -> int:
    return int.from_bytes(org_id.bytes[:4], "big", signed=True)


async def _lock(db: AsyncSession, org_id: UUID) -> None:
    await db.execute(text("SELECT pg_advisory_xact_lock(:a, :b)"), {"a": _LOCK_CLASS, "b": _lock_key(org_id)})


def _ref_number(ref: str) -> int:
    found = _REF.match(ref or "")
    return int(found.group(1)) if found else 0


async def _next_ref(db: AsyncSession, org_id: UUID) -> str:
    refs = (await db.execute(
        select(SecurityObjective.ref).where(SecurityObjective.organization_id == org_id)
    )).scalars().all()
    return f"OBJ-{max([_ref_number(r) for r in refs] + [0]) + 1:02d}"


# ---------------------------------------------------------------------------
# Loading and names
# ---------------------------------------------------------------------------


async def _load(db: AsyncSession, org_id: UUID, objective_id: UUID, lock: bool = False) -> SecurityObjective:
    query = select(SecurityObjective).where(
        SecurityObjective.id == objective_id, SecurityObjective.organization_id == org_id,
    )
    if lock:
        query = query.with_for_update()
    objective = (await db.execute(query)).scalar_one_or_none()
    if objective is None:
        raise _not_found()
    return objective


async def _active_metrics(db: AsyncSession, org_id: UUID, objective_ids: Iterable[UUID]) -> Dict[UUID, ObjectiveMetric]:
    ids = list(objective_ids)
    if not ids:
        return {}
    rows = (await db.execute(
        select(ObjectiveMetric).where(
            ObjectiveMetric.organization_id == org_id,
            ObjectiveMetric.objective_id.in_(ids),
            ObjectiveMetric.active_to.is_(None),
        )
    )).scalars().all()
    return {m.objective_id: m for m in rows}


async def active_metric(db: AsyncSession, org_id: UUID, objective_id: UUID) -> Optional[ObjectiveMetric]:
    return (await _active_metrics(db, org_id, [objective_id])).get(objective_id)


def _user_name(user: Optional[User]) -> Optional[str]:
    if user is None:
        return None
    return (user.display_name or "").strip() or user.email


async def _names(
    db, team_ids: Iterable[Optional[UUID]], user_ids: Iterable[Optional[UUID]], org_id: Optional[UUID] = None,
):
    """Display names by id. Teams are read within ``org_id`` when it is given."""
    team_ids = {t for t in team_ids if t}
    user_ids = {u for u in user_ids if u}
    teams: Dict[UUID, str] = {}
    users: Dict[UUID, str] = {}
    if team_ids:
        query = select(Team).where(Team.id.in_(team_ids))
        if org_id is not None:
            query = query.where(Team.organization_id == org_id)
        for team in (await db.execute(query)).scalars().all():
            teams[team.id] = team.name
    if user_ids:
        # By id alone, on purpose: these ids are the caller's own or were checked
        # against membership when written, and the record of who logged a value
        # or set a target must keep its name after that person leaves.
        for user in (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all():
            users[user.id] = _user_name(user)
    return teams, users


async def _check_team(db: AsyncSession, org_id: UUID, team_id: Optional[UUID]) -> None:
    if team_id is None:
        return
    found = await db.scalar(select(Team.id).where(Team.id == team_id, Team.organization_id == org_id))
    if found is None:
        raise ObjectiveError("Choose a team of this organisation")


async def _check_member(db: AsyncSession, org_id: UUID, user_id: Optional[UUID]) -> None:
    if user_id is None:
        return
    found = await db.scalar(select(OrganizationMember.id).where(
        OrganizationMember.organization_id == org_id, OrganizationMember.user_id == user_id,
    ))
    if found is None:
        raise ObjectiveError("Choose a person who is a member of this organisation")


# ---------------------------------------------------------------------------
# Metric fields
# ---------------------------------------------------------------------------


def _check_target(unit: Optional[str], direction: Optional[str], target: Optional[float], warn: Optional[float]) -> None:
    for label, value in (("target", target), ("warning level", warn)):
        if value is None:
            continue
        if unit == "percent" and not 0 <= value <= 100:
            raise ObjectiveError(f"A percentage {label} must be between 0 and 100")
        if unit == "count" and value < 0:
            raise ObjectiveError(f"A count {label} cannot be below 0")
    if warn is not None:
        if target is None:
            raise ObjectiveError("Set a target before a warning level")
        if direction == "at_most" and not warn > target:
            raise ObjectiveError("The warning level must be above the target when lower is better")
        if direction != "at_most" and not warn < target:
            raise ObjectiveError("The warning level must be below the target when higher is better")


def metric_fields(spec: MetricSpec) -> Dict[str, Any]:
    """The columns of a new metric row for ``spec``, validated; raises ObjectiveError."""
    target, warn = spec.target_value, spec.warn_value
    if spec.kind == "live":
        found = get_measure(spec.measure_key)
        if found is None:
            raise ObjectiveError("Choose a measure from the library")
        params = resolve_params(found, spec.params)
        unit, direction = found.unit, spec.direction or found.direction
        _check_target(unit, direction, target, warn)
        return {
            "kind": "live", "measure_key": found.key, "params": params,
            "name": spec.name or wording.metric_name(found, params, unit),
            "description": spec.description or wording.metric_description(found, "live", "nightly"),
            "unit": unit, "direction": direction, "target_value": target, "warn_value": warn,
            "target_text": wording.target_text(unit, direction, target),
            "min_population": found.min_population, "frequency": "nightly",
        }
    if spec.kind == "logged":
        if not spec.name:
            raise ObjectiveError("Name the metric the team will log")
        if spec.unit is None:
            raise ObjectiveError("Say whether the logged metric is a percentage or a count")
        if spec.frequency is None:
            raise ObjectiveError("Say how often the team will log the metric")
        direction = spec.direction or "at_least"
        _check_target(spec.unit, direction, target, warn)
        return {
            "kind": "logged", "measure_key": None, "params": {}, "name": spec.name,
            "description": spec.description or wording.metric_description(None, "logged", spec.frequency),
            "unit": spec.unit, "direction": direction, "target_value": target, "warn_value": warn,
            "target_text": wording.target_text(spec.unit, direction, target),
            "min_population": None, "frequency": spec.frequency,
        }
    return {
        "kind": "milestone", "measure_key": None, "params": {}, "name": spec.name or "Milestone",
        "description": spec.description or wording.metric_description(None, "milestone", None),
        "unit": None, "direction": None, "target_value": None, "warn_value": None,
        "target_text": "Done by the due date", "min_population": None, "frequency": None,
    }


async def _open_metric(
    db: AsyncSession, objective: SecurityObjective, fields: Dict[str, Any], actor: Actor, stamp_target: bool,
) -> Tuple[Optional[ObjectiveMetric], ObjectiveMetric]:
    now = _now()
    old = await active_metric(db, objective.organization_id, objective.id)
    if old is not None:
        old.active_to = now
        await db.flush()
    new = ObjectiveMetric(
        organization_id=objective.organization_id, objective_id=objective.id, active_from=now,
        created_by_user_id=actor.user_id,
        target_set_by_user_id=actor.user_id if stamp_target else None,
        target_set_at=now if stamp_target else None,
        **fields,
    )
    db.add(new)
    await db.flush()
    return old, new


def _copy_fields(metric: ObjectiveMetric) -> Dict[str, Any]:
    return {
        "kind": metric.kind, "measure_key": metric.measure_key, "params": dict(metric.params or {}),
        "name": metric.name, "description": metric.description, "unit": metric.unit,
        "direction": metric.direction, "target_value": metric.target_value, "warn_value": metric.warn_value,
        "target_text": metric.target_text, "min_population": metric.min_population, "frequency": metric.frequency,
    }


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _newest_logged_query(metric: ObjectiveMetric):
    """The newest logged entry for the objective, by the period it is for.

    A change to the target, the name or the confirm step opens a new metric
    version; the entries people typed under the earlier versions still count,
    as long as they measure the same thing (same kind and unit). Each stored
    row keeps pointing at the version it was logged under; the reading judges
    the value against the version in force.
    """
    versions = select(ObjectiveMetric.id).where(
        ObjectiveMetric.organization_id == metric.organization_id,
        ObjectiveMetric.objective_id == metric.objective_id,
        ObjectiveMetric.kind == metric.kind,
        ObjectiveMetric.unit.is_not_distinct_from(metric.unit),
    )
    return select(IsmsMetricResult).where(
        IsmsMetricResult.organization_id == metric.organization_id,
        IsmsMetricResult.objective_metric_id.in_(versions),
        IsmsMetricResult.source == "logged",
    ).order_by(
        IsmsMetricResult.period_start.desc().nulls_last(), IsmsMetricResult.created_at.desc(),
        IsmsMetricResult.id.desc(),
    ).limit(1)


async def _newest_result(db: AsyncSession, metric: ObjectiveMetric) -> Optional[IsmsMetricResult]:
    return (await db.execute(_newest_logged_query(metric))).scalar_one_or_none()


COULD_NOT_MEASURE = "Could not be measured today"


async def _measure_safely(
    db: AsyncSession, org_id: UUID, key: str, params: Optional[Dict[str, Any]], today: date,
):
    """Run one library measure; ``None`` when its calculator fails.

    One measure that cannot read one organisation's data must not take the
    register, the dashboard, the measure list or that organisation's night
    down with it. The savepoint keeps the session usable after an SQL error.
    The failure is logged with the organisation and the measure, and the
    reading says it could not be measured: never a value, never on track.
    """
    try:
        async with db.begin_nested():
            return await run_measure(db, org_id, key, params, today)
    except Exception as exc:
        if type(exc).__name__ == "SoftTimeLimitExceeded":  # the worker is stopping the task
            raise
        logger.error("isms measure %s failed for org %s: %s", key, org_id, exc, exc_info=True)
        return None


def _empty_reading(status: str, target_label: str = "", days_left: Optional[int] = None) -> Dict[str, Any]:
    return {
        "status": status, "value": None, "value_text": wording.DASH, "numerator": None, "denominator": None,
        "population": "", "segments": [], "target_label": target_label, "measured_on": None,
        "logged_by_name": None, "current_period_start": None, "days_left": days_left,
    }


async def read_objective(
    db: AsyncSession, org_id: UUID, objective: SecurityObjective, metric: Optional[ObjectiveMetric], today: date,
) -> Dict[str, Any]:
    """The reading of one objective today (the ``Reading`` shape)."""
    days_left = (objective.due_date - today).days if objective.due_date else None
    # A closed objective reads achieved or missed whatever its metric.
    unmeasured = status_rules.closed_status(objective.lifecycle_status) or status_rules.NOT_MEASURED
    if metric is None or metric.kind == "unset":
        return _empty_reading(unmeasured, days_left=days_left)
    label = wording.target_label(metric.unit, metric.direction, metric.target_value, metric.warn_value)

    def judge(value, denominator, period, has_logged) -> str:
        return status_rules.derive_status(
            objective.lifecycle_status, objective.objective_type, objective.due_date, metric,
            value, denominator, period, has_logged, today,
        )

    if metric.kind == "live":
        found = get_measure(metric.measure_key)
        if found is None:
            return _empty_reading(unmeasured, label, days_left)
        m = await _measure_safely(db, org_id, found.key, metric.params, today)
        if m is None:
            failed = _empty_reading(judge(None, None, None, False), label, days_left)
            failed["population"] = COULD_NOT_MEASURE
            return failed
        return {
            "status": judge(m.value, m.denominator, None, False), "value": m.value,
            "value_text": wording.value_text(metric.unit, m.value, "live", metric.target_value, metric.warn_value),
            "numerator": m.numerator, "denominator": m.denominator, "population": m.population,
            "segments": list(m.segments), "target_label": label, "measured_on": today,
            "logged_by_name": None, "current_period_start": None, "days_left": days_left,
        }

    row = await _newest_result(db, metric)
    current = status_rules.period_start(metric.frequency, today) if metric.kind == "logged" else None
    value = _float(row.value) if row is not None else None
    logged_by = None
    if row is not None and row.entered_by_user_id:
        _, users = await _names(db, [], [row.entered_by_user_id], org_id=org_id)
        logged_by = users.get(row.entered_by_user_id)
    return {
        "status": judge(value, None, row.period_start if row else None, row is not None), "value": value,
        "value_text": wording.value_text(metric.unit, value, metric.kind, metric.target_value, metric.warn_value),
        "numerator": None, "denominator": None, "population": "", "segments": [], "target_label": label,
        "measured_on": row.measured_on if row else None, "logged_by_name": logged_by,
        "current_period_start": current, "days_left": days_left,
    }


def _metric_out(metric: Optional[ObjectiveMetric], users: Dict[UUID, str]) -> Optional[Dict[str, Any]]:
    if metric is None:
        return None
    return {
        "id": metric.id, "kind": metric.kind, "measure_key": metric.measure_key, "params": metric.params or {},
        "name": metric.name, "description": metric.description, "unit": metric.unit, "direction": metric.direction,
        "target_value": _float(metric.target_value), "warn_value": _float(metric.warn_value),
        "target_text": metric.target_text, "min_population": metric.min_population, "frequency": metric.frequency,
        "target_set_by_user_id": metric.target_set_by_user_id,
        "target_set_by_name": users.get(metric.target_set_by_user_id) if metric.target_set_by_user_id else None,
        "target_set_at": metric.target_set_at, "active_from": metric.active_from,
    }


def _objective_out(objective, metric, reading, teams, users) -> Dict[str, Any]:
    return {
        "id": objective.id, "ref": objective.ref, "name": objective.name,
        "planned_actions": objective.planned_actions or "", "resources": objective.resources or "",
        "evaluation_method": objective.evaluation_method or "", "objective_type": objective.objective_type,
        "owning_team_id": objective.owning_team_id, "owning_team_name": teams.get(objective.owning_team_id),
        "owner_user_id": objective.owner_user_id, "owner_name": users.get(objective.owner_user_id),
        "owner_text": objective.owner_text, "due_date": objective.due_date, "review_date": objective.review_date,
        "lifecycle_status": objective.lifecycle_status, "iso_clauses": list(objective.iso_clauses or []),
        "source": objective.source, "last_evaluated_at": objective.last_evaluated_at,
        "last_evaluated_by_name": users.get(objective.last_evaluated_by_user_id),
        "last_evaluation_note": objective.last_evaluation_note,
        "created_at": objective.created_at, "updated_at": objective.updated_at,
        "archived_at": objective.archived_at, "metric": _metric_out(metric, users), "reading": reading,
    }


TREND_POINTS = 12


async def stored_series(
    db: AsyncSession, org_id: UUID, metrics: Dict[UUID, ObjectiveMetric],
) -> Dict[UUID, Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]]:
    """Per objective: its trend (up to 12 stored values, oldest first) and its baseline (the first one).

    Only results whose metric version measures the same thing as the active
    metric (``same_measure``) and that carry a number. A logged entry counts
    once per period (the newest row for it) and is dated on its period: a
    point's ``measured_on`` is ``coalesce(period_start, measured_on)``. Two
    statements for the whole list, whatever its length: the versions, then one
    windowed query.
    """
    active = {oid: m for oid, m in metrics.items() if m is not None and m.kind != "unset"}
    out: Dict[UUID, Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]] = {oid: ([], None) for oid in metrics}
    if not active:
        return out
    versions = (await db.execute(select(ObjectiveMetric).where(
        ObjectiveMetric.organization_id == org_id, ObjectiveMetric.objective_id.in_(list(active)),
    ))).scalars().all()
    same_ids = [v.id for v in versions if same_measure(v, active.get(v.objective_id))]
    if not same_ids:
        return out
    # A logged entry is one point per period, placed on its period: a correction is
    # a new row for the same period and the newest row for a period is the one that
    # counts (as in ``_newest_logged_query``). Ranked across every row of the period
    # before rows without a number are dropped, so a correction is never skipped.
    # Computed rows have no period and are kept as they are, dated on their day.
    point_on = func.coalesce(IsmsMetricResult.period_start, IsmsMetricResult.measured_on)
    per_period = func.row_number().over(
        partition_by=(ObjectiveMetric.objective_id, IsmsMetricResult.period_start),
        order_by=(IsmsMetricResult.created_at.desc(), IsmsMetricResult.id.desc()),
    )
    entries = select(
        ObjectiveMetric.objective_id.label("objective_id"), point_on.label("point_on"),
        IsmsMetricResult.period_start.label("period_start"), IsmsMetricResult.value.label("value"),
        IsmsMetricResult.created_at.label("created_at"), IsmsMetricResult.id.label("id"),
        per_period.label("per_period"),
    ).join(ObjectiveMetric, ObjectiveMetric.id == IsmsMetricResult.objective_metric_id).where(
        IsmsMetricResult.organization_id == org_id, ObjectiveMetric.organization_id == org_id,
        IsmsMetricResult.objective_metric_id.in_(same_ids),
    ).subquery()
    e = entries.c
    newest = func.row_number().over(
        partition_by=e.objective_id, order_by=(e.point_on.desc(), e.created_at.desc(), e.id.desc()),
    ).label("newest")
    oldest = func.row_number().over(
        partition_by=e.objective_id, order_by=(e.point_on, e.created_at, e.id),
    ).label("oldest")
    ranked = select(
        e.objective_id, e.point_on.label("measured_on"), e.value, newest, oldest,
    ).where(
        e.value.isnot(None), e.point_on.isnot(None), or_(e.period_start.is_(None), e.per_period == 1),
    ).subquery()
    rows = (await db.execute(select(ranked).where(or_(ranked.c.newest <= TREND_POINTS, ranked.c.oldest == 1))
                             .order_by(ranked.c.objective_id, ranked.c.newest.desc()))).all()
    trends: Dict[UUID, List[Dict[str, Any]]] = {}
    baselines: Dict[UUID, Dict[str, Any]] = {}
    for r in rows:
        m = active[r.objective_id]
        # The server words every number: the same faithful rounding as the tile's value.
        point = {"measured_on": r.measured_on, "value": float(r.value),
                 "value_text": wording.value_text(m.unit, r.value, m.kind, m.target_value, m.warn_value)}
        if r.newest <= TREND_POINTS:
            trends.setdefault(r.objective_id, []).append(point)
        if r.oldest == 1:
            baselines[r.objective_id] = point
    for oid in metrics:
        out[oid] = (trends.get(oid, []), baselines.get(oid))
    return out


def reading_pace(objective: SecurityObjective, metric: Optional[ObjectiveMetric], reading: Dict[str, Any],
                 baseline: Optional[Dict[str, Any]], today: date) -> Optional[Dict[str, Any]]:
    """``reading.pace`` (contract A2); the status in ``reading`` is never touched.

    Only beside a status that judges the value against the line: an objective
    inside its date reads ``in_progress``, except "Insufficient data" (too few
    to judge) and "Measurement overdue" (a stale entry), which carry a value
    but are not judging it.
    """
    if metric is None or reading.get("status") != status_rules.IN_PROGRESS:
        return None
    return status_rules.pace(
        objective.objective_type, objective.lifecycle_status, objective.archived_at is not None,
        objective.due_date, metric, reading.get("value"),
        baseline["measured_on"] if baseline else None, baseline["value"] if baseline else None, today,
    )


async def _build(db, org_id, objectives: Sequence[SecurityObjective], today: date) -> List[Dict[str, Any]]:
    metrics = await _active_metrics(db, org_id, [o.id for o in objectives])
    teams, users = await _names(
        db, [o.owning_team_id for o in objectives],
        [u for o in objectives for u in (o.owner_user_id, o.last_evaluated_by_user_id)]
        + [m.target_set_by_user_id for m in metrics.values()],
        org_id=org_id,
    )
    series = await stored_series(db, org_id, {o.id: metrics.get(o.id) for o in objectives})
    out = []
    for objective in objectives:
        metric = metrics.get(objective.id)
        reading = await read_objective(db, org_id, objective, metric, today)
        trend, baseline = series.get(objective.id, ([], None))
        reading["trend"] = trend
        reading["pace"] = reading_pace(objective, metric, reading, baseline, today)
        out.append(_objective_out(objective, metric, reading, teams, users))
    return out


def summarise(items: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Summary counts over unarchived items (the ``ObjectiveSummary`` shape)."""
    live = [i for i in items if i.get("archived_at") is None]
    counts = {s: 0 for s in status_rules.READING_STATUSES}
    for item in live:
        counts[item["reading"]["status"]] += 1
    return {
        "objectives": len(live), "on_track": counts["on_track"], "at_risk": counts["at_risk"],
        "off_track": counts["off_track"], "in_progress": counts["in_progress"], "proposed": counts["proposed"],
        "not_measured": counts["not_measured"], "insufficient_data": counts["insufficient_data"],
        "needs_attention": sum(counts[s] for s in status_rules.NEEDS_ATTENTION),
        "overdue": sum(counts[s] for s in status_rules.OVERDUE),
        # Closed objectives: counted on their own, in none of the figures above.
        "achieved": counts["achieved"], "missed": counts["missed"],
    }


async def _refresh(db: AsyncSession, objective: SecurityObjective) -> None:
    await db.flush()
    await db.refresh(objective)


async def get_objective(db: AsyncSession, org_id: UUID, objective_id: UUID, today: date) -> Dict[str, Any]:
    """One objective (the ``ObjectiveOut`` shape); 404 for another organisation's id."""
    objective = await _load(db, org_id, objective_id)
    return (await _build(db, org_id, [objective], today))[0]


async def list_objectives(
    db: AsyncSession, org_id: UUID, today: date, include_archived: bool = False, seed: bool = True,
) -> Dict[str, Any]:
    """The register (the ``ObjectiveList`` shape), ordered by ref.

    Seeds missing starters first unless ``seed`` is false (the caller commits).
    """
    if seed:
        await ensure_starters(db, org_id, today, Actor.system())
    query = select(SecurityObjective).where(SecurityObjective.organization_id == org_id)
    if not include_archived:
        query = query.where(SecurityObjective.archived_at.is_(None))
    objectives = sorted((await db.execute(query)).scalars().all(), key=lambda o: (_ref_number(o.ref), o.ref))
    items = await _build(db, org_id, objectives, today)
    return {"items": items, "summary": summarise(items), "measured_at": _now()}


# ---------------------------------------------------------------------------
# Starters
# ---------------------------------------------------------------------------


def _starter_metric_fields(starter: Starter) -> Dict[str, Any]:
    if starter.kind == "live":
        found = get_measure(starter.measure_key)
        spec = MetricSpec(
            kind="live", measure_key=found.key, name=starter.metric_name,
            target_value=found.suggested_target, warn_value=found.suggested_warn,
        )
    elif starter.kind == "logged":
        spec = MetricSpec(
            kind="logged", name=starter.metric_name, description=starter.description, unit=starter.unit,
            direction=starter.direction, target_value=starter.target, warn_value=starter.warn,
            frequency=starter.frequency,
        )
    else:
        spec = MetricSpec(kind="milestone", name=starter.metric_name, description=starter.description)
    return metric_fields(spec)


def _evaluation_for(fields: Dict[str, Any]) -> str:
    return wording.evaluation_text(
        get_measure(fields.get("measure_key")), fields.get("params"), fields["kind"], fields.get("unit"),
        fields.get("direction"), fields.get("target_value"), fields.get("warn_value"), fields.get("frequency"),
        fields.get("name"),
    )


def _follow_metric_wording(
    objective: SecurityObjective, old_fields: Optional[Dict[str, Any]], new_fields: Dict[str, Any],
) -> None:
    """Keep "How results are evaluated" in step with the metric while it is still the generated text.

    The text names the target ("Target: at least 100%; warning below 90%"), so a
    changed target would otherwise leave the register and the clause 6.2 document
    quoting the old one. Text a person has rewritten is left alone.
    """
    if old_fields is None or "unset" in (old_fields.get("kind"), new_fields.get("kind")):
        return
    if (objective.evaluation_method or "") == _evaluation_for(old_fields):
        objective.evaluation_method = _evaluation_for(new_fields)


async def ensure_starters(db: AsyncSession, org_id: UUID, today: date, actor: Optional[Actor] = None) -> int:
    """Seed every starter not yet present (archived ones count as present); returns how many were added."""
    actor = actor or Actor.system()
    wanted = {s.source for s in STARTERS}

    async def present() -> set:
        return set((await db.execute(
            select(SecurityObjective.source).where(
                SecurityObjective.organization_id == org_id, SecurityObjective.source.in_(wanted),
            )
        )).scalars().all())

    if await present() >= wanted:
        return 0
    await _lock(db, org_id)
    have = await present()
    added = 0
    for starter in STARTERS:
        if starter.source in have:
            continue
        fields = _starter_metric_fields(starter)
        now = _now()
        objective = SecurityObjective(
            organization_id=org_id, ref=await _next_ref(db, org_id), name=starter.name,
            planned_actions=starter.planned_actions, resources="", evaluation_method=_evaluation_for(fields),
            objective_type=starter.objective_type, lifecycle_status="proposed", iso_clauses=list(starter.clauses),
            source=starter.source, created_at=now, updated_at=now, created_by_user_id=actor.user_id,
            updated_by_user_id=actor.user_id,
        )
        db.add(objective)
        await db.flush()
        db.add(ObjectiveMetric(
            organization_id=org_id, objective_id=objective.id, active_from=now, created_by_user_id=actor.user_id,
            **fields,
        ))
        await create_audit_entry(
            db=db, organization_id=org_id, entity_type=ENTITY_TYPE, entity_id=objective.id, action="create",
            field_name="seeded_starter",
            new_value=json.dumps({"ref": objective.ref, "source": starter.source, "name": starter.name}),
            **actor.audit(),
        )
        await db.flush()
        added += 1
    return added


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _as(model, data):
    return data if isinstance(data, model) else model(**data)


async def create_objective(
    db: AsyncSession, org_id: UUID, data: ObjectiveCreate, actor: Actor, today: date,
) -> Dict[str, Any]:
    """A new objective, always ``proposed``; with ``metric`` its first metric row too."""
    data = _as(ObjectiveCreate, data)
    if not data.name.strip():
        raise ObjectiveError("Name the objective")
    await _check_team(db, org_id, data.owning_team_id)
    await _check_member(db, org_id, data.owner_user_id)
    fields = metric_fields(data.metric) if data.metric is not None else None
    await _lock(db, org_id)
    now = _now()
    evaluation = data.evaluation_method or (data.metric.evaluation_method if data.metric else None) or ""
    objective = SecurityObjective(
        organization_id=org_id, ref=await _next_ref(db, org_id), name=data.name.strip(),
        planned_actions=data.planned_actions, resources=data.resources, evaluation_method=evaluation,
        objective_type=data.objective_type, owning_team_id=data.owning_team_id, owner_user_id=data.owner_user_id,
        due_date=data.due_date, review_date=data.review_date, lifecycle_status="proposed",
        iso_clauses=list(data.iso_clauses), source="custom", created_at=now, updated_at=now,
        created_by_user_id=actor.user_id, updated_by_user_id=actor.user_id,
    )
    db.add(objective)
    await db.flush()
    await _audit_objective(db, objective, "create", actor, {})
    if fields is not None:
        old, new = await _open_metric(db, objective, fields, actor, stamp_target=False)
        await _audit_metric(db, objective, actor, old, new)
    await _refresh(db, objective)
    return (await _build(db, org_id, [objective], today))[0]


_PLAIN_FIELDS = (
    "name", "planned_actions", "resources", "evaluation_method", "objective_type", "owning_team_id",
    "owner_user_id", "owner_text", "due_date", "review_date", "iso_clauses",
)


async def update_objective(
    db: AsyncSession, org_id: UUID, objective_id: UUID, changes: Any, actor: Actor, today: date,
    role: Optional[str] = None,
) -> Dict[str, Any]:
    """Apply the fields the caller sent (``ObjectiveUpdate.changes()`` or a dict).

    ``lifecycle_status`` is refused while the objective is proposed;
    ``archived`` true archives and false restores. Both take a confirmed
    objective off the dashboard's judged counts (an archived one leaves the
    dashboard and the clause documents; a closed one is no longer judged), so
    once confirmed only an admin may change either (403).

    ``objective_type`` and ``due_date`` decide how a confirmed objective is
    judged (an achieve-by-date objective reads "in progress" until its date),
    so once confirmed only an admin may change them (403), the same rule as
    the metric. ``role`` is the caller's organisation role; ``None`` is a
    trusted internal caller and is not restricted.
    """
    if isinstance(changes, ObjectiveUpdate):
        changes = changes.changes()
    changes = dict(changes or {})
    if not changes:
        raise ObjectiveError("Nothing to update")
    unknown = set(changes) - set(_PLAIN_FIELDS) - {"lifecycle_status", "archived"}
    if unknown:
        raise ObjectiveError(f"{sorted(unknown)[0]} cannot be changed here")
    objective = await _load(db, org_id, objective_id, lock=True)
    before = tracked_values(objective)

    if "name" in changes and not (changes["name"] or "").strip():
        raise ObjectiveError("name cannot be cleared")
    for required in ("objective_type", "iso_clauses"):
        if required in changes and changes[required] is None:
            raise ObjectiveError(f"{required} cannot be cleared")
    if objective.lifecycle_status != "proposed" and role is not None and role != "admin":
        for judged in ("objective_type", "due_date"):
            if judged in changes and changes[judged] != getattr(objective, judged):
                raise ObjectiveError(
                    "Only an admin can change the type or due date of a confirmed objective", 403,
                )
        closing = "lifecycle_status" in changes and changes["lifecycle_status"] != objective.lifecycle_status
        archiving = changes.get("archived") is not None and (
            bool(changes["archived"]) != (objective.archived_at is not None)
        )
        if closing or archiving:
            raise ObjectiveError("Only an admin can archive, restore or close a confirmed objective", 403)
    if "lifecycle_status" in changes:
        wanted = changes["lifecycle_status"]
        if objective.lifecycle_status == "proposed":
            raise ObjectiveError("Confirm the objective first")
        if wanted not in ("active", "achieved", "missed"):
            raise ObjectiveError("Lifecycle must be active, achieved or missed")
    if "owning_team_id" in changes:
        await _check_team(db, org_id, changes["owning_team_id"])
    if "owner_user_id" in changes:
        await _check_member(db, org_id, changes["owner_user_id"])

    for name in _PLAIN_FIELDS:
        if name in changes:
            value = changes[name]
            if isinstance(value, str):
                value = value.strip()
            if name in ("planned_actions", "resources", "evaluation_method") and value is None:
                value = ""
            if name == "iso_clauses":
                value = list(value)
            setattr(objective, name, value)
    if "lifecycle_status" in changes:
        objective.lifecycle_status = changes["lifecycle_status"]
    if "archived" in changes and changes["archived"] is not None:
        if changes["archived"] and objective.archived_at is None:
            objective.archived_at = _now()
        elif not changes["archived"]:
            objective.archived_at = None

    if tracked_values(objective) != before:
        objective.updated_at = _now()
        objective.updated_by_user_id = actor.user_id
        await db.flush()
        await _audit_objective(db, objective, "update", actor, before)
    await _refresh(db, objective)
    return (await _build(db, org_id, [objective], today))[0]


async def set_metric(
    db: AsyncSession, org_id: UUID, objective_id: UUID, spec: MetricSpec, actor: Actor, role: str, today: date,
) -> Dict[str, Any]:
    """Close the active metric row and open a new one.

    An editor may do this while the objective is proposed; once confirmed it
    takes an admin (403), and the new row stamps who set the target.
    """
    spec = _as(MetricSpec, spec)
    objective = await _load(db, org_id, objective_id, lock=True)
    confirmed = objective.lifecycle_status != "proposed"
    if confirmed and role != "admin":
        raise ObjectiveError("Only an admin can change the metric of a confirmed objective", 403)
    fields = metric_fields(spec)
    before = tracked_values(objective)
    old, new = await _open_metric(db, objective, fields, actor, stamp_target=confirmed)
    if spec.evaluation_method is not None:
        objective.evaluation_method = spec.evaluation_method
    _follow_metric_wording(objective, _copy_fields(old) if old is not None else None, fields)
    objective.updated_at = _now()
    objective.updated_by_user_id = actor.user_id
    await db.flush()
    await _audit_metric(db, objective, actor, old, new)
    if tracked_values(objective) != before:
        await _audit_objective(db, objective, "update", actor, before)
    await _refresh(db, objective)
    return (await _build(db, org_id, [objective], today))[0]


async def confirm_objective(
    db: AsyncSession, org_id: UUID, objective_id: UUID, body: ConfirmBody, actor: Actor, role: str, today: date,
) -> Dict[str, Any]:
    """Move a proposed objective to ``active`` with a team and a target set by an admin."""
    body = _as(ConfirmBody, body)
    if role != "admin":
        raise ObjectiveError("Only an admin can confirm an objective", 403)
    objective = await _load(db, org_id, objective_id, lock=True)
    if objective.lifecycle_status != "proposed":
        raise ObjectiveError("Only a proposed objective can be confirmed")
    team_id = body.owning_team_id or objective.owning_team_id
    if team_id is None:
        raise ObjectiveError("Choose a responsible team before confirming")
    await _check_team(db, org_id, team_id)
    metric = await active_metric(db, org_id, objective.id)
    if metric is None or metric.kind == "unset":
        raise ObjectiveError("Choose a metric before confirming")
    old_fields = _copy_fields(metric)
    fields = dict(old_fields)
    sent = body.model_fields_set
    due_date = body.due_date if "due_date" in sent and body.due_date else objective.due_date
    if metric.kind in ("live", "logged"):
        target = body.target_value if "target_value" in sent and body.target_value is not None else _float(
            metric.target_value)
        warn = body.warn_value if "warn_value" in sent else _float(metric.warn_value)
        if target is None:
            raise ObjectiveError("Set a target before confirming")
        _check_target(metric.unit, metric.direction, target, warn)
        fields.update(target_value=target, warn_value=warn,
                      target_text=wording.target_text(metric.unit, metric.direction, target))
    elif due_date is None:
        raise ObjectiveError("Set a due date before confirming")

    before = tracked_values(objective)
    objective.owning_team_id = team_id
    objective.due_date = due_date
    if "review_date" in sent and body.review_date is not None:
        objective.review_date = body.review_date
    objective.lifecycle_status = "active"
    _follow_metric_wording(objective, old_fields, fields)
    objective.updated_at = _now()
    objective.updated_by_user_id = actor.user_id
    old, new = await _open_metric(db, objective, fields, actor, stamp_target=True)
    await _audit_metric(db, objective, actor, old, new)
    await _audit_objective(db, objective, "update", actor, before)
    await _refresh(db, objective)
    return (await _build(db, org_id, [objective], today))[0]


async def record_evaluation(
    db: AsyncSession, org_id: UUID, objective_id: UUID, note: str, actor: Actor, today: date,
) -> Dict[str, Any]:
    """Record that the results were evaluated (clause 9.1), with a note."""
    note = (note or "").strip()
    if not note:
        raise ObjectiveError("Write what the evaluation found")
    if len(note) > 4000:
        raise ObjectiveError("The evaluation note is at most 4000 characters")
    objective = await _load(db, org_id, objective_id, lock=True)
    before = tracked_values(objective)
    now = _now()
    objective.last_evaluated_at = now
    objective.last_evaluated_by_user_id = actor.user_id
    objective.last_evaluation_note = note
    objective.updated_at = now
    objective.updated_by_user_id = actor.user_id
    await db.flush()
    await _audit_objective(db, objective, "update", actor, before)
    await create_audit_entry(
        db=db, organization_id=org_id, entity_type=ENTITY_TYPE, entity_id=objective.id, action="update",
        field_name="last_evaluated_at", old_value=json.dumps(before.get("last_evaluated_at"), default=str),
        new_value=json.dumps(now.isoformat()), **actor.audit(),
    )
    await _refresh(db, objective)
    return (await _build(db, org_id, [objective], today))[0]


#: Callers whose identical repeated post is not stored again (B4): a collector
#: re-sending the same reading. A person in the browser is never deduplicated.
DEDUPLICATED_ORIGINS = frozenset({"api_key", "mcp"})
#: ``Numeric(14,4)``: ten digits before the point (schemas.VALUE_LIMIT).
_VALUE_LIMIT = Decimal("1e10")
#: ``auth.User.auth_method`` values that mean an API key signed the call.
KEY_AUTH_METHODS = frozenset({"api_key", "user_api_key"})


def result_origin(request: Any, caller: Any) -> str:
    """The stored origin of a logged value, from how the caller signed in.

    A key caller is ``mcp`` when it says so with ``X-Audit-Source: mcp`` (the
    MCP server does), otherwise ``api_key``. Any person signed in another way
    (Google, OIDC, anything else) is ``ui``, whatever header they send: the
    header can only narrow a key to ``mcp``, never turn a key into a person or
    a person into a key. A call with no request is ``system``.
    """
    if request is None or caller is None:
        return "system"
    if getattr(caller, "auth_method", None) in KEY_AUTH_METHODS:
        said = (request.headers.get("x-audit-source") or "").strip().lower()
        return "mcp" if said == "mcp" else "api_key"
    return "ui"
_FOUR_PLACES = Decimal("0.0001")


async def log_result(
    db: AsyncSession, org_id: UUID, objective_id: UUID, value: float, actor: Actor, today: date,
    period_start: Optional[date] = None, note: Optional[str] = None, collector: Optional[str] = None,
) -> Dict[str, Any]:
    """Log a value for a logged or milestone metric; a correction is a new row for the same period."""
    return (await record_result(db, org_id, objective_id, value, actor, today, period_start, note, collector))[0]


async def objective_id_for_ref(db: AsyncSession, org_id: UUID, ref: str) -> UUID:
    """The id of the organisation's objective with this ref (``OBJ-09``); 404 when there is none."""
    found = (await db.execute(select(SecurityObjective.id).where(
        SecurityObjective.organization_id == org_id, SecurityObjective.ref == ref,
    ))).scalar_one_or_none()
    if found is None:
        raise _not_found()
    return found


async def record_result(
    db: AsyncSession, org_id: UUID, objective_id: UUID, value: float, actor: Actor, today: date,
    period_start: Optional[date] = None, note: Optional[str] = None, collector: Optional[str] = None,
) -> Tuple[Dict[str, Any], bool]:
    """``log_result``, also saying whether a row was written.

    The row's ``detail`` records where the value came from (``origin``, from
    how the caller signed in: :func:`result_origin`) and the ``collector`` name
    it gave, which only a key caller may send. A post from an API key or MCP
    whose value, as stored, equals the newest entry under the metric version
    in force writes nothing and answers with the objective as it stands; the
    second element is then False. For a logged metric "newest" means newest
    for the same period; for a milestone, newest overall. Nothing written
    means no result row and no field-level audit rows; the request itself is
    still in the audit log (the audit middleware records every write call).
    """
    objective = await _load(db, org_id, objective_id, lock=True)
    if objective.archived_at is not None:
        raise ObjectiveError("Restore the objective before logging a value")
    if objective.lifecycle_status in status_rules.CLOSED:
        raise ObjectiveError("This objective is closed. Set it back to active to log a value")
    metric = await active_metric(db, org_id, objective.id)
    if metric is None or metric.kind not in ("logged", "milestone"):
        raise ObjectiveError("Values are logged only for a logged or milestone metric")
    value = float(value)
    if metric.kind == "milestone":
        if value not in (0.0, 1.0):
            raise ObjectiveError("A milestone is 1 (done) or 0 (not done)")
        period = period_start or today
        if period > today:
            raise ObjectiveError("That date has not happened yet")
    else:
        if metric.unit == "percent" and not 0 <= value <= 100:
            raise ObjectiveError("A percentage must be between 0 and 100")
        if metric.unit == "count" and value < 0:
            raise ObjectiveError("A count cannot be below 0")
        period = status_rules.period_start(metric.frequency, period_start or today)
        if period is None:
            period = period_start or today
        if period > status_rules.period_start(metric.frequency, today):
            raise ObjectiveError("That period has not started yet")
    note = (note or "").strip() or None
    collector = (collector or "").strip() or None
    origin = actor.origin or "system"
    if collector is not None and origin not in DEDUPLICATED_ORIGINS:
        raise ObjectiveError("A collector name is accepted only from an API key. Leave it out when typing a value")
    if not -_VALUE_LIMIT < value < _VALUE_LIMIT:
        raise ObjectiveError("That value is too large to store")
    stored = Decimal(str(value)).quantize(_FOUR_PLACES, rounding=ROUND_HALF_UP)
    if not -_VALUE_LIMIT < stored < _VALUE_LIMIT:  # 9999999999.99999 rounds up to ten digits plus one
        raise ObjectiveError("That value is too large to store")
    if origin in DEDUPLICATED_ORIGINS:
        # Only the version in force: after a target change or a rename the first
        # post is a new entry, judged against the new version.
        repeat_of = _newest_logged_query(metric).where(IsmsMetricResult.objective_metric_id == metric.id)
        if metric.kind == "logged":
            repeat_of = repeat_of.where(IsmsMetricResult.period_start == period)
        newest = (await db.execute(repeat_of)).scalar_one_or_none()
        if newest is not None and newest.value is not None and Decimal(newest.value) == stored:
            return (await _build(db, org_id, [objective], today))[0], False
    # The stored status judges the entry as of its own period: a value back-filled
    # for an earlier period earns what it earned then, never "measurement overdue"
    # because today's period has no entry yet. The tile reading is unchanged: it
    # still judges the newest entry against today.
    period_last_day = status_rules.period_end(metric.frequency, period) if metric.kind == "logged" else None
    as_of = min(today, period_last_day) if period_last_day is not None else today
    # Judged from the value as stored (four places), as the tile will read it.
    status = status_rules.derive_status(
        objective.lifecycle_status, objective.objective_type, objective.due_date, metric, float(stored), None,
        period, True, as_of,
    )
    row = IsmsMetricResult(
        organization_id=org_id, objective_metric_id=metric.id, measured_on=today, period_start=period,
        value=stored, status=status, detail={"origin": origin, "collector": collector},
        source="logged", entered_by_user_id=actor.user_id, note=note, created_at=_now(),
    )
    db.add(row)
    await db.flush()
    await log_entity_changes(
        db=db, organization_id=org_id, entity_type=RESULT_ENTITY_TYPE, entity_id=row.id, action="create",
        old_values={}, new_values={f: getattr(row, f) for f in RESULT_TRACKED_FIELDS},
        tracked_fields=RESULT_TRACKED_FIELDS, **actor.audit(),
    )
    await db.flush()
    return (await _build(db, org_id, [objective], today))[0], True


# ---------------------------------------------------------------------------
# Detail, library and nightly recording
# ---------------------------------------------------------------------------


BREAKDOWN_LIMIT = 10
CHANGE_DAYS = 60
#: ``VendorActionItem.source`` of a deficiency raised from an objective (contract section 5).
DEFICIENCY_SOURCE = "security_objective"


async def _breakdown_safely(
    db: AsyncSession, org_id: UUID, objective: SecurityObjective, metric: Optional[ObjectiveMetric],
    today: date, limit: int,
) -> Optional[Dict[str, Any]]:
    """What holds a live objective back; None when it has none, is not live or open, or the breakdown fails.

    Guarded like ``_measure_safely``: a breakdown that cannot read the data is
    logged and leaves the detail call working with ``breakdown`` null.
    """
    if objective.archived_at is not None or status_rules.closed_status(objective.lifecycle_status):
        return None
    if metric is None or metric.kind != "live" or get_measure(metric.measure_key) is None:
        return None
    try:
        async with db.begin_nested():
            return await run_breakdown(db, org_id, metric.measure_key, metric.params, today, limit)
    except Exception as exc:
        if type(exc).__name__ == "SoftTimeLimitExceeded":
            raise
        logger.error("isms breakdown %s failed for org %s: %s", metric.measure_key, org_id, exc, exc_info=True)
        return None


def _settings(metric: ObjectiveMetric) -> Dict[str, Any]:
    measure = get_measure(metric.measure_key)
    raw = metric.params if isinstance(metric.params, dict) else {}
    return resolve_params(measure, raw) if measure is not None else dict(raw)


def same_measure(version: ObjectiveMetric, active: Optional[ObjectiveMetric]) -> bool:
    """Whether a result's metric version measures the same thing as the metric in force (contract A1).

    Live: the same measure and settings. Logged: the same name (trimmed, any case) and unit.
    Milestone: always. Another kind, or no metric in force: never. A change of target,
    warning, direction or frequency alone keeps it the same measure.
    """
    if active is None or version.kind != active.kind:
        return False
    if version.kind == "live":
        return version.measure_key == active.measure_key and _settings(version) == _settings(active)
    if version.kind == "logged":
        return ((version.name or "").strip().casefold() == (active.name or "").strip().casefold()
                and version.unit == active.unit)
    return True


def change_over(results: Sequence[Dict[str, Any]], metric: Optional[ObjectiveMetric], today: date):
    """The newest result against the oldest in the last 60 days, in the metric's current unit.

    Computed results for a live metric, logged results for a logged one;
    nothing for a milestone or unset metric. Only results that measure the same thing as the
    metric in force (``same_measure``). None with fewer than two.
    """
    if metric is None or metric.kind not in ("live", "logged"):
        return None
    source = "computed" if metric.kind == "live" else "logged"
    since = date.fromordinal(today.toordinal() - CHANGE_DAYS)
    usable = [
        r for r in results
        if r.get("same_measure", True) and r["source"] == source and r["unit"] == metric.unit
        and r["value"] is not None
        and r["measured_on"] is not None and since <= r["measured_on"] <= today
    ]
    if len(usable) < 2:
        return None
    ordered = sorted(usable, key=lambda r: (r["measured_on"], r["created_at"]))
    delta = float(Decimal(str(ordered[-1]["value"])) - Decimal(str(ordered[0]["value"])))
    return {"delta": delta, "days": CHANGE_DAYS, "text": wording.change_text(metric.unit, delta, CHANGE_DAYS)}


async def raised_deficiency_count(db: AsyncSession, org_id: UUID, objective_id: UUID) -> int:
    """Open deficiencies raised from this objective (``source`` and ``source_ref``, section 5)."""
    return int(await db.scalar(
        select(func.count(VendorActionItem.id)).where(
            VendorActionItem.organization_id == org_id,
            deficiencies.is_deficiency(), deficiencies.is_open(),
            VendorActionItem.source == DEFICIENCY_SOURCE,
            VendorActionItem.source_ref == str(objective_id),
        )
    ) or 0)


async def metric_detail(
    db: AsyncSession, org_id: UUID, objective_id: UUID, today: date, breakdown_limit: int = BREAKDOWN_LIMIT,
) -> Dict[str, Any]:
    """The objective, its results newest first (at most 100), every metric version, and what holds it back."""
    objective = await _load(db, org_id, objective_id)
    versions = (await db.execute(
        select(ObjectiveMetric).where(
            ObjectiveMetric.organization_id == org_id, ObjectiveMetric.objective_id == objective.id,
        ).order_by(ObjectiveMetric.active_from.desc(), ObjectiveMetric.id.desc())
    )).scalars().all()
    by_id = {m.id: m for m in versions}
    results = (await db.execute(
        select(IsmsMetricResult).where(
            IsmsMetricResult.organization_id == org_id,
            IsmsMetricResult.objective_metric_id.in_(list(by_id) or [objective.id]),
        ).order_by(IsmsMetricResult.created_at.desc(), IsmsMetricResult.id.desc()).limit(_RESULTS_LIMIT)
    )).scalars().all()
    _, users = await _names(
        db, [], [r.entered_by_user_id for r in results] + [m.target_set_by_user_id for m in versions],
        org_id=org_id,
    )
    active = next((m for m in versions if m.active_to is None), None)
    out_results = []
    for r in results:
        m = by_id[r.objective_metric_id]
        out_results.append({
            "id": r.id, "measured_on": r.measured_on, "period_start": r.period_start, "value": _float(r.value),
            "value_text": wording.value_text(m.unit, r.value, m.kind, m.target_value, m.warn_value),
            "numerator": r.numerator,
            "denominator": r.denominator, "status": r.status, "source": r.source,
            "entered_by_name": users.get(r.entered_by_user_id), "note": r.note,
            "origin": (r.detail or {}).get("origin") if r.source == "logged" else None,
            "collector": (r.detail or {}).get("collector") if r.source == "logged" else None,
            "target_label": wording.target_label(m.unit, m.direction, m.target_value, m.warn_value),
            "created_at": r.created_at,
            "target_value": _float(m.target_value), "warn_value": _float(m.warn_value),
            "direction": m.direction, "unit": m.unit, "same_measure": same_measure(m, active),
        })
    limit = max(1, min(int(breakdown_limit), 50))
    return {
        "objective": (await _build(db, org_id, [objective], today))[0],
        "results": out_results,
        "metric_versions": [_metric_out(m, users) for m in versions],
        "breakdown": await _breakdown_safely(db, org_id, objective, active, today, limit),
        "change": change_over(out_results, active, today),
        "raised_deficiencies": await raised_deficiency_count(db, org_id, objective.id),
    }


async def measures_today(db: AsyncSession, org_id: UUID, text_: Optional[str], today: date) -> List[Dict[str, Any]]:
    """Every library measure with its value today: outcome measures, then supporting, each best match first."""
    out = []
    for position, m in enumerate(MEASURES):
        params = resolve_params(m, None)
        reading = await _measure_safely(db, org_id, m.key, None, today)
        today_value = {
            "value": None, "value_text": wording.DASH, "population": COULD_NOT_MEASURE, "segments": [],
        } if reading is None else {
            "value": reading.value, "value_text": wording.value_text(m.unit, reading.value, "live"),
            "population": reading.population, "segments": list(reading.segments),
        }
        out.append((GROUPS.index(m.group), -match_score(m, text_), position, {
            "key": m.key, "name": m.name.format(**params), "description": m.description,
            "formula": m.formula.format(**params), "unit": m.unit, "direction": m.direction,
            "suggested_target": m.suggested_target, "suggested_warn": m.suggested_warn,
            "clauses": list(m.clauses), "min_population": m.min_population, "source_label": m.source_label,
            "params": [p.as_dict() for p in m.params],
            "today": today_value,
            "match": match_score(m, text_), "group": m.group,
        }))
    return [item for *_, item in sorted(out, key=lambda t: t[:3])]


def wording_for(request: Any) -> Dict[str, str]:
    """The ``Wording`` shape for a ``WordingRequest`` (pure; writes nothing)."""
    found = get_measure(request.measure_key) if request.kind == "live" else None
    if request.kind == "live" and found is None:
        raise ObjectiveError("Choose a measure from the library")
    return wording.wording(
        found, request.kind, request.params, request.name, request.unit, request.direction,
        request.target_value, request.warn_value, request.frequency,
    )


async def record_nightly_results(db: AsyncSession, org_id: UUID, today: date) -> int:
    """Seed missing starters, then write one computed row per live metric for ``today``.

    Insert-or-skip on ``uq_isms_metric_results_computed_day``, each inside a
    savepoint, so a second run on the same day writes nothing. Returns the
    number of rows written. The caller commits.
    """
    await ensure_starters(db, org_id, today, Actor.system())
    objectives = (await db.execute(
        select(SecurityObjective).where(
            SecurityObjective.organization_id == org_id, SecurityObjective.archived_at.is_(None),
        )
    )).scalars().all()
    metrics = await _active_metrics(db, org_id, [o.id for o in objectives])
    written = 0
    for objective in objectives:
        metric = metrics.get(objective.id)
        if metric is None or metric.kind != "live":
            continue
        if objective.lifecycle_status in status_rules.CLOSED:
            continue  # closed: no longer judged, and its final state is not a stored status
        exists_today = await db.scalar(select(IsmsMetricResult.id).where(
            IsmsMetricResult.objective_metric_id == metric.id, IsmsMetricResult.measured_on == today,
            IsmsMetricResult.source == "computed",
        ))
        if exists_today is not None:
            continue
        reading = await read_objective(db, org_id, objective, metric, today)
        if reading["measured_on"] is None:
            # The measure failed (logged by the runner) or is no longer in the library:
            # no row, so the gap shows instead of a made-up reading.
            continue
        row = IsmsMetricResult(
            organization_id=org_id, objective_metric_id=metric.id, measured_on=today, period_start=None,
            value=None if reading["value"] is None else Decimal(str(round(reading["value"], 4))),
            numerator=reading["numerator"], denominator=reading["denominator"], status=reading["status"],
            detail={"population": reading["population"], "segments": reading["segments"]},
            source="computed", created_at=_now(),
        )
        try:
            async with db.begin_nested():
                db.add(row)
            written += 1
        except IntegrityError:
            logger.info("isms metric result for %s on %s already recorded", metric.id, today)
    return written


# ---------------------------------------------------------------------------
# Documents: the legacy shape and the stored results
# ---------------------------------------------------------------------------


def legacy_row(objective: SecurityObjective, metric: Optional[ObjectiveMetric], team_name: Optional[str]) -> Dict:
    """One objective in the shape the documents read (no live value anywhere in it)."""
    if metric is None:
        measure_text, target = "", ""
    else:
        measure_text = metric.description if metric.kind == "unset" else metric.name
        target = metric.target_text or ""
    return {
        "id": objective.legacy_row_id or str(objective.id), "ref": objective.ref, "objective": objective.name,
        "measure": measure_text or "", "target": target, "owner": team_name or objective.owner_text or "",
        "resources": objective.resources or "",
        "due_date": objective.due_date.isoformat() if objective.due_date else None,
        "evaluation": objective.evaluation_method or "",
        "status": _LEGACY_STATUS.get(objective.lifecycle_status, "in_progress"),
        "planned_actions": objective.planned_actions or "", "type": objective.objective_type,
        "review_date": objective.review_date.isoformat() if objective.review_date else None,
        "clauses": list(objective.iso_clauses or []),
    }


def _document_objectives_query(org_id):
    return select(SecurityObjective).where(
        SecurityObjective.organization_id == org_id,
        SecurityObjective.archived_at.is_(None),
        SecurityObjective.lifecycle_status != "proposed",
    )


def _sorted(objectives):
    return sorted(objectives, key=lambda o: (_ref_number(o.ref), o.ref))


def _metrics_query(org_id, ids):
    return select(ObjectiveMetric).where(
        ObjectiveMetric.organization_id == org_id, ObjectiveMetric.objective_id.in_(ids),
        ObjectiveMetric.active_to.is_(None),
    )


def legacy_objectives_sync(session: Session, org_id) -> List[Dict[str, Any]]:
    """The legacy objectives list for documents, on a sync session (doc generation)."""
    objectives = _sorted(session.execute(_document_objectives_query(org_id)).scalars().all())
    if not objectives:
        return []
    metrics = {m.objective_id: m for m in session.execute(
        _metrics_query(org_id, [o.id for o in objectives])).scalars().all()}
    team_ids = {o.owning_team_id for o in objectives if o.owning_team_id}
    teams = {
        t.id: t.name for t in session.execute(
            select(Team).where(Team.id.in_(team_ids), Team.organization_id == org_id)
        ).scalars().all()
    } if team_ids else {}
    return [legacy_row(o, metrics.get(o.id), teams.get(o.owning_team_id)) for o in objectives]


async def legacy_objectives(db: AsyncSession, org_id: UUID) -> List[Dict[str, Any]]:
    """The async twin of ``legacy_objectives_sync`` (``GET /isms-profile``)."""
    objectives = _sorted((await db.execute(_document_objectives_query(org_id))).scalars().all())
    if not objectives:
        return []
    metrics = {m.objective_id: m for m in (await db.execute(
        _metrics_query(org_id, [o.id for o in objectives]))).scalars().all()}
    teams, _ = await _names(db, [o.owning_team_id for o in objectives], [], org_id=org_id)
    return [legacy_row(o, metrics.get(o.id), teams.get(o.owning_team_id)) for o in objectives]


NOT_YET_MEASURED = "Not yet measured"


def document_results_sync(session: Session, org_id, today: Optional[date] = None) -> List[Dict[str, Any]]:
    """For each document objective, the newest stored result of its metric, or "Not yet measured".

    A live metric prints the newest nightly row of the version in force. A
    logged or milestone metric prints the entry the register reads (the newest
    by the period it is for, across versions of the same kind and unit) with
    the status worked out as the register works it out, so a back-filled
    correction to an old period does not replace the current figure.

    Kept out of the regeneration fingerprint by the caller: a new value must
    not mark a document stale.
    """
    today = today or date.today()
    objectives = _sorted(session.execute(_document_objectives_query(org_id)).scalars().all())
    if not objectives:
        return []
    metrics = {m.objective_id: m for m in session.execute(
        _metrics_query(org_id, [o.id for o in objectives])).scalars().all()}
    out = []
    for o in objectives:
        metric = metrics.get(o.id)
        row = None
        status = None
        if metric is not None and metric.kind in ("logged", "milestone"):
            row = session.execute(_newest_logged_query(metric)).scalar_one_or_none()
            if row is not None:
                status = status_rules.derive_status(
                    o.lifecycle_status, o.objective_type, o.due_date, metric, _float(row.value), None,
                    row.period_start, True, today,
                )
        elif metric is not None and metric.kind == "live":
            row = session.execute(
                select(IsmsMetricResult).where(
                    IsmsMetricResult.organization_id == metric.organization_id,
                    IsmsMetricResult.objective_metric_id == metric.id,
                ).order_by(
                    IsmsMetricResult.measured_on.desc(), IsmsMetricResult.created_at.desc(),
                    IsmsMetricResult.id.desc(),
                ).limit(1)
            ).scalar_one_or_none()
        legacy = legacy_row(o, metric, None)
        # A closed objective prints its final state, not the last stored judgement.
        closed = status_rules.closed_status(o.lifecycle_status)
        if closed is not None:
            status = closed
        if row is None:
            out.append({
                "ref": o.ref, "objective": o.name, "metric": legacy["measure"], "target": legacy["target"],
                "value_text": NOT_YET_MEASURED,
                "status": status_rules.STATUS_LABELS[closed] if closed else NOT_YET_MEASURED,
                "measured_on": None, "population": "",
            })
            continue
        out.append({
            "ref": o.ref, "objective": o.name, "metric": metric.name,
            "target": wording.target_label(metric.unit, metric.direction, metric.target_value, metric.warn_value),
            "value_text": wording.value_text(
                metric.unit, row.value, metric.kind, metric.target_value, metric.warn_value,
            ),
            "status": status_rules.STATUS_LABELS.get(status or row.status, status or row.status),
            "measured_on": row.measured_on.isoformat(), "population": (row.detail or {}).get("population", ""),
        })
    return out
