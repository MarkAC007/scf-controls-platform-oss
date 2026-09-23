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
    # The framework REGISTRY: which focal documents the catalogue offers at all.
    # Distinct from FRAMEWORK_MAPPINGS, which is the per-control view of how a
    # control's mapping set moved. A framework can be retired from the registry
    # while every surviving control keeps mappings, and vice versa, so the two
    # are not derivable from one another.
    FRAMEWORKS = "frameworks"


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
    """A successor candidate attached to a planned deprecation (plan §4.2.3).

    For CONTROLS this list is never a guess. It holds at most one entry: the
    successor the workbook itself declares, at score 1.0, with ``signals``
    naming the declaring source. Name-similarity scoring was removed - the
    workbook is the sole authority on control succession, and a generated
    proposal sitting in the same list as a declaration made the two
    indistinguishable to anyone reviewing a four-figure renumbering.

    For FRAMEWORKS the list is still derived (no publisher crosswalk exists for
    focal documents), which is why ``score``, ``control_overlap`` and
    ``ambiguous`` remain on the model.
    """
    scf_id: str
    name: Optional[str] = None
    score: float = Field(..., ge=0.0, le=1.0)
    # Share of the predecessor's controls that also map to this candidate, once
    # control renumbering is undone. Reviewable evidence a non-expert can check
    # without knowing the instrument: "410 of 412 controls carried over".
    # None where either side has too small a control set for the ratio to mean
    # anything.
    control_overlap: Optional[float] = None
    # Which independent signals produced this suggestion (e.g. ["id_stem",
    # "display_name"]). Frameworks have no publisher-declared crosswalk, so the
    # suggestion is derived; naming the signals is what lets a reviewer weigh it
    # rather than take it on faith. Optional so pre-existing stored diffs still
    # validate.
    signals: List[str] = Field(default_factory=list)
    # Present when the suggestion is one of several plausible successors, so a
    # one-to-many ambiguity is visible instead of silently collapsed to the top
    # scorer.
    ambiguous: bool = False


class AddedEntity(BaseModel):
    """A row present in the workbook but not in the live catalog."""
    key: str
    name: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class IdReuse(BaseModel):
    """This key is still in the workbook, but for a DIFFERENT control.

    SCF 2026.3's READ THIS sheet lists 23 controls merged away into survivors.
    14 of those ids were then handed to an unrelated control in the same
    release: ``END-03`` was "Prohibit Installation Without Privileged Status",
    was merged into ``CHG-04.2``, and ``END-03`` in 2026.3 is "Endpoint
    Protection Mechanisms". The diff can only class such a row as ``changed``,
    because the key is present on both sides - so without this flag an operator
    reading the field-level diff sees a wholesale rewrite of a control's name,
    description and mappings with no explanation, and an org's assessment
    history stays silently attached to a control that no longer means what it
    meant.

    ``merged_into`` is where the ORIGINAL owner of the id went; ``legacy_name``
    is what the id used to be called. Information only: nothing is re-scoped
    and no pairing is implied. The id's original owner is not deprecated by
    this run (its key survives), so there is nothing to pair.
    """
    merged_into: str
    legacy_name: Optional[str] = None


class ChangedEntity(BaseModel):
    """A row present in both, with field-level differences."""
    key: str
    name: Optional[str] = None
    fields: Dict[str, FieldChange] = Field(default_factory=dict)
    # Set when the publisher declared THIS key merged away while the key itself
    # survives in the new workbook for an unrelated control. Optional so stored
    # diffs written before the field existed still validate.
    id_reused: Optional[IdReuse] = None


class DeprecatedEntity(BaseModel):
    """A row active in the live catalog but absent from the workbook."""
    key: str
    name: Optional[str] = None
    superseded_by: Optional[str] = None
    # Where superseded_by came from, and the ONLY marker that this run claims a
    # succession for the row. 'workbook_crosswalk' is the workbook's Legacy
    # SCF # column; 'publisher_merged' is the READ THIS sheet's merge list.
    # Either way the publisher declared it, so the row is a rename rather than
    # a retirement. None means this run declares nothing and the value (if any)
    # predates the run as an admin pairing on the live row.
    superseded_source: Optional[str] = None
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


# =============================================================================
# Publisher change sheets (SCF 2026.3 onward, plan §4.2.2)
# =============================================================================

