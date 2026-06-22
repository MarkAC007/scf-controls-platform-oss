import type { AxisBand } from '../../types'
import { AXIS_META, bandToClass, formatAxisDisplay } from './axisHelpers'
import type { AxisKey } from './axisHelpers'

interface AxisDotProps {
  axis: AxisKey
  value: number | null
  band: AxisBand | null
  /** For the Maturity axis, pass the 0–5 score so we can show L{n} instead of %. */
  maturityScore?: number | null
  warning?: string | null
}

export default function AxisDot({ axis, value, band, maturityScore, warning }: AxisDotProps) {
  const meta = AXIS_META[axis]
  const display = formatAxisDisplay(axis, value, maturityScore)
  const tooltip = `${meta.fullLabel} — ${display}${band ? ` (${band})` : ''}\n${meta.formula}${warning ? `\n⚠ ${warningLabel(warning)}` : ''}`

  return (
    <div
      className={`cp-axis-dot ${bandToClass(band)}`}
      title={tooltip}
      tabIndex={0}
      aria-label={tooltip}
    >
      <span className="cp-axis-dot-label">{meta.label}</span>
      <span className="cp-axis-dot-value">{display}</span>
      {warning && <span className="cp-axis-warning" aria-hidden>⚠</span>}
    </div>
  )
}

function warningLabel(warning: string): string {
  if (warning === 'low_ai_coverage') {
    return 'Low AI coverage — more than 30% of evidence files are unassessed.'
  }
  return warning
}
