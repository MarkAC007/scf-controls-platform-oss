/**
 * Acting on many evidence items at once (#789).
 *
 * The list is where the same three answers — tracked, how often, which team — are
 * given eighty times in a row on a first run. What is pinned here is that the
 * bar states what it will act on, that each control fires exactly one narrow
 * command, and that the outcome (including the part that failed) is said out
 * loud rather than inferred from the list refreshing.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, cleanup, fireEvent } from '@testing-library/react'
import { EvidenceBulkActionsBar } from '../EvidenceBulkActionsBar'
import type { EvidenceBulkActionsBarProps } from '../EvidenceBulkActionsBar'

afterEach(cleanup)

// Team ids from the team system — the legacy per-user assign list is sunset.
const teamOptions = [
  { value: 'team-1', label: 'Security Operations' },
  { value: 'team-2', label: 'GRC' },
]

const handlers = () => ({
  onSelectAllVisible: vi.fn(),
  onClear: vi.fn(),
  onSetTracked: vi.fn(),
  onSetFrequency: vi.fn(),
  onAssignTeam: vi.fn(),
  onDismissResult: vi.fn(),
})

function renderBar(overrides: Partial<EvidenceBulkActionsBarProps> = {}) {
  const h = handlers()
  const props: EvidenceBulkActionsBarProps = {
    selectedCount: 3,
    visibleCount: 12,
    allVisibleSelected: false,
    teamOptions,
    ...h,
    ...overrides,
  }
  render(<EvidenceBulkActionsBar {...props} />)
  return h
}

describe('what the bar says it will act on', () => {
  it('states the selected count once anything is selected', () => {
    renderBar({ selectedCount: 3 })
    expect(screen.getByText('3 selected')).toBeInTheDocument()
  })

  it('states what is shown when nothing is selected', () => {
    renderBar({ selectedCount: 0 })
    expect(screen.getByText('12 items shown')).toBeInTheDocument()
  })

  it('singularises a single shown item', () => {
    renderBar({ selectedCount: 0, visibleCount: 1 })
    expect(screen.getByText('1 item shown')).toBeInTheDocument()
  })

  it('offers no actions until something is selected', () => {
    renderBar({ selectedCount: 0 })
    expect(screen.queryByRole('button', { name: /start tracking/i })).not.toBeInTheDocument()
  })

  it('selects everything currently visible, not everything that exists', () => {
    // The filters above this bar are the user's statement of scope. Acting
    // beyond them would edit rows they cannot see.
    const h = renderBar({ selectedCount: 0 })
    fireEvent.click(screen.getByRole('button', { name: /select all 12 shown/i }))
    expect(h.onSelectAllVisible).toHaveBeenCalledTimes(1)
  })

  it('turns the same control into Clear once everything visible is selected', () => {
    const h = renderBar({ selectedCount: 12, allVisibleSelected: true })
    fireEvent.click(screen.getByRole('button', { name: /clear selection/i }))
    expect(h.onClear).toHaveBeenCalledTimes(1)
    expect(h.onSelectAllVisible).not.toHaveBeenCalled()
  })

  it('cannot select all when nothing is shown', () => {
    renderBar({ selectedCount: 0, visibleCount: 0 })
    expect(screen.getByRole('button', { name: /select all 0 shown/i })).toBeDisabled()
  })
})

describe('each control fires one narrow command', () => {
  it('starts tracking', () => {
    const h = renderBar()
    fireEvent.click(screen.getByRole('button', { name: /start tracking/i }))
    expect(h.onSetTracked).toHaveBeenCalledWith(true)
  })

  it('stops tracking', () => {
    const h = renderBar()
    fireEvent.click(screen.getByRole('button', { name: /stop tracking/i }))
    expect(h.onSetTracked).toHaveBeenCalledWith(false)
  })

  it('sets a frequency using the shared vocabulary value, not its label', () => {
    const h = renderBar()
    fireEvent.change(screen.getByLabelText(/set frequency/i), { target: { value: 'quarterly' } })
    expect(h.onSetFrequency).toHaveBeenCalledWith('quarterly')
  })

  it('does not fire when the frequency select returns to its placeholder', () => {
    const h = renderBar()
    fireEvent.change(screen.getByLabelText(/set frequency/i), { target: { value: '' } })
    expect(h.onSetFrequency).not.toHaveBeenCalled()
  })

  it('resets the frequency select, because it is a command and not a value', () => {
    // Leaving "Quarterly" showing would read as the current state of a
    // selection whose members may each differ.
    renderBar()
    const select = screen.getByLabelText(/set frequency/i) as HTMLSelectElement
    fireEvent.change(select, { target: { value: 'quarterly' } })
    expect(select.value).toBe('')
  })

  it('assigns an owner team by TEAM ID, not a label', () => {
    const h = renderBar()
    fireEvent.change(screen.getByLabelText(/assign owner team/i), { target: { value: 'team-1' } })
    expect(h.onAssignTeam).toHaveBeenCalledWith('team-1')
  })

  it('lists the org teams by name', () => {
    renderBar()
    expect(screen.getByRole('option', { name: 'Security Operations' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'GRC' })).toBeInTheDocument()
  })

  it('hides the assign-owner control entirely for non-admins (teamOptions null)', () => {
    renderBar({ teamOptions: null })
    expect(screen.queryByLabelText(/assign owner team/i)).toBeNull()
  })

  it('renders disabled with a create-teams hint when the org has no teams yet', () => {
    renderBar({ teamOptions: [] })
    expect(screen.getByLabelText(/assign owner team/i)).toBeDisabled()
    expect(screen.getByText('No teams yet')).toBeInTheDocument()
  })

  it('disables every action while a batch is in flight', () => {
    renderBar({ busy: true })
    expect(screen.getByRole('button', { name: /start tracking/i })).toBeDisabled()
    expect(screen.getByLabelText(/set frequency/i)).toBeDisabled()
    expect(screen.getByLabelText(/assign owner team/i)).toBeDisabled()
    expect(screen.getByText(/applying/i)).toBeInTheDocument()
  })
})

describe('the outcome is stated, including the part that failed', () => {
  it('counts what landed', () => {
    renderBar({ result: { updated: 9, created: 3, failed: 0, errors: [] } })
    expect(screen.getByRole('status')).toHaveTextContent('12 items updated')
  })

  it('singularises a single change', () => {
    renderBar({ result: { updated: 1, created: 0, failed: 0, errors: [] } })
    expect(screen.getByRole('status')).toHaveTextContent('1 item updated')
  })

  it('names the failures rather than reporting only the successes', () => {
    // Partial success is the normal case for this endpoint. A bar that showed
    // "37 updated" and swallowed the other three would be the same silent
    // inertness this epic keeps finding, one level up.
    renderBar({
      result: {
        updated: 37,
        created: 0,
        failed: 3,
        errors: ['E-IAM-04: Invalid system_id', 'E-IAM-09: not found'],
      },
    })
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('3 failed')
    expect(status).toHaveTextContent('E-IAM-04: Invalid system_id')
    expect(status).toHaveTextContent('E-IAM-09: not found')
  })

  it('marks a failing result so it is not only a colour', () => {
    const { container } = render(
      <EvidenceBulkActionsBar
        selectedCount={3}
        visibleCount={12}
        allVisibleSelected={false}
        teamOptions={teamOptions}
        result={{ updated: 0, created: 0, failed: 3, errors: ['nope'] }}
        {...handlers()}
      />,
    )
    expect(container.querySelector('.evidence-bulk-result.has-failures')).not.toBeNull()
  })

  it('can be dismissed', () => {
    const h = renderBar({ result: { updated: 1, created: 0, failed: 0, errors: [] } })
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }))
    expect(h.onDismissResult).toHaveBeenCalledTimes(1)
  })

  it('says nothing at all before a batch has run', () => {
    renderBar({ result: null })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })
})

describe('how the list wires it up', () => {
  const sources = import.meta.glob('../../*.tsx', {
    query: '?raw',
    import: 'default',
    eager: true,
  }) as Record<string, string>

  function evidenceReview(): string {
    const key = Object.keys(sources).find(k => k.endsWith('EvidenceReview.tsx'))
    if (!key) {
      throw new Error(`EvidenceReview.tsx not loaded — glob matched ${Object.keys(sources).length}`)
    }
    return sources[key]
  }

  it('loaded the fixture it is asserting on', () => {
    expect(evidenceReview()).toContain('EvidenceBulkActionsBar')
  })

  it('issues one batch request rather than one request per item', () => {
    const text = evidenceReview()
    expect(text).toContain('batchUpdateEvidenceTracking(')
    // The failure this guards against is a `for (const id of ids)` loop around
    // the single-row endpoint: N round trips, N task-generation passes, and no
    // coherent answer to "what happened?".
    expect(text).toMatch(/ids\.map\(evidence_id => \(\{ evidence_id, \.\.\.patch \}\)\)/)
  })

  it('sends only the field being changed', () => {
    // `exclude_unset` on the API means an omitted key is left alone, so a bulk
    // frequency change must not also blank forty rows' other fields.
    const text = evidenceReview()
    for (const call of ['applyBulk({ is_tracked:', 'applyBulk({ frequency }']) {
      expect(text).toContain(call)
    }
  })

  it('assigns the owner team through the batch team endpoint, never a tracking patch', () => {
    // Ownership is the accountable team in the team system — the sunset legacy
    // per-user assign must not creep back in as a tracking-field write.
    const text = evidenceReview()
    expect(text).toContain('onAssignTeam={assignOwnerTeamBulk}')
    expect(text).toContain('batchAssignTeamToItems')
    expect(text).not.toContain('applyBulk({ assigned_user_id:')
  })

  it('keeps the checkbox outside the row that owns the button role', () => {
    // A checkbox nested inside role="button" is invalid and unreachable — the
    // outer role swallows it.
    const text = evidenceReview()
    const block = text.slice(text.indexOf('evidence-card-select-row'))
    const checkbox = block.indexOf('evidence-card-select"')
    const card = block.indexOf('evidence-card-modern')
    expect(checkbox).toBeGreaterThan(-1)
    expect(checkbox).toBeLessThan(card)
  })

  it('keeps bulk selection separate from the item being read', () => {
    // Collapsing them would make opening an item to check something silently
    // change what the next bulk action hits.
    const text = evidenceReview()
    expect(text).toContain('const [bulkSelection, setBulkSelection]')
    expect(text).toContain('const [selectedEvidenceId, setSelectedEvidenceId]')
  })
})