class PublisherFrameworkRef(BaseModel):
    """A focal document the publisher declares added or removed in a release.

    Keyed by the FOCAL DOCUMENT IDENTIFIER, not by our framework id. The FDI is
    the publisher's stable identity for the document and the only thing the
    errata sheet gives us; resolving it to a live framework id is the diff's job.
    """
    fdi: str
    name: Optional[str] = None


class PublisherFrameworkErratum(BaseModel):
    """A focal document that is still shipping but whose mappings moved.

    ``note`` is the publisher's own text ('FDE #: 8.10, 8.12, 8.5'), kept
    verbatim: the reference format varies per document family and nothing
    downstream acts on the individual requirement ids.
    """
    fdi: str
    name: Optional[str] = None
    note: str


class PublisherFrameworkChanges(BaseModel):
    """Framework-level errata, straight from the STRM Errata sheet."""
    added: List[PublisherFrameworkRef] = Field(default_factory=list)
    removed: List[PublisherFrameworkRef] = Field(default_factory=list)
    mapping_errata: List[PublisherFrameworkErratum] = Field(default_factory=list)


class PublisherControlMerge(BaseModel):
    """A deprecated control the publisher merged into a survivor."""
    legacy_scf_id: Optional[str] = None
    legacy_name: Optional[str] = None
    merged_into: Optional[str] = None


class PublisherControlChanges(BaseModel):
    """Per-control publisher change tags.

    ``counts`` is tag OCCURRENCES, not rows: one control routinely carries
    'renumbered' and 'wordsmithed' together. An empty dict means the workbook
    shipped no Change Overview sheet at all, which is different from a release
    that reported every tag as zero.
    """
    counts: Dict[str, int] = Field(default_factory=dict)
    merged: List[PublisherControlMerge] = Field(default_factory=list)
    tags: Dict[str, List[str]] = Field(default_factory=dict)


class PublisherChanges(BaseModel):
    """What the publisher SAYS it changed, as opposed to what we derived.

    A declaration is a stronger claim than any heuristic: a focal document the
    publisher lists as removed is a deliberate retirement, and the churn gate
    treats it as accounted for. Absent from every workbook up to 2026.2, so
    every consumer must tolerate None.
    """
    summary: Optional[str] = None
    frameworks: PublisherFrameworkChanges = Field(
        default_factory=PublisherFrameworkChanges
    )
    controls: PublisherControlChanges = Field(default_factory=PublisherControlChanges)


class PublisherChangesSummary(BaseModel):
    """Count-only mirror of ``PublisherChanges`` for the diff summary JSONB."""
    summary: Optional[str] = None
    frameworks_added: int = 0
    frameworks_removed: int = 0
    mapping_errata: int = 0
    controls: Dict[str, int] = Field(default_factory=dict)


class DiffDetail(BaseModel):
    """The complete stored diff object for a platform import run.

    ``framework_registry`` is the workbook's own registry
    (``{framework_id: {name, focal_document_id, geography}}``), carried here so
    the apply transaction can persist it without re-reading the workbook — the
    diff is the only thing that survives staging. Defaulted so diffs stored
    before the field existed still validate.
    """
    from_version: str
    to_version: str
    entities: Dict[CatalogEntityType, EntityDiff] = Field(default_factory=dict)
    framework_registry: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    # The publisher's own account of the release (2026.3+). None when the
    # workbook shipped no change sheets, which is every release before it, and
    # also the value stored diffs from before this field carried.
    publisher_changes: Optional[PublisherChanges] = None


class EntityDiffCounts(BaseModel):
    """Count-only view of one entity's diff.

    ``renamed`` is a subset of ``deprecated``, not a sixth disjoint class: it
    counts the deprecations the workbook itself identifies as renumberings.
    deprecated - renamed is the true retirement count. Defaulted so summaries
    stored before the crosswalk existed still validate.
    """
    added: int = 0
    changed: int = 0
    deprecated: int = 0
    resurrected: int = 0
    unchanged: int = 0
    renamed: int = 0
    # Subset of ``changed``: rows whose key the publisher declared merged away
    # and then reused for an unrelated control. Defaulted so summaries stored
    # before the flag existed still validate.
    id_reused: int = 0


