/**
 * Every sidebar destination has an address (#810).
 *
 * #785 gave two screens a URL and left eleven with none: clicking them wrote
 * nothing to the address bar, so a reload dropped the user on the dashboard,
 * a bookmark saved the wrong screen, and a link pasted to a colleague opened
 * somewhere else. This walks the destination list the sidebar actually renders
 * — not a copy of it — and pins the two halves of the contract for each one:
 * navigating there writes a non-bare URL, and arriving at that URL seeds that
 * screen.
 *
 * Reading the list off `Sidebar` rather than restating it is the point. A
 * destination added to the nav and forgotten in `SYNCED_TABS` fails here, which
 * is exactly the drift that produced #810.
 */
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import Sidebar from '../../components/Sidebar'
import {
  DEFAULT_TAB,
  PARAM_TAB,
  readAppLocation,
  readTabFromUrl,
  searchForTab,
  toSearchString,
} from '../appUrl'

/**
 * The destinations the sidebar offers, in the order it offers them, obtained by
 * clicking every one of them. Both role-gated sections are switched on so the
 * walk covers the platform tabs and the consultant portal too.
 */
function sidebarDestinations(): string[] {
  const onTabChange = vi.fn()
  const { unmount } = render(
    <Sidebar activeTab="dashboard" onTabChange={onTabChange} isPlatformAdmin showConsultantPortal />,
  )
  for (const button of screen.getAllByRole('button')) fireEvent.click(button)
  unmount()
  return onTabChange.mock.calls.map(([tab]) => tab as string)
}

const DESTINATIONS = sidebarDestinations()

/**
 * Put the browser at a search string — `a=b`, no leading `?` — the way a
 * reload or a pasted link does.
 */
function arriveAt(search: string): void {
  window.history.replaceState({}, '', `/${toSearchString(search)}`)
}

afterEach(() => {
  window.history.replaceState({}, '', '/')
})

describe('the destination list itself', () => {
  // A sidebar that rendered nothing would make every case below vacuous.
  it('found the whole nav', () => {
    expect(DESTINATIONS.length).toBeGreaterThanOrEqual(20)
    expect(DESTINATIONS).toContain(DEFAULT_TAB)
    expect(new Set(DESTINATIONS).size).toBe(DESTINATIONS.length)
  })
})

describe('every sidebar destination is addressable', () => {
  const addressable = DESTINATIONS.filter(tab => tab !== DEFAULT_TAB)

  it.each(addressable)('%s writes a non-bare URL and is seeded back from it', tab => {
    // Navigating there from the landing screen.
    const next = searchForTab('', tab as never)
    expect(next).not.toBeNull()
    expect(toSearchString(next!)).not.toBe('')
    expect(new URLSearchParams(next!).get(PARAM_TAB)).toBe(tab)

    // Arriving at that URL cold — a reload, a bookmark, a pasted link.
    arriveAt(next!)
    expect(readTabFromUrl()).toBe(tab)
    expect(readAppLocation(window.location.search).tab).toBe(tab)

    // And the URL is settled once there: no rewrite on the next render.
    expect(searchForTab(window.location.search, tab as never)).toBeNull()
  })

  // The complaint in #810 is a fleet of screens sharing one address. This is
  // the assertion that they no longer do.
  it('gives each destination a distinct URL', () => {
    const urls = addressable.map(tab => searchForTab('', tab as never))
    expect(new Set(urls).size).toBe(addressable.length)
  })
})

describe('the landing screen keeps the bare path', () => {
  it('writes nothing when there is nothing to clear', () => {
    expect(searchForTab('', DEFAULT_TAB)).toBeNull()
  })

  it('seeds the landing screen from a bare URL', () => {
    arriveAt('')
    expect(readTabFromUrl()).toBe(DEFAULT_TAB)
  })

  it('takes the previous screen off the URL on the way back', () => {
    expect(searchForTab('?tab=scoping', DEFAULT_TAB)).toBe('')
  })

  it('resolves a tab naming no destination to the landing screen', () => {
    arriveAt('tab=nonsense')
    expect(readTabFromUrl()).toBe(DEFAULT_TAB)
    // ...and normalises it away rather than leaving it to be re-read.
    expect(searchForTab(window.location.search, DEFAULT_TAB)).toBe('')
  })
})

