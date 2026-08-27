/**
 * FilterRadio: radio-group primitive for explorer filter sidebars.
 * Renders a radiogroup with labelled options; only one option active at a time.
 */
import { describe, it, expect, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import FilterRadio from '../FilterRadio'

const OPTIONS = [
  { value: 'all', label: 'All' },
  { value: 'not_started', label: 'Not started' },
  { value: 'in_progress', label: 'In progress' },
  { value: 'completed', label: 'Completed' },
]

describe('FilterRadio', () => {
  it('renders a radiogroup with the given aria-label', () => {
    render(
      <FilterRadio
        label="STATUS"
        options={OPTIONS}
        value="all"
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('radiogroup', { name: 'STATUS' })).toBeInTheDocument()
  })

  it('renders one radio per option', () => {
    render(
      <FilterRadio
        label="STATUS"
        options={OPTIONS}
        value="all"
        onChange={vi.fn()}
      />,
    )
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(OPTIONS.length)
  })

  it('marks the current value as checked', () => {
    render(
      <FilterRadio
        label="STATUS"
        options={OPTIONS}
        value="in_progress"
        onChange={vi.fn()}
      />,
    )
    expect(screen.getByRole('radio', { name: 'In progress' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'All' })).not.toBeChecked()
  })

  it('calls onChange with the new value when a radio is clicked', async () => {
    const onChange = vi.fn()
    render(
      <FilterRadio
        label="STATUS"
        options={OPTIONS}
        value="all"
        onChange={onChange}
      />,
    )
    await userEvent.click(screen.getByRole('radio', { name: 'Not started' }))
    expect(onChange).toHaveBeenCalledWith('not_started')
  })

  it('uses the provided name for the radio group', () => {
    render(
      <FilterRadio
        label="STATUS"
        options={OPTIONS}
        value="all"
        onChange={vi.fn()}
        name="status-filter"
      />,
    )
    const radios = screen.getAllByRole('radio') as HTMLInputElement[]
    expect(radios.every(r => r.name === 'status-filter')).toBe(true)
  })

  it('falls back to a generated name when name prop is omitted', () => {
    render(
      <FilterRadio
        label="TASK TYPE"
        options={OPTIONS}
        value="all"
        onChange={vi.fn()}
      />,
    )
    const radios = screen.getAllByRole('radio') as HTMLInputElement[]
    // All radios share the same name (so they form a group)
    const names = new Set(radios.map(r => r.name))
    expect(names.size).toBe(1)
    expect(radios[0].name.length).toBeGreaterThan(0)
  })
})
