/**
 * DeficienciesPage: the Board view beside the List (#1155).
 *
 * The board and the list must agree on what a filter shows. Both read one
 * query and apply Status in the browser (the board by hiding columns), so its
 * column counts are the strip's counts and the same filters give the same
 * total. A collapsed Cancelled column keeps its count but not its cards, so
 * the toolbar count leaves those out.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider } from '../../../__tests__/confirmDialog'
import DeficienciesPage from '../DeficienciesPage'
import { DEFICIENCY_VIEW_STORAGE_KEY } from '../DeficienciesPage'
import { BOARD_COLLAPSE_STORAGE_KEY } from '../DeficiencyBoard'
import type { ActionItemStatus, Deficiency } from '../../../types'

const state = vi.hoisted(() => ({ editor: true }))
const api = vi.hoisted(() => ({
  listDeficiencies: vi.fn(),
  getDeficiencySummary: vi.fn(),
  createDeficiency: vi.fn(),
  updateDeficiency: vi.fn(),
  getOrgMembers: vi.fn(),
  getRiskAssessments: vi.fn(),
  fetchScopedControlsPage: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('../../../hooks/useHasOrgRole', () => ({ useIsOrgEditor: () => state.editor }))
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const make = (over: Partial<Deficiency>): Deficiency => ({
  id: 'x',
  organization_id: 'org-one',
  title: 'x',
  priority: 'medium',
  status: 'open',
  auto_generated: false,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
})

const ALL = [
  make({ id: 'a', title: 'Overdue gap', status: 'open', due_date: '2020-01-01', scf_id: 'TESTCONTROL-01', domain: 'TESTCONTROL' }),
  make({ id: 'b', title: 'Being fixed', status: 'in_progress', due_date: '2020-02-01', scf_id: 'OTHERCTL-02', domain: 'OTHERCTL' }),
  make({ id: 'c', title: 'Risk only gap', status: 'open', risk_code: 'R-TEST-1' }),
  make({ id: 'd', title: 'Fixed gap', status: 'completed', scf_id: 'TESTCONTROL-01', domain: 'TESTCONTROL', retest_note: 'ok' }),
  make({ id: 'e', title: 'Raised in error', status: 'cancelled', scf_id: 'OTHERCTL-02', domain: 'OTHERCTL' }),
  make({ id: 'f', title: 'Second fix', status: 'in_progress', risk_code: 'R-TEST-1' }),
  make({ id: 'g', title: 'Another closed', status: 'completed', risk_code: 'R-TEST-1', retest_note: 'ok' }),
]

/** The server's filtering, faithfully: every filter it is given applies. */
function serverList(filters: Record<string, unknown>): Deficiency[] {
  return ALL.filter(
    (d) =>
      (!filters.status || d.status === filters.status) &&
      (!filters.open_only || d.status === 'open' || d.status === 'in_progress') &&
      (!filters.domain || d.domain === filters.domain) &&
      (!filters.risk_code || d.risk_code === filters.risk_code),
  )
}

const SUMMARY = { open_total: 4, material_open_total: 1, unlinked_open: 2, by_control: [], by_domain: [] }

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <DeficienciesPage organizationId="org-one" />
      </ConfirmProvider>
    </QueryClientProvider>,
  )
}

const COLUMNS: ActionItemStatus[] = ['open', 'in_progress', 'completed', 'cancelled']
const viewButton = (name: 'List' | 'Board') => within(screen.getByRole('group', { name: 'View' })).getByRole('button', { name })
const toolbarCount = () => document.querySelector('.explorer-toolbar-count')?.textContent ?? ''

/** The board's columns and their header counts; a hidden column is absent. */
function boardCounts(): Partial<Record<ActionItemStatus, number>> {
  const out: Partial<Record<ActionItemStatus, number>> = {}
  for (const s of COLUMNS) {
    const count = screen.queryByTestId(`deficiency-column-count-${s}`)
    if (count) out[s] = Number(count.textContent)
  }
  return out
}
const boardTotal = () => Object.values(boardCounts()).reduce((a, b) => a + (b ?? 0), 0)

async function showList() {
  fireEvent.click(viewButton('List'))
  await waitFor(() => expect(screen.queryByTestId('deficiency-board')).not.toBeInTheDocument())
  await waitFor(() => expect(toolbarCount()).toMatch(/deficienc/))
}
async function showBoard() {
  fireEvent.click(viewButton('Board'))
  await screen.findByTestId('deficiency-board')
  await waitFor(() => expect(toolbarCount()).toMatch(/deficienc/))
}

