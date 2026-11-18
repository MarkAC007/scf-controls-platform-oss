# SPDX-License-Identifier: AGPL-3.0-only
"""The measure library: live ISMS metrics worked out from the registers.

Each ``Measure`` names its rule in words (``formula``), its unit and the
direction that is good, a suggested target and warning level, the clauses it
evidences and the smallest population worth judging. Its calculator reads the
organisation's own records and returns a ``Measurement``: the value, the
numerator and denominator behind it, the population in words and the
segments shown beside the figure.

Eight are ``outcome`` measures (what the ISMS achieves); seven are
``supporting`` measures (how well the platform is used). Supporting measures
are never seeded as starter objectives.

One row function per measure feeds both the calculator and the breakdown
("what is holding this back"), so the count on the tile and the length of the
list can never disagree: every record in scope becomes one ``Row`` that is
``good``, ``bad`` (counted against, and listed) or ``out`` (not in the
population, perhaps shown as a segment).

``today`` is always passed in; no calculator reads the clock. Unknown or
malformed settings fall back to the defaults. A missing record never makes a
measure read better: each rule says how an unknown counts.

Minimum population: 5 for measures over a stream of events (risks,
deficiencies, tasks, controls); 1 for the short, complete lists where every
member matters (suppliers, audits). A percentage over nobody is always
insufficient data (``status.derive_status``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import Date, and_, cast, exists, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogControl, SCFCatalogRisk
from models import (
    CapabilityTest,
    ControlTeamAssignment,
    CustomRiskDefinition,
    EvidenceAssessment,
    EvidenceCollectionTask,
    EvidenceFile,
    EvidenceTracking,
    IsmsProfile,
    Organization,
    OrganizationMember,
    OrganizationRiskProfile,
    RiskAssessment,
    ScopedControl,
    Team,
    User,
    Vendor,
    VendorActionItem,
    VendorAssessment,
)
from services import deficiencies
from services import target_maturity
from services.doc_gen.context import IMPLEMENTED_STATUSES
from services.risk_acceptance import STATE_APPROVED, STATE_EXPIRED, STATE_UNAPPROVED, acceptance_state

DASH = "—"
OUTCOME = "outcome"
SUPPORTING = "supporting"
GROUPS = (OUTCOME, SUPPORTING)


@dataclass(frozen=True)
class ParamSpec:
    key: str
    label: str
    kind: str  # "number" | "choice"
    default: Any
    choices: Tuple[Tuple[str, str], ...] = ()
    minimum: int = 1
    maximum: int = 3650

    def resolve(self, raw: Any) -> Any:
        if self.kind == "choice":
            allowed = {value for value, _ in self.choices}
            return raw if isinstance(raw, str) and raw in allowed else self.default
        if isinstance(raw, bool):
            return self.default
        try:
            number = int(raw)
        except (TypeError, ValueError):
            return self.default
        if isinstance(raw, float) and raw != number:
            return self.default
        return number if self.minimum <= number <= self.maximum else self.default

    def as_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"key": self.key, "label": self.label, "kind": self.kind, "default": self.default}
        if self.choices:
            out["choices"] = [{"value": v, "label": label} for v, label in self.choices]
        return out


@dataclass(frozen=True)
class Measurement:
    value: Optional[float]
    numerator: Optional[int]
    denominator: Optional[int]
    population: str
    segments: List[Dict[str, Any]] = field(default_factory=list)


GOOD, BAD, OUT = "good", "bad", "out"


@dataclass
class Row:
    """One record in a measure's scope, classified once for both the figure and the list."""

    key: str
    outcome: str  # GOOD | BAD | OUT
    group: str = ""  # for a BAD row: the part of the breakdown it is listed under
    segment: Optional[str] = None  # the Measurement segment it is counted in, if any
    sort: Tuple = ()
    data: Dict[str, Any] = field(default_factory=dict)


Calculator = Callable[[AsyncSession, UUID, Dict[str, Any], date], Awaitable[Measurement]]
RowFunction = Callable[[AsyncSession, UUID, Dict[str, Any], date], Awaitable[List[Row]]]
BreakdownFunction = Callable[[AsyncSession, UUID, Dict[str, Any], date, int], Awaitable[Optional[Dict[str, Any]]]]


@dataclass(frozen=True)
class Measure:
    key: str
    name: str  # may hold {param} placeholders
    description: str
    formula: str  # the rule in words; may hold {param} placeholders; no target
    unit: str
    direction: str
    suggested_target: Optional[float]
    suggested_warn: Optional[float]
    clauses: Tuple[str, ...]
    min_population: int
    source_label: str
    params: Tuple[ParamSpec, ...]
    calculator: Calculator
    breakdown: Optional[BreakdownFunction] = None
    group: str = OUTCOME


