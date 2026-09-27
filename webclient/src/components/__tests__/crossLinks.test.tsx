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
vi.mock('../../data/apiClient', () => ({
  getRiskAssessments: vi.fn(),
}))

import AppLink, { destinationSearch } from '../AppLink'
import DeprecatedBadge from '../DeprecatedBadge'
import RiskThreatContext from '../RiskThreatContext'
import { auditEntityDestination } from '../AuditLogPage'
import { changelogDestination } from '../CatalogChangelogPage'
import { getRiskAssessments } from '../../data/apiClient'
import riskCodesData from '../../data/risk_codes.json'
import type { AuditLogEntry } from '../../types'

// Fixture identifiers. The risk code must exist in the bundled catalogue,
// since RiskThreatContext drops codes it cannot describe.
const CONTROL = 'CONTROL_ONE'
const SUCCESSOR = 'CONTROL_TWO'
const EVIDENCE = 'EVIDENCE_ONE'
const RISK = Object.keys(riskCodesData.codes)[0]

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
    fireEvent.click(screen.getByRole('button', { name: RISK }))
    const link = await screen.findByRole('link', { name: 'Open risk record' })
    expect(link.getAttribute('href')).toBe(`/?tab=risk-register&risk=${RISK}`)
  })

  it('says plainly when the organisation has no record for the code', async () => {
    vi.mocked(getRiskAssessments).mockResolvedValue([])
    render(withQuery(<RiskThreatContext mapping={mapping} />))
    fireEvent.click(screen.getByRole('button', { name: RISK }))
    await waitFor(() =>
      expect(screen.getByText(new RegExp(`no ${RISK} record in this organisation`))).toBeInTheDocument(),
    )
    expect(screen.queryByRole('link')).toBeNull()
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
