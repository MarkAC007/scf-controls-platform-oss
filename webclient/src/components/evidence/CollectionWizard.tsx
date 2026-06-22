import { useState, useEffect } from 'react'
import {
  SystemSelectStep,
  ConfigureCollectionStep,
  GenerateEndpointStep,
  ReviewExportStep,
} from './CollectionWizardSteps'
import { getSystems } from '../../data/apiClient'
import type { System } from '../../types'

// ---- Wizard state machine ----

type WizardPhase = 'select_system' | 'configure' | 'generate' | 'review'

interface WizardState {
  phase: WizardPhase
  selectedSystem: System | null
  collectionMethod: 'manual' | 'automated' | null
  frequency: string
  evidenceIds: string[]
  endpointId: string | null
  endpointUrl: string | null
  secretKey: string | null
  secretPrefix: string | null
  testResult: 'idle' | 'testing' | 'success' | 'error'
}

const initialState: WizardState = {
  phase: 'select_system',
  selectedSystem: null,
  collectionMethod: null,
  frequency: 'monthly',
  evidenceIds: [],
  endpointId: null,
  endpointUrl: null,
  secretKey: null,
  secretPrefix: null,
  testResult: 'idle',
}

// ---- Props ----

interface CollectionWizardProps {
  orgId: string
  onClose: () => void
  onComplete?: () => void
}

// ---- Progress indicator ----

const STEPS = [
  { key: 'select_system', label: 'Select System' },
  { key: 'configure', label: 'Configure' },
  { key: 'generate', label: 'Generate' },
  { key: 'review', label: 'Review' },
] as const

function StepIndicator({ currentPhase }: { currentPhase: WizardPhase }) {
  const currentIdx = STEPS.findIndex(s => s.key === currentPhase)
  return (
    <div className="wizard-steps">
      {STEPS.map((step, idx) => (
        <div
          key={step.key}
          className={`wizard-step ${idx < currentIdx ? 'completed' : ''} ${idx === currentIdx ? 'active' : ''}`}
        >
          <div className="wizard-step-number">
            {idx < currentIdx ? '\u2713' : idx + 1}
          </div>
          <span className="wizard-step-label">{step.label}</span>
          {idx < STEPS.length - 1 && <div className="wizard-step-connector" />}
        </div>
      ))}
    </div>
  )
}

// ---- Main component ----

export function CollectionWizard({ orgId, onClose, onComplete }: CollectionWizardProps) {
  const [state, setState] = useState<WizardState>(initialState)
  const [systems, setSystems] = useState<System[]>([])
  const [loadingSystems, setLoadingSystems] = useState(true)

  useEffect(() => {
    getSystems(orgId)
      .then((data) => setSystems(data))
      .catch(() => setSystems([]))
      .finally(() => setLoadingSystems(false))
  }, [orgId])

  const updateState = (partial: Partial<WizardState>) => {
    setState(prev => ({ ...prev, ...partial }))
  }

  const goBack = () => {
    const phases: WizardPhase[] = ['select_system', 'configure', 'generate', 'review']
    const currentIdx = phases.indexOf(state.phase)
    if (currentIdx > 0) {
      updateState({ phase: phases[currentIdx - 1] })
    }
  }

  return (
    <div className="wizard-overlay" onClick={onClose}>
      <div className="wizard-modal" onClick={e => e.stopPropagation()}>
        <div className="wizard-header">
          <h2>Set Up Evidence Collection</h2>
          <button className="wizard-close" onClick={onClose} aria-label="Close">&times;</button>
        </div>

        <StepIndicator currentPhase={state.phase} />

        <div className="wizard-content">
          {state.phase === 'select_system' && loadingSystems && (
            <div className="wizard-loading">
              <div className="loading-spinner" />
              <p>Loading systems...</p>
            </div>
          )}

          {state.phase === 'select_system' && !loadingSystems && (
            <SystemSelectStep
              systems={systems}
              selectedSystem={state.selectedSystem}
              onSelect={(system) => updateState({ selectedSystem: system })}
              onNext={() => updateState({ phase: 'configure' })}
            />
          )}

          {state.phase === 'configure' && (
            <ConfigureCollectionStep
              collectionMethod={state.collectionMethod}
              frequency={state.frequency}
              evidenceIds={state.evidenceIds}
              onUpdate={(updates) => updateState(updates)}
              onBack={goBack}
              onNext={() => updateState({ phase: 'generate' })}
            />
          )}

          {state.phase === 'generate' && (
            <GenerateEndpointStep
              orgId={orgId}
              systemName={state.selectedSystem?.name || 'Unknown'}
              evidenceIds={state.evidenceIds}
              state={state}
              onUpdate={(updates) => updateState(updates)}
              onBack={goBack}
              onNext={() => updateState({ phase: 'review' })}
            />
          )}

          {state.phase === 'review' && (
            <ReviewExportStep
              state={state}
              onBack={goBack}
              onDone={() => {
                onComplete?.()
                onClose()
              }}
            />
          )}
        </div>
      </div>
    </div>
  )
}