def resolve_params(measure: Measure, params: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The measure's settings: known keys only, each valid or its default."""
    raw = params if isinstance(params, dict) else {}
    return {spec.key: spec.resolve(raw.get(spec.key, spec.default)) for spec in measure.params}


def _percent(good: int, total: int) -> Optional[float]:
    return (100.0 * good / total) if total else None


def months_ago(today: date, months: int) -> date:
    """The same day ``months`` calendar months earlier (clamped to month end)."""
    index = today.year * 12 + (today.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    for day in (today.day, 30, 29, 28):
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return date(year, month, 28)


def _segment(label: str, count: int) -> Dict[str, Any]:
    return {"label": label, "count": int(count)}


def _segments(rows: List[Row], labels: Iterable[str]) -> List[Dict[str, Any]]:
    return [_segment(label, sum(1 for r in rows if r.segment == label)) for label in labels]


def _tally(rows: List[Row]) -> Tuple[int, int]:
    """``(good, in population)``: good and bad rows are in the population, out rows are not."""
    good = sum(1 for r in rows if r.outcome == GOOD)
    return good, good + sum(1 for r in rows if r.outcome == BAD)


def day_text(value: Optional[date]) -> str:
    """``18 Nov 2026``; a dash for no date."""
    if value is None:
        return DASH
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day} {value.strftime('%b %Y')}"


def _words(value: Optional[str], empty: str = DASH) -> str:
    """``in_progress`` to ``In progress``."""
    if not value:
        return empty
    text = str(value).replace("_", " ").strip()
    return text[:1].upper() + text[1:] if text else empty


def _raised(created_at: Optional[datetime]) -> Optional[date]:
    return created_at.date() if isinstance(created_at, datetime) else created_at


# ---------------------------------------------------------------------------
# Names, read inside the organisation only
# ---------------------------------------------------------------------------

def _user_name(user: User) -> str:
    return (user.display_name or "").strip() or user.email


async def member_names(db: AsyncSession, org_id: UUID, user_ids: Iterable[Optional[UUID]]) -> Dict[UUID, str]:
    """Display names of people who are members of ``org_id``; anyone else has no name here."""
    ids = {u for u in user_ids if u}
    if not ids:
        return {}
    rows = (await db.execute(
        select(User).join(OrganizationMember, and_(
            OrganizationMember.user_id == User.id, OrganizationMember.organization_id == org_id,
        )).where(User.id.in_(ids))
    )).scalars().all()
    return {u.id: _user_name(u) for u in rows}


async def _accountable_teams(db: AsyncSession, org_id: UUID, control_ids: Iterable[UUID]) -> Dict[UUID, str]:
    ids = {c for c in control_ids if c}
    if not ids:
        return {}
    rows = (await db.execute(
        select(ControlTeamAssignment.scoped_control_id, Team.name)
        .join(Team, and_(Team.id == ControlTeamAssignment.team_id, Team.organization_id == org_id))
        .where(
            ControlTeamAssignment.organization_id == org_id,
            ControlTeamAssignment.is_accountable.is_(True),
            ControlTeamAssignment.scoped_control_id.in_(ids),
        )
    )).all()
    return {cid: name for cid, name in rows}


async def _control_names(db: AsyncSession, scf_ids: Iterable[str]) -> Dict[str, str]:
    ids = {s for s in scf_ids if s}
    if not ids:
        return {}
    rows = (await db.execute(
        select(SCFCatalogControl.scf_id, SCFCatalogControl.control_name).where(SCFCatalogControl.scf_id.in_(ids))
    )).all()
    return {sid: name for sid, name in rows}


def _control_text(scf_id: str, names: Dict[str, str]) -> str:
    name = names.get(scf_id)
    return f"{scf_id} {name}" if name else scf_id


# ---------------------------------------------------------------------------
# Breakdowns
# ---------------------------------------------------------------------------

Renderer = Callable[[AsyncSession, UUID, List[Row], date], Awaitable[List[Tuple[Dict[str, str], Optional[Dict[str, Any]]]]]]


@dataclass(frozen=True)
class BreakdownSpec:
    noun: str  # "risks"; the first part of the title carries it
    groups: Tuple[str, ...]  # listing order of the bad groups
    columns: Tuple[Tuple[str, str], ...]
    register_kind: str
    register_label: str
    render: Renderer
    phrases: Tuple[Tuple[str, str], ...] = ()  # group -> title wording, when the group label does not read well


def _title(spec: BreakdownSpec, counts: List[Tuple[str, int]]) -> str:
    phrases = dict(spec.phrases)
    parts = []
    for i, (group, n) in enumerate(counts):
        words = phrases.get(group, group)
        parts.append(" ".join(p for p in (str(n), spec.noun if i == 0 else "", words) if p))
    return " · ".join(parts)


def _breakdown(rows_fn: RowFunction, spec: BreakdownSpec) -> BreakdownFunction:
    """The breakdown of a row function: its bad rows, worst first, at most ``limit``; None when there are none."""

    async def run(db: AsyncSession, org_id: UUID, params: Dict[str, Any], today: date, limit: int):
        bad = [r for r in await rows_fn(db, org_id, params, today) if r.outcome == BAD]
        if not bad:
            return None
        order = {g: i for i, g in enumerate(spec.groups)}
        bad.sort(key=lambda r: (order.get(r.group, len(order)), r.sort, r.key))
        counts = [(g, sum(1 for r in bad if r.group == g)) for g in spec.groups]
        counts += [(g, sum(1 for r in bad if r.group == g)) for g in sorted({r.group for r in bad} - set(order))]
        shown = bad[:max(1, int(limit))]
        rendered = await spec.render(db, org_id, shown, today)
        return {
            "title": _title(spec, [(g, n) for g, n in counts if n]),
            "columns": [{"key": k, "label": label} for k, label in spec.columns],
            "rows": [
                {"key": r.key, "group": r.group, "cells": cells, "link": link}
                for r, (cells, link) in zip(shown, rendered)
            ],
            "total": len(bad),
            "register": {"kind": spec.register_kind, "key": None},
            "register_label": spec.register_label,
        }

    return run


def _calculator(rows_fn: RowFunction, build: Callable[[List[Row], Dict[str, Any]], Measurement]) -> Calculator:
    async def run(db: AsyncSession, org_id: UUID, params: Dict[str, Any], today: date) -> Measurement:
        return build(await rows_fn(db, org_id, params, today), params)

    return run


# ---------------------------------------------------------------------------
# Risks
# ---------------------------------------------------------------------------

_LEVEL_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_TREATMENT_CLOSED = ("treated", "monitoring", "accepted")


def _stamp(assessment) -> datetime:
    return assessment.updated_at or assessment.created_at or datetime.min


async def _risk_universe(db: AsyncSession, org_id: UUID, assessments) -> set:
    """The register the risk summary counts (``api/risk_assessments.py`` risk-summary)."""
    catalog_codes = set((await db.execute(
        select(SCFCatalogRisk.code).where(SCFCatalogRisk.status == "active")
    )).scalars().all())
    custom_codes = set((await db.execute(
        select(CustomRiskDefinition.risk_code).where(CustomRiskDefinition.organization_id == org_id)
    )).scalars().all())
    if not catalog_codes:
        catalog_codes = {a.risk_code for a in assessments if not a.risk_code.startswith("R-ORG-")}
    return catalog_codes | custom_codes


async def _risks(db: AsyncSession, org_id: UUID, today: date) -> List[Dict[str, Any]]:
    """Every risk in the register with its newest assessment (or None) and its level against appetite."""
    assessments = (await db.execute(
        select(RiskAssessment).where(RiskAssessment.organization_id == org_id)
    )).scalars().all()
    newest: Dict[str, Any] = {}
    for a in assessments:
        held = newest.get(a.risk_code)
        if held is None or _stamp(a) > _stamp(held):
            newest[a.risk_code] = a
    profile = (await db.execute(
        select(OrganizationRiskProfile).where(OrganizationRiskProfile.organization_id == org_id)
    )).scalar_one_or_none()
    low, medium, high = (profile.low_max, profile.medium_max, profile.high_max) if profile else (4, 9, 16)
    acceptable = _LEVEL_RANK.get((profile.acceptable_risk_level if profile else "medium") or "medium", 1)

    out = []
    for code in sorted(await _risk_universe(db, org_id, assessments)):
        a = newest.get(code)
        item: Dict[str, Any] = {"code": code, "a": a, "assessed": a is not None and a.inherent_risk_score is not None,
                                "accepted": a is not None and acceptance_state(a, today) == STATE_APPROVED,
                                "level": None, "score": None, "within": False}
        if item["assessed"]:
            residual = a.get_residual_risk_level(low, medium, high)
            item["level"] = residual or a.get_inherent_risk_level(low, medium, high)
            item["score"] = a.residual_risk_score if residual else a.inherent_risk_score
            item["within"] = _LEVEL_RANK.get(item["level"], 3) <= acceptable
        out.append(item)
    return out


async def _risk_within_appetite_rows(db, org_id, params, today) -> List[Row]:
    rows = []
    for r in await _risks(db, org_id, today):
        code = r["code"]
        if r["accepted"]:
            rows.append(Row(code, GOOD, segment="accepted", data=r))
        elif not r["assessed"]:
            rows.append(Row(code, BAD, "not assessed", "not assessed", data=r))
        elif r["within"]:
            rows.append(Row(code, GOOD, data=r))
        else:
            rows.append(Row(code, BAD, "above appetite", "above appetite", sort=(-(r["score"] or 0),), data=r))
    return rows


def _risk_within_appetite_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} risks",
        segments=_segments(rows, ("not assessed", "above appetite", "accepted")),
    )


