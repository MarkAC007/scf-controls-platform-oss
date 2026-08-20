/**
 * Sidebar platform gating.
 *
 * The Platform section (Catalog / Tenants) must be invisible to everyone but
 * platform admins — for a normal org user the nav offers no way to reach the
 * platform pages at all. The org-visible Catalog Changelog entry is not
 * admin-gated.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import Sidebar from '../Sidebar'

describe('Sidebar platform gating', () => {
  it('hides the Platform section for non-platform-admins', () => {
    render(<Sidebar activeTab="dashboard" onTabChange={vi.fn()} />)

    expect(screen.queryByText('Platform')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Catalog' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Tenants' })).not.toBeInTheDocument()
  })

  it('hides the Platform section when isPlatformAdmin is explicitly false', () => {
    render(<Sidebar activeTab="dashboard" onTabChange={vi.fn()} isPlatformAdmin={false} />)

    expect(screen.queryByText('Platform')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Catalog' })).not.toBeInTheDocument()
  })

  it('shows Catalog and Tenants to platform admins and routes to the platform tabs', () => {
    const onTabChange = vi.fn()
    render(<Sidebar activeTab="dashboard" onTabChange={onTabChange} isPlatformAdmin />)

    expect(screen.getByText('Platform')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Catalog' }))
    expect(onTabChange).toHaveBeenCalledWith('platform-catalog')

    fireEvent.click(screen.getByRole('button', { name: 'Tenants' }))
    expect(onTabChange).toHaveBeenCalledWith('platform-tenants')
  })

  it('shows the org-visible Catalog Changelog entry regardless of admin status', () => {
    const onTabChange = vi.fn()
    render(<Sidebar activeTab="dashboard" onTabChange={onTabChange} />)

    fireEvent.click(screen.getByRole('button', { name: 'Catalog Changelog' }))
    expect(onTabChange).toHaveBeenCalledWith('catalog-changelog')
  })
})
