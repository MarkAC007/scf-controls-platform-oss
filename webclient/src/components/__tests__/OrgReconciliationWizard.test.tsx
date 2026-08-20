/**
 * OrgReconciliationWizard flow (mocked fetch — backend routes are 501 stubs).
 *
 * The assertions that matter: preview renders all five §4.3 sections (a–e),
 * retire-only decisions demand a justification, the first-reconciliation
 * framework confirmation gates saving, apply stays disabled until decisions
 * are saved (PUT carrying the confirmed framework ids), and rollback is
 * gated on typing the exact version with confirm_text travelling in the
 * request.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import OrgReconciliationWizard from '../platform/OrgReconciliationWizard'
import {
  applyOrgReconciliation,
  getOrgReconciliationRun,
  getOrgReconciliationStatus,
  listOrgReconciliationRuns,
  postOrgReconciliationPreview,
  putOrgReconciliationActions,
  rollbackOrgReconciliation,
} from '../../data/catalogUpgradeApi'
import type {
  OrgReconciliationRunDetail,
  OrgReconciliationRunSummary,
  ReconciliationPreviewResponse,
} from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', () => ({
  getOrgReconciliationStatus: vi.fn(),
  listOrgReconciliationRuns: vi.fn(),
  getOrgReconciliationRun: vi.fn(),
  postOrgReconciliationPreview: vi.fn(),
  putOrgReconciliationActions: vi.fn(),
  applyOrgReconciliation: vi.fn(),
  rollbackOrgReconciliation: vi.fn(),
  cancelOrgReconciliationRun: vi.fn(),
}))

const mockStatus = vi.mocked(getOrgReconciliationStatus)
const mockListRuns = vi.mocked(listOrgReconciliationRuns)
const mockGetRun = vi.mocked(getOrgReconciliationRun)
const mockPreview = vi.mocked(postOrgReconciliationPreview)
const mockPutActions = vi.mocked(putOrgReconciliationActions)
const mockApply = vi.mocked(applyOrgReconciliation)
const mockRollback = vi.mocked(rollbackOrgReconciliation)

// Fixture keys deliberately avoid the real SCF `XXX-NN` id shape.
const ORG_ID = 'org-1'
const RUN_ID = 'org-run-1'

function runSummary(): OrgReconciliationRunSummary {
  return {
    id: RUN_ID,
    organization_id: ORG_ID,
    from_version: '2026.1',
    to_version: '2026.2',
    catalog_import_run_id: 'import-run-1',
    status: 'previewed',
    created_at: '2026-08-20T10:00:00Z',
    updated_at: '2026-08-20T10:00:00Z',
  }
}

function previewedDetail(): OrgReconciliationRunDetail {
  return { ...runSummary(), planned_actions: [], actions_log: [] }
}

function appliedDetail(): OrgReconciliationRunDetail {
  return {
    ...runSummary(),
    status: 'applied',
    planned_actions: [],
    actions_log: [{ step: 'scope' }, { step: 'migrate' }, { step: 'state' }],
    applied_at: '2026-08-20T11:00:00Z',
  }
}

function previewResponse(): ReconciliationPreviewResponse {
  return {
    run: runSummary(),
    additions: {
      in_scope: [{ scf_id: 'NEW-1', name: 'New Control', frameworks: ['iso_27001'] }],
      out_of_scope_count: 4,
    },
    deprecated_impacts: [
      {
        key: 'DEP-1',
        entity: 'controls',
        name: 'Legacy Control',
        data_summary: { evidence_items: 3 },
        superseded_by: 'SUC-1',
        suggested_action: 'migrate',
        planned_action: null,
      },
      {
        key: 'DEP-2',
        entity: 'controls',
        name: 'Orphaned Control',
        data_summary: { evidence_items: 1 },
        superseded_by: null,
        suggested_action: 'retain',
        planned_action: null,
      },
    ],
    changed_in_scope: [
      {
        scf_id: 'CHG-1',
        name: 'Changed Control',
        fields: { description: { old: 'Old wording', new: 'New wording' } },
        reassessment_recommended: true,
      },
    ],
    orphans: {
      items: [{ source_table: 'scoped_controls', key: 'GONE-1', detail: null }],
      count: 1,
    },
    framework_confirmation: {
      required: true,
      selections: [{ framework_id: 'iso_27001', source: 'backfill', active: true }],
    },
  }
}

function renderWizard() {
  return render(
    <OrgReconciliationWizard
      organizationId={ORG_ID}
      organizationName="Acme Corp"
      onRunSettled={vi.fn()}
      onClose={vi.fn()}
    />
  )
}

/** Mount on an eligible org with no runs, and create a preview in-session. */
async function openPreview() {
  renderWizard()
  fireEvent.click(await screen.findByRole('button', { name: 'Preview reconciliation' }))
  await screen.findByText('NEW-1')
}

