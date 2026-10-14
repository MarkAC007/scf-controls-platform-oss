/**
 * Target maturity resolution (PIG R5) — the client twin of
 * backend/services/target_maturity.py `resolve_target`.
 *
 * A control's own override wins; otherwise it inherits its domain's target
 * from the organisation settings. A control belongs to a domain by its
 * `scf_id` prefix, the attribution the journey gates use, so the roadmap and
 * the gates always agree on which target applies.
 */
import type { MaturityLevel } from '../types'

const LEVELS: readonly string[] = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']

export type TargetSource = 'control' | 'domain'

/** The target choices, labelled as the control's own maturity select labels them. */
export const MATURITY_LEVEL_OPTIONS: ReadonlyArray<{ value: MaturityLevel; label: string }> = [
  { value: 'L0', label: 'L0 - Initial' },
  { value: 'L1', label: 'L1 - Repeatable' },
  { value: 'L2', label: 'L2 - Defined' },
  { value: 'L3', label: 'L3 - Managed' },
  { value: 'L4', label: 'L4 - Measured' },
  { value: 'L5', label: 'L5 - Optimized' },
]

/** The domain a control belongs to: its `scf_id` prefix, upper-cased (backend `domain_of`). */
export function domainOf(scfId: string): string {
  return (scfId || '').split('-', 1)[0].trim().toUpperCase()
}

export interface ResolvedTarget {
  level: MaturityLevel | null
  source: TargetSource | null
}

function asLevel(value: unknown): MaturityLevel | null {
  if (typeof value !== 'string') return null
  const cleaned = value.trim().toUpperCase()
  return LEVELS.includes(cleaned) ? (cleaned as MaturityLevel) : null
}

export function resolveTargetMaturity(
  scfId: string,
  override: string | null | undefined,
  domainTargets: Record<string, string> | null | undefined,
): ResolvedTarget {
  const own = asLevel(override)
  if (own) return { level: own, source: 'control' }
  const domain = domainOf(scfId)
  // Keys are matched case-insensitively, as the backend upper-cases them
  // when it reads settings (`stored_domain_targets`).
  const key = Object.keys(domainTargets ?? {}).find(k => k.trim().toUpperCase() === domain)
  const inherited = key === undefined ? null : asLevel(domainTargets?.[key])
  if (inherited) return { level: inherited, source: 'domain' }
  return { level: null, source: null }
}
