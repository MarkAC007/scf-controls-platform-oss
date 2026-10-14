/**
 * DeficiencyBoard — the deficiencies as a Kanban board, one column per
 * status (#1155, PIG R8). The Deficiencies page shows it beside the list.
 *
 * - A move follows the lifecycle (`deficiencyMove`), whether it is a drag or
 *   a choice from the card's Move to… menu: Open ↔ In progress is one write;
 *   Completed opens the completion form, which needs a retest note; Cancelled
 *   and any reopen are asked first, in the list's own words.
 * - Nothing moves before the server agrees. A card stays in its column until
 *   the refetch lands, and a refused write shows on the card.
 * - Dragging is native HTML5 drag and drop, for editors only. The Move to…
 *   menu is the keyboard and screen-reader path, so no move needs a drag.
 * - The page decides which cards and which columns: the Status filter hides
 *   columns here rather than narrowing the cards, so each column's count is
 *   the count for that status.
 * - Cancelled is collapsed by default (cancelled deficiencies no longer
 *   count); whether it is open is the viewer's own, kept in local storage.
 *   The page holds that choice (`useCancelledCollapsed`), because its count
 *   leaves out the cards a collapsed column hides. A Cancelled column that is
 *   the only one shown is never collapsed: the filter asked for it.
 * - After a move from the Move to… menu, focus follows the card to its new
 *   column (or lands on that column when the card is no longer shown), so a
 *   keyboard user is not dropped back to the top of the page.
 */
import {
  useEffect,
  useId,
  useRef,
  useState,
  type DragEvent,
  type FocusEvent,
  type JSX,
  type KeyboardEvent,
} from 'react'
import { isOverdue, localIsoDate } from '../../hooks/useDeficiencies'
import { formatAuditDate } from '../../utils/auditValue'
import type { ActionItemStatus, Deficiency, UserSimple } from '../../types'
import AppLink from '../AppLink'
import { useDeficiencyMove } from './deficiencyMove'
import { PRIORITY_LABELS, STATUSES, STATUS_LABELS, ownerLabel, sourceLabel } from './deficiencyText'

/** Local storage: whether this viewer keeps the Cancelled column collapsed. */
export const BOARD_COLLAPSE_STORAGE_KEY = 'scf.deficiencies.board.cancelledCollapsed'

/** A per-browser convenience: any storage failure just means the default. */
function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(BOARD_COLLAPSE_STORAGE_KEY) !== 'false'
  } catch {
    return true
  }
}

function writeCollapsed(collapsed: boolean) {
  try {
    window.localStorage.setItem(BOARD_COLLAPSE_STORAGE_KEY, String(collapsed))
  } catch {
    // Private mode or storage full: the choice lasts this visit only.
  }
}

/** The viewer's Cancelled-column choice, remembered: `[collapsed, toggle]`. */
export function useCancelledCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(readCollapsed)
  const toggle = () =>
    setCollapsed((c) => {
      writeCollapsed(!c)
      return !c
    })
  return [collapsed, toggle]
}

/**
 * Whether a column shows only its header. Only Cancelled collapses, and only
 * beside other columns: filtered to Cancelled alone, its cards are the point.
 */
export function isColumnCollapsed(
  status: ActionItemStatus,
  statuses: readonly ActionItemStatus[],
  cancelledCollapsed: boolean,
): boolean {
  return status === 'cancelled' && statuses.length > 1 && cancelledCollapsed
}

const countLabel = (n: number) => `${n} deficienc${n === 1 ? 'y' : 'ies'}`

interface Props {
  organizationId: string
  /** The cards, of any status, already narrowed by every filter but Status. */
  deficiencies: Deficiency[]
  /** The columns to show, in order: the Status filter's choice. */
  statuses: ActionItemStatus[]
  /** Undefined while the members are not known. */
  users: UserSimple[] | undefined
  canEdit: boolean
  /** The viewer's choice for the Cancelled column, from `useCancelledCollapsed`. */
  cancelledCollapsed: boolean
  onToggleCancelled: () => void
  onEdit: (d: Deficiency) => void
  onComplete: (d: Deficiency) => void
}

/** Where focus goes once a menu move's refetch lands. */
interface PendingFocus {
  id: string
  status: ActionItemStatus
  /** The cards as they were when the write returned: a change means the refetch landed. */
  rows: Deficiency[]
}

