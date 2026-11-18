# SPDX-License-Identifier: AGPL-3.0-only
"""Security objectives (ISO/IEC 27001 clause 6.2) and ISMS metrics (clause 9.1).

* ``GET …/security-objectives`` lists the register, ordered by ref (viewer).
  It seeds the missing starter objectives first, so the register is never
  empty on first open; that is why a read commits.
* ``POST …/security-objectives`` adds an objective, always ``proposed``
  (editor); ``GET …/security-objectives/{id}`` reads one (viewer);
  ``PATCH …/security-objectives/{id}`` edits, archives or restores it, and
  moves a confirmed objective between active, achieved and missed (editor).
* ``PUT …/security-objectives/{id}/metric`` sets the measure and target
  (editor while proposed, admin once confirmed). The metric in force is
  closed and a new version opened, so stored results keep the target they
  were judged against.
* ``POST …/security-objectives/{id}/confirm`` confirms a proposed objective
  with a team and a target (admin).
* ``POST …/security-objectives/{id}/evaluation`` records that the results
  were evaluated, with a note (editor).
* ``GET …/isms-metrics/measures`` is the measure library with today's
  values; ``POST …/isms-metrics/wording`` returns the generated wording for
  a metric and writes nothing (viewer).
* ``GET …/isms-metrics`` is the dashboard (unarchived objectives, the same
  builder as the register); ``GET …/isms-metrics/{id}`` is one metric with
  its results and versions (viewer).
* ``POST …/isms-metrics/{id}/results`` logs a value for a logged or
  milestone metric (editor). Results are never changed or removed.
  ``POST …/isms-metrics/by-ref/{ref}/results`` does the same addressed by
  ``OBJ-09``, for collectors; an API key repeating the newest value is
  answered 200 and no result row and no field-level audit rows are written;
  the request itself is still in the audit log (phase 3 B4). The stored
  origin comes from how the caller signed in, never from a header, and only
  a key caller may name a collector.
* ``GET …/isms-metrics/measurement-tasks`` is this period's logged-value
  to-dos, derived when read (viewer; phase 3 B2).
* ``GET /api/isms-metrics/portfolio`` is one row of summary counts per
  organisation the caller can open, from stored rows (any signed-in caller;
  phase 3 B3). The organisations come from ``get_accessible_org_ids`` only,
  narrowed to the pinned organisation for the static key.

Nothing is deleted here: objectives are archived. The rules live in
``services/isms_metrics/service.py``; this module builds the caller, calls
the service, turns a rule failure into its HTTP answer and commits. Every
write is in the audit log, written by the service with the caller's id.
"""
from datetime import date
from typing import Annotated, Awaitable, List, Optional, TypeVar
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, User, get_accessible_org_ids, require_auth, require_org_role
from database import get_db
from services import single_tenant
from services.isms_metrics import measurement_tasks, portfolio, service
from services.isms_metrics.schemas import (
    ConfirmBody,
    EvaluationCreate,
    MeasurementTask,
    MeasureOut,
    MetricDetail,
    MetricSpec,
    ObjectiveCreate,
    ObjectiveList,
    ObjectiveOut,
    ObjectiveUpdate,
    Portfolio,
    ResultCreate,
    Wording,
    WordingRequest,
)
from services.isms_metrics.service import Actor, ObjectiveError

router = APIRouter(tags=["security-objectives"])

_OBJECTIVES = "/organizations/{org_id}/security-objectives"
_METRICS = "/organizations/{org_id}/isms-metrics"

T = TypeVar("T")


def _caller_id(membership: OrgMembership) -> Optional[UUID]:
    return UUID(membership.user.db_id) if membership.user and membership.user.db_id else None


def _actor(request: Request, membership: OrgMembership) -> Actor:
    return Actor.from_request(request, _caller_id(membership), caller=membership.user)


def _today() -> date:
    return date.today()


async def _answer(call: Awaitable[T]) -> T:
    """Await a service call, answering a rule failure with its status and sentence.

    Nothing is committed on a failure: the session is closed with the
    request, which discards whatever the service had flushed.
    """
    try:
        return await call
    except ObjectiveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


# ---------------------------------------------------------------------------
# Security objectives
# ---------------------------------------------------------------------------


