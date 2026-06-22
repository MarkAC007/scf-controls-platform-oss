import type { ScopedControlsFile, ScopedControl, EvidenceTracking, EvidenceId } from '../types'
import * as api from './apiClient'

/**
 * Load scoped controls from the API
 * Returns null if no scoping data exists (empty scoped_controls array)
 */
export async function loadScopedControls(): Promise<ScopedControlsFile | null> {
  try {
    // Get organization info
    const org = await api.getCurrentOrganization()

    // Get scoped controls from API
    const scopedControls = await api.getScopedControls()

    // Get evidence tracking from API
    const evidenceTrackingList = await api.getEvidenceTracking()

    // Convert evidence tracking array to object keyed by evidence_id
    const evidenceTracking: Record<EvidenceId, EvidenceTracking> = {}
    evidenceTrackingList.forEach(item => {
      evidenceTracking[item.evidence_id] = {
        id: item.id,  // Include database ID for assignments/comments
        is_tracked: item.is_tracked ?? undefined,
        method_of_collection: item.method_of_collection ?? undefined,
        collecting_system: item.collecting_system ?? undefined,
        owner: item.owner ?? undefined,
        frequency: item.frequency ?? undefined,
        comments: item.comments ?? undefined
      }
    })

    // Convert API response to ScopedControlsFile format
    const data: ScopedControlsFile = {
      organizationId: org.id,  // Add database org ID for components
      organization: {
        id: org.id,
        name: org.name,
        created_at: org.created_at,
        updated_at: org.updated_at
      },
      scoped_controls: scopedControls.map(control => ({
        id: control.id,  // Include database ID for assignments/comments
        scf_id: control.scf_id,
        selected: control.selected,
        selection_reason: control.selection_reason ?? undefined,
        implementation_status: (control.implementation_status ?? undefined) as ScopedControl['implementation_status'],
        priority: (control.priority ?? undefined) as ScopedControl['priority'],
        owner: (control.owner ?? undefined) as ScopedControl['owner'],
        assigned_to: control.assigned_to ?? undefined,
        maturity_level: (control.maturity_level ?? undefined) as ScopedControl['maturity_level'],
        target_date: control.target_date ?? undefined,
        completion_date: control.completion_date ?? undefined,
        implementation_notes: control.implementation_notes ?? undefined,
        related_documentation: (control.related_documentation ?? undefined) as ScopedControl['related_documentation'],
        custom_fields: control.custom_fields ?? undefined,
        // SCF v4 fields
        control_weighting: control.control_weighting ?? undefined,
        validation_cadence: control.validation_cadence ?? undefined,
        nist_csf_function: (control.nist_csf_function ?? undefined) as ScopedControl['nist_csf_function'],
        control_question: control.control_question ?? undefined,
        pptdf_people: control.pptdf_people ?? undefined,
        pptdf_process: control.pptdf_process ?? undefined,
        pptdf_technology: control.pptdf_technology ?? undefined,
        pptdf_data: control.pptdf_data ?? undefined,
        pptdf_facility: control.pptdf_facility ?? undefined
      })),
      evidence_tracking: evidenceTracking,
      metadata: {
        total_controls: scopedControls.length,
        total_selected: scopedControls.filter(c => c.selected).length,
        total_implemented: scopedControls.filter(c => c.implementation_status === 'implemented').length,
        last_updated: org.updated_at
      }
    }

    // Return null if no scoping data
    if (data.scoped_controls.length === 0) {
      console.log('No scoped controls found')
      return null
    }

    return data
  } catch (error) {
    console.error('Failed to load scoped controls from API:', error)
    throw error
  }
}

/**
 * Normalize a scoped control to include all fields (with null for missing ones)
 * Note: evidence_refs and frameworks_driving_selection are excluded as they're not used in the UI
 */
function normalizeScopedControl(control: ScopedControl): Omit<ScopedControl, 'evidence_refs' | 'frameworks_driving_selection'> {
  return {
    id: control.id,  // Preserve database ID
    scf_id: control.scf_id,
    selected: control.selected,
    selection_reason: control.selection_reason ?? undefined,
    implementation_status: control.implementation_status ?? undefined,
    priority: control.priority ?? undefined,
    owner: control.owner ?? undefined,
    assigned_to: control.assigned_to ?? undefined,
    maturity_level: control.maturity_level ?? undefined,
    target_date: control.target_date ?? undefined,
    completion_date: control.completion_date ?? undefined,
    implementation_notes: control.implementation_notes ?? undefined,
    related_documentation: control.related_documentation ?? undefined,
    custom_fields: control.custom_fields ?? undefined,
    // SCF v4 fields
    control_weighting: control.control_weighting ?? undefined,
    validation_cadence: control.validation_cadence ?? undefined,
    nist_csf_function: control.nist_csf_function ?? undefined,
    control_question: control.control_question ?? undefined,
    pptdf_people: control.pptdf_people ?? undefined,
    pptdf_process: control.pptdf_process ?? undefined,
    pptdf_technology: control.pptdf_technology ?? undefined,
    pptdf_data: control.pptdf_data ?? undefined,
    pptdf_facility: control.pptdf_facility ?? undefined
  }
}

/**
 * Normalize evidence tracking to include all fields (with null for missing ones)
 */
function normalizeEvidenceTracking(tracking: EvidenceTracking): EvidenceTracking {
  return {
    id: tracking.id,  // Preserve database ID
    is_tracked: tracking.is_tracked ?? undefined,
    method_of_collection: tracking.method_of_collection ?? undefined,
    collecting_system: tracking.collecting_system ?? undefined,
    owner: tracking.owner ?? undefined,
    frequency: tracking.frequency ?? undefined,
    comments: tracking.comments ?? undefined
  }
}