/** Wait for the list to settle on the server's answer for the current filters. */
async function listTotal(expected: number) {
  await waitFor(() => expect(document.querySelectorAll('.deficiency-list > li')).toHaveLength(expected))
  return document.querySelectorAll('.deficiency-list > li').length
}

describe('DeficienciesPage: Board view (#1155)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    state.editor = true
    window.localStorage.clear()
    window.history.replaceState(null, '', '/?tab=deficiencies')
    api.listDeficiencies.mockImplementation(async (_org: string, filters: Record<string, unknown>) => serverList(filters))
    api.getDeficiencySummary.mockResolvedValue(SUMMARY)
    api.getOrgMembers.mockResolvedValue([])
    api.getRiskAssessments.mockResolvedValue([])
    api.fetchScopedControlsPage.mockResolvedValue({ controls: [], total: 0, offset: 0 })
  })
  afterEach(() => window.history.replaceState(null, '', '/'))

  it('opens on the list by default, with a List | Board toggle', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    expect(viewButton('List')).toHaveAttribute('aria-pressed', 'true')
    expect(viewButton('Board')).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByTestId('deficiency-board')).not.toBeInTheDocument()
  })

  it('writes the chosen view to the URL and to local storage', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    await showBoard()
    expect(new URLSearchParams(window.location.search).get('view')).toBe('board')
    expect(new URLSearchParams(window.location.search).get('tab')).toBe('deficiencies')
    expect(window.localStorage.getItem(DEFICIENCY_VIEW_STORAGE_KEY)).toBe('board')
    expect(viewButton('Board')).toHaveAttribute('aria-pressed', 'true')

    await showList()
    expect(new URLSearchParams(window.location.search).get('view')).toBe('list')
    expect(window.localStorage.getItem(DEFICIENCY_VIEW_STORAGE_KEY)).toBe('list')
  })

  it('opens on the board when the URL says so, whatever local storage says', async () => {
    window.localStorage.setItem(DEFICIENCY_VIEW_STORAGE_KEY, 'list')
    window.history.replaceState(null, '', '/?tab=deficiencies&view=board')
    renderPage()
    expect(await screen.findByTestId('deficiency-board')).toBeInTheDocument()
  })

  it('falls back to the viewer’s remembered view when the URL names none', async () => {
    window.localStorage.setItem(DEFICIENCY_VIEW_STORAGE_KEY, 'board')
    renderPage()
    expect(await screen.findByTestId('deficiency-board')).toBeInTheDocument()
  })

  it('follows Back and Forward between the views', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    window.history.replaceState(null, '', '/?tab=deficiencies&view=board')
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByTestId('deficiency-board')).toBeInTheDocument()
  })

  it('counts each column as the strip counts that status, with no filter set', async () => {
    window.localStorage.setItem(BOARD_COLLAPSE_STORAGE_KEY, 'false')
    renderPage()
    await screen.findByText('Overdue gap')
    await showBoard()
    await waitFor(() => expect(boardCounts()).toEqual({ open: 2, in_progress: 2, completed: 2, cancelled: 1 }))
    expect(screen.getByTestId('deficiency-stat-in-progress')).toHaveTextContent(String(boardCounts().in_progress))
    expect(screen.getByTestId('deficiency-stat-completed')).toHaveTextContent(String(boardCounts().completed))
    expect(screen.getByTestId('deficiency-stat-cancelled')).toHaveTextContent(String(boardCounts().cancelled))
  })

  it('hides columns for the Status filter, without narrowing what the board fetches', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    await showBoard()
    fireEvent.click(screen.getByRole('radio', { name: 'Completed' }))
    await waitFor(() => expect(Object.keys(boardCounts())).toEqual(['completed']))
    expect(boardCounts().completed).toBe(2)
    // The board's own query never carries the status: it hides columns instead.
    expect(api.listDeficiencies).not.toHaveBeenCalledWith('org-one', expect.objectContaining({ status: 'completed' }))

    fireEvent.click(screen.getByRole('radio', { name: 'Open or in progress' }))
    await waitFor(() => expect(Object.keys(boardCounts())).toEqual(['open', 'in_progress']))
    expect(api.listDeficiencies).not.toHaveBeenCalledWith('org-one', expect.objectContaining({ open_only: true }))
  })

  // The issue's "done when": the board and the list show the same counts for every filter.
  const FILTER_CASES: { name: string; apply: () => void }[] = [
    { name: 'no filter', apply: () => {} },
    { name: 'status Open', apply: () => fireEvent.click(screen.getByRole('radio', { name: 'Open' })) },
    { name: 'status In progress', apply: () => fireEvent.click(screen.getByRole('radio', { name: 'In progress' })) },
    { name: 'status Completed', apply: () => fireEvent.click(screen.getByRole('radio', { name: 'Completed' })) },
    { name: 'status Cancelled', apply: () => fireEvent.click(screen.getByRole('radio', { name: 'Cancelled' })) },
    { name: 'open or in progress', apply: () => fireEvent.click(screen.getByRole('radio', { name: 'Open or in progress' })) },
    { name: 'overdue only', apply: () => fireEvent.click(screen.getByRole('checkbox', { name: /Overdue only/ })) },
    { name: 'domain', apply: () => fireEvent.change(screen.getByLabelText('Domain'), { target: { value: 'OTHERCTL' } }) },
    { name: 'risk', apply: () => fireEvent.change(screen.getByLabelText('Risk'), { target: { value: 'R-TEST-1' } }) },
    { name: 'search', apply: () => fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'fix' } }) },
    {
      name: 'risk and in progress',
      apply: () => {
        fireEvent.change(screen.getByLabelText('Risk'), { target: { value: 'R-TEST-1' } })
        fireEvent.click(screen.getByRole('radio', { name: 'In progress' }))
      },
    },
    {
      name: 'domain, open or in progress, and overdue',
      apply: () => {
        fireEvent.change(screen.getByLabelText('Domain'), { target: { value: 'OTHERCTL' } })
        fireEvent.click(screen.getByRole('radio', { name: 'Open or in progress' }))
        fireEvent.click(screen.getByRole('checkbox', { name: /Overdue only/ }))
      },
    },
  ]

  /** What the filters should show, worked out from the fixture rather than the page. */
  const EXPECTED: Record<string, number> = {
    'no filter': 7,
    'status Open': 2,
    'status In progress': 2,
    'status Completed': 2,
    'status Cancelled': 1,
    'open or in progress': 4,
    'overdue only': 2,
    domain: 2,
    risk: 3,
    search: 3,
    'risk and in progress': 1,
    'domain, open or in progress, and overdue': 1,
  }

  for (const c of FILTER_CASES) {
    it(`shows the same total on the board as in the list: ${c.name}`, async () => {
      // Every column open, so every card counted is a card shown.
      window.localStorage.setItem(BOARD_COLLAPSE_STORAGE_KEY, 'false')
      renderPage()
      await screen.findByText('Overdue gap')
      await waitFor(() => expect(screen.getByLabelText('Domain').querySelectorAll('option').length).toBeGreaterThan(1))
      c.apply()
      const expected = EXPECTED[c.name]
      const inList = await listTotal(expected)
      const listCount = toolbarCount()

      await showBoard()
      await waitFor(() => expect(boardTotal()).toBe(expected))
      expect(boardTotal()).toBe(inList)
      expect(toolbarCount()).toBe(listCount)
      expect(document.querySelectorAll('[data-testid^="deficiency-card-"]')).toHaveLength(expected)

      await showList()
      expect(await listTotal(expected)).toBe(inList)
    })
  }

  it('leaves out of the toolbar count the cards a collapsed Cancelled column hides', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    expect(toolbarCount()).toBe('7 deficiencies')
    await showBoard()
    // Collapsed by default: its count shows, its card does not, and the toolbar agrees.
    expect(boardCounts().cancelled).toBe(1)
    expect(screen.queryByTestId('deficiency-card-e')).not.toBeInTheDocument()
    expect(toolbarCount()).toBe('6 deficiencies')
    expect(document.querySelectorAll('[data-testid^="deficiency-card-"]')).toHaveLength(6)

    fireEvent.click(screen.getByRole('button', { name: /Cancelled/, expanded: false }))
    expect(await screen.findByTestId('deficiency-card-e')).toBeInTheDocument()
    expect(toolbarCount()).toBe('7 deficiencies')
  })

  it('opens Cancelled when the Status filter asks for it, however it was left', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    await showBoard()
    expect(screen.queryByTestId('deficiency-card-e')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'Cancelled' }))
    expect(await screen.findByTestId('deficiency-card-e')).toBeInTheDocument()
    expect(toolbarCount()).toBe('1 deficiency')
    expect(within(screen.getByTestId('deficiency-column-cancelled')).queryByRole('button', { name: /Cancelled/ })).not.toBeInTheDocument()
  })

  it('switches views without a fetch or a Loading… flash, whatever the Status filter', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    fireEvent.click(screen.getByRole('radio', { name: 'Completed' }))
    await waitFor(() => expect(toolbarCount()).toBe('2 deficiencies'))
    const calls = api.listDeficiencies.mock.calls.length

    fireEvent.click(viewButton('Board'))
    expect(screen.queryByText('Loading deficiencies…')).not.toBeInTheDocument()
    expect(screen.getByTestId('deficiency-board')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('radio', { name: 'In progress' }))
    fireEvent.click(viewButton('List'))
    expect(screen.queryByText('Loading deficiencies…')).not.toBeInTheDocument()
    expect(document.querySelectorAll('.deficiency-list > li')).toHaveLength(2)
    expect(api.listDeficiencies.mock.calls.length).toBe(calls)
  })

  it('keeps the board, with its columns, when the filters match nothing', async () => {
    window.history.replaceState(null, '', '/?tab=deficiencies&view=board')
    renderPage()
    await screen.findByTestId('deficiency-card-a')
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'nothing like this' } })
    expect(await screen.findByText('No deficiencies match these filters.')).toBeInTheDocument()
    expect(screen.getByTestId('deficiency-board')).toBeInTheDocument()
    expect(boardCounts()).toEqual({ open: 0, in_progress: 0, completed: 0, cancelled: 0 })
  })

  it('takes the remembered view, and names it, when Back lands on a deficiencies URL with none', async () => {
    window.history.replaceState(null, '', '/?tab=deficiencies&view=list')
    window.localStorage.setItem(DEFICIENCY_VIEW_STORAGE_KEY, 'board')
    renderPage()
    await screen.findByText('Overdue gap')
    expect(screen.queryByTestId('deficiency-board')).not.toBeInTheDocument()

    window.history.replaceState(null, '', '/?tab=deficiencies')
    window.dispatchEvent(new PopStateEvent('popstate'))
    expect(await screen.findByTestId('deficiency-board')).toBeInTheDocument()
    expect(new URLSearchParams(window.location.search).get('view')).toBe('board')
  })

  it('names the view in the URL when an in-app link arrives without one', async () => {
    // App names the tab in its own effect, after the page has mounted.
    window.history.replaceState(null, '', '/?tab=risk-register')
    window.localStorage.setItem(DEFICIENCY_VIEW_STORAGE_KEY, 'board')
    renderPage()
    window.history.replaceState(null, '', '/?tab=deficiencies')
    await waitFor(() => expect(new URLSearchParams(window.location.search).get('view')).toBe('board'))
    expect(new URLSearchParams(window.location.search).get('tab')).toBe('deficiencies')
    expect(await screen.findByTestId('deficiency-board')).toBeInTheDocument()
  })

  it('leaves another page’s URL alone', async () => {
    window.history.replaceState(null, '', '/?tab=risk-register')
    renderPage()
    await screen.findByText('Overdue gap')
    expect(window.location.search).toBe('?tab=risk-register')
  })

  it('opens the completion form when a card is dropped on Completed, and leaves it where it was on Cancel', async () => {
    renderPage()
    await screen.findByText('Overdue gap')
    await showBoard()
    const card = await screen.findByTestId('deficiency-card-a')
    const data = new Map<string, string>()
    const dt = { setData: (k: string, v: string) => data.set(k, v), getData: (k: string) => data.get(k) ?? '', effectAllowed: 'all', dropEffect: 'none', types: [] }
    fireEvent.dragStart(card, { dataTransfer: dt })
    fireEvent.dragOver(screen.getByTestId('deficiency-column-completed'), { dataTransfer: dt })
    fireEvent.drop(screen.getByTestId('deficiency-column-completed'), { dataTransfer: dt })
    const dialog = await screen.findByRole('dialog', { name: 'Complete deficiency' })
    expect(within(dialog).getByRole('button', { name: 'Complete' })).toBeDisabled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(api.updateDeficiency).not.toHaveBeenCalled()
    expect(screen.getByTestId('deficiency-column-open')).toContainElement(screen.getByTestId('deficiency-card-a'))
  })

  it('shows a viewer the board without drag or Move to…', async () => {
    state.editor = false
    window.history.replaceState(null, '', '/?tab=deficiencies&view=board')
    renderPage()
    const card = await screen.findByTestId('deficiency-card-a')
    expect(card).not.toHaveAttribute('draggable')
    expect(screen.queryByRole('button', { name: /^Move / })).not.toBeInTheDocument()
  })
})
