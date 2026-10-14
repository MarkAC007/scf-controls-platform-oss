"""Material controls (PIG R6).

A material control is one the organisation cannot let fall away quietly: it
may not leave scope, or be marked not applicable, without an approving admin.

Whether a control is material, in precedence order:

1. ``scoped_controls.material_override`` (true or false), set by an admin
   with a reason. The admin who sets it is recorded as its approver.
2. Otherwise the catalogue: a control weighted 10 is material by default.

The default is computed from the catalogue on every read, never stored, so a
catalogue update that reweights a control is reflected without a backfill,
and an override survives it.

This module also links a control to the approved procedure document that
describes how it is operated (playbook step 2d).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import and_, false, func, literal, select
from sqlalchemy.dialects.postgresql import array
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogControl
from models import GeneratedDocument, OrganizationFrameworkSelection, ScopedControl
from services.doc_gen.lifecycle import STATUS_APPROVED, STATUS_PUBLISHED

#: Catalogue weighting at and above which a control is material by default.
#: SCF weights run 0 to 10, so this is "the top weighting".
MATERIAL_WEIGHTING = 10

#: The document type a procedure link must point at.
PROCEDURE_DOCUMENT_TYPE = "procedure"

#: Lifecycle statuses that count as signed off. Publishing follows approval,
#: so a published procedure is still the approved one; an edit sends either
#: back to review, which is the drift the scoping list shows.
SIGNED_OFF_STATUSES = (STATUS_APPROVED, STATUS_PUBLISHED)


def default_material(weighting: Optional[int]) -> bool:
    """The catalogue's default: material when the weighting is 10."""
    return weighting is not None and weighting >= MATERIAL_WEIGHTING


def resolve_material(
    override: Optional[bool], weighting: Optional[int]
) -> Tuple[bool, str]:
    """``(material, source)``; source is ``"control"`` or ``"weighting"``."""
    if override is not None:
        return bool(override), "control"
    return default_material(weighting), "weighting"


def material_expr():
    """SQL for a scoped control being material, over ScopedControl rows.

    The catalogue weighting is read through a correlated subquery so the
    expression can sit in any query over ``scoped_controls`` without the
    caller having to join the catalogue. A control the catalogue no longer
    holds has no weighting and is material only by override. It correlates
    only to ``scoped_controls``, so it also works in a query that already
    joins the catalogue.
    """
    catalog_default = (
        select(SCFCatalogControl.control_weighting >= MATERIAL_WEIGHTING)
        .where(SCFCatalogControl.scf_id == ScopedControl.scf_id)
        .correlate(ScopedControl)
        .scalar_subquery()
    )
    return func.coalesce(ScopedControl.material_override, catalog_default, false())


async def is_material(db: AsyncSession, org_id: UUID, scf_id: str) -> bool:
    """Whether this control is material for the organisation.

    A control with no scope record yet has no override, so it is material
    when the catalogue weights it 10.
    """
    override = await db.scalar(
        select(ScopedControl.material_override).where(
            ScopedControl.organization_id == org_id,
            ScopedControl.scf_id == scf_id,
        )
    )
    if override is not None:
        return bool(override)
    weighting = await db.scalar(
        select(SCFCatalogControl.control_weighting).where(SCFCatalogControl.scf_id == scf_id)
    )
    return default_material(weighting)


def material_exclusion_message(scf_id: str) -> str:
    return (
        f"{scf_id} is a material control. Excluding it needs a reason and an "
        "organisation admin: an admin uses the scope-override endpoint with "
        "action 'exclude' and a reason, and is recorded as the approver."
    )


def material_not_applicable_message(scf_id: str) -> str:
    return (
        f"{scf_id} is a material control. Marking it not applicable needs an "
        "organisation admin, who is recorded in the audit log as the approver."
    )