class DiffSummary(BaseModel):
    """catalog_import_runs.diff_summary JSONB shape (plan §4.1 M4)."""
    from_version: str
    to_version: str
    entities: Dict[CatalogEntityType, EntityDiffCounts] = Field(default_factory=dict)
    # Counts only; the full lists live in the diff detail. Defaulted so
    # summaries stored before this field existed still validate.
    publisher_changes: Optional[PublisherChangesSummary] = None


# =============================================================================
# Sanity report (staging gates, plan §4.2.2)
# =============================================================================

class SanityCheck(BaseModel):
    # e.g. "version_parseable", "control_count_drop", "control_churn",
    #      "zero_rows", "framework_names"
    check: str
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
    # Which authority named superseded_by; None where this run names nobody.
    superseded_source: Optional[str] = None                        # deprecated
    suggestions: List[SupersededSuggestion] = Field(default_factory=list)  # deprecated
    id_reused: Optional[IdReuse] = None                            # changed


class DiffPageResponse(BaseModel):
    run_id: UUID
    items: List[DiffItem]
    total: int
    page: int = 1
    page_size: int = 50
    entity: Optional[CatalogEntityType] = None      # echo of filter
    change_class: Optional[ChangeClass] = None      # echo of filter


# =============================================================================
# Live framework registry (the succession seam, plan §4.2.2)
# =============================================================================

class FrameworkRegistryStatus(BaseModel):
    """Read-only state of the framework registry for the LIVE catalogue version.

    ``present`` False, or ``with_focal_document_id`` 0, both mean the same thing
    operationally: the next upgrade's declared succession tier cannot fire and
    the framework_churn gate will block. The console shows this on the catalogue
    version card so the operator learns it before uploading a workbook.
    """
    catalog_version: Optional[str] = None
    present: bool = False
    entries: int = 0
    with_focal_document_id: int = 0
    source: Optional[str] = None


class FrameworkRegistryRegistration(BaseModel):
    """Result of registering the CURRENT catalogue's workbook (POST).

    ``catalog_version`` is the live version the row was stamped with;
    ``workbook_version`` is the version read out of the uploaded workbook. They
    are equal on every accepted registration — the endpoint refuses a mismatch
    with a 409 rather than writing identifiers that do not describe the live
    rows — and both are reported so the operator can see what was compared.
    """
    catalog_version: str
    workbook_version: str
    entries: int
    with_focal_document_id: int
    source: str


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
    # The org is holding an assessment against an id whose original owner was
    # merged elsewhere. Surfaced, never acted on: no automatic re-scoping.
    id_reused: Optional[IdReuse] = None


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


class FrameworkImpactItem(BaseModel):
    """(f) a framework the org selected that this upgrade retires.

    The catalogue encodes the edition in the framework id, so an ordinary
    version bump presents as an unrelated removal plus an unrelated addition:
    an org scoped to ``apac_australia_ism_march_2026`` would simply lose its
    framework, with the June edition offered as something new. This item is the
    decision that replaces that silence.

    ``superseded_source`` says how much the successor is worth: a
    ``workbook_focal_document`` pairing is the publisher's own statement that
    the two ids are the same document, a ``derived_succession`` pairing is this
    platform's inference from the id and the display name. Both are proposals —
    nothing rebinds a selection until an admin sets the action and applies.
    """
    framework_id: str
    name: Optional[str] = None
    superseded_by: Optional[str] = None
    superseded_by_name: Optional[str] = None
    superseded_source: Optional[str] = None
    confidence: Optional[float] = None
    signals: List[str] = Field(default_factory=list)
    ambiguous: bool = False
    control_overlap: Optional[float] = None
    # Other candidates the matcher considered. Present so a one-to-many match
    # is visible to the reviewer rather than silently collapsed to the winner.
    alternatives: List[SupersededSuggestion] = Field(default_factory=list)
    suggested_action: PlannedActionType = PlannedActionType.RETAIN
    planned_action: Optional[PlannedAction] = None


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
    # (f) frameworks the org has selected that this upgrade retires, each with
    # a proposed action. Additive: framework_confirmation above is unchanged.
    framework_impacts: List[FrameworkImpactItem] = Field(default_factory=list)
    # Retired frameworks the org never selected. A count, not a list: they are
    # not this tenant's decision, but a reviewer seeing 1 impact out of 75
    # retirements is better informed than one seeing 1 out of nothing.
    frameworks_retired_outside_scope: int = 0


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
