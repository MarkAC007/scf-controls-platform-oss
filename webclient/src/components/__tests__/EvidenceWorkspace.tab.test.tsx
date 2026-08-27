/**
 * Confirms the sub-tab strip in EvidenceWorkspace is rendered via the
 * Phase-1 TabRow component. URL semantics are covered by the deeplink suite;
 * this pins the visual chrome change only.
 */
import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import EvidenceWorkspace from '../EvidenceWorkspace'
import type { ScopedControlsFile } from '../../types'

vi.mock('../EvidenceReview', () => ({
  default: () => <div data-testid="workspace-pane" />,
}))
vi.mock('../evidence/EvidenceDashboardTab', () => ({
  default: () => <div data-testid="dashboard-pane" />,
}))

const SCOPING: ScopedControlsFile = {
  organizationId: 'org-1',
  controls: {},
} as unknown as ScopedControlsFile

beforeEach(() => {
  window.history.replaceState({}, '', '/?tab=evidence')
})
afterEach(() => {
  window.history.replaceState({}, '', '/')
})

describe('EvidenceWorkspace uses explorer TabRow chrome', () => {
  it('renders a tablist role for the sub-tab strip', () => {
    render(
      <EvidenceWorkspace
        controls={[]}
        scopingData={SCOPING}
        onScopingDataChange={() => {}}
        organizationId="org-1"
      />,
    )
    expect(screen.getByRole('tablist')).toBeInTheDocument()
  })

  it('renders Dashboard tab with role=tab', () => {
    render(
      <EvidenceWorkspace
        controls={[]}
        scopingData={SCOPING}
        onScopingDataChange={() => {}}
        organizationId="org-1"
      />,
    )
    expect(screen.getByRole('tab', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('renders Workspace tab with role=tab', () => {
    render(
      <EvidenceWorkspace
        controls={[]}
        scopingData={SCOPING}
        onScopingDataChange={() => {}}
        organizationId="org-1"
      />,
    )
    expect(screen.getByRole('tab', { name: 'Workspace' })).toBeInTheDocument()
  })

  it('marks the active tab as aria-selected=true', () => {
    window.history.replaceState({}, '', '/?tab=evidence&view=workspace')
    render(
      <EvidenceWorkspace
        controls={[]}
        scopingData={SCOPING}
        onScopingDataChange={() => {}}
        organizationId="org-1"
      />,
    )
    const workspaceTab = screen.getByRole('tab', { name: 'Workspace' })
    expect(workspaceTab).toHaveAttribute('aria-selected', 'true')
  })
})
