# SPDX-License-Identifier: AGPL-3.0-only
"""Request and response shapes for security objectives and ISMS metrics.

Request bodies forbid unknown fields. The service returns plain dicts in the
response shapes below, so a router can use them as ``response_model``.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_TEXT = 4000
_SHORT = 300

Lifecycle = Literal["proposed", "active", "achieved", "missed"]
MetricKind = Literal["live", "logged", "milestone", "unset"]
PerformanceStatus = Literal[
    "on_track", "at_risk", "off_track", "in_progress", "proposed",
    "insufficient_data", "first_measurement_due", "measurement_overdue", "not_measured",
    # A closed objective's final state: on a reading only, never on a stored result.
    "achieved", "missed",
]
Unit = Literal["percent", "count"]
Direction = Literal["at_least", "at_most"]
LoggedFrequency = Literal["monthly", "quarterly", "annually"]


def _strip(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return _strip(value)


class MetricSpec(_Body):
    kind: Literal["live", "logged", "milestone"]
    measure_key: Optional[str] = Field(None, max_length=64)
    params: Optional[Dict[str, Any]] = None
    name: Optional[str] = Field(None, max_length=_SHORT)
    description: Optional[str] = Field(None, max_length=_TEXT)
    unit: Optional[Unit] = None
    direction: Optional[Direction] = None
    target_value: Optional[float] = Field(None, allow_inf_nan=False)
    warn_value: Optional[float] = Field(None, allow_inf_nan=False)
    frequency: Optional[LoggedFrequency] = None
    evaluation_method: Optional[str] = Field(None, max_length=_TEXT)


def _clauses(value: Optional[List[str]]) -> Optional[List[str]]:
    if value is None:
        return value
    if len(value) > 20:
        raise ValueError("at most 20 clauses")
    cleaned = []
    for item in value:
        item = (item or "").strip()
        if len(item) > 20:
            raise ValueError("each clause is at most 20 characters")
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned


class ObjectiveCreate(_Body):
    name: str = Field(..., min_length=1, max_length=_SHORT)
    planned_actions: str = Field("", max_length=_TEXT)
    resources: str = Field("", max_length=_TEXT)
    evaluation_method: str = Field("", max_length=_TEXT)
    objective_type: Literal["maintain", "achieve"] = "maintain"
    owning_team_id: Optional[UUID] = None
    owner_user_id: Optional[UUID] = None
    due_date: Optional[date] = None
    review_date: Optional[date] = None
    iso_clauses: List[str] = Field(default_factory=list)
    metric: Optional[MetricSpec] = None

    @field_validator("iso_clauses")
    @classmethod
    def _check_clauses(cls, value: List[str]) -> List[str]:
        return _clauses(value) or []


class ObjectiveUpdate(_Body):
    name: Optional[str] = Field(None, min_length=1, max_length=_SHORT)
    planned_actions: Optional[str] = Field(None, max_length=_TEXT)
    resources: Optional[str] = Field(None, max_length=_TEXT)
    evaluation_method: Optional[str] = Field(None, max_length=_TEXT)
    objective_type: Optional[Literal["maintain", "achieve"]] = None
    owning_team_id: Optional[UUID] = None
    owner_user_id: Optional[UUID] = None
    owner_text: Optional[str] = Field(None, max_length=_SHORT)
    due_date: Optional[date] = None
    review_date: Optional[date] = None
    iso_clauses: Optional[List[str]] = None
    lifecycle_status: Optional[Literal["active", "achieved", "missed"]] = None
    archived: Optional[bool] = None

    @field_validator("iso_clauses")
    @classmethod
    def _check_clauses(cls, value: Optional[List[str]]) -> Optional[List[str]]:
        return _clauses(value)

    @model_validator(mode="after")
    def _at_least_one(self) -> "ObjectiveUpdate":
        if not self.model_fields_set:
            raise ValueError("Nothing to update")
        return self

    def changes(self) -> Dict[str, Any]:
        """Only the fields the caller sent (an explicit null clears)."""
        return {name: getattr(self, name) for name in self.model_fields_set}


class ConfirmBody(_Body):
    owning_team_id: Optional[UUID] = None
    target_value: Optional[float] = Field(None, allow_inf_nan=False)
    warn_value: Optional[float] = Field(None, allow_inf_nan=False)
    due_date: Optional[date] = None
    review_date: Optional[date] = None


class EvaluationCreate(_Body):
    note: str = Field(..., min_length=1, max_length=_TEXT)


#: A collector name: a plain identifier, so it can never print as a link, an
#: image or markup in a generated document.
COLLECTOR_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,79}$"
#: ``Numeric(14,4)`` holds ten digits before the point.
VALUE_LIMIT = 1e10


class ResultCreate(_Body):
    value: float = Field(..., allow_inf_nan=False, gt=-VALUE_LIMIT, lt=VALUE_LIMIT)
    period_start: Optional[date] = None
    note: Optional[str] = Field(None, max_length=_TEXT)
    #: The collector that sent the value (B4), e.g. ``patch-sla``; stored on the row.
    #: Accepted only from a key caller (the service refuses it from a person).
    collector: Optional[str] = Field(None, max_length=80, pattern=COLLECTOR_PATTERN)


class WordingRequest(_Body):
    kind: Literal["live", "logged", "milestone"]
    measure_key: Optional[str] = Field(None, max_length=64)
    params: Optional[Dict[str, Any]] = None
    name: Optional[str] = Field(None, max_length=_SHORT)
    unit: Optional[Unit] = None
    direction: Optional[Direction] = None
    target_value: Optional[float] = Field(None, allow_inf_nan=False)
    warn_value: Optional[float] = Field(None, allow_inf_nan=False)
    frequency: Optional[LoggedFrequency] = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class Segment(BaseModel):
    label: str
    count: int


class MetricOut(BaseModel):
    id: UUID
    kind: MetricKind
    measure_key: Optional[str]
    params: Dict[str, Any]
    name: str
    description: str
    unit: Optional[Unit]
    direction: Optional[Direction]
    target_value: Optional[float]
    warn_value: Optional[float]
    target_text: str
    min_population: Optional[int]
    frequency: Optional[Literal["nightly", "monthly", "quarterly", "annually"]]
    target_set_by_user_id: Optional[UUID]
    target_set_by_name: Optional[str]
    target_set_at: Optional[datetime]
    active_from: datetime


class TrendPoint(BaseModel):
    measured_on: date
    value: float
    value_text: str


class Pace(BaseModel):
    """Where a straight line from the first stored result to the target puts today (phase 3, A2)."""
    state: Literal["on_pace", "behind"]
    label: str
    expected_value: float
    value_text: str        # today's value printed for the pace line, worded together with expected_text
    expected_text: str
    baseline_on: date
    baseline_value: float
    due_date: date
    target_value: float


class Reading(BaseModel):
    status: PerformanceStatus
    value: Optional[float]
    value_text: str
    numerator: Optional[int]
    denominator: Optional[int]
    population: str
    segments: List[Segment]
    target_label: str
    measured_on: Optional[date]
    logged_by_name: Optional[str]
    current_period_start: Optional[date]
    days_left: Optional[int]
    # Phase 3: up to 12 stored results of the same measure, oldest first; never null.
    trend: List[TrendPoint] = Field(default_factory=list)
    # Phase 3: an "achieve" objective's pace; information only, never a status.
    pace: Optional[Pace] = None


class ObjectiveOut(BaseModel):
    id: UUID
    ref: str
    name: str
    planned_actions: str
    resources: str
    evaluation_method: str
    objective_type: Literal["maintain", "achieve"]
    owning_team_id: Optional[UUID]
    owning_team_name: Optional[str]
    owner_user_id: Optional[UUID]
    owner_name: Optional[str]
    owner_text: Optional[str]
    due_date: Optional[date]
    review_date: Optional[date]
    lifecycle_status: Lifecycle
    iso_clauses: List[str]
    source: str
    last_evaluated_at: Optional[datetime]
    last_evaluated_by_name: Optional[str]
    last_evaluation_note: Optional[str]
    created_at: datetime
    updated_at: datetime
    archived_at: Optional[datetime]
    metric: Optional[MetricOut]
    reading: Reading


class ObjectiveSummary(BaseModel):
    objectives: int
    on_track: int
    at_risk: int
    off_track: int
    needs_attention: int
    in_progress: int
    proposed: int
    not_measured: int
    overdue: int
    insufficient_data: int
    achieved: int = 0
    missed: int = 0


class ObjectiveList(BaseModel):
    items: List[ObjectiveOut]
    summary: ObjectiveSummary
    measured_at: datetime


class MeasurementTask(BaseModel):
    """A derived to-do: log the current period's value of a logged metric (phase 3, B2)."""
    id: str
    objective_id: UUID
    ref: str
    title: str
    metric_name: str
    unit: Optional[Unit]
    frequency: LoggedFrequency
    period_start: date
    due_date: date
    status: Literal["not_started", "completed"]
    first: bool
    mine: bool
    owning_team_id: Optional[UUID]
    owning_team_name: Optional[str]
    owner_name: Optional[str]
    completed_date: Optional[date]
    value_text: Optional[str]


