import type { AxisBand } from '../../types'

export type AxisKey = 'IC' | 'M' | 'EC' | 'EQ'

export interface AxisMeta {
  key: AxisKey
  label: string
  fullLabel: string
  formula: string
}

export const AXIS_META: Record<AxisKey, AxisMeta> = {
  IC: {
    key: 'IC',
    label: 'IC',
    fullLabel: 'Implementation Coverage',
    formula:
      '(monitored + implemented + 0.5·ready_for_review + 0.25·in_progress) / (scoped − N/A)',
  },
  M: {
    key: 'M',
    label: 'M',
    fullLabel: 'Maturity',
    formula: 'Weighted average L-level across implemented or monitored controls (L0=0 … L5=5)',
  },
  EC: {
    key: 'EC',
    label: 'EC',
    fullLabel: 'Evidence Coverage',
    formula: 'controls with ≥1 evidence file / (scoped − N/A)',
  },
  EQ: {
    key: 'EQ',
    label: 'EQ',
    fullLabel: 'Evidence Quality',
    formula:
      '(1·sufficient + 0.5·partial + 0·insufficient) / total_assessed × (avg_relevance / 100)',
  },
}

export function bandToClass(band: AxisBand | null): string {
  if (band === 'Strong') return 'cp-axis-band-strong'
  if (band === 'Moderate') return 'cp-axis-band-moderate'
  if (band === 'Developing') return 'cp-axis-band-developing'
  return 'cp-axis-band-unknown'
}

/** Format a 0.0–1.0 axis value as a rounded percentage. Returns '—' for null. */
export function formatAxisPercent(value: number | null): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—'
  return `${Math.round(value * 100)}%`
}

/** Format the maturity score (0–5 scale) as `L{rounded}`. Returns '—' for null. */
export function formatMaturityLevel(score: number | null): string {
  if (score === null || score === undefined || Number.isNaN(score)) return '—'
  const level = Math.round(score)
  if (level < 0 || level > 5) return '—'
  return `L${level}`
}

/** Format the display value for an axis — L{n} for Maturity, otherwise %. */
export function formatAxisDisplay(
  axis: AxisKey,
  value: number | null,
  maturityScore: number | null | undefined,
): string {
  if (axis === 'M') return formatMaturityLevel(maturityScore ?? null)
  return formatAxisPercent(value)
}
