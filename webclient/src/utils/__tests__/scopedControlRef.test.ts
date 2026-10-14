import { describe, expect, it } from 'vitest'
import { scfIdForScopedControl } from '../scopedControlRef'

const scopingData = {
  scoped_controls: [
    { id: 'sc-0001', scf_id: 'AST-14', selected: true },
    { id: 'sc-0002', scf_id: 'GOV-01', selected: true },
  ],
}

describe('scfIdForScopedControl', () => {
  it('returns the SCF id of the scoped control row', () => {
    expect(scfIdForScopedControl(scopingData, 'sc-0002')).toBe('GOV-01')
  })

  it('does not match a row by its SCF id', () => {
    expect(scfIdForScopedControl(scopingData, 'AST-14')).toBeNull()
  })

  it('returns null when the loaded data has no such row', () => {
    expect(scfIdForScopedControl(scopingData, 'sc-9999')).toBeNull()
  })

  it('returns undefined, not null, before the scoping data loads', () => {
    expect(scfIdForScopedControl(null, 'sc-0001')).toBeUndefined()
    expect(scfIdForScopedControl(undefined, 'sc-0001')).toBeUndefined()
  })
})
