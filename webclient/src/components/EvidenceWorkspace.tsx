import { useEffect, useState } from 'react'
import type {
  EnrichedControl,
  ScopedControlsFile,
  CollectionInterfacesFile,
  ERLFile,
  EvidenceTemplatesFile,
} from '../types'
import EvidenceReview from './EvidenceReview'
import EvidenceDashboardTab from './evidence/EvidenceDashboardTab'
import {
  evidenceItemSearch,
  pushSearch,
  readAppLocation,
  replaceSearch,
  withEvidenceView,
} from '../data/appUrl'
import type { EvidenceView } from '../data/appUrl'
import TabRow from './explorer/TabRow'
import type { TabRowItem } from './explorer/TabRow'

interface EvidenceWorkspaceProps {
  controls: EnrichedControl[]
  scopingData: ScopedControlsFile
  onScopingDataChange: (data: ScopedControlsFile) => void
  erlData?: ERLFile
  evidenceTemplates?: EvidenceTemplatesFile
  organizationId: string
  /** Opens the Systems Registry. Optional — see `SystemSelectStep`. */
  onNavigateToSystems?: () => void
}

export default function EvidenceWorkspace({
  controls,
  scopingData,
  onScopingDataChange,
  erlData,
  evidenceTemplates,
  organizationId,
  onNavigateToSystems,
}: EvidenceWorkspaceProps) {
  // Seeded from the URL rather than defaulted (#785). Defaulting to the
  // dashboard and correcting in an effect would flash the wrong sub-screen and,
  // worse, mount EvidenceReview a render late — after its own "select the first
  // item" effect had already claimed the selection a deep link asked for.
  const [activeTab, setActiveTab] = useState<EvidenceView>(
    () => readAppLocation(window.location.search).evidenceView,
  )

  // `replaceState`: the sub-tabs are two halves of one screen, not two places.
  const selectTab = (tab: EvidenceView) => {
    setActiveTab(tab)
    replaceSearch(withEvidenceView(window.location.search, tab))
  }

  const handleNavigateToEvidence = (evidenceId: string) => {
    pushSearch(evidenceItemSearch(window.location.search, evidenceId))
    setActiveTab('workspace')
  }

  // Back and Forward across the sub-tabs. Reads only `view`: App owns `tab` and
  // EvidenceReview owns `item`, each with its own listener on the same event.
  useEffect(() => {
    const onPopState = () =>
      setActiveTab(readAppLocation(window.location.search).evidenceView)
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const EVIDENCE_TABS: TabRowItem[] = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'workspace', label: 'Workspace' },
  ]

  return (
    <div className="evidence-workspace">
      <TabRow
        tabs={EVIDENCE_TABS}
        activeId={activeTab}
        onSelect={(id) => selectTab(id as EvidenceView)}
        aria-label="Evidence sub-tabs"
      />

      <div className="evidence-workspace-content">
        {activeTab === 'workspace' && (
          <EvidenceReview
            controls={controls}
            scopingData={scopingData}
            onScopingDataChange={onScopingDataChange}
            erlData={erlData}
            evidenceTemplates={evidenceTemplates}
            onNavigateToSystems={onNavigateToSystems}
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
