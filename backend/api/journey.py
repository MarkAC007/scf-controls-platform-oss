"""
Organisational Journey API — the staged path an organisation walks.

Four routes:

  GET    /organizations/{org_id}/journey            the path, with preconditions evaluated
  GET    /organizations/{org_id}/journey/templates  templates this deployment ships
  POST   /organizations/{org_id}/journey/import     create or replace the path from a template
  POST   /organizations/{org_id}/journey/stages/{stage_id}/attest   a named person passes a stage

The GET is deliberately readable by a viewer and deliberately non-mutating even
when the organisation has no journey yet: it renders the default template as an
unlit preview rather than writing one. An organisation with nobody helping them
still gets to see where the road goes.

Nothing here advances a stage on computed evidence. `attest` is the only route
that writes a passed state, it requires an editor, and it records who.
"""
import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth import OrgMembership, require_org_role
from database import get_db
from models import (
    ConsultantClientRelationship,
    ConsultantProfile,
    JourneyStage,
    JourneyStageState,
    OrgJourney,
    User,
)
from services import journey as journey_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["journey"])


class StageSpec(BaseModel):
    """One stage of an uploaded journey artefact."""
    key: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=255)
    summary: Optional[str] = None
    expect_next: Optional[str] = None
    precondition_spec: List[Dict[str, Any]] = Field(default_factory=list)


class TemplateSpec(BaseModel):
    """A journey artefact a practitioner uploads.

    This is the route, authored outside the platform and versioned wherever the
    practitioner keeps it. The platform stores what was uploaded for this one
    organisation and never treats it as product content.
    """
    template_key: Optional[str] = Field(default=None, max_length=100)
    template_version: Optional[str] = Field(default=None, max_length=50)
    name: str = Field(default="Compliance journey", max_length=255)
    description: Optional[str] = None
    stages: List[StageSpec] = Field(min_length=1)


class ImportRequest(BaseModel):
    # Names a template file on disk, so it is constrained to a bare filename
    # stem here and traversal is refused as a 422 before it reaches the
    # service. journey_service.load_template() repeats the check and adds a
    # resolved-path containment test; this field is the outer of the two and
    # neither is permitted to be the only one.
    template_key: str = Field(
        default=journey_service.DEFAULT_TEMPLATE_KEY,
        max_length=100,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    )
    activate: bool = Field(default=False, description="Start the first stage immediately")
    practitioner_name: Optional[str] = None
    template: Optional[TemplateSpec] = Field(
        default=None,
        description="An uploaded artefact. When present it wins over template_key, "
                    "and nothing is read from the deployment's own templates.",
    )


class AttestRequest(BaseModel):
    note: Optional[str] = Field(default=None, description="What was checked, and by whom, in the attester's words")
    conditional: bool = Field(default=False, description="Pass with named items still outstanding")
    target_date: Optional[date] = Field(default=None, description="When outstanding items are due; required when conditional")


