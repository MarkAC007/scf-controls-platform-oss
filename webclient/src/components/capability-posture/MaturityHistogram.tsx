import type { CapabilityThemeControlItem } from '../../types'

interface MaturityHistogramProps {
  controls: CapabilityThemeControlItem[]
  /** Include an "—" bar for controls without a maturity_level set. Default: true. */
  includeUnset?: boolean
}

const LEVELS = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5'] as const

const LEVEL_COLORS: Record<string, string> = {
  L0: '#ef4444',
  L1: '#f97316',
  L2: '#f59e0b',
  L3: '#22c55e',
  L4: '#16a34a',
  L5: '#15803d',
  unset: '#94a3b8',
}

export default function MaturityHistogram({ controls, includeUnset = true }: MaturityHistogramProps) {
  const counts: Record<string, number> = {
    L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, L5: 0, unset: 0,
  }
  for (const ctrl of controls) {
    const level = ctrl.maturity_level
    if (level && level in counts) counts[level] += 1
    else counts.unset += 1
  }

  const bars: Array<{ key: string; label: string; count: number }> = LEVELS.map(l => ({
    key: l,
    label: l,
    count: counts[l],
  }))
  if (includeUnset && counts.unset > 0) {
    bars.push({ key: 'unset', label: '—', count: counts.unset })
  }

  const max = Math.max(1, ...bars.map(b => b.count))

  return (
    <div className="cp-histogram" role="img" aria-label="Maturity level distribution histogram">
      <div className="cp-histogram-bars">
        {bars.map(bar => {
          const heightPct = (bar.count / max) * 100
          return (
            <div key={bar.key} className="cp-histogram-col">
              <div className="cp-histogram-bar-track">
                <div
                  className="cp-histogram-bar-fill"
                  style={{
                    height: `${heightPct}%`,
                    backgroundColor: LEVEL_COLORS[bar.key] || LEVEL_COLORS.unset,
                  }}
                  title={`${bar.label}: ${bar.count} control${bar.count === 1 ? '' : 's'}`}
                />
              </div>
              <span className="cp-histogram-count">{bar.count}</span>
              <span className="cp-histogram-label">{bar.label}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
