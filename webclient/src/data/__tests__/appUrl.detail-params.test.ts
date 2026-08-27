import { describe, it, expect } from 'vitest'
import {
  readAppLocation,
  withRiskItem,
  withVendorItem,
  withSystemItem,
  withTaskItem,
  withTab,
  withoutTab,
  searchForTab,
} from '../appUrl'

/**
 * Detail URL params for risk, vendor, system, task (Phase 4, Task 1).
 *
 * Four new tab-owned params, each modelled byte-for-byte on the library `item`
 * mechanism (Phase-2 Task 1). The params are: risk-register→`risk`,
 * vendors→`vendor`, systems→`system`, tasks→`task`.
 *
 * Each uses a distinct param name (no shared literal like evidence/library
 * share `item`), so collision is impossible at the param-name level — but the
 * TAB_OWNED_PARAMS mechanism still prevents stale params surviving a tab switch.
 */

// ---------------------------------------------------------------------------
// readAppLocation — riskItem
// ---------------------------------------------------------------------------

describe('readAppLocation — riskItem', () => {
  it('returns null riskItem when tab is not risk-register', () => {
    expect(readAppLocation('?tab=vendors&risk=RISK-1').riskItem).toBeNull()
  })

  it('returns null riskItem when tab=risk-register but no risk param', () => {
    expect(readAppLocation('?tab=risk-register').riskItem).toBeNull()
  })

  it('returns the id when tab=risk-register and risk is present', () => {
    expect(readAppLocation('?tab=risk-register&risk=RISK-42').riskItem).toBe('RISK-42')
  })

  it('round-trips a deep link: ?tab=risk-register&risk=RISK-42', () => {
    const loc = readAppLocation('?tab=risk-register&risk=RISK-42')
    expect(loc.tab).toBe('risk-register')
    expect(loc.riskItem).toBe('RISK-42')
  })

  it('treats a blank risk param as absent', () => {
    expect(readAppLocation('?tab=risk-register&risk=').riskItem).toBeNull()
    expect(readAppLocation('?tab=risk-register&risk=%20%20').riskItem).toBeNull()
  })

  it('does not expose riskItem on other tabs', () => {
    for (const tab of ['vendors', 'systems', 'tasks', 'evidence', 'library']) {
      expect(readAppLocation(`?tab=${tab}&risk=RISK-1`).riskItem).toBeNull()
    }
  })
})

// ---------------------------------------------------------------------------
// readAppLocation — vendorItem
// ---------------------------------------------------------------------------

describe('readAppLocation — vendorItem', () => {
  it('returns null vendorItem when tab is not vendors', () => {
    expect(readAppLocation('?tab=risk-register&vendor=V-1').vendorItem).toBeNull()
  })

  it('returns null vendorItem when tab=vendors but no vendor param', () => {
    expect(readAppLocation('?tab=vendors').vendorItem).toBeNull()
  })

  it('returns the id when tab=vendors and vendor is present', () => {
    expect(readAppLocation('?tab=vendors&vendor=42').vendorItem).toBe('42')
  })

  it('round-trips a deep link: ?tab=vendors&vendor=42', () => {
    const loc = readAppLocation('?tab=vendors&vendor=42')
    expect(loc.tab).toBe('vendors')
    expect(loc.vendorItem).toBe('42')
  })

  it('treats a blank vendor param as absent', () => {
    expect(readAppLocation('?tab=vendors&vendor=').vendorItem).toBeNull()
    expect(readAppLocation('?tab=vendors&vendor=%20%20').vendorItem).toBeNull()
  })

  it('does not expose vendorItem on other tabs', () => {
    for (const tab of ['risk-register', 'systems', 'tasks', 'evidence', 'library']) {
      expect(readAppLocation(`?tab=${tab}&vendor=V-1`).vendorItem).toBeNull()
    }
  })
})

// ---------------------------------------------------------------------------
// readAppLocation — systemItem
// ---------------------------------------------------------------------------