async def unscope_touches_material(
    db: AsyncSession, org_id: UUID, framework_ids: Sequence[str]
) -> bool:
    """Whether removing these frameworks would take a material control out of scope.

    It mirrors ``scoping_service.bulk_unscope_frameworks``: a control leaves
    only when it is in scope, maps one of the frameworks, has no individual
    ``include``, and maps no other framework the organisation keeps active. A
    material control another framework keeps in scope is not at stake, so
    removing a framework that merely shares weighting-10 controls stays an
    editor's call.
    """
    wanted = sorted(set(framework_ids))
    if not wanted:
        return False
    remaining = sorted(
        set((await db.execute(
            select(OrganizationFrameworkSelection.framework_id).where(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active.is_(True),
            )
        )).scalars().all()) - set(wanted)
    )
    leaving = [
        ScopedControl.organization_id == org_id,
        ScopedControl.selected.is_(True),
        ScopedControl.scope_override.is_distinct_from("include"),
        material_expr(),
        SCFCatalogControl.framework_mappings.has_any(array(wanted)),
    ]
    if remaining:
        leaving.append(~SCFCatalogControl.framework_mappings.has_any(array(remaining)))
    hit = await db.scalar(
        select(literal(1)).where(
            select(ScopedControl.id)
            .join(SCFCatalogControl, SCFCatalogControl.scf_id == ScopedControl.scf_id)
            .where(*leaving)
            .exists()
        )
    )
    return bool(hit)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


async def _scoped(db: AsyncSession, org_id: UUID, scf_id: str) -> ScopedControl:
    control = (await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id,
            )
        )
    )).scalar_one_or_none()
    if control is None:
        raise LookupError(f"{scf_id} has no scope record in this organisation")
    return control


async def set_material_override(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    material: Optional[bool],
    reason: Optional[str],
    approver_user_id: Optional[UUID],
) -> ScopedControl:
    """Set or clear one control's materiality override.

    A set override needs a reason, and ``approver_user_id`` is the admin
    making the change: the caller passes its own id, never one from a payload.

    Clearing returns the control to its catalogue default. When that takes a
    material control out of materiality (a "material" override on a control
    weighted below 10), it is the same decision as setting "not material", so
    it needs a reason too; the reason and the admin stay on the row, with the
    override itself cleared, as the record of why it is no longer material.
    """
    control = await _scoped(db, org_id, scf_id)
    if material is None:
        cleaned = (reason or "").strip()
        weighting = await db.scalar(
            select(SCFCatalogControl.control_weighting).where(SCFCatalogControl.scf_id == scf_id)
        )
        dematerialises = control.material_override is True and not default_material(weighting)
        if dematerialises and not cleaned:
            raise ValueError(
                "Clearing this override makes the control not material; that needs a reason"
            )
        control.material_override = None
        if dematerialises:
            control.material_reason = cleaned
            control.material_set_at = datetime.utcnow()
            control.material_set_by = approver_user_id
        else:
            control.material_reason = None
            control.material_set_at = None
            control.material_set_by = None
    else:
        cleaned = (reason or "").strip()
        if not cleaned:
            raise ValueError("Changing whether a control is material needs a reason")
        control.material_override = bool(material)
        control.material_reason = cleaned
        control.material_set_at = datetime.utcnow()
        control.material_set_by = approver_user_id
    control.updated_by_user_id = approver_user_id
    return control


async def set_procedure_document(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    document_id: Optional[UUID],
    user_id: Optional[UUID],
) -> ScopedControl:
    """Link a control to an approved procedure document, or unlink it.

    The document must belong to the organisation, be a procedure, and be
    approved or published when it is linked. A document edited after linking
    goes back to review through the document lifecycle; the link stays and the
    list reports the document's current status, so the drift is visible.
    """
    control = await _scoped(db, org_id, scf_id)
    if document_id is not None:
        doc = (await db.execute(
            select(GeneratedDocument).where(
                GeneratedDocument.id == document_id,
                GeneratedDocument.organization_id == org_id,
            )
        )).scalar_one_or_none()
        if doc is None:
            raise LookupError("No such document in this organisation")
        if doc.document_type != PROCEDURE_DOCUMENT_TYPE:
            raise ValueError(
                f"Only a procedure document can be linked; this is a {doc.document_type}"
            )
        if doc.lifecycle_status not in SIGNED_OFF_STATUSES:
            raise ValueError(
                "Only an approved or published procedure can be linked; "
                f"this one is {doc.lifecycle_status}"
            )
    control.procedure_document_id = document_id
    control.updated_by_user_id = user_id
    return control
