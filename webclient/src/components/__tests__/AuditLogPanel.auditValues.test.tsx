/**
 * AuditLogPanel value rendering: the per-control change history reads the same
 * audit values as the org-wide page and must decode them the same way. These
 * are the page's cases mirrored onto the panel — the two renderers share a
 * helper precisely so they cannot drift apart again.
 */
import { render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AuditLogPanel } from '../AuditLogPanel'
import { getAuditLog } from '../../data/apiClient'
import type { AuditLogEntry } from '../../types'

vi.mock('../../data/apiClient', () => ({
  getAuditLog: vi.fn(),
}))

const mockGetAuditLog = vi.mocked(getAuditLog)

const ORG_ID = 'org-1'
const SCF_ID = 'IAC-05'

let nextId = 0

function entry(overrides: Partial<AuditLogEntry>): AuditLogEntry {
  nextId += 1
  return {
    id: `entry-${nextId}`,
    organization_id: ORG_ID,
    entity_type: 'scoped_control',
    entity_id: 'a1b2c3d4-0000-4000-8000-000000000001',
    scf_id: SCF_ID,
    action: 'update',
    changed_by_user_id: 'user-1',
    changed_by_email: 'someone@example.test',
    changed_at: '2026-09-22T10:54:26.604292+00:00',
    action_source: 'ui',
    ...overrides,
  }
}

async function renderEntries(entries: AuditLogEntry[]) {
  mockGetAuditLog.mockResolvedValue({ entries, total: entries.length } as never)
  const { container } = render(<AuditLogPanel scfId={SCF_ID} organizationId={ORG_ID} />)
  await waitFor(() => {
    expect(screen.queryByText('Loading change history...')).not.toBeInTheDocument()
  })
  return container
}

/** The rendered text of the change row carrying this field's label. */
function changeText(label: string): string {
  const labelNode = screen.getByText(`${label}:`)
  return (labelNode.parentElement?.textContent ?? '').replace(`${label}:`, '')
}

beforeEach(() => {
  vi.clearAllMocks()
  nextId = 0
})

describe('AuditLogPanel change values', () => {
  it('renders an empty-to-date transition as an em-dash and a date', async () => {
    await renderEntries([
      entry({ field_name: 'completion_date', old_value: 'null', new_value: '"2026-09-22"' }),
    ])
    expect(changeText('Completion Date')).toBe('—→Sep 22, 2026')
  })

  it('renders a date-to-empty transition as a date and an em-dash', async () => {
    await renderEntries([
      entry({ field_name: 'completion_date', old_value: '"2026-09-22"', new_value: 'null' }),
    ])
    expect(changeText('Completion Date')).toBe('Sep 22, 2026→—')
  })

  it('renders a date-to-date transition as two dates', async () => {
    await renderEntries([
      entry({ field_name: 'target_date', old_value: '"2026-09-20"', new_value: '"2026-09-22"' }),
    ])
    expect(changeText('Target Date')).toBe('Sep 20, 2026→Sep 22, 2026')
  })

  it('renders a SQL NULL prior value as an em-dash', async () => {
    await renderEntries([
      entry({
        field_name: 'start_date',
        old_value: null as unknown as undefined,
        new_value: '"2026-09-22"',
      }),
    ])
    expect(changeText('start_date')).toBe('—→Sep 22, 2026')
  })

  it('renders a timestamp-shaped date value as a date', async () => {
    await renderEntries([
      entry({
        field_name: 'next_review_date',
        old_value: '"2026-09-22T00:00:00"',
        new_value: '"2026-10-22T00:00:00"',
      }),
    ])
    expect(changeText('next_review_date')).toBe('Sep 22, 2026→Oct 22, 2026')
  })

  it('keeps an unencoded historical date readable', async () => {
    await renderEntries([
      entry({ field_name: 'treatment_due_date', old_value: '2026-09-20', new_value: '2026-09-22' }),
    ])
    expect(changeText('treatment_due_date')).toBe('Sep 20, 2026→Sep 22, 2026')
  })

  it('renders a status without its JSON quotes', async () => {
    await renderEntries([
      entry({ field_name: 'implementation_status', old_value: '"at_risk"', new_value: '"implemented"' }),
    ])
    const text = changeText('Status')
    expect(text).toBe('At Risk→Implemented')
    expect(text).not.toContain('"')
  })

  it('keeps a bare string from a non-encoding writer as it was stored', async () => {
    await renderEntries([
      entry({ field_name: 'scan_status', old_value: 'pending', new_value: 'skipped' }),
    ])
    expect(changeText('scan_status')).toBe('pending→skipped')
  })

  it('renders a scoping change from the JSON booleans the writer emits', async () => {
    await renderEntries([
      entry({ field_name: 'selected', old_value: 'false', new_value: 'true' }),
    ])
    expect(changeText('Scoped')).toBe('No→Yes')
  })

  it('does not date-format a field whose name merely contains "date"', async () => {
    await renderEntries([
      entry({ field_name: 'updated_at', old_value: '"2026-09-20"', new_value: '"2026-09-22"' }),
      entry({ field_name: 'validated', old_value: 'false', new_value: 'true' }),
    ])
    expect(changeText('updated_at')).toBe('2026-09-20→2026-09-22')
    expect(changeText('validated')).toBe('false→true')
  })

  it('never renders the string a broken date formats to', async () => {
    const container = await renderEntries([
      entry({ field_name: 'completion_date', old_value: 'null', new_value: '"2026-09-22"' }),
      entry({ field_name: 'target_date', old_value: '"2026-09-22"', new_value: 'null' }),
      entry({ field_name: 'end_date', old_value: '2026-09-20', new_value: '"2026-09-22"' }),
      entry({ field_name: 'implementation_status', old_value: '"at_risk"', new_value: '"implemented"' }),
      entry({ field_name: 'scan_status', old_value: 'pending', new_value: 'skipped' }),
    ])
    expect(container.textContent).not.toMatch(/Invalid Date/)
  })
})
