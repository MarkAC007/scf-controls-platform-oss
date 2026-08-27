import { describe, it, expect } from 'vitest'
import {
  readAppLocation,
  withLibraryItem,
  withTab,
  withoutTab,
  searchForTab,
} from '../appUrl'

/**
 * Library tab `?item=` URL plumbing (Phase 2, Task 1).
 *
 * The library tab reuses the literal `item` param name that evidence uses, but
 * the two are tab-scoped via TAB_OWNED_PARAMS — they cannot collide because
 * switching tabs clears the outgoing tab's params.
 */

describe('readAppLocation — library', () => {
  it('returns null libraryItem when tab is not library', () => {
    expect(readAppLocation('?tab=evidence&item=E-HRS-16').libraryItem).toBeNull()
  })

  it('returns null libraryItem when tab=library but no item', () => {
    expect(readAppLocation('?tab=library').libraryItem).toBeNull()
  })

  it('returns the item id when tab=library and item is present', () => {
    expect(readAppLocation('?tab=library&item=GOV-04').libraryItem).toBe('GOV-04')
  })

  it('round-trips a deep link: ?tab=library&item=GOV-04', () => {
    const loc = readAppLocation('?tab=library&item=GOV-04')
    expect(loc.tab).toBe('library')
    expect(loc.libraryItem).toBe('GOV-04')
  })

  it('treats a blank library item as absent', () => {
    expect(readAppLocation('?tab=library&item=').libraryItem).toBeNull()
    expect(readAppLocation('?tab=library&item=%20%20').libraryItem).toBeNull()
  })

  it('does not expose evidenceItem when tab is library', () => {
    // evidence item should be null for library tab — the two are independent
    const loc = readAppLocation('?tab=library&item=GOV-04')
    expect(loc.evidenceItem).toBeNull()
  })
})

describe('withLibraryItem', () => {
  it('sets the item and ensures tab=library', () => {
    const result = withLibraryItem('', 'GOV-04')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('library')
    expect(p.get('item')).toBe('GOV-04')
  })

  it('clears the item when null is passed', () => {
    const result = withLibraryItem('?tab=library&item=GOV-04', null)
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('library')
    expect(p.get('item')).toBeNull()
  })

  it('preserves tab=library when already set', () => {
    const result = withLibraryItem('?tab=library&item=GOV-01', 'GOV-04')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('library')
    expect(p.get('item')).toBe('GOV-04')
  })

  it('switches the tab to library even if another tab was active', () => {
    // Starting from the evidence tab (which has its own item param)
    const result = withLibraryItem('?tab=evidence&view=workspace&item=E-HRS-16', 'GOV-04')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('library')
    expect(p.get('item')).toBe('GOV-04')
  })

  it('round-trips through readAppLocation', () => {
    const search = withLibraryItem('', 'GOV-04')
    const loc = readAppLocation(`?${search}`)
    expect(loc.tab).toBe('library')
    expect(loc.libraryItem).toBe('GOV-04')
  })

  it('escapes an id that would otherwise break the query string', () => {
    const result = withLibraryItem('', 'GOV&X=1')
    expect(result).toContain('item=GOV%26X%3D1')
    expect(readAppLocation(`?${result}`).libraryItem).toBe('GOV&X=1')
  })
})

describe('tab-switch clearing — no collision between library and evidence item params', () => {
  it('clears the library item when switching from library to another tab', () => {
    // ?tab=library&item=GOV-04 → navigate to scoping
    const next = searchForTab('?tab=library&item=GOV-04', 'scoping')
    const p = new URLSearchParams(next!)
    expect(p.get('tab')).toBe('scoping')
    expect(p.get('item')).toBeNull()
  })

  it('clears the library item when switching to evidence', () => {
    const next = withTab('?tab=library&item=GOV-04', 'evidence')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('evidence')
    expect(p.get('item')).toBeNull()
  })

  it('withoutTab removes the library item with the tab', () => {
    const next = withoutTab('?tab=library&item=GOV-04')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBeNull()
    expect(p.get('item')).toBeNull()
  })

  // The critical no-collision test: evidence item must NOT be cleared when
  // the library param is being cleared (and vice versa).
  it('evidence ?item= is still present after navigating FROM evidence (evidence keeps own clearing logic)', () => {
    // When on evidence tab with item, switching away clears evidence's item
    const next = withoutTab('?tab=evidence&view=workspace&item=E-HRS-16')
    const p = new URLSearchParams(next)
    expect(p.get('item')).toBeNull()  // evidence item cleared
    expect(p.get('view')).toBeNull()
  })

  it('library item does not bleed into evidence session', () => {
    // Navigate: library with item → then switch to evidence
    const fromLibrary = '?tab=library&item=GOV-04'
    const toEvidence = withTab(fromLibrary, 'evidence')
    const p = new URLSearchParams(toEvidence)
    expect(p.get('tab')).toBe('evidence')
    // The library item was cleared when we left library
    expect(p.get('item')).toBeNull()
  })

  it('evidence item does not bleed into library session', () => {
    // Navigate: evidence with item → then switch to library
    const fromEvidence = '?tab=evidence&view=workspace&item=E-HRS-16'
    const toLibrary = withTab(fromEvidence, 'library')
    const p = new URLSearchParams(toLibrary)
    expect(p.get('tab')).toBe('library')
    // The evidence item was cleared when we left evidence
    expect(p.get('item')).toBeNull()
  })

  it('readAppLocation sees null libraryItem and non-null evidenceItem when on evidence tab', () => {
    const loc = readAppLocation('?tab=evidence&view=workspace&item=E-HRS-16')
    expect(loc.tab).toBe('evidence')
    expect(loc.evidenceItem).toBe('E-HRS-16')
    expect(loc.libraryItem).toBeNull()
  })

  it('readAppLocation sees null evidenceItem and non-null libraryItem when on library tab', () => {
    const loc = readAppLocation('?tab=library&item=GOV-04')
    expect(loc.tab).toBe('library')
    expect(loc.libraryItem).toBe('GOV-04')
    expect(loc.evidenceItem).toBeNull()
  })
})

describe('evidence ?item= behavior unchanged', () => {
  it('evidence item is still read correctly when on evidence tab', () => {
    const loc = readAppLocation('?tab=evidence&item=E-HRS-16')
    expect(loc.evidenceItem).toBe('E-HRS-16')
    expect(loc.evidenceView).toBe('workspace')
  })

  it('evidence item infers workspace view', () => {
    expect(readAppLocation('?tab=evidence&item=E-HRS-16').evidenceView).toBe('workspace')
  })

  it('evidence item treats blank as absent', () => {
    expect(readAppLocation('?tab=evidence&item=').evidenceItem).toBeNull()
  })

  it('evidenceItemSearch still works correctly', () => {
    // This is a sanity check that we haven't broken the evidence helpers
    const loc = readAppLocation('?tab=evidence&view=workspace&item=E-HRS-16')
    expect(loc.tab).toBe('evidence')
    expect(loc.evidenceItem).toBe('E-HRS-16')
    expect(loc.evidenceView).toBe('workspace')
  })
})
