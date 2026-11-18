// SPDX-License-Identifier: AGPL-3.0-only
/** Every status is an icon and a word, never colour alone. */
import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import StatusChip from '../StatusChip'
import type { PerformanceStatus } from '../../../../data/securityObjectivesApi'

const CASES: [PerformanceStatus, string][] = [
  ['on_track', 'On track'],
  ['at_risk', 'At risk'],
  ['off_track', 'Off track'],
  ['in_progress', 'In progress'],
  ['proposed', 'Proposed · confirm target'],
  ['insufficient_data', 'Insufficient data'],
  ['first_measurement_due', 'First measurement due'],
  ['measurement_overdue', 'Measurement overdue'],
  ['not_measured', 'Not measured'],
  ['achieved', 'Achieved'],
  ['missed', 'Missed'],
]

describe('StatusChip', () => {
  it.each(CASES)('%s reads "%s" with an icon', (status, word) => {
    const { container } = render(<StatusChip status={status} />)
    const chip = container.querySelector('.obj-status')!
    expect(chip).toHaveTextContent(word)
    expect(chip).toHaveClass(`obj-status--${status}`)
    expect(chip.querySelector('svg[aria-hidden="true"]')).not.toBeNull()
  })

  it('draws closed statuses with their own icons, not the live ones', () => {
    const icon = (s: PerformanceStatus) => render(<StatusChip status={s} />).container.querySelector('svg')!.innerHTML
    expect(icon('achieved')).not.toBe(icon('on_track'))
    expect(icon('missed')).not.toBe(icon('off_track'))
    expect(icon('achieved')).not.toBe(icon('missed'))
  })

  it('counts the days left on an objective in progress', () => {
    const { container } = render(<StatusChip status="in_progress" daysLeft={133} />)
    expect(container.querySelector('.obj-status')).toHaveTextContent('In progress · 133 days left')
  })
})