beforeEach(() => {
  vi.clearAllMocks()
  mockStatus.mockResolvedValue({
    organization_id: ORG_ID,
    reconciled_catalog_version: '2026.1',
    platform_catalog_version: '2026.2',
    eligible: true,
    active_run: null,
    first_reconciliation: true,
  })
  mockListRuns.mockResolvedValue({ runs: [], total: 0 })
  mockPreview.mockResolvedValue(previewResponse())
  mockGetRun.mockResolvedValue(previewedDetail())
  mockPutActions.mockResolvedValue({ run_id: RUN_ID, actions: [] })
  mockApply.mockResolvedValue({ run_id: RUN_ID, status: 'applying' })
  mockRollback.mockResolvedValue({ run_id: RUN_ID, status: 'rolling_back' })
})

describe('OrgReconciliationWizard preview flow', () => {
  it('renders all five preview sections (a–e)', async () => {
    await openPreview()

    // (a) scope additions incl. out-of-scope count
    expect(screen.getByText('New Control')).toBeInTheDocument()
    expect(screen.getByText(/4 new controls in frameworks/)).toBeInTheDocument()
    // (b) deprecated-with-org-data table with successor and data at stake
    expect(screen.getByText('DEP-1')).toBeInTheDocument()
    expect(screen.getByText('SUC-1')).toBeInTheDocument()
    expect(screen.getByText('evidence items: 3')).toBeInTheDocument()
    // (c) changed-in-scope with re-assessment flag and field-level old/new
    expect(screen.getByText('CHG-1')).toBeInTheDocument()
    expect(screen.getByText('Re-assessment recommended')).toBeInTheDocument()
    expect(screen.getByText('Old wording')).toBeInTheDocument()
    // (d) orphan report banner, report-only
    expect(screen.getByText('Orphan report.')).toBeInTheDocument()
    expect(screen.getByText('GONE-1')).toBeInTheDocument()
    // (e) first-reconciliation framework confirmation
    expect(screen.getByLabelText('Confirm framework selections')).toBeInTheDocument()

    // migrate defaults where superseded_by is set; retain where not; the
    // migrate radio is disabled without a successor
    expect(screen.getByLabelText('Migrate DEP-1')).toBeChecked()
    expect(screen.getByLabelText('Retain DEP-2')).toBeChecked()
    expect(screen.getByLabelText('Migrate DEP-2')).toBeDisabled()
  })

  it('gates save on the framework confirmation and apply on saved decisions', async () => {
    await openPreview()

    const saveButton = screen.getByRole('button', { name: 'Save decisions' })
    const applyButton = screen.getByRole('button', { name: 'Apply reconciliation' })

    // First reconciliation: save blocked until the framework list is confirmed
    expect(saveButton).toBeDisabled()
    expect(applyButton).toBeDisabled()

    fireEvent.click(screen.getByLabelText('Confirm framework selections'))
    expect(saveButton).toBeEnabled()
    expect(applyButton).toBeDisabled()

    // Save: PUT carries the decisions and the confirmed framework ids
    fireEvent.click(saveButton)
    await waitFor(() =>
      expect(mockPutActions).toHaveBeenCalledWith(
        ORG_ID,
        RUN_ID,
        expect.arrayContaining([
          expect.objectContaining({ key: 'DEP-1', action: 'migrate', successor_scf_id: 'SUC-1' }),
          expect.objectContaining({ key: 'DEP-2', action: 'retain' }),
        ]),
        ['iso_27001']
      )
    )

    // Apply becomes available and posts the stale-preview guard version
    mockGetRun.mockResolvedValue(appliedDetail())
    await waitFor(() => expect(applyButton).toBeEnabled())
    fireEvent.click(applyButton)
    await waitFor(() => expect(mockApply).toHaveBeenCalledWith(ORG_ID, RUN_ID, '2026.2'))

    // The refreshed run is applied → report
    expect(await screen.findByText('Reconciliation applied.')).toBeInTheDocument()
    expect(screen.getByText(/3 actions executed/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Roll back…' })).toBeInTheDocument()
  })

  it('requires a justification before saving a retire-only decision', async () => {
    await openPreview()
    fireEvent.click(screen.getByLabelText('Confirm framework selections'))

    const saveButton = screen.getByRole('button', { name: 'Save decisions' })
    expect(saveButton).toBeEnabled()

    // Switching to retire-only without a justification blocks saving
    fireEvent.click(screen.getByLabelText('Retire only DEP-2'))
    expect(saveButton).toBeDisabled()

    fireEvent.change(screen.getByLabelText('Justification for DEP-2'), {
      target: { value: 'Control retired; no successor applies to us' },
    })
    expect(saveButton).toBeEnabled()

    fireEvent.click(saveButton)
    await waitFor(() =>
      expect(mockPutActions).toHaveBeenCalledWith(
        ORG_ID,
        RUN_ID,
        expect.arrayContaining([
          expect.objectContaining({
            key: 'DEP-2',
            action: 'retire_only',
            justification: 'Control retired; no successor applies to us',
          }),
        ]),
        ['iso_27001']
      )
    )
  })
})

describe('OrgReconciliationWizard rollback', () => {
  it('gates rollback on typing the exact version and sends confirm_text', async () => {
    mockStatus.mockResolvedValue({
      organization_id: ORG_ID,
      reconciled_catalog_version: '2026.2',
      platform_catalog_version: '2026.2',
      eligible: false,
      active_run: null,
      first_reconciliation: false,
    })
    const applied = appliedDetail()
    mockListRuns.mockResolvedValue({ runs: [applied], total: 1 })
    mockGetRun.mockResolvedValue(applied)

    renderWizard()

    fireEvent.click(await screen.findByRole('button', { name: 'Roll back…' }))

    // The dialog states how many rows the snapshot restore covers
    expect(screen.getByText('3', { selector: 'strong' })).toBeInTheDocument()
    expect(screen.getByText(/pre-/)).toBeInTheDocument()

    const confirmButton = screen.getByRole('button', { name: 'Roll back' })
    expect(confirmButton).toBeDisabled()

    // Wrong text keeps the rollback disabled
    fireEvent.change(screen.getByLabelText('Confirm rollback version'), {
      target: { value: '2026.1' },
    })
    expect(confirmButton).toBeDisabled()
    expect(mockRollback).not.toHaveBeenCalled()

    // Exact version enables it; confirm_text travels in the request
    fireEvent.change(screen.getByLabelText('Confirm rollback version'), {
      target: { value: '2026.2' },
    })
    expect(confirmButton).toBeEnabled()

    mockGetRun.mockResolvedValue({ ...applied, status: 'rolling_back' })
    fireEvent.click(confirmButton)

    await waitFor(() => expect(mockRollback).toHaveBeenCalledWith(ORG_ID, RUN_ID, '2026.2'))
    expect(await screen.findByText(/Rolling back — restoring/)).toBeInTheDocument()
  })
})
