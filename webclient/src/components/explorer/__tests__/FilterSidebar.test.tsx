import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import FilterSidebar, { FilterCheckbox, FilterGroup, FilterSelect } from '../FilterSidebar'

describe('FilterSidebar', () => {
  it('renders children when not collapsed', () => {
    render(
      <FilterSidebar collapsed={false} onToggleCollapsed={vi.fn()}>
        <span>Filter content</span>
      </FilterSidebar>,
    )
    expect(screen.getByText('Filter content')).toBeInTheDocument()
  })

  it('does not render children when collapsed', () => {
    render(
      <FilterSidebar collapsed={true} onToggleCollapsed={vi.fn()}>
        <span>Filter content</span>
      </FilterSidebar>,
    )
    expect(screen.queryByText('Filter content')).not.toBeInTheDocument()
  })

  it('toggle button fires onToggleCollapsed on click', () => {
    const onToggle = vi.fn()
    render(
      <FilterSidebar collapsed={false} onToggleCollapsed={onToggle}>
        <span>content</span>
      </FilterSidebar>,
    )
    fireEvent.click(screen.getByRole('button', { name: /collapse filters/i }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('toggle button fires onToggleCollapsed when collapsed', () => {
    const onToggle = vi.fn()
    render(
      <FilterSidebar collapsed={true} onToggleCollapsed={onToggle}>
        <span>content</span>
      </FilterSidebar>,
    )
    fireEvent.click(screen.getByRole('button', { name: /expand filters/i }))
    expect(onToggle).toHaveBeenCalledTimes(1)
  })

  it('toggle button has aria-expanded=true when not collapsed', () => {
    render(
      <FilterSidebar collapsed={false} onToggleCollapsed={vi.fn()}>
        <span>content</span>
      </FilterSidebar>,
    )
    const btn = screen.getByRole('button', { name: /collapse filters/i })
    expect(btn).toHaveAttribute('aria-expanded', 'true')
  })

  it('toggle button has aria-expanded=false when collapsed', () => {
    render(
      <FilterSidebar collapsed={true} onToggleCollapsed={vi.fn()}>
        <span>content</span>
      </FilterSidebar>,
    )
    const btn = screen.getByRole('button', { name: /expand filters/i })
    expect(btn).toHaveAttribute('aria-expanded', 'false')
  })

  it('uses supplied aria-label', () => {
    const { container } = render(
      <FilterSidebar
        collapsed={false}
        onToggleCollapsed={vi.fn()}
        aria-label="Custom filters label"
      >
        <span>content</span>
      </FilterSidebar>,
    )
    const sidebar = container.querySelector('.explorer-filters')
    expect(sidebar).toHaveAttribute('aria-label', 'Custom filters label')
  })

  it('defaults aria-label to "Filters"', () => {
    const { container } = render(
      <FilterSidebar collapsed={false} onToggleCollapsed={vi.fn()}>
        <span>content</span>
      </FilterSidebar>,
    )
    const sidebar = container.querySelector('.explorer-filters')
    expect(sidebar).toHaveAttribute('aria-label', 'Filters')
  })

  it('collapsed sidebar has explorer-filters--collapsed class', () => {
    const { container } = render(
      <FilterSidebar collapsed={true} onToggleCollapsed={vi.fn()}>
        <span>content</span>
      </FilterSidebar>,
    )
    expect(container.querySelector('.explorer-filters--collapsed')).toBeInTheDocument()
  })
})

describe('FilterGroup', () => {
  it('renders label and children', () => {
    render(
      <FilterGroup label="SCOPE">
        <span>group content</span>
      </FilterGroup>,
    )
    expect(screen.getByText('SCOPE')).toBeInTheDocument()
    expect(screen.getByText('group content')).toBeInTheDocument()
  })

  it('has explorer-filter-group class', () => {
    const { container } = render(
      <FilterGroup label="DOMAIN">
        <span>child</span>
      </FilterGroup>,
    )
    expect(container.querySelector('.explorer-filter-group')).toBeInTheDocument()
  })

  it('label has explorer-filter-label class', () => {
    const { container } = render(
      <FilterGroup label="DOMAIN">
        <span>child</span>
      </FilterGroup>,
    )
    expect(container.querySelector('.explorer-filter-label')).toBeInTheDocument()
  })
})

describe('FilterCheckbox', () => {
  it('renders the label text', () => {
    render(
      <FilterCheckbox label="In scope" checked={false} onChange={vi.fn()} />,
    )
    expect(screen.getByText('In scope')).toBeInTheDocument()
  })

  it('renders optional count', () => {
    render(
      <FilterCheckbox label="In scope" checked={false} onChange={vi.fn()} count={346} />,
    )
    expect(screen.getByText('346')).toBeInTheDocument()
  })

  it('checkbox reflects checked state', () => {
    render(
      <FilterCheckbox label="In scope" checked={true} onChange={vi.fn()} />,
    )
    const input = screen.getByRole('checkbox')
    expect(input).toBeChecked()
  })

  it('checkbox reflects unchecked state', () => {
    render(
      <FilterCheckbox label="In scope" checked={false} onChange={vi.fn()} />,
    )
    const input = screen.getByRole('checkbox')
    expect(input).not.toBeChecked()
  })

  it('onChange called with true when checking', () => {
    const onChange = vi.fn()
    render(
      <FilterCheckbox label="In scope" checked={false} onChange={onChange} />,
    )
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(true)
  })

  it('onChange called with false when unchecking', () => {
    const onChange = vi.fn()
    render(
      <FilterCheckbox label="In scope" checked={true} onChange={onChange} />,
    )
    fireEvent.click(screen.getByRole('checkbox'))
    expect(onChange).toHaveBeenCalledWith(false)
  })

  it('has explorer-filter-check class', () => {
    const { container } = render(
      <FilterCheckbox label="In scope" checked={false} onChange={vi.fn()} />,
    )
    expect(container.querySelector('.explorer-filter-check')).toBeInTheDocument()
  })
})

describe('FilterSelect', () => {
  const options = [
    { value: '', label: 'All domains' },
    { value: 'gov', label: 'Governance' },
    { value: 'ast', label: 'Asset Management' },
  ]

  it('renders all options', () => {
    render(
      <FilterSelect value="" onChange={vi.fn()} options={options} />,
    )
    expect(screen.getByRole('combobox')).toBeInTheDocument()
    expect(screen.getByText('All domains')).toBeInTheDocument()
    expect(screen.getByText('Governance')).toBeInTheDocument()
  })

  it('reflects the current value', () => {
    render(
      <FilterSelect value="gov" onChange={vi.fn()} options={options} />,
    )
    const select = screen.getByRole('combobox')
    expect(select).toHaveValue('gov')
  })

  it('onChange called with the selected value', () => {
    const onChange = vi.fn()
    render(
      <FilterSelect value="" onChange={onChange} options={options} />,
    )
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'ast' } })
    expect(onChange).toHaveBeenCalledWith('ast')
  })

  it('renders optional label', () => {
    render(
      <FilterSelect label="DOMAIN" value="" onChange={vi.fn()} options={options} />,
    )
    expect(screen.getByText('DOMAIN')).toBeInTheDocument()
  })
})
