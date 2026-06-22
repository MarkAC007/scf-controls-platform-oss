/**
 * Evidence Collection Maturity Types
 *
 * These types define the maturity levels for evidence collection processes,
 * distinct from control maturity (C|P-CMM). Evidence maturity measures how
 * mature an organisation's evidence collection practices are.
 */

export type EvidenceMaturityLevel = 'L0' | 'L1' | 'L2' | 'L3' | 'L4' | 'L5'

export interface EvidenceMaturityInfo {
  level: EvidenceMaturityLevel
  name: string
  description: string
  colour: string
  colourBg: string
  characteristics: string[]
  upgradeActions: string[]
  timeToUpgrade?: string
  roiIndicator?: string
}

export const EVIDENCE_MATURITY_LEVELS: Record<EvidenceMaturityLevel, EvidenceMaturityInfo> = {
  L0: {
    level: 'L0',
    name: 'Non-Existent',
    description: 'No evidence collection process in place',
    colour: '#6b7280',
    colourBg: 'rgba(107, 114, 128, 0.15)',
    characteristics: [
      'No documented evidence requirements',
      'Evidence collected only during audits',
      'No designated evidence owners',
      'No storage or retention practices'
    ],
    upgradeActions: [
      'Identify required evidence types',
      'Assign evidence owners',
      'Create basic documentation'
    ],
    timeToUpgrade: '2-4 weeks',
    roiIndicator: 'Eliminates audit fire drills'
  },
  L1: {
    level: 'L1',
    name: 'Ad Hoc',
    description: 'Manual, inconsistent evidence collection',
    colour: '#ef4444',
    colourBg: 'rgba(239, 68, 68, 0.15)',
    characteristics: [
      'Manual evidence collection',
      'Inconsistent collection frequency',
      'Single person dependency',
      'No quality checks'
    ],
    upgradeActions: [
      'Document collection procedures',
      'Establish collection schedules',
      'Define quality criteria'
    ],
    timeToUpgrade: '1-2 months',
    roiIndicator: '40% reduction in collection time'
  },
  L2: {
    level: 'L2',
    name: 'Developing',
    description: 'Documented processes being established',
    colour: '#f97316',
    colourBg: 'rgba(249, 115, 22, 0.15)',
    characteristics: [
      'Documented collection procedures',
      'Scheduled collection activities',
      'Multiple people trained',
      'Basic quality checks'
    ],
    upgradeActions: [
      'Automate routine collections',
      'Implement version control',
      'Add automated reminders'
    ],
    timeToUpgrade: '2-3 months',
    roiIndicator: '60% reduction in manual effort'
  },
  L3: {
    level: 'L3',
    name: 'Defined',
    description: 'Standardised, repeatable collection processes',
    colour: '#eab308',
    colourBg: 'rgba(234, 179, 8, 0.15)',
    characteristics: [
      'Standardised procedures across teams',
      'Automated collection where possible',
      'Defined retention policies',
      'Regular quality audits'
    ],
    upgradeActions: [
      'Implement metrics tracking',
      'Add anomaly detection',
      'Create dashboards for monitoring'
    ],
    timeToUpgrade: '3-6 months',
    roiIndicator: '80% audit preparation reduction'
  },
  L4: {
    level: 'L4',
    name: 'Managed',
    description: 'Measured and controlled collection processes',
    colour: '#84cc16',
    colourBg: 'rgba(132, 204, 22, 0.15)',
    characteristics: [
      'Metrics-driven collection',
      'Automated quality monitoring',
      'Proactive issue detection',
      'Continuous validation'
    ],
    upgradeActions: [
      'Implement predictive analytics',
      'Automate remediation',
      'Integrate with GRC platform'
    ],
    timeToUpgrade: '6-12 months',
    roiIndicator: '95% evidence availability'
  },
  L5: {
    level: 'L5',
    name: 'Optimising',
    description: 'Continuously improving, fully automated',
    colour: '#22c55e',
    colourBg: 'rgba(34, 197, 94, 0.15)',
    characteristics: [
      'Self-healing collection systems',
      'Predictive compliance insights',
      'Real-time audit readiness',
      'Continuous optimisation'
    ],
    upgradeActions: [
      'Share best practices',
      'Mentor other teams',
      'Contribute to industry standards'
    ],
    timeToUpgrade: 'Ongoing excellence',
    roiIndicator: '100% audit confidence'
  }
}

/**
 * Get maturity info by level
 */
export function getEvidenceMaturityInfo(level: EvidenceMaturityLevel): EvidenceMaturityInfo {
  return EVIDENCE_MATURITY_LEVELS[level]
}

/**
 * Get the next level for upgrade recommendations
 */
export function getNextMaturityLevel(level: EvidenceMaturityLevel): EvidenceMaturityLevel | null {
  const levels: EvidenceMaturityLevel[] = ['L0', 'L1', 'L2', 'L3', 'L4', 'L5']
  const currentIndex = levels.indexOf(level)
  if (currentIndex < levels.length - 1) {
    return levels[currentIndex + 1]
  }
  return null
}

/**
 * Calculate overall maturity score from distribution
 */
export function calculateMaturityScore(distribution: Record<EvidenceMaturityLevel, number>): number {
  const weights: Record<EvidenceMaturityLevel, number> = {
    L0: 0,
    L1: 1,
    L2: 2,
    L3: 3,
    L4: 4,
    L5: 5
  }

  let totalWeight = 0
  let totalCount = 0

  Object.entries(distribution).forEach(([level, count]) => {
    totalWeight += weights[level as EvidenceMaturityLevel] * count
    totalCount += count
  })

  return totalCount > 0 ? totalWeight / totalCount : 0
}

/**
 * Get maturity grade from score
 */
export function getMaturityGrade(score: number): { grade: string; label: string } {
  if (score >= 4.5) return { grade: 'A', label: 'Excellent' }
  if (score >= 3.5) return { grade: 'B', label: 'Good' }
  if (score >= 2.5) return { grade: 'C', label: 'Developing' }
  if (score >= 1.5) return { grade: 'D', label: 'Initial' }
  return { grade: 'F', label: 'Not Started' }
}
