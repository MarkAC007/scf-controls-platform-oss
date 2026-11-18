// SPDX-License-Identifier: AGPL-3.0-only
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

/** One control as the target standing reads it. */
export interface TargetStandingControl {
  scf_id: string
  /** The catalogue's name for the control's domain. */
  domainName?: string | null
  maturity?: string | null
  targetOverride?: string | null
}

/** One domain's controls in scope, measured against the level they are meant to reach. */
export interface DomainTargetStanding {
  code: string
  name: string
  /** The domain's own target from the organisation settings; null when it has none. */
  target: MaturityLevel | null
  controls: number
  rated: number
  /** Average recorded level over the rated controls; null when none is rated. */
  average: number | null
  /** Controls with a target: their own override, else the domain's. */
  withTarget: number
  atTarget: number
}

export interface TargetStanding {
  /** Domains with at least one control that has a target, furthest behind first. */
  domains: DomainTargetStanding[]
  withTarget: number
  atTarget: number
  /** Controls with no target of their own and none on their domain, and the domains they sit in. */
  noTarget: number
  domainsWithoutTarget: number
}

/**
 * Where the controls in scope stand against their target maturity.
 *
 * The rule is the "Controls at target maturity" measure's: a control is at
 * target when its recorded level is at or above its target, and a control
 * with a target and no recorded level counts against. Controls with no target
 * are counted apart, never against.
 */
export function targetStanding(
  controls: readonly TargetStandingControl[],
  domainTargets: Record<string, string> | null | undefined,
): TargetStanding {
  const byDomain = new Map<string, DomainTargetStanding & { levelSum: number; noTarget: number }>()
  for (const control of controls) {
    const code = domainOf(control.scf_id)
    let row = byDomain.get(code)
    if (!row) {
      row = {
        code,
        name: control.domainName?.trim() || code,
        target: resolveTargetMaturity(control.scf_id, null, domainTargets).level,
        controls: 0, rated: 0, average: null, withTarget: 0, atTarget: 0, levelSum: 0, noTarget: 0,
      }
      byDomain.set(code, row)
    }
    row.controls++
    const actual = asLevel(control.maturity)
    if (actual) {
      row.rated++
      row.levelSum += LEVELS.indexOf(actual)
    }
    const target = resolveTargetMaturity(control.scf_id, control.targetOverride, domainTargets).level
    if (!target) {
      row.noTarget++
      continue
    }
    row.withTarget++
    if (actual && LEVELS.indexOf(actual) >= LEVELS.indexOf(target)) row.atTarget++
  }

  const all = Array.from(byDomain.values())
  const domains = all
    .filter(row => row.withTarget > 0)
    .map(({ levelSum, noTarget: _noTarget, ...row }) => ({
      ...row,
      average: row.rated > 0 ? levelSum / row.rated : null,
    }))
    .sort((a, b) =>
      (b.withTarget - b.atTarget) - (a.withTarget - a.atTarget)
      || a.atTarget / a.withTarget - b.atTarget / b.withTarget
      || a.code.localeCompare(b.code))

  return {
    domains,
    withTarget: domains.reduce((sum, row) => sum + row.withTarget, 0),
    atTarget: domains.reduce((sum, row) => sum + row.atTarget, 0),
    noTarget: all.reduce((sum, row) => sum + row.noTarget, 0),
    domainsWithoutTarget: all.filter(row => row.withTarget === 0).length,
  }
}