class PortfolioItem(ObjectiveSummary):
    """One organisation's summary counts from stored rows (phase 3, B3)."""
    organization_id: UUID
    organization_name: str
    last_measured_on: Optional[date]
    stale: bool


class Portfolio(BaseModel):
    items: List[PortfolioItem]
    generated_at: datetime


class ResultOut(BaseModel):
    id: UUID
    measured_on: date
    period_start: Optional[date]
    value: Optional[float]
    value_text: str
    numerator: Optional[int]
    denominator: Optional[int]
    status: PerformanceStatus
    source: Literal["computed", "logged"]
    entered_by_name: Optional[str]
    note: Optional[str]
    # Logged rows only (B4): where the value came from, and the collector name it gave.
    origin: Optional[Literal["ui", "api_key", "mcp", "system"]] = None
    collector: Optional[str] = None
    target_label: str
    created_at: datetime
    # The target in force on that result's metric version.
    target_value: Optional[float] = None
    warn_value: Optional[float] = None
    direction: Optional[Direction] = None
    unit: Optional[Unit] = None
    # False when the result's metric version measures something else than the metric in force
    # (another measure or its settings, another logged name or unit, another kind). Contract A1.
    same_measure: bool = True


RecordKind = Literal["risk", "deficiency", "evidence", "vendor", "control", "capability_test", "audit"]


