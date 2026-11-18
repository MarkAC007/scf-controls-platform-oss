// SPDX-License-Identifier: AGPL-3.0-only
/**
 * A performance status: always an icon and a word, never colour alone.
 * Icons are inline SVG sized by CSS (`.obj-status svg`, `.kpi-label svg`).
 */
import type { JSX } from 'react'
import type { PerformanceStatus } from '../../../data/securityObjectivesApi'
import { STATUS_META, statusText, type StatusIconName } from './objectiveText'

export function StatusIcon({ name }: { name: StatusIconName }): JSX.Element {
  const common = {
    viewBox: '0 0 16 16',
    'aria-hidden': true,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 2,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
  }
  switch (name) {
    case 'check':
      return <svg {...common}><path d="M3 8.5l3.2 3L13 4.5" /></svg>
    case 'triangle':
      return <svg {...common} fill="currentColor" strokeWidth={1}><path d="M8 2.5l6 11H2z" /></svg>
    case 'cross':
      return <svg {...common}><path d="M4 4l8 8M12 4l-8 8" /></svg>
    case 'arrow':
      return <svg {...common}><path d="M2.5 8h10M9 4.5L12.5 8 9 11.5" /></svg>
    case 'diamond':
      return <svg {...common}><path d="M8 2l6 6-6 6-6-6z" /></svg>
    case 'check-circle':
      return <svg {...common}><circle cx="8" cy="8" r="6" /><path d="M5.2 8.2l2 1.9L11 6" /></svg>
    case 'cross-circle':
      return <svg {...common}><circle cx="8" cy="8" r="6" /><path d="M5.8 5.8l4.4 4.4M10.2 5.8l-4.4 4.4" /></svg>
    case 'clock':
      return <svg {...common}><circle cx="8" cy="8" r="5.5" /><path d="M8 5v3.2l2 1.3" /></svg>
    case 'circle':
    default:
      return <svg {...common}><circle cx="8" cy="8" r="5.5" /></svg>
  }
}

interface Props {
  status: PerformanceStatus
  daysLeft?: number | null
}

export default function StatusChip({ status, daysLeft }: Props): JSX.Element {
  return (
    <span className={`obj-status obj-status--${status}`}>
      <StatusIcon name={STATUS_META[status].icon} />
      {statusText(status, daysLeft)}
    </span>
  )
}
