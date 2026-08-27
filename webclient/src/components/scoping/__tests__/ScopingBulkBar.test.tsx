/**
 * ScopingBulkBar.test.tsx — TDD tests for the scoping bulk-action bar.
 *
 * Mirrors EvidenceBulkActionsBar interaction idioms.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import ScopingBulkBar from '../ScopingBulkBar'
import type { ScopingBulkBarProps } from '../ScopingBulkBar'

const defaultOwnerOptions = [
  { value: 'GRC', label: 'GRC' },
  { value: 'SecOps', label: 'Security Operations' },
]

function defaultProps(overrides: Partial<ScopingBulkBarProps> = {}): ScopingBulkBarProps {
  return {
    selectedCount: 3,
    visibleCount: 10,
    allVisibleSelected: false,
    ownerOptions: defaultOwnerOptions,
    busy: false,
    progressText: undefined,
    onSelectAllVisible: vi.fn(),
    onSetApplicable: vi.fn(),
    onSetNA: vi.fn(),
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

  describe('action buttons', () => {
    it('renders "Set applicable" button', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.getByRole('button', { name: /set applicable/i })).toBeInTheDocument()
    })

    it('calls onSetApplicable when "Set applicable" is clicked', () => {
      const onSetApplicable = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onSetApplicable })} />)
      fireEvent.click(screen.getByRole('button', { name: /set applicable/i }))
      expect(onSetApplicable).toHaveBeenCalledTimes(1)
    })

    it('renders "Set N/A" button', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.getByRole('button', { name: /set n\/a/i })).toBeInTheDocument()
    })

    it('calls onSetNA when "Set N/A" is clicked', () => {
      const onSetNA = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onSetNA })} />)
      fireEvent.click(screen.getByRole('button', { name: /set n\/a/i }))
      expect(onSetNA).toHaveBeenCalledTimes(1)
    })

    it('renders the "Assign owner" select with owner options', () => {
      render(<ScopingBulkBar {...defaultProps()} />)
      expect(screen.getByRole('combobox', { name: /assign owner/i })).toBeInTheDocument()
    })

    it('calls onAssignOwner with selected owner value', () => {
      const onAssignOwner = vi.fn()
      render(<ScopingBulkBar {...defaultProps({ onAssignOwner })} />)
      const select = screen.getByRole('combobox', { name: /assign owner/i })
      fireEvent.change(select, { target: { value: 'GRC' } })
      expect(onAssignOwner).toHaveBeenCalledWith('GRC')
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
    it('disables "Set applicable" when busy=true', () => {
      render(<ScopingBulkBar {...defaultProps({ busy: true })} />)
      expect(screen.getByRole('button', { name: /set applicable/i })).toBeDisabled()
    })

    it('disables "Set N/A" when busy=true', () => {
      render(<ScopingBulkBar {...defaultProps({ busy: true })} />)
      expect(screen.getByRole('button', { name: /set n\/a/i })).toBeDisabled()
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
