"""Per-org catalog reconciliation service (WP2b read side + WP2c apply/rollback,
plan §4.3).

Covers eligibility, skip-version diff union, the synchronous preview
(branches a–e), planned-action defaults, the orphan report, planned-action
edits, org changelog assembly, and — WP2c — the transactional apply, the
snapshot-restore rollback, and cancel. The Celery tasks that drive apply and
rollback live in ``tasks_reconciliation.py``.

Design notes:

- **Ledger authority.** The platform catalog version and the run window for a
  skip-version union are both derived from the same in-memory list of applied
  ``CatalogImportRun`` rows (the same rule as
  ``services.catalog_apply.get_current_catalog_version``: latest applied run's
  ``to_version``). Deriving both from one list means the reported version and
  the unioned runs can never disagree within a single preview.
- **Skip-version union.** When an org is two or more applied runs behind, the
  per-run ``DiffDetail`` objects are unioned in apply order. Later changes win
  per key; add-then-deprecate collapses to deprecated; deprecate-then-resurrect
  collapses to the net effect against the org's from-version (changed or
  unchanged). Per-row timestamps cannot make these distinctions — the ledger
  can (plan §4.3).
- **Query style.** Selects fetch whole model rows and filter/order in Python.
  Result sets are small (one org's rows; the catalog; the run ledger) and this
  keeps the service runnable against the suite's fake-session harness, which
  returns tables wholesale.
- **Diff details** live in object storage next to the platform run
  (``tasks_catalog.diff_detail_object_key``). The default loader streams them
  from ``s3_service``; previews/changelog calls run it via ``asyncio.to_thread``
  so the (synchronous, per plan §4.3) preview endpoint does not block the event
  loop. Tests inject an in-memory loader.
"""
from __future__ import annotations

import asyncio
import logging
import re
import uuid as uuid_module
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import UUID

from sqlalchemy import Date as SA_Date, DateTime as SA_DateTime, select, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogControl, SCFCatalogEvidence
from models import (
    CatalogImportRun,
    CDMMapping,
    ControlAssessmentComposite,
    EngagementControlScope,
    EvidenceCollectionTask,
    EvidenceTracking,
    OrganizationCatalogState,
    OrganizationFrameworkSelection,
    OrganizationReconciliationRun,
    ScopedControl,
)
from schemas_catalog_upgrade import (
    AddedEntity,
    CatalogEntityType,
    ChangeClass,
    ChangedEntity,
    ChangedInScopeItem,
    ChangelogEntry,
    DeprecatedEntity,
    DeprecatedImpactItem,
    DiffDetail,
    DiffSummary,
    EntityDiff,
    EntityDiffCounts,
    FieldChange,
    FrameworkConfirmation,
    FrameworkSelectionItem,
    OrgSnapshot,
    OrgSnapshotRow,
    OrphanItem,
    OrphanReport,
    PlannedAction,
    PlannedActionType,
    ResurrectedEntity,
    ScopeAdditionItem,
    ScopeAdditionsPreview,
)
from services.catalog_apply import CATALOG_LOCK_KEY
from services.scoping_service import bulk_scope_frameworks

logger = logging.getLogger(__name__)

# Org run statuses that count as "active" (the catupg005 partial-unique set).
ACTIVE_RUN_STATUSES = ("previewed", "applying", "rolling_back")

DetailLoader = Callable[[CatalogImportRun], DiffDetail]


# ---------------------------------------------------------------------------
# Errors (the API layer maps these to HTTP statuses)
# ---------------------------------------------------------------------------


class ReconciliationError(Exception):
    """Base error for org reconciliation."""


class NotEligibleError(ReconciliationError):
    """The org is already at (or ahead of) the platform catalog version, or
    there is nothing applied to reconcile against."""


class ActiveRunConflictError(ReconciliationError):
    """An apply/rollback is in flight for the org; no new preview allowed."""


class RunNotFoundError(ReconciliationError):
    """No reconciliation run with that id belongs to the org."""


class RunStateError(ReconciliationError):
    """The run is not in the status the operation requires."""


class ActionValidationError(ReconciliationError):
    """A planned-actions update failed validation against the preview."""

    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or [message]


class DiffDetailUnavailableError(ReconciliationError):
    """A platform run's stored diff detail could not be loaded."""


class StalePreviewError(ReconciliationError):
    """A newer platform run was applied after this preview was anchored;
    the preview no longer describes the catch-up the org would take."""


class FrameworksNotConfirmedError(ReconciliationError):
    """First reconciliation: the heuristic framework selections were never
    confirmed by an admin (plan §4.3e); apply is refused."""


class SnapshotUnavailableError(ReconciliationError):
    """The applied run carries no org_snapshot; rollback has no authority."""


class RollbackNotLatestError(ReconciliationError):
    """Only the org's latest applied reconciliation run may roll back."""


# ---------------------------------------------------------------------------
# Version ordering
# ---------------------------------------------------------------------------


def _version_key(version: Optional[str]) -> Tuple:
    """Sortable key for catalog versions (numeric segments, string tiebreak)."""
    if not version:
        return ((), "")
    return (tuple(int(part) for part in re.findall(r"\d+", version)), version)


def _now() -> datetime:
    # Reconciliation tables use naive UTC timestamps, like the catalog tables.
    return datetime.utcnow()


# ---------------------------------------------------------------------------
# Default diff-detail loader (object storage)
# ---------------------------------------------------------------------------


def load_diff_detail_from_storage(run: CatalogImportRun) -> DiffDetail:
    """Fetch a platform run's stored DiffDetail JSON from object storage."""
    from services import s3_service

    key = run.diff_detail_object_key or f"_catalog-upgrade/{run.id}/diff_detail.json"
    chunks = s3_service.download_blob_stream(key)
    if chunks is None:
        raise DiffDetailUnavailableError(
            f"diff detail not found in storage for import run {run.id} ({key})"
        )
    return DiffDetail.model_validate_json(b"".join(chunks))


# ---------------------------------------------------------------------------
# Ledger access (whole-row selects, Python-side filtering — see module notes)
# ---------------------------------------------------------------------------


async def applied_platform_runs(session: AsyncSession) -> List[CatalogImportRun]:
    """All applied platform import runs, oldest first (apply order)."""
    result = await session.execute(select(CatalogImportRun))
    runs = [r for r in result.scalars().all() if r.status == "applied"]
    runs.sort(key=lambda r: (r.completed_at or r.created_at or _now()))
    return runs


def select_runs_for_union(
    runs: List[CatalogImportRun],
    org_version: Optional[str],
    target_version: Optional[str] = None,
) -> List[CatalogImportRun]:
    """The applied runs an org must catch up through, in apply order.

    Runs at or below the org's reconciled version are excluded; a target
    version (when given) bounds the window from above.
    """
    org_key = _version_key(org_version) if org_version else None
    target_key = _version_key(target_version) if target_version else None
    selected = []
    for run in runs:
        run_key = _version_key(run.to_version)
        if org_key is not None and run_key <= org_key:
            continue
        if target_key is not None and run_key > target_key:
            continue
        selected.append(run)
    return selected


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


@dataclass
class EligibilityInfo:
    """Org-vs-platform catalog position, plus the run context around it."""

    organization_id: UUID
    reconciled_catalog_version: Optional[str]
    platform_catalog_version: Optional[str]
    eligible: bool
    first_reconciliation: bool
    last_reconciled_at: Optional[datetime]
    active_run: Optional[OrganizationReconciliationRun]
    stale_preview: bool
    latest_platform_run: Optional[CatalogImportRun]


async def _org_rows(session: AsyncSession, model, org_id: UUID) -> List[Any]:
    result = await session.execute(select(model))
    return [
        row
        for row in result.scalars().all()
        if getattr(row, "organization_id", None) == org_id
    ]


