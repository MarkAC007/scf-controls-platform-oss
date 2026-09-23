"""The API must say what timezone its timestamps are in.

Most datetime columns are ``timestamp without time zone`` holding UTC instants
by convention. Serialised bare, they reach the client as e.g.
``"2026-09-22T10:49:24.967249"`` -- and ECMAScript's ``Date`` constructor
parses a date-time *without* an offset as **local** time. A browser an hour
ahead of UTC therefore reads every such instant an hour in the past, which
surfaces as a file uploaded seconds ago being labelled "1h ago".

These tests pin the wire contract: every datetime leaving a response schema
carries an explicit ``+00:00`` offset, aware values are not shifted, and the
string is readable by ``datetime.fromisoformat()`` -- which cannot parse a
``Z`` suffix on Python < 3.11, so the offset spelling is load-bearing for
downstream consumers on older runtimes.
"""
import uuid
from datetime import datetime as _datetime, timedelta, timezone
from typing import Annotated, Optional, get_args, get_origin, get_type_hints
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel, PlainSerializer

import schemas
from schemas import EvidenceFileResponse, UtcDateTime

# A browser an hour ahead of UTC is where the ambiguity becomes visible.
BROWSER_TZ = ZoneInfo("Europe/London")
SUMMER_INSTANT = _datetime(2026, 9, 22, 10, 49, 24, 967249)  # UTC+1 locally
WINTER_INSTANT = _datetime(2026, 1, 15, 10, 49, 24, 967249)  # UTC+0 locally


class _Sample(BaseModel):
    """Stand-in for a response schema: one naive field, one already aware."""

    naive_at: UtcDateTime
    aware_at: UtcDateTime
    optional_at: Optional[UtcDateTime] = None


def _parse_as_browser(wire: str, browser_tz: ZoneInfo = BROWSER_TZ) -> _datetime:
    """Resolve a wire string the way ``new Date(...)`` resolves it.

    An offset-designated string is an absolute instant. An offset-less one is
    interpreted against the reader's own zone -- that substitution is the whole
    defect, so it is modelled explicitly rather than assumed away.
    """
    parsed = _datetime.fromisoformat(wire)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=browser_tz)
    return parsed


def _evidence_file(uploaded_at: _datetime, **overrides) -> EvidenceFileResponse:
    payload = {
        "id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "evidence_id": "EV-1",
        "filename": "export.json",
        "s3_key": "org/ev/export.json",
        "content_type": "application/json",
        "file_size_bytes": 128,
        "classification": "internal",
        "uploaded_at": uploaded_at,
        "is_deleted": False,
    }
    payload.update(overrides)
    return EvidenceFileResponse(**payload)


# ---------------------------------------------------------------------------
# The contract itself
# ---------------------------------------------------------------------------


def test_naive_datetime_serialises_with_utc_offset():
    wire = _Sample(naive_at=SUMMER_INSTANT, aware_at=SUMMER_INSTANT).model_dump(mode="json")

    assert wire["naive_at"] == "2026-09-22T10:49:24.967249+00:00"
    assert wire["naive_at"].endswith("+00:00"), "no designator means the reader has to guess"


def test_aware_datetime_is_not_shifted_or_double_stamped():
    """The timezone=True columns must pass through untouched."""
    aware = _datetime(2026, 9, 22, 10, 49, 24, 967249, tzinfo=timezone.utc)

    wire = _Sample(naive_at=SUMMER_INSTANT, aware_at=aware).model_dump(mode="json")
    parsed = _datetime.fromisoformat(wire["aware_at"])

    assert parsed == aware, "an already-aware instant was moved"
    assert parsed.utcoffset() == timedelta(0)
    assert wire["aware_at"] == "2026-09-22T10:49:24.967249+00:00"


def test_aware_non_utc_datetime_keeps_its_own_instant():
    """A value that is aware but not UTC is still the same instant afterwards."""
    aware = _datetime(2026, 9, 22, 11, 49, 24, 967249, tzinfo=timezone(timedelta(hours=1)))

    wire = _Sample(naive_at=SUMMER_INSTANT, aware_at=aware).model_dump(mode="json")

    assert _datetime.fromisoformat(wire["aware_at"]) == aware


@pytest.mark.parametrize("instant", [SUMMER_INSTANT, WINTER_INSTANT])
def test_wire_string_round_trips_through_fromisoformat(instant):
    """Consumers on Python < 3.11 cannot parse a ``Z`` suffix; assert the offset form."""
    wire = _Sample(naive_at=instant, aware_at=instant).model_dump(mode="json")

    for value in (wire["naive_at"], wire["aware_at"]):
        assert "Z" not in value
        parsed = _datetime.fromisoformat(value)  # must not raise
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timedelta(0)


def test_optional_datetime_field_is_covered_and_none_survives():
    both = _Sample(naive_at=SUMMER_INSTANT, aware_at=SUMMER_INSTANT, optional_at=SUMMER_INSTANT)
    assert both.model_dump(mode="json")["optional_at"].endswith("+00:00")

    neither = _Sample(naive_at=SUMMER_INSTANT, aware_at=SUMMER_INSTANT)
    assert neither.model_dump(mode="json")["optional_at"] is None


