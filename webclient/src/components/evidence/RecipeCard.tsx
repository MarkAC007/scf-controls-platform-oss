import { useState } from 'react'
import type { CollectionRecipe, RecipeConfidence } from '../../types'
import { RecipeConfidenceBadge } from './RecipeConfidenceBadge'

interface RecipeCardProps {
  recipe: CollectionRecipe
  confidence: RecipeConfidence
  className?: string
}

export function RecipeCard({ recipe, confidence, className = '' }: RecipeCardProps) {
  const [expandedNotes, setExpandedNotes] = useState<Record<number, boolean>>({})

  const toggleNote = (stepNum: number) => {
    setExpandedNotes(prev => ({ ...prev, [stepNum]: !prev[stepNum] }))
  }

  return (
    <div className={`recipe-card ${className}`}>
      <div className="recipe-card-header">
        <div className="recipe-card-title-row">
          <h4 className="recipe-card-title">{recipe.title}</h4>
          <RecipeConfidenceBadge confidence={confidence} />
        </div>
        <div className="recipe-card-meta">
          {recipe.estimated_time && (
            <span className="recipe-meta-item">
              <span className="recipe-meta-icon">{'\u23F1'}</span>
              {recipe.estimated_time}
            </span>
          )}
          {recipe.frequency && (
            <span className="recipe-meta-item">
              <span className="recipe-meta-icon">{'\u21BB'}</span>
              {recipe.frequency}
            </span>
          )}
        </div>
      </div>

      <ol className="recipe-steps">
        {recipe.steps.map((step) => (
          <li key={step.step} className="recipe-step">
            <div className="recipe-step-number">{step.step}</div>
            <div className="recipe-step-content">
              <p className="recipe-step-action">{step.action}</p>

              {step.permissions_required && (
                <span className="recipe-permission-badge">
                  {'\uD83D\uDD12'} {step.permissions_required}
                </span>
              )}

              {(step.security_note || step.audit_note) && (
                <div className="recipe-step-notes">
                  {step.security_note && (
                    <div className="recipe-note-toggle">
                      <button
                        className="recipe-note-btn recipe-note-security"
                        onClick={() => toggleNote(step.step * 10)}
                        aria-expanded={!!expandedNotes[step.step * 10]}
                      >
                        {'\u26A0'} Security Note
                        <span className="recipe-note-chevron">
                          {expandedNotes[step.step * 10] ? '\u25B2' : '\u25BC'}
                        </span>
                      </button>
                      {expandedNotes[step.step * 10] && (
                        <p className="recipe-note-content recipe-note-security-content">
                          {step.security_note}
                        </p>
                      )}
                    </div>
                  )}

                  {step.audit_note && (
                    <div className="recipe-note-toggle">
                      <button
                        className="recipe-note-btn recipe-note-audit"
                        onClick={() => toggleNote(step.step * 10 + 1)}
                        aria-expanded={!!expandedNotes[step.step * 10 + 1]}
                      >
                        {'\u2139'} Audit Note
                        <span className="recipe-note-chevron">
                          {expandedNotes[step.step * 10 + 1] ? '\u25B2' : '\u25BC'}
                        </span>
                      </button>
                      {expandedNotes[step.step * 10 + 1] && (
                        <p className="recipe-note-content recipe-note-audit-content">
                          {step.audit_note}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {step.vendor_docs_url && (
                <a
                  href={step.vendor_docs_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="recipe-docs-link"
                >
                  {'\uD83D\uDCDA'} Vendor Docs
                </a>
              )}
            </div>
          </li>
        ))}
      </ol>
    </div>
  )
}

export default RecipeCard