async def get_active_run(
    session: AsyncSession, org_id: UUID
) -> Optional[OrganizationReconciliationRun]:
    """The org's single active run (previewed/applying/rolling_back), if any."""
    runs = await _org_rows(session, OrganizationReconciliationRun, org_id)
    active = [r for r in runs if r.status in ACTIVE_RUN_STATUSES]
    if not active:
        return None
    active.sort(key=lambda r: r.created_at or _now())
    return active[-1]


async def check_eligibility(session: AsyncSession, org_id: UUID) -> EligibilityInfo:
    """Compare the org's reconciled version against the platform ledger.

    - eligible: an applied platform run exists whose version exceeds the org's
      reconciled version (or the org has never reconciled at all);
    - first_reconciliation: the org has no catalog-state row, or the row was
      backfilled (catupg003) and no reconciliation has ever completed —
      either way the heuristic framework selections need admin confirmation
      (plan §4.3e);
    - stale_preview: the org's active previewed run is anchored to an import
      run that is no longer the latest applied one.
    """
    states = await _org_rows(session, OrganizationCatalogState, org_id)
    state = states[0] if states else None

    ledger = await applied_platform_runs(session)
    latest = ledger[-1] if ledger else None
    platform_version = latest.to_version if latest else None

    org_version = state.reconciled_catalog_version if state else None
    eligible = platform_version is not None and (
        org_version is None or _version_key(platform_version) > _version_key(org_version)
    )

    active_run = await get_active_run(session, org_id)
    stale_preview = bool(
        active_run
        and active_run.status == "previewed"
        and latest is not None
        and active_run.catalog_import_run_id != latest.id
    )

    first_reconciliation = state is None or state.last_reconciled_at is None

    return EligibilityInfo(
        organization_id=org_id,
        reconciled_catalog_version=org_version,
        platform_catalog_version=platform_version,
        eligible=eligible,
        first_reconciliation=first_reconciliation,
        last_reconciled_at=state.last_reconciled_at if state else None,
        active_run=active_run,
        stale_preview=stale_preview,
        latest_platform_run=latest,
    )


# ---------------------------------------------------------------------------
# Skip-version diff union (pure function)
# ---------------------------------------------------------------------------


@dataclass
class _KeyState:
    """Net position of one entity key across the unioned runs."""

    base: str  # "absent" | "active" | "deprecated" — state at the org's from-version
    active: bool  # final state after the last unioned run
    name: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)  # for base-absent adds
    fields: Dict[str, FieldChange] = field(default_factory=dict)
    superseded_by: Optional[str] = None
    suggestions: List = field(default_factory=list)


def _merge_fields(state: _KeyState, changes: Dict[str, FieldChange]) -> None:
    """Earliest old + latest new per field (later changes win, plan §4.3)."""
    for name, change in changes.items():
        existing = state.fields.get(name)
        if existing is None:
            state.fields[name] = FieldChange(old=change.old, new=change.new)
        else:
            existing.new = change.new


def union_diff_details(details: List[DiffDetail]) -> DiffDetail:
    """Union per-run diffs, in apply order, into one net diff.

    Collapse rules against the org's from-version:
    - later changes win per key/field (earliest old value, latest new value);
    - added then deprecated → deprecated (the org never saw it active);
    - deprecated then resurrected → changed with the net field delta, or
      unchanged when the fields round-trip;
    - fields whose merged old and new values are equal are dropped, and a
      changed row with no surviving fields degrades to unchanged.
    """
    if not details:
        raise ValueError("union_diff_details requires at least one DiffDetail")

    states: Dict[CatalogEntityType, Dict[str, _KeyState]] = {}
    unchanged_seen: Dict[CatalogEntityType, set] = {}

    for detail in details:
        for entity, diff in detail.entities.items():
            entity_states = states.setdefault(entity, {})
            unchanged_seen.setdefault(entity, set()).update(diff.unchanged)

            for added in diff.added:
                state = entity_states.get(added.key)
                if state is None:
                    entity_states[added.key] = _KeyState(
                        base="absent", active=True,
                        name=added.name, data=dict(added.data),
                    )
                else:
                    state.active = True
                    state.name = added.name or state.name
                    if state.base == "absent":
                        state.data.update(added.data)

            for changed in diff.changed:
                state = entity_states.get(changed.key)
                if state is None:
                    state = _KeyState(base="active", active=True, name=changed.name)
                    entity_states[changed.key] = state
                state.name = changed.name or state.name
                if state.base == "absent" and state.active:
                    # A row this union adds: fold changes into its data.
                    state.data.update({n: fc.new for n, fc in changed.fields.items()})
                else:
                    _merge_fields(state, changed.fields)

            for deprecated in diff.deprecated:
                state = entity_states.get(deprecated.key)
                if state is None:
                    state = _KeyState(base="active", active=False, name=deprecated.name)
                    entity_states[deprecated.key] = state
                state.active = False
                state.name = deprecated.name or state.name
                state.superseded_by = deprecated.superseded_by
                state.suggestions = list(deprecated.suggestions)

            for resurrected in diff.resurrected:
                state = entity_states.get(resurrected.key)
                if state is None:
                    state = _KeyState(base="deprecated", active=True, name=resurrected.name)
                    entity_states[resurrected.key] = state
                state.active = True
                state.name = resurrected.name or state.name
                state.superseded_by = None
                state.suggestions = []
                if state.base == "absent":
                    state.data.update({n: fc.new for n, fc in resurrected.fields.items()})
                else:
                    _merge_fields(state, resurrected.fields)

    entities: Dict[CatalogEntityType, EntityDiff] = {}
    for entity, entity_states in states.items():
        diff = EntityDiff()
        classified = set()
        for key in sorted(entity_states):
            state = entity_states[key]
            net_fields = {
                n: fc for n, fc in state.fields.items() if fc.old != fc.new
            }
            if state.base == "absent":
                if state.active:
                    diff.added.append(
                        AddedEntity(key=key, name=state.name, data=state.data)
                    )
                else:
                    # Add-then-deprecate: surfaces as deprecated (count-only for
                    # orgs, which cannot have data on a row they never saw).
                    diff.deprecated.append(
                        DeprecatedEntity(
                            key=key, name=state.name,
                            superseded_by=state.superseded_by,
                            suggestions=state.suggestions,
                        )
                    )
                classified.add(key)
            elif state.base == "active":
                if not state.active:
                    diff.deprecated.append(
                        DeprecatedEntity(
                            key=key, name=state.name,
                            superseded_by=state.superseded_by,
                            suggestions=state.suggestions,
                        )
                    )
                    classified.add(key)
                elif net_fields:
                    diff.changed.append(
                        ChangedEntity(key=key, name=state.name, fields=net_fields)
                    )
                    classified.add(key)
                # else: net no-op (e.g. deprecate→resurrect round trip) → unchanged
            else:  # base deprecated
                if state.active:
                    diff.resurrected.append(
                        ResurrectedEntity(key=key, name=state.name, fields=net_fields)
                    )
                    classified.add(key)
                # else: deprecated at base and still deprecated → unchanged

        unclassified = (
            set(entity_states) | unchanged_seen.get(entity, set())
        ) - classified
        diff.unchanged = sorted(unclassified)
        entities[entity] = diff

    return DiffDetail(
        from_version=details[0].from_version,
        to_version=details[-1].to_version,
        entities=entities,
    )


def summarize_diff(detail: DiffDetail) -> DiffSummary:
    """Count-only view of a diff (the org run's diff_summary column shape)."""
    return DiffSummary(
        from_version=detail.from_version,
        to_version=detail.to_version,
        entities={
            entity: EntityDiffCounts(
                added=len(diff.added),
                changed=len(diff.changed),
                deprecated=len(diff.deprecated),
                resurrected=len(diff.resurrected),
                unchanged=len(diff.unchanged),
            )
            for entity, diff in detail.entities.items()
        },
    )


