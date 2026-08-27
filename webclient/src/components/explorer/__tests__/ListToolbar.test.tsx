import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ListToolbar from '../ListToolbar'

describe('ListToolbar', () => {
  it('renders the search placeholder', () => {
    render(
      <ListToolbar
        search=""
        onSearchChange={vi.fn()}
        searchPlaceholder="Search controls…"
      />,
    )

    expect(screen.getByPlaceholderText('Search controls…')).toBeInTheDocument()
  })

  it('search input has type="search"', () => {
    render(
      <ListToolbar
        search=""
        onSearchChange={vi.fn()}
        searchPlaceholder="Search controls…"
      />,
    )

    const input = screen.getByRole('searchbox')
    expect(input).toHaveAttribute('type', 'search')
  })

  it('search input has aria-label equal to searchPlaceholder', () => {
    render(
      <ListToolbar
        search=""
        onSearchChange={vi.fn()}
        searchPlaceholder="Search controls…"
      />,
    )

    expect(screen.getByRole('searchbox', { name: 'Search controls…' })).toBeInTheDocument()
  })

  it('typing calls onSearchChange with the new value', () => {
    const onSearchChange = vi.fn()
    render(
      <ListToolbar
        search=""
        onSearchChange={onSearchChange}
        searchPlaceholder="Search"
      />,
    )

    const input = screen.getByRole('searchbox')
    fireEvent.change(input, { target: { value: 'GOV-01' } })
    expect(onSearchChange).toHaveBeenCalledWith('GOV-01')
  })

  it('renders the count node when provided', () => {
    render(
      <ListToolbar
        search=""
        onSearchChange={vi.fn()}
        searchPlaceholder="Search"
        count={<>346 in scope</>}
      />,
    )

    expect(screen.getByText('346 in scope')).toBeInTheDocument()
  })

  it('renders the actions node when provided', () => {
    render(
      <ListToolbar
        search=""
        onSearchChange={vi.fn()}
        searchPlaceholder="Search"
        actions={<button>Export CSV</button>}
      />,
    )

    expect(screen.getByRole('button', { name: 'Export CSV' })).toBeInTheDocument()
  })

  it('does not render count or actions area when omitted', () => {
    const { container } = render(
      <ListToolbar
        search=""
        onSearchChange={vi.fn()}
        searchPlaceholder="Search"
      />,
    )

    // The toolbar element should exist
    expect(container.querySelector('.explorer-toolbar')).toBeInTheDocument()
    // but count and actions slots should not
    expect(container.querySelector('.explorer-toolbar-count')).not.toBeInTheDocument()
    expect(container.querySelector('.explorer-toolbar-actions')).not.toBeInTheDocument()
  })

  it('reflects the search prop value in the input', () => {
    render(
      <ListToolbar
        search="hello"
        onSearchChange={vi.fn()}
        searchPlaceholder="Search"
      />,
    )

    expect(screen.getByRole('searchbox')).toHaveValue('hello')
  })
})
