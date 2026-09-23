/**
 * AuditLogPage value rendering: the Change column must read the stored
 * encoding of an audit value rather than print it. Fixtures use the shapes the
 * table actually holds — JSON-encoded from the main writer, bare strings from
 * the writers that never encoded, and a SQL NULL from a create row.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import AuditLogPage from '../AuditLogPage'
import { getOrgAuditLog } from '../../data/apiClient'
import type { AuditLogEntry } from '../../types'

vi.mock('../../data/apiClient', () => ({
  getOrgAuditLog: vi.fn(),
}))

const mockGetOrgAuditLog = vi.mocked(getOrgAuditLog)

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
  const { container } = render(<AuditLogPage organizationId={ORG_ID} />)
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

beforeEach(() => {
  vi.clearAllMocks()
  nextId = 0
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
    expect(text).toBe('At Risk→Implemented')
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
