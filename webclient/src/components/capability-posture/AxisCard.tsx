import type { AxisBand } from '../../types'
import { AXIS_META, bandToClass, formatAxisDisplay } from './axisHelpers'
import type { AxisKey } from './axisHelpers'

interface AxisCardProps {
  axis: AxisKey
  value: number | null
  band: AxisBand | null
  /** For the Maturity axis, pass the 0–5 score so we can show L{n} instead of %. */
  maturityScore?: number | null
  warning?: string | null
}

export default function AxisCard({ axis, value, band, maturityScore, warning }: AxisCardProps) {
  const meta = AXIS_META[axis]
  const display = formatAxisDisplay(axis, value, maturityScore)

  return (
    <div className={`cp-axis-card ${bandToClass(band)}`}>
      <div className="cp-axis-card-header">
        <span className="cp-axis-card-key">{meta.label}</span>
        <span className="cp-axis-card-name">{meta.fullLabel}</span>
      </div>
      <div className="cp-axis-card-value">{display}</div>
      <div className="cp-axis-card-band">{band ?? '—'}</div>
      <button
        type="button"
        className="cp-axis-card-formula"
        title={meta.formula}
        aria-label={`${meta.fullLabel} formula: ${meta.formula}`}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="10" />
          <line x1="12" y1="16" x2="12" y2="12" />
          <line x1="12" y1="8" x2="12.01" y2="8" />
        </svg>
        <span>formula</span>
      </button>
      {warning === 'low_ai_coverage' && (
        <div className="cp-axis-card-warning" title="More than 30% of evidence files are unassessed.">
          ⚠ Low AI coverage
        </div>
      )}
    </div>
  )
}
