/**
 * Catalog upgrade API shapes — frontend mirror of the frozen WP-C contracts
 * in ``backend/schemas_catalog_upgrade.py`` (platform-admin surface).
 *
 * These types are IMPORTED by downstream frontend WPs; changing a shape here
 * requires the matching backend contract-change WP first. UUIDs and datetimes
 * arrive as strings over JSON.
 */

// ─── Enums ──────────────────────────────────────────────────────────────────

/** Entities the platform diff engine reports on (plan §4.2.2). */
export type CatalogEntityType =
  | 'controls'
  | 'domains'
  | 'evidence'
  | 'assessment_objectives'
  | 'capability_themes'
  | 'framework_mappings'

export const CATALOG_ENTITY_TYPES: CatalogEntityType[] = [
  'controls',
  'domains',
  'evidence',
  'assessment_objectives',
  'capability_themes',
  'framework_mappings',
]

/** Per-entity diff classification (plan §4.2.2). */
export type ChangeClass = 'added' | 'changed' | 'deprecated' | 'resurrected' | 'unchanged'

export const CHANGE_CLASSES: ChangeClass[] = ['added', 'changed', 'deprecated', 'resurrected', 'unchanged']

/** catalog_import_runs.status (plan §4.1 M4). */
export type PlatformRunStatus =
  | 'staging'
  | 'staged'
  | 'blocked'
  | 'applying'
  | 'applied'
  | 'failed'
  | 'cancelled'
  | 'reverted'

/** organization_reconciliation_runs.status (plan §4.1 M5). */
export type OrgRunStatus =
  | 'previewed'
  | 'applying'
  | 'applied'
  | 'failed'
  | 'rolling_back'
  | 'rolled_back'
  | 'cancelled'

// ─── Diff shapes ────────────────────────────────────────────────────────────

/** Old AND new value for one changed field (the diff is the revert anchor). */
export interface FieldChange {
  old?: unknown
  new?: unknown
}

/** Display-only successor suggestion for a planned deprecation (plan §4.2.3). */
export interface SupersededSuggestion {
  scf_id: string
  name?: string | null
  score: number
}

/** Count-only view of one entity's diff. */
export interface EntityDiffCounts {
  added: number
  changed: number
  deprecated: number
  resurrected: number
  unchanged: number
}

/** catalog_import_runs.diff_summary shape (plan §4.1 M4). */
export interface DiffSummary {
  from_version: string
  to_version: string
  entities: Partial<Record<CatalogEntityType, EntityDiffCounts>>
}

/** One row of the paginated diff view (GET .../runs/{id}/diff). */
export interface DiffItem {
  entity: CatalogEntityType
  change_class: ChangeClass
  key: string
  name?: string | null
  fields: Record<string, FieldChange>
  data: Record<string, unknown>
  superseded_by?: string | null
  suggestions: SupersededSuggestion[]
}

export interface DiffPageResponse {
  run_id: string
  items: DiffItem[]
  total: number
  page: number
  page_size: number
  entity?: CatalogEntityType | null
  change_class?: ChangeClass | null
}

// ─── Sanity report ──────────────────────────────────────────────────────────

export interface SanityCheck {
  check: string
  passed: boolean
  detail?: string | null
}

/** Any failed check → run 'blocked'. */
export interface SanityReport {
  passed: boolean
  checks: SanityCheck[]
}

// ─── Superseded pairings ────────────────────────────────────────────────────

/** ``superseded_by: null`` explicitly records "no successor" (retire outright). */
export interface SupersededPairing {
  deprecated_scf_id: string
  superseded_by: string | null
}

export interface PairingsUpdateRequest {
  pairings: SupersededPairing[]
}

export interface PairingsUpdateResponse {
  run_id: string
  pairings: SupersededPairing[]
}

// ─── Platform import runs ───────────────────────────────────────────────────

export interface PlatformImportRunSummary {
  id: string
  from_version?: string | null
  to_version?: string | null
  status: PlatformRunStatus
  created_by?: string | null
  created_at: string
  updated_at: string
  diff_summary?: DiffSummary | null
}

export interface PlatformImportRunDetail extends PlatformImportRunSummary {
  sanity_report?: SanityReport | null
  superseded_pairings: SupersededPairing[]
  workbook_object_key?: string | null
  diff_detail_object_key?: string | null
  error?: string | null
  applied_at?: string | null
  reverted_at?: string | null
}

export interface PlatformImportRunsListResponse {
  runs: PlatformImportRunSummary[]
  total: number
}

/** POST /api/admin/catalog/upgrade — run created, staging enqueued (202). */
export interface UpgradeUploadResponse {
  run_id: string
  status: PlatformRunStatus
  task_id?: string | null
}

/** POST .../runs/{id}/apply body (plan §4.5): typed confirmation. */
export interface UpgradeApplyRequest {
  expected_to_version: string
  confirm_text: string
}

/** Generic accepted/actioned response for apply / cancel / revert. */
export interface RunActionResponse {
  run_id: string
  status: string
  task_id?: string | null
  detail?: string | null
}

export type UpgradeApplyResponse = RunActionResponse
export type UpgradeCancelResponse = RunActionResponse
/** Revert is refused (409) while any org is reconciled to to_version. */
export type UpgradeRevertResponse = RunActionResponse

// ─── Tenants reconciliation board (page owned by WP4b) ──────────────────────

export interface TenantBoardRow {
  organization_id: string
  organization_name: string
  reconciled_catalog_version?: string | null
  last_reconciled_at?: string | null
  eligible: boolean
  active_run_id?: string | null
  active_run_status?: OrgRunStatus | null
}

export interface TenantsBoardResponse {
  platform_catalog_version?: string | null
  tenants: TenantBoardRow[]
  total: number
}