async def _risk_titles(db: AsyncSession, org_id: UUID, codes: Iterable[str]) -> Dict[str, str]:
    codes = set(codes)
    titles = dict((await db.execute(
        select(SCFCatalogRisk.code, SCFCatalogRisk.title).where(SCFCatalogRisk.code.in_(codes))
    )).all()) if codes else {}
    if codes:
        titles.update(dict((await db.execute(
            select(CustomRiskDefinition.risk_code, CustomRiskDefinition.title).where(
                CustomRiskDefinition.organization_id == org_id, CustomRiskDefinition.risk_code.in_(codes),
            )
        )).all()))
    return titles


def _acceptance_text(a, today: date) -> str:
    state = acceptance_state(a, today) if a is not None else None
    if state is None:
        return "None"
    if state == STATE_EXPIRED:
        return f"Expired {day_text(a.acceptance_expires_on)}" if a.acceptance_expires_on else "Expired"
    if state == STATE_UNAPPROVED:
        return "Not approved"
    return f"Approved until {day_text(a.acceptance_expires_on)}"


async def _risk_context(db, org_id, shown: List[Row]):
    titles = await _risk_titles(db, org_id, [r.key for r in shown])
    owners = await member_names(db, org_id, [r.data["a"].owner_user_id for r in shown if r.data.get("a") is not None])
    return titles, owners


def _risk_cell(r: Row, titles: Dict[str, str]) -> str:
    title = titles.get(r.key)
    return f"{r.key} {title}" if title else r.key


def _owner_cell(a, owners: Dict[UUID, str]) -> str:
    return owners.get(a.owner_user_id, DASH) if a is not None and a.owner_user_id else DASH


async def _render_risks(db, org_id, shown: List[Row], today: date):
    titles, owners = await _risk_context(db, org_id, shown)
    out = []
    for r in shown:
        a = r.data.get("a")
        level = f"{_words(r.data['level'])} · {r.data['score']}" if r.data.get("assessed") else "Not assessed"
        out.append(({
            "risk": _risk_cell(r, titles), "level": level,
            "treatment": _words(a.treatment_status) if a is not None else DASH,
            "owner": _owner_cell(a, owners), "acceptance": _acceptance_text(a, today),
        }, {"kind": "risk", "key": r.key}))
    return out


_RISK_COLUMNS = (("risk", "Risk"), ("level", "Level"), ("treatment", "Treatment"), ("owner", "Owner"),
                 ("acceptance", "Acceptance"))


async def _risks_assessed_rows(db, org_id, params, today) -> List[Row]:
    return [
        Row(r["code"], GOOD, data=r) if r["assessed"] else Row(r["code"], BAD, "not assessed", "not assessed", data=r)
        for r in await _risks(db, org_id, today)
    ]


def _risks_assessed_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} risks assessed", segments=_segments(rows, ("not assessed",)),
    )


def _treatment_open(a) -> bool:
    """A treatment is under way: not treated, monitored or accepted, and started or planned."""
    if a is None or a.treatment_status in _TREATMENT_CLOSED:
        return False
    return a.treatment_status == "treating" or bool((a.treatment_plan or "").strip()) or a.treatment_due_date is not None


async def _risk_treatments_rows(db, org_id, params, today) -> List[Row]:
    rows = []
    for r in await _risks(db, org_id, today):
        a = r["a"]
        if not _treatment_open(a):
            rows.append(Row(r["code"], OUT, data=r))
        elif a.treatment_due_date is None:
            rows.append(Row(r["code"], BAD, "no due date", "no due date", data=r))
        elif a.treatment_due_date < today:
            rows.append(Row(r["code"], BAD, "overdue", "overdue", sort=(a.treatment_due_date,), data=r))
        else:
            rows.append(Row(r["code"], GOOD, data=r))
    return rows


def _risk_treatments_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} treatments in date", segments=_segments(rows, ("overdue", "no due date")),
    )


async def _render_treatments(db, org_id, shown: List[Row], today: date):
    titles, owners = await _risk_context(db, org_id, shown)
    return [({
        "risk": _risk_cell(r, titles), "treatment": _words(r.data["a"].treatment_status),
        "due": day_text(r.data["a"].treatment_due_date), "owner": _owner_cell(r.data["a"], owners),
    }, {"kind": "risk", "key": r.key}) for r in shown]


# ---------------------------------------------------------------------------
# Deficiencies
# ---------------------------------------------------------------------------

async def _open_deficiencies(db: AsyncSession, org_id: UUID):
    """Open deficiencies with whether each is material, by the same rule as ``deficiencies.count_open``."""
    return (await db.execute(
        select(
            VendorActionItem.id, VendorActionItem.title, VendorActionItem.priority, VendorActionItem.owner_name,
            VendorActionItem.owner_user_id, VendorActionItem.due_date, VendorActionItem.created_at,
            deficiencies.material_deficiency().label("material"),
        )
        .outerjoin(ScopedControl, and_(
            ScopedControl.id == VendorActionItem.scoped_control_id, ScopedControl.organization_id == org_id,
        ))
        .where(VendorActionItem.organization_id == org_id, deficiencies.is_deficiency(), deficiencies.is_open())
    )).all()


def _deficiency_data(d, today: date) -> Dict[str, Any]:
    raised = _raised(d.created_at)
    return {"title": d.title, "priority": d.priority, "owner_name": d.owner_name, "owner_user_id": d.owner_user_id,
            "due": d.due_date, "raised": raised, "age": (today - raised).days if raised else None}


async def _open_material_rows(db, org_id, params, today) -> List[Row]:
    rows = []
    for d in await _open_deficiencies(db, org_id):
        data = _deficiency_data(d, today)
        if d.material:
            rows.append(Row(str(d.id), BAD, "material", sort=(data["raised"] or date.max,), data=data))
        else:
            rows.append(Row(str(d.id), GOOD, data=data))
    return rows


def _open_material_build(rows: List[Row], params) -> Measurement:
    material = sum(1 for r in rows if r.outcome == BAD)
    return Measurement(
        value=float(material), numerator=material, denominator=len(rows),
        population=f"{material} of {len(rows)} open are material", segments=[],
    )


