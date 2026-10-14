"""Capability test records (PIG R11).

A capability test is a test of whether the organisation can do what its
controls say: an incident response exercise, a continuity test, or another.
The record is what was run and when, the scenario, the result and the
outcome. A test is corrected, never deleted.

Its findings are deficiencies (R8, ``services/deficiencies.py``): each one is
a deficiency linked to the test (``capability_test_id``, source
``capability_test``), with its own owner, due date and closure, and
optionally the control or risk it is against and its root cause.

The result and the findings must agree. ``passed`` means nothing was found,
so a test with findings cannot read as passed; ``passed_with_findings`` and
``failed`` each need at least one finding, so a failure cannot be recorded
with nothing to act on. A cancelled finding (raised in error) does not count,
so restoring one to a test recorded as passed is refused until the test's
result is corrected.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, Iterable, Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CapabilityTest, VendorActionItem
from services.deficiencies import CANCELLED, is_deficiency, is_open

TEST_TYPES = ("incident_response", "continuity", "other")
RESULT_PASSED = "passed"
RESULT_PASSED_WITH_FINDINGS = "passed_with_findings"
RESULT_FAILED = "failed"
RESULTS = (RESULT_PASSED, RESULT_PASSED_WITH_FINDINGS, RESULT_FAILED)

ENTITY_TYPE = "capability_test"

#: Fields written to the audit log for a capability test.
TRACKED_FIELDS = frozenset({
    "test_type",
    "title",
    "tested_on",
    "scenario",
    "result",
    "outcome",
    "recorded_by_user_id",
})


def tracked_values(test: CapabilityTest) -> Dict[str, object]:
    return {f: getattr(test, f) for f in TRACKED_FIELDS}


def result_problem(result: str, finding_count: int) -> Optional[str]:
    """Why ``result`` cannot stand with this many findings, or None."""
    if result == RESULT_PASSED and finding_count:
        return (
            f"A test with {finding_count} finding(s) did not simply pass: "
            "record it as passed_with_findings or failed"
        )
    if result != RESULT_PASSED and not finding_count:
        return f"A test that {'failed' if result == RESULT_FAILED else 'passed with findings'} needs at least one finding"
    return None


def tested_on_problem(tested_on: date, today: Optional[date] = None) -> Optional[str]:
    """A test is recorded once it has run, so its date cannot be in the future."""
    if tested_on > (today or date.today()):
        return "tested_on cannot be in the future: record a test once it has run"
    return None


def _findings_of(test_ids: Iterable[UUID]):
    return (
        VendorActionItem.capability_test_id.in_(list(test_ids)),
        is_deficiency(),
        VendorActionItem.status != CANCELLED,
    )


async def finding_counts(
    db: AsyncSession, organization_id: UUID, test_ids: Sequence[UUID],
) -> Dict[UUID, Tuple[int, int]]:
    """``{test_id: (findings, open findings)}``; cancelled findings are not counted."""
    if not test_ids:
        return {}
    rows = (await db.execute(
        select(
            VendorActionItem.capability_test_id,
            func.count(VendorActionItem.id),
            func.count(VendorActionItem.id).filter(is_open()),
        ).where(
            VendorActionItem.organization_id == organization_id, *_findings_of(test_ids),
        ).group_by(VendorActionItem.capability_test_id)
    )).all()
    counts = {test_id: (0, 0) for test_id in test_ids}
    for test_id, total, open_ in rows:
        counts[test_id] = (int(total), int(open_))
    return counts


async def restored_finding_problem(
    db: AsyncSession, organization_id: UUID, test_id: UUID,
) -> Optional[str]:
    """Why a cancelled finding of this test cannot be restored, or None.

    The test row is locked so a concurrent correction of its result waits.
    """
    test = (await db.execute(
        select(CapabilityTest).where(
            CapabilityTest.id == test_id, CapabilityTest.organization_id == organization_id,
        ).with_for_update()
    )).scalar_one_or_none()
    if test is None:
        return None
    total, _ = (await finding_counts(db, organization_id, [test_id]))[test_id]
    problem = result_problem(test.result, total + 1)
    if problem:
        return f"{problem}. Correct the capability test's result before restoring this finding"
    return None


async def count_within_days(
    db: AsyncSession,
    organization_id: UUID,
    days: int,
    test_types: Optional[Sequence[str]] = None,
    today: Optional[date] = None,
) -> int:
    """Tests run from ``days`` days ago to today, both included, whatever their result.

    With ``days=365`` a test run a year ago today still counts, as an
    approval does for ``documents_approved``.

    A failed test still shows the capability was tested; what it found is
    tracked as deficiencies.
    """
    today = today or date.today()
    conds = [
        CapabilityTest.organization_id == organization_id,
        CapabilityTest.tested_on >= today - timedelta(days=days),
        CapabilityTest.tested_on <= today,
    ]
    if test_types:
        conds.append(CapabilityTest.test_type.in_(list(test_types)))
    return int(await db.scalar(select(func.count(CapabilityTest.id)).where(*conds)) or 0)
