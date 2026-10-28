"""
ISMS profile — the organisation facts ISO/IEC 27001 clause documents need.

The scope statement and five registers (context issues, interested parties,
objectives, communication plan, audit programme) are edited here and rendered
by the clause document generators. Each section is saved on its own, so two
people editing different registers do not overwrite each other, and every save
writes one audit row naming the section.

Starter rows are offered for the interested parties and communication plan.
They are fixed text, not model output: generic parties and communications
almost every ISMS has, for a person to keep, edit or delete. Nothing here calls
a language model and nothing here reads SCF content.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_role
from database import get_db
from models import IsmsProfile
from services.audit_service import create_audit_entry, detect_action_source

router = APIRouter(tags=["ISMS profile"])

_TEXT = 4000
_SHORT = 300
_ROWS = 200


def _row_id() -> str:
    return uuid.uuid4().hex[:12]


class _Row(BaseModel):
    """Every register row carries a stable id so the UI can key and edit it."""

    id: str = Field(default_factory=_row_id, max_length=64)

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


class Exclusion(_Row):
    item: str = Field("", max_length=_SHORT)
    justification: str = Field("", max_length=_TEXT)


class ScopeStatement(BaseModel):
    """Clause 4.3. In-scope systems and suppliers come from their registers."""

    organisation_description: str = Field("", max_length=_TEXT)
    boundaries: str = Field("", max_length=_TEXT)
    locations: List[str] = Field(default_factory=list, max_length=_ROWS)
    services: List[str] = Field(default_factory=list, max_length=_ROWS)
    interfaces: str = Field("", max_length=_TEXT)
    exclusions: List[Exclusion] = Field(default_factory=list, max_length=_ROWS)


class ContextIssue(_Row):
    """Clause 4.1."""

    kind: Literal["internal", "external"] = "external"
    category: Literal[
        "political", "economic", "social", "technological", "legal",
        "environmental", "organisational",
    ] = "organisational"
    issue: str = Field("", max_length=_TEXT)
    effect: str = Field("", max_length=_TEXT)
    response: str = Field("", max_length=_TEXT)
    #: Amendment 1:2024 asks whether climate change is a relevant issue.
    climate_related: bool = False


class InterestedParty(_Row):
    """Clause 4.2."""

    party: str = Field("", max_length=_SHORT)
    kind: Literal["internal", "external"] = "external"
    requirements: str = Field("", max_length=_TEXT)
    addressed_through_isms: bool = True
    how_addressed: str = Field("", max_length=_TEXT)


class Objective(_Row):
    """Clause 6.2."""

    objective: str = Field("", max_length=_TEXT)
    measure: str = Field("", max_length=_TEXT)
    target: str = Field("", max_length=_SHORT)
    owner: str = Field("", max_length=_SHORT)
    resources: str = Field("", max_length=_TEXT)
    due_date: Optional[str] = Field(None, max_length=10, pattern=r"^\d{4}-\d{2}-\d{2}$")
    evaluation: str = Field("", max_length=_TEXT)
    status: Literal["planned", "in_progress", "achieved", "missed"] = "planned"


class Communication(_Row):
    """Clause 7.4: what, when, with whom, who, how."""

    what: str = Field("", max_length=_TEXT)
    when: str = Field("", max_length=_SHORT)
    audience: str = Field("", max_length=_SHORT)
    communicator: str = Field("", max_length=_SHORT)
    method: str = Field("", max_length=_SHORT)


class AuditEntry(_Row):
    """Clause 9.2: one planned internal audit."""

    area: str = Field("", max_length=_SHORT)
    criteria: str = Field("", max_length=_TEXT)
    planned_period: str = Field("", max_length=_SHORT)
    auditor: str = Field("", max_length=_SHORT)
    status: Literal["planned", "in_progress", "completed", "cancelled"] = "planned"
    findings: str = Field("", max_length=_TEXT)


SECTIONS: Dict[str, Any] = {
    "scope": ScopeStatement,
    "context_issues": ContextIssue,
    "interested_parties": InterestedParty,
    "objectives": Objective,
    "communication_plan": Communication,
    "audit_programme": AuditEntry,
}
Section = Literal[
    "scope", "context_issues", "interested_parties", "objectives",
    "communication_plan", "audit_programme",
]


class IsmsProfileOut(BaseModel):
    scope: ScopeStatement
    context_issues: List[ContextIssue]
    interested_parties: List[InterestedParty]
    objectives: List[Objective]
    communication_plan: List[Communication]
    audit_programme: List[AuditEntry]
    updated_at: Optional[str] = None


class SectionUpdate(BaseModel):
    #: The section's whole value: an object for ``scope``, a list otherwise.
    data: Any


def _validate(section: str, data: Any) -> Any:
    """Validate a section's value, turning a schema failure into a 422 that
    says which row and field, rather than a 500."""
    model = SECTIONS[section]
    try:
        if section == "scope":
            return model.model_validate(data or {}).model_dump()
        if not isinstance(data, list):
            raise HTTPException(status_code=422, detail=f"{section} must be a list")
        if len(data) > _ROWS:
            raise HTTPException(status_code=422, detail=f"{section} holds at most {_ROWS} rows")
        out = []
        for index, row in enumerate(data, start=1):
            try:
                out.append(model.model_validate(row).model_dump())
            except ValidationError as exc:
                raise HTTPException(
                    status_code=422, detail=f"Row {index}: {_first_error(exc)}"
                ) from exc
        return out
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=_first_error(exc)) from exc


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    field = ".".join(str(p) for p in err.get("loc", ()))
    return f"{field}: {err.get('msg', 'invalid value')}" if field else err.get("msg", "invalid value")


def serialise(profile: Optional[IsmsProfile]) -> IsmsProfileOut:
    """Read a stored profile, tolerating rows written by an older schema."""
    def rows(section: str) -> List[Any]:
        stored = getattr(profile, section, None) or []
        model = SECTIONS[section]
        out = []
        for row in stored:
            try:
                out.append(model.model_validate(row))
            except Exception:  # noqa: BLE001 -- one bad row must not hide the rest
                continue
        return out

    return IsmsProfileOut(
        scope=ScopeStatement.model_validate((profile.scope if profile else None) or {}),
        context_issues=rows("context_issues"),
        interested_parties=rows("interested_parties"),
        objectives=rows("objectives"),
        communication_plan=rows("communication_plan"),
        audit_programme=rows("audit_programme"),
        updated_at=profile.updated_at.isoformat() if profile and profile.updated_at else None,
    )


async def _load(db: AsyncSession, organization_id: UUID) -> Optional[IsmsProfile]:
    return (await db.execute(
        select(IsmsProfile).where(IsmsProfile.organization_id == organization_id)
    )).scalar_one_or_none()


def _actor_id(membership: OrgMembership) -> Optional[UUID]:
    db_id = membership.user.db_id
    return UUID(db_id) if db_id else None


@router.get("/organizations/{org_id}/isms-profile", response_model=IsmsProfileOut)
async def get_isms_profile(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    return serialise(await _load(db, membership.organization_id))


@router.put("/organizations/{org_id}/isms-profile/{section}", response_model=IsmsProfileOut)
async def update_isms_profile_section(
    org_id: UUID,
    section: Section,
    payload: SectionUpdate,
    request: Request,
    membership: OrgMembership = Depends(require_org_role("editor")),
    db: AsyncSession = Depends(get_db),
):
    value = _validate(section, payload.data)
    profile = await _load(db, membership.organization_id)
    if profile is None:
        profile = IsmsProfile(organization_id=membership.organization_id)
        db.add(profile)
        await db.flush()
    before = getattr(profile, section)
    setattr(profile, section, value)
    profile.updated_by_user_id = _actor_id(membership)
    profile.updated_at = datetime.utcnow()

    await create_audit_entry(
        db,
        organization_id=membership.organization_id,
        entity_type="isms_profile",
        entity_id=profile.id,
        action="update",
        changed_by_user_id=_actor_id(membership),
        field_name=section,
        old_value=json.dumps(before, sort_keys=True)[:10000] if before else None,
        new_value=json.dumps(value, sort_keys=True)[:10000],
        action_source=detect_action_source(request),
    )
    await db.commit()
    await db.refresh(profile)
    return serialise(profile)


# ---------------------------------------------------------------------------
# Starter rows
# ---------------------------------------------------------------------------

_PARTY_STARTERS = [
    ("Customers and clients", "external",
     "Protection of their data and services; contractual security commitments; timely "
     "notification of incidents that affect them."),
    ("Employees and contractors", "internal",
     "Clear rules and training; protection of their personal data; a way to report "
     "security concerns."),
    ("Board and senior management", "internal",
     "Assurance that information security risks are managed within appetite; reporting "
     "on performance and incidents."),
    ("Regulators and authorities", "external",
     "Compliance with the statutory and regulatory obligations recorded against the "
     "organisation's scoped frameworks; breach notification within required timescales."),
    ("Suppliers and service providers", "external",
     "Clear security requirements in contracts; secure access to the organisation's "
     "systems where needed."),
    ("Insurers", "external",
     "Evidence of the controls declared when cover was arranged."),
]

_COMMUNICATION_STARTERS = [
    ("Publication of new or revised ISMS policies", "On approval", "All staff",
     "CISO function", "Email and intranet, with acknowledgement"),
    ("Security awareness messages", "Monthly", "All staff",
     "CISO function", "Email, intranet and team briefings"),
    ("Information security incidents affecting customers", "As required by contract and law",
     "Affected customers", "Incident manager function", "Direct notification"),
    ("Personal data breaches", "Within statutory timescales", "Supervisory authority",
     "Data protection function", "Regulator's reporting channel"),
    ("ISMS performance and management review outputs", "After each management review",
     "Board and senior management", "CISO function", "Management review minutes"),
    ("Security requirements and changes", "At onboarding and on change",
     "Suppliers and service providers", "Supplier owner function", "Contract schedules and email"),
]


@router.get("/organizations/{org_id}/isms-profile/starters/{section}")
async def isms_profile_starters(
    org_id: UUID,
    section: Literal["interested_parties", "communication_plan"],
    membership: OrgMembership = Depends(require_org_role("viewer")),
):
    """Generic starter rows for a register, for a person to edit. Not saved."""
    if section == "interested_parties":
        rows = [
            InterestedParty(party=p, kind=k, requirements=r) for p, k, r in _PARTY_STARTERS
        ]
    else:
        rows = [
            Communication(what=w, when=t, audience=a, communicator=c, method=m)
            for w, t, a, c, m in _COMMUNICATION_STARTERS
        ]
    return [r.model_dump() for r in rows]
