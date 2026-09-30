/**
 * DetailSplitLayout — reference information on the left, the organisation's
 * own inputs in a panel that slides out from the right.
 *
 * Used by the Evidence and Control detail pages: the left half is the catalog
 * object (read-only), the right half is what the team records against it.
 * With the panel open the two sit 50/50; closed, the information takes the
 * full width and an edge tab brings the panel back. On narrow screens the
 * panel overlays the information instead of squeezing it.
 *
 * The open/closed choice is one preference for every detail page, kept in
 * localStorage — a per-browser convenience, so every access is guarded and
 * the layout works without it.
 */
import { useState, type JSX, type ReactNode } from 'react'

const STORAGE_KEY = 'scf.detailPanelOpen'
/** Below this width the panel overlays the information (matches the CSS). */
const SIDE_BY_SIDE_QUERY = '(min-width: 1100px)'

function initialOpen(): boolean {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    if (stored === 'true') return true
    if (stored === 'false') return false
  } catch {
    // Storage unavailable (private window, blocked site data) — use the default.
  }
  // Open by default where there is room for both halves.
  try {
    return window.matchMedia?.(SIDE_BY_SIDE_QUERY).matches ?? true
  } catch {
    return true
  }
}

export interface DetailSplitLayoutProps {
  /** The read-only information (the page's scrollable body). */
  children: ReactNode
  /** The inputs panel. When null the information takes the full width. */
  panel: ReactNode | null
  /** Panel heading, also used for the open/close button labels. */
  panelTitle: string
  /** Optional status shown in the panel header (e.g. a saving chip). */
  panelStatus?: ReactNode
}

export default function DetailSplitLayout({
  children,
  panel,
  panelTitle,
  panelStatus,
}: DetailSplitLayoutProps): JSX.Element {
  const [open, setOpen] = useState(initialOpen)

  const setPanelOpen = (next: boolean) => {
    setOpen(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, String(next))
    } catch {
      // Not remembered — the layout still works.
    }
  }

  const hasPanel = panel !== null && panel !== undefined && panel !== false
  const showPanel = hasPanel && open

  return (
    <div className={`detail-split${showPanel ? ' detail-split--open' : ''}`}>
      <div className="detail-split-info">{children}</div>

      {showPanel && (
        <aside className="detail-split-panel" aria-label={panelTitle} data-testid="detail-split-panel">
          <div className="detail-split-panel-header">
            <span className="detail-split-panel-title">{panelTitle}</span>
            {panelStatus}
            <button
              type="button"
              className="detail-split-panel-close"
              onClick={() => setPanelOpen(false)}
              aria-label={`Close ${panelTitle}`}
              title={`Close ${panelTitle}`}
            >
              <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
                <path d="M5 2l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </div>
          <div className="detail-split-panel-body">{panel}</div>
        </aside>
      )}

      {hasPanel && !open && (
        <button
          type="button"
          className="detail-split-reopen"
          onClick={() => setPanelOpen(true)}
          aria-label={`Open ${panelTitle}`}
        >
          <svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M9 2L4 7l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="detail-split-reopen-label">{panelTitle}</span>
        </button>
      )}
    </div>
  )
}
