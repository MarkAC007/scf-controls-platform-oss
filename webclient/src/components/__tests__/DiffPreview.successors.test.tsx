/**
 * DiffPreview — publisher-declared succession in the diff table.
 *
 * Two things the diff has to say out loud, because both change what an admin
 * does next: a `changed` control row whose id the publisher re-used for a merge
 * is not a wording change, and a `deprecated` row's successor comes from a
 * named workbook declaration rather than a similarity guess.
 */
import { fireEvent, render, screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import DiffPreview from '../platform/DiffPreview'
import { getCatalogUpgradeDiff } from '../../data/catalogUpgradeApi'
import type { DiffItem, DiffPageResponse, DiffSummary } from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', () => ({
  getCatalogUpgradeDiff: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockGetDiff = vi.mocked(getCatalogUpgradeDiff)

const RUN_ID = 'run-1'

const CHANGED_WITH_ID_REUSE: DiffItem = {
  entity: 'controls',
  change_class: 'changed',
  key: 'CTL-9',
  name: 'Access Enforcement',
  fields: { name: { old: 'Access Control', new: 'Access Enforcement' } },
  data: {},
  id_reused: { merged_into: 'CTL-9', legacy_name: 'Legacy Access Control' },
  suggestions: [],
}

const CHANGED_PLAIN: DiffItem = {
  entity: 'controls',
  change_class: 'changed',
  key: 'CTL-8',
  name: 'Session Lock',
  fields: { name: { old: 'Session Locking', new: 'Session Lock' } },
  data: {},
  id_reused: null,
  suggestions: [],
}

const DEPRECATED_DECLARED: DiffItem = {
  entity: 'controls',
  change_class: 'deprecated',
  key: 'OLD-1',
  name: 'Legacy Control',
  fields: {},
  data: {},
  superseded_by: 'NEW-1',
  superseded_source: 'workbook_crosswalk',
  // Still shipped by the backend at score 1.0 — never rendered as a suggestion.
  suggestions: [{ scf_id: 'NEW-1', name: 'Successor Control', score: 1 }],
}

const DEPRECATED_MERGED: DiffItem = {
  entity: 'controls',
  change_class: 'deprecated',
  key: 'OLD-2',
  name: 'Merged Control',
  fields: {},
  data: {},
  superseded_by: 'NEW-2',
  superseded_source: 'publisher_merged',
  suggestions: [],
}

const DEPRECATED_UNDECLARED: DiffItem = {
  entity: 'controls',
  change_class: 'deprecated',
  key: 'OLD-3',
  name: 'Orphan Control',
  fields: {},
  data: {},
  superseded_by: null,
  superseded_source: null,
  suggestions: [],
}

function page(items: DiffItem[]): DiffPageResponse {
  return { run_id: RUN_ID, items, total: items.length, page: 1, page_size: 50 }
}

const summary: DiffSummary = {
  from_version: '2026.2',
  to_version: '2026.3',
  entities: {
    controls: { added: 2, changed: 2, deprecated: 3, resurrected: 0, unchanged: 1400, id_reused: 1 },
  },
}

function rowFor(key: string): HTMLElement {
  const row = screen.getByText(key).closest('tr')
  if (!row) throw new Error(`No table row rendered for ${key}`)
  return row as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  mockGetDiff.mockResolvedValue(
    page([CHANGED_WITH_ID_REUSE, CHANGED_PLAIN, DEPRECATED_DECLARED, DEPRECATED_MERGED, DEPRECATED_UNDECLARED])
  )
})

describe('DiffPreview — id reuse on changed rows', () => {
  it('says which legacy control the publisher merged into a re-used id', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    expect(await screen.findByText('CTL-9')).toBeInTheDocument()
    expect(
      within(rowFor('CTL-9')).getByText(
        'ID reused — publisher merged Legacy Access Control into CTL-9'
      )
    ).toBeInTheDocument()

    // The field-level diff is still shown alongside it.
    expect(within(rowFor('CTL-9')).getByText('Access Control')).toBeInTheDocument()
  })

  it('says nothing about id reuse on an ordinary changed row', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    await screen.findByText('CTL-8')
    expect(within(rowFor('CTL-8')).queryByText(/ID reused/)).not.toBeInTheDocument()
  })
})

describe('DiffPreview — declared successors on deprecated rows', () => {
  it('labels the workbook declaration that produced the successor', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    await screen.findByText('OLD-1')
    expect(within(rowFor('OLD-1')).getByText('Superseded by NEW-1')).toBeInTheDocument()
    expect(within(rowFor('OLD-1')).getByText('Legacy SCF # crosswalk')).toBeInTheDocument()

    expect(within(rowFor('OLD-2')).getByText('Superseded by NEW-2')).toBeInTheDocument()
    expect(within(rowFor('OLD-2')).getByText('Publisher merge list')).toBeInTheDocument()
  })

  it('renders no similarity scores for the declared successor', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    await screen.findByText('OLD-1')
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()
    expect(screen.queryByText('NEW-1 · 100%')).not.toBeInTheDocument()
  })

  it('says no successor was declared when the workbook declared none', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    await screen.findByText('OLD-3')
    expect(within(rowFor('OLD-3')).getByText('No successor declared')).toBeInTheDocument()
  })

  it('keeps the entity tab count to real entity changes, not the id-reuse subset', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    // 2 added + 2 changed + 3 deprecated + 0 resurrected = 7; id_reused is a
    // subset of changed and must not be added again.
    expect(await screen.findByRole('tab', { name: 'Controls (7)' })).toBeInTheDocument()
    expect(
      screen.getByText(
        '1 changed control re-uses an id the publisher merged a retired control into.'
      )
    ).toBeInTheDocument()
  })

  it('reports no id-reuse count for a run staged before it was detected', async () => {
    const older: DiffSummary = {
      ...summary,
      entities: {
        controls: { added: 2, changed: 2, deprecated: 3, resurrected: 0, unchanged: 1400 },
      },
    }
    render(<DiffPreview runId={RUN_ID} diffSummary={older} />)

    expect(await screen.findByRole('tab', { name: 'Controls (7)' })).toBeInTheDocument()
    expect(screen.queryByText(/re-use[s]? an id the publisher merged/)).not.toBeInTheDocument()
  })

  it('still filters by change class', async () => {
    render(<DiffPreview runId={RUN_ID} diffSummary={summary} />)

    await screen.findByText('OLD-1')
    mockGetDiff.mockResolvedValue(page([DEPRECATED_DECLARED]))
    fireEvent.click(screen.getByRole('button', { name: 'Deprecated' }))

    await screen.findByText('OLD-1')
    expect(mockGetDiff).toHaveBeenLastCalledWith(RUN_ID, {
      entity: 'controls',
      change_class: 'deprecated',
      page: 1,
      page_size: 50,
    })
  })
})