describe('readAppLocation — systemItem', () => {
  it('returns null systemItem when tab is not systems', () => {
    expect(readAppLocation('?tab=vendors&system=SYS-1').systemItem).toBeNull()
  })

  it('returns null systemItem when tab=systems but no system param', () => {
    expect(readAppLocation('?tab=systems').systemItem).toBeNull()
  })

  it('returns the id when tab=systems and system is present', () => {
    expect(readAppLocation('?tab=systems&system=SYS-7').systemItem).toBe('SYS-7')
  })

  it('round-trips a deep link: ?tab=systems&system=SYS-7', () => {
    const loc = readAppLocation('?tab=systems&system=SYS-7')
    expect(loc.tab).toBe('systems')
    expect(loc.systemItem).toBe('SYS-7')
  })

  it('treats a blank system param as absent', () => {
    expect(readAppLocation('?tab=systems&system=').systemItem).toBeNull()
    expect(readAppLocation('?tab=systems&system=%20%20').systemItem).toBeNull()
  })

  it('does not expose systemItem on other tabs', () => {
    for (const tab of ['risk-register', 'vendors', 'tasks', 'evidence', 'library']) {
      expect(readAppLocation(`?tab=${tab}&system=SYS-1`).systemItem).toBeNull()
    }
  })
})

// ---------------------------------------------------------------------------
// readAppLocation — taskItem
// ---------------------------------------------------------------------------

describe('readAppLocation — taskItem', () => {
  it('returns null taskItem when tab is not tasks', () => {
    expect(readAppLocation('?tab=vendors&task=TASK-1').taskItem).toBeNull()
  })

  it('returns null taskItem when tab=tasks but no task param', () => {
    expect(readAppLocation('?tab=tasks').taskItem).toBeNull()
  })

  it('returns the id when tab=tasks and task is present', () => {
    expect(readAppLocation('?tab=tasks&task=TASK-99').taskItem).toBe('TASK-99')
  })

  it('round-trips a deep link: ?tab=tasks&task=TASK-99', () => {
    const loc = readAppLocation('?tab=tasks&task=TASK-99')
    expect(loc.tab).toBe('tasks')
    expect(loc.taskItem).toBe('TASK-99')
  })

  it('treats a blank task param as absent', () => {
    expect(readAppLocation('?tab=tasks&task=').taskItem).toBeNull()
    expect(readAppLocation('?tab=tasks&task=%20%20').taskItem).toBeNull()
  })

  it('does not expose taskItem on other tabs', () => {
    for (const tab of ['risk-register', 'vendors', 'systems', 'evidence', 'library']) {
      expect(readAppLocation(`?tab=${tab}&task=TASK-1`).taskItem).toBeNull()
    }
  })
})

// ---------------------------------------------------------------------------
// withRiskItem
// ---------------------------------------------------------------------------

describe('withRiskItem', () => {
  it('sets the risk param and ensures tab=risk-register', () => {
    const result = withRiskItem('', 'RISK-42')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('risk')).toBe('RISK-42')
  })

  it('clears the risk param when null is passed', () => {
    const result = withRiskItem('?tab=risk-register&risk=RISK-42', null)
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('risk')).toBeNull()
  })

  it('preserves tab=risk-register when already set', () => {
    const result = withRiskItem('?tab=risk-register&risk=RISK-1', 'RISK-42')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('risk')).toBe('RISK-42')
  })

  it('switches the tab to risk-register even if another tab was active', () => {
    const result = withRiskItem('?tab=vendors&vendor=7', 'RISK-42')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('risk')).toBe('RISK-42')
    // The vendor param should have been cleared when leaving vendors
    expect(p.get('vendor')).toBeNull()
  })

  it('round-trips through readAppLocation', () => {
    const search = withRiskItem('', 'RISK-42')
    const loc = readAppLocation(`?${search}`)
    expect(loc.tab).toBe('risk-register')
    expect(loc.riskItem).toBe('RISK-42')
  })

  it('escapes an id that would otherwise break the query string', () => {
    const result = withRiskItem('', 'RISK&X=1')
    expect(result).toContain('risk=RISK%26X%3D1')
    expect(readAppLocation(`?${result}`).riskItem).toBe('RISK&X=1')
  })
})

// ---------------------------------------------------------------------------
// withVendorItem
// ---------------------------------------------------------------------------

