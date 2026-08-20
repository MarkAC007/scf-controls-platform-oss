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
from typing import Dict, List, Optional, Set
from uuid import UUID

from sqlalchemy import select, and_, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import ScopedControl, OrganizationFrameworkSelection

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

    # Get existing scoped controls for this org WITH their selected status
    existing_query = await db.execute(
        select(ScopedControl.scf_id, ScopedControl.selected)
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
            .values(selected=True, selection_reason=reason)
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
    protected (selected=True). Explicitly-scoped frameworks are derived from
    the "Bulk scoped from:" selection_reason convention. Also deactivates the
    frameworks' organization_framework_selections rows.
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
        select(ScopedControl.scf_id)
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,  # noqa: E712
            )
        )
    )
    in_scope_ids: Set[str] = {row[0] for row in in_scope_query.fetchall()}

    # 3. Determine which frameworks were EXPLICITLY scoped by the user.
    # We parse selection_reason ("Bulk scoped from: iso_27001_2022, ...") to find
    # frameworks the user intentionally added. This avoids the bug where checking
    # ALL framework_mappings of in-scope controls produces a huge set (each SCF
    # control maps to 10-50+ frameworks), causing every control to appear
    # "protected" by frameworks the user never explicitly scoped.
    explicit_fw_query = await db.execute(
        select(ScopedControl.selection_reason)
        .where(
            and_(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected == True,  # noqa: E712
                ScopedControl.selection_reason.like("Bulk scoped from:%")
            )
        )
        .distinct()
    )
    explicitly_scoped_frameworks: Set[str] = set()
    for row in explicit_fw_query.fetchall():
        if row[0]:
            fw_part = row[0].replace("Bulk scoped from:", "").strip()
            for fw in fw_part.split(", "):
                fw = fw.strip()
                if fw:
                    explicitly_scoped_frameworks.add(fw)

    active_frameworks: Set[str] = explicitly_scoped_frameworks - removing_frameworks

    # 4. For each candidate control, check overlap with explicitly-scoped frameworks
    to_remove: Set[str] = set()
    protected_controls: Set[str] = set()
    already_out: Set[str] = set()
    protected_by_count: Dict[str, int] = {}

    for scf_id in framework_control_ids:
        if scf_id not in in_scope_ids:
            already_out.add(scf_id)
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
            .values(selected=False, selection_reason=reason)
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
