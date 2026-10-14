/**
 * AuditLogPage value rendering: the Change column must read the stored
 * encoding of an audit value rather than print it. Fixtures use the shapes the
 * table actually holds — JSON-encoded from the main writer, bare strings from
 * the writers that never encoded, and a SQL NULL from a create row.
 */
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuditLogPage from '../AuditLogPage'
import { getOrgAuditLog, getOrgMembers } from '../../data/apiClient'
import type { AuditLogEntry } from '../../types'

vi.mock('../../data/apiClient', () => ({
  getOrgAuditLog: vi.fn(),
  getOrgMembers: vi.fn(),
}))

const mockGetOrgAuditLog = vi.mocked(getOrgAuditLog)
const mockGetOrgMembers = vi.mocked(getOrgMembers)

function queryWrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const ORG_ID = 'org-1'
const CHANGE_COLUMN = 6
const ZERO_ENTITY_ID = '00000000-0000-0000-0000-000000000000'

let nextId = 0

function entry(overrides: Partial<AuditLogEntry>): AuditLogEntry {
  nextId += 1
  return {
    id: `entry-${nextId}`,
    organization_id: ORG_ID,
    entity_type: 'scoped_control',
    entity_id: 'a1b2c3d4-0000-4000-8000-000000000001',
    action: 'update',
    changed_by_user_id: 'user-1',
    changed_by_email: 'someone@example.test',
    changed_at: '2026-09-22T10:54:26.604292+00:00',
    action_source: 'ui',
    ...overrides,
  }
}

async function renderEntries(entries: AuditLogEntry[]) {
  mockGetOrgAuditLog.mockResolvedValue({
    entries,
    total: entries.length,
    offset: 0,
    limit: 50,
  })
  const { container } = render(<AuditLogPage organizationId={ORG_ID} />, { wrapper: queryWrapper() })
  await waitFor(() => {
    expect(screen.queryByText('Loading audit log...')).not.toBeInTheDocument()
  })
  return container
}

/** The rendered text of one row's Change cell. */
function changeText(container: HTMLElement, row = 0): string {
  const cells = container.querySelectorAll('tbody tr')[row].querySelectorAll('td')
  return cells[CHANGE_COLUMN].textContent ?? ''
}

const VALIDATOR_ID = 'c73c5103-28a7-4bab-8697-e5eb8799ba0c'

beforeEach(() => {
  vi.clearAllMocks()
  mockGetOrgMembers.mockResolvedValue([{ id: VALIDATOR_ID, email: 'jane@example.test', display_name: 'Jane Doe' }])
  nextId = 0
})

// #1156: the org-wide page printed user ids raw, as the control's change
// history did. It names the member, and an id no member has reads as one.
describe('AuditLogPage member names', () => {
  it('names the member a user id refers to, and calls any other id an unknown member', async () => {
    const container = await renderEntries([
      entry({ field_name: 'last_validated_by', old_value: 'null', new_value: `"${VALIDATOR_ID}"` }),
      entry({
        entity_type: 'capability_test',
        field_name: 'recorded_by_user_id',
        old_value: `"${VALIDATOR_ID}"`,
        new_value: '"00000000-1111-4222-8333-444444444444"',
      }),
    ])
    expect(changeText(container, 0)).toBe('—→Jane Doe')
    expect(changeText(container, 1)).toBe('Jane Doe→Unknown member (00000000…)')
    expect(container.textContent).not.toContain(VALIDATOR_ID)
    expect(mockGetOrgMembers).toHaveBeenCalledWith(ORG_ID)
  })

  // #1156 review: the log is not held back behind the member list, and its
  // own error is not hidden while the members load.
  it('shows the log, and the log error, while the members load', async () => {
    mockGetOrgMembers.mockReturnValue(new Promise(() => {}))
    const container = await renderEntries([
      entry({ field_name: 'last_validated_by', old_value: 'null', new_value: `"${VALIDATOR_ID}"` }),
    ])
    expect(changeText(container)).toBe('—→Loading…')

    cleanup()
    mockGetOrgAuditLog.mockRejectedValue(new Error('Audit log unavailable'))
    render(<AuditLogPage organizationId={ORG_ID} />, { wrapper: queryWrapper() })
    expect(await screen.findByText(/Audit log unavailable/)).toBeInTheDocument()
  })
})

