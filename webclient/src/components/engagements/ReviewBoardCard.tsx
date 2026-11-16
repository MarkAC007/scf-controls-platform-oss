import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent } from 'react'
import {
  AI_VERDICT_LABELS,
  REVIEW_STATUS_LABELS,
  type EvidenceCard,
  type ReviewStatus,
} from '../../data/engagementReviewApi'

/**
 * One evidence object on the review board. The card is the evidence object
 * (ERL id), not a control or a clause; collection state lives on it.
 */

/** Long chip lists collapse to this many plus "+N". */
const CHIP_LIMIT = 4

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return '?'
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

/** Who has the next move when this caller has none. */
export function nextMoveText(card: EvidenceCard): string {
  switch (card.status) {
    case 'accepted':
      return 'Accepted. No further move.'
    case 'action_required':
      return 'Next move: the organisation'
    default:
      return card.reviewer ? `Next move: ${card.reviewer.name} (auditor)` : 'Next move: the auditor'
  }
}

function filesText(card: EvidenceCard): string {
  if (card.file_count === 0) return 'No files'
  return `${card.file_count} file${card.file_count === 1 ? '' : 's'} · ${card.in_window_count} in window`
}

function ChipList({ items, kind, label }: { items: string[]; kind: 'control' | 'clause'; label: string }) {
  if (items.length === 0) return null
  const shown = items.slice(0, CHIP_LIMIT)
  const rest = items.slice(CHIP_LIMIT)
  return (
    <span className="erb-chip-row">
      <span className="erb-chip-label">{label}</span>
      {shown.map(item => (
        <span key={item} className={`erb-chip erb-chip-${kind}`}>{item}</span>
      ))}
      {rest.length > 0 && (
        <span className={`erb-chip erb-chip-${kind} erb-chip-more`} title={rest.join(', ')}>
          +{rest.length}
        </span>
      )}
    </span>
  )
}

interface MoveMenuProps {
  card: EvidenceCard
  onMove: (to: ReviewStatus) => void
}

function MoveMenu({ card, onMove }: MoveMenuProps) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([])
  const targets = card.allowed_transitions
  const menuId = `erb-move-${card.review_id}`

  useEffect(() => {
    if (open) itemRefs.current[active]?.focus()
  }, [open, active])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (!menuRef.current?.contains(t) && !buttonRef.current?.contains(t)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  if (targets.length === 0) {
    return <span className="erb-next-move">{nextMoveText(card)}</span>
  }

  const close = (refocus: boolean) => {
    setOpen(false)
    if (refocus) buttonRef.current?.focus()
  }

  const openMenu = (index: number) => {
    setActive(index)
    setOpen(true)
  }

  const onButtonKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); openMenu(0) }
    if (e.key === 'ArrowUp') { e.preventDefault(); openMenu(targets.length - 1) }
  }

  const onMenuKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setActive(i => (i + 1) % targets.length) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setActive(i => (i - 1 + targets.length) % targets.length) }
    else if (e.key === 'Home') { e.preventDefault(); setActive(0) }
    else if (e.key === 'End') { e.preventDefault(); setActive(targets.length - 1) }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(true) }
    else if (e.key === 'Tab') { setOpen(false) }
  }

  const choose = (to: ReviewStatus) => {
    close(true)
    onMove(to)
  }

  return (
    <span className="erb-move">
      <button
        ref={buttonRef}
        type="button"
        className="erb-move-btn"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-label={`Move ${card.evidence_id} to…`}
        onClick={() => (open ? close(false) : openMenu(0))}
        onKeyDown={onButtonKey}
      >Move to…</button>
      {open && (
        <div
          ref={menuRef}
          id={menuId}
          role="menu"
          aria-label={`Move ${card.evidence_id} to`}
          className="erb-move-menu"
          onKeyDown={onMenuKey}
        >
          {targets.map((to, i) => (
            <button
              key={to}
              ref={el => { itemRefs.current[i] = el }}
              type="button"
              role="menuitem"
              tabIndex={i === active ? 0 : -1}
              className={`erb-move-item erb-stage-${to}`}
              onClick={() => choose(to)}
            >
              <span className="erb-move-dot" aria-hidden="true" />
              {REVIEW_STATUS_LABELS[to]}
              {(card.note_required[to] || to === 'submitted') && (
                <span className="erb-move-note"> · needs a note</span>
              )}
            </button>
          ))}
        </div>
      )}
    </span>
  )
}