class RecordLink(BaseModel):
    kind: RecordKind
    key: Optional[str]  # what that screen opens a record by; None = the register itself


class BreakdownColumn(BaseModel):
    key: str
    label: str


class BreakdownRow(BaseModel):
    key: str
    group: str
    cells: Dict[str, str]
    link: Optional[RecordLink]


class Breakdown(BaseModel):
    title: str
    columns: List[BreakdownColumn]
    rows: List[BreakdownRow]
    total: int
    # "register" would shadow ABCMeta.register on the model class, so the field
    # is named register_ and read and written as "register".
    model_config = ConfigDict(populate_by_name=True)
    register_: RecordLink = Field(validation_alias="register", serialization_alias="register")
    register_label: str


class Change(BaseModel):
    delta: float
    days: int
    text: str


class MetricDetail(BaseModel):
    objective: ObjectiveOut
    results: List[ResultOut]
    metric_versions: List[MetricOut]
    breakdown: Optional[Breakdown] = None
    change: Optional[Change] = None
    raised_deficiencies: int = 0


class ParamChoice(BaseModel):
    value: str
    label: str


class ParamSpecOut(BaseModel):
    key: str
    label: str
    kind: Literal["number", "choice"]
    default: Any
    choices: Optional[List[ParamChoice]] = None


class MeasureToday(BaseModel):
    value: Optional[float]
    value_text: str
    population: str
    segments: List[Segment]


class MeasureOut(BaseModel):
    key: str
    name: str
    description: str
    formula: str
    unit: Unit
    direction: Direction
    suggested_target: Optional[float]
    suggested_warn: Optional[float]
    clauses: List[str]
    min_population: int
    source_label: str
    params: List[ParamSpecOut]
    today: MeasureToday
    match: int
    group: Literal["outcome", "supporting"] = "outcome"


class Wording(BaseModel):
    evaluation_method: str
    metric_name: str
    metric_description: str
    target_text: str
    target_label: str