export default function DeficiencyBoard({
  organizationId,
  deficiencies,
  statuses,
  users,
  canEdit,
  cancelledCollapsed,
  onToggleCancelled,
  onEdit,
  onComplete,
}: Props): JSX.Element {
  const { move, isBusy, errorFor, clearError } = useDeficiencyMove(organizationId, deficiencies, onComplete)
  const [dragging, setDragging] = useState<Deficiency | null>(null)
  const [dropTarget, setDropTarget] = useState<ActionItemStatus | null>(null)
  // dragenter/dragleave fire for every child crossed, and WebKit gives
  // dragleave no relatedTarget: count the entries per column instead.
  const dragDepth = useRef(new Map<ActionItemStatus, number>())
  const [openMenuId, setOpenMenuId] = useState<string | null>(null)
  const [pendingFocus, setPendingFocus] = useState<PendingFocus | null>(null)
  const boardRef = useRef<HTMLDivElement>(null)
  const today = localIsoDate()

  useEffect(() => {
    if (!pendingFocus) return
    const board = boardRef.current
    const col = board?.querySelector<HTMLElement>(`[data-status="${pendingFocus.status}"]`)
    const moved = col?.querySelector<HTMLElement>(`[data-id="${CSS.escape(pendingFocus.id)}"]`)
    if (moved) {
      moved.focus()
      setPendingFocus(null)
      return
    }
    // Not there yet: wait for the refetch, unless it has already landed.
    if (deficiencies === pendingFocus.rows) return
    col?.querySelector<HTMLElement>('[data-column-focus]')?.focus()
    setPendingFocus(null)
  }, [pendingFocus, deficiencies])

  const moveFromMenu = async (d: Deficiency, target: ActionItemStatus) => {
    if (await move(d, target)) setPendingFocus({ id: d.id, status: target, rows: deficiencies })
  }

  const endDrag = () => {
    dragDepth.current.clear()
    setDragging(null)
    setDropTarget(null)
  }

  // A column takes a drop only from an editor, only of one of this board's
  // cards, and only into a column the card is not already in.
  const accepts = (status: ActionItemStatus) => canEdit && dragging !== null && dragging.status !== status

  const onDragEnter = (status: ActionItemStatus) => (e: DragEvent<HTMLElement>) => {
    if (!accepts(status)) return
    e.preventDefault()
    dragDepth.current.set(status, (dragDepth.current.get(status) ?? 0) + 1)
    if (dropTarget !== status) setDropTarget(status)
  }

  const onDragOver = (status: ActionItemStatus) => (e: DragEvent<HTMLElement>) => {
    if (!accepts(status)) return
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (dropTarget !== status) setDropTarget(status)
  }

  const onDragLeave = (status: ActionItemStatus) => () => {
    const depth = Math.max(0, (dragDepth.current.get(status) ?? 0) - 1)
    dragDepth.current.set(status, depth)
    if (depth === 0 && dropTarget === status) setDropTarget(null)
  }

  const onDrop = (status: ActionItemStatus) => (e: DragEvent<HTMLElement>) => {
    if (!canEdit) return
    e.preventDefault()
    const id = dragging?.id ?? e.dataTransfer.getData('text/plain')
    endDrag()
    const d = deficiencies.find((x) => x.id === id)
    if (d) void move(d, status)
  }

  return (
    <div ref={boardRef} className="deficiency-board" data-testid="deficiency-board" role="group" aria-label="Deficiencies by status">
      {statuses.map((status) => {
        const cards = deficiencies.filter((d) => d.status === status)
        // The toggle shows whenever the column could collapse; it is moot alone.
        const collapsible = status === 'cancelled' && statuses.length > 1
        const collapsed = isColumnCollapsed(status, statuses, cancelledCollapsed)
        const classes = [
          'deficiency-column',
          `deficiency-column--${status}`,
          collapsed && 'deficiency-column--collapsed',
          dropTarget === status && 'deficiency-column--drop',
        ]
          .filter(Boolean)
          .join(' ')
        const count = (
          <span className="deficiency-column-count" data-testid={`deficiency-column-count-${status}`}>
            {cards.length}
          </span>
        )
        return (
          <section
            key={status}
            className={classes}
            data-testid={`deficiency-column-${status}`}
            data-status={status}
            aria-label={`${STATUS_LABELS[status]}, ${countLabel(cards.length)}`}
            onDragEnter={onDragEnter(status)}
            onDragOver={onDragOver(status)}
            onDragLeave={onDragLeave(status)}
            onDrop={onDrop(status)}
          >
            <header className="deficiency-column-head">
              {collapsible ? (
                <button
                  type="button"
                  className="deficiency-column-toggle"
                  aria-expanded={!collapsed}
                  onClick={onToggleCancelled}
                  data-column-focus
                >
                  <span aria-hidden="true" className="deficiency-column-caret">{collapsed ? '▸' : '▾'}</span>
                  <span className="deficiency-column-title">{STATUS_LABELS[status]}</span>
                  {count}
                </button>
              ) : (
                <>
                  <h3 className="deficiency-column-title" tabIndex={-1} data-column-focus>
                    {STATUS_LABELS[status]}
                  </h3>
                  {count}
                </>
              )}
            </header>
            {collapsed ? (
              <p className="deficiency-column-note">No longer counted.</p>
            ) : cards.length === 0 ? (
              <p className="deficiency-column-note">None</p>
            ) : (
              <ul className="deficiency-column-cards">
                {cards.map((d) => {
                  const busy = isBusy(d)
                  const error = errorFor(d)
                  return (
                    <li
                      key={d.id}
                      className={`deficiency-card${dragging?.id === d.id ? ' deficiency-card--dragging' : ''}`}
                      data-testid={`deficiency-card-${d.id}`}
                      data-id={d.id}
                      tabIndex={-1}
                      draggable={canEdit && !busy ? true : undefined}
                      aria-busy={busy || undefined}
                      onDragStart={
                        canEdit
                          ? (e) => {
                              e.dataTransfer.setData('text/plain', d.id)
                              e.dataTransfer.effectAllowed = 'move'
                              setDragging(d)
                            }
                          : undefined
                      }
                      onDragEnd={canEdit ? endDrag : undefined}
                    >
                      <span className="deficiency-card-title">{d.title}</span>
                      <div className="deficiency-card-chips">
                        <span className={`deficiency-chip deficiency-chip--${d.priority}`}>{PRIORITY_LABELS[d.priority]}</span>
                        {isOverdue(d, today) && <span className="deficiency-chip deficiency-chip--overdue">Overdue</span>}
                      </div>
                      <div className="deficiency-card-meta">
                        {d.scf_id && (
                          <span>
                            Control{' '}
                            <AppLink to={{ kind: 'control', id: d.scf_id }} className="deficiency-subject-id">
                              {d.scf_id}
                            </AppLink>
                          </span>
                        )}
                        {d.risk_code && (
                          <span>
                            Risk{' '}
                            <AppLink to={{ kind: 'risk', id: d.risk_code }} className="deficiency-subject-id">
                              {d.risk_code}
                            </AppLink>
                          </span>
                        )}
                        <span>Owner: {ownerLabel(d, users)}</span>
                        <span>Due: {d.due_date ? formatAuditDate(d.due_date) : 'none'}</span>
                        {d.completed_date && <span>Completed {formatAuditDate(d.completed_date)}</span>}
                        {d.capability_test_id ? (
                          <AppLink
                            to={{ kind: 'capability-test', id: d.capability_test_id }}
                            aria-label={`${sourceLabel(d)} "${d.title}": open its test`}
                          >
                            {sourceLabel(d)}
                          </AppLink>
                        ) : (
                          <span>{sourceLabel(d)}</span>
                        )}
                      </div>
                      {canEdit && (
                        <div className="deficiency-card-actions">
                          <button
                            type="button"
                            className="btn-secondary btn-small"
                            onClick={() => {
                              clearError(d)
                              onEdit(d)
                            }}
                            disabled={busy}
                            aria-label={`Edit "${d.title}"`}
                          >
                            Edit
                          </button>
                          <MoveMenu
                            deficiency={d}
                            disabled={busy}
                            open={openMenuId === d.id}
                            onOpenChange={(open) => setOpenMenuId(open ? d.id : null)}
                            onMove={(target) => void moveFromMenu(d, target)}
                          />
                        </div>
                      )}
                      {error && (
                        <div role="alert" className="gov-error">
                          {error}
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </section>
        )
      })}
    </div>
  )
}

/**
 * The card's Move to… menu: every other status. A menu button. Enter, Space
 * or ArrowDown opens it on its first item (ArrowUp on its last); the arrow
 * keys, Home and End move; Escape, from the menu or the button, closes it
 * back onto the button. Tab, focus moving elsewhere, or a pointer pressed
 * outside close it. The board keeps one open at a time.
 */
function MoveMenu({
  deficiency,
  disabled,
  open,
  onOpenChange,
  onMove,
}: {
  deficiency: Deficiency
  disabled: boolean
  open: boolean
  onOpenChange: (open: boolean) => void
  onMove: (target: ActionItemStatus) => void
}): JSX.Element {
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const focusOnOpen = useRef<'first' | 'last'>('first')
  const menuId = useId()
  const targets = STATUSES.filter((s) => s !== deficiency.status)

  // The board passes a new callback every render; read the latest from a ref
  // so the effects below run on opening only, not on every refetch.
  const onOpenChangeRef = useRef(onOpenChange)
  useEffect(() => {
    onOpenChangeRef.current = onOpenChange
  })

  // Focus lands in the menu when it opens, and only then: a re-render while
  // it is open must not pull the viewer back to its first item.
  useEffect(() => {
    if (!open) return
    const items = itemRefs.current.filter((x): x is HTMLButtonElement => x !== null)
    items[focusOnOpen.current === 'last' ? items.length - 1 : 0]?.focus()
    focusOnOpen.current = 'first'
    // pointerdown, not mousedown: a tap outside closes it too.
    const onPointerDown = (e: PointerEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) onOpenChangeRef.current(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const close = () => {
    onOpenChange(false)
    triggerRef.current?.focus()
  }

  const openOn = (where: 'first' | 'last') => {
    focusOnOpen.current = where
    onOpenChange(true)
  }

  // Focus moved to something outside the menu: close. A null relatedTarget
  // (focus to nowhere, or a click Safari does not focus) is left to the
  // pointerdown listener, or a click on an item would close it first.
  const onBlur = (e: FocusEvent<HTMLDivElement>) => {
    const to = e.relatedTarget as Node | null
    if (open && to && !e.currentTarget.contains(to)) onOpenChange(false)
  }

  const onTriggerKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      openOn(e.key === 'ArrowUp' ? 'last' : 'first')
    } else if (e.key === 'Escape' && open) {
      e.preventDefault()
      e.stopPropagation()
      close()
    }
  }

  const onMenuKeyDown = (e: KeyboardEvent<HTMLUListElement>) => {
    const items = itemRefs.current.filter((x): x is HTMLButtonElement => x !== null)
    const at = items.indexOf(document.activeElement as HTMLButtonElement)
    const focus = (i: number) => items[(i + items.length) % items.length]?.focus()
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        focus(at + 1)
        break
      case 'ArrowUp':
        e.preventDefault()
        focus(at - 1)
        break
      case 'Home':
        e.preventDefault()
        focus(0)
        break
      case 'End':
        e.preventDefault()
        focus(items.length - 1)
        break
      case 'Escape':
        e.preventDefault()
        e.stopPropagation()
        close()
        break
      case 'Tab':
        onOpenChange(false)
        break
    }
  }

  return (
    <div className="deficiency-move" ref={wrapRef} onBlur={onBlur}>
      <button
        ref={triggerRef}
        type="button"
        className="btn-secondary btn-small"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Move "${deficiency.title}" to…`}
        disabled={disabled}
        onClick={() => (open ? onOpenChange(false) : openOn('first'))}
        onKeyDown={onTriggerKeyDown}
      >
        Move to…
      </button>
      {open && (
        <ul
          id={menuId}
          role="menu"
          aria-label={`Move "${deficiency.title}" to`}
          className="deficiency-move-menu"
          onKeyDown={onMenuKeyDown}
        >
          {targets.map((status, i) => (
            <li key={status} role="none">
              <button
                ref={(el) => {
                  itemRefs.current[i] = el
                }}
                type="button"
                role="menuitem"
                tabIndex={-1}
                className="deficiency-move-item"
                onClick={() => {
                  close()
                  onMove(status)
                }}
              >
                {STATUS_LABELS[status]}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