interface ReviewBoardCardProps {
  card: EvidenceCard
  frameworkLabel: string
  selected: boolean
  pending: boolean
  onOpen: (card: EvidenceCard, opener: HTMLElement) => void
  onMove: (card: EvidenceCard, to: ReviewStatus) => void
  onDragStart: (card: EvidenceCard) => void
  onDragEnd: () => void
}

export default function ReviewBoardCard({
  card, frameworkLabel, selected, pending, onOpen, onMove, onDragStart, onDragEnd,
}: ReviewBoardCardProps) {
  const canDrag = card.allowed_transitions.length > 0
  const noFiles = card.file_count === 0
  const noneInWindow = card.file_count > 0 && card.in_window_count === 0

  const handleDragStart = (e: DragEvent<HTMLElement>) => {
    e.dataTransfer.setData('text/plain', card.evidence_id)
    e.dataTransfer.effectAllowed = 'move'
    onDragStart(card)
  }

  return (
    <article
      className={`erb-card${selected ? ' is-selected' : ''}${pending ? ' is-pending' : ''}`}
      aria-label={`${card.evidence_id}${card.title ? ` ${card.title}` : ''}`}
      aria-current={selected ? 'true' : undefined}
      aria-busy={pending || undefined}
      draggable={canDrag}
      onDragStart={canDrag ? handleDragStart : undefined}
      onDragEnd={canDrag ? onDragEnd : undefined}
      data-evidence-id={card.evidence_id}
    >
      <button
        type="button"
        className="erb-card-open"
        aria-pressed={selected}
        aria-label={`Open ${card.evidence_id}${card.title ? `, ${card.title}` : ''}`}
        onClick={e => onOpen(card, e.currentTarget)}
      >
        <span className="erb-card-head">
          <span className="erb-card-id">{card.evidence_id}</span>
          <span className={`erb-verdict erb-verdict-${card.ai_verdict}`}>{AI_VERDICT_LABELS[card.ai_verdict]}</span>
        </span>
        <span className="erb-card-title">{card.title ?? 'Untitled evidence object'}</span>
        <ChipList items={card.controls.map(c => c.scf_id)} kind="control" label="SCF" />
        <ChipList items={card.clauses} kind="clause" label={frameworkLabel} />
        <span className="erb-card-meta">
          <span className={`erb-files${noFiles || noneInWindow ? ' is-warn' : ''}`}>
            <svg className="erb-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d="M21.4 11.1l-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5" />
            </svg>
            {filesText(card)}
            {noneInWindow && <span className="erb-visually-hidden"> (none in the audit window)</span>}
          </span>
          {card.open_query_count > 0 && (
            <span className="erb-queries">
              <svg className="erb-icon" viewBox="0 0 24 24" aria-hidden="true">
                <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
              </svg>
              {card.open_query_count} open quer{card.open_query_count === 1 ? 'y' : 'ies'}
            </span>
          )}
        </span>
      </button>
      <div className="erb-card-foot">
        {card.reviewer ? (
          <span className="erb-avatar" title={`Reviewer: ${card.reviewer.name}`}>
            <span aria-hidden="true">{initials(card.reviewer.name)}</span>
            <span className="erb-visually-hidden">Reviewer: {card.reviewer.name}</span>
          </span>
        ) : (
          <span className="erb-unassigned">Unassigned</span>
        )}
        <span className="erb-owner" title={`Owner: ${card.owner.label}`}>Owner: {card.owner.label}</span>
        <MoveMenu card={card} onMove={to => onMove(card, to)} />
      </div>
    </article>
  )
}