async def _render_open_deficiencies(db, org_id, shown: List[Row], today: date):
    owners = await member_names(db, org_id, [r.data["owner_user_id"] for r in shown])
    return [({
        "deficiency": r.data["title"], "priority": _words(r.data["priority"]),
        "owner": owners.get(r.data["owner_user_id"]) or r.data["owner_name"] or DASH,
        "due": day_text(r.data["due"]), "age": DASH if r.data["age"] is None else f"{r.data['age']} days",
    }, {"kind": "deficiency", "key": r.key}) for r in shown]


_OPEN_DEFICIENCY_COLUMNS = (("deficiency", "Deficiency"), ("priority", "Priority"), ("owner", "Owner"),
                            ("due", "Due"), ("age", "Age"))


async def _corrected_rows(db, org_id, params, today) -> List[Row]:
    days, window, priority = params["days"], params["window_months"], params["priority"]
    raised_on = cast(VendorActionItem.created_at, Date)
    conds = [
        VendorActionItem.organization_id == org_id,
        deficiencies.is_deficiency(),
        raised_on >= months_ago(today, window),
        raised_on <= today - timedelta(days=days),
    ]
    if priority != "any":
        conds.append(VendorActionItem.priority == priority)
    found = (await db.execute(
        select(VendorActionItem.id, VendorActionItem.title, VendorActionItem.status,
               VendorActionItem.completed_date, raised_on.label("raised")).where(and_(*conds))
    )).all()
    rows = []
    for d in found:
        data = {"title": d.title, "status": d.status, "raised": d.raised, "done": d.completed_date}
        if d.status == deficiencies.CANCELLED:
            rows.append(Row(str(d.id), OUT, segment="cancelled", data=data))
        elif d.status == deficiencies.COMPLETED and d.completed_date is not None:
            taken = (d.completed_date - d.raised).days
            data["taken"] = taken
            if taken <= days:
                rows.append(Row(str(d.id), GOOD, data=data))
            else:
                rows.append(Row(str(d.id), BAD, "completed late", sort=(-taken,), data=data))
        elif d.status == deficiencies.COMPLETED:
            rows.append(Row(str(d.id), BAD, "completed with no date", sort=(d.raised,), data=data))
        else:
            data["open_days"] = (today - d.raised).days
            rows.append(Row(str(d.id), BAD, "still open", sort=(d.raised,), data=data))
    return rows


def _corrected_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} raised {params['days']}+ days ago",
        segments=_segments(rows, ("cancelled",)),
    )


async def _render_corrected(db, org_id, shown: List[Row], today: date):
    out = []
    for r in shown:
        if "taken" in r.data:
            took = f"{r.data['taken']} days taken"
        elif "open_days" in r.data:
            took = f"{r.data['open_days']} days open"
        else:
            took = "No completion date"
        out.append(({"deficiency": r.data["title"], "raised": day_text(r.data["raised"]),
                     "status": _words(r.data["status"]), "days": took}, {"kind": "deficiency", "key": r.key}))
    return out


async def _older_than_rows(db, org_id, params, today) -> List[Row]:
    days = params["days"]
    rows = []
    for d in await _open_deficiencies(db, org_id):
        data = _deficiency_data(d, today)
        # An open deficiency with no raised date has no known age: it counts against.
        if data["age"] is None or data["age"] > days:
            rows.append(Row(str(d.id), BAD, "older", sort=(data["raised"] or date.min,), data=data))
        else:
            rows.append(Row(str(d.id), GOOD, data=data))
    return rows


def _older_than_build(rows: List[Row], params) -> Measurement:
    old = sum(1 for r in rows if r.outcome == BAD)
    return Measurement(
        value=float(old), numerator=old, denominator=len(rows),
        population=f"{old} of {len(rows)} open are older than {params['days']} days", segments=[],
    )


async def _average_age(db, org_id, params, today) -> Measurement:
    ages = [_deficiency_data(d, today)["age"] for d in await _open_deficiencies(db, org_id)]
    known = [a for a in ages if a is not None]
    # No open deficiencies reads 0. One with no raised date cannot be aged and is not averaged in.
    value = round(sum(known) / len(known), 1) if known else 0.0
    return Measurement(
        value=float(value), numerator=None, denominator=len(ages),
        population=f"{len(ages)} open deficiencies", segments=[],
    )


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

async def _evidence_rows(db, org_id, params, today) -> List[Row]:
    window = params["window_days"]
    found = (await db.execute(
        select(EvidenceCollectionTask.id, EvidenceCollectionTask.status, EvidenceCollectionTask.completed_date,
               EvidenceCollectionTask.due_date, EvidenceCollectionTask.assigned_user_id,
               EvidenceCollectionTask.title, EvidenceTracking.evidence_id)
        .outerjoin(EvidenceTracking, and_(
            EvidenceTracking.id == EvidenceCollectionTask.evidence_tracking_id,
            EvidenceTracking.organization_id == org_id,
        ))
        .where(
            EvidenceCollectionTask.organization_id == org_id,
            EvidenceCollectionTask.due_date >= today - timedelta(days=window),
            EvidenceCollectionTask.due_date <= today - timedelta(days=1),
        )
    )).all()
    rows = []
    for t in found:
        data = {"status": t.status, "done": t.completed_date, "due": t.due_date, "assignee": t.assigned_user_id,
                "evidence_id": t.evidence_id, "title": t.title}
        key, sort = str(t.id), (t.due_date,)
        if t.status == "wont_do":
            rows.append(Row(key, OUT, segment="won't do", data=data))
        elif t.status == "completed" and t.completed_date is None:
            rows.append(Row(key, BAD, "completed with no date", "completed with no date", sort, data))
        elif t.status == "completed" and t.completed_date <= t.due_date:
            rows.append(Row(key, GOOD, data=data))
        elif t.status == "completed":
            rows.append(Row(key, BAD, "completed late", sort=sort, data=data))
        else:
            rows.append(Row(key, BAD, "not completed", sort=sort, data=data))
    return rows


def _evidence_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} tasks due in the last {params['window_days']} days",
        segments=_segments(rows, ("won't do", "completed with no date")),
    )


async def _render_evidence(db, org_id, shown: List[Row], today: date):
    names = await member_names(db, org_id, [r.data["assignee"] for r in shown])
    return [({
        "evidence": r.data["evidence_id"] or r.data["title"] or DASH, "due": day_text(r.data["due"]),
        "status": _words(r.data["status"]), "completed": day_text(r.data["done"]),
        "assignee": names.get(r.data["assignee"], DASH) if r.data["assignee"] else DASH,
    }, {"kind": "evidence", "key": r.data["evidence_id"]} if r.data["evidence_id"] else None) for r in shown]