def test_python_mode_dump_still_yields_datetime_objects():
    """In-process callers keep real datetimes; only the wire representation changed."""
    dumped = _Sample(naive_at=SUMMER_INSTANT, aware_at=SUMMER_INSTANT).model_dump()

    assert isinstance(dumped["naive_at"], _datetime)


# ---------------------------------------------------------------------------
# The reported defect: a fresh upload must read as seconds old, not an hour
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now_utc",
    [
        _datetime(2026, 9, 22, 10, 49, 28, tzinfo=timezone.utc),  # browser at UTC+1
        _datetime(2026, 1, 15, 10, 49, 28, tzinfo=timezone.utc),  # browser at UTC+0
    ],
    ids=["browser_offset_plus_one", "browser_offset_zero"],
)
def test_fresh_upload_reads_as_seconds_old_in_an_offset_browser(now_utc):
    uploaded_at = now_utc.replace(tzinfo=None) - timedelta(seconds=4)  # naive UTC, as stored

    wire = _evidence_file(uploaded_at).model_dump(mode="json")["uploaded_at"]
    age = (now_utc - _parse_as_browser(wire)).total_seconds()

    assert 0 <= age < 60, f"a four-second-old upload read as {age}s old from {wire!r}"


def test_undesignated_string_is_what_produced_the_hour_of_drift():
    """Locks the regression: the old shape is an hour out, the new shape is not.

    Fixed clock, no wall time involved -- this fails the moment the designator
    stops being emitted.
    """
    now_utc = _datetime(2026, 9, 22, 10, 49, 28, tzinfo=timezone.utc)
    uploaded_at = now_utc.replace(tzinfo=None) - timedelta(seconds=4)

    undesignated = uploaded_at.isoformat()  # what a bare datetime used to emit
    assert not undesignated.endswith("+00:00")
    drift = (now_utc - _parse_as_browser(undesignated)).total_seconds()
    assert drift == pytest.approx(3604, abs=1), "the browser-side skew is not being modelled"

    designated = _evidence_file(uploaded_at).model_dump(mode="json")["uploaded_at"]
    assert (now_utc - _parse_as_browser(designated)).total_seconds() == pytest.approx(4, abs=1)


def test_every_evidence_file_timestamp_is_designated():
    """Not one field: the whole record."""
    response = _evidence_file(
        SUMMER_INSTANT,
        hash_verified_at=SUMMER_INSTANT + timedelta(milliseconds=83),
        expires_at=SUMMER_INSTANT + timedelta(days=365),
        reviewed_at=SUMMER_INSTANT + timedelta(minutes=5),
    )

    wire = response.model_dump(mode="json")

    for field in ("uploaded_at", "hash_verified_at", "expires_at", "reviewed_at"):
        assert wire[field].endswith("+00:00"), f"{field} left without a designator"
        assert _datetime.fromisoformat(wire[field]).tzinfo is not None


def test_evidence_file_date_fields_are_untouched():
    """Calendar dates carry no zone and must not grow one."""
    from datetime import date

    wire = _evidence_file(
        SUMMER_INSTANT, effective_period_start=date(2026, 1, 1)
    ).model_dump(mode="json")

    assert wire["effective_period_start"] == "2026-01-01"


# ---------------------------------------------------------------------------
# Class-wide guard: a new schema cannot quietly reintroduce a bare datetime
# ---------------------------------------------------------------------------


def _designated(annotation) -> bool:
    """True if every ``datetime`` inside this annotation carries a serialiser."""
    if get_origin(annotation) is Annotated:
        base, *metadata = get_args(annotation)
        if base is _datetime:
            return any(isinstance(m, PlainSerializer) for m in metadata)
        return _designated(base)
    args = get_args(annotation)
    if args:
        return all(_designated(arg) for arg in args if arg is not type(None))
    return annotation is not _datetime


def _schema_models():
    for name in dir(schemas):
        obj = getattr(schemas, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
            if obj.__module__ == schemas.__name__:
                yield obj


def test_no_schema_declares_an_undesignated_datetime():
    offenders = []
    datetime_fields = 0

    for model in _schema_models():
        try:
            hints = get_type_hints(model, include_extras=True)
        except Exception:  # pragma: no cover - unresolvable forward ref
            continue
        for field_name in model.model_fields:
            annotation = hints.get(field_name)
            if annotation is None or "datetime" not in repr(annotation):
                continue
            datetime_fields += 1
            if not _designated(annotation):
                offenders.append(f"{model.__name__}.{field_name}")

    assert datetime_fields > 0, "the guard found no datetime fields to check"
    assert not offenders, (
        "these fields serialise without a timezone designator; annotate them "
        f"with UtcDateTime: {sorted(offenders)}"
    )


def test_guard_would_catch_a_bare_datetime():
    """Negative control -- the guard is not vacuously green."""

    class _Regression(BaseModel):
        created_at: _datetime

    assert not _designated(get_type_hints(_Regression, include_extras=True)["created_at"])
    assert _designated(get_type_hints(_Sample, include_extras=True)["naive_at"])
