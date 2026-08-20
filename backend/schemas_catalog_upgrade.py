"""Frozen contracts for the SCF catalog upgrade feature (WP-C).

Pydantic models for the catalog upgrade + per-org reconciliation API surface
(plan §4.5) and the diff-detail / snapshot JSON shapes (plan §4.1–4.3).

Downstream WPs IMPORT these models; they never redefine them. Changing any
shape in this module requires an explicit contract-change WP — see
docs/plans/scf-catalog-upgrade-contracts.md.

Deliberately DB-free: plain Pydantic only, no SQLAlchemy imports.
"""
from enum import Enum
from typing import Any, Dict, List, Optional
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# =============================================================================
# Enums
# =============================================================================

class CatalogEntityType(str, Enum):
    """Entities the platform diff engine reports on (plan §4.2.2)."""
    CONTROLS = "controls"
    DOMAINS = "domains"
    EVIDENCE = "evidence"
    ASSESSMENT_OBJECTIVES = "assessment_objectives"
    CAPABILITY_THEMES = "capability_themes"
    FRAMEWORK_MAPPINGS = "framework_mappings"


class ChangeClass(str, Enum):
    """Per-entity diff classification (plan §4.2.2)."""
    ADDED = "added"
    CHANGED = "changed"
    DEPRECATED = "deprecated"
    RESURRECTED = "resurrected"
    UNCHANGED = "unchanged"


class PlatformRunStatus(str, Enum):
    """catalog_import_runs.status (plan §4.1 M4)."""
    STAGING = "staging"
    STAGED = "staged"
    BLOCKED = "blocked"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REVERTED = "reverted"


class OrgRunStatus(str, Enum):
    """organization_reconciliation_runs.status (plan §4.1 M5)."""
    PREVIEWED = "previewed"
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"
    ROLLING_BACK = "rolling_back"
    ROLLED_BACK = "rolled_back"
    CANCELLED = "cancelled"


class PlannedActionType(str, Enum):
    """Per-deprecated-control decision in an org reconciliation (plan §4.3b)."""
    MIGRATE = "migrate"
    RETAIN = "retain"
    RETIRE_ONLY = "retire_only"


class FrameworkSelectionSource(str, Enum):
    """organization_framework_selections.source (plan §4.1 M3)."""
    BULK_SCOPE = "bulk_scope"
    BACKFILL = "backfill"
    RECONCILIATION = "reconciliation"


# =============================================================================
# Diff detail (the stored per-run diff object — also the platform revert anchor)
# =============================================================================

class FieldChange(BaseModel):
    """Old AND new value for one changed field (plan §4.1 M4: the diff IS the
    platform revert anchor, so both sides are always stored)."""
    old: Optional[Any] = None
    new: Optional[Any] = None


class SupersededSuggestion(BaseModel):
    """Display-only successor suggestion for a planned deprecation (plan §4.2.3).

    Never auto-applied; the admin pairs manually via the pairings PUT.
    """
    scf_id: str
    name: Optional[str] = None
    score: float = Field(..., ge=0.0, le=1.0)


class AddedEntity(BaseModel):
    """A row present in the workbook but not in the live catalog."""
    key: str
    name: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class ChangedEntity(BaseModel):
    """A row present in both, with field-level differences."""
    key: str
    name: Optional[str] = None
    fields: Dict[str, FieldChange] = Field(default_factory=dict)


class DeprecatedEntity(BaseModel):
    """A row active in the live catalog but absent from the workbook."""
    key: str
    name: Optional[str] = None
    superseded_by: Optional[str] = None
    suggestions: List[SupersededSuggestion] = Field(default_factory=list)


class ResurrectedEntity(BaseModel):
    """A row deprecated in the live catalog that reappears in the workbook.

    Re-activation may carry field changes; `fields` may be empty.
    """
    key: str
    name: Optional[str] = None
    fields: Dict[str, FieldChange] = Field(default_factory=dict)


class EntityDiff(BaseModel):
    """Full diff for one entity type."""
    added: List[AddedEntity] = Field(default_factory=list)
    changed: List[ChangedEntity] = Field(default_factory=list)
    deprecated: List[DeprecatedEntity] = Field(default_factory=list)
    resurrected: List[ResurrectedEntity] = Field(default_factory=list)
    unchanged: List[str] = Field(default_factory=list)  # keys only