async def _ai_verdict_rows(db, org_id, params, today) -> List[Row]:
    found = (await db.execute(
        select(EvidenceAssessment.id, EvidenceAssessment.evidence_id, EvidenceAssessment.status,
               EvidenceAssessment.assessed_at, EvidenceFile.filename)
        .join(EvidenceFile, and_(EvidenceFile.id == EvidenceAssessment.evidence_file_id,
                                 EvidenceFile.organization_id == org_id))
        .where(
            EvidenceAssessment.organization_id == org_id,
            EvidenceAssessment.status.in_(REVIEWABLE_STATUSES),
            EvidenceAssessment.review_decision.is_(None),
            EvidenceFile.is_deleted.is_(False),
        )
    )).all()
    return [Row(str(a.id), BAD, "awaiting review", sort=(a.assessed_at or datetime.min,),
                data={"evidence_id": a.evidence_id, "status": a.status, "assessed": a.assessed_at,
                      "filename": a.filename}) for a in found]


#: Terminal AI verdicts a person can confirm or override; the review queue's
#: own list (``api/evidence_assessment.py`` ``REVIEWABLE_STATUSES``), copied so
#: a service does not import a router. A test pins the two together.
REVIEWABLE_STATUSES = ("sufficient", "partial", "insufficient", "unassessable")


def _ai_verdict_build(rows: List[Row], params) -> Measurement:
    n = len(rows)
    return Measurement(value=float(n), numerator=n, denominator=n,
                       population=f"{n} AI verdicts awaiting review", segments=[])


async def _render_ai_verdicts(db, org_id, shown: List[Row], today: date):
    return [({
        "evidence": r.data["evidence_id"], "file": r.data["filename"], "verdict": _words(r.data["status"]),
        "assessed": day_text(r.data["assessed"]),
    }, {"kind": "evidence", "key": r.data["evidence_id"]}) for r in shown]


# ---------------------------------------------------------------------------
# Suppliers
# ---------------------------------------------------------------------------

_IN_USE = ("active", "approved", "under_review")


async def _supplier_rows(db, org_id, params, today) -> List[Row]:
    cutoff = months_ago(today, params["months"])
    assessed_on = func.coalesce(cast(VendorAssessment.completed_at, Date), VendorAssessment.assessment_date)
    current = exists().where(
        VendorAssessment.vendor_id == Vendor.id,
        VendorAssessment.status == "completed",
        assessed_on >= cutoff,
        assessed_on <= today,
    )
    last = (
        select(func.max(assessed_on))
        .where(VendorAssessment.vendor_id == Vendor.id, VendorAssessment.status == "completed", assessed_on <= today)
        .correlate(Vendor).scalar_subquery()
    )
    found = (await db.execute(
        select(Vendor.id, Vendor.name, Vendor.status, Vendor.criticality, current.label("current"),
               last.label("last")).where(
            Vendor.organization_id == org_id, Vendor.criticality.in_(("high", "critical")),
        )
    )).all()
    rows = []
    for v in found:
        data = {"name": v.name, "criticality": v.criticality, "last": v.last}
        if v.status not in _IN_USE:
            rows.append(Row(str(v.id), OUT, segment="not in use", data=data))
        elif v.current:
            rows.append(Row(str(v.id), GOOD, data=data))
        else:
            rows.append(Row(str(v.id), BAD, "without a current assessment",
                            sort=(0 if v.criticality == "critical" else 1, (v.name or "").lower()), data=data))
    return rows


def _supplier_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} high and critical suppliers",
        segments=_segments(rows, ("not in use",)),
    )


async def _render_suppliers(db, org_id, shown: List[Row], today: date):
    return [({
        "supplier": r.data["name"], "criticality": _words(r.data["criticality"]),
        "last_assessment": day_text(r.data["last"]) if r.data["last"] else "None",
    }, {"kind": "vendor", "key": r.key}) for r in shown]


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

def _accountable_team_exists():
    """EXISTS, not a join: a control with several team rows is still one control (``services/journey.py``)."""
    return (
        select(literal(1)).select_from(ControlTeamAssignment).where(
            ControlTeamAssignment.scoped_control_id == ScopedControl.id,
            ControlTeamAssignment.organization_id == ScopedControl.organization_id,
            ControlTeamAssignment.is_accountable.is_(True),
        ).exists()
    )


async def _scoped_controls(db: AsyncSession, org_id: UUID, *extra):
    return (await db.execute(
        select(ScopedControl.id, ScopedControl.scf_id, ScopedControl.implementation_status, *extra)
        .where(ScopedControl.organization_id == org_id, ScopedControl.selected.is_(True))
    )).all()


async def _controls_implemented_rows(db, org_id, params, today) -> List[Row]:
    return [
        Row(c.scf_id, GOOD, data={"id": c.id, "status": c.implementation_status})
        if c.implementation_status in IMPLEMENTED_STATUSES else
        Row(c.scf_id, BAD, "not implemented", sort=(c.scf_id,), data={"id": c.id, "status": c.implementation_status})
        for c in await _scoped_controls(db, org_id)
    ]


def _controls_build(population: str) -> Callable[[List[Row], Dict[str, Any]], Measurement]:
    def build(rows: List[Row], params) -> Measurement:
        good, total = _tally(rows)
        return Measurement(value=_percent(good, total), numerator=good, denominator=total,
                           population=f"{good} of {total} {population}",
                           segments=_segments(rows, ("no target set",)) if population == "controls with a target" else [])
    return build


async def _render_controls(db, org_id, shown: List[Row], today: date):
    names = await _control_names(db, [r.key for r in shown])
    teams = await _accountable_teams(db, org_id, [r.data["id"] for r in shown])
    return [({
        "control": _control_text(r.key, names), "status": _words(r.data["status"], "Not set"),
        "team": teams.get(r.data["id"], DASH),
    }, {"kind": "control", "key": r.key}) for r in shown]


async def _controls_with_team_rows(db, org_id, params, today) -> List[Row]:
    return [
        Row(c.scf_id, GOOD if c.owned else BAD, "" if c.owned else "without an accountable team", sort=(c.scf_id,),
            data={"id": c.id, "status": c.implementation_status})
        for c in await _scoped_controls(db, org_id, _accountable_team_exists().label("owned"))
    ]


async def _render_unowned_controls(db, org_id, shown: List[Row], today: date):
    names = await _control_names(db, [r.key for r in shown])
    return [({"control": _control_text(r.key, names), "status": _words(r.data["status"], "Not set")},
             {"kind": "control", "key": r.key}) for r in shown]


async def _maturity_rows(db, org_id, params, today) -> List[Row]:
    settings = await db.scalar(select(Organization.settings).where(Organization.id == org_id))
    targets = target_maturity.stored_domain_targets(settings)
    rows = []
    for c in await _scoped_controls(db, org_id, ScopedControl.target_maturity_override, ScopedControl.maturity_level):
        target, _ = target_maturity.resolve_target(c.scf_id, c.target_maturity_override, targets)
        current = target_maturity.normalise_level(c.maturity_level)
        data = {"id": c.id, "target": target, "current": current}
        if target is None:
            rows.append(Row(c.scf_id, OUT, segment="no target set", data=data))
        elif current is None:
            rows.append(Row(c.scf_id, BAD, "with no current level", sort=(c.scf_id,), data=data))
        elif current < target:  # L0..L5 are single digits, so string order is level order
            rows.append(Row(c.scf_id, BAD, "below target", sort=(c.scf_id,), data=data))
        else:
            rows.append(Row(c.scf_id, GOOD, data=data))
    return rows


