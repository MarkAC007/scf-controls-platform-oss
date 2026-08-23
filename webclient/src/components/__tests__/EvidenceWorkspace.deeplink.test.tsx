/**
 * The evidence workspace's half of the URL contract (#785).
 *
 * Before this, reaching an evidence item put its id in
 * `sessionStorage` under a key two files knew about. Nothing about that
 * survived a reload, could be pasted to a colleague, or could be linked from a
 * notification — which is why the notification defect was unfixable.
 *
 * These cases pin the sub-tab half: seeded from the URL on mount, written on
 * every switch, restored on Back. `EvidenceReview` and `EvidenceDashboardTab`
 * are mocked; both fetch their own data and neither is under test here.
 */
import { render, screen, fireEvent, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import EvidenceWorkspace from '../EvidenceWorkspace'
import type { ScopedControlsFile } from '../../types'

vi.mock('../EvidenceReview', () => ({
  default: () => <div data-testid="workspace-pane" />,
}))
vi.mock('../evidence/EvidenceDashboardTab', () => ({
  default: ({ onNavigateToEvidence }: { onNavigateToEvidence: (id: string) => void }) => (
    <button data-testid="jump" onClick={() => onNavigateToEvidence('E-HRS-16')}>
      jump
    </button>
  ),
}))

const SCOPING: ScopedControlsFile = {
  organizationId: 'org-1',
  controls: {},
} as unknown as ScopedControlsFile

function renderAt(search: string) {
  window.history.replaceState({}, '', `/${search}`)
  return render(
    <EvidenceWorkspace
      controls={[]}
      scopingData={SCOPING}
      onScopingDataChange={() => {}}
      organizationId="org-1"
    />,
  )
}

const search = () => new URLSearchParams(window.location.search)

let pushSpy: ReturnType<typeof vi.spyOn>

beforeEach(() => {
  pushSpy = vi.spyOn(window.history, 'pushState')
})

afterEach(() => {
  pushSpy.mockRestore()
  window.history.replaceState({}, '', '/')
})

describe('sub-tab seeding', () => {
  it('lands on the dashboard when the URL names nothing', () => {
    renderAt('')
    expect(screen.getByTestId('jump')).toBeTruthy()
  })

  it('lands on the workspace when the URL names it', () => {
    renderAt('?tab=evidence&view=workspace')
    expect(screen.getByTestId('workspace-pane')).toBeTruthy()
  })

  // The point of seeding in the initialiser rather than an effect: the pane the
  // link asked for is the first thing rendered, not the second.
  it('lands on the workspace for a bare item, with no view named', () => {
    renderAt('?tab=evidence&item=E-HRS-16')
    expect(screen.getByTestId('workspace-pane')).toBeTruthy()
  })

  it('ignores a view naming something that does not exist', () => {
    renderAt('?tab=evidence&view=nonsense')
    expect(screen.getByTestId('jump')).toBeTruthy()
  })
})

describe('sub-tab writing', () => {
  it('records a switch to the workspace without adding a history entry', () => {
    renderAt('?tab=evidence')
    fireEvent.click(screen.getByText('Workspace'))
    expect(search().get('view')).toBe('workspace')
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('drops the item when returning to the dashboard', () => {
    renderAt('?tab=evidence&view=workspace&item=E-HRS-16')
    fireEvent.click(screen.getByText('Dashboard'))
    expect(search().get('view')).toBe('dashboard')
    // A selection left in the URL would reopen on the next reload, on a
    // sub-screen that has no selection.
    expect(search().get('item')).toBeNull()
  })

  it('keeps the tab parameter it did not write', () => {
    renderAt('?tab=evidence&invite_type=org')
    fireEvent.click(screen.getByText('Workspace'))
    expect(search().get('tab')).toBe('evidence')
    expect(search().get('invite_type')).toBe('org')
  })
})

describe('navigating to an item from the dashboard', () => {
  it('opens the workspace at that item', () => {
    renderAt('?tab=evidence')
    fireEvent.click(screen.getByTestId('jump'))
    expect(screen.getByTestId('workspace-pane')).toBeTruthy()
    expect(search().get('view')).toBe('workspace')
    expect(search().get('item')).toBe('E-HRS-16')
  })

  // Arriving at an item is somewhere the user can go Back from. Switching
  // sub-tab is not — that distinction is the whole history model.
  it('adds a history entry', () => {
    renderAt('?tab=evidence')
    fireEvent.click(screen.getByTestId('jump'))
    expect(pushSpy).toHaveBeenCalledTimes(1)
  })
})

describe('Back and Forward', () => {
  it('restores the sub-tab the URL names', () => {
    renderAt('?tab=evidence&view=workspace')
    expect(screen.getByTestId('workspace-pane')).toBeTruthy()

    window.history.replaceState({}, '', '/?tab=evidence&view=dashboard')
    act(() => {
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    expect(screen.getByTestId('jump')).toBeTruthy()
  })
})
