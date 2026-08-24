import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  DEFAULT_EVIDENCE_VIEW,
  DEFAULT_TAB,
  SYNCED_TABS,
  evidenceItemSearch,
  pushSearch,
  readAppLocation,
  replaceSearch,
  toSearchString,
  withEvidenceItem,
  withEvidenceView,
  withTab,
  withoutTab,
} from '../appUrl'

/**
 * The URL vocabulary (#785).
 *
 * These are the assertions the three consumers rely on but cannot make
 * themselves: App, EvidenceWorkspace and EvidenceReview all need auth and org
 * providers to render, which is exactly why the vocabulary lives in a pure
 * module. Everything below is a string in, a string or an object out.
 */

describe('readAppLocation', () => {
  it('returns no tab for an empty query string', () => {
    expect(readAppLocation('').tab).toBeNull()
  })

  it('honours tab=documents', () => {
    expect(readAppLocation('?tab=documents').tab).toBe('documents')
  })

  it('honours tab=evidence', () => {
    expect(readAppLocation('?tab=evidence').tab).toBe('evidence')
  })

  // The allow-list holds every sidebar destination (#810) and nothing else: a
  // `tab` naming no screen lands on the dashboard rather than on a blank one.
  it('ignores a tab naming no screen', () => {
    for (const rejected of ['nonsense', 'dashboard', 'Documents', '../admin', '']) {
      expect(readAppLocation(`?tab=${rejected}`).tab).toBeNull()
    }
  })

  it('honours every destination the sidebar offers', () => {
    // The walk that proves this against the nav itself is in
    // `appUrl.destinations.test.tsx`; this pins the ones with parameters of
    // their own, which the rest of this file goes on to exercise.
    expect(SYNCED_TABS).toContain('scoping')
    expect(SYNCED_TABS).toContain('tasks')
    expect(SYNCED_TABS).toContain('settings')
    expect(SYNCED_TABS).toContain('users')
    expect(SYNCED_TABS).toContain('cdm')
    expect(SYNCED_TABS).not.toContain(DEFAULT_TAB)
  })

  it('defaults the evidence view when none is named', () => {
    expect(readAppLocation('?tab=evidence').evidenceView).toBe(DEFAULT_EVIDENCE_VIEW)
  })

  it('honours both evidence views', () => {
    expect(readAppLocation('?view=workspace').evidenceView).toBe('workspace')
    expect(readAppLocation('?view=dashboard').evidenceView).toBe('dashboard')
  })

  it('resolves an unknown view to the default', () => {
    expect(readAppLocation('?view=nonsense').evidenceView).toBe(DEFAULT_EVIDENCE_VIEW)
  })

  // A link that carries an item but lands on the dashboard is the worst
  // outcome: it looks like it worked.
  it('infers the workspace view from a bare item', () => {
    const location = readAppLocation('?tab=evidence&item=E-HRS-16')
    expect(location.evidenceView).toBe('workspace')
    expect(location.evidenceItem).toBe('E-HRS-16')
  })

  it('lets an explicit view override the inference', () => {
    expect(readAppLocation('?item=E-HRS-16&view=dashboard').evidenceView).toBe('dashboard')
  })

  it('treats a blank item as absent', () => {
    expect(readAppLocation('?item=').evidenceItem).toBeNull()
    expect(readAppLocation('?item=%20%20').evidenceItem).toBeNull()
  })

  it('does not throw on a malformed query string', () => {
    for (const malformed of ['?', '?&&&', '?=', '?tab', '?%', '?item=%E0%A4%A']) {
      expect(() => readAppLocation(malformed)).not.toThrow()
    }
  })

  it('names a landing screen for an unrecognised URL', () => {
    expect(DEFAULT_TAB).toBe('dashboard')
  })
})