# ---------------------------------------------------------------------------
# Preview (plan §4.3 branches a–e)
# ---------------------------------------------------------------------------


@dataclass
class PreviewResult:
    run: OrganizationReconciliationRun
    additions: ScopeAdditionsPreview
    deprecated_impacts: List[DeprecatedImpactItem]
    changed_in_scope: List[ChangedInScopeItem]
    orphans: OrphanReport
    framework_confirmation: FrameworkConfirmation
    union_detail: DiffDetail
    eligibility: EligibilityInfo


async def _load_union(
    runs: List[CatalogImportRun], detail_loader: DetailLoader
) -> DiffDetail:
    details = []
    for run in runs:
        details.append(await asyncio.to_thread(detail_loader, run))
    return union_diff_details(details)


def _default_action_for(superseded_by: Optional[str]) -> PlannedActionType:
    # Plan §4.3b: migrate is the default when a successor is paired; without
    # one, retain (safe for orgs mid-engagement) — retire_only is opt-in.
    return PlannedActionType.MIGRATE if superseded_by else PlannedActionType.RETAIN


async def build_preview(
    session: AsyncSession,
    org_id: UUID,
    *,
    user_id: Optional[UUID] = None,
    target_version: Optional[str] = None,
    detail_loader: Optional[DetailLoader] = None,
    commit: bool = True,
) -> PreviewResult:
    """Synchronous org-impact preview; creates a run in status 'previewed'.

    A prior previewed run is superseded (cancelled) by a new preview; an
    in-flight apply or rollback raises ActiveRunConflictError instead.
    """
    loader = detail_loader or load_diff_detail_from_storage
    eligibility = await check_eligibility(session, org_id)

    if eligibility.platform_catalog_version is None:
        raise NotEligibleError("no applied platform catalog run exists yet")
    if not eligibility.eligible:
        raise NotEligibleError(
            f"organisation is already reconciled to catalog "
            f"{eligibility.reconciled_catalog_version} "
            f"(platform: {eligibility.platform_catalog_version})"
        )
    if target_version is not None and _version_key(target_version) > _version_key(
        eligibility.platform_catalog_version
    ):
        raise NotEligibleError(
            f"target version {target_version} is ahead of the platform catalog "
            f"version {eligibility.platform_catalog_version}"
        )

    active = eligibility.active_run
    if active is not None:
        if active.status != "previewed":
            raise ActiveRunConflictError(
                f"run {active.id} is {active.status}; wait for it to finish"
            )
        # Re-preview supersedes the stale/undecided one.
        active.status = "cancelled"
        active.updated_at = _now()

    ledger = await applied_platform_runs(session)
    runs_to_union = select_runs_for_union(
        ledger, eligibility.reconciled_catalog_version, target_version
    )
    if not runs_to_union:
        raise NotEligibleError(
            f"no applied catalog runs between "
            f"{eligibility.reconciled_catalog_version} and "
            f"{target_version or eligibility.platform_catalog_version}"
        )

    union = await _load_union(runs_to_union, loader)

    # --- org + catalog context -------------------------------------------
    scoped_rows = await _org_rows(session, ScopedControl, org_id)
    scoped_by_id = {row.scf_id: row for row in scoped_rows}
    tracking_rows = await _org_rows(session, EvidenceTracking, org_id)
    tracking_by_id: Dict[str, List[Any]] = {}
    for row in tracking_rows:
        tracking_by_id.setdefault(row.evidence_id, []).append(row)
    composites = await _org_rows(session, ControlAssessmentComposite, org_id)
    composite_ids = {row.scf_id for row in composites}
    selections = await _org_rows(session, OrganizationFrameworkSelection, org_id)
    active_framework_ids = {s.framework_id for s in selections if s.active}

    result = await session.execute(select(SCFCatalogControl))
    catalog_controls = {row.scf_id: row for row in result.scalars().all()}
    result = await session.execute(select(SCFCatalogEvidence))
    catalog_evidence = {row.evidence_id: row for row in result.scalars().all()}

    controls_diff = union.entities.get(CatalogEntityType.CONTROLS, EntityDiff())
    evidence_diff = union.entities.get(CatalogEntityType.EVIDENCE, EntityDiff())

    # --- (a) additions vs the org's active framework selections ----------
    in_scope: List[ScopeAdditionItem] = []
    out_of_scope = 0
    for added in controls_diff.added:
        mapped = set((added.data.get("framework_mappings") or {}).keys())
        matched = sorted(mapped & active_framework_ids)
        if matched:
            in_scope.append(
                ScopeAdditionItem(scf_id=added.key, name=added.name, frameworks=matched)
            )
        else:
            out_of_scope += 1
    additions = ScopeAdditionsPreview(in_scope=in_scope, out_of_scope_count=out_of_scope)

    # --- (b) deprecated entities the org has data on ----------------------
    impacts: List[DeprecatedImpactItem] = []
    planned: List[PlannedAction] = []
    for dep in controls_diff.deprecated:
        scoped = scoped_by_id.get(dep.key)
        if scoped is None:
            continue  # no org data at stake — count-only via diff_summary
        catalog_row = catalog_controls.get(dep.key)
        superseded = (
            getattr(catalog_row, "superseded_by", None) if catalog_row else None
        ) or dep.superseded_by
        action = PlannedAction(
            key=dep.key,
            entity=CatalogEntityType.CONTROLS,
            action=_default_action_for(superseded),
            successor_scf_id=superseded,
        )
        planned.append(action)
        impacts.append(
            DeprecatedImpactItem(
                key=dep.key,
                entity=CatalogEntityType.CONTROLS,
                name=dep.name
                or (getattr(catalog_row, "control_name", None) if catalog_row else None),
                data_summary={
                    "selected": bool(scoped.selected),
                    "implementation_status": scoped.implementation_status,
                    "maturity_level": scoped.maturity_level,
                    "has_composite": dep.key in composite_ids,
                },
                superseded_by=superseded,
                suggested_action=action.action,
                planned_action=action,
            )
        )
    for dep in evidence_diff.deprecated:
        rows = tracking_by_id.get(dep.key)
        if not rows:
            continue
        catalog_row = catalog_evidence.get(dep.key)
        superseded = (
            getattr(catalog_row, "superseded_by", None) if catalog_row else None
        ) or dep.superseded_by
        action = PlannedAction(
            key=dep.key,
            entity=CatalogEntityType.EVIDENCE,
            action=_default_action_for(superseded),
            successor_scf_id=superseded,
        )
        planned.append(action)
        impacts.append(
            DeprecatedImpactItem(
                key=dep.key,
                entity=CatalogEntityType.EVIDENCE,
                name=dep.name,
                data_summary={
                    "tracking_rows": len(rows),
                    "is_tracked": any(bool(r.is_tracked) for r in rows),
                },
                superseded_by=superseded,
                suggested_action=action.action,
                planned_action=action,
            )
        )

    # --- (c) changed controls ∩ selected scope (informational) ------------
    changed_in_scope: List[ChangedInScopeItem] = []
    for changed in controls_diff.changed:
        scoped = scoped_by_id.get(changed.key)
        if scoped is None or not scoped.selected:
            continue
        changed_in_scope.append(
            ChangedInScopeItem(
                scf_id=changed.key,
                name=changed.name,
                fields=changed.fields,
                reassessment_recommended=changed.key in composite_ids,
            )
        )

    # --- (d) orphan report (pre-existing invalid keys; never blocks) -------
    orphan_items: List[OrphanItem] = []
    for scf_id in sorted(scoped_by_id):
        if scf_id not in catalog_controls:
            orphan_items.append(
                OrphanItem(
                    source_table="scoped_controls",
                    key=scf_id,
                    detail="references a catalog control id not present in the catalog",
                )
            )
    for evidence_id in sorted(tracking_by_id):
        if evidence_id not in catalog_evidence:
            orphan_items.append(
                OrphanItem(
                    source_table="evidence_tracking",
                    key=evidence_id,
                    detail="references a catalog evidence id not present in the catalog",
                )
            )
    orphans = OrphanReport(items=orphan_items, count=len(orphan_items))

    # --- (e) first-reconciliation framework confirmation -------------------
    framework_confirmation = FrameworkConfirmation(
        required=eligibility.first_reconciliation,
        selections=[
            FrameworkSelectionItem(
                framework_id=s.framework_id, source=s.source, active=bool(s.active)
            )
            for s in sorted(selections, key=lambda s: s.framework_id)
        ],
    )

    # --- persist the run ---------------------------------------------------
    now = _now()
    run = OrganizationReconciliationRun(
        id=uuid_module.uuid4(),
        organization_id=org_id,
        from_version=eligibility.reconciled_catalog_version or union.from_version,
        to_version=union.to_version,
        catalog_import_run_id=runs_to_union[-1].id,
        status="previewed",
        diff_summary=summarize_diff(union).model_dump(mode="json"),
        planned_actions=[a.model_dump(mode="json") for a in planned],
        org_snapshot=None,
        actions_log=[
            {
                "event": "previewed",
                "at": now.isoformat(),
                "by": str(user_id) if user_id else None,
                "first_reconciliation": eligibility.first_reconciliation,
                "unioned_import_run_ids": [str(r.id) for r in runs_to_union],
            }
        ],
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    session.add(run)
    if commit:
        await session.commit()

    logger.info(
        "Reconciliation preview: org=%s run=%s %s -> %s (%d ledger runs unioned)",
        org_id, run.id, run.from_version, run.to_version, len(runs_to_union),
    )

    return PreviewResult(
        run=run,
        additions=additions,
        deprecated_impacts=impacts,
        changed_in_scope=changed_in_scope,
        orphans=orphans,
        framework_confirmation=framework_confirmation,
        union_detail=union,
        eligibility=eligibility,
    )


# ---------------------------------------------------------------------------
# Runs (read)
# ---------------------------------------------------------------------------


async def list_org_runs(
    session: AsyncSession, org_id: UUID
) -> List[OrganizationReconciliationRun]:
    """The org's reconciliation runs, newest first."""
    runs = await _org_rows(session, OrganizationReconciliationRun, org_id)
    runs.sort(key=lambda r: r.created_at or _now(), reverse=True)
    return runs


async def get_org_run(
    session: AsyncSession, org_id: UUID, run_id: UUID
) -> OrganizationReconciliationRun:
    runs = await _org_rows(session, OrganizationReconciliationRun, org_id)
    for run in runs:
        if run.id == run_id:
            return run
    raise RunNotFoundError(f"reconciliation run {run_id} not found for this organisation")


# ---------------------------------------------------------------------------
# Planned-action edits (PUT .../actions)
# ---------------------------------------------------------------------------


def _first_reconciliation_flag(run: OrganizationReconciliationRun) -> bool:
    for entry in run.actions_log or []:
        if isinstance(entry, dict) and entry.get("event") == "previewed":
            return bool(entry.get("first_reconciliation"))
    return False


def frameworks_confirmed(run: OrganizationReconciliationRun) -> Optional[List[str]]:
    """The confirmed framework list recorded on the run, or None. The apply
    guard (WP2c) uses this to enforce the first-reconciliation confirmation."""
    confirmed = None
    for entry in run.actions_log or []:
        if isinstance(entry, dict) and entry.get("event") == "frameworks_confirmed":
            confirmed = list(entry.get("framework_ids") or [])
    return confirmed


async def update_planned_actions(
    session: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    actions: List[PlannedAction],
    *,
    confirmed_framework_ids: Optional[List[str]] = None,
    user_id: Optional[UUID] = None,
    commit: bool = True,
) -> OrganizationReconciliationRun:
    """Replace a previewed run's planned actions, validated against the preview.

    - the action set must cover EXACTLY the preview's deprecated-impact keys
      (no unknown, missing, or duplicate keys);
    - migrate requires an existing, active successor control;
    - retire_only requires a justification;
    - on the org's first reconciliation, confirmed_framework_ids is mandatory
      (before or with the edit) and is applied to the selection rows:
      confirmed → active with source 'reconciliation'; unconfirmed heuristic
      backfill rows are deactivated.
    """
    run = await get_org_run(session, org_id, run_id)
    if run.status != "previewed":
        raise RunStateError(
            f"run {run.id} is {run.status}; planned actions can only be edited "
            f"while previewed"
        )

    defaults = [PlannedAction.model_validate(a) for a in (run.planned_actions or [])]
    default_keys = {(a.entity, a.key) for a in defaults}

    errors: List[str] = []
    seen = set()
    for action in actions:
        pair = (action.entity, action.key)
        if pair in seen:
            errors.append(f"duplicate action for {action.entity.value} {action.key}")
        seen.add(pair)
        if pair not in default_keys:
            errors.append(
                f"{action.entity.value} {action.key} is not a deprecated impact "
                f"of this preview"
            )
    missing = default_keys - seen
    for entity, key in sorted(missing, key=lambda p: (p[0].value, p[1])):
        errors.append(f"missing action for {entity.value} {key}")

    result = await session.execute(select(SCFCatalogControl))
    catalog_controls = {row.scf_id: row for row in result.scalars().all()}
    result = await session.execute(select(SCFCatalogEvidence))
    catalog_evidence = {row.evidence_id: row for row in result.scalars().all()}

    for action in actions:
        if action.action == PlannedActionType.MIGRATE:
            successor = action.successor_scf_id
            if not successor:
                errors.append(f"{action.key}: migrate requires successor_scf_id")
            else:
                # Same entity-keyed lookup as _validate_actions_for_apply: an
                # evidence migrate's successor lives in the evidence catalog.
                lookup = (
                    catalog_controls
                    if action.entity == CatalogEntityType.CONTROLS
                    else catalog_evidence
                )
                row = lookup.get(successor)
                if row is None:
                    errors.append(
                        f"{action.key}: successor {successor} does not exist in the catalog"
                    )
                elif getattr(row, "status", "active") != "active":
                    errors.append(f"{action.key}: successor {successor} is not active")
        elif action.action == PlannedActionType.RETIRE_ONLY:
            if not (action.justification or "").strip():
                errors.append(f"{action.key}: retire_only requires a justification")

    first_run = _first_reconciliation_flag(run)
    if (
        first_run
        and confirmed_framework_ids is None
        and frameworks_confirmed(run) is None
    ):
        errors.append(
            "first reconciliation: confirmed_framework_ids is required to confirm "
            "the organisation's framework selections"
        )

    selections = await _org_rows(session, OrganizationFrameworkSelection, org_id)
    if confirmed_framework_ids is not None:
        known = {s.framework_id for s in selections}
        for framework_id in confirmed_framework_ids:
            if framework_id not in known:
                errors.append(
                    f"confirmed framework {framework_id} has no selection row "
                    f"for this organisation"
                )

    if errors:
        raise ActionValidationError("; ".join(errors), errors)

    now = _now()
    log = list(run.actions_log or [])

    if confirmed_framework_ids is not None:
        confirmed_set = set(confirmed_framework_ids)
        for selection in selections:
            if selection.framework_id in confirmed_set:
                selection.active = True
                selection.source = "reconciliation"
                selection.selected_by = user_id
                selection.selected_at = now
            elif selection.source == "backfill" and selection.active:
                # Heuristic backfill row the admin did not confirm.
                selection.active = False
        log.append(
            {
                "event": "frameworks_confirmed",
                "at": now.isoformat(),
                "by": str(user_id) if user_id else None,
                "framework_ids": sorted(confirmed_set),
            }
        )

    run.planned_actions = [a.model_dump(mode="json") for a in actions]
    log.append(
        {
            "event": "actions_updated",
            "at": now.isoformat(),
            "by": str(user_id) if user_id else None,
            "count": len(actions),
        }
    )
    run.actions_log = log
    run.updated_at = now
    if commit:
        await session.commit()
    return run


# ---------------------------------------------------------------------------
# Org changelog (plan §4.5/§4.6 — what THIS org has reconciled through)
# ---------------------------------------------------------------------------

_CHANGE_ORDER = (
    ChangeClass.ADDED,
    ChangeClass.CHANGED,
    ChangeClass.DEPRECATED,
    ChangeClass.RESURRECTED,
)


def _entries_for_platform_run(
    detail: DiffDetail, version: str, applied_at: Optional[datetime]
) -> List[ChangelogEntry]:
    entries: List[ChangelogEntry] = []
    for entity in sorted(detail.entities, key=lambda e: e.value):
        diff = detail.entities[entity]
        for change_class in _CHANGE_ORDER:
            if change_class == ChangeClass.ADDED:
                for item in diff.added:
                    entries.append(
                        ChangelogEntry(
                            version=version, applied_at=applied_at, entity=entity,
                            change_class=change_class, key=item.key, name=item.name,
                            summary="added to the catalog",
                        )
                    )
            elif change_class == ChangeClass.CHANGED:
                for item in diff.changed:
                    entries.append(
                        ChangelogEntry(
                            version=version, applied_at=applied_at, entity=entity,
                            change_class=change_class, key=item.key, name=item.name,
                            summary="fields changed: " + ", ".join(sorted(item.fields)),
                        )
                    )
            elif change_class == ChangeClass.DEPRECATED:
                for item in diff.deprecated:
                    entries.append(
                        ChangelogEntry(
                            version=version, applied_at=applied_at, entity=entity,
                            change_class=change_class, key=item.key, name=item.name,
                            summary=(
                                f"retired; superseded by {item.superseded_by}"
                                if item.superseded_by
                                else "retired with no successor"
                            ),
                        )
                    )
            else:
                for item in diff.resurrected:
                    entries.append(
                        ChangelogEntry(
                            version=version, applied_at=applied_at, entity=entity,
                            change_class=change_class, key=item.key, name=item.name,
                            summary="re-activated",
                        )
                    )
    return entries


async def assemble_changelog(
    session: AsyncSession,
    org_id: UUID,
    *,
    limit: int = 50,
    offset: int = 0,
    detail_loader: Optional[DetailLoader] = None,
) -> Tuple[List[ChangelogEntry], int]:
    """Changelog of catalog changes the org has actually reconciled through.

    Assembled from the org's APPLIED reconciliation runs: each is expanded to
    the platform runs its version window covers, whose stored diff details
    supply the per-key entries. Newest reconciliation (and, within it, newest
    catalog version) first. Orgs that have never applied a reconciliation get
    an empty changelog — Option B: unreconciled orgs are not told a story
    about changes they have not taken (plan §4.8 handles the banner side).

    Loads every contributing diff detail to compute the true total before
    paginating; acceptable at the ledger's cardinality (a handful of runs).
    """
    loader = detail_loader or load_diff_detail_from_storage

    org_runs = [
        r
        for r in await _org_rows(session, OrganizationReconciliationRun, org_id)
        if r.status == "applied"
    ]
    org_runs.sort(key=lambda r: r.completed_at or r.created_at or _now(), reverse=True)

    platform_runs = await applied_platform_runs(session)

    entries: List[ChangelogEntry] = []
    detail_cache: Dict[Any, DiffDetail] = {}
    for org_run in org_runs:
        from_key = _version_key(org_run.from_version)
        to_key = _version_key(org_run.to_version)
        window = [
            p
            for p in platform_runs
            if from_key < _version_key(p.to_version) <= to_key
        ]
        window.sort(key=lambda p: _version_key(p.to_version), reverse=True)
        for platform_run in window:
            if platform_run.id not in detail_cache:
                detail_cache[platform_run.id] = await asyncio.to_thread(
                    loader, platform_run
                )
            entries.extend(
                _entries_for_platform_run(
                    detail_cache[platform_run.id],
                    platform_run.to_version,
                    org_run.completed_at,
                )
            )

    total = len(entries)
    return entries[offset : offset + limit], total


# ---------------------------------------------------------------------------
# WP2c — advisory locks (plan §4.3: dual locks, first statements in the txn)
# ---------------------------------------------------------------------------

# Platform apply/revert holds the single-bigint catalog key EXCLUSIVELY
# (catalog_apply.acquire_catalog_lock). Org reconciliation takes the SAME key
# SHARED — orgs may reconcile concurrently with each other, but never
# interleave with a platform apply or revert — plus an exclusive per-org lock
# in the two-int32 keyspace so one org cannot run two mutations at once.
_SHARED_CATALOG_LOCK_SQL = text("SELECT pg_advisory_xact_lock_shared(:key)")
_ORG_LOCK_SQL = text("SELECT pg_advisory_xact_lock(:cls, :key)")

# int32 of b"SCOR" — the lock class for per-org reconciliation locks. The
# two-int32 advisory keyspace is disjoint from the single-bigint one, so this
# can never collide with CATALOG_LOCK_KEY.
ORG_RECONCILIATION_LOCK_CLASS = 0x5343_4F52


def org_lock_key(org_id: UUID) -> int:
    """Fold an org UUID into a positive int32 for the per-org advisory lock.

    A cross-org collision merely over-serialises two orgs' reconciliations;
    it can never under-lock.
    """
    folded = org_id.int
    folded ^= folded >> 64
    folded ^= folded >> 32
    return folded & 0x7FFF_FFFF


async def acquire_reconciliation_locks(session: AsyncSession, org_id: UUID) -> None:
    """Take the shared catalog lock + the exclusive org lock (transaction-scoped)."""
    await session.execute(_SHARED_CATALOG_LOCK_SQL, {"key": CATALOG_LOCK_KEY})
    await session.execute(
        _ORG_LOCK_SQL,
        {"cls": ORG_RECONCILIATION_LOCK_CLASS, "key": org_lock_key(org_id)},
    )


# ---------------------------------------------------------------------------
# WP2c — snapshot pre-imaging (plan §4.1 M5, §4.3: the rollback authority)
# ---------------------------------------------------------------------------

# Tables an org apply may touch, with the natural key the snapshot is keyed by
# (stable across the run — apply never mutates these columns). An
# OrgSnapshotRow with row == {} records "this row did not exist before the
# apply": rollback removes it (delete when unreferenced, demote otherwise).
_SNAPSHOT_TABLES: Dict[str, Tuple[type, Tuple[str, ...]]] = {
    "scoped_controls": (ScopedControl, ("organization_id", "scf_id")),
    "evidence_tracking": (EvidenceTracking, ("organization_id", "evidence_id")),
    "organization_catalog_state": (OrganizationCatalogState, ("organization_id",)),
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return value


def _row_image(model: type, row: Any) -> Dict[str, Any]:
    """JSON-safe pre-image of a row's column values (relationships excluded)."""
    columns = set(model.__table__.columns.keys())
    return {
        k: _jsonable(v) for k, v in vars(row).items() if k in columns
    }


def _coerce_for_column(model: type, name: str, value: Any) -> Any:
    """Invert _jsonable using the model's column type (restore path)."""
    if value is None:
        return None
    column = model.__table__.columns.get(name)
    if column is None:
        return value
    if isinstance(column.type, SA_DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(column.type, SA_Date) and isinstance(value, str):
        return date.fromisoformat(value)
    if isinstance(column.type, PG_UUID) and isinstance(value, str):
        return uuid_module.UUID(value)
    return value


def _natural_key(model: type, key_cols: Tuple[str, ...], row: Any) -> Tuple[str, ...]:
    return tuple(str(getattr(row, col)) for col in key_cols)


# ---------------------------------------------------------------------------
# WP2c — apply (plan §4.3)
# ---------------------------------------------------------------------------

# The org's assessment state migrate carries onto the successor ScopedControl.
# Catalog-derived columns (control_question, weighting, PPTDF flags, ...) are
# NOT copied — they belong to the successor control, not the predecessor.
_MIGRATED_CONTROL_STATE_FIELDS = (
    "implementation_status",
    "priority",
    "maturity_level",
    "owner",
    "assigned_to",
    "assigned_user_id",
    "owner_user_id",
    "target_date",
    "completion_date",
    "implementation_notes",
    "related_documentation",
    "custom_fields",
)

_MIGRATED_EVIDENCE_STATE_FIELDS = (
    "is_tracked",
    "method_of_collection",
    "collecting_system",
    "owner",
    "frequency",
    "comments",
    "maturity_level",
    "assigned_user_id",
    "owner_user_id",
    "next_collection_date",
    "last_collection_date",
    "system_id",
)

# Run statuses the apply/rollback entry points accept: the endpoint enqueues
# from 'previewed'/'applied'; the Celery task flips to the in-flight status
# before calling the service, and a task retry may re-enter in that status.
_APPLYABLE_STATUSES = ("previewed", "applying")
_ROLLBACKABLE_STATUSES = ("applied", "rolling_back")


@dataclass
class OrgReconcileReport:
    """Returned by apply/rollback; the Celery task serialises it."""

    run_id: str
    organization_id: str
    action: str  # "applied" | "rolled_back"
    from_version: str
    to_version: str
    migrated: int = 0
    retained: int = 0
    retired: int = 0
    scope_added: int = 0
    scope_updated: int = 0
    snapshot_rows: int = 0
    restored: int = 0
    deleted: int = 0
    demoted: int = 0

    def as_dict(self) -> dict:
        return vars(self).copy()


async def _check_apply_guards(
    session: AsyncSession, org_id: UUID, run: OrganizationReconciliationRun
) -> None:
    """Shared apply guards (plan §4.3): status, staleness anchor, first-run
    framework confirmation, single-active-run."""
    if run.status not in _APPLYABLE_STATUSES:
        raise RunStateError(
            f"run {run.id} is {run.status}; only a previewed run can be applied"
        )

    ledger = await applied_platform_runs(session)
    latest = ledger[-1] if ledger else None
    if latest is None or latest.id != run.catalog_import_run_id:
        raise StalePreviewError(
            f"run {run.id} was previewed against import run "
            f"{run.catalog_import_run_id}, but a newer platform run has been "
            f"applied since — re-run the preview"
        )

    if _first_reconciliation_flag(run) and frameworks_confirmed(run) is None:
        raise FrameworksNotConfirmedError(
            "first reconciliation: the organisation's framework selections "
            "must be confirmed (PUT .../actions with confirmed_framework_ids) "
            "before apply"
        )

    others = [
        r
        for r in await _org_rows(session, OrganizationReconciliationRun, org_id)
        if r.status in ACTIVE_RUN_STATUSES and r.id != run.id
    ]
    if others:
        raise ActiveRunConflictError(
            f"another reconciliation run is active for this organisation "
            f"({others[0].id}: {others[0].status})"
        )


async def check_apply_preflight(
    session: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    expected_to_version: Optional[str] = None,
) -> OrganizationReconciliationRun:
    """Synchronous endpoint pre-flight so guard refusals surface as HTTP
    errors instead of async task failures. The service re-checks the same
    guards under the advisory locks."""
    run = await get_org_run(session, org_id, run_id)
    if run.status != "previewed":
        raise RunStateError(
            f"run {run.id} is {run.status}; only a previewed run can be applied"
        )
    if expected_to_version is not None and expected_to_version != run.to_version:
        raise RunStateError(
            f"expected_to_version {expected_to_version!r} does not match the "
            f"run's to_version {run.to_version!r} — the preview on the server "
            f"is not the one reviewed"
        )
    await _check_apply_guards(session, org_id, run)
    return run


def _validate_actions_for_apply(
    actions: List[PlannedAction],
    catalog_controls: Dict[str, Any],
    catalog_evidence: Dict[str, Any],
) -> None:
    """Re-validate migrate successors at apply time (the catalog may have
    moved since the actions PUT). Runs before any mutation."""
    errors: List[str] = []
    for action in actions:
        if action.action != PlannedActionType.MIGRATE:
            continue
        successor = action.successor_scf_id
        if not successor:
            errors.append(f"{action.key}: migrate requires successor_scf_id")
            continue
        lookup = (
            catalog_controls
            if action.entity == CatalogEntityType.CONTROLS
            else catalog_evidence
        )
        row = lookup.get(successor)
        if row is None:
            errors.append(
                f"{action.key}: successor {successor} does not exist in the catalog"
            )
        elif getattr(row, "status", "active") != "active":
            errors.append(f"{action.key}: successor {successor} is not active")
    if errors:
        raise ActionValidationError("; ".join(errors), errors)


def _execute_control_action(
    session: AsyncSession,
    org_id: UUID,
    action: PlannedAction,
    scoped_by_id: Dict[str, Any],
    to_version: str,
    now: datetime,
    report: OrgReconcileReport,
) -> None:
    scoped = scoped_by_id.get(action.key)
    if scoped is None:
        # Org data disappeared between preview and apply — nothing at stake.
        return
    if action.action == PlannedActionType.RETAIN:
        # Keep scoped untouched; the control stays deprecated for the org and
        # renders badged (plan §4.3b / §4.4).
        report.retained += 1
        return
    if action.action == PlannedActionType.MIGRATE:
        successor_id = action.successor_scf_id
        successor = scoped_by_id.get(successor_id)
        if successor is None:
            successor = ScopedControl(
                organization_id=org_id,
                scf_id=successor_id,
                selected=True,
                selection_reason=(
                    f"Migrated from {action.key} (retired in catalog {to_version})"
                ),
                **{
                    f: getattr(scoped, f, None)
                    for f in _MIGRATED_CONTROL_STATE_FIELDS
                },
            )
            session.add(successor)
            scoped_by_id[successor_id] = successor
        elif not successor.selected:
            successor.selected = True
            successor.selection_reason = (
                f"Migrated from {action.key} (retired in catalog {to_version})"
            )
            successor.updated_at = now
        # An already-selected successor keeps its own state untouched.
        scoped.selected = False
        scoped.out_of_scope_justification = action.justification or (
            f"Retired in catalog {to_version}; migrated to {successor_id}"
        )
        scoped.updated_at = now
        report.migrated += 1
        return
    # RETIRE_ONLY — demote/unscope; NEVER a DELETE (engagement CASCADE,
    # plan §4.3b). Status flip only.
    scoped.selected = False
    scoped.out_of_scope_justification = action.justification or (
        f"Retired in catalog {to_version}"
    )
    scoped.updated_at = now
    report.retired += 1


def _execute_evidence_action(
    session: AsyncSession,
    org_id: UUID,
    action: PlannedAction,
    tracking_by_id: Dict[str, Any],
    to_version: str,
    now: datetime,
    report: OrgReconcileReport,
) -> None:
    tracking = tracking_by_id.get(action.key)
    if tracking is None:
        return
    if action.action == PlannedActionType.RETAIN:
        report.retained += 1
        return
    if action.action == PlannedActionType.MIGRATE:
        # Copy-and-demote (never mutate the natural key in place: the
        # snapshot is keyed by (org, evidence_id)).
        successor_id = action.successor_scf_id
        if successor_id not in tracking_by_id:
            successor = EvidenceTracking(
                organization_id=org_id,
                evidence_id=successor_id,
                **{
                    f: getattr(tracking, f, None)
                    for f in _MIGRATED_EVIDENCE_STATE_FIELDS
                },
            )
            session.add(successor)
            tracking_by_id[successor_id] = successor
        tracking.is_tracked = False
        tracking.updated_at = now
        report.migrated += 1
        return
    # RETIRE_ONLY
    tracking.is_tracked = False
    tracking.updated_at = now
    report.retired += 1


async def apply_reconciliation_run(
    session: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    *,
    user_id: Optional[UUID] = None,
) -> OrgReconcileReport:
    """Apply a previewed reconciliation run in ONE locked transaction.

    Order (plan §4.3): dual advisory locks FIRST → guards re-checked under
    the locks → pre-image capture → planned-action execution → scope
    re-materialisation via bulk_scope_frameworks(commit=False) → snapshot of
    every touched row into org_snapshot → org state update → run 'applied' →
    commit. Any failure rolls back — org data untouched.
    """
    await acquire_reconciliation_locks(session, org_id)

    try:
        run = await get_org_run(session, org_id, run_id)
        await _check_apply_guards(session, org_id, run)

        now = _now()
        to_version = run.to_version
        report = OrgReconcileReport(
            run_id=str(run.id),
            organization_id=str(org_id),
            action="applied",
            from_version=run.from_version,
            to_version=to_version,
        )

        # --- pre-image capture (BEFORE any mutation) ----------------------
        scoped_rows = await _org_rows(session, ScopedControl, org_id)
        tracking_rows = await _org_rows(session, EvidenceTracking, org_id)
        states = await _org_rows(session, OrganizationCatalogState, org_id)
        state = states[0] if states else None

        pre_images: Dict[Tuple[str, Tuple[str, ...]], Dict[str, Any]] = {}
        for row in scoped_rows:
            key = _natural_key(ScopedControl, ("organization_id", "scf_id"), row)
            pre_images[("scoped_controls", key)] = _row_image(ScopedControl, row)
        for row in tracking_rows:
            key = _natural_key(
                EvidenceTracking, ("organization_id", "evidence_id"), row
            )
            pre_images[("evidence_tracking", key)] = _row_image(EvidenceTracking, row)
        state_image = (
            _row_image(OrganizationCatalogState, state) if state is not None else {}
        )

        # --- planned actions (validated up front, then executed) ----------
        actions = [
            PlannedAction.model_validate(a) for a in (run.planned_actions or [])
        ]
        result = await session.execute(select(SCFCatalogControl))
        catalog_controls = {r.scf_id: r for r in result.scalars().all()}
        result = await session.execute(select(SCFCatalogEvidence))
        catalog_evidence = {r.evidence_id: r for r in result.scalars().all()}
        _validate_actions_for_apply(actions, catalog_controls, catalog_evidence)

        scoped_by_id = {r.scf_id: r for r in scoped_rows}
        tracking_by_id = {r.evidence_id: r for r in tracking_rows}
        for action in actions:
            if action.entity == CatalogEntityType.CONTROLS:
                _execute_control_action(
                    session, org_id, action, scoped_by_id, to_version, now, report
                )
            elif action.entity == CatalogEntityType.EVIDENCE:
                _execute_evidence_action(
                    session, org_id, action, tracking_by_id, to_version, now, report
                )

        # --- scope re-materialisation (plan §4.3: the WP2a primitive) -----
        selections = await _org_rows(session, OrganizationFrameworkSelection, org_id)
        active_framework_ids = sorted(
            {s.framework_id for s in selections if s.active}
        )
        if active_framework_ids:
            scope_result = await bulk_scope_frameworks(
                session,
                org_id,
                active_framework_ids,
                user_id=user_id,
                selection_reason=f"Catalog {to_version} reconciliation",
                commit=False,
            )
            report.scope_added = scope_result.added
            report.scope_updated = scope_result.updated

        await session.flush()

        # --- snapshot: pre-images of exactly the rows this run touched ----
        snapshot_rows: List[OrgSnapshotRow] = []
        for row in await _org_rows(session, ScopedControl, org_id):
            key = _natural_key(ScopedControl, ("organization_id", "scf_id"), row)
            pre = pre_images.get(("scoped_controls", key))
            if pre is None:
                snapshot_rows.append(
                    OrgSnapshotRow(
                        table="scoped_controls",
                        primary_key={"organization_id": key[0], "scf_id": key[1]},
                        row={},
                    )
                )
            elif _row_image(ScopedControl, row) != pre:
                snapshot_rows.append(
                    OrgSnapshotRow(
                        table="scoped_controls",
                        primary_key={"organization_id": key[0], "scf_id": key[1]},
                        row=pre,
                    )
                )
        for row in await _org_rows(session, EvidenceTracking, org_id):
            key = _natural_key(
                EvidenceTracking, ("organization_id", "evidence_id"), row
            )
            pre = pre_images.get(("evidence_tracking", key))
            if pre is None:
                snapshot_rows.append(
                    OrgSnapshotRow(
                        table="evidence_tracking",
                        primary_key={"organization_id": key[0], "evidence_id": key[1]},
                        row={},
                    )
                )
            elif _row_image(EvidenceTracking, row) != pre:
                snapshot_rows.append(
                    OrgSnapshotRow(
                        table="evidence_tracking",
                        primary_key={"organization_id": key[0], "evidence_id": key[1]},
                        row=pre,
                    )
                )
        # Org state is always touched: pre-image (or {} when the org had no
        # state row and this apply creates one).
        snapshot_rows.append(
            OrgSnapshotRow(
                table="organization_catalog_state",
                primary_key={"organization_id": str(org_id)},
                row=state_image,
            )
        )

        run.org_snapshot = OrgSnapshot(
            captured_at=now, rows=snapshot_rows
        ).model_dump(mode="json")
        report.snapshot_rows = len(snapshot_rows)

        # --- org state + run finalisation ---------------------------------
        if state is None:
            state = OrganizationCatalogState(
                organization_id=org_id,
                reconciled_catalog_version=to_version,
            )
            session.add(state)
        state.reconciled_catalog_version = to_version
        state.last_reconciled_at = now
        state.last_reconciliation_run_id = run.id
        state.updated_at = now

        run.status = "applied"
        run.completed_at = now
        run.updated_at = now
        run.actions_log = list(run.actions_log or []) + [
            {
                "event": "applied",
                "at": now.isoformat(),
                "by": str(user_id) if user_id else None,
                "migrated": report.migrated,
                "retained": report.retained,
                "retired": report.retired,
                "scope_added": report.scope_added,
                "scope_updated": report.scope_updated,
                "snapshot_rows": report.snapshot_rows,
            }
        ]

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "Reconciliation apply committed: org=%s run=%s -> %s "
        "(migrated=%d retired=%d scope_added=%d snapshot_rows=%d)",
        org_id, run.id, to_version,
        report.migrated, report.retired, report.scope_added, report.snapshot_rows,
    )
    return report


# ---------------------------------------------------------------------------
# WP2c — rollback (plan §4.3: snapshot restore is the authority)
# ---------------------------------------------------------------------------


async def _check_rollback_guards(
    session: AsyncSession,
    org_id: UUID,
    run: OrganizationReconciliationRun,
    *,
    cancel_previewed: bool,
) -> None:
    if run.status not in _ROLLBACKABLE_STATUSES:
        raise RunStateError(
            f"run {run.id} is {run.status}; only an applied run can roll back"
        )
    now = _now()
    for other in await _org_rows(session, OrganizationReconciliationRun, org_id):
        if other.id == run.id:
            continue
        if other.status == "applied" and (
            (other.completed_at or other.created_at or now)
            > (run.completed_at or run.created_at or now)
        ):
            raise RollbackNotLatestError(
                f"run {run.id} is not the organisation's latest applied "
                f"reconciliation (run {other.id} applied later)"
            )
        if other.status == "previewed":
            if cancel_previewed:
                # A live preview is invalidated by the rollback; supersede it
                # the same way a re-preview would.
                other.status = "cancelled"
                other.updated_at = now
            # Preflight (cancel_previewed=False) tolerates it: the service
            # cancels it under the locks.
        elif other.status in ("applying", "rolling_back"):
            raise ActiveRunConflictError(
                f"run {other.id} is {other.status}; wait for it to finish"
            )
    if not run.org_snapshot:
        raise SnapshotUnavailableError(
            f"run {run.id} has no org_snapshot; rollback has no restore authority"
        )


async def check_rollback_preflight(
    session: AsyncSession, org_id: UUID, run_id: UUID
) -> OrganizationReconciliationRun:
    """Synchronous endpoint pre-flight for rollback (no mutations)."""
    run = await get_org_run(session, org_id, run_id)
    if run.status != "applied":
        raise RunStateError(
            f"run {run.id} is {run.status}; only an applied run can roll back"
        )
    await _check_rollback_guards(session, org_id, run, cancel_previewed=False)
    return run


async def rollback_reconciliation_run(
    session: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    *,
    user_id: Optional[UUID] = None,
) -> OrgReconcileReport:
    """Snapshot-restore rollback of the org's latest applied run.

    Pre-imaged rows are restored verbatim (updated_at excepted); run-created
    rows are deleted only when unreferenced by engagement_control_scope or
    cdm_mappings (scoped controls) / evidence_collection_tasks (evidence),
    otherwise demoted (plan §4.3, §4.8: the absolute delete-vs-CASCADE rule).
    Org state reverts to the pre-apply image. Same dual-lock, single
    transaction discipline as apply.
    """
    await acquire_reconciliation_locks(session, org_id)

    try:
        run = await get_org_run(session, org_id, run_id)
        await _check_rollback_guards(session, org_id, run, cancel_previewed=True)

        now = _now()
        report = OrgReconcileReport(
            run_id=str(run.id),
            organization_id=str(org_id),
            action="rolled_back",
            from_version=run.from_version,
            to_version=run.to_version,
        )
        snapshot = OrgSnapshot.model_validate(run.org_snapshot)

        scoped_rows = await _org_rows(session, ScopedControl, org_id)
        tracking_rows = await _org_rows(session, EvidenceTracking, org_id)
        states = await _org_rows(session, OrganizationCatalogState, org_id)
        live_by_key: Dict[Tuple[str, Tuple[str, ...]], Any] = {}
        for row in scoped_rows:
            key = _natural_key(ScopedControl, ("organization_id", "scf_id"), row)
            live_by_key[("scoped_controls", key)] = row
        for row in tracking_rows:
            key = _natural_key(
                EvidenceTracking, ("organization_id", "evidence_id"), row
            )
            live_by_key[("evidence_tracking", key)] = row
        if states:
            live_by_key[
                ("organization_catalog_state", (str(org_id),))
            ] = states[0]

        # --- partition the snapshot ---------------------------------------
        restores: List[Tuple[OrgSnapshotRow, Any]] = []
        created_scoped: List[Any] = []
        created_tracking: List[Any] = []
        for snap in snapshot.rows:
            model, key_cols = _SNAPSHOT_TABLES[snap.table]
            key = tuple(str(snap.primary_key[col]) for col in key_cols)
            live = live_by_key.get((snap.table, key))
            if snap.row:
                restores.append((snap, live))
            elif live is not None:
                if snap.table == "scoped_controls":
                    created_scoped.append(live)
                elif snap.table == "evidence_tracking":
                    created_tracking.append(live)
                else:
                    # organization_catalog_state created by the apply: the org
                    # had no state row before — remove it again.
                    await session.delete(live)
                    report.deleted += 1

        # --- reference guards for run-created rows ------------------------
        scoped_ids = [r.id for r in created_scoped if getattr(r, "id", None)]
        referenced_scoped_ids: set = set()
        if scoped_ids:
            result = await session.execute(
                select(EngagementControlScope).where(
                    EngagementControlScope.scoped_control_id.in_(scoped_ids)
                )
            )
            referenced_scoped_ids |= {
                r.scoped_control_id
                for r in result.scalars().all()
                if r.scoped_control_id in scoped_ids
            }
            result = await session.execute(
                select(CDMMapping).where(CDMMapping.scoped_control_id.in_(scoped_ids))
            )
            referenced_scoped_ids |= {
                r.scoped_control_id
                for r in result.scalars().all()
                if r.scoped_control_id in scoped_ids
            }
        tracking_ids = [r.id for r in created_tracking if getattr(r, "id", None)]
        referenced_tracking_ids: set = set()
        if tracking_ids:
            result = await session.execute(
                select(EvidenceCollectionTask).where(
                    EvidenceCollectionTask.evidence_tracking_id.in_(tracking_ids)
                )
            )
            referenced_tracking_ids = {
                r.evidence_tracking_id
                for r in result.scalars().all()
                if r.evidence_tracking_id in tracking_ids
            }

        # --- remove (or demote) run-created rows --------------------------
        for row in created_scoped:
            if getattr(row, "id", None) in referenced_scoped_ids:
                row.selected = False
                row.out_of_scope_justification = (
                    f"Catalog {run.to_version} reconciliation rolled back; row "
                    f"retained (referenced by an engagement or mapping)"
                )
                row.updated_at = now
                report.demoted += 1
            else:
                await session.delete(row)
                report.deleted += 1
        for row in created_tracking:
            if getattr(row, "id", None) in referenced_tracking_ids:
                row.is_tracked = False
                row.updated_at = now
                report.demoted += 1
            else:
                await session.delete(row)
                report.deleted += 1

        # --- restore pre-imaged rows verbatim -----------------------------
        for snap, live in restores:
            model, key_cols = _SNAPSHOT_TABLES[snap.table]
            if live is None:
                # Apply never deletes, so a missing pre-imaged row means it
                # was removed out-of-band; re-create it from the image.
                live = model(
                    **{
                        col: _coerce_for_column(model, col, snap.primary_key[col])
                        for col in key_cols
                    }
                )
                session.add(live)
            for name, value in snap.row.items():
                if name == "updated_at":
                    continue
                setattr(live, name, _coerce_for_column(model, name, value))
            if "updated_at" in model.__table__.columns:
                live.updated_at = now
            report.restored += 1

        run.status = "rolled_back"
        run.completed_at = now
        run.updated_at = now
        run.actions_log = list(run.actions_log or []) + [
            {
                "event": "rolled_back",
                "at": now.isoformat(),
                "by": str(user_id) if user_id else None,
                "restored": report.restored,
                "deleted": report.deleted,
                "demoted": report.demoted,
            }
        ]

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "Reconciliation rollback committed: org=%s run=%s back to %s "
        "(restored=%d deleted=%d demoted=%d)",
        org_id, run.id, report.from_version,
        report.restored, report.deleted, report.demoted,
    )
    return report


# ---------------------------------------------------------------------------
# WP2c — cancel
# ---------------------------------------------------------------------------


async def cancel_reconciliation_run(
    session: AsyncSession,
    org_id: UUID,
    run_id: UUID,
    *,
    user_id: Optional[UUID] = None,
    commit: bool = True,
) -> OrganizationReconciliationRun:
    """Cancel a previewed run before apply (plan §4.5)."""
    run = await get_org_run(session, org_id, run_id)
    if run.status != "previewed":
        raise RunStateError(
            f"run {run.id} is {run.status}; only a previewed run can be cancelled"
        )
    now = _now()
    run.status = "cancelled"
    run.updated_at = now
    run.actions_log = list(run.actions_log or []) + [
        {
            "event": "cancelled",
            "at": now.isoformat(),
            "by": str(user_id) if user_id else None,
        }
    ]
    if commit:
        await session.commit()
    return run