describe('withVendorItem', () => {
  it('sets the vendor param and ensures tab=vendors', () => {
    const result = withVendorItem('', '42')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('vendors')
    expect(p.get('vendor')).toBe('42')
  })

  it('clears the vendor param when null is passed', () => {
    const result = withVendorItem('?tab=vendors&vendor=42', null)
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('vendors')
    expect(p.get('vendor')).toBeNull()
  })

  it('switches the tab to vendors even if another tab was active', () => {
    const result = withVendorItem('?tab=risk-register&risk=RISK-1', '42')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('vendors')
    expect(p.get('vendor')).toBe('42')
    expect(p.get('risk')).toBeNull()
  })

  it('round-trips through readAppLocation', () => {
    const search = withVendorItem('', '42')
    const loc = readAppLocation(`?${search}`)
    expect(loc.tab).toBe('vendors')
    expect(loc.vendorItem).toBe('42')
  })

  it('escapes an id that would otherwise break the query string', () => {
    const result = withVendorItem('', 'V&X=1')
    expect(result).toContain('vendor=V%26X%3D1')
    expect(readAppLocation(`?${result}`).vendorItem).toBe('V&X=1')
  })
})

// ---------------------------------------------------------------------------
// withSystemItem
// ---------------------------------------------------------------------------

describe('withSystemItem', () => {
  it('sets the system param and ensures tab=systems', () => {
    const result = withSystemItem('', 'SYS-7')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('systems')
    expect(p.get('system')).toBe('SYS-7')
  })

  it('clears the system param when null is passed', () => {
    const result = withSystemItem('?tab=systems&system=SYS-7', null)
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('systems')
    expect(p.get('system')).toBeNull()
  })

  it('switches the tab to systems even if another tab was active', () => {
    const result = withSystemItem('?tab=tasks&task=TASK-1', 'SYS-7')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('systems')
    expect(p.get('system')).toBe('SYS-7')
    expect(p.get('task')).toBeNull()
  })

  it('round-trips through readAppLocation', () => {
    const search = withSystemItem('', 'SYS-7')
    const loc = readAppLocation(`?${search}`)
    expect(loc.tab).toBe('systems')
    expect(loc.systemItem).toBe('SYS-7')
  })

  it('escapes an id that would otherwise break the query string', () => {
    const result = withSystemItem('', 'SYS&X=1')
    expect(result).toContain('system=SYS%26X%3D1')
    expect(readAppLocation(`?${result}`).systemItem).toBe('SYS&X=1')
  })
})

// ---------------------------------------------------------------------------
// withTaskItem
// ---------------------------------------------------------------------------

describe('withTaskItem', () => {
  it('sets the task param and ensures tab=tasks', () => {
    const result = withTaskItem('', 'TASK-99')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('tasks')
    expect(p.get('task')).toBe('TASK-99')
  })

  it('clears the task param when null is passed', () => {
    const result = withTaskItem('?tab=tasks&task=TASK-99', null)
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('tasks')
    expect(p.get('task')).toBeNull()
  })

  it('switches the tab to tasks even if another tab was active', () => {
    const result = withTaskItem('?tab=systems&system=SYS-1', 'TASK-99')
    const p = new URLSearchParams(result)
    expect(p.get('tab')).toBe('tasks')
    expect(p.get('task')).toBe('TASK-99')
    expect(p.get('system')).toBeNull()
  })

  it('round-trips through readAppLocation', () => {
    const search = withTaskItem('', 'TASK-99')
    const loc = readAppLocation(`?${search}`)
    expect(loc.tab).toBe('tasks')
    expect(loc.taskItem).toBe('TASK-99')
  })

  it('escapes an id that would otherwise break the query string', () => {
    const result = withTaskItem('', 'T&X=1')
    expect(result).toContain('task=T%26X%3D1')
    expect(readAppLocation(`?${result}`).taskItem).toBe('T&X=1')
  })
})

// ---------------------------------------------------------------------------
// tab-switch clearing — no collision between detail params and each other
// ---------------------------------------------------------------------------