class DiffDetail(BaseModel):
    """The complete stored diff object for a platform import run."""
    from_version: str
    to_version: str
    entities: Dict[CatalogEntityType, EntityDiff] = Field(default_factory=dict)


class EntityDiffCounts(BaseModel):
    """Count-only view of one entity's diff."""
    added: int = 0
    changed: int = 0
    deprecated: int = 0
    resurrected: int = 0
    unchanged: int = 0


class DiffSummary(BaseModel):
    """catalog_import_runs.diff_summary JSONB shape (plan §4.1 M4)."""
    from_version: str
    to_version: str
    entities: Dict[CatalogEntityType, EntityDiffCounts] = Field(default_factory=dict)


# =============================================================================
# Sanity report (staging gates, plan §4.2.2)
# =============================================================================

class SanityCheck(BaseModel):
    check: str  # e.g. "version_parseable", "control_count_drop", "zero_rows", "framework_names"
    passed: bool
    detail: Optional[str] = None


class SanityReport(BaseModel):
    """catalog_import_runs.sanity_report shape. Any failed check → run 'blocked'."""
    passed: bool
    checks: List[SanityCheck] = Field(default_factory=list)


# =============================================================================
# Superseded pairings (plan §4.2.3)
# =============================================================================

class SupersededPairing(BaseModel):
    """Admin-confirmed successor for a control deprecated by a run.

    `superseded_by=None` explicitly records "no successor" (retire outright).
    """
    deprecated_scf_id: str
    superseded_by: Optional[str] = None


class PairingsUpdateRequest(BaseModel):
    pairings: List[SupersededPairing]


class PairingsUpdateResponse(BaseModel):
    run_id: UUID
    pairings: List[SupersededPairing]


# =============================================================================
# Platform import runs (plan §4.2, §4.5)
# =============================================================================

class PlatformImportRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    from_version: Optional[str] = None
    to_version: Optional[str] = None
    status: PlatformRunStatus
    created_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    diff_summary: Optional[DiffSummary] = None


class PlatformImportRunDetail(PlatformImportRunSummary):
    sanity_report: Optional[SanityReport] = None
    superseded_pairings: List[SupersededPairing] = Field(default_factory=list)
    workbook_object_key: Optional[str] = None
    diff_detail_object_key: Optional[str] = None
    error: Optional[str] = None
    applied_at: Optional[datetime] = None
    reverted_at: Optional[datetime] = None


class PlatformImportRunsListResponse(BaseModel):
    runs: List[PlatformImportRunSummary]
    total: int


class UpgradeUploadResponse(BaseModel):
    """POST /api/admin/catalog/upgrade — run created, staging enqueued."""
    run_id: UUID
    status: PlatformRunStatus = PlatformRunStatus.STAGING
    task_id: Optional[str] = None


class DiffItem(BaseModel):
    """One row of the paginated diff view (GET .../runs/{id}/diff).

    A single generic shape across change classes so the endpoint can paginate
    and filter uniformly; class-irrelevant fields are None/empty.
    """
    entity: CatalogEntityType
    change_class: ChangeClass
    key: str
    name: Optional[str] = None
    fields: Dict[str, FieldChange] = Field(default_factory=dict)   # changed / resurrected
    data: Dict[str, Any] = Field(default_factory=dict)             # added
    superseded_by: Optional[str] = None                            # deprecated
    suggestions: List[SupersededSuggestion] = Field(default_factory=list)  # deprecated


class DiffPageResponse(BaseModel):
    run_id: UUID
    items: List[DiffItem]
    total: int
    page: int = 1
    page_size: int = 50
    entity: Optional[CatalogEntityType] = None      # echo of filter
    change_class: Optional[ChangeClass] = None      # echo of filter


class UpgradeApplyRequest(BaseModel):
    """POST .../runs/{id}/apply body (plan §4.5): typed confirmation."""
    expected_to_version: str
    confirm_text: str


class RunActionResponse(BaseModel):
    """Generic accepted/actioned response for apply / cancel / revert."""
    run_id: UUID
    status: str
    task_id: Optional[str] = None
    detail: Optional[str] = None


class UpgradeApplyResponse(RunActionResponse):
    pass


class UpgradeCancelResponse(RunActionResponse):
    pass


class UpgradeRevertResponse(RunActionResponse):
    """Revert is refused (409) while any org is reconciled to to_version;
    blockers are listed in `detail` by WP1c."""
    pass


