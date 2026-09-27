/**
 * Cover for UIP-018 — the Settings index rendered static links with no current
 * section, so a reader deep in Settings could not tell where they were.
 */
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import SettingsSectionNav from '../SettingsSectionNav'

const SECTIONS = [
  { id: 'sec-a', label: 'ALPHA' },
  { id: 'sec-b', label: 'BRAVO' },
  { id: 'sec-c', label: 'CHARLIE' },
]

/** Section blocks whose on-screen tops the test controls (jsdom lays nothing out). */
function mountSections(tops: Record<string, number>) {
  const els = SECTIONS.map(({ id }) => {
    const el = document.createElement('div')
    el.id = id
    el.getBoundingClientRect = () => ({ top: tops[id] } as DOMRect)
    document.body.appendChild(el)
    return el
  })
  return {
    setTops(next: Record<string, number>) {
      els.forEach(el => { el.getBoundingClientRect = () => ({ top: next[el.id] } as DOMRect) })
    },
  }
}

afterEach(() => {
  SECTIONS.forEach(({ id }) => document.getElementById(id)?.remove())
})

function link(name: string) {
  return screen.getByRole('link', { name })
}

describe('SettingsSectionNav', () => {
  it('renders one anchor per section plus the scope note', () => {
    mountSections({ 'sec-a': 0, 'sec-b': 800, 'sec-c': 1600 })
    render(<SettingsSectionNav sections={SECTIONS} note="Org-scoped." />)
    expect(link('ALPHA')).toHaveAttribute('href', '#sec-a')
    expect(link('CHARLIE')).toHaveAttribute('href', '#sec-c')
    expect(screen.getByText('Org-scoped.')).toBeInTheDocument()
  })

  it('marks the section at the top of the pane as current', () => {
    mountSections({ 'sec-a': 0, 'sec-b': 800, 'sec-c': 1600 })
    render(<SettingsSectionNav sections={SECTIONS} />)
    expect(link('ALPHA')).toHaveClass('active')
    expect(link('ALPHA')).toHaveAttribute('aria-current', 'location')
    expect(link('BRAVO')).not.toHaveClass('active')
  })

  it('moves the current marker as the reader scrolls', () => {
    const sections = mountSections({ 'sec-a': 0, 'sec-b': 800, 'sec-c': 1600 })
    render(<SettingsSectionNav sections={SECTIONS} />)
    sections.setTops({ 'sec-a': -900, 'sec-b': 40, 'sec-c': 700 })
    act(() => { window.dispatchEvent(new Event('scroll')) })
    expect(link('BRAVO')).toHaveClass('active')
    expect(link('ALPHA')).not.toHaveAttribute('aria-current')
  })

  it('marks a clicked link current straight away', () => {
    mountSections({ 'sec-a': 0, 'sec-b': 800, 'sec-c': 1600 })
    render(<SettingsSectionNav sections={SECTIONS} />)
    fireEvent.click(link('CHARLIE'))
    expect(link('CHARLIE')).toHaveClass('active')
  })
})