async def _render_maturity(db, org_id, shown: List[Row], today: date):
    names = await _control_names(db, [r.key for r in shown])
    return [({"control": _control_text(r.key, names), "current": r.data["current"] or "Not rated",
              "target": r.data["target"]}, {"kind": "control", "key": r.key}) for r in shown]


# ---------------------------------------------------------------------------
# Capability tests
# ---------------------------------------------------------------------------

async def _response_test_rows(db, org_id, params, today) -> List[Row]:
    found = (await db.execute(
        select(CapabilityTest.id, CapabilityTest.title, CapabilityTest.test_type, CapabilityTest.tested_on,
               CapabilityTest.result).where(
            CapabilityTest.organization_id == org_id,
            CapabilityTest.test_type.in_(("incident_response", "continuity")),
            CapabilityTest.tested_on >= months_ago(today, params["months"]),
            CapabilityTest.tested_on <= today,
        )
    )).all()
    rows = []
    for t in found:
        data = {"title": t.title, "type": t.test_type, "on": t.tested_on, "result": t.result}
        if t.result in ("passed", "passed_with_findings"):
            rows.append(Row(str(t.id), GOOD, data=data))
        elif t.result == "failed":
            rows.append(Row(str(t.id), BAD, "failed", "failed", sort=(-t.tested_on.toordinal(),), data=data))
        else:
            rows.append(Row(str(t.id), OUT, data=data))
    return rows


def _response_test_build(rows: List[Row], params) -> Measurement:
    passed = sum(1 for r in rows if r.outcome == GOOD)
    return Measurement(
        value=float(passed), numerator=passed, denominator=len(rows),
        population=f"{passed} passed tests in the last {params['months']} months",
        segments=_segments(rows, ("failed",)),
    )


async def _render_tests(db, org_id, shown: List[Row], today: date):
    return [({"test": r.data["title"], "type": _words(r.data["type"]), "date": day_text(r.data["on"]),
              "result": _words(r.data["result"])}, {"kind": "capability_test", "key": r.key}) for r in shown]


# ---------------------------------------------------------------------------
# Internal audit programme
# ---------------------------------------------------------------------------

_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def planned_date(entry: Dict[str, Any]) -> Optional[date]:
    """The entry's ``planned_date`` as a date; anything that is not a valid ``YYYY-MM-DD`` is undated."""
    raw = entry.get("planned_date")
    if not isinstance(raw, str) or not _ISO_DAY.match(raw.strip()):
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError:
        return None


async def _audit_rows(db, org_id, params, today) -> List[Row]:
    """Each audit not cancelled falls in exactly one place (contract section 3).

    Undated: in the population. Planned on or before today and not completed:
    in, however old. Planned on or before today and completed: in when planned
    within the last ``months``, else it has left. Planned after today: in only
    when already completed, else counted as ``not yet due``.
    """
    raw = await db.scalar(select(IsmsProfile.audit_programme).where(IsmsProfile.organization_id == org_id))
    since = months_ago(today, params["months"])
    rows = []
    for i, e in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(e, dict):
            continue
        key, planned, done = str(i), planned_date(e), e.get("status") == "completed"
        data = {"entry": e, "planned": planned}
        sort = (0, planned.toordinal(), i) if planned else (1, 0, i)
        if e.get("status") == "cancelled":
            rows.append(Row(key, OUT, segment="cancelled", data=data))
        elif planned is not None and planned > today and not done:
            rows.append(Row(key, OUT, segment="not yet due", data=data))
        elif planned is not None and planned <= today and done and planned < since:
            rows.append(Row(key, OUT, data=data))  # old history: it has left the window
        elif done:
            rows.append(Row(key, GOOD, data=data))
        else:
            # An undated audit counts as due. Its own group says why it is listed, so the fix (a planned date) is plain.
            rows.append(Row(key, BAD, "not completed" if planned is not None else "no planned date", sort=sort, data=data))
    return rows


def _audit_build(rows: List[Row], params) -> Measurement:
    good, total = _tally(rows)
    return Measurement(
        value=_percent(good, total), numerator=good, denominator=total,
        population=f"{good} of {total} audits due in the last {params['months']} months",
        segments=_segments(rows, ("cancelled", "not yet due")),
    )


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else DASH


async def _render_audits(db, org_id, shown: List[Row], today: date):
    out = []
    for r in shown:
        e = r.data["entry"]
        planned = day_text(r.data["planned"]) if r.data["planned"] else _text(e.get("planned_period"))
        out.append(({"area": _text(e.get("area")), "planned": planned, "auditor": _text(e.get("auditor")),
                     "status": _words(e.get("status") if isinstance(e.get("status"), str) else None)},
                    {"kind": "audit", "key": None}))
    return out


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PRIORITIES = (("any", "Any priority"), ("critical", "Critical"), ("high", "High"), ("medium", "Medium"), ("low", "Low"))

_CONTROL_COLUMNS = (("control", "Control"), ("status", "Status"), ("team", "Team"))


def _live(rows_fn: RowFunction, build, spec: Optional[BreakdownSpec]) -> Dict[str, Any]:
    return {"calculator": _calculator(rows_fn, build), "breakdown": _breakdown(rows_fn, spec) if spec else None}