# =============================================================================
# Tenants reconciliation board (plan §4.5, §4.6)
# =============================================================================

class TenantBoardRow(BaseModel):
    organization_id: UUID
    organization_name: str
    reconciled_catalog_version: Optional[str] = None
    last_reconciled_at: Optional[datetime] = None
    eligible: bool = False
    active_run_id: Optional[UUID] = None
    active_run_status: Optional[OrgRunStatus] = None


class TenantsBoardResponse(BaseModel):
    platform_catalog_version: Optional[str] = None
    tenants: List[TenantBoardRow]
    total: int


# =============================================================================
# Post-apply superseded-by correction (plan §4.2.3 PATCH)
# =============================================================================

class SupersededByPatchRequest(BaseModel):
    superseded_by: Optional[str] = None  # None clears the pairing
    justification: Optional[str] = None  # recorded in the audit log


class SupersededByPatchResponse(BaseModel):
    scf_id: str
    superseded_by: Optional[str] = None


# =============================================================================
# Extended catalog status (plan §4.2.5 — ledger is the version authority)
# =============================================================================

class CatalogStatusExtended(BaseModel):
    """Extended GET /api/catalog/status response (wired in WP1c).

    Superset of the existing {seeded, controls} shape; `catalog_version` is
    the to_version of the latest applied import run (the ledger), or None
    when no applied run exists yet.
    """
    seeded: bool
    controls: int
    catalog_version: Optional[str] = None


# =============================================================================
# Org reconciliation — planned actions and snapshot (plan §4.3)
# =============================================================================

class PlannedAction(BaseModel):
    """Per-deprecated-entity decision stored in run.planned_actions."""
    key: str  # scf_id / evidence_id of the deprecated entity
    entity: CatalogEntityType = CatalogEntityType.CONTROLS
    action: PlannedActionType
    justification: Optional[str] = None
    successor_scf_id: Optional[str] = None  # required by apply when action=migrate


class OrgSnapshotRow(BaseModel):
    """Pre-image of one row touched by an org apply — the rollback authority
    (plan §4.1 M5, §4.3). Restored verbatim on rollback."""
    table: str
    primary_key: Dict[str, Any]
    row: Dict[str, Any]


class OrgSnapshot(BaseModel):
    """organization_reconciliation_runs.org_snapshot JSONB shape."""
    captured_at: datetime
    rows: List[OrgSnapshotRow] = Field(default_factory=list)


# =============================================================================
# Org reconciliation — runs and status (plan §4.3, §4.5)
# =============================================================================

class OrgReconciliationRunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    from_version: Optional[str] = None
    to_version: Optional[str] = None
    catalog_import_run_id: Optional[UUID] = None  # staleness guard anchor
    status: OrgRunStatus
    created_at: datetime
    updated_at: datetime


class OrgReconciliationRunDetail(OrgReconciliationRunSummary):
    diff_summary: Optional[DiffSummary] = None
    planned_actions: List[PlannedAction] = Field(default_factory=list)
    actions_log: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[str] = None
    applied_at: Optional[datetime] = None
    rolled_back_at: Optional[datetime] = None


class OrgReconciliationRunsListResponse(BaseModel):
    runs: List[OrgReconciliationRunSummary]
    total: int


class OrgCatalogStatusResponse(BaseModel):
    """GET /organizations/{org_id}/catalog-reconciliation/status — drives the
    org-visible version card and 'catalog {v} available' banner (plan §4.6)."""
    organization_id: UUID
    reconciled_catalog_version: Optional[str] = None
    platform_catalog_version: Optional[str] = None
    eligible: bool = False
    last_reconciled_at: Optional[datetime] = None
    active_run: Optional[OrgReconciliationRunSummary] = None
    first_reconciliation: bool = False  # M3 backfill is heuristic → framework confirm required


# =============================================================================
# Org reconciliation — preview (plan §4.3 branches a–e)
# =============================================================================

class ScopeAdditionItem(BaseModel):
    """(a) new control intersecting the org's active framework selections."""
    scf_id: str
    name: Optional[str] = None
    frameworks: List[str] = Field(default_factory=list)


class ScopeAdditionsPreview(BaseModel):
    in_scope: List[ScopeAdditionItem] = Field(default_factory=list)
    out_of_scope_count: int = 0  # count-only for non-selected frameworks


