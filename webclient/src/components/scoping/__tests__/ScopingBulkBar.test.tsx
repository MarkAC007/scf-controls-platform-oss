/**
 * ScopingBulkBar.test.tsx — TDD tests for the scoping bulk-action bar.
 *
 * Mirrors EvidenceBulkActionsBar interaction idioms.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import ScopingBulkBar from '../ScopingBulkBar'
import type { ScopingBulkBarProps } from '../ScopingBulkBar'

// Team ids from the team system — the legacy owner-label list is sunset.
const defaultTeamOptions = [
  { value: 'team-1', label: 'GRC' },
  { value: 'team-2', label: 'Security Operations' },
]

function defaultProps(overrides: Partial<ScopingBulkBarProps> = {}): ScopingBulkBarProps {
  return {
    selectedCount: 3,
    visibleCount: 10,
    allVisibleSelected: false,
    teamOptions: defaultTeamOptions,
    busy: false,
    progressText: undefined,
    onSelectAllVisible: vi.fn(),
    onSetMaturity: vi.fn(),
    onSetStatus: vi.fn(),
    onAssignOwner: vi.fn(),
    onClear: vi.fn(),
    ...overrides,
  }
}

describe('ScopingBulkBar', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  describe('selection count display', () => {
    it('shows selected count when selection is non-empty', () => {
      render(<ScopingBulkBar {...defaultProps({ selectedCount: 3 })} />)
      expect(screen.getByText(/3 selected/i)).toBeInTheDocument()
    })

    it('shows 0 selected when nothing is selected', () => {
      render(<ScopingBulkBar {...defaultProps({ selectedCount: 0 })} />)
      expect(screen.getByText(/0 selected/i)).toBeInTheDocument()
    })
  })

  describe('select-all-visible button', () => {
    it('shows "Select all N shown" when not all visible are selected', () => {
      render(<ScopingBulkBar {...defaultProps({ visibleCount: 10, allVisibleSelected: false })} />)
      expect(screen.getByRole('button', { name: /select all 10 shown/i })).toBeInTheDocument()
    })

    it('calls onSelectAllVisible when "Select all N shown" is clicked', () => {
      const onSelectAllVisible = vi.fn()
      render(
        <ScopingBulkBar
          {...defaultProps({ visibleCount: 5, allVisibleSelected: false, onSelectAllVisible })}
        />,
      )
      fireEvent.click(screen.getByRole('button', { name: /select all 5 shown/i }))
      expect(onSelectAllVisible).toHaveBeenCalledTimes(1)
    })

    it('shows "Clear selection" when all visible are selected', () => {
      render(<ScopingBulkBar {...defaultProps({ allVisibleSelected: true })} />)
      expect(screen.getByRole('button', { name: /clear selection/i })).toBeInTheDocument()
    })

    it('calls onClear when "Clear selection" is clicked (all-selected state)', () => {
      const onClear = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ allVisibleSelected: true, onClear })} />)
      fireEvent.click(screen.getByRole('button', { name: /clear selection/i }))
      expect(onClear).toHaveBeenCalledTimes(1)
    })
  })

  describe('bulk scoping is gone', () => {
    // The removed buttons wrote `selected` — bulk SCOPING, not status. Bulk
    // descoping of an arbitrary row selection was withdrawn deliberately, so
    // assert their absence rather than trusting the props type alone.
    it('no longer renders "Set applicable"', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.queryByRole('button', { name: /set applicable/i })).toBeNull()
    })

    it('no longer renders "Set N/A"', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.queryByRole('button', { name: /set n\/a/i })).toBeNull()
    })
  })

  describe('set maturity', () => {
    it('renders the "Set maturity" select with L0 through L5', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      const select = screen.getByRole('combobox', { name: /set maturity level/i })
      expect(select).toBeInTheDocument()
      for (const level of ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']) {
        expect(screen.getByRole('option', { name: new RegExp(`^${level} - `) })).toBeInTheDocument()
      }
    })

    it('calls onSetMaturity with the chosen level', () => {
      const onSetMaturity = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onSetMaturity })} />)
      const select = screen.getByRole('combobox', { name: /set maturity level/i })
      fireEvent.change(select, { target: { value: 'L3' } })
      expect(onSetMaturity).toHaveBeenCalledTimes(1)
      expect(onSetMaturity).toHaveBeenCalledWith('L3')
    })

    it('resets to the placeholder after firing (it is a command, not a bound value)', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      const select = screen.getByRole('combobox', { name: /set maturity level/i })
      fireEvent.change(select, { target: { value: 'L3' } })
      expect((select as HTMLSelectElement).value).toBe('')
    })

    it('does not fire on the placeholder option', () => {
      const onSetMaturity = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onSetMaturity })} />)
      const select = screen.getByRole('combobox', { name: /set maturity level/i })
      fireEvent.change(select, { target: { value: '' } })
      expect(onSetMaturity).not.toHaveBeenCalled()
    })
  })

  describe('set status', () => {
    it('renders the "Set status" select with all eight API values', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      const select = screen.getByRole('combobox', { name: /set implementation status/i })
      expect(select).toBeInTheDocument()
      // Eight statuses + the placeholder option.
      expect(select.querySelectorAll('option')).toHaveLength(9)
    })

    it('offers "Not applicable" under its plain API label', () => {
      // implementation_status = not_applicable is NOT the removed "Set N/A",
      // which wrote `selected = false` and took the control out of scope.
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.getByRole('option', { name: 'Not applicable' })).toBeInTheDocument()
    })

    it('calls onSetStatus with the chosen status', () => {
      const onSetStatus = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onSetStatus })} />)
      const select = screen.getByRole('combobox', { name: /set implementation status/i })
      fireEvent.change(select, { target: { value: 'ready_for_review' } })
      expect(onSetStatus).toHaveBeenCalledTimes(1)
      expect(onSetStatus).toHaveBeenCalledWith('ready_for_review')
    })

    it('resets to the placeholder after firing', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      const select = screen.getByRole('combobox', { name: /set implementation status/i })
      fireEvent.change(select, { target: { value: 'implemented' } })
      expect((select as HTMLSelectElement).value).toBe('')
    })

    it('does not fire on the placeholder option', () => {
      const onSetStatus = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onSetStatus })} />)
      const select = screen.getByRole('combobox', { name: /set implementation status/i })
      fireEvent.change(select, { target: { value: '' } })
      expect(onSetStatus).not.toHaveBeenCalled()
    })
  })

  describe('action buttons', () => {
    it('renders the "Assign owner" select with the org teams', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.getByRole('combobox', { name: /assign owner/i })).toBeInTheDocument()
    })

    it('calls onAssignOwner with the selected TEAM ID, not a label', () => {
      const onAssignOwner = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onAssignOwner })} />)
      const select = screen.getByRole('combobox', { name: /assign owner/i })
      fireEvent.change(select, { target: { value: 'team-1' } })
      expect(onAssignOwner).toHaveBeenCalledWith('team-1')
    })

    it('hides the assign-owner control entirely for non-admins (teamOptions null)', () => {
      render(<ScopingBulkBar {...defaultProps({ teamOptions: null })} />)
      expect(screen.queryByRole('combobox', { name: /assign owner/i })).toBeNull()
    })

    it('renders disabled with a create-teams hint when the org has no teams yet', () => {
      render(<ScopingBulkBar {...defaultProps({ teamOptions: [] })} />)
      const select = screen.getByRole('combobox', { name: /assign owner/i })
      expect(select).toBeDisabled()
      expect(screen.getByText('No teams yet')).toBeInTheDocument()
    })

    it('renders "Clear" button when selection is partial', () => {
      render(<ScopingBulkBar {...defaultProps({ selectedCount: 2, allVisibleSelected: false })} />)
      // The standalone clear button (not the "Clear selection" in the select-all row)
      const clearBtns = screen.getAllByRole('button', { name: /clear/i })
      expect(clearBtns.length).toBeGreaterThan(0)
    })

    it('calls onClear when "Clear" standalone button is clicked', () => {
      const onClear = vi.fn()
      render(
        <ScopingBulkBar
          {...defaultProps({ selectedCount: 2, allVisibleSelected: false, onClear })}
        />,
      )
      // Find the "Clear" button (distinct from "Select all N shown")
      const clearBtn = screen.getByRole('button', { name: /^clear$/i })
      fireEvent.click(clearBtn)
      expect(onClear).toHaveBeenCalledTimes(1)
    })
  })

  describe('busy state', () => {
    it('disables the "Set maturity" select when busy=true', () => {
      render(<ScopingBulkBar {...defaultProps({ busy: true })} />)
      expect(screen.getByRole('combobox', { name: /set maturity level/i })).toBeDisabled()
    })

    it('disables the "Set status" select when busy=true', () => {
      render(<ScopingBulkBar {...defaultProps({ busy: true })} />)
      expect(screen.getByRole('combobox', { name: /set implementation status/i })).toBeDisabled()
    })

    it('disables "Assign owner" select when busy=true', () => {
      render(<ScopingBulkBar {...defaultProps({ busy: true })} />)
      expect(screen.getByRole('combobox', { name: /assign owner/i })).toBeDisabled()
    })

    it('shows progressText when busy and progressText is provided', () => {
      render(
        <ScopingBulkBar
          {...defaultProps({ busy: true, progressText: 'Updating 3 of 12…' })}
        />,
      )
      expect(screen.getByText('Updating 3 of 12…')).toBeInTheDocument()
    })

    it('does not show progressText when not busy', () => {
      render(
        <ScopingBulkBar
          {...defaultProps({ busy: false, progressText: 'Updating 3 of 12…' })}
        />,
      )
      expect(screen.queryByText('Updating 3 of 12…')).not.toBeInTheDocument()
    })
  })
})