describe('AuditLogPage change values', () => {
  it('renders an empty-to-date transition as an em-dash and a date', async () => {
    const container = await renderEntries([
      entry({ field_name: 'completion_date', old_value: 'null', new_value: '"2026-09-22"' }),
    ])
    expect(changeText(container)).toBe('—→Sep 22, 2026')
  })

  it('renders a date-to-empty transition as a date and an em-dash', async () => {
    const container = await renderEntries([
      entry({ field_name: 'completion_date', old_value: '"2026-09-22"', new_value: 'null' }),
    ])
    expect(changeText(container)).toBe('Sep 22, 2026→—')
  })

  it('renders a date-to-date transition as two dates', async () => {
    const container = await renderEntries([
      entry({ field_name: 'target_date', old_value: '"2026-09-20"', new_value: '"2026-09-22"' }),
    ])
    expect(changeText(container)).toBe('Sep 20, 2026→Sep 22, 2026')
  })

  it('renders a SQL NULL prior value as an em-dash', async () => {
    const container = await renderEntries([
      entry({
        field_name: 'completion_date',
        old_value: null as unknown as undefined,
        new_value: '"2026-09-22"',
      }),
    ])
    expect(changeText(container)).toBe('—→Sep 22, 2026')
  })

  it('renders a timestamp-shaped date value as a date', async () => {
    const container = await renderEntries([
      entry({
        field_name: 'next_review_date',
        old_value: '"2026-09-22T00:00:00"',
        new_value: '"2026-10-22T00:00:00"',
      }),
    ])
    expect(changeText(container)).toBe('Sep 22, 2026→Oct 22, 2026')
  })

  it('keeps an unencoded historical date readable', async () => {
    const container = await renderEntries([
      entry({ field_name: 'treatment_due_date', old_value: '2026-09-20', new_value: '2026-09-22' }),
    ])
    expect(changeText(container)).toBe('Sep 20, 2026→Sep 22, 2026')
  })

  it('renders a status without its JSON quotes', async () => {
    const container = await renderEntries([
      entry({ field_name: 'implementation_status', old_value: '"at_risk"', new_value: '"implemented"' }),
    ])
    const text = changeText(container)
    expect(text).toBe('At risk→Implemented')
    expect(text).not.toContain('"')
  })

  it('keeps a bare string from a non-encoding writer as it was stored', async () => {
    const container = await renderEntries([
      entry({ field_name: 'scan_status', old_value: 'pending', new_value: 'skipped' }),
    ])
    expect(changeText(container)).toBe('pending→skipped')
  })

  it('renders a scoping change from the JSON booleans the writer emits', async () => {
    const container = await renderEntries([
      entry({ field_name: 'selected', old_value: 'false', new_value: 'true' }),
    ])
    expect(changeText(container)).toBe('No→Yes')
  })

  it('does not date-format a field whose name merely contains "date"', async () => {
    const container = await renderEntries([
      entry({ field_name: 'updated_at', old_value: '"2026-09-20"', new_value: '"2026-09-22"' }),
      entry({ field_name: 'validated', old_value: 'false', new_value: 'true' }),
    ])
    expect(changeText(container, 0)).toBe('2026-09-20→2026-09-22')
    expect(changeText(container, 1)).toBe('false→true')
  })

  it('never renders the string a broken date formats to', async () => {
    const container = await renderEntries([
      entry({ field_name: 'completion_date', old_value: 'null', new_value: '"2026-09-22"' }),
      entry({ field_name: 'completion_date', old_value: '"2026-09-22"', new_value: 'null' }),
      entry({ field_name: 'start_date', old_value: null as unknown as undefined, new_value: '"2026-09-22"' }),
      entry({ field_name: 'end_date', old_value: '2026-09-20', new_value: '"2026-09-22"' }),
      entry({ field_name: 'implementation_status', old_value: '"at_risk"', new_value: '"implemented"' }),
      entry({ field_name: 'scan_status', old_value: 'pending', new_value: 'skipped' }),
    ])
    expect(container.textContent).not.toMatch(/Invalid Date/)
  })
})

