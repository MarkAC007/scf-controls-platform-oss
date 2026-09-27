/**
 * AppLink — a real link to another object in the app.
 *
 * Every cross-reference (control → evidence, risk → control, audit row →
 * object) renders through this so it behaves like a link everywhere: a tab
 * stop, Enter to follow, a URL to copy, and Cmd/Ctrl/middle-click to open in
 * a new tab. A plain click navigates in place through `navigateToSearch`,
 * which leaves a history entry so Back returns to the source.
 *
 * Callers name a destination, never a query string: this is the one
 * component that turns an object into a URL, so the screens that link out
 * stay off the closed list of address-bar writers
 * (EvidenceReview.deeplink.test.ts).
 */
import type { MouseEvent, ReactNode } from 'react'
import {
  controlItemSearch,
  evidenceItemSearch,
  navigateToSearch,
  toSearchString,
  withRiskItem,
  withSystemItem,
  withTaskItem,
  withVendorItem,
  type LibraryMode,
} from '../data/appUrl'

export type AppDestination =
  /** `mode` defaults to in-scope; pass full-library for a control outside scope. */
  | { kind: 'control'; id: string; mode?: LibraryMode }
  | { kind: 'evidence' | 'risk' | 'vendor' | 'system' | 'task'; id: string }

/** The destination's query string, built on top of the current one. */
export function destinationSearch(search: string, to: AppDestination): string {
  switch (to.kind) {
    case 'control':
      return controlItemSearch(search, to.id, to.mode)
    case 'evidence':
      return evidenceItemSearch(search, to.id)
    case 'risk':
      return withRiskItem(search, to.id)
    case 'vendor':
      return withVendorItem(search, to.id)
    case 'system':
      return withSystemItem(search, to.id)
    case 'task':
      return withTaskItem(search, to.id)
  }
}

interface AppLinkProps {
  to: AppDestination
  children: ReactNode
  className?: string
  title?: string
  'aria-label'?: string
  'data-testid'?: string
  /**
   * Navigate through an existing App handler instead of the URL alone. The
   * href is still rendered, so keyboard and new-tab behaviour are unchanged.
   */
  onNavigate?: () => void
}

function currentSearch(): string {
  return window.location.search.replace(/^\?/, '')
}

export default function AppLink({ to, children, onNavigate, ...rest }: AppLinkProps) {
  const handleClick = (e: MouseEvent<HTMLAnchorElement>) => {
    // Leave anything but a plain primary click to the browser: new tab,
    // new window, download, context menu.
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) {
      return
    }
    e.preventDefault()
    // Links sit inside clickable rows and cards; the row must not also act.
    e.stopPropagation()
    // Built at click time, so parameters written since render are kept.
    if (onNavigate) onNavigate()
    else navigateToSearch(destinationSearch(currentSearch(), to))
  }

  return (
    <a
      href={`${window.location.pathname}${toSearchString(destinationSearch(currentSearch(), to))}`}
      onClick={handleClick}
      {...rest}
    >
      {children}
    </a>
  )
}
