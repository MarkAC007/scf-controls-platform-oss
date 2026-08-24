/**
 * AccountableOwnerTypeFilter: reachable by clicking, and reports the value the
 * server expects (#822 phase 2, ISC-42).
 *
 * Driven with ``user-event`` rather than a synthetic change event, because the
 * criterion is that somebody can operate this control, not that a handler
 * exists. A synthetic ``fireEvent.change`` would pass against a ``<select>``
 * that was disabled, covered, or zero-sized.
 *
 * The values matter as much as the labels: the two non-sentinel options are
 * pushed straight to the API as ``?accountable_owner_type=``, which the
 * backend validates against ``organization_members.member_type``. A friendly
 * label with a value of ``'contractor'`` would render perfectly and 400 on
 * every request.
 */
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import AccountableOwnerTypeFilter, {
  ALL_OWNER_TYPES,
} from '../AccountableOwnerTypeFilter'

function renderFilter(value = ALL_OWNER_TYPES) {
  const onChange = vi.fn()
  render(<AccountableOwnerTypeFilter value={value} onChange={onChange} />)
  return { onChange, select: screen.getByLabelText('Filter by accountable owner type') }
}

describe('AccountableOwnerTypeFilter', () => {
  it('renders all three options', () => {
    renderFilter()
    expect(screen.getByRole('option', { name: 'All Owner Types' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Contractor-owned' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'Internally owned' })).toBeInTheDocument()
  })

  it('has an accessible name, so it is not an unlabelled select in a filter bar', () => {
    const { select } = renderFilter()
    expect(select.tagName).toBe('SELECT')
  })

  it('calls onChange with external_contractor when a user selects it', async () => {
    const user = userEvent.setup()
    const { onChange, select } = renderFilter()
    await user.selectOptions(select, 'external_contractor')
    expect(onChange).toHaveBeenCalledWith('external_contractor')
  })

  it('calls onChange with internal when a user selects it', async () => {
    const user = userEvent.setup()
    const { onChange, select } = renderFilter()
    await user.selectOptions(select, 'internal')
    expect(onChange).toHaveBeenCalledWith('internal')
  })

  it('returns to the sentinel when a user clears the filter', async () => {
    // The way out matters as much as the way in: a filter that cannot be
    // undone leaves the reader looking at a narrowed list they cannot widen.
    const user = userEvent.setup()
    const { onChange, select } = renderFilter('external_contractor')
    await user.selectOptions(select, ALL_OWNER_TYPES)
    expect(onChange).toHaveBeenCalledWith(ALL_OWNER_TYPES)
  })

  it('sends the values the API validates against, not the labels', () => {
    // 'Contractor-owned' is for the reader; 'external_contractor' is what the
    // endpoint checks against organization_members.member_type. Mixing them up
    // renders correctly and 400s on every request.
    renderFilter()
    expect(
      screen.getByRole<HTMLOptionElement>('option', { name: 'Contractor-owned' }).value
    ).toBe('external_contractor')
    expect(
      screen.getByRole<HTMLOptionElement>('option', { name: 'Internally owned' }).value
    ).toBe('internal')
  })

  it('shows the current value as selected', () => {
    const { select } = renderFilter('external_contractor')
    expect((select as HTMLSelectElement).value).toBe('external_contractor')
  })

  it('does not fire onChange merely by rendering', () => {
    // A controlled select that called back on mount would re-filter the list
    // on every render of the page around it.
    const { onChange } = renderFilter('internal')
    expect(onChange).not.toHaveBeenCalled()
  })
})