describe('tab-switch clearing — detail params cleared on tab switch', () => {
  it('clears risk param when switching away from risk-register', () => {
    const next = searchForTab('?tab=risk-register&risk=RISK-1', 'vendors')
    const p = new URLSearchParams(next!)
    expect(p.get('tab')).toBe('vendors')
    expect(p.get('risk')).toBeNull()
  })

  it('clears vendor param when switching away from vendors', () => {
    const next = searchForTab('?tab=vendors&vendor=42', 'risk-register')
    const p = new URLSearchParams(next!)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('vendor')).toBeNull()
  })

  it('clears system param when switching away from systems', () => {
    const next = searchForTab('?tab=systems&system=SYS-7', 'tasks')
    const p = new URLSearchParams(next!)
    expect(p.get('tab')).toBe('tasks')
    expect(p.get('system')).toBeNull()
  })

  it('clears task param when switching away from tasks', () => {
    const next = searchForTab('?tab=tasks&task=TASK-99', 'systems')
    const p = new URLSearchParams(next!)
    expect(p.get('tab')).toBe('systems')
    expect(p.get('task')).toBeNull()
  })

  it('withoutTab removes risk param with the tab', () => {
    const next = withoutTab('?tab=risk-register&risk=RISK-1')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBeNull()
    expect(p.get('risk')).toBeNull()
  })

  it('withoutTab removes vendor param with the tab', () => {
    const next = withoutTab('?tab=vendors&vendor=42')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBeNull()
    expect(p.get('vendor')).toBeNull()
  })

  it('withoutTab removes system param with the tab', () => {
    const next = withoutTab('?tab=systems&system=SYS-7')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBeNull()
    expect(p.get('system')).toBeNull()
  })

  it('withoutTab removes task param with the tab', () => {
    const next = withoutTab('?tab=tasks&task=TASK-99')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBeNull()
    expect(p.get('task')).toBeNull()
  })

  it('risk param does not bleed into vendors session', () => {
    const next = withTab('?tab=risk-register&risk=RISK-1', 'vendors')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('vendors')
    expect(p.get('risk')).toBeNull()
  })

  it('vendor param does not bleed into risk-register session', () => {
    const next = withTab('?tab=vendors&vendor=42', 'risk-register')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('vendor')).toBeNull()
  })

  it('system param does not bleed into tasks session', () => {
    const next = withTab('?tab=systems&system=SYS-7', 'tasks')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('tasks')
    expect(p.get('system')).toBeNull()
  })

  it('task param does not bleed into systems session', () => {
    const next = withTab('?tab=tasks&task=TASK-99', 'systems')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('systems')
    expect(p.get('task')).toBeNull()
  })
})

// ---------------------------------------------------------------------------
// Collision suite: detail params do not collide with evidence/library item
// ---------------------------------------------------------------------------

describe('collision — detail params do not affect evidence or library item', () => {
  it('evidence item is unaffected when a risk URL is present', () => {
    // If somehow risk= is in the URL while on evidence (impossible via helpers,
    // but guard the read side): riskItem should be null
    const loc = readAppLocation('?tab=evidence&item=E-HRS-16&risk=RISK-1')
    expect(loc.evidenceItem).toBe('E-HRS-16')
    expect(loc.riskItem).toBeNull()
  })

  it('library item is unaffected when a vendor URL is present', () => {
    const loc = readAppLocation('?tab=library&item=GOV-04&vendor=42')
    expect(loc.libraryItem).toBe('GOV-04')
    expect(loc.vendorItem).toBeNull()
  })

  it('navigating from evidence to risk-register clears evidence item, not risk param', () => {
    const next = withRiskItem('?tab=evidence&view=workspace&item=E-HRS-16', 'RISK-1')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('risk-register')
    expect(p.get('risk')).toBe('RISK-1')
    expect(p.get('item')).toBeNull()
    expect(p.get('view')).toBeNull()
  })

  it('navigating from library to vendors clears library item, sets vendor', () => {
    const next = withVendorItem('?tab=library&item=GOV-04', '42')
    const p = new URLSearchParams(next)
    expect(p.get('tab')).toBe('vendors')
    expect(p.get('vendor')).toBe('42')
    expect(p.get('item')).toBeNull()
  })

  it('readAppLocation sees null for all detail fields on evidence tab', () => {
    const loc = readAppLocation('?tab=evidence&view=workspace&item=E-HRS-16')
    expect(loc.riskItem).toBeNull()
    expect(loc.vendorItem).toBeNull()
    expect(loc.systemItem).toBeNull()
    expect(loc.taskItem).toBeNull()
  })

  it('readAppLocation sees null for all other fields on risk-register tab', () => {
    const loc = readAppLocation('?tab=risk-register&risk=RISK-1')
    expect(loc.evidenceItem).toBeNull()
    expect(loc.libraryItem).toBeNull()
    expect(loc.vendorItem).toBeNull()
    expect(loc.systemItem).toBeNull()
    expect(loc.taskItem).toBeNull()
  })
})
