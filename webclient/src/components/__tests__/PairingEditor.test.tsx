/**
 * PairingEditor — the workbook is the authority for successor pairing.
 *
 * What these assertions protect (Mark's design decision: "the spreadsheet is
 * the authority here, exclusively"):
 *  - no scored similarity chips anywhere; the declared successor and the
 *    workbook declaration that produced it are what the admin reads
 *  - a row carrying a declared successor is NOT undecided
 *  - every deprecated control is loaded, by paging the diff endpoint to `total`
 *  - the PUT carries OVERRIDES ONLY — a row left on its declaration, or cleared
 *    back to it, is not sent, so apply-time uses the workbook's own successor
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import PairingEditor from '../platform/PairingEditor'
import {
  getCatalogUpgradeDiff,
  putCatalogUpgradePairings,
} from '../../data/catalogUpgradeApi'
import type {
  DiffItem,
  DiffPageResponse,
  SupersededPairing,
} from '../../types/catalogUpgrade'

vi.mock('../../data/catalogUpgradeApi', () => ({
  getCatalogUpgradeDiff: vi.fn(),
  putCatalogUpgradePairings: vi.fn(),
}))

vi.mock('react-hot-toast', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}))

const mockGetDiff = vi.mocked(getCatalogUpgradeDiff)
const mockPutPairings = vi.mocked(putCatalogUpgradePairings)

const RUN_ID = 'run-1'

/** Fixture keys deliberately avoid the real SCF `XXX-NN` id shape. */
function deprecatedRow(
  key: string,
  supersededBy: string | null,
  source: DiffItem['superseded_source']
): DiffItem {
  return {
    entity: 'controls',
    change_class: 'deprecated',
    key,
    name: `${key} legacy control`,
    fields: {},
    data: {},
    superseded_by: supersededBy,
    superseded_source: source,
    // The backend still ships the declared successor here at score 1.0. The
    // editor must ignore it entirely rather than offer it as a suggestion.
    suggestions: supersededBy ? [{ scf_id: supersededBy, name: 'Successor', score: 1 }] : [],
  }
}

/**
 * Four deprecated controls over two pages — page 2 exists only to prove the
 * editor keeps paging until `total` instead of showing the first page.
 */
const PAGE_ONE: DiffPageResponse = {
  run_id: RUN_ID,
  items: [
    deprecatedRow('OLD-1', 'NEW-1', 'workbook_crosswalk'),
    deprecatedRow('OLD-2', 'NEW-2', 'publisher_merged'),
    deprecatedRow('OLD-3', null, null),
  ],
  total: 4,
  page: 1,
  page_size: 500,
}

const PAGE_TWO: DiffPageResponse = {
  run_id: RUN_ID,
  items: [deprecatedRow('OLD-4', 'NEW-4', 'workbook_crosswalk')],
  total: 4,
  page: 2,
  page_size: 500,
}

function primePages() {
  mockGetDiff.mockImplementation(async (_runId, params = {}) =>
    (params.page ?? 1) >= 2 ? PAGE_TWO : PAGE_ONE
  )
}

function renderEditor(pairings: SupersededPairing[] = []) {
  const onPairingsSaved = vi.fn()
  render(
    <PairingEditor runId={RUN_ID} pairings={pairings} onPairingsSaved={onPairingsSaved} />
  )
  return { onPairingsSaved }
}

function rowFor(key: string): HTMLElement {
  const row = screen.getByText(key).closest('tr')
  if (!row) throw new Error(`No table row rendered for ${key}`)
  return row as HTMLElement
}

beforeEach(() => {
  vi.clearAllMocks()
  primePages()
  mockPutPairings.mockImplementation(async (runId, pairings) => ({
    run_id: runId,
    pairings,
  }))
})

describe('PairingEditor — declared successors, no similarity chips', () => {
  it('shows the workbook declaration and its source, and no scored chips', async () => {
    renderEditor()

    expect(await screen.findByText('OLD-1')).toBeInTheDocument()

    // The declared successor is read from the workbook, per row, with a label
    // saying which declaration produced it.
    expect(within(rowFor('OLD-1')).getByText('NEW-1')).toBeInTheDocument()
    expect(within(rowFor('OLD-1')).getByText('Legacy SCF # crosswalk')).toBeInTheDocument()
    expect(within(rowFor('OLD-2')).getByText('NEW-2')).toBeInTheDocument()
    expect(within(rowFor('OLD-2')).getByText('Publisher merge list')).toBeInTheDocument()
    expect(within(rowFor('OLD-3')).getByText('none declared')).toBeInTheDocument()

    // No similarity anywhere: no percentage text, and no clickable score chip.
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /NEW-1 ·/ })).not.toBeInTheDocument()
    expect(screen.queryByText('Suggestions')).not.toBeInTheDocument()
    expect(screen.getByText('Declared by workbook')).toBeInTheDocument()
  })

  it('states that the workbook successors are applied unless overridden here', async () => {
    renderEditor()
    await screen.findByText('OLD-1')

    expect(
      screen.getByText(/applied as declared unless you override/i)
    ).toBeInTheDocument()
  })

  it('treats a declared row as decided and only an undeclared row as undecided', async () => {
    renderEditor()
    await screen.findByText('OLD-4')

    // 3 declared (OLD-1, OLD-2, OLD-4), 0 overridden, 1 undecided (OLD-3).
    expect(
      screen.getByText('3 declared by the workbook · 0 overridden · 1 undecided')
    ).toBeInTheDocument()

    expect(within(rowFor('OLD-3')).getByText('Undecided')).toBeInTheDocument()
    expect(within(rowFor('OLD-1')).queryByText('Undecided')).not.toBeInTheDocument()
  })

  it('pages the diff endpoint at page_size 500 until every row is loaded', async () => {
    renderEditor()

    expect(await screen.findByText('OLD-4')).toBeInTheDocument()

    expect(mockGetDiff).toHaveBeenCalledTimes(2)
    expect(mockGetDiff).toHaveBeenNthCalledWith(1, RUN_ID, {
      entity: 'controls',
      change_class: 'deprecated',
      page: 1,
      page_size: 500,
    })
    expect(mockGetDiff).toHaveBeenNthCalledWith(2, RUN_ID, {
      entity: 'controls',
      change_class: 'deprecated',
      page: 2,
      page_size: 500,
    })
    expect(screen.getAllByRole('row')).toHaveLength(5) // header + 4 controls
  })
})