// ─── Post-apply superseded-by correction ────────────────────────────────────

export interface SupersededByPatchRequest {
  superseded_by: string | null
  justification?: string | null
}

export interface SupersededByPatchResponse {
  scf_id: string
  superseded_by: string | null
}

// ─── Extended catalog status ────────────────────────────────────────────────

/**
 * Extended GET /api/catalog/status response (plan §4.2.5). Superset of the
 * existing {seeded, controls}; ``catalog_version`` is the to_version of the
 * latest applied import run, or null before any upgrade has been applied.
 */
export interface CatalogStatusExtended {
  seeded: boolean
  controls: number
  catalog_version?: string | null
}

// ─── Org reconciliation — planned actions (plan §4.3b) ──────────────────────

/** Per-deprecated-control decision in an org reconciliation. */
export type PlannedActionType = 'migrate' | 'retain' | 'retire_only'

/** organization_framework_selections.source (plan §4.1 M3). */
export type FrameworkSelectionSource = 'bulk_scope' | 'backfill' | 'reconciliation'

/** Per-deprecated-entity decision stored in run.planned_actions. */
export interface PlannedAction {
  key: string
  entity: CatalogEntityType
  action: PlannedActionType
  justification?: string | null
  successor_scf_id?: string | null
}

// ─── Org reconciliation — runs and status (plan §4.3, §4.5) ─────────────────

// ─── Catalog lifecycle badge fields (read-path rows, plan §4.4) ─────────────

/**
 * Lifecycle badge fields WP3a/WP3b attach to read-path rows that render
 * existing org data (controls listing, catalog API, theme drill-down, …).
 * All optional: older payloads and not-yet-swept endpoints omit them.
 */
export interface CatalogLifecycleFields {
  catalog_status?: string | null
  retired_in_version?: string | null
  superseded_by?: string | null
}
export interface OrgReconciliationRunSummary {
  id: string
  organization_id: string
  from_version?: string | null
  to_version?: string | null
  catalog_import_run_id?: string | null
  status: OrgRunStatus
  created_at: string
  updated_at: string
}

export interface OrgReconciliationRunDetail extends OrgReconciliationRunSummary {
  diff_summary?: DiffSummary | null
  planned_actions: PlannedAction[]
  actions_log: Record<string, unknown>[]
  error?: string | null
  applied_at?: string | null
  rolled_back_at?: string | null
}

export interface OrgReconciliationRunsListResponse {
  runs: OrgReconciliationRunSummary[]
  total: number
}

/** GET /organizations/{org_id}/catalog-reconciliation/status. */
export interface OrgCatalogStatusResponse {
  organization_id: string
  reconciled_catalog_version?: string | null
  platform_catalog_version?: string | null
  eligible: boolean
  last_reconciled_at?: string | null
  active_run?: OrgReconciliationRunSummary | null
  /** True on the org's first reconciliation — framework confirm required. */
  first_reconciliation: boolean
}

// ─── Org reconciliation — preview sections a–e (plan §4.3) ──────────────────

/** (a) new control intersecting the org's active framework selections. */
export interface ScopeAdditionItem {
  scf_id: string
  name?: string | null
  frameworks: string[]
}

export interface ScopeAdditionsPreview {
  in_scope: ScopeAdditionItem[]
  /** Count-only for controls in frameworks the org has not selected. */
  out_of_scope_count: number
}

/** (b) deprecated entity the org has data on, with the action decision. */
export interface DeprecatedImpactItem {
  key: string
  entity: CatalogEntityType
  name?: string | null
  data_summary: Record<string, unknown>
  superseded_by?: string | null
  suggested_action: PlannedActionType
  planned_action?: PlannedAction | null
}

/** (c) changed control ∩ org's selected controls — informational. */
export interface ChangedInScopeItem {
  scf_id: string
  name?: string | null
  fields: Record<string, FieldChange>
  reassessment_recommended: boolean
}

/** (d) pre-existing org row referencing an invalid catalog key. */
export interface OrphanItem {
  source_table: string
  key: string
  detail?: string | null
}

/** Report-only; never blocks a reconciliation. */
export interface OrphanReport {
  items: OrphanItem[]
  count: number
}

/** (e) one row of the first-reconciliation framework confirmation list. */
export interface FrameworkSelectionItem {
  framework_id: string
  source: FrameworkSelectionSource
  active: boolean
}

export interface FrameworkConfirmation {
  required: boolean
  selections: FrameworkSelectionItem[]
}

/** POST .../preview — synchronous, creates a run in status 'previewed'. */
export interface ReconciliationPreviewResponse {
  run: OrgReconciliationRunSummary
  additions: ScopeAdditionsPreview
  deprecated_impacts: DeprecatedImpactItem[]
  changed_in_scope: ChangedInScopeItem[]
  orphans: OrphanReport
  framework_confirmation: FrameworkConfirmation
}

// ─── Org reconciliation — actions PUT, apply, rollback, cancel ──────────────

export interface ReconciliationActionsUpdateResponse {
  run_id: string
  actions: PlannedAction[]
}

export type ReconciliationApplyResponse = RunActionResponse
export type ReconciliationRollbackResponse = RunActionResponse
export type ReconciliationCancelResponse = RunActionResponse

// ─── Org changelog (org-visible, plan §4.6) ─────────────────────────────────

/** One applied catalog change visible to the org. */
export interface ChangelogEntry {
  version: string
  applied_at?: string | null
  entity: CatalogEntityType
  change_class: ChangeClass
  key: string
  name?: string | null
  summary?: string | null
}

/** GET /api/organizations/{org_id}/catalog-changelog response. */
export interface OrgChangelogResponse {
  organization_id: string
  entries: ChangelogEntry[]
  total: number
}