MEASURES: Tuple[Measure, ...] = (
    Measure(
        key="risk_within_appetite", name="Risks within appetite",
        description="Share of the risk register at or below the acceptable risk level, or formally accepted.",
        formula=("Risks whose residual level (inherent when no residual score is set) is at or below the "
                 "acceptable risk level, or whose acceptance is approved and in force, as a share of every "
                 "risk in the register; an unassessed risk counts against."),
        unit="percent", direction="at_least", suggested_target=100, suggested_warn=90,
        clauses=("6.1.2", "6.1.3", "8.2", "8.3"), min_population=5, source_label="Risk Register",
        params=(),
        **_live(_risk_within_appetite_rows, _risk_within_appetite_build, BreakdownSpec(
            "risks", ("above appetite", "not assessed"), _RISK_COLUMNS, "risk", "Risk Register", _render_risks)),
    ),
    Measure(
        key="open_material_deficiencies", name="Open material deficiencies",
        description="Deficiencies against material controls that are still open.",
        formula="The number of open deficiencies raised against a material control.",
        unit="count", direction="at_most", suggested_target=0, suggested_warn=3,
        clauses=("10.2", "9.1"), min_population=0, source_label="Deficiencies (POA&M)",
        params=(),
        **_live(_open_material_rows, _open_material_build, BreakdownSpec(
            "open material deficiencies", ("material",), _OPEN_DEFICIENCY_COLUMNS, "deficiency", "Deficiencies",
            _render_open_deficiencies, phrases=(("material", ""),))),
    ),
    Measure(
        key="deficiencies_corrected_in_time", name="Deficiencies corrected within {days} days",
        description="Share of deficiencies corrected within the agreed number of days of being raised.",
        formula=("Deficiencies raised in the last {window_months} months and at least {days} days ago, "
                 "not cancelled, that were completed within {days} days of being raised, as a share of all of them."),
        unit="percent", direction="at_least", suggested_target=90, suggested_warn=75,
        clauses=("10.2",), min_population=5, source_label="Deficiencies (POA&M)",
        params=(
            ParamSpec("days", "Days to correct", "number", 90),
            ParamSpec("window_months", "Look back (months)", "number", 12, maximum=120),
            ParamSpec("priority", "Priority", "choice", "any", choices=_PRIORITIES),
        ),
        **_live(_corrected_rows, _corrected_build, BreakdownSpec(
            "deficiencies", ("still open", "completed late", "completed with no date"),
            (("deficiency", "Deficiency"), ("raised", "Raised"), ("status", "Status"), ("days", "Days")),
            "deficiency", "Deficiencies", _render_corrected)),
    ),
    Measure(
        key="evidence_collected_on_time", name="Evidence collected on time",
        description="Share of evidence collection tasks completed by their due date.",
        formula=("Evidence tasks due in the last {window_days} days, other than those marked won't do, "
                 "completed on or before their due date, as a share of all of them."),
        unit="percent", direction="at_least", suggested_target=90, suggested_warn=75,
        clauses=("9.1", "7.5"), min_population=5, source_label="Evidence tasks",
        params=(ParamSpec("window_days", "Look back (days)", "number", 90),),
        **_live(_evidence_rows, _evidence_build, BreakdownSpec(
            "tasks", ("not completed", "completed late", "completed with no date"),
            (("evidence", "Evidence"), ("due", "Due"), ("status", "Status"), ("completed", "Completed"),
             ("assignee", "Assignee")),
            "evidence", "Evidence tasks", _render_evidence)),
    ),
    Measure(
        key="supplier_assurance_current", name="Suppliers with current assurance",
        description="Share of high and critical suppliers in use with a completed assessment in date.",
        formula=("High and critical suppliers in use with an assessment completed in the last {months} months, "
                 "as a share of all high and critical suppliers in use."),
        unit="percent", direction="at_least", suggested_target=95, suggested_warn=80,
        clauses=("8.1", "A.5.19", "A.5.20", "A.5.21", "A.5.22"), min_population=1, source_label="Vendor register",
        params=(ParamSpec("months", "Assessment valid for (months)", "number", 12, maximum=120),),
        **_live(_supplier_rows, _supplier_build, BreakdownSpec(
            "suppliers", ("without a current assessment",),
            (("supplier", "Supplier"), ("criticality", "Criticality"), ("last_assessment", "Last assessment")),
            "vendor", "Vendors", _render_suppliers)),
    ),
    Measure(
        key="controls_implemented", name="Controls implemented",
        description="Share of the controls in scope that are implemented.",
        formula="Controls in scope recorded as implemented, ready for review or monitored, as a share of all controls in scope.",
        unit="percent", direction="at_least", suggested_target=None, suggested_warn=None,
        clauses=("6.1.3", "8.1"), min_population=5, source_label="Controls in scope",
        params=(),
        **_live(_controls_implemented_rows, _controls_build("controls"), BreakdownSpec(
            "controls", ("not implemented",), _CONTROL_COLUMNS, "control", "Controls", _render_controls)),
    ),
    Measure(
        key="response_tests_passed", name="Response tests passed",
        description="Incident response and continuity tests passed.",
        formula=("The number of incident response and continuity tests run in the last {months} months "
                 "that passed, with or without findings."),
        unit="count", direction="at_least", suggested_target=2, suggested_warn=1,
        clauses=("8.1", "A.5.24", "A.5.26", "A.5.29", "A.5.30"), min_population=0, source_label="Capability tests",
        params=(ParamSpec("months", "Look back (months)", "number", 12, maximum=120),),
        **_live(_response_test_rows, _response_test_build, BreakdownSpec(
            "tests", ("failed",), (("test", "Test"), ("type", "Type"), ("date", "Date"), ("result", "Result")),
            "capability_test", "Capability tests", _render_tests)),
    ),
    Measure(
        key="audit_programme_completed", name="Internal audits completed",
        description="Share of the internal audit programme that was due and has been carried out.",
        formula=("Audits in the internal audit programme marked completed, as a share of every audit not cancelled "
                 "that is due: undated audits and audits planned on or before today and not completed always count; "
                 "a completed audit counts while planned within the last {months} months or planned in the future; "
                 "an audit planned after today and not completed is not yet due."),
        unit="percent", direction="at_least", suggested_target=100, suggested_warn=None,
        clauses=("9.2",), min_population=1, source_label="Internal audit programme",
        params=(ParamSpec("months", "Look back (months)", "number", 12, maximum=120),),
        **_live(_audit_rows, _audit_build, BreakdownSpec(
            "audits", ("not completed", "no planned date"),
            (("area", "Area"), ("planned", "Planned"), ("auditor", "Auditor"), ("status", "Status")),
            "audit", "Internal audit programme", _render_audits,
            phrases=(("no planned date", "with no planned date"),))),
    ),
    # --- Supporting measures (use of the platform); never seeded as starters ---
    Measure(
        key="risks_assessed", name="Risks assessed",
        description="Share of the risk register with an assessment that carries an inherent score.",
        formula=("Risks in the register whose newest assessment has both likelihood and impact (an inherent score), "
                 "as a share of every risk in the register; a risk with no assessment, or none scored, counts "
                 "against, whether or not it is accepted."),
        unit="percent", direction="at_least", suggested_target=100, suggested_warn=90,
        clauses=("6.1.2", "8.2"), min_population=5, source_label="Risk Register",
        params=(), group=SUPPORTING,
        **_live(_risks_assessed_rows, _risks_assessed_build, BreakdownSpec(
            "risks", ("not assessed",), _RISK_COLUMNS, "risk", "Risk Register", _render_risks)),
    ),
    Measure(
        key="risk_treatments_on_time", name="Risk treatments on time",
        description="Share of risk treatments under way whose due date has not passed.",
        formula=("Risks whose newest assessment has a treatment under way (status treating, or a treatment plan or "
                 "due date recorded, and not treated, monitored or accepted) with a due date of today or later, "
                 "as a share of all of them; a treatment with no due date counts against."),
        unit="percent", direction="at_least", suggested_target=90, suggested_warn=75,
        clauses=("6.1.3", "8.3"), min_population=5, source_label="Risk Register",
        params=(), group=SUPPORTING,
        **_live(_risk_treatments_rows, _risk_treatments_build, BreakdownSpec(
            "treatments", ("overdue", "no due date"),
            (("risk", "Risk"), ("treatment", "Treatment"), ("due", "Due"), ("owner", "Owner")),
            "risk", "Risk Register", _render_treatments, phrases=(("no due date", "with no due date"),))),
    ),
    Measure(
        key="controls_with_team", name="Controls with an accountable team",
        description="Share of the controls in scope that a team is accountable for.",
        formula=("Controls in scope with a team marked accountable (a consulted team does not count), "
                 "as a share of all controls in scope."),
        unit="percent", direction="at_least", suggested_target=100, suggested_warn=90,
        clauses=("5.3", "7.1"), min_population=5, source_label="Controls in scope",
        params=(), group=SUPPORTING,
        **_live(_controls_with_team_rows, _controls_build("controls"), BreakdownSpec(
            "controls", ("without an accountable team",), (("control", "Control"), ("status", "Status")),
            "control", "Controls", _render_unowned_controls)),
    ),
    Measure(
        key="controls_at_target_maturity", name="Controls at target maturity",
        description="Share of the controls in scope with a target maturity that have reached it.",
        formula=("Controls in scope with a target maturity (their own, else their domain's) whose recorded maturity "
                 "is at or above it, as a share of all controls in scope with a target; a control with a target "
                 "and no recorded level counts against. Controls with no target are shown, not counted."),
        unit="percent", direction="at_least", suggested_target=None, suggested_warn=None,
        clauses=("6.2", "9.1"), min_population=5, source_label="Controls in scope",
        params=(), group=SUPPORTING,
        **_live(_maturity_rows, _controls_build("controls with a target"), BreakdownSpec(
            "controls", ("below target", "with no current level"),
            (("control", "Control"), ("current", "Current"), ("target", "Target")),
            "control", "Controls", _render_maturity)),
    ),
    Measure(
        key="open_deficiencies_older_than", name="Open deficiencies older than {days} days",
        description="Open deficiencies raised more than the given number of days ago.",
        formula=("The number of open deficiencies raised more than {days} days ago; one with no raised date "
                 "counts."),
        unit="count", direction="at_most", suggested_target=None, suggested_warn=None,
        clauses=("10.2",), min_population=0, source_label="Deficiencies (POA&M)",
        params=(ParamSpec("days", "Older than (days)", "number", 90),), group=SUPPORTING,
        **_live(_older_than_rows, _older_than_build, BreakdownSpec(
            "open deficiencies", ("older",), _OPEN_DEFICIENCY_COLUMNS, "deficiency", "Deficiencies",
            _render_open_deficiencies, phrases=(("older", "past the limit"),))),
    ),
    Measure(
        key="open_deficiency_average_age", name="Average age of open deficiencies",
        description="The average number of days open deficiencies have been open.",
        formula="The average age in days of open deficiencies; no open deficiencies reads 0.",
        unit="count", direction="at_most", suggested_target=None, suggested_warn=None,
        clauses=("10.2",), min_population=0, source_label="Deficiencies (POA&M)",
        params=(), group=SUPPORTING, calculator=_average_age, breakdown=None,
    ),
    Measure(
        key="ai_verdicts_awaiting_review", name="AI verdicts awaiting review",
        description="Evidence files with an AI assessment that no person has yet confirmed or overridden.",
        formula=("The number of evidence files, not deleted, whose AI assessment reached a verdict (sufficient, "
                 "partial, insufficient or unassessable) and has no human review decision."),
        unit="count", direction="at_most", suggested_target=None, suggested_warn=None,
        clauses=("9.1", "7.5"), min_population=0, source_label="Evidence review queue",
        params=(), group=SUPPORTING,
        **_live(_ai_verdict_rows, _ai_verdict_build, BreakdownSpec(
            "AI verdicts", ("awaiting review",),
            (("evidence", "Evidence"), ("file", "File"), ("verdict", "Verdict"), ("assessed", "Assessed")),
            "evidence", "Evidence review queue", _render_ai_verdicts)),
    ),
)

