import type { ControlsMappingFile, ERLFile, EnrichedControl, ControlGuidance, ResolvedArtifact, FrameworkNameMap, CollectionInterfacesFile, CollectionInterface, EvidenceId, EvidenceTemplatesFile } from '../types'
import { fetchBulkControls, fetchBulkEvidence, buildFrameworkNameMap } from './catalogApi'

const DATA_BASE = '/data'

// Internal SCF mappings to exclude from framework display
// These are risk/threat codes and internal SCF metadata, not external compliance frameworks
const INTERNAL_MAPPING_PREFIXES = [
  'risk_',      // Risk mappings (R-GV-1, R-AC-1, etc.)
  'threat_',    // Threat mappings (NT-1, MT-1, etc.)
  'scf_core_',  // SCF core profiles
  'control_threat_summary',  // Summary field
  'risk_threat_summary',     // Summary field
  'minimum_security_requirements_mcr_dsr',  // Internal
  'identify_',   // MCR/DSR identification
  'errata_',     // Version errata
]

// Helper function to check if a framework key is internal/should be filtered
function isInternalMapping(frameworkKey: string): boolean {
  return INTERNAL_MAPPING_PREFIXES.some(prefix => frameworkKey.startsWith(prefix))
}

export async function loadCollectionInterfaces(): Promise<CollectionInterfacesFile> {
  const res = await fetch(`${DATA_BASE}/collection_interfaces.json`)
  if (!res.ok) throw new Error('Failed to load collection_interfaces.json')
  return res.json()
}

export async function loadEvidenceTemplates(): Promise<EvidenceTemplatesFile> {
  const res = await fetch(`${DATA_BASE}/evidence_templates.json`)
  if (!res.ok) return {}  // Graceful fallback — templates are optional
  return res.json()
}

export function getInterfacesForEvidence(
  evidenceId: EvidenceId,
  erl: ERLFile,
  interfaces: CollectionInterfacesFile
): { id: string; interface: CollectionInterface }[] {
  const evidenceItem = erl[evidenceId]
  if (!evidenceItem?.collection_interfaces) return []

  return evidenceItem.collection_interfaces
    .map(id => {
      const ci = interfaces[id]
      return ci ? { id, interface: ci } : null
    })
    .filter((item): item is { id: string; interface: CollectionInterface } => item !== null)
}

/**
 * Load all data from catalog API.
 */
export async function loadAllData(): Promise<{
  controls: ControlGuidance[]
  mappings: ControlsMappingFile
  erl: ERLFile
  frameworkNames: FrameworkNameMap
  collectionInterfaces: CollectionInterfacesFile
  evidenceTemplates: EvidenceTemplatesFile
}> {
  const [controls, erl, collectionInterfaces, evidenceTemplates] = await Promise.all([
    fetchBulkControls(),
    fetchBulkEvidence(),
    loadCollectionInterfaces(),
    loadEvidenceTemplates(),
  ])

  const frameworkNames = buildFrameworkNameMap(controls)

  return { controls, mappings: {}, erl, frameworkNames, collectionInterfaces, evidenceTemplates }
}

export function enrichControls(
  controls: ControlGuidance[],
  mappings: ControlsMappingFile,
  erl: ERLFile,
  frameworkNames: FrameworkNameMap
): EnrichedControl[] {
  return controls.map((c) => enrichControl(c, mappings, erl, frameworkNames))
}

export function enrichControl(
  control: ControlGuidance,
  mappings: ControlsMappingFile,
  erl: ERLFile,
  frameworkNames: FrameworkNameMap
): EnrichedControl {
  const controlId = control.scf_id
  const mapEntry = control.framework_mappings || mappings[controlId] || {}
  const frameworksResolved: { [framework: string]: string[] } = {}
  let frameworksCount = 0
  for (const [fwRefId, refs] of Object.entries(mapEntry)) {
    // Skip internal mappings (risk_, threat_, etc.) - these are shown in RiskThreatContext
    if (isInternalMapping(fwRefId)) {
      continue
    }
    if (Array.isArray(refs) && refs.length > 0) {
      const baseId = fwRefId.endsWith('_ref') ? fwRefId.slice(0, -4) : fwRefId
      const friendly = frameworkNames[baseId] || baseId
      frameworksResolved[friendly] = refs
      frameworksCount += 1
    }
  }

  const evidenceIds = control.evidence_requests || control.audit_artifacts || []
  const artifactsResolved: ResolvedArtifact[] = evidenceIds
    .map((id) => {
      const entry = erl[id]
      if (!entry) return null
      return {
        id,
        title: entry.artifact_title || entry.evidence_title || '',
        domain: entry.area_of_focus || entry.evidence_domain || ''
      }
    })
    .filter(Boolean) as ResolvedArtifact[]

  return {
    ...control,
    artifactsResolved,
    frameworksResolved,
    frameworksCount
  }
}