describe('AuditLogPage entity id', () => {
  it('labels a request-level row instead of printing an all-zero id', async () => {
    const container = await renderEntries([
      entry({ entity_type: 'engagements', entity_id: ZERO_ENTITY_ID, field_name: undefined }),
    ])
    expect(screen.getByText('request-level record')).toBeInTheDocument()
    expect(container.textContent).not.toContain('00000000')
  })

  it('still shows a real entity id', async () => {
    const container = await renderEntries([
      entry({ entity_id: 'a1b2c3d4-0000-4000-8000-000000000001', scf_id: 'IAC-05' }),
    ])
    expect(screen.getByText('IAC-05')).toBeInTheDocument()
    expect(container.textContent).not.toContain('request-level record')
  })
})

describe('AuditLogPage PIG entities', () => {
  it('labels a deficiency row and its fields', async () => {
    const container = await renderEntries([
      entry({ entity_type: 'deficiency', field_name: 'root_cause', old_value: 'null', new_value: '"Backups untested"' }),
    ])
    const cells = container.querySelectorAll('tbody tr')[0].querySelectorAll('td')
    expect(cells[3]).toHaveTextContent('Deficiency')
    expect(cells[5]).toHaveTextContent('Root Cause')
  })

  it('reads a capability test result as words', async () => {
    const container = await renderEntries([
      entry({ entity_type: 'capability_test', field_name: 'result', old_value: '"passed"', new_value: '"passed_with_findings"' }),
    ])
    expect(changeText(container)).toBe('Passed→Passed with findings')
  })

  it('names the entity type in the link the same way as the cell', async () => {
    await renderEntries([
      entry({ entity_type: 'scoped_control', entity_id: 'a1b2c3d4-0000-4000-8000-000000000001', scf_id: 'IAC-05' }),
    ])
    const link = screen.getByRole('link', { name: 'Open scoped control IAC-05' })
    expect(link).toHaveAttribute('title', 'Scoped control a1b2c3d4-0000-4000-8000-000000000001')
  })

  // #1156: the filter offered "Capability Test" while the rows read
  // "Capability test". Both now come from one label map.
  it('names each entity type in the filter exactly as its rows do', async () => {
    await renderEntries([])
    const filter = screen.getAllByRole('combobox')[0]
    const options = Array.from(filter.querySelectorAll('option')).filter((o) => o.getAttribute('value'))
    expect(options.length).toBeGreaterThan(10)
    const types = options.map((o) => o.getAttribute('value') as string)
    const optionText = options.map((o) => o.textContent)
    expect(optionText).toContain('Capability test')
    expect(optionText).toContain('Compensating control')
    expect(optionText).toContain('Journey stage')

    cleanup()
    const rows = await renderEntries(types.map((type) => entry({ entity_type: type, field_name: 'title' })))
    const rowText = Array.from(rows.querySelectorAll('tbody tr')).map((tr) => tr.querySelectorAll('td')[3].textContent)
    expect(rowText).toEqual(optionText)
  })

  it('offers the PIG entity types in the filter', async () => {
    await renderEntries([])
    const filter = screen.getAllByRole('combobox')[0]
    const values = Array.from(filter.querySelectorAll('option')).map((o) => o.getAttribute('value'))
    for (const type of ['deficiency', 'capability_test', 'compensating_control', 'posture_snapshot', 'journey_stage', 'journey', 'organization_framework_selection']) {
      expect(values).toContain(type)
    }
  })
})
