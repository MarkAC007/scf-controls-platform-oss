import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ConfirmProvider, answerConfirm, confirmClosed, findConfirm } from '../../../__tests__/confirmDialog'
import DeficiencyBoard, { BOARD_COLLAPSE_STORAGE_KEY, useCancelledCollapsed } from '../DeficiencyBoard'
import type { ActionItemStatus, Deficiency } from '../../../types'

const api = vi.hoisted(() => ({
  updateDeficiency: vi.fn(),
}))

vi.mock('../../../data/apiClient', () => api)
vi.mock('react-hot-toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }))

const USERS = [{ id: 'user-ann', email: 'ann@example.test', display_name: 'Ann' }]

const make = (over: Partial<Deficiency>): Deficiency => ({
  id: 'x',
  organization_id: 'org-one',
  title: 'x',
  priority: 'medium',
  status: 'open',
  auto_generated: false,
  source: null,
  created_at: '2026-10-01T00:00:00Z',
  updated_at: '2026-10-01T00:00:00Z',
  ...over,
})

const OPEN = make({
  id: 'd-open',
  title: 'Backups untested',
  priority: 'high',
  owner_user_id: 'user-ann',
  due_date: '2020-01-31',
  scf_id: 'TESTCONTROL-01',
  risk_code: 'R-TEST-1',
})
const DOING = make({ id: 'd-doing', title: 'Rotating keys', status: 'in_progress', due_date: '2999-01-01' })
const DONE = make({
  id: 'd-done',
  title: 'Old gap',
  status: 'completed',
  completed_date: '2026-10-02',
  retest_note: 'Restored on 2 Oct',
  source: 'capability_test',
  capability_test_id: 'ct-1',
})
const GONE = make({ id: 'd-gone', title: 'Raised in error', status: 'cancelled' })
const ALL = [OPEN, DOING, DONE, GONE]
const ALL_STATUSES: ActionItemStatus[] = ['open', 'in_progress', 'completed', 'cancelled']

function renderBoard({
  canEdit = true,
  deficiencies = ALL,
  statuses = ALL_STATUSES,
}: { canEdit?: boolean; deficiencies?: Deficiency[]; statuses?: ActionItemStatus[] } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onEdit = vi.fn()
  const onComplete = vi.fn()
  // The page owns the Cancelled column's collapse (its toolbar count needs it).
  function Board({ rows }: { rows: Deficiency[] }) {
    const [collapsed, toggle] = useCancelledCollapsed()
    return (
      <DeficiencyBoard
        organizationId="org-one"
        deficiencies={rows}
        statuses={statuses}
        users={USERS}
        canEdit={canEdit}
        cancelledCollapsed={collapsed}
        onToggleCancelled={toggle}
        onEdit={onEdit}
        onComplete={onComplete}
      />
    )
  }
  const ui = (rows: Deficiency[]) => (
    <QueryClientProvider client={client}>
      <ConfirmProvider>
        <Board rows={rows} />
        <button type="button">Elsewhere</button>
      </ConfirmProvider>
    </QueryClientProvider>
  )
  const view = render(ui(deficiencies))
  return { onEdit, onComplete, rerender: (rows: Deficiency[]) => view.rerender(ui(rows)) }
}

const column = (status: ActionItemStatus) => screen.getByTestId(`deficiency-column-${status}`)
const card = (id: string) => screen.getByTestId(`deficiency-card-${id}`)

/** A DataTransfer stand-in: jsdom has none, and drag events need one. */
function dataTransfer() {
  const data = new Map<string, string>()
  return {
    data,
    dropEffect: 'none',
    effectAllowed: 'all',
    setData: (type: string, value: string) => data.set(type, value),
    getData: (type: string) => data.get(type) ?? '',
    clearData: () => data.clear(),
    types: [] as string[],
  }
}

function drag(id: string, to: ActionItemStatus) {
  const dt = dataTransfer()
  fireEvent.dragStart(card(id), { dataTransfer: dt })
  fireEvent.dragOver(column(to), { dataTransfer: dt })
  fireEvent.drop(column(to), { dataTransfer: dt })
  fireEvent.dragEnd(card(id), { dataTransfer: dt })
  return dt
}

async function moveWithMenu(id: string, to: string) {
  fireEvent.click(within(card(id)).getByRole('button', { name: /^Move ".*" to…$/ }))
  const menu = await screen.findByRole('menu')
  fireEvent.click(within(menu).getByRole('menuitem', { name: to }))
}

describe('DeficiencyBoard (#1155)', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    window.localStorage.clear()
  })
  afterEach(() => vi.restoreAllMocks())

  it('shows one column per status, each holding its own cards, with a count', () => {
    renderBoard()
    for (const [status, label, ids] of [
      ['open', 'Open', ['d-open']],
      ['in_progress', 'In progress', ['d-doing']],
      ['completed', 'Completed', ['d-done']],
      ['cancelled', 'Cancelled', ['d-gone']],
    ] as const) {
      const col = column(status)
      expect(col).toHaveAccessibleName(`${label}, 1 deficiency`)
      expect(within(col).getByTestId(`deficiency-column-count-${status}`)).toHaveTextContent('1')
      for (const id of ids) {
        if (status !== 'cancelled') expect(col).toContainElement(card(id))
      }
    }
  })

  it('shows on a card its title, priority, owner, due date, overdue flag, subject and source link', () => {
    renderBoard()
    const open = within(card('d-open'))
    expect(open.getByText('Backups untested')).toBeInTheDocument()
    expect(open.getByText('High')).toBeInTheDocument()
    expect(open.getByText('Owner: Ann')).toBeInTheDocument()
    expect(open.getByText(/^Due: /)).toBeInTheDocument()
    expect(open.getByText('Overdue')).toBeInTheDocument()
    expect(open.getByRole('link', { name: 'TESTCONTROL-01' })).toHaveAttribute('href', expect.stringContaining('item=TESTCONTROL-01'))
    expect(open.getByRole('link', { name: 'R-TEST-1' })).toHaveAttribute('href', expect.stringContaining('risk=R-TEST-1'))
    expect(open.getByText('Raised by hand')).toBeInTheDocument()
    expect(within(card('d-doing')).queryByText('Overdue')).not.toBeInTheDocument()
    const link = within(card('d-done')).getByRole('link', { name: /open its test/ })
    expect(new URL(link.getAttribute('href')!, 'http://x').searchParams.get('test')).toBe('ct-1')
  })

  it('collapses the Cancelled column by default, showing its count, and remembers the choice', () => {
    renderBoard()
    const toggle = within(column('cancelled')).getByRole('button', { name: /Cancelled/ })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(toggle).toHaveTextContent('1')
    expect(screen.queryByTestId('deficiency-card-d-gone')).not.toBeInTheDocument()

    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(column('cancelled')).toContainElement(card('d-gone'))
    expect(window.localStorage.getItem(BOARD_COLLAPSE_STORAGE_KEY)).toBe('false')
  })

  it('opens the Cancelled column when the viewer last left it open', () => {
    window.localStorage.setItem(BOARD_COLLAPSE_STORAGE_KEY, 'false')
    renderBoard()
    expect(column('cancelled')).toContainElement(card('d-gone'))
  })

  it('shows only the columns it is given (the Status filter hides columns)', () => {
    renderBoard({ statuses: ['open', 'in_progress'] })
    expect(column('open')).toBeInTheDocument()
    expect(column('in_progress')).toBeInTheDocument()
    expect(screen.queryByTestId('deficiency-column-completed')).not.toBeInTheDocument()
    expect(screen.queryByTestId('deficiency-column-cancelled')).not.toBeInTheDocument()
  })

  it('moves Open to In progress from the Move to… menu with one PATCH and no dialog', async () => {
    api.updateDeficiency.mockResolvedValue({ ...OPEN, status: 'in_progress' })
    renderBoard()
    await moveWithMenu('d-open', 'In progress')
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledTimes(1))
    expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'd-open', { status: 'in_progress' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('moves In progress back to Open by dragging, with one PATCH and no dialog', async () => {
    api.updateDeficiency.mockResolvedValue({ ...DOING, status: 'open' })
    renderBoard()
    drag('d-doing', 'open')
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledTimes(1))
    expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'd-doing', { status: 'open' })
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('does nothing when a card is dropped on its own column', async () => {
    renderBoard()
    drag('d-open', 'open')
    await act(async () => {})
    expect(api.updateDeficiency).not.toHaveBeenCalled()
  })

  it('hands a card dropped on Completed to the completion form and sends nothing itself', async () => {
    const { onComplete } = renderBoard()
    drag('d-open', 'completed')
    await waitFor(() => expect(onComplete).toHaveBeenCalledWith(OPEN))
    expect(api.updateDeficiency).not.toHaveBeenCalled()
    // Still where it was: nothing moved without a retest note.
    expect(column('open')).toContainElement(card('d-open'))
  })

  it('hands a card moved to Completed from the menu to the completion form', async () => {
    const { onComplete } = renderBoard()
    await moveWithMenu('d-doing', 'Completed')
    expect(onComplete).toHaveBeenCalledWith(DOING)
    expect(api.updateDeficiency).not.toHaveBeenCalled()
  })

  it('cancels only after the same confirmation the list asks, word for word', async () => {
    api.updateDeficiency.mockResolvedValue({ ...OPEN, status: 'cancelled' })
    renderBoard()
    drag('d-open', 'cancelled')
    const open = await findConfirm()
    expect(open.title).toBe('Cancel deficiency')
    expect(open.message).toBe(
      `Cancel "${OPEN.title}"? It stops counting as an open deficiency. Deficiencies are never deleted; it can be reopened.`,
    )
    expect(within(open.dialog).getByRole('button', { name: 'Keep it' })).toBeInTheDocument()
    open.cancel()
    await confirmClosed()
    expect(api.updateDeficiency).not.toHaveBeenCalled()
    expect(column('open')).toContainElement(card('d-open'))

    await moveWithMenu('d-open', 'Cancelled')
    await answerConfirm(true)
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'd-open', { status: 'cancelled' }))
    expect(api.updateDeficiency).toHaveBeenCalledTimes(1)
  })

  it('reopens a completed card only after the reopen warning, then sends the column it was moved to', async () => {
    api.updateDeficiency.mockResolvedValue({ ...DONE, status: 'in_progress' })
    renderBoard()
    drag('d-done', 'in_progress')
    const open = await findConfirm()
    expect(open.title).toBe('Reopen deficiency')
    expect(open.message).toMatch(/retest note and completion date are cleared/)
    open.cancel()
    await confirmClosed()
    expect(api.updateDeficiency).not.toHaveBeenCalled()

    drag('d-done', 'in_progress')
    await answerConfirm(true)
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'd-done', { status: 'in_progress' }))
  })

  it('reopens a cancelled card with the reopen confirmation', async () => {
    window.localStorage.setItem(BOARD_COLLAPSE_STORAGE_KEY, 'false')
    api.updateDeficiency.mockResolvedValue({ ...GONE, status: 'open' })
    renderBoard()
    await moveWithMenu('d-gone', 'Open')
    const message = await answerConfirm(true)
    expect(message).toBe(`Reopen "${GONE.title}"? It counts as an open deficiency again.`)
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledWith('org-one', 'd-gone', { status: 'open' }))
  })

  it('shows a refused write on its card, which stays in its column, until the card changes', async () => {
    api.updateDeficiency.mockRejectedValue(new Error('Insufficient permissions'))
    const { rerender } = renderBoard()
    drag('d-open', 'in_progress')
    expect(await within(card('d-open')).findByRole('alert')).toHaveTextContent('Insufficient permissions')
    expect(column('open')).toContainElement(card('d-open'))
    expect(within(card('d-doing')).queryByRole('alert')).not.toBeInTheDocument()

    rerender([{ ...OPEN, title: 'Backups untested, renamed', updated_at: '2026-10-03T00:00:00Z' }, DOING, DONE, GONE])
    expect(within(card('d-open')).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('lets a read-only viewer see the board but not drag or move a card', () => {
    renderBoard({ canEdit: false })
    expect(card('d-open')).toBeInTheDocument()
    for (const id of ['d-open', 'd-doing', 'd-done']) {
      expect(card(id)).not.toHaveAttribute('draggable')
    }
    expect(screen.queryByRole('button', { name: /^Move / })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /^Edit/ })).not.toBeInTheDocument()
    // A drop is refused: the column does not accept it, and nothing is sent.
    const dt = dataTransfer()
    dt.setData('text/plain', 'd-open')
    const over = fireEvent.dragOver(column('in_progress'), { dataTransfer: dt })
    expect(over).toBe(true) // not prevented: the browser shows "no drop"
    fireEvent.drop(column('in_progress'), { dataTransfer: dt })
    expect(api.updateDeficiency).not.toHaveBeenCalled()
  })

  it('makes an editor’s cards draggable, carrying the card id', () => {
    renderBoard()
    expect(card('d-open')).toHaveAttribute('draggable', 'true')
    const dt = dataTransfer()
    fireEvent.dragStart(card('d-open'), { dataTransfer: dt })
    expect(dt.data.get('text/plain')).toBe('d-open')
    const over = fireEvent.dragOver(column('in_progress'), { dataTransfer: dt })
    expect(over).toBe(false) // prevented: the column accepts the drop
    expect(column('in_progress')).toHaveClass('deficiency-column--drop')
  })

  it('offers in the Move to… menu every other status, operable from the keyboard', async () => {
    renderBoard()
    const trigger = within(card('d-open')).getByRole('button', { name: 'Move "Backups untested" to…' })
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu')
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(trigger)
    const menu = screen.getByRole('menu')
    const items = within(menu).getAllByRole('menuitem')
    expect(items.map((i) => i.textContent)).toEqual(['In progress', 'Completed', 'Cancelled'])
    expect(trigger).toHaveAttribute('aria-expanded', 'true')
    await waitFor(() => expect(items[0]).toHaveFocus())

    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(items[1]).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'ArrowUp' })
    expect(items[2]).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'Home' })
    expect(items[0]).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'End' })
    expect(items[2]).toHaveFocus()

    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    expect(api.updateDeficiency).not.toHaveBeenCalled()
  })

  it('hands a card to the edit form from its Edit button', () => {
    const { onEdit } = renderBoard()
    fireEvent.click(within(card('d-doing')).getByRole('button', { name: 'Edit "Rotating keys"' }))
    expect(onEdit).toHaveBeenCalledWith(DOING)
  })

  // Review finding 1: a Cancelled-only board must show its cards.
  it('shows the Cancelled column open when it is the only column, whatever the viewer left it as', () => {
    renderBoard({ statuses: ['cancelled'] })
    expect(column('cancelled')).toContainElement(card('d-gone'))
    expect(within(column('cancelled')).queryByRole('button', { name: /Cancelled/ })).not.toBeInTheDocument()
  })

  // Review finding 2: a keyboard move must not drop focus to the page.
  it('puts focus back on the card in its new column after a Move to… move', async () => {
    api.updateDeficiency.mockResolvedValue({ ...OPEN, status: 'in_progress' })
    const { rerender } = renderBoard()
    await moveWithMenu('d-open', 'In progress')
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledTimes(1))
    // The refetch lands: the card is now in In progress.
    rerender([{ ...OPEN, status: 'in_progress', updated_at: '2026-10-05T00:00:00Z' }, DOING, DONE, GONE])
    await waitFor(() => expect(card('d-open')).toHaveFocus())
    expect(column('in_progress')).toContainElement(card('d-open'))
  })

  it('puts focus on the target column when the moved card is no longer shown there', async () => {
    api.updateDeficiency.mockResolvedValue({ ...OPEN, status: 'in_progress' })
    const { rerender } = renderBoard()
    await moveWithMenu('d-open', 'In progress')
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledTimes(1))
    // The refetch lands without it (a search, say, no longer matches it).
    rerender([DOING, DONE, GONE])
    await waitFor(() => expect(within(column('in_progress')).getByRole('heading', { name: 'In progress' })).toHaveFocus())
  })

  // Review finding 3: two moves at once keep their own state.
  it('keeps two cards’ moves apart: each its own busy state and its own refusal', async () => {
    const settle: Record<string, { ok: () => void; fail: (e: Error) => void }> = {}
    api.updateDeficiency.mockImplementation(
      (_org: string, id: string) =>
        new Promise((resolve, reject) => {
          settle[id] = { ok: () => resolve({}), fail: reject }
        }),
    )
    renderBoard()
    const moveButton = (id: string) => within(card(id)).getByRole('button', { name: /^Move ".*" to…$/ })
    await moveWithMenu('d-open', 'In progress')
    await moveWithMenu('d-doing', 'Open')
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledTimes(2))
    expect(moveButton('d-open')).toBeDisabled()
    expect(moveButton('d-doing')).toBeDisabled()

    await act(async () => settle['d-doing'].ok())
    expect(moveButton('d-doing')).toBeEnabled()
    expect(moveButton('d-open')).toBeDisabled()

    await act(async () => settle['d-open'].fail(new Error('Insufficient permissions')))
    expect(await within(card('d-open')).findByRole('alert')).toHaveTextContent('Insufficient permissions')

    // A later move of another card leaves this card's refusal alone.
    await moveWithMenu('d-doing', 'Open')
    await waitFor(() => expect(api.updateDeficiency).toHaveBeenCalledTimes(3))
    expect(within(card('d-open')).getByRole('alert')).toHaveTextContent('Insufficient permissions')
  })

  // Review finding 7: the row can change while the question is open.
  it('shows a refusal on a card that changed while its question was open', async () => {
    api.updateDeficiency.mockRejectedValue(new Error('This finding cannot be cancelled'))
    const { rerender } = renderBoard()
    await moveWithMenu('d-open', 'Cancelled')
    const open = await findConfirm()
    // Edited elsewhere meanwhile: the refetch brings a newer version.
    rerender([{ ...OPEN, title: 'Backups untested', updated_at: '2026-10-04T00:00:00Z' }, DOING, DONE, GONE])
    open.accept()
    await confirmClosed()
    expect(await within(card('d-open')).findByRole('alert')).toHaveTextContent('cannot be cancelled')
  })

  // Review finding 5: the menu button's keyboard and pointer contract.
  it('opens the Move to… menu on ArrowDown from its button, and closes it on Escape from the button', async () => {
    renderBoard()
    const trigger = within(card('d-open')).getByRole('button', { name: 'Move "Backups untested" to…' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const items = within(screen.getByRole('menu')).getAllByRole('menuitem')
    await waitFor(() => expect(items[0]).toHaveFocus())

    trigger.focus()
    fireEvent.keyDown(trigger, { key: 'Escape' })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
  })

  it('keeps focus where it is in an open Move to… menu when the board re-renders', async () => {
    const { rerender } = renderBoard()
    fireEvent.click(within(card('d-open')).getByRole('button', { name: /^Move / }))
    const items = within(screen.getByRole('menu')).getAllByRole('menuitem')
    await waitFor(() => expect(items[0]).toHaveFocus())
    fireEvent.keyDown(items[0], { key: 'ArrowDown' })
    expect(items[1]).toHaveFocus()
    // A refetch lands: same rows, a new array.
    rerender([...ALL])
    expect(screen.getByRole('menu')).toBeInTheDocument()
    expect(items[1]).toHaveFocus()
  })

  it('keeps only one Move to… menu open at a time', () => {
    renderBoard()
    fireEvent.click(within(card('d-open')).getByRole('button', { name: /^Move / }))
    // Focus gone to nowhere (as after a click Safari does not focus): the first
    // menu's focusout cannot be what closes it, only the board's one open id.
    act(() => (document.activeElement as HTMLElement).blur())
    expect(screen.getAllByRole('menu')).toHaveLength(1)
    fireEvent.click(within(card('d-doing')).getByRole('button', { name: /^Move / }))
    expect(screen.getAllByRole('menu')).toHaveLength(1)
    expect(within(card('d-doing')).getByRole('menu')).toBeInTheDocument()
  })

  it('closes the Move to… menu when focus or a pointer leaves it', async () => {
    renderBoard()
    const elsewhere = screen.getByRole('button', { name: 'Elsewhere' })
    fireEvent.click(within(card('d-open')).getByRole('button', { name: /^Move / }))
    const first = within(screen.getByRole('menu')).getAllByRole('menuitem')[0]
    await waitFor(() => expect(first).toHaveFocus())
    fireEvent.blur(first, { relatedTarget: elsewhere })
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()

    fireEvent.click(within(card('d-open')).getByRole('button', { name: /^Move / }))
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.pointerDown(elsewhere)
    expect(screen.queryByRole('menu')).not.toBeInTheDocument()
  })

  // Review finding 9: WebKit gives dragleave no relatedTarget.
  it('keeps a column highlighted while the drag crosses its cards', () => {
    renderBoard()
    const dt = dataTransfer()
    fireEvent.dragStart(card('d-open'), { dataTransfer: dt })
    fireEvent.dragEnter(column('in_progress'), { dataTransfer: dt })
    fireEvent.dragEnter(card('d-doing'), { dataTransfer: dt })
    fireEvent.dragLeave(column('in_progress'), { dataTransfer: dt, relatedTarget: null })
    expect(column('in_progress')).toHaveClass('deficiency-column--drop')
    fireEvent.dragLeave(card('d-doing'), { dataTransfer: dt, relatedTarget: null })
    expect(column('in_progress')).not.toHaveClass('deficiency-column--drop')
  })
})