describe('writers', () => {
  it('sets the tab', () => {
    expect(withTab('', 'evidence')).toBe('tab=evidence')
  })

  it('preserves unrelated parameters', () => {
    // `invite_type` describes the session, not the screen; App reads it on
    // mount and a navigation that dropped it would break invite acceptance.
    const next = withTab('?invite_type=org', 'documents')
    expect(readAppLocation(`?${next}`).tab).toBe('documents')
    expect(new URLSearchParams(next).get('invite_type')).toBe('org')
  })

  it('takes the documents parameters with it when leaving', () => {
    const next = withoutTab('?tab=documents&doc=DOC-1&mode=editor&invite_type=org')
    const params = new URLSearchParams(next)
    expect(params.get('tab')).toBeNull()
    expect(params.get('doc')).toBeNull()
    expect(params.get('mode')).toBeNull()
    expect(params.get('invite_type')).toBe('org')
  })

  it('takes the evidence parameters with it when leaving', () => {
    const next = withoutTab('?tab=evidence&view=workspace&item=E-HRS-16')
    expect(new URLSearchParams(next).toString()).toBe('')
  })

  // Leaving via the *other* tab's parameters must not delete them by accident.
  it('leaves the other tab\'s parameters alone when they are not the tab being left', () => {
    const next = withoutTab('?tab=evidence&doc=DOC-1')
    expect(new URLSearchParams(next).get('doc')).toBe('DOC-1')
  })

  it('sets the evidence view', () => {
    expect(new URLSearchParams(withEvidenceView('', 'workspace')).get('view')).toBe('workspace')
  })

  it('drops the item when leaving the workspace sub-tab', () => {
    const next = withEvidenceView('?view=workspace&item=E-HRS-16', 'dashboard')
    expect(new URLSearchParams(next).get('item')).toBeNull()
  })

  it('sets and clears the item', () => {
    expect(new URLSearchParams(withEvidenceItem('', 'E-HRS-16')).get('item')).toBe('E-HRS-16')
    expect(new URLSearchParams(withEvidenceItem('?item=E-HRS-16', null)).get('item')).toBeNull()
  })

  it('builds a complete deep link in one call', () => {
    const location = readAppLocation(`?${evidenceItemSearch('', 'E-HRS-16')}`)
    expect(location).toEqual({
      tab: 'evidence',
      evidenceView: 'workspace',
      evidenceItem: 'E-HRS-16',
    })
  })

  it('round-trips a deep link built from a documents URL', () => {
    // Navigating to evidence from the document workspace: the item link must
    // win, and setting the tab takes `doc`/`mode` off the URL with the screen
    // they belonged to.
    const next = evidenceItemSearch('?tab=documents&doc=DOC-1', 'E-AST-01')
    const location = readAppLocation(`?${next}`)
    expect(location.tab).toBe('evidence')
    expect(location.evidenceItem).toBe('E-AST-01')
    expect(new URLSearchParams(next).get('doc')).toBeNull()
  })

  it('escapes an id that would otherwise break the query string', () => {
    const next = evidenceItemSearch('', 'E&X=1')
    expect(next).toContain('item=E%26X%3D1')
    expect(readAppLocation(`?${next}`).evidenceItem).toBe('E&X=1')
  })

  it('renders an empty search as no question mark at all', () => {
    // A bare `?` is a URL change the history API will happily record.
    expect(toSearchString('')).toBe('')
    expect(toSearchString('tab=evidence')).toBe('?tab=evidence')
  })
})

describe('history helpers', () => {
  let replaceSpy: ReturnType<typeof vi.spyOn>
  let pushSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    replaceSpy = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {})
    pushSpy = vi.spyOn(window.history, 'pushState').mockImplementation(() => {})
  })

  afterEach(() => {
    replaceSpy.mockRestore()
    pushSpy.mockRestore()
  })

  it('replaceSearch keeps the path and adds no history entry', () => {
    replaceSearch('tab=evidence')
    expect(replaceSpy).toHaveBeenCalledTimes(1)
    expect(replaceSpy.mock.calls[0][2]).toBe(`${window.location.pathname}?tab=evidence`)
    expect(pushSpy).not.toHaveBeenCalled()
  })

  it('pushSearch adds an entry', () => {
    pushSearch(evidenceItemSearch('', 'E-HRS-16'))
    expect(pushSpy).toHaveBeenCalledTimes(1)
    expect(String(pushSpy.mock.calls[0][2])).toContain('item=E-HRS-16')
  })

  it('keeps the path when the search is empty', () => {
    replaceSearch('')
    expect(replaceSpy.mock.calls[0][2]).toBe(window.location.pathname)
  })
})
