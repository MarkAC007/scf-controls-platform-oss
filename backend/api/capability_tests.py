"""Capability test records (PIG R11).

* ``GET …/capability-tests`` lists the organisation's tests, newest first
  (viewer); ``GET …/capability-tests/{id}`` reads one with its findings.
* ``POST …/capability-tests`` records a test and its findings (editor). Each
  finding is recorded as a deficiency linked to the test.
* ``PATCH …/capability-tests/{id}`` corrects a test (editor).
* ``POST …/capability-tests/{id}/findings`` adds a finding found later
  (editor).

A test is never deleted. See ``services/capability_tests.py`` for how the
result and the findings must agree. Every write here is in the audit log.
"""
from datetime import date
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deficiencies import audit_deficiency, check_owner, deficiency_response, resolve_subject
from auth import OrgMembership, require_org_role
from database import get_db
from models import CapabilityTest, VendorActionItem
from schemas import (
    CAPABILITY_TEST_TYPES,
    CapabilityTestCreate,
    CapabilityTestFinding,
    CapabilityTestResponse,
    CapabilityTestUpdate,
    DeficiencyResponse,
)
from services.audit_service import (
    detect_action_source,
    get_client_ip,
    get_request_id,
    get_user_agent,
    log_entity_changes,
)
from services.capability_tests import (
    ENTITY_TYPE,
    TRACKED_FIELDS,
    finding_counts,
    result_problem,
    tested_on_problem,
    tracked_values,
)
from services.deficiencies import SOURCE_CAPABILITY_TEST, blank_to_none, is_deficiency

router = APIRouter(tags=["capability-tests"])


def _caller_id(membership: OrgMembership) -> Optional[UUID]:
    return UUID(membership.user.db_id) if membership.user and membership.user.db_id else None


def _response(test: CapabilityTest, counts, findings=None) -> CapabilityTestResponse:
    total, open_ = counts
    return CapabilityTestResponse(
        id=test.id,
        organization_id=test.organization_id,
        test_type=test.test_type,
        title=test.title,
        tested_on=test.tested_on,
        scenario=test.scenario,
        result=test.result,
        outcome=test.outcome,
        recorded_by_user_id=test.recorded_by_user_id,
        finding_count=total,
        open_finding_count=open_,
        findings=findings,
        created_at=test.created_at,
        updated_at=test.updated_at,
    )


async def _get_or_404(db: AsyncSession, org_id: UUID, test_id: UUID, lock: bool = False) -> CapabilityTest:
    query = select(CapabilityTest).where(
        CapabilityTest.id == test_id, CapabilityTest.organization_id == org_id,
    )
    if lock:
        # Writes that check the result against the findings hold the row,
        # so two of them cannot each pass the check against the other.
        query = query.with_for_update()
    test = (await db.execute(query)).scalar_one_or_none()
    if test is None:
        raise HTTPException(status_code=404, detail="Capability test not found")
    return test


async def _findings(db: AsyncSession, org_id: UUID, test_id: UUID) -> List[DeficiencyResponse]:
    rows = (await db.execute(
        select(VendorActionItem).where(
            VendorActionItem.organization_id == org_id,
            VendorActionItem.capability_test_id == test_id,
            is_deficiency(),
        # Findings recorded together share a timestamp; the title then
        # keeps the order stable.
        ).order_by(VendorActionItem.created_at, VendorActionItem.title, VendorActionItem.id)
    )).scalars().all()
    return [deficiency_response(item) for item in rows]


async def _detail(db: AsyncSession, org_id: UUID, test: CapabilityTest) -> CapabilityTestResponse:
    counts = (await finding_counts(db, org_id, [test.id]))[test.id]
    return _response(test, counts, await _findings(db, org_id, test.id))


async def _audit_test(db, request, org_id, test, action, user_id, old_values):
    await log_entity_changes(
        db=db,
        organization_id=org_id,
        entity_type=ENTITY_TYPE,
        entity_id=test.id,
        action=action,
        changed_by_user_id=user_id,
        old_values=old_values,
        new_values=tracked_values(test),
        tracked_fields=TRACKED_FIELDS,
        ip_address=get_client_ip(request),
        user_agent=get_user_agent(request),
        action_source=detect_action_source(request),
        request_id=get_request_id(request),
    )


async def _check_finding(db: AsyncSession, org_id: UUID, finding: CapabilityTestFinding):
    """Validate a finding before anything is written: (scf_id, control_id, risk_code)."""
    subject = await resolve_subject(db, org_id, finding.scf_id, finding.risk_code)
    await check_owner(db, org_id, finding.owner_user_id)
    return subject


def _finding_item(org_id: UUID, test: CapabilityTest, finding: CapabilityTestFinding, subject) -> VendorActionItem:
    scf_id, control_id, risk_code = subject
    return VendorActionItem(
        organization_id=org_id,
        vendor_id=None,
        scoped_control_id=control_id,
        scf_id=scf_id,
        risk_code=risk_code,
        title=finding.title,
        description=finding.description,
        priority=finding.priority,
        status="open",
        owner_name=finding.owner_name,
        owner_user_id=finding.owner_user_id,
        due_date=finding.due_date,
        auto_generated=False,
        source=SOURCE_CAPABILITY_TEST,
        capability_test_id=test.id,
        root_cause=blank_to_none(finding.root_cause),
    )


