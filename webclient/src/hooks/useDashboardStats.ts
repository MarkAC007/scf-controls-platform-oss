import { useMemo } from 'react'
import type { EnrichedControl, ScopedControlsFile, ImplementationStatus, EvidenceMaturityLevel } from '../types'
import { getScopedControl, getEvidenceTracking } from '../data/scopingService'

export interface DashboardStats {
  selectedCount: number
  topDomains: [string, number][]
  statusCounts: Record<ImplementationStatus, number>
  implementedPercentage: number
  controlsByTeam: Record<string, number>
  maturityCounts: Record<string, number>
  averageMaturity: number
  totalEvidence: number
  trackedEvidence: number
  evidencePercentage: number
  evidenceByTeamCounts: Record<string, { total: number; tracked: number }>
  frameworkStats: Array<{
    frameworkKey: string
    frameworkName: string
    totalControls: number
    selectedControls: number
    implementedControls: number
    inProgressControls: number
    atRiskControls: number
    notStartedControls: number
    gapControlIds: string[]
    gapsByDomain: Record<string, { controlIds: string[]; controlNames: string[] }>
  }>
  evidenceMaturityDistribution: Record<EvidenceMaturityLevel, number>
}

export function useDashboardStats(
  controls: EnrichedControl[],
  scopingData: ScopedControlsFile
): DashboardStats {
  return useMemo(() => {
    const selectedControls = controls.filter(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      return scoped?.selected
    })

    // Control scoping stats
    const totalControls = controls.length
    const selectedCount = selectedControls.length
    const _scopingPercentage = totalControls > 0 ? Math.round((selectedCount / totalControls) * 100) : 0

    // By domain
    const byDomain: Record<string, number> = {}
    selectedControls.forEach(c => {
      byDomain[c.scf_domain] = (byDomain[c.scf_domain] || 0) + 1
    })
    const topDomains = Object.entries(byDomain)
      .sort(([, a], [, b]) => b - a)
      .slice(0, 5) as [string, number][]

    // Implementation status stats
    const statusCounts: Record<ImplementationStatus, number> = {
      not_started: 0,
      in_progress: 0,
      implemented: 0,
      ready_for_review: 0,
      monitored: 0,
      at_risk: 0,
      not_applicable: 0,
      deferred: 0
    }
    selectedControls.forEach(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      const status = scoped?.implementation_status || 'not_started'
      statusCounts[status]++
    })
    const implementedPercentage = selectedCount > 0
      ? Math.round((statusCounts.implemented / selectedCount) * 100)
      : 0

    // Framework statistics with gap analysis
    const frameworkStats: DashboardStats['frameworkStats'] = []

    const frameworkMap = new Map<string, {
      controls: Map<string, { domain: string; name: string }>
      selectedControls: Map<string, string>
    }>()

    controls.forEach(c => {
      Object.keys(c.frameworksResolved).forEach(fwName => {
        if (!frameworkMap.has(fwName)) {
          frameworkMap.set(fwName, {
            controls: new Map(),
            selectedControls: new Map()
          })
        }
        frameworkMap.get(fwName)!.controls.set(c.scf_id, {
          domain: c.scf_domain,
          name: c.control_name
        })

        const scoped = getScopedControl(scopingData, c.scf_id)
        if (scoped?.selected) {
          const status = scoped.implementation_status || 'not_started'
          frameworkMap.get(fwName)!.selectedControls.set(c.scf_id, status)
        }
      })
    })

    frameworkMap.forEach((data, fwName) => {
      const selectedControlsForFw = data.selectedControls
      let implementedCount = 0
      let inProgressCount = 0
      let atRiskCount = 0
      let notStartedCount = 0

      selectedControlsForFw.forEach((status) => {
        if (status === 'implemented') implementedCount++
        else if (status === 'in_progress') inProgressCount++
        else if (status === 'at_risk') atRiskCount++
        else if (status === 'not_started') notStartedCount++
      })

      const gapControlIds: string[] = []
      const gapsByDomain: Record<string, { controlIds: string[]; controlNames: string[] }> = {}

      data.controls.forEach((controlMeta, controlId) => {
        if (!selectedControlsForFw.has(controlId)) {
          gapControlIds.push(controlId)
          const domain = controlMeta.domain
          if (!gapsByDomain[domain]) {
            gapsByDomain[domain] = { controlIds: [], controlNames: [] }
          }
          gapsByDomain[domain].controlIds.push(controlId)
          gapsByDomain[domain].controlNames.push(controlMeta.name)
        }
      })

      frameworkStats.push({
        frameworkKey: fwName.toLowerCase().replace(/[^a-z0-9]+/g, '_'),
        frameworkName: fwName,
        totalControls: data.controls.size,
        selectedControls: selectedControlsForFw.size,
        implementedControls: implementedCount,
        inProgressControls: inProgressCount,
        atRiskControls: atRiskCount,
        notStartedControls: notStartedCount,
        gapControlIds,
        gapsByDomain
      })
    })

    frameworkStats.sort((a, b) => b.totalControls - a.totalControls)

    // Controls by Owner Team
    const controlsByTeam: Record<string, number> = {}
    selectedControls.forEach(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      const team = scoped?.owner || 'Unassigned'
      controlsByTeam[team] = (controlsByTeam[team] || 0) + 1
    })

    // Maturity level stats
    const maturityCounts: Record<string, number> = {
      L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, L5: 0, unset: 0
    }
    selectedControls.forEach(c => {
      const scoped = getScopedControl(scopingData, c.scf_id)
      const maturity = scoped?.maturity_level
      if (maturity) {
        maturityCounts[maturity]++
      } else {
        maturityCounts.unset++
      }
    })

    const maturityWeights: Record<string, number> = { L0: 0, L1: 1, L2: 2, L3: 3, L4: 4, L5: 5 }
    let totalWeight = 0
    let assessedControls = 0
    Object.entries(maturityCounts).forEach(([level, count]) => {
      if (level !== 'unset' && count > 0) {
        totalWeight += (maturityWeights[level] ?? 0) * count
        assessedControls += count
      }
    })
    const averageMaturity = assessedControls > 0 ? totalWeight / assessedControls : 0

    // Evidence tracking stats
    const uniqueEvidence = new Set<string>()
    selectedControls.forEach(c => {
      c.artifactsResolved.forEach(artifact => {
        uniqueEvidence.add(artifact.id)
      })
    })

    const totalEvidence = uniqueEvidence.size
    let trackedEvidence = 0
    uniqueEvidence.forEach(evidenceId => {
      const tracking = getEvidenceTracking(scopingData, evidenceId)
      if (tracking?.is_tracked) {
        trackedEvidence++
      }
    })
    const evidencePercentage = totalEvidence > 0
      ? Math.round((trackedEvidence / totalEvidence) * 100)
      : 0

    // Evidence by Owner Team
    const evidenceByTeam: Record<string, { total: Set<string>; tracked: Set<string> }> = {}
    selectedControls.forEach(c => {
      c.artifactsResolved.forEach(artifact => {
        const tracking = getEvidenceTracking(scopingData, artifact.id)
        const team = tracking?.owner || 'Unassigned'
        if (!evidenceByTeam[team]) {
          evidenceByTeam[team] = { total: new Set(), tracked: new Set() }
        }
        evidenceByTeam[team].total.add(artifact.id)
        if (tracking?.is_tracked) {
          evidenceByTeam[team].tracked.add(artifact.id)
        }
      })
    })

    const evidenceByTeamCounts: Record<string, { total: number; tracked: number }> = {}
    Object.entries(evidenceByTeam).forEach(([team, data]) => {
      evidenceByTeamCounts[team] = {
        total: data.total.size,
        tracked: data.tracked.size
      }
    })

    // Evidence Collection Maturity Distribution
    const evidenceMaturityDistribution: Record<EvidenceMaturityLevel, number> = {
      L0: 0, L1: 0, L2: 0, L3: 0, L4: 0, L5: 0
    }
    uniqueEvidence.forEach(evidenceId => {
      const tracking = getEvidenceTracking(scopingData, evidenceId)
      if (tracking?.maturity_level) {
        evidenceMaturityDistribution[tracking.maturity_level]++
      }
    })

    return {
      selectedCount,
      topDomains,
      statusCounts,
      implementedPercentage,
      controlsByTeam,
      maturityCounts,
      averageMaturity,
      totalEvidence,
      trackedEvidence,
      evidencePercentage,
      evidenceByTeamCounts,
      frameworkStats,
      evidenceMaturityDistribution
    }
  }, [controls, scopingData])
}
