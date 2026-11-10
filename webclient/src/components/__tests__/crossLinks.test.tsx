/**
 * Cross-links between objects (2026-09-25 cross-link QA, docs/qa/).
 *
 * Each gap was a reference rendered as text, or as a click target with no
 * URL. These pin the fix for each: a real anchor whose href names the exact
 * object, and a plain click that navigates in place with a Back entry.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('../../contexts/OrganizationContext', () => ({
  useOrganization: () => ({ currentOrg: { id: 'org-1' } }),
}))
// Invented catalogue rows: the SCF's own titles are CC BY-ND and live only in
// the database, so tests describe codes the server would never return.
const RISK_CATALOG = {
  catalog_version: 'test',
  categories: { XX: { name: 'Test grouping', color: '#6366f1' } },
  codes: {
    'R-XX-1': { category: 'XX', title: 'Invented risk one', description: 'Invented.' },
    'R-XX-2': { category: 'XX', title: 'Invented risk two', description: 'Invented.' },
    'R-XX-3': { category: 'XX', title: 'Invented risk three', description: 'Invented.' },
  },
}
const THREAT_CATALOG = {
  catalog_version: 'test',
  categories: { NT: { name: 'Natural', color: '#10b981' } },
  codes: { 'NT-1': { category: 'NT', title: 'Invented threat', description: 'Invented.' } },
}

vi.mock('../../data/apiClient', () => ({
  getRiskAssessments: vi.fn(),
  getRiskCatalog: vi.fn(async () => RISK_CATALOG),
  getThreatCatalog: vi.fn(async () => THREAT_CATALOG),
}))

import AppLink, { destinationSearch, navigateToDestination } from '../AppLink'
import DeprecatedBadge from '../DeprecatedBadge'
import RiskThreatContext from '../RiskThreatContext'
import { auditEntityDestination } from '../AuditLogPage'
import { changelogDestination } from '../CatalogChangelogPage'
import { getRiskAssessments } from '../../data/apiClient'
import type { AuditLogEntry } from '../../types'

// Fixture identifiers. The risk code must exist in the served catalogue,
// since RiskThreatContext drops codes it cannot describe.
const CONTROL = 'CONTROL_ONE'
const SUCCESSOR = 'CONTROL_TWO'
const EVIDENCE = 'EVIDENCE_ONE'
const RISK = 'R-XX-1'

function withQuery(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{ui}</QueryClientProvider>
}

afterEach(() => {
  window.history.replaceState(null, '', '/')
  vi.clearAllMocks()
})

describe('AppLink', () => {
  it('renders a real href for the destination', () => {
    render(<AppLink to={{ kind: 'vendor', id: 'v1' }}>vendor</AppLink>)
    expect(screen.getByRole('link', { name: 'vendor' }).getAttribute('href'))
      .toBe('/?tab=vendors&vendor=v1')
  })

  it('a plain click pushes the URL and announces it on popstate', () => {
    const onPop = vi.fn()
    window.addEventListener('popstate', onPop)
    const before = window.history.length
    render(<AppLink to={{ kind: 'control', id: CONTROL, mode: 'full-library' }}>control</AppLink>)
    fireEvent.click(screen.getByRole('link', { name: 'control' }))
    window.removeEventListener('popstate', onPop)
    expect(window.location.search).toBe(`?tab=library&mode=full-library&item=${CONTROL}`)
    expect(window.history.length).toBe(before + 1)
    expect(onPop).toHaveBeenCalledTimes(1)
  })

  it('a modified or middle click is left to the browser', () => {
    render(<AppLink to={{ kind: 'evidence', id: EVIDENCE }}>evidence</AppLink>)
    const link = screen.getByRole('link', { name: 'evidence' })
    fireEvent.click(link, { ctrlKey: true })
    fireEvent.click(link, { button: 1 })
    expect(window.location.search).toBe('')
  })

  it('opens the library list with no control selected', () => {
    expect(destinationSearch(`tab=library&mode=full-library&item=${CONTROL}`, { kind: 'library' }))
      .toBe('tab=library&mode=in-scope')
  })

  it('navigates in place without a link, for rows that are not anchors', () => {
    const onPop = vi.fn()
    window.addEventListener('popstate', onPop)
    navigateToDestination({ kind: 'control', id: CONTROL })
    window.removeEventListener('popstate', onPop)
    expect(window.location.search).toBe(`?tab=library&mode=in-scope&item=${CONTROL}`)
    expect(onPop).toHaveBeenCalledTimes(1)
  })

  it('keeps unrelated session parameters', () => {
    expect(destinationSearch('invite_type=org', { kind: 'evidence', id: EVIDENCE }))
      .toBe(`invite_type=org&tab=evidence&view=workspace&item=${EVIDENCE}`)
  })
})

describe('deprecated successor', () => {
  it('links the successor control without implying scope', () => {
    render(<DeprecatedBadge catalog_status="deprecated" superseded_by={SUCCESSOR} />)
    const link = screen.getByRole('link', { name: `Open successor ${SUCCESSOR}` })
    expect(link.getAttribute('href')).toBe(`/?tab=library&mode=full-library&item=${SUCCESSOR}`)
  })

  it('renders no link when there is no successor', () => {
    render(<DeprecatedBadge catalog_status="deprecated" />)
    expect(screen.queryByRole('link')).toBeNull()
  })

  it('stays text in compact rows, which are themselves clickable', () => {
    render(<DeprecatedBadge catalog_status="deprecated" superseded_by={SUCCESSOR} compact />)
    expect(screen.queryByRole('link')).toBeNull()
  })
})

describe('control → risk record', () => {
  const mapping = { risk_codes: [RISK], threat_codes: [] }

  it('offers the organisation risk record when one exists', async () => {
    vi.mocked(getRiskAssessments).mockResolvedValue([{ risk_code: RISK }] as never)
    render(withQuery(<RiskThreatContext mapping={mapping} />))
    fireEvent.click(await screen.findByRole('button', { name: RISK }))
    const link = await screen.findByRole('link', { name: 'Open risk record' })
    expect(link.getAttribute('href')).toBe(`/?tab=risk-register&risk=${RISK}`)
  })

  it('says plainly when the organisation has no record for the code', async () => {
    vi.mocked(getRiskAssessments).mockResolvedValue([])
    render(withQuery(<RiskThreatContext mapping={mapping} />))
    fireEvent.click(await screen.findByRole('button', { name: RISK }))
    await waitFor(() =>
      expect(screen.getByText(new RegExp(`no ${RISK} record in this organisation`))).toBeInTheDocument(),
    )
    expect(screen.queryByRole('link')).toBeNull()
  })
})

describe('control → risk codes by rating (SCF 2026.3)', () => {
  it('shows Likely codes open and keeps Possible ones behind a count until asked', async () => {
    vi.mocked(getRiskAssessments).mockResolvedValue([])
    render(withQuery(
      <RiskThreatContext
        mapping={{
          risk_codes: ['R-XX-1', 'R-XX-2', 'R-XX-3'],
          threat_codes: ['NT-1'],
          risk_ratings: { 'R-XX-1': 'likely', 'R-XX-2': 'possible', 'R-XX-3': 'possible' },
          threat_ratings: { 'NT-1': 'likely' },
        }}
      />,
    ))
    expect(await screen.findByRole('button', { name: 'R-XX-1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'R-XX-2' })).toBeNull()
    const toggle = screen.getByRole('button', { name: '+2 possible' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(screen.getByRole('button', { name: 'R-XX-2' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'R-XX-3' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'hide possible' }).getAttribute('aria-expanded')).toBe('true')
    // Threats are rated separately and here have nothing to hide.
    expect(screen.getByRole('button', { name: 'NT-1' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: /possible/ })).toHaveLength(1)
  })

  it('shows every code open when the catalogue carries no ratings (pre-2026.3 install)', async () => {
    vi.mocked(getRiskAssessments).mockResolvedValue([])
    render(withQuery(<RiskThreatContext mapping={{ risk_codes: ['R-XX-1', 'R-XX-2'], threat_codes: [] }} />))
    expect(await screen.findByRole('button', { name: 'R-XX-1' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'R-XX-2' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /possible/ })).toBeNull()
  })

  it('drops codes the served catalogue does not define', async () => {
    vi.mocked(getRiskAssessments).mockResolvedValue([])
    render(withQuery(<RiskThreatContext mapping={{ risk_codes: ['R-XX-1', 'R-ZZ-9'], threat_codes: [] }} />))
    expect(await screen.findByRole('button', { name: 'R-XX-1' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'R-ZZ-9' })).toBeNull()
  })
})

describe('audit log destinations', () => {
  const base = {
    id: 'a', organization_id: 'org-1', entity_id: '11111111-2222-3333-4444-555555555555',
    action: 'update', changed_at: '2026-09-25T00:00:00Z',
  } as AuditLogEntry

  it('opens a risk assessment by its resolved risk code', () => {
    const dest = auditEntityDestination({
      ...base, entity_type: 'risk_assessment', entity_ref: RISK, entity_label: RISK,
    })
    expect(dest?.to).toEqual({ kind: 'risk', id: RISK })
    expect(dest?.label).toBe(RISK)
  })

  it('opens a scoped control by scf_id', () => {
    expect(auditEntityDestination({ ...base, entity_type: 'scoped_control', scf_id: CONTROL })?.to)
      .toEqual({ kind: 'control', id: CONTROL })
  })

  it('maps vendor, system, task and evidence rows to their pages', () => {
    const kinds = ['vendor', 'system', 'evidence_collection_task', 'evidence_tracking'].map(
      (entity_type) => auditEntityDestination({ ...base, entity_type, entity_ref: 'x' })?.to.kind,
    )
    expect(kinds).toEqual(['vendor', 'system', 'task', 'evidence'])
  })

  it('does not link a deleted object or a type with no page', () => {
    expect(auditEntityDestination({ ...base, entity_type: 'risk_assessment' })).toBeNull()
    expect(auditEntityDestination({ ...base, entity_type: 'api_key', entity_ref: 'x' })).toBeNull()
  })
})

describe('changelog destinations', () => {
  it('opens a control or evidence item directly', () => {
    expect(changelogDestination('controls', CONTROL)?.to)
      .toEqual({ kind: 'control', id: CONTROL, mode: 'full-library' })
    expect(changelogDestination('evidence', EVIDENCE)?.to).toEqual({ kind: 'evidence', id: EVIDENCE })
  })

  it('opens an objective on its parent control', () => {
    expect(changelogDestination('assessment_objectives', `${CONTROL}_A01`)?.to)
      .toEqual({ kind: 'control', id: CONTROL, mode: 'full-library' })
  })

  it('leaves identifiers without a page as text', () => {
    expect(changelogDestination('domains', 'DOMAIN')).toBeNull()
    expect(changelogDestination('framework_mappings', CONTROL)).toBeNull()
  })
})