REGISTRY: Dict[str, Measure] = {m.key: m for m in MEASURES}

#: The row function behind each live measure with a breakdown, for the agreement tests.
ROWS: Dict[str, RowFunction] = {
    "risk_within_appetite": _risk_within_appetite_rows, "open_material_deficiencies": _open_material_rows,
    "deficiencies_corrected_in_time": _corrected_rows, "evidence_collected_on_time": _evidence_rows,
    "supplier_assurance_current": _supplier_rows, "controls_implemented": _controls_implemented_rows,
    "response_tests_passed": _response_test_rows, "audit_programme_completed": _audit_rows,
    "risks_assessed": _risks_assessed_rows, "risk_treatments_on_time": _risk_treatments_rows,
    "controls_with_team": _controls_with_team_rows, "controls_at_target_maturity": _maturity_rows,
    "open_deficiencies_older_than": _older_than_rows, "ai_verdicts_awaiting_review": _ai_verdict_rows,
}


def get_measure(key: Optional[str]) -> Optional[Measure]:
    return REGISTRY.get(key) if key else None


async def measure(db: AsyncSession, org_id: UUID, key: str, params: Optional[Dict[str, Any]], today: date) -> Measurement:
    """Run one measure for one organisation on ``today``."""
    spec = REGISTRY[key]
    return await spec.calculator(db, org_id, resolve_params(spec, params), today)


async def breakdown(
    db: AsyncSession, org_id: UUID, key: str, params: Optional[Dict[str, Any]], today: date, limit: int,
) -> Optional[Dict[str, Any]]:
    """What holds one measure back today, worst first; None when the measure has no breakdown or nothing does."""
    spec = REGISTRY[key]
    if spec.breakdown is None:
        return None
    return await spec.breakdown(db, org_id, resolve_params(spec, params), today, limit)


_WORD = re.compile(r"[a-z0-9]+")


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def match_score(measure_: Measure, text: Optional[str]) -> int:
    """Distinct words of ``text`` (three letters or more) found in the measure's name, description and formula."""
    if not text:
        return 0
    haystack = " ".join((measure_.name, measure_.description, measure_.formula)).lower()
    words = {_stem(w) for w in _WORD.findall(haystack)}
    wanted = {_stem(w) for w in _WORD.findall(text.lower()) if len(w) >= 3}
    return sum(1 for w in wanted if w in words)


__all__ = [
    "Measure", "Measurement", "ParamSpec", "Row", "MEASURES", "REGISTRY", "ROWS", "GROUPS", "OUTCOME", "SUPPORTING",
    "get_measure", "measure", "breakdown", "resolve_params", "match_score", "months_ago", "planned_date",
    "member_names", "day_text",
]