async def _practitioner(db: AsyncSession, org_id: UUID) -> Optional[Dict[str, Any]]:
    """The consultancy engaged with this organisation, if any.

    This is what lights the road. An organisation with no active consultant
    relationship sees every stone dark — not because the feature is withheld,
    but because nobody is walking it with them yet.
    """
    row = (
        await db.execute(
            select(ConsultantProfile)
            .join(
                ConsultantClientRelationship,
                ConsultantClientRelationship.consultant_id == ConsultantProfile.id,
            )
            .where(
                ConsultantClientRelationship.organization_id == org_id,
                ConsultantClientRelationship.status == "active",
                ConsultantProfile.is_active.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {"company_name": row.company_name, "consultant_profile_id": str(row.id)}


def _stage_payload(
    stage: JourneyStage,
    preconditions: Dict[str, Any],
    attested_by_name: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "id": str(stage.id),
        "ordinal": stage.ordinal,
        "key": stage.key,
        "title": stage.title,
        "summary": stage.summary,
        "expect_next": stage.expect_next,
        "state": stage.state,
        "started_at": stage.started_at.isoformat() if stage.started_at else None,
        "attested_at": stage.attested_at.isoformat() if stage.attested_at else None,
        "attested_by_user_id": str(stage.attested_by_user_id) if stage.attested_by_user_id else None,
        # A user id is not a signature. The point of this feature is that a
        # named person stood behind the verdict, so the name travels with it.
        "attested_by_name": attested_by_name,
        "attestation_note": stage.attestation_note,
        "target_date": stage.target_date.isoformat() if stage.target_date else None,
        "preconditions": preconditions,
    }


def _preview_stage(index: int, raw: Dict[str, Any]) -> Dict[str, Any]:
    """A stage from a template that has not been imported — every stone dark."""
    return {
        "id": None,
        "ordinal": index,
        "key": raw.get("key") or f"stage-{index}",
        "title": raw.get("title") or f"Stage {index + 1}",
        "summary": raw.get("summary"),
        "expect_next": raw.get("expect_next"),
        "state": JourneyStageState.LOCKED.value,
        "started_at": None,
        "attested_at": None,
        "attested_by_user_id": None,
        "attested_by_name": None,
        "attestation_note": None,
        "target_date": None,
        "preconditions": {"checks": [], "met_count": 0, "total_count": 0, "unknown_count": 0, "all_met": False},
    }


@router.get("/organizations/{org_id}/journey")
async def get_journey(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """The organisation's journey, with every stage's preconditions evaluated.

    When no journey has been imported, this returns the default template as a
    preview with `provisioned: false`. Nothing is written.
    """
    practitioner = await _practitioner(db, org_id)

    journey = (
        await db.execute(
            select(OrgJourney)
            .options(selectinload(OrgJourney.stages))
            .where(OrgJourney.organization_id == org_id)
        )
    ).scalar_one_or_none()

    if journey is None:
        try:
            template = journey_service.load_template()
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("No default journey template available: %s", exc)
            raise HTTPException(status_code=404, detail="No journey template is available on this deployment")
        stages = [_preview_stage(i, s) for i, s in enumerate(template.get("stages", []))]
        return {
            "provisioned": False,
            "activated": False,
            "practitioner": practitioner,
            "name": template.get("name"),
            "description": template.get("description"),
            "template_key": template.get("template_key"),
            "template_version": template.get("template_version"),
            "attribution": template.get("attribution"),
            "current_stage_key": None,
            "stages": stages,
            "focus": [],
        }

    totals = await journey_service._in_scope_counts(db, org_id)

    # One lookup for every attester on the path, rather than one per stage.
    attester_ids = {s.attested_by_user_id for s in journey.stages if s.attested_by_user_id}
    names: Dict[UUID, str] = {}
    if attester_ids:
        rows = (await db.execute(
            select(User.id, User.display_name, User.email).where(User.id.in_(attester_ids))
        )).all()
        names = {row[0]: (row[1] or row[2]) for row in rows}

    stages_out: List[Dict[str, Any]] = []
    current_key: Optional[str] = None
    focus: List[Dict[str, Any]] = []

    for stage in sorted(journey.stages, key=lambda s: s.ordinal):
        pre = await journey_service.evaluate_stage_preconditions(db, org_id, stage, totals)
        payload = _stage_payload(stage, pre, names.get(stage.attested_by_user_id))

        # "Every mechanical check has gone green and a person is now the only
        # remaining dependency" is DERIVED, never stored. It follows from data
        # that moves under us — a control changing status would make a stored
        # flag a lie within the hour — so it is computed here, beside the
        # evaluation it depends on. The row stays ACTIVE: the organisation has
        # not left the stage, and the path must not imply that it has.
        if stage.state == JourneyStageState.ACTIVE.value and pre["all_met"]:
            payload["state"] = JourneyStageState.AWAITING_ATTESTATION.value

        stages_out.append(payload)
        if stage.state in (JourneyStageState.ACTIVE.value, JourneyStageState.AWAITING_ATTESTATION.value):
            current_key = stage.key
            # Focus is the active stage's unmet checks — a lens on work the
            # platform already tracks, never a second to-do list.
            focus = [c for c in pre["checks"] if c["met"] is not True]

    return {
        "provisioned": True,
        "activated": journey.activated_at is not None,
        "activated_at": journey.activated_at.isoformat() if journey.activated_at else None,
        "practitioner": practitioner or ({"company_name": journey.practitioner_name} if journey.practitioner_name else None),
        "name": journey.name,
        "description": journey.description,
        "template_key": journey.template_key,
        "template_version": journey.template_version,
        "current_stage_key": current_key,
        "stages": stages_out,
        "focus": focus,
    }


@router.get("/organizations/{org_id}/journey/templates")
async def list_templates(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Journey templates this deployment ships. Operators can add their own."""
    return {"templates": journey_service.available_templates()}


@router.post("/organizations/{org_id}/journey/import")
async def import_journey(
    org_id: UUID,
    payload: ImportRequest = Body(...),
    membership: OrgMembership = Depends(require_org_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create this organisation's journey, or re-issue it from a revision.

    The body may carry an uploaded artefact (``template``) or name one this
    deployment ships (``template_key``). An upload wins.

    Re-issuing merges by stage key and preserves every attestation; a revision
    that drops an already-signed stage is refused with a 409 rather than
    quietly rewriting the audit trail.
    """
    if payload.template is not None:
        template = payload.template.model_dump()
        source = f"upload:{template.get('template_key') or 'unnamed'}"
        duplicates = {k for k in
                      [st["key"] for st in template["stages"]]
                      if [st["key"] for st in template["stages"]].count(k) > 1}
        if duplicates:
            raise HTTPException(
                status_code=422,
                detail=f"Stage keys must be unique; repeated: {', '.join(sorted(duplicates))}",
            )
        unknown = journey_service.unsupported_checks(template)
        if unknown:
            # Refused rather than silently degraded. A gate the engine cannot
            # evaluate would sit unknown forever, and the practitioner would
            # not find out until a client asked why a wave never turned green.
            raise HTTPException(
                status_code=422,
                detail="This deployment cannot evaluate these check types: "
                       + ", ".join(sorted(unknown)),
            )
    else:
        try:
            template = journey_service.load_template(payload.template_key)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"No template named '{payload.template_key}'")
        source = f"builtin:{payload.template_key}"

    practitioner = await _practitioner(db, org_id)
    name = payload.practitioner_name or (practitioner or {}).get("company_name")

    try:
        journey = await journey_service.import_template(
            db,
            org_id,
            template,
            practitioner_name=name,
            activate=payload.activate,
        )
    except ValueError as exc:
        # A revision that would erase a signature.
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()

    logger.info(
        "Journey imported for org %s from %s (activate=%s, stages=%d)",
        org_id, source, payload.activate, len(template.get("stages", [])),
    )
    return {
        "id": str(journey.id),
        "template_key": journey.template_key,
        "template_version": journey.template_version,
        "activated": journey.activated_at is not None,
    }


@router.post("/organizations/{org_id}/journey/stages/{stage_id}/attest")
async def attest_stage(
    org_id: UUID,
    stage_id: UUID,
    payload: AttestRequest = Body(...),
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    """Pass a stage. A named person signs; the platform records who and when.

    Preconditions are NOT a gate here. They inform the person signing; they do
    not sign for them. A stage may be attested with checks outstanding — that
    is what `conditional` plus a `target_date` is for, and it is recorded as
    such rather than hidden.
    """
    stage = (
        await db.execute(
            select(JourneyStage)
            .join(OrgJourney, OrgJourney.id == JourneyStage.journey_id)
            .where(
                JourneyStage.id == stage_id,
                OrgJourney.organization_id == org_id,
            )
        )
    ).scalar_one_or_none()

    # Cross-tenant reads are indistinguishable from a missing row.
    if stage is None:
        raise HTTPException(status_code=404, detail="Stage not found")

    if stage.state in (JourneyStageState.PASSED.value, JourneyStageState.PASSED_CONDITIONAL.value):
        raise HTTPException(status_code=409, detail="This stage has already been attested")

    if payload.conditional and payload.target_date is None:
        raise HTTPException(status_code=422, detail="A conditional pass needs a target date for the outstanding items")

    user_db_id = membership.user.db_id if membership.user else None
    if not user_db_id:
        # The whole point of this route is recording who. If we cannot, refuse.
        raise HTTPException(status_code=403, detail="Attestation requires an identified user")

    now = datetime.now(timezone.utc)
    stage.state = (
        JourneyStageState.PASSED_CONDITIONAL.value if payload.conditional
        else JourneyStageState.PASSED.value
    )
    stage.attested_by_user_id = UUID(user_db_id)
    stage.attested_at = now
    stage.attestation_note = payload.note
    stage.target_date = payload.target_date

    # Open the next stone.
    next_stage = (
        await db.execute(
            select(JourneyStage).where(
                JourneyStage.journey_id == stage.journey_id,
                JourneyStage.ordinal == stage.ordinal + 1,
            )
        )
    ).scalar_one_or_none()
    if next_stage is not None and next_stage.state == JourneyStageState.LOCKED.value:
        next_stage.state = JourneyStageState.ACTIVE.value
        next_stage.started_at = now

    await db.commit()

    logger.info(
        "Journey stage %s attested for org %s by user %s (conditional=%s)",
        stage.key, org_id, user_db_id, payload.conditional,
    )
    return {
        "id": str(stage.id),
        "state": stage.state,
        "attested_at": stage.attested_at.isoformat(),
        "next_stage_key": next_stage.key if next_stage is not None else None,
    }