describe('a screen takes its own parameters with it', () => {
  // Now that every destination is synced, evidence → scoping → evidence is an
  // ordinary walk. Carrying `item` across the middle screen would reopen a
  // stale selection on arrival — the #785 contract broken by the #810 fix.
  it('drops the evidence selection when leaving for another destination', () => {
    const next = searchForTab('?tab=evidence&view=workspace&item=E-HRS-16', 'scoping')
    const params = new URLSearchParams(next!)
    expect(params.get(PARAM_TAB)).toBe('scoping')
    expect(params.get('item')).toBeNull()
    expect(params.get('view')).toBeNull()
  })

  it('drops the open document when leaving for another destination', () => {
    const params = new URLSearchParams(searchForTab('?tab=documents&doc=DOC-1&mode=editor', 'tasks')!)
    expect(params.get(PARAM_TAB)).toBe('tasks')
    expect(params.get('doc')).toBeNull()
    expect(params.get('mode')).toBeNull()
  })

  it('keeps parameters that describe the session rather than the screen', () => {
    const params = new URLSearchParams(searchForTab('?invite_type=org', 'webhooks')!)
    expect(params.get('invite_type')).toBe('org')
  })
})

describe('a walk of the whole sidebar', () => {
  // What a user does: click through every destination in turn. Each step must
  // leave the address bar describing where they are, with nothing accumulated
  // from where they have been.
  it('leaves the URL describing the current screen at every step', () => {
    arriveAt('')
    for (const tab of DESTINATIONS) {
      const next = searchForTab(window.location.search, tab as never)
      if (next !== null) arriveAt(next)
      expect(readTabFromUrl()).toBe(tab)
      expect(window.location.search.includes('item=')).toBe(false)
      expect(window.location.search.includes('doc=')).toBe(false)
    }
  })
})

/**
 * The other half of #810: Back has to traverse the history the app writes.
 *
 * That decision lives in an effect in `App`, and `App` cannot be mounted here —
 * it needs auth, org and query providers and twenty-two screens' worth of
 * fetching, and a test built on those mocks would pass or fail on the mocks.
 * So this reads the source, the same technique and for the same reason as
 * `components/__tests__/EvidenceReview.deeplink.test.ts`. What it pins is
 * structural — which history call the tab writer makes — and structure is what
 * a source assertion can pin and a render assertion cannot.
 */
const SOURCES = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const APP = SOURCES['../../App.tsx']

describe('a sidebar navigation is a history entry', () => {
  it('loaded the source under test', () => {
    expect(APP).toBeTypeOf('string')
  })

  // #785 wrote the app's history with `replaceState` and then refused to let
  // anyone walk it. Choosing a destination in the sidebar is a place the user
  // went; Back is how they expect to leave it.
  it('the tab writer pushes', () => {
    const start = APP.indexOf('const next = searchForTab(')
    expect(start).toBeGreaterThan(-1)
    expect(APP.slice(start, start + 300)).toMatch(/pushSearch\(next\)/)
  })

  // Except on the first pass, which normalises whatever the address bar
  // arrived with. A correction to a URL the user never chose is not somewhere
  // to go Back to, and replacing it keeps the entry they arrived on at the top
  // of the stack so Back still leaves the app.
  it('normalises the arrival URL without inventing an entry for it', () => {
    const start = APP.indexOf('const next = searchForTab(')
    expect(APP.slice(start, start + 300)).toMatch(/else replaceSearch\(next\)/)
  })

  // Back and Forward still reach `activeTab`; without this the pushed entries
  // would change the URL and leave the screen behind.
  it('keeps the popstate listener', () => {
    expect(APP).toMatch(/addEventListener\('popstate', onPopState\)/)
    expect(APP).toMatch(/removeEventListener\('popstate', onPopState\)/)
    expect(APP).toMatch(/const fromUrl = readTabFromUrl\(\)/)
  })
})