/**
 * Normalize the entire scoped controls file to include all fields
 */
function normalizeScopedControlsFile(data: ScopedControlsFile): Omit<ScopedControlsFile, 'scoped_controls'> & {
  scoped_controls: Omit<ScopedControl, 'evidence_refs' | 'frameworks_driving_selection'>[]
} {
  // Normalize all scoped controls
  const normalizedControls = data.scoped_controls.map(normalizeScopedControl)

  // Normalize all evidence tracking entries
  const normalizedEvidenceTracking: Record<EvidenceId, EvidenceTracking> = {}
  Object.entries(data.evidence_tracking || {}).forEach(([evidenceId, tracking]) => {
    normalizedEvidenceTracking[evidenceId] = normalizeEvidenceTracking(tracking)
  })

  return {
    organization: data.organization,
    scoped_controls: normalizedControls,
    evidence_tracking: normalizedEvidenceTracking,
    metadata: data.metadata
  }
}

/**
 * Save scoped controls to the API
 */
export async function saveScopedControls(data: ScopedControlsFile): Promise<boolean> {
  try {
    // This is now a no-op since saves happen immediately on each change
    // The function is kept for backward compatibility with the UI
    console.log('✅ Changes saved to database')
    return true
  } catch (error) {
    console.error('Failed to save scoped controls:', error)
    return false
  }
}

/**
 * Export scoped controls as downloadable JSON file with complete structure
 */
export function exportScopedControls(data: ScopedControlsFile): void {
  // Normalize the data to include all fields
  const normalizedData = normalizeScopedControlsFile(data)

  const blob = new Blob([JSON.stringify(normalizedData, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `scoped_controls_${new Date().toISOString().split('T')[0]}.json`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

/**
 * Get a single scoped control by SCF ID
 */
export function getScopedControl(
  data: ScopedControlsFile,
  scf_id: string
): ScopedControl | undefined {
  return data.scoped_controls.find(c => c.scf_id === scf_id)
}

/**
 * Update or add a scoped control
 * Now saves directly to API
 */
export async function updateScopedControl(
  data: ScopedControlsFile,
  scopedControl: ScopedControl
): Promise<ScopedControlsFile> {
  try {
    // Save to API
    const updatedControl = await api.createOrUpdateScopedControl({
      scf_id: scopedControl.scf_id,
      selected: scopedControl.selected,
      selection_reason: scopedControl.selection_reason,
      implementation_status: scopedControl.implementation_status,
      priority: scopedControl.priority,
      owner: scopedControl.owner,
      assigned_to: scopedControl.assigned_to,
      maturity_level: scopedControl.maturity_level,
      target_date: scopedControl.target_date,
      completion_date: scopedControl.completion_date,
      implementation_notes: scopedControl.implementation_notes,
      related_documentation: scopedControl.related_documentation,
      custom_fields: scopedControl.custom_fields,
      // SCF v4 fields
      control_weighting: scopedControl.control_weighting,
      validation_cadence: scopedControl.validation_cadence,
      nist_csf_function: scopedControl.nist_csf_function,
      control_question: scopedControl.control_question
    })

    // Update local data with response (includes database ID)
    const index = data.scoped_controls.findIndex(c => c.scf_id === scopedControl.scf_id)

    // Merge full server response — convert nulls to undefined for type compatibility
    const normalized = Object.fromEntries(
      Object.entries(updatedControl).map(([k, v]) => [k, v === null ? undefined : v])
    )
    const controlWithId = {
      ...scopedControl,
      ...normalized,  // Preserves server-computed fields like completion_date, updated_at
    }

    if (index >= 0) {
      data.scoped_controls[index] = controlWithId
    } else {
      data.scoped_controls.push(controlWithId)
    }

    // Update metadata
    data.metadata.total_selected = data.scoped_controls.filter(c => c.selected).length
    data.metadata.total_implemented = data.scoped_controls.filter(
      c => c.implementation_status === 'implemented'
    ).length

    return data
  } catch (error) {
    console.error('Failed to update scoped control:', error)
    throw error
  }
}

/**
 * Get evidence tracking data for a specific evidence item
 */
export function getEvidenceTracking(
  data: ScopedControlsFile,
  evidenceId: EvidenceId
): EvidenceTracking | undefined {
  return data.evidence_tracking?.[evidenceId]
}

/**
 * Update or add evidence tracking data
 * Now saves directly to API
 */
export async function updateEvidenceTracking(
  data: ScopedControlsFile,
  evidenceId: EvidenceId,
  tracking: EvidenceTracking
): Promise<ScopedControlsFile> {
  try {
    // Save to API
    const updatedTracking = await api.createOrUpdateEvidenceTracking({
      evidence_id: evidenceId,
      is_tracked: tracking.is_tracked,
      method_of_collection: tracking.method_of_collection,
      collecting_system: tracking.collecting_system,
      owner: tracking.owner,
      frequency: tracking.frequency,
      comments: tracking.comments
    })

    // Update local data with response (includes database ID)
    if (!data.evidence_tracking) {
      data.evidence_tracking = {}
    }

    // Merge full server response — convert nulls to undefined for type compatibility
    const normalizedTracking = Object.fromEntries(
      Object.entries(updatedTracking).map(([k, v]) => [k, v === null ? undefined : v])
    )
    data.evidence_tracking[evidenceId] = {
      ...tracking,
      ...normalizedTracking,  // Preserves server-computed fields
    }

    return data
  } catch (error) {
    console.error('Failed to update evidence tracking:', error)
    throw error
  }
}
