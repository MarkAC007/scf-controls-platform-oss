/**
 * DeprecatedBadge: renders only for deprecated rows, carries the retirement
 * hints, and getCatalogLifecycle survives rows without the badge fields
 * (old payloads / endpoints not yet swept).
 */
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import DeprecatedBadge, { getCatalogLifecycle, isDeprecated } from '../DeprecatedBadge'

// Fixture ids deliberately avoid the real SCF `XXX-NN` id shape.
describe('DeprecatedBadge', () => {
  it('renders nothing for active rows', () => {
    const { container } = render(<DeprecatedBadge catalog_status="active" />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders nothing when catalog_status is absent (old payloads)', () => {
    const { container } = render(<DeprecatedBadge />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders for deprecated rows with retirement hints in the tooltip', () => {
    render(
      <DeprecatedBadge
        catalog_status="deprecated"
        retired_in_version="2026.2"
        superseded_by="ctrl-successor"
      />
    )
    const badge = screen.getByText(/Deprecated/)
    expect(badge).toHaveAttribute(
      'title',
      'Retired in catalog 2026.2 · Superseded by ctrl-successor'
    )
    expect(badge.textContent).toContain('ctrl-successor')
  })

  it('renders for deprecated rows without a successor', () => {
    render(<DeprecatedBadge catalog_status="deprecated" />)
    const badge = screen.getByText('Deprecated')
    expect(badge).toHaveAttribute('title', 'This control has been retired from the SCF catalog')
  })
})

describe('getCatalogLifecycle', () => {
  it('extracts the badge fields from a row', () => {
    const lifecycle = getCatalogLifecycle({
      scf_id: 'ctrl-legacy',
      catalog_status: 'deprecated',
      retired_in_version: '2026.2',
      superseded_by: 'ctrl-successor',
    })
    expect(lifecycle).toEqual({
      catalog_status: 'deprecated',
      retired_in_version: '2026.2',
      superseded_by: 'ctrl-successor',
    })
    expect(isDeprecated(lifecycle)).toBe(true)
  })

  it('yields an empty lifecycle for rows without the fields', () => {
    expect(getCatalogLifecycle({ scf_id: 'ctrl-legacy' })).toEqual({
      catalog_status: undefined,
      retired_in_version: undefined,
      superseded_by: undefined,
    })
    expect(getCatalogLifecycle(null)).toEqual({})
    expect(getCatalogLifecycle('nope')).toEqual({})
    expect(isDeprecated(getCatalogLifecycle(null))).toBe(false)
  })
})
