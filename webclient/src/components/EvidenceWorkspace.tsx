import { useState } from 'react'
import type {
  EnrichedControl,
  ScopedControlsFile,
  CollectionInterfacesFile,
  ERLFile,
  EvidenceTemplatesFile,
} from '../types'
import EvidenceReview from './EvidenceReview'
import EvidenceDashboardTab from './evidence/EvidenceDashboardTab'

type EvidenceTab = 'workspace' | 'dashboard'

interface EvidenceWorkspaceProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onScopingDataChange: (data: ScopedControlsFile) => void
  collectionInterfaces?: CollectionInterfacesFile
  erlData?: ERLFile
  evidenceTemplates?: EvidenceTemplatesFile
  organizationId: string
}

export default function EvidenceWorkspace({
  controls,
  scopingData,
  onScopingDataChange,
  collectionInterfaces,
  erlData,
  evidenceTemplates,
  organizationId,
}: EvidenceWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<EvidenceTab>('dashboard')

  const handleNavigateToEvidence = (evidenceId: string) => {
    sessionStorage.setItem('navigate_to_evidence', evidenceId)
    setActiveTab('workspace')
  }

  return (
    <div className="evidence-workspace">
      <div className="evidence-workspace-tabs">
        <button
          className={`evidence-workspace-tab ${activeTab === 'dashboard' ? 'active' : ''}`}
          onClick={() => setActiveTab('dashboard')}
        >
          Dashboard
        </button>
        <button
          className={`evidence-workspace-tab ${activeTab === 'workspace' ? 'active' : ''}`}
          onClick={() => setActiveTab('workspace')}
        >
          Workspace
        </button>
      </div>

      <div className="evidence-workspace-content">
        {activeTab === 'workspace' && (
          <EvidenceReview
            controls={controls}
            scopingData={scopingData}
            onScopingDataChange={onScopingDataChange}
            collectionInterfaces={collectionInterfaces}
            erlData={erlData}
            evidenceTemplates={evidenceTemplates}
          />
        )}

        {activeTab === 'dashboard' && (
          <EvidenceDashboardTab
            organizationId={organizationId}
            controls={controls}
            scopingData={scopingData}
            onNavigateToEvidence={handleNavigateToEvidence}
          />
        )}
      </div>
    </div>
  )
}
