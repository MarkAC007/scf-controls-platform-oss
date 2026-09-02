/**
 * Task 7: Footer restyle — behavioural parity + Explorer spec
 *
 * Covers:
 * - Version button renders and is clickable (on-demand /version check)
 * - Update badge renders in available state (amber/warning) and breaking state (red/danger)
 * - Credit link renders and points to compliancegenie.io
 * - Docs link renders with aria-label="Documentation"
 * - Footer renders the current version string
 */
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi, beforeEach } from 'vitest'

import Footer from '../Footer'

// ---- Mocks ----

// ResizeObserver not available in jsdom (Footer publishes --app-footer-height)
globalThis.ResizeObserver = vi.fn().mockImplementation(() => ({
  observe: vi.fn(),
  unobserve: vi.fn(),
  disconnect: vi.fn(),
}))

// Suppress toast side-effects in tests
vi.mock('react-hot-toast', () => ({
  default: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}))

// Silence the on-mount /version fetch by default; individual tests override
vi.mock('../../data/apiClient', () => ({
  apiClient: {
    get: vi.fn().mockResolvedValue(null),
  },
}))

import { apiClient } from '../../data/apiClient'
const mockGet = vi.mocked(apiClient.get)

beforeEach(() => {
  vi.clearAllMocks()
  // Default: /version returns null (no update info) — badge stays hidden
  mockGet.mockResolvedValue(null)
})

// ----------- Version button -----------

describe('Footer: version button', () => {
  it('renders a button labelled with the current version', async () => {
    render(<Footer />)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /v\d+\.\d+\.\d+/i })).toBeInTheDocument()
    )
  })

  it('has title="Check for updates"', async () => {
    render(<Footer />)
    await waitFor(() => {
      const btn = screen.getByRole('button', { name: /v\d/i })
      expect(btn).toHaveAttribute('title', 'Check for updates')
    })
  })

  it('calls /version when the version button is clicked', async () => {
    const user = userEvent.setup()
    render(<Footer />)
    const btn = await screen.findByRole('button', { name: /v\d/i })
    await user.click(btn)
    // on-mount call + the on-click call
    expect(mockGet).toHaveBeenCalledWith('/version')
  })
})

// ----------- Update badge — available state -----------

describe('Footer: update badge (available)', () => {
  it('renders the available badge when update_available is true', async () => {
    mockGet.mockResolvedValue({
      platform: { version: '0.23.0' },
      update: {
        update_available: true,
        latest_version: '0.24.0',
        breaking: false,
        skip_blocked: false,
        release_url: 'https://example.com/release',
      },
    })
    render(<Footer />)
    await waitFor(() =>
      expect(screen.getByText(/update available/i)).toBeInTheDocument()
    )
    const badge = screen.getByText(/update available/i).closest('a')
    expect(badge).toHaveAttribute('href', 'https://example.com/release')
    // available state uses success tokens — class check
    expect(badge).toHaveClass('footer-update-badge--available')
  })

  it('does NOT render the badge when update_available is false', async () => {
    mockGet.mockResolvedValue({
      platform: { version: '0.23.0' },
      update: { update_available: false, check_enabled: true },
    })
    render(<Footer />)
    // Give time for the fetch to settle
    await waitFor(() => expect(mockGet).toHaveBeenCalled())
    expect(screen.queryByText(/update available/i)).not.toBeInTheDocument()
  })
})

// ----------- Update badge — breaking state -----------

describe('Footer: update badge (breaking)', () => {
  it('renders with footer-update-badge--breaking class when breaking is true', async () => {
    mockGet.mockResolvedValue({
      platform: { version: '0.23.0' },
      update: {
        update_available: true,
        latest_version: '1.0.0',
        breaking: true,
        skip_blocked: false,
        release_url: 'https://example.com/release',
      },
    })
    render(<Footer />)
    await waitFor(() =>
      expect(screen.getByText(/update available/i)).toBeInTheDocument()
    )
    const badge = screen.getByText(/update available/i).closest('a')
    expect(badge).toHaveClass('footer-update-badge--breaking')
  })
})

// ----------- Credit link -----------

describe('Footer: credit link', () => {
  it('renders "Built and maintained by" text', async () => {
    render(<Footer />)
    expect(screen.getByText(/built and maintained by/i)).toBeInTheDocument()
  })

  it('renders compliancegenie.io link pointing to the website', async () => {
    render(<Footer />)
    const link = screen.getByRole('link', { name: /compliancegenie\.io/i })
    expect(link).toHaveAttribute('href', 'https://compliancegenie.io')
  })
})

// ----------- Docs link -----------

describe('Footer: docs link', () => {
  it('renders a docs link with aria-label="Documentation"', async () => {
    render(<Footer />)
    const link = screen.getByRole('link', { name: /documentation/i })
    expect(link).toBeInTheDocument()
    expect(link).toHaveAttribute('aria-label', 'Documentation')
  })

  it('docs link points to the documentation site', async () => {
    render(<Footer />)
    const link = screen.getByRole('link', { name: /documentation/i })
    expect(link).toHaveAttribute('href', expect.stringContaining('docs.'))
  })
})

describe('platform version 0.0.0 sentinel', () => {
  it('falls back to the build-time app version when the backend reports 0.0.0', async () => {
    mockGet.mockResolvedValue({ platform: { version: '0.0.0' }, update: { update_available: false } })
    render(<Footer />)
    const btn = await screen.findByRole('button', { name: /v\d+\.\d+\.\d+/i })
    expect(btn.textContent).not.toContain('0.0.0')
  })
})
