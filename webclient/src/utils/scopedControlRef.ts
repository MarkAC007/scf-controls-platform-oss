import type { ScopedControlsFile } from '../types'

/**
 * The SCF id of a scoped_controls row, from the scoping data already loaded
 * for the organisation on screen. A notification names the row; the library
 * opens a control by its SCF id.
 *
 * null: the data has loaded and holds no such row, so the control is gone.
 * undefined: the data has not loaded, so nothing is known yet.
 */
export function scfIdForScopedControl(
  scopingData: Pick<ScopedControlsFile, 'scoped_controls'> | null | undefined,
  scopedControlId: string,
): string | null | undefined {
  if (!scopingData) return undefined
  return scopingData.scoped_controls.find(control => control.id === scopedControlId)?.scf_id ?? null
}
