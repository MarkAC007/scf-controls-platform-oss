import type { CSSProperties, JSX, KeyboardEvent, ReactNode } from 'react'

export interface ExplorerListRowProps {
  monoId?: string
  title: string
  description?: string
  accent?: boolean
  highlighted?: boolean
  onClick?: () => void
  children?: ReactNode
}

export default function ExplorerListRow({
  monoId,
  title,
  description,
  accent = false,
  highlighted = false,
  onClick,
  children,
}: ExplorerListRowProps): JSX.Element {
  function handleKeyDown(e: KeyboardEvent<HTMLDivElement>): void {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      onClick?.()
    }
  }

  const interactive = onClick !== undefined

  return (
    <div
      className={`explorer-row${highlighted ? ' explorer-row--highlighted' : ''}`}
      role={interactive ? 'button' : undefined}
      tabIndex={interactive ? 0 : undefined}
      onClick={interactive ? onClick : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
    >
      <div
        className={`explorer-row-tick${accent ? ' explorer-row-tick--accent' : ''}`}
        aria-hidden="true"
      />
      {monoId !== undefined && <div className="explorer-row-id">{monoId}</div>}
      <div className="explorer-row-body">
        <div className="explorer-row-title">{title}</div>
        {description !== undefined && (
          <div className="explorer-row-desc">{description}</div>
        )}
      </div>
      {children}
    </div>
  )
}

export function RowChip({ children }: { children: ReactNode }): JSX.Element {
  return <div className="explorer-row-chip">{children}</div>
}

export function RowMeta({
  children,
  width,
}: {
  children: ReactNode
  width?: number
}): JSX.Element {
  const style: CSSProperties = width !== undefined ? { width: `${width}px` } : {}
  return (
    <div className="explorer-row-meta" style={style}>
      {children}
    </div>
  )
}

export function RowWeightBar({ value }: { value: number }): JSX.Element {
  const pct = Math.min(100, Math.max(0, (value / 10) * 100))
  const isHigh = value >= 8
  return (
    <div className="explorer-row-weight">
      <div className="explorer-row-weight-track">
        <div
          className={`explorer-row-weight-fill${isHigh ? ' explorer-row-weight-fill--high' : ''}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="explorer-row-weight-value">{value}</span>
    </div>
  )
}

export function RowTickCircle({ on }: { on: boolean }): JSX.Element {
  return (
    <div
      className={`explorer-row-tick-circle${on ? '' : ' explorer-row-tick-circle--off'}`}
      aria-hidden="true"
    >
      {on && (
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path
            d="M1.5 5.5l2.5 2.5 4.5-5"
            stroke="var(--primary)"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
      )}
    </div>
  )
}
