/**
 * DetailSplitLayout — information on the left, inputs in a slide-out panel on
 * the right; open by default, closable, and the choice is remembered.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'
import DetailSplitLayout from '../DetailSplitLayout'

function renderLayout(panel: React.ReactNode = <p>the inputs</p>) {
  return render(
    <DetailSplitLayout panelTitle="Your record" panel={panel}>
      <p>the information</p>
    </DetailSplitLayout>,
  )
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('DetailSplitLayout', () => {
  it('shows the information and the inputs panel side by side by default', () => {
    renderLayout()
    expect(screen.getByText('the information')).toBeInTheDocument()
    const panel = screen.getByRole('complementary', { name: 'Your record' })
    expect(panel).toHaveTextContent('the inputs')
  })

  it('closing the panel keeps the information and offers a way back', () => {
    renderLayout()
    fireEvent.click(screen.getByRole('button', { name: 'Close Your record' }))
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.getByText('the information')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open Your record' }))
    expect(screen.getByRole('complementary', { name: 'Your record' })).toBeInTheDocument()
  })

  it('remembers a closed panel for the next detail page', () => {
    const first = renderLayout()
    fireEvent.click(screen.getByRole('button', { name: 'Close Your record' }))
    first.unmount()

    renderLayout()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open Your record' })).toBeInTheDocument()
  })

  it('without a panel the information takes the full width and nothing opens', () => {
    renderLayout(null)
    expect(screen.getByText('the information')).toBeInTheDocument()
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Your record/ })).not.toBeInTheDocument()
  })
})
