// SPDX-License-Identifier: AGPL-3.0-only
/** Design row 9: Annex A runs compress on the tile only; the register and the form keep one chip per clause. */
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import ClauseChips, { clauseRuns } from '../ClauseChips'

const RUN = ['A.5.24', 'A.5.25', 'A.5.26', 'A.5.27', 'A.5.28', 'A.5.29', 'A.5.30']

describe('clauseRuns', () => {
  it('joins consecutive Annex A controls in the same theme, in the order given', () => {
    expect(clauseRuns(['6.1.3', ...RUN, '8.3']).map((r) => r.text)).toEqual(['6.1.3', 'A.5.24–30', '8.3'])
  })

  it('never joins across a theme, a gap, a repeat or a reordering', () => {
    expect(clauseRuns(['A.5.37', 'A.6.1']).map((r) => r.text)).toEqual(['A.5.37', 'A.6.1'])
    expect(clauseRuns(['A.5.1', 'A.6.2']).map((r) => r.text)).toEqual(['A.5.1', 'A.6.2'])
    expect(clauseRuns(['A.5.1', 'A.5.3']).map((r) => r.text)).toEqual(['A.5.1', 'A.5.3'])
    expect(clauseRuns(['A.5.2', 'A.5.2']).map((r) => r.text)).toEqual(['A.5.2', 'A.5.2'])
    expect(clauseRuns(['A.5.2', 'A.5.1']).map((r) => r.text)).toEqual(['A.5.2', 'A.5.1'])
    expect(clauseRuns(['6.1', '6.2']).map((r) => r.text)).toEqual(['6.1', '6.2'])
  })

  it('keeps every clause of a run', () => {
    expect(clauseRuns(RUN)).toEqual([{ text: 'A.5.24–30', clauses: RUN }])
  })
})

describe('ClauseChips', () => {
  it('keeps one chip per clause by default, as the register shows them', () => {
    render(<ClauseChips clauses={RUN} />)
    expect(screen.getAllByText(/^A\.5\.\d+$/).map((e) => e.textContent)).toEqual(RUN)
    expect(screen.queryByText('A.5.24–30')).toBeNull()
  })

  it('keeps a remove button per clause in the form, even when asked to compress', () => {
    const onRemove = vi.fn()
    render(<ClauseChips clauses={RUN} onRemove={onRemove} compact />)
    expect(screen.getAllByRole('button')).toHaveLength(RUN.length)
    fireEvent.click(screen.getAllByRole('button')[2])
    expect(onRemove).toHaveBeenCalledWith('A.5.26')
  })

  it('compresses when asked, the range hidden from screen readers and the full list read instead', () => {
    const { container } = render(<ClauseChips clauses={['6.1.3', ...RUN]} compact />)
    expect(screen.getByText('A.5.24–30')).toHaveAttribute('aria-hidden', 'true')
    expect(container.querySelector('.ismsm-sr-only')).toHaveTextContent(RUN.join(', '))
    // No title: on the tile the stretched link covers it, so it could never show (review F6).
    expect(container.querySelector('.obj-clauses')).not.toHaveAttribute('title')
    expect(screen.getByText('6.1.3')).toHaveClass('obj-clause')
  })
})
