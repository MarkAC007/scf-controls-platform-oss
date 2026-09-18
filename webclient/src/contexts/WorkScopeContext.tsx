/**
 * WorkScopeContext — the header-level answer to "whose work am I looking at?"
 *
 * Two values only, because a scope control with more than two positions stops
 * being a scope and starts being a filter:
 *
 *   'everything' — the organisation's whole list (the default)
 *   'my_teams'   — only what a team the caller belongs to is assigned
 *
 * The default is 'everything' and is never auto-flipped. A default that hides
 * data is a default that lies about how much there is: a caller who has not
 * chosen a narrowing must see the organisation as it actually is.
 *
 * This is a presentation scope, not a permission. It narrows what a list asks
 * for; it confers nothing and withholds nothing the caller could not already
 * reach by clearing it.
 *
 * Persistence follows OrganizationContext's ``scf_current_org_id`` precedent —
 * a plain localStorage key, read and written inside try/catch so that a browser
 * with storage disabled degrades to a per-session choice rather than throwing
 * on mount.
 */
import { createContext, useCallback, useContext, useState, ReactNode } from 'react'

/** Storage key — same ``scf_`` prefix as ``scf_current_org_id``. */
const WORK_SCOPE_STORAGE_KEY = 'scf_work_scope'

export type WorkScope = 'everything' | 'my_teams'

export const DEFAULT_WORK_SCOPE: WorkScope = 'everything'

interface WorkScopeContextType {
  /** The active scope. */
  scope: WorkScope
  /** True when the scope narrows to the caller's teams — the common read. */
  isMyTeams: boolean
  /** Change the scope (and persist it). */
  setScope: (scope: WorkScope) => void
}

const WorkScopeContext = createContext<WorkScopeContextType | undefined>(undefined)

/**
 * Read the persisted scope. Anything that is not exactly 'my_teams' — absent,
 * corrupt, a value from a future version — resolves to the default, so the
 * failure mode of storage is "show everything", never "hide most of it".
 */
function readStoredScope(): WorkScope {
  try {
    return localStorage.getItem(WORK_SCOPE_STORAGE_KEY) === 'my_teams'
      ? 'my_teams'
      : DEFAULT_WORK_SCOPE
  } catch {
    return DEFAULT_WORK_SCOPE
  }
}

export function WorkScopeProvider({ children }: { children: ReactNode }) {
  const [scope, setScopeState] = useState<WorkScope>(readStoredScope)

  const setScope = useCallback((next: WorkScope) => {
    setScopeState(next)
    try {
      localStorage.setItem(WORK_SCOPE_STORAGE_KEY, next)
    } catch {
      // Storage unavailable (private mode, blocked site data). The choice still
      // applies to this session; it just will not survive a reload.
    }
  }, [])

  return (
    <WorkScopeContext.Provider
      value={{ scope, isMyTeams: scope === 'my_teams', setScope }}
    >
      {children}
    </WorkScopeContext.Provider>
  )
}

/** Hook to read the work scope. Throws outside the provider, like the others. */
export function useWorkScope(): WorkScopeContextType {
  const context = useContext(WorkScopeContext)
  if (!context) {
    throw new Error('useWorkScope must be used within WorkScopeProvider')
  }
  return context
}
