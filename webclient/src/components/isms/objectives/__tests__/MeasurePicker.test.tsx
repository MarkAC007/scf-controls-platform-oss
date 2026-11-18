// SPDX-License-Identifier: AGPL-3.0-only
/** Supporting measures are listed after the outcome measures, under their own heading. */
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import MeasurePicker from '../MeasurePicker'
import { measure } from './fixtures'

const props = {
  loading: false, error: null, mode: 'none' as const, measureKey: '', params: {},
  onPickMeasure: vi.fn(), onParam: vi.fn(), onMode: vi.fn(), disabled: false,
}

describe('MeasurePicker', () => {
  it('lists outcome measures first, then supporting ones under their heading', () => {
    // The server ranks by match across both groups; a supporting measure can come first.
    render(
      <MeasurePicker
        {...props}
        measures={[
          measure({ key: 'evidence_fresh', name: 'Evidence kept fresh', group: 'supporting', match: 3 }),
          measure({ key: 'risk_within_appetite', name: 'Risks within appetite', group: 'outcome', match: 2 }),
          measure({ key: 'vendors_reviewed', name: 'Vendors reviewed', group: 'outcome', match: 0 }),
        ]}
      />,
    )
    const radios = screen.getAllByRole('radio').map((r) => r.getAttribute('aria-label'))
    expect(radios).toEqual(['Risks within appetite', 'Vendors reviewed', 'Evidence kept fresh'])
    const heading = screen.getByRole('heading', { name: 'Supporting measures (use of the platform)' })
    // The heading sits between the last outcome measure and the first supporting one.
    const list = screen.getByRole('radiogroup', { name: 'Measures' })
    const order = [...list.children].map((el) => (el === heading ? 'heading' : el.textContent?.slice(0, 12)))
    expect(order.indexOf('heading')).toBe(2)
    // "best match" goes to the first card shown.
    expect(screen.getAllByText('best match')).toHaveLength(1)
    expect(screen.getByText('best match').closest('label')).toHaveTextContent('Risks within appetite')
  })

  it('shows no heading when there are no supporting measures', () => {
    render(<MeasurePicker {...props} measures={[measure()]} />)
    expect(screen.queryByText('Supporting measures (use of the platform)')).toBeNull()
  })
})