class DeprecatedImpactItem(BaseModel):
    """(b) deprecated entity the org has data on, with the action decision."""
    key: str
    entity: CatalogEntityType = CatalogEntityType.CONTROLS
    name: Optional[str] = None
    data_summary: Dict[str, Any] = Field(default_factory=dict)  # org data at stake
    superseded_by: Optional[str] = None
    suggested_action: PlannedActionType = PlannedActionType.RETAIN
    planned_action: Optional[PlannedAction] = None


class ChangedInScopeItem(BaseModel):
    """(c) changed control ∩ org's selected controls — informational."""
    scf_id: str
    name: Optional[str] = None
    fields: Dict[str, FieldChange] = Field(default_factory=dict)
    reassessment_recommended: bool = False  # flagged where composites exist


class OrphanItem(BaseModel):
    """(d) pre-existing org row referencing an invalid catalog key."""
    source_table: str
    key: str
    detail: Optional[str] = None


class OrphanReport(BaseModel):
    """Report-only; never blocks a reconciliation."""
    items: List[OrphanItem] = Field(default_factory=list)
    count: int = 0


class FrameworkSelectionItem(BaseModel):
    """(e) one row of the first-reconciliation framework confirmation list."""
    framework_id: str
    source: FrameworkSelectionSource
    active: bool = True


class FrameworkConfirmation(BaseModel):
    required: bool = False  # True on the org's first reconciliation only
    selections: List[FrameworkSelectionItem] = Field(default_factory=list)


class ReconciliationPreviewRequest(BaseModel):
    """POST .../preview body. target_version defaults to the platform's
    current (ledger) version; skip-version catch-up unions ledger diffs."""
    target_version: Optional[str] = None


class ReconciliationPreviewResponse(BaseModel):
    """Synchronous preview — creates a run in status 'previewed'."""
    run: OrgReconciliationRunSummary
    additions: ScopeAdditionsPreview
    deprecated_impacts: List[DeprecatedImpactItem] = Field(default_factory=list)
    changed_in_scope: List[ChangedInScopeItem] = Field(default_factory=list)
    orphans: OrphanReport
    framework_confirmation: FrameworkConfirmation


# =============================================================================
# Org reconciliation — actions PUT, apply, rollback, cancel (plan §4.3, §4.5)
# =============================================================================

class ReconciliationActionsUpdateRequest(BaseModel):
    """PUT .../runs/{run_id}/actions — replace planned actions; on the first
    reconciliation also carries the confirmed framework list."""
    actions: List[PlannedAction]
    confirmed_framework_ids: Optional[List[str]] = None


class ReconciliationActionsUpdateResponse(BaseModel):
    run_id: UUID
    actions: List[PlannedAction]


class ReconciliationApplyRequest(BaseModel):
    """POST .../runs/{run_id}/apply — guarded by run status 'previewed' and
    stale-preview refusal (plan §4.3)."""
    expected_to_version: str


class ReconciliationApplyResponse(RunActionResponse):
    pass


class ReconciliationRollbackRequest(BaseModel):
    """POST .../runs/{run_id}/rollback — typed confirmation (plan §4.6)."""
    confirm_text: str


class ReconciliationRollbackResponse(RunActionResponse):
    pass


class ReconciliationCancelResponse(RunActionResponse):
    pass


# =============================================================================
# Org changelog (plan §4.5, §4.6 — viewer-visible)
# =============================================================================

class ChangelogEntry(BaseModel):
    version: str
    applied_at: Optional[datetime] = None
    entity: CatalogEntityType
    change_class: ChangeClass
    key: str
    name: Optional[str] = None
    summary: Optional[str] = None


class OrgChangelogResponse(BaseModel):
    organization_id: UUID
    entries: List[ChangelogEntry] = Field(default_factory=list)
    total: int = 0


# =============================================================================
# Deprecated-catalog read-path badging (plan §4.4 — shared by WP3a/WP3b)
# =============================================================================

class CatalogLifecycleBadge(BaseModel):
    """Mixin for response models that render existing org data referencing a
    possibly-deprecated catalog row (plan §4.4: such rows always resolve,
    badged). All three fields stay None while the referenced row is active.
    """
    catalog_status: Optional[str] = None
    retired_in_version: Optional[str] = None
    superseded_by: Optional[str] = None