async def _record_findings(db, request, org_id, test, findings, subjects, user_id) -> None:
    for finding, subject in zip(findings, subjects):
        item = _finding_item(org_id, test, finding, subject)
        db.add(item)
        await db.flush()
        await audit_deficiency(db, request, org_id, item, "create", user_id, {})


@router.get(
    "/organizations/{org_id}/capability-tests",
    response_model=List[CapabilityTestResponse],
)
async def list_capability_tests(
    org_id: UUID,
    test_type: Optional[str] = Query(None, pattern=f"^({CAPABILITY_TEST_TYPES})$"),
    since: Optional[date] = Query(None, description="Tests run on or after this date"),
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The organisation's capability tests, most recently run first. Requires viewer."""
    query = select(CapabilityTest).where(CapabilityTest.organization_id == org_id)
    if test_type:
        query = query.where(CapabilityTest.test_type == test_type)
    if since:
        query = query.where(CapabilityTest.tested_on >= since)
    query = query.order_by(CapabilityTest.tested_on.desc(), CapabilityTest.created_at.desc(), CapabilityTest.id)
    tests = (await db.execute(query)).scalars().all()
    counts = await finding_counts(db, org_id, [t.id for t in tests])
    return [_response(t, counts[t.id]) for t in tests]


@router.get(
    "/organizations/{org_id}/capability-tests/{test_id}",
    response_model=CapabilityTestResponse,
)
async def get_capability_test(
    org_id: UUID,
    test_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """One capability test with its findings. Requires viewer."""
    return await _detail(db, org_id, await _get_or_404(db, org_id, test_id))


@router.post(
    "/organizations/{org_id}/capability-tests",
    response_model=CapabilityTestResponse,
    status_code=201,
)
async def create_capability_test(
    org_id: UUID,
    data: CapabilityTestCreate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Record a capability test and its findings. Requires editor.

    Each finding is recorded as a deficiency linked to the test. The result
    and the findings must agree: ``passed`` with none, otherwise at least one.
    """
    problem = tested_on_problem(data.tested_on) or result_problem(data.result, len(data.findings))
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    # Every finding is checked before anything is written.
    subjects = [await _check_finding(db, org_id, f) for f in data.findings]

    user_id = _caller_id(membership)
    test = CapabilityTest(
        organization_id=org_id,
        test_type=data.test_type,
        title=data.title,
        tested_on=data.tested_on,
        scenario=data.scenario,
        result=data.result,
        outcome=data.outcome,
        recorded_by_user_id=user_id,
    )
    db.add(test)
    await db.flush()
    await _audit_test(db, request, org_id, test, "create", user_id, {})
    await _record_findings(db, request, org_id, test, data.findings, subjects, user_id)
    await db.commit()
    await db.refresh(test)
    return await _detail(db, org_id, test)


@router.patch(
    "/organizations/{org_id}/capability-tests/{test_id}",
    response_model=CapabilityTestResponse,
)
async def update_capability_test(
    org_id: UUID,
    test_id: UUID,
    data: CapabilityTestUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Correct a capability test. Requires editor.

    A changed result must still agree with the findings recorded (cancelled
    ones excluded) plus any ``findings`` sent with it, which are added. A test
    recorded as passed is corrected to failed by sending both. Findings are
    managed as deficiencies once recorded.
    """
    test = await _get_or_404(db, org_id, test_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    for field, value in changes.items():
        if value is None:
            raise HTTPException(status_code=422, detail=f"{field} cannot be cleared")
    changes.pop("findings", None)
    new_findings = data.findings or []
    if not changes and not new_findings:
        raise HTTPException(status_code=422, detail="Nothing to update")
    if "tested_on" in changes:
        problem = tested_on_problem(changes["tested_on"])
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if "result" in changes or new_findings:
        total, _ = (await finding_counts(db, org_id, [test.id]))[test.id]
        problem = result_problem(changes.get("result", test.result), total + len(new_findings))
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    subjects = [await _check_finding(db, org_id, f) for f in new_findings]

    user_id = _caller_id(membership)
    old_values = tracked_values(test)
    for field, value in changes.items():
        setattr(test, field, value)
    await db.flush()
    if changes:
        await _audit_test(db, request, org_id, test, "update", user_id, old_values)
    await _record_findings(db, request, org_id, test, new_findings, subjects, user_id)
    await db.commit()
    await db.refresh(test)
    return await _detail(db, org_id, test)


@router.post(
    "/organizations/{org_id}/capability-tests/{test_id}/findings",
    response_model=DeficiencyResponse,
    status_code=201,
)
async def add_capability_test_finding(
    org_id: UUID,
    test_id: UUID,
    data: CapabilityTestFinding,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Add a finding to a capability test, as a deficiency linked to it. Requires editor.

    A test recorded as ``passed`` found nothing: correct its result with
    the finding in one ``PATCH`` instead.
    """
    test = await _get_or_404(db, org_id, test_id, lock=True)
    problem = result_problem(test.result, 1)
    if problem:
        raise HTTPException(status_code=422, detail=problem)
    subject = await _check_finding(db, org_id, data)
    user_id = _caller_id(membership)
    item = _finding_item(org_id, test, data, subject)
    db.add(item)
    await db.flush()
    await audit_deficiency(db, request, org_id, item, "create", user_id, {})
    await db.commit()
    await db.refresh(item)
    return deficiency_response(item)
