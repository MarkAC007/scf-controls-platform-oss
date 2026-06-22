/**
 * Evidence Maturity Advisory Components
 *
 * A suite of components for displaying and managing evidence collection maturity levels.
 *
 * Components:
 * - MaturityBadge: Compact badge showing maturity level with tooltip
 * - MaturityAdvisoryCard: Detailed card with upgrade recommendations
 * - MaturityDistributionWidget: Dashboard widget showing org-wide distribution
 *
 * Types:
 * - EvidenceMaturityLevel: L0-L5 levels
 * - EvidenceMaturityInfo: Full metadata for each level
 */

export { MaturityBadge } from './MaturityBadge'
export { MaturityAdvisoryCard } from './MaturityAdvisoryCard'
export { MaturityDistributionWidget } from './MaturityDistributionWidget'

export type { EvidenceMaturityLevel, EvidenceMaturityInfo } from './EvidenceMaturityTypes'

export {
  EVIDENCE_MATURITY_LEVELS,
  getEvidenceMaturityInfo,
  getNextMaturityLevel,
  calculateMaturityScore,
  getMaturityGrade
} from './EvidenceMaturityTypes'