@router.get(_OBJECTIVES, response_model=ObjectiveList)
async def list_security_objectives(
    org_id: UUID,
    include_archived: bool = Query(False, description="Include archived objectives"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The objectives register, ordered by ref. Requires viewer.

    Seeds the missing starter objectives first (as ``proposed``) and commits
    them, so the register is never empty on first open. The summary counts
    unarchived objectives only.
    """
    out = await _answer(service.list_objectives(db, org_id, _today(), include_archived=include_archived))
    await db.commit()
    return out


@router.post(_OBJECTIVES, response_model=ObjectiveOut, status_code=201)
async def create_security_objective(
    org_id: UUID,
    data: ObjectiveCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Add an objective, always ``proposed``; ``metric`` sets its first metric. Requires editor."""
    out = await _answer(service.create_objective(db, org_id, data, _actor(request, membership), _today()))
    await db.commit()
    return out


@router.get(_OBJECTIVES + "/{objective_id}", response_model=ObjectiveOut)
async def get_security_objective(
    org_id: UUID,
    objective_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """One objective with its metric and today's reading. Requires viewer."""
    return await _answer(service.get_objective(db, org_id, objective_id, _today()))


@router.patch(_OBJECTIVES + "/{objective_id}", response_model=ObjectiveOut)
async def update_security_objective(
    org_id: UUID,
    objective_id: UUID,
    data: ObjectiveUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Edit an objective; ``archived`` archives or restores it. Requires editor.

    ``lifecycle_status`` (active, achieved, missed) is refused while the
    objective is still proposed: confirm it first. Once confirmed, changing
    ``objective_type`` or ``due_date`` takes an admin (403), because they
    decide how the objective is judged.
    """
    out = await _answer(service.update_objective(
        db, org_id, objective_id, data, _actor(request, membership), _today(), role=membership.role,
    ))
    await db.commit()
    return out


@router.put(_OBJECTIVES + "/{objective_id}/metric", response_model=ObjectiveOut)
async def set_security_objective_metric(
    org_id: UUID,
    objective_id: UUID,
    data: MetricSpec,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Set the measure and target. Requires editor while proposed, admin once confirmed.

    The metric in force is closed and a new version opened. On a confirmed
    objective the new version records the admin as the person who set the
    target.
    """
    out = await _answer(service.set_metric(
        db, org_id, objective_id, data, _actor(request, membership), membership.role, _today(),
    ))
    await db.commit()
    return out


@router.post(_OBJECTIVES + "/{objective_id}/confirm", response_model=ObjectiveOut)
async def confirm_security_objective(
    org_id: UUID,
    objective_id: UUID,
    data: ConfirmBody,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Confirm a proposed objective: it becomes ``active``. Requires admin.

    Needs a responsible team, a metric, and a target (live and logged) or a
    due date (milestone), already set or sent here.
    """
    out = await _answer(service.confirm_objective(
        db, org_id, objective_id, data, _actor(request, membership), membership.role, _today(),
    ))
    await db.commit()
    return out


@router.post(_OBJECTIVES + "/{objective_id}/evaluation", response_model=ObjectiveOut)
async def record_security_objective_evaluation(
    org_id: UUID,
    objective_id: UUID,
    data: EvaluationCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Record that the objective's results were evaluated, with a note. Requires editor."""
    out = await _answer(service.record_evaluation(
        db, org_id, objective_id, data.note, _actor(request, membership), _today(),
    ))
    await db.commit()
    return out


# ---------------------------------------------------------------------------
# ISMS metrics. The two fixed paths are declared before /{objective_id}.
# ---------------------------------------------------------------------------


@router.get(_METRICS + "/measures", response_model=List[MeasureOut])
async def list_isms_measures(
    org_id: UUID,
    text: Optional[str] = Query(None, max_length=300, description="Words to match; best matches first"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The measure library with each measure's value today. Requires viewer."""
    return await _answer(service.measures_today(db, org_id, text, _today()))


@router.post(_METRICS + "/wording", response_model=Wording)
async def isms_metric_wording(
    org_id: UUID,
    data: WordingRequest,
    membership: OrgMembership = Depends(require_org_role("viewer")),
):
    """The generated wording for a metric and target. Writes nothing. Requires viewer."""
    try:
        return service.wording_for(data)
    except ObjectiveError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.get("/isms-metrics/portfolio", response_model=Portfolio)
async def isms_metrics_portfolio(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Summary counts for every organisation the caller can open, from stored rows.

    The organisations are ``get_accessible_org_ids`` (direct memberships and
    active consultant relationships; a user API key gets its one
    organisation), never ids from the request. The static key in
    single-tenant mode is pinned to one organisation for every other route,
    so it sees that organisation only here too. No calculator runs, nothing
    is seeded and nothing is committed.
    """
    org_ids = await get_accessible_org_ids(user, db)
    if user.auth_method == "api_key":
        pinned = single_tenant.single_tenant_org_id()
        if pinned is not None:
            org_ids = [o for o in org_ids if str(o) == str(pinned)]
    return await portfolio.portfolio(db, org_ids, _today())


@router.get(_METRICS + "/measurement-tasks", response_model=List[MeasurementTask])
async def list_isms_measurement_tasks(
    org_id: UUID,
    assigned_to_me: bool = False,
    include_done: bool = False,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """This period's measurement tasks for logged metrics, due date first. Requires viewer.

    Derived from the objectives and their entries when read: nothing is
    stored, nothing is committed and no notification is raised. ``mine`` marks
    the tasks whose reminder goes to the caller; ``assigned_to_me`` keeps only
    those. Done tasks are returned only with ``include_done``.
    """
    return await measurement_tasks.measurement_tasks(
        db, org_id, _caller_id(membership), _today(), assigned_to_me=assigned_to_me, include_done=include_done,
    )


@router.get(_METRICS, response_model=ObjectiveList)
async def list_isms_metrics(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The ISMS metrics dashboard: unarchived objectives and their readings. Requires viewer.

    The same builder as the register, so both pages show the same reading.
    Seeds missing starters like the register does, and commits them.
    """
    out = await _answer(service.list_objectives(db, org_id, _today(), include_archived=False))
    await db.commit()
    return out


@router.get(_METRICS + "/{objective_id}", response_model=MetricDetail)
async def get_isms_metric(
    org_id: UUID,
    objective_id: UUID,
    breakdown_limit: Annotated[int, Query(ge=1, le=50)] = 10,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """One objective's metric: results newest first (at most 100), every version, and what holds it back.

    ``breakdown`` lists at most ``breakdown_limit`` records, worst first. Requires viewer.
    """
    return await _answer(service.metric_detail(db, org_id, objective_id, _today(), breakdown_limit=breakdown_limit))


@router.post(_METRICS + "/{objective_id}/results", response_model=ObjectiveOut, status_code=201)
async def log_isms_metric_result(
    org_id: UUID,
    objective_id: UUID,
    data: ResultCreate,
    request: Request,
    response: Response = None,  # injected by FastAPI; None when a test calls the handler directly
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Log a value for a logged or milestone metric. Requires editor.

    ``period_start`` defaults to the start of the current period. A
    correction is a new entry for the same period; the newest one counts.
    """
    return await _log_result(db, org_id, objective_id, data, request, response, membership)


async def _log_result(db, org_id, objective_id, data: ResultCreate, request, response, membership):
    out, written = await _answer(service.record_result(
        db, org_id, objective_id, data.value, _actor(request, membership), _today(),
        period_start=data.period_start, note=data.note, collector=data.collector,
    ))
    await db.commit()
    if not written and response is not None:
        # An identical repeat from a key: no result row and no field-level audit
        # rows; the request itself is still in the audit log (the middleware).
        response.status_code = 200
    return out


@router.post(_METRICS + "/by-ref/{ref}/results", response_model=ObjectiveOut, status_code=201)
async def log_isms_metric_result_by_ref(
    org_id: UUID,
    data: ResultCreate,
    request: Request,
    response: Response = None,  # injected by FastAPI; None when a test calls the handler directly
    ref: str = Path(..., pattern=r"^OBJ-\d{1,6}$"),
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Log a value addressed by the objective's ref (``OBJ-09``), for collectors. Requires editor.

    Same body and answer as the by-id route: 201 when a row is written, 200
    when an API key repeats the newest value (no result row and no
    field-level audit rows; the request itself is still in the audit log).
    """
    objective_id = await _answer(service.objective_id_for_ref(db, org_id, ref))
    return await _log_result(db, org_id, objective_id, data, request, response, membership)
