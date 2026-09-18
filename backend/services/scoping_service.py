"""
Scoping service: bulk framework scope/unscope operations.

Extracted from api/scoped_controls.py so per-org catalog reconciliation
can re-materialise scope through the same code path the endpoints use.

This module is also the forward-writer of organization_framework_selections,
the structured record that replaces selection_reason free-text parsing as the
source of truth for which frameworks drive scope re-materialisation:
bulk-scope upserts an active selection per requested framework (reactivating
a previously deactivated one); bulk-unscope deactivates them.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import select, and_, case, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import ScopedControl, OrganizationFrameworkSelection
from catalog_models import SCFCatalogControl

logger = logging.getLogger(__name__)


@dataclass
class BulkScopeResult:
    """Outcome of a bulk-scope operation (mirrors BulkScopeFrameworkResponse)."""
    added: int
    updated: int
    skipped: int
    total: int
    frameworks_processed: List[str]
    message: str


@dataclass
class BulkUnscopeResult:
    """Outcome of a bulk-unscope operation (mirrors BulkUnscopeFrameworkResponse)."""
    removed: int
    protected: int
    already_out_of_scope: int
    total: int
    frameworks_processed: List[str]
    message: str
    protected_by: Dict[str, int] = field(default_factory=dict)


def _framework_filter(framework_ids: List[str]):
    """WHERE fragment + params matching catalog rows mapped to any framework."""
    conditions = " OR ".join(
        f"framework_mappings ? :fw_{i}" for i in range(len(framework_ids))
    )
    params = {f"fw_{i}": fw for i, fw in enumerate(framework_ids)}
    return conditions, params


async def _upsert_framework_selections(
    db: AsyncSession,
    org_id: UUID,
    framework_ids: List[str],
    user_id: Optional[UUID],
) -> bool:
    """Ensure an active selection row exists per framework; returns True if rows changed."""
    result = await db.execute(
        select(OrganizationFrameworkSelection).where(
            and_(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.framework_id.in_(framework_ids),
            )
        )
    )
    existing = {sel.framework_id: sel for sel in result.scalars().all()}

    changed = False
    for framework_id in framework_ids:
        selection = existing.get(framework_id)
        if selection is None:
            db.add(OrganizationFrameworkSelection(
                organization_id=org_id,
                framework_id=framework_id,
                source="bulk_scope",
                active=True,
                selected_by=user_id,
            ))
            changed = True
        elif not selection.active:
            selection.active = True
            selection.source = "bulk_scope"
            selection.selected_by = user_id
            selection.selected_at = datetime.utcnow()
            changed = True
        # Active selection: leave untouched (idempotent re-scope).
    return changed


async def _deactivate_framework_selections(
    db: AsyncSession,
    org_id: UUID,
    framework_ids: List[str],
) -> bool:
    """Deactivate selection rows for the frameworks; returns True if rows changed."""
    result = await db.execute(
        select(OrganizationFrameworkSelection).where(
            and_(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.framework_id.in_(framework_ids),
                OrganizationFrameworkSelection.active == True,  # noqa: E712
            )
        )
    )
    changed = False
    for selection in result.scalars().all():
        selection.active = False
        changed = True
    return changed


async def bulk_scope_frameworks(
    db: AsyncSession,
    org_id: UUID,
    framework_ids: List[str],
    user_id: Optional[UUID] = None,
    selection_reason: Optional[str] = None,
    commit: bool = True,
) -> BulkScopeResult:
    """
    Add all active catalog controls mapped to the frameworks to the org's scope.

    ADDITIVE ONLY — controls already in scope are never modified or overwritten.
    Three-way partition of the framework's controls:
      - not scoped yet          → new ScopedControl row (selected=True)
      - scoped, selected=False  → flipped back to selected=True
      - scoped, selected=True   → skipped
    Also upserts organization_framework_selections (source='bulk_scope').

    Set commit=False when running inside a caller-managed transaction
    (e.g. reconciliation apply).
    """
    framework_conditions, params = _framework_filter(framework_ids)

    # The interpolated fragment contains only generated ":fw_N" placeholder
    # names; every framework id value is passed as a bound parameter.
    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
    catalog_query = text(f"""
        SELECT scf_id
        FROM scf_catalog_controls
        WHERE ({framework_conditions})
          AND status = 'active'
    """)

    catalog_result = await db.execute(catalog_query, params)
    framework_control_ids: Set[str] = {row[0] for row in catalog_result.fetchall()}

    if not framework_control_ids:
        return BulkScopeResult(
            added=0,
            updated=0,
            skipped=0,
            total=0,
            frameworks_processed=framework_ids,
            message=f"No controls found for frameworks: {', '.join(framework_ids)}"
        )

    # Explicit exclusions behave as already selected for framework materialisation.
    # The historical two-column shape is retained for reconciliation adapters.
    effective_selected = case(
        (ScopedControl.scope_override == "exclude", True),
        else_=ScopedControl.selected,
    ).label("selected")
    existing_query = await db.execute(
        select(ScopedControl.scf_id, effective_selected)
        .where(ScopedControl.organization_id == org_id)
    )
    existing_controls = {row[0]: row[1] for row in existing_query.fetchall()}
    # Partition framework controls into three buckets
    new_control_ids: Set[str] = set()
    needs_update_ids: Set[str] = set()
    already_scoped_ids: Set[str] = set()

    for scf_id in framework_control_ids:
        if scf_id not in existing_controls:
            new_control_ids.add(scf_id)
        elif not existing_controls[scf_id]:
            needs_update_ids.add(scf_id)
        else:
            already_scoped_ids.add(scf_id)

    reason = selection_reason or f"Bulk scoped from: {', '.join(framework_ids)}"

    # Batch insert new controls
    added_count = 0
    for scf_id in new_control_ids:
        new_control = ScopedControl(
            organization_id=org_id,
            scf_id=scf_id,
            selected=True,
            implementation_status="not_started",
            selection_reason=reason,
        )
        db.add(new_control)
        added_count += 1

    # Update existing controls that have selected=False → True
    updated_count = 0
    if needs_update_ids:
        await db.execute(
            ScopedControl.__table__.update()
            .where(
                and_(
                    ScopedControl.organization_id == org_id,
                    ScopedControl.scf_id.in_(needs_update_ids)
                )
            )
            .values(
                selected=True,
                selection_reason=reason,
                out_of_scope_justification=None,
            )
        )
        updated_count = len(needs_update_ids)

    selections_changed = await _upsert_framework_selections(
        db, org_id, framework_ids, user_id
    )

    if commit and (added_count > 0 or updated_count > 0 or selections_changed):
        await db.commit()

    skipped_count = len(already_scoped_ids)

    logger.info(
        f"Bulk scope by framework: org={org_id}, frameworks={framework_ids}, "
        f"added={added_count}, updated={updated_count}, skipped={skipped_count}"
    )

    # Build response message
    framework_names = ", ".join(framework_ids)
    parts = []
    if added_count > 0:
        parts.append(f"Added {added_count} new controls")
    if updated_count > 0:
        parts.append(f"updated {updated_count} existing controls")
    if parts:
        message = f"{' and '.join(parts)} from {framework_names}"
        if skipped_count > 0:
            message += f" ({skipped_count} already in scope)"
    else:
        message = f"All {len(framework_control_ids)} controls from {framework_names} already in scope"

    return BulkScopeResult(
        added=added_count,
        updated=updated_count,
        skipped=skipped_count,
        total=len(framework_control_ids),
        frameworks_processed=framework_ids,
        message=message
    )


async def bulk_unscope_frameworks(
    db: AsyncSession,
    org_id: UUID,
    framework_ids: List[str],
    removal_reason: Optional[str] = None,
    commit: bool = True,
) -> BulkUnscopeResult:
    """
    Remove the frameworks' controls from scope, with overlap protection.

    Controls mapped to any OTHER framework the org explicitly scoped remain
    protected (selected=True). Active organization_framework_selections are
    authoritative; individual inclusions also protect controls. The removed
    framework selections are deactivated in the same transaction.
    """
    removing_frameworks = set(framework_ids)

    # 1. Find all catalog controls mapped to the frameworks being removed
    framework_conditions, params = _framework_filter(framework_ids)

    # The interpolated fragment contains only generated ":fw_N" placeholder
    # names; every framework id value is passed as a bound parameter.
    # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
    catalog_query = text(f"""
        SELECT scf_id, framework_mappings
        FROM scf_catalog_controls
        WHERE {framework_conditions}
    """)

    catalog_result = await db.execute(catalog_query, params)
    catalog_rows = catalog_result.fetchall()

    if not catalog_rows:
        return BulkUnscopeResult(
            removed=0,
            protected=0,
            already_out_of_scope=0,
            total=0,
            frameworks_processed=framework_ids,
            message=f"No controls found for frameworks: {', '.join(framework_ids)}"
        )

    # Build map: scf_id → set of framework keys
    control_frameworks: Dict[str, set] = {}
    for row in catalog_rows:
        scf_id = row[0]
        fw_mappings = row[1] or {}
        control_frameworks[scf_id] = set(fw_mappings.keys())

    framework_control_ids = set(control_frameworks.keys())

    # 2. Get all in-scope controls for this org
    in_scope_query = await db.execute(
        select(ScopedControl.scf_id, ScopedControl.scope_override)
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,  # noqa: E712
            )
        )
    )
    in_scope_overrides: Dict[str, Optional[str]] = {
        row[0]: (row[1] if len(row) > 1 else None) for row in in_scope_query.fetchall()
    }

    # 3. Structured framework selections are the sole authority.
    explicit_fw_query = await db.execute(
        select(OrganizationFrameworkSelection.framework_id).where(
            and_(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active == True,  # noqa: E712
            )
        )
    )
    explicitly_scoped_frameworks = {row[0] for row in explicit_fw_query.fetchall()}
    active_frameworks: Set[str] = explicitly_scoped_frameworks - removing_frameworks

    # 4. For each candidate control, check overlap with explicitly-scoped frameworks
    to_remove: Set[str] = set()
    protected_controls: Set[str] = set()
    already_out: Set[str] = set()
    protected_by_count: Dict[str, int] = {}

    for scf_id in framework_control_ids:
        if scf_id not in in_scope_overrides:
            already_out.add(scf_id)
            continue

        if in_scope_overrides[scf_id] == "include":
            protected_controls.add(scf_id)
            protected_by_count["individual_inclusion"] = protected_by_count.get("individual_inclusion", 0) + 1
            continue

        # Check if this control maps to any other explicitly-scoped framework
        other_active_fws = control_frameworks[scf_id] & active_frameworks
        if other_active_fws:
            # Protected — overlaps with other in-scope frameworks
            protected_controls.add(scf_id)
            for fw in other_active_fws:
                protected_by_count[fw] = protected_by_count.get(fw, 0) + 1
        else:
            # Safe to remove — no overlap
            to_remove.add(scf_id)

    # 5. Bulk update: set selected=False for removable controls
    removed_count = 0
    if to_remove:
        reason = removal_reason or f"Bulk un-scoped from: {', '.join(framework_ids)}"
        await db.execute(
            ScopedControl.__table__.update()
            .where(
                and_(
                    ScopedControl.organization_id == org_id,
                    ScopedControl.scf_id.in_(to_remove)
                )
            )
            .values(selected=False, out_of_scope_justification=reason)
        )
        removed_count = len(to_remove)

    selections_changed = await _deactivate_framework_selections(
        db, org_id, framework_ids
    )

    if commit and (removed_count > 0 or selections_changed):
        await db.commit()

    logger.info(
        f"Bulk unscope by framework: org={org_id}, frameworks={framework_ids}, "
        f"removed={removed_count}, protected={len(protected_controls)}, "
        f"already_out={len(already_out)}, "
        f"explicitly_scoped={explicitly_scoped_frameworks}, "
        f"active_after_removal={active_frameworks}"
    )

    # Build response message
    framework_names = ", ".join(framework_ids)
    if removed_count > 0:
        message = f"Removed {removed_count} controls from {framework_names}"
        if protected_controls:
            message += f". {len(protected_controls)} controls protected by overlap with other in-scope frameworks"
    elif protected_controls:
        message = (
            f"No controls removed from {framework_names} — all {len(protected_controls)} "
            f"are shared with other in-scope frameworks"
        )
    else:
        message = f"No in-scope controls found for {framework_names}"

    return BulkUnscopeResult(
        removed=removed_count,
        protected=len(protected_controls),
        already_out_of_scope=len(already_out),
        total=len(framework_control_ids),
        protected_by=protected_by_count,
        frameworks_processed=framework_ids,
        message=message
    )
INTERNAL_MAPPING_PREFIXES = (
    "risk_",
    "threat_",
    "scf_core_",
    "control_threat_summary",
    "risk_threat_summary",
    "minimum_security_requirements_mcr_dsr",
    "identify_",
    "errata_",
)


def _framework_name(framework_id: str) -> str:
    return framework_id.replace("_", " ").strip().title()


def _framework_family(framework_id: str) -> str:
    groups = (
        ("international", ("iso_", "iec_", "bsi_", "cobit_", "coso_", "apec_", "oecd_")),
        ("us_federal", ("us_fedramp_", "us_nist_", "us_cmmc_", "us_hipaa_", "nist_", "pci_dss_")),
        ("us_state", ("us_ak_", "us_ca_", "us_co_", "us_ct_", "us_de_", "us_fl_", "us_il_", "us_ny_", "us_tx_", "us_va_", "us_wa_")),
        ("emea", ("emea_",)),
        ("apac", ("apac_",)),
        ("americas", ("americas_",)),
        ("industry", ("aicpa_", "swift_", "tisax_", "csa_", "mitre_", "govramp_", "sparta")),
    )
    for family, prefixes in groups:
        if framework_id.startswith(prefixes):
            return family
    return "other"


async def framework_scope_summary(db: AsyncSession, org_id: UUID) -> Dict[str, Any]:
    """Return the organization overlay for every selectable catalog framework."""
    catalog_result = await db.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.status == "active")
    )
    catalog_controls = catalog_result.scalars().all()

    scoped_result = await db.execute(
        select(ScopedControl).where(ScopedControl.organization_id == org_id)
    )
    scoped_by_id = {row.scf_id: row for row in scoped_result.scalars().all()}

    selection_result = await db.execute(
        select(OrganizationFrameworkSelection).where(
            OrganizationFrameworkSelection.organization_id == org_id
        )
    )
    selections = {
        row.framework_id: row for row in selection_result.scalars().all()
    }

    control_ids_by_framework: Dict[str, Set[str]] = {}
    for control in catalog_controls:
        for framework_id in (control.framework_mappings or {}):
            if framework_id.startswith(INTERNAL_MAPPING_PREFIXES):
                continue
            control_ids_by_framework.setdefault(framework_id, set()).add(control.scf_id)

    frameworks = []
    for framework_id, control_ids in control_ids_by_framework.items():
        selection = selections.get(framework_id)
        active = bool(selection and selection.active)
        in_scope = {
            scf_id
            for scf_id in control_ids
            if scoped_by_id.get(scf_id) is not None and scoped_by_id[scf_id].selected
        }
        explicit_exclusions = {
            scf_id
            for scf_id in control_ids
            if scoped_by_id.get(scf_id) is not None
            and scoped_by_id[scf_id].scope_override == "exclude"
        }
        mapped = len(control_ids)
        missing = mapped - len(in_scope)
        frameworks.append(
            {
                "id": framework_id,
                "name": _framework_name(framework_id),
                "family": _framework_family(framework_id),
                "mapped_control_count": mapped,
                "in_scope_count": len(in_scope),
                "missing_count": missing,
                "coverage_percentage": round((len(in_scope) / mapped) * 100, 1) if mapped else 0.0,
                "expected_additions": len(control_ids - in_scope - explicit_exclusions),
                "active": active,
                "partial": active and missing > 0,
                "source": selection.source if selection else None,
                "selected_at": selection.selected_at if selection else None,
                "selected_by": selection.selected_by if selection else None,
            }
        )

    frameworks.sort(key=lambda item: (not item["active"], item["name"]))
    return {
        "total": len(frameworks),
        "selected_count": sum(1 for item in frameworks if item["active"]),
        "frameworks": frameworks,
    }


async def preview_framework_change(
    db: AsyncSession,
    org_id: UUID,
    framework_ids: List[str],
    operation: str,
) -> Dict[str, Any]:
    """Compute exact add/remove effects from current structured scope state."""
    catalog_result = await db.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.status == "active")
    )
    catalog_controls = catalog_result.scalars().all()
    requested = set(framework_ids)
    mappings_by_control = {
        row.scf_id: set((row.framework_mappings or {}).keys())
        for row in catalog_controls
        if set((row.framework_mappings or {}).keys()) & requested
    }

    scoped_result = await db.execute(
        select(ScopedControl).where(ScopedControl.organization_id == org_id)
    )
    scoped = {row.scf_id: row for row in scoped_result.scalars().all()}

    selection_result = await db.execute(
        select(OrganizationFrameworkSelection.framework_id).where(
            and_(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active == True,  # noqa: E712
            )
        )
    )
    active_frameworks = {row[0] for row in selection_result.fetchall()}
    # The frameworks that justify a control independently of the one being changed. For a
    # remove this is the post-change active set; for an add it is the pre-change set minus
    # the requested framework. Both are the same expression, and using it for `add` is what
    # lets the add path report real overlap instead of a structural zero.
    other_active = active_frameworks - requested

    preview: Dict[str, Any] = {
        "operation": operation,
        "frameworks": sorted(requested),
        "mapped_controls": sorted(mappings_by_control),
        "new_controls": [],
        "already_covered": [],
        "shared_with_active_frameworks": [],
        "individual_inclusions": [],
        "explicitly_excluded": [],
        "controls_leaving_scope": [],
    }

    for scf_id, mappings in mappings_by_control.items():
        row = scoped.get(scf_id)
        selected = bool(row and row.selected)
        override = row.scope_override if row else None
        if override == "exclude":
            preview["explicitly_excluded"].append(scf_id)
            continue
        if operation == "add":
            if not selected:
                preview["new_controls"].append(scf_id)
            elif override == "include":
                preview["individual_inclusions"].append(scf_id)
            elif mappings & other_active:
                preview["shared_with_active_frameworks"].append(scf_id)
            else:
                preview["already_covered"].append(scf_id)
            continue
        if not selected:
            preview["already_covered"].append(scf_id)
        elif override == "include":
            preview["individual_inclusions"].append(scf_id)
        elif mappings & other_active:
            preview["shared_with_active_frameworks"].append(scf_id)
        else:
            preview["controls_leaving_scope"].append(scf_id)

    for key, value in preview.items():
        if isinstance(value, list):
            value.sort()
    return preview


async def set_individual_scope_override(
    db: AsyncSession,
    org_id: UUID,
    scf_id: str,
    action: str,
    reason: Optional[str],
    user_id: Optional[UUID],
    *,
    commit: bool = True,
) -> ScopedControl:
    """Apply framework-baseline + individual-override precedence to one control."""
    result = await db.execute(
        select(ScopedControl).where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id == scf_id,
            )
        )
    )
    control = result.scalar_one_or_none()

    catalog_result = await db.execute(
        select(SCFCatalogControl).where(SCFCatalogControl.scf_id == scf_id)
    )
    catalog = catalog_result.scalar_one_or_none()
    if catalog is None:
        raise ValueError("Control not found in the SCF catalog")

    if control is None:
        if action == "inherit":
            raise ValueError("Control has no individual override")
        control = ScopedControl(
            organization_id=org_id,
            scf_id=scf_id,
            implementation_status="not_started",
            created_by_user_id=user_id,
        )
        db.add(control)

    now = datetime.utcnow()
    if action == "include":
        control.selected = True
        control.scope_override = "include"
        control.scope_override_reason = (reason or "").strip() or "Individually included"
        control.selection_reason = control.scope_override_reason
        control.out_of_scope_justification = None
    elif action == "exclude":
        cleaned = (reason or "").strip()
        if not cleaned:
            raise ValueError("An exclusion rationale is required")
        control.selected = False
        control.scope_override = "exclude"
        control.scope_override_reason = cleaned
        control.out_of_scope_justification = cleaned
    else:
        selection_result = await db.execute(
            select(OrganizationFrameworkSelection.framework_id).where(
                and_(
                    OrganizationFrameworkSelection.organization_id == org_id,
                    OrganizationFrameworkSelection.active == True,  # noqa: E712
                )
            )
        )
        active_frameworks = {row[0] for row in selection_result.fetchall()}
        control.selected = bool(set((catalog.framework_mappings or {}).keys()) & active_frameworks)
        control.scope_override = None
        control.scope_override_reason = None
        control.out_of_scope_justification = None

    control.scope_override_set_at = now if action != "inherit" else None
    control.scope_override_set_by = user_id if action != "inherit" else None
    control.updated_by_user_id = user_id
    if commit:
        await db.commit()
        await db.refresh(control)
    return control