describe('PairingEditor — overrides', () => {
  it('overriding a declared row, then clearing it, returns it to the declaration', async () => {
    renderEditor()
    await screen.findByText('OLD-1')

    const input = within(rowFor('OLD-1')).getByLabelText('Override successor for OLD-1')
    fireEvent.change(input, { target: { value: 'NEW-9' } })

    expect(within(rowFor('OLD-1')).getByText('Overridden → NEW-9')).toBeInTheDocument()
    expect(
      screen.getByText('2 declared by the workbook · 1 overridden · 1 undecided')
    ).toBeInTheDocument()

    fireEvent.click(within(rowFor('OLD-1')).getByRole('button', { name: 'Clear' }))

    expect(within(rowFor('OLD-1')).queryByText('Overridden → NEW-9')).not.toBeInTheDocument()
    expect(
      screen.getByText('3 declared by the workbook · 0 overridden · 1 undecided')
    ).toBeInTheDocument()
  })

  it('records an explicit "no successor" as an override', async () => {
    renderEditor()
    await screen.findByText('OLD-2')

    fireEvent.click(within(rowFor('OLD-2')).getByRole('button', { name: 'No successor' }))

    expect(
      within(rowFor('OLD-2')).getByText('Overridden — retire with no successor')
    ).toBeInTheDocument()
    expect(
      screen.getByText('2 declared by the workbook · 1 overridden · 1 undecided')
    ).toBeInTheDocument()
  })

  it('sends only overrides on save, never a row left on its declaration', async () => {
    const { onPairingsSaved } = renderEditor()
    await screen.findByText('OLD-4')

    // OLD-1: overridden to a different successor -> sent.
    fireEvent.change(within(rowFor('OLD-1')).getByLabelText('Override successor for OLD-1'), {
      target: { value: 'NEW-9' },
    })
    // OLD-2: explicitly retired with no successor -> sent.
    fireEvent.click(within(rowFor('OLD-2')).getByRole('button', { name: 'No successor' }))
    // OLD-3: no declaration, explicitly retired -> sent (it is now decided).
    fireEvent.click(within(rowFor('OLD-3')).getByRole('button', { name: 'No successor' }))
    // OLD-4: typed back to exactly what the workbook declared -> NOT an override.
    fireEvent.change(within(rowFor('OLD-4')).getByLabelText('Override successor for OLD-4'), {
      target: { value: 'NEW-4' },
    })

    expect(
      screen.getByText('1 declared by the workbook · 3 overridden · 0 undecided')
    ).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Save pairings' }))

    await waitFor(() => expect(mockPutPairings).toHaveBeenCalledTimes(1))
    const [, sent] = mockPutPairings.mock.calls[0]
    expect(sent).toEqual([
      { deprecated_scf_id: 'OLD-1', superseded_by: 'NEW-9' },
      { deprecated_scf_id: 'OLD-2', superseded_by: null },
      { deprecated_scf_id: 'OLD-3', superseded_by: null },
    ])
    await waitFor(() => expect(onPairingsSaved).toHaveBeenCalledWith(sent))
  })

  it('seeds the draft from saved overrides and leaves the rest on the declaration', async () => {
    renderEditor([{ deprecated_scf_id: 'OLD-1', superseded_by: 'NEW-9' }])
    await screen.findByText('OLD-4')

    expect(within(rowFor('OLD-1')).getByLabelText('Override successor for OLD-1')).toHaveValue(
      'NEW-9'
    )
    expect(within(rowFor('OLD-2')).getByLabelText('Override successor for OLD-2')).toHaveValue('')
    expect(
      screen.getByText('2 declared by the workbook · 1 overridden · 1 undecided')
    ).toBeInTheDocument()
  })

  it('gives every control in the table an explicit button type', async () => {
    renderEditor()
    await screen.findByText('OLD-4')

    for (const button of screen.getAllByRole('button')) {
      expect(button).toHaveAttribute('type')
    }
  })
})
