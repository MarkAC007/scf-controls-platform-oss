import { useState, useCallback, useEffect, useRef } from 'react'
import type {
  EvidenceId,
  EvidenceSuggestionsResponse,
  CollectionInterface,
  EvidenceMaturityLevel,
  CollectionGuidanceResponse,
} from '../../types'
import {
  getEvidenceSuggestions,
  submitRecipeFeedback,
  generateSystemRecipes,
  getRecipeGenerationStatus,
} from '../../data/apiClient'
import { RecipeCard } from './RecipeCard'
import { MaturityBadge } from '../maturity/MaturityBadge'

// Poll the generation status every 3s for at most ~3 minutes
const GENERATION_POLL_INTERVAL_MS = 3000
const GENERATION_POLL_MAX_ATTEMPTS = 60

interface CollectionGuidancePanelProps {
  evidenceId: EvidenceId
  suggestions: EvidenceSuggestionsResponse | null
  loadingSuggestions: boolean
  collectionMethods: { id: string; ci: CollectionInterface }[]
  currentMaturityLevel?: EvidenceMaturityLevel
  orgId?: string
  onSystemSelect: (systemName: string) => void
}

export function CollectionGuidancePanel({
  evidenceId,
  suggestions,
  loadingSuggestions,
  collectionMethods,
  currentMaturityLevel,
  orgId,
  onSystemSelect,
}: CollectionGuidancePanelProps) {
  const [selectedSystemId, setSelectedSystemId] = useState<string | null>(null)
  const [guidance, setGuidance] = useState<CollectionGuidanceResponse | null>(null)
  const [loadingGuidance, setLoadingGuidance] = useState(false)
  const [feedbackSubmitted, setFeedbackSubmitted] = useState<string | null>(null)
  const [generationState, setGenerationState] = useState<'idle' | 'requesting' | 'generating' | 'failed'>('idle')
  const [generationError, setGenerationError] = useState<string | null>(null)
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => () => {
    if (pollTimerRef.current) clearTimeout(pollTimerRef.current)
  }, [])

  const loadGuidance = useCallback(async (systemId: string) => {
    setLoadingGuidance(true)
    try {
      const result = await getEvidenceSuggestions(evidenceId, orgId, {
        systemId,
        maturityLevel: currentMaturityLevel || 'L1',
      })
      setGuidance(result.collection_guidance || null)
    } catch (error) {
      console.error('Failed to load collection guidance:', error)
      setGuidance(null)
    } finally {
      setLoadingGuidance(false)
    }
  }, [evidenceId, orgId, currentMaturityLevel])

  const cancelGenerationPoll = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const handleSystemClick = useCallback(async (systemId: string, systemName: string) => {
    // A poll for a previously selected system must never overwrite the
    // guidance of the newly selected one
    cancelGenerationPoll()

    if (selectedSystemId === systemId) {
      // Deselect — back to discovery mode
      setSelectedSystemId(null)
      setGuidance(null)
      setGenerationState('idle')
      return
    }

    setSelectedSystemId(systemId)
    setFeedbackSubmitted(null)
    setGenerationState('idle')
    setGenerationError(null)
    await loadGuidance(systemId)
  }, [selectedSystemId, loadGuidance, cancelGenerationPoll])

  const handleGenerate = useCallback(async () => {
    if (!selectedSystemId || !orgId) return
    cancelGenerationPoll()
    setGenerationState('requesting')
    setGenerationError(null)

    try {
      await generateSystemRecipes(selectedSystemId, orgId)
    } catch (error: any) {
      console.error('Failed to queue recipe generation:', error)
      setGenerationState('failed')
      const msg: string = error?.message || ''
      setGenerationError(
        msg.includes('403') || /forbidden/i.test(msg)
          ? 'Generating guidance requires editor access to this organisation.'
          : msg || 'Could not start generation'
      )
      return
    }

    setGenerationState('generating')
    const systemId = selectedSystemId
    let attempts = 0

    const poll = async () => {
      attempts += 1
      try {
        const status = await getRecipeGenerationStatus(systemId, orgId)
        if (status.status === 'completed') {
          setGenerationState('idle')
          await loadGuidance(systemId)
          return
        }
        if (status.status === 'failed') {
          setGenerationState('failed')
          setGenerationError(status.error || 'Generation failed')
          return
        }
      } catch (error) {
        console.error('Failed to poll generation status:', error)
      }
      if (attempts >= GENERATION_POLL_MAX_ATTEMPTS) {
        setGenerationState('failed')
        setGenerationError('Generation timed out — try refreshing later')
        return
      }
      pollTimerRef.current = setTimeout(poll, GENERATION_POLL_INTERVAL_MS)
    }

    pollTimerRef.current = setTimeout(poll, GENERATION_POLL_INTERVAL_MS)
  }, [selectedSystemId, orgId, loadGuidance])

  const handleFeedback = useCallback(async (feedbackType: 'helpful' | 'not_matching') => {
    if (!guidance || !orgId) return
    try {
      await submitRecipeFeedback(evidenceId, {
        system_type: guidance.system_type,
        vendor: guidance.vendor,
        feedback_type: feedbackType,
        maturity_level: guidance.current_maturity as EvidenceMaturityLevel,
      }, orgId)
      setFeedbackSubmitted(feedbackType)
    } catch (error) {
      console.error('Failed to submit feedback:', error)
    }
  }, [guidance, orgId, evidenceId])

  const isGuidanceMode = selectedSystemId && guidance

  const generateSection = orgId ? (
    <div className="recipe-generate-section">
      {generationState === 'generating' || generationState === 'requesting' ? (
        <p className="recipe-generate-hint">
          <span className="spinner" /> Generating guidance from vendor documentation… this can take a couple of minutes.
        </p>
      ) : (
        <>
          {guidance && (
            <p className="recipe-generate-hint">
              This is generic guidance for the system type. Generate guidance
              tailored to {guidance.system_name} from its vendor documentation.
            </p>
          )}
          <button
            type="button"
            className="recipe-generate-btn"
            onClick={handleGenerate}
          >
            {'✨'} Generate collection guidance for this system
          </button>
          {generationState === 'failed' && generationError && (
            <p className="recipe-generate-error">{generationError}</p>
          )}
        </>
      )}
    </div>
  ) : null

  return (
    <div className="collection-guidance-panel">
      {/* Collection Suggestions — always shown */}
      <div className="detail-section-container suggestion-section">
        <div className="container-header">
          <span className="container-icon">{'\uD83D\uDCA1'}</span>
          <span className="container-title">Collection Suggestions</span>
          {suggestions?.has_suggestions && (
            <span className="container-count">{suggestions.capable_systems.length}</span>
          )}
        </div>
        <div className="container-content">
          {loadingSuggestions ? (
            <div className="suggestion-loading">Loading suggestions...</div>
          ) : !suggestions?.has_suggestions ? (
            <p className="muted">No systems configured to collect this evidence type</p>
          ) : (
            <>
              <div className="suggestion-systems">
                <div className="suggestion-label">
                  {isGuidanceMode
                    ? 'Select a system for step-by-step guidance:'
                    : 'Systems that can provide this evidence:'}
                </div>
                <div className="suggestion-chips">
                  {suggestions.capable_systems.map(sys => (
                    <button
                      key={sys.system_id}
                      className={`suggestion-chip ${sys.capability_status} ${
                        selectedSystemId === sys.system_id ? 'suggestion-chip-selected' : ''
                      }`}
                      onClick={() => handleSystemClick(sys.system_id, sys.name)}
                      title={
                        selectedSystemId === sys.system_id
                          ? 'Click to deselect'
                          : `Click for ${sys.name} collection guide`
                      }
                    >
                      <span className="chip-status-indicator" />
                      <span className="chip-name">{sys.name}</span>
                      <span className="chip-status">({sys.capability_status})</span>
                      {sys.vendor && <span className="chip-vendor">{sys.vendor}</span>}
                    </button>
                  ))}
                </div>
              </div>

              {!isGuidanceMode && suggestions.recommendation && (
                <div className="suggestion-recommendation">
                  <div className="recommendation-icon">{'\u2728'}</div>
                  <div className="recommendation-content">
                    <strong>Recommendation:</strong> Use {suggestions.recommendation.system_name}
                    <div className="recommendation-reason">{suggestions.recommendation.reason}</div>
                  </div>
                  <button
                    className="recommendation-apply-btn"
                    onClick={() => onSystemSelect(suggestions.recommendation!.system_name)}
                  >
                    Apply
                  </button>
                </div>
              )}

              {suggestions.currently_tracking && (
                <div className="suggestion-current">
                  Currently collecting via: <strong>{suggestions.currently_tracking}</strong>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Guidance Mode — recipe card when system selected */}
      {selectedSystemId && (
        <div className="detail-section-container guidance-section">
          <div className="container-header">
            <span className="container-icon">{'\uD83D\uDCD6'}</span>
            <span className="container-title">Collection Guide</span>
            {guidance && currentMaturityLevel && (
              <MaturityBadge level={currentMaturityLevel} size="small" showTooltip={false} />
            )}
          </div>
          <div className="container-content">
            {loadingGuidance ? (
              <div className="suggestion-loading">Loading collection guide...</div>
            ) : guidance?.recipe ? (
              <>
                <RecipeCard
                  recipe={guidance.recipe}
                  confidence={guidance.recipe_confidence as 'system_specific' | 'vendor_generic' | 'type_generic'}
                />

                {/* Feedback buttons */}
                <div className="recipe-feedback">
                  {feedbackSubmitted ? (
                    <div className="recipe-feedback-thanks">
                      {'\u2705'} Thanks for your feedback!
                    </div>
                  ) : (
                    <>
                      <span className="recipe-feedback-label">Was this helpful?</span>
                      <button
                        className="recipe-feedback-btn recipe-feedback-yes"
                        onClick={() => handleFeedback('helpful')}
                      >
                        {'\uD83D\uDC4D'} This helped
                      </button>
                      <button
                        className="recipe-feedback-btn recipe-feedback-no"
                        onClick={() => handleFeedback('not_matching')}
                      >
                        {'\uD83D\uDC4E'} Didn't match my system
                      </button>
                    </>
                  )}
                </div>

                {/* Next level preview */}
                {guidance.next_level_preview && (
                  <div className="recipe-next-level-preview">
                    <div className="recipe-next-level-header">
                      <span className="recipe-next-level-icon">{'\u2191'}</span>
                      <span>Next Level Preview</span>
                    </div>
                    <div className="recipe-next-level-content">
                      <p className="recipe-next-level-title">{guidance.next_level_preview.title}</p>
                      {guidance.next_level_preview.estimated_time && (
                        <span className="recipe-meta-item">
                          <span className="recipe-meta-icon">{'\u23F1'}</span>
                          {guidance.next_level_preview.estimated_time}
                        </span>
                      )}
                      <p className="recipe-next-level-hint">
                        Upgrade your maturity level to unlock this recipe.
                      </p>
                    </div>
                  </div>
                )}

                {/* Alternative count */}
                {guidance.alternatives_count > 0 && (
                  <p className="recipe-alternatives-hint">
                    {guidance.alternatives_count} other system{guidance.alternatives_count > 1 ? 's' : ''} can also provide this evidence.
                  </p>
                )}

                {/* AI generation for systems with only generic guidance */}
                {guidance.recipe_confidence === 'type_generic' && generateSection}
              </>
            ) : (
              <>
                <p className="muted">No recipe available for this system at the current maturity level.</p>
                {generateSection}
              </>
            )}
          </div>
        </div>
      )}

      {/* Collection Methods — shown in discovery mode or collapsed in guidance mode */}
      {collectionMethods.length > 0 && (
        <div className="detail-section-container collection-methods-section">
          <div className="container-header">
            <span className="container-icon">{'\uD83D\uDD0C'}</span>
            <span className="container-title">Collection Methods</span>
            <span className="container-count">{collectionMethods.length}</span>
          </div>
          <div className="container-content">
            <p className="collection-methods-intro">
              Industry-standard methods to collect this evidence:
            </p>
            <div className="collection-methods-grid">
              {collectionMethods.map(({ id, ci }) => {
                // In guidance mode, highlight maturity-appropriate methods
                const isAppropriate = isGuidanceMode && guidance?.maturity_appropriate_methods?.some(m => m.id === id)

                return (
                  <div
                    key={id}
                    className={`collection-method-card automation-${ci.automation_potential || 'medium'} ${
                      isGuidanceMode && !isAppropriate ? 'collection-method-dimmed' : ''
                    } ${isAppropriate ? 'collection-method-recommended' : ''}`}
                  >
                    <div className="method-card-header">
                      <span className="method-id">{id}</span>
                      {ci.automation_potential && (
                        <span className={`automation-badge ${ci.automation_potential}`}>
                          {ci.automation_potential === 'high' ? '\u26A1' : ci.automation_potential === 'medium' ? '\uD83D\uDD27' : '\u270B'}
                          {ci.automation_potential.toUpperCase()}
                        </span>
                      )}
                      {isAppropriate && (
                        <span className="method-recommended-badge">Recommended</span>
                      )}
                    </div>
                    <div className="method-card-title">{ci.title}</div>
                    <div className="method-card-details">
                      <div className="method-detail">
                        <span className="method-label">Method:</span>
                        <span className="method-value">{ci.collection_method.replace('_', ' ')}</span>
                      </div>
                      <div className="method-detail">
                        <span className="method-label">System Types:</span>
                        <span className="method-value">{ci.system_types.slice(0, 3).join(', ')}{ci.system_types.length > 3 ? '...' : ''}</span>
                      </div>
                      {ci.example_systems && ci.example_systems.length > 0 && (
                        <div className="method-detail">
                          <span className="method-label">Examples:</span>
                          <span className="method-value">{ci.example_systems.slice(0, 3).join(', ')}</span>
                        </div>
                      )}
                      {ci.maturity_range && (
                        <div className="method-detail">
                          <span className="method-label">Maturity:</span>
                          <span className="method-value">{ci.maturity_range.min} - {ci.maturity_range.max}</span>
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
            <p className="collection-methods-hint">
              {'\uD83D\uDCA1'} These are catalog recommendations. Add systems to your Systems Registry to enable automated collection.
            </p>
          </div>
        </div>
      )}
    </div>
  )
}

export default CollectionGuidancePanel
