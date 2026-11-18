// SPDX-License-Identifier: AGPL-3.0-only
/**
 * The maturity levels a control is rated on (L0 to L5), in the shape the
 * maturity distribution widget draws. Colours are the evidence
 * collection levels' own, so the two distributions read the same way.
 */
import { EVIDENCE_MATURITY_LEVELS } from './EvidenceMaturityTypes'
import type { EvidenceMaturityInfo, EvidenceMaturityLevel } from './EvidenceMaturityTypes'

const level = (id: EvidenceMaturityLevel, name: string, description: string): EvidenceMaturityInfo => ({
  level: id,
  name,
  description,
  colour: EVIDENCE_MATURITY_LEVELS[id].colour,
  colourBg: EVIDENCE_MATURITY_LEVELS[id].colourBg,
  characteristics: [],
  upgradeActions: [],
})

// Named as the control screens name them (`MATURITY_LEVEL_OPTIONS`), so a level
// reads the same here as where it was set.
export const CONTROL_MATURITY_LEVELS: Record<EvidenceMaturityLevel, EvidenceMaturityInfo> = {
  L0: level('L0', 'Initial', 'The control is not performed, or only by chance.'),
  L1: level('L1', 'Repeatable', 'The control is performed informally: it depends on the people doing it, not on a set way of working.'),
  L2: level('L2', 'Defined', 'The control follows set requirements and is planned and tracked by the team that runs it.'),
  L3: level('L3', 'Managed', 'The control is standardised across the organisation and managed centrally.'),
  L4: level('L4', 'Measured', 'The control is measured, and management decides from those measurements.'),
  L5: level('L5', 'Optimized', 'The control is improved continuously from its own performance data.'),
}
