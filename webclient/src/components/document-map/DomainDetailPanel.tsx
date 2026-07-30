import { useEffect } from 'react'
import type { CDMDocumentMapDomain } from '../../data/apiClient'
import { orderDocuments } from './documentOrder'
import { CheckGlyph, CloseGlyph, DocGlyph, RingGlyph } from './icons'

/**
 * Slide-over detail for one domain — mirrors the risk register's overlay
 * geometry and transition so the gesture is already familiar.
 */

interface DomainDetailPanelProps {
  domain: CDMDocumentMapDomain | null
  onClose: () => void
  onOpenDocuments: () => void
}

interface LadderRowProps {
  label: string
  value: number
  /** Denominator for the proportion bar. Omit for a count with no meaningful whole. */
  of?: number
  kind: 'docs' | 'conf' | 'prop'
}

function LadderRow({ label, value, of, kind }: LadderRowProps) {
  const showBar = typeof of === 'number' && of > 0
  return (
    <div className={`dm-ladder-row dm-ladder-row-${kind}`}>
      <span className="dm-ladder-key">{label}</span>
      {showBar ? (
        <span className="dm-ladder-track">
          <span style={{ width: `${Math.min(100, Math.round((value / of!) * 100))}%` }} />
        </span>
      ) : (
        /* A count, not a ratio — a full-width bar would imply a completeness
           that has no denominator behind it. */
        <span className="dm-ladder-rule" />
      )}
      <span className="dm-ladder-val">
        {value}
        {showBar && <span className="dm-ladder-of"> of {of}</span>}
      </span>
    </div>
  )
}

export default function DomainDetailPanel({
  domain,
  onClose,
  onOpenDocuments,
}: DomainDetailPanelProps) {
  useEffect(() => {
    if (!domain) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [domain, onClose])

  const scoped = domain?.scoped_control_counts

  return (
    <div className={`dm-overlay ${domain ? 'visible' : ''}`} hidden={!domain}>
      <div className="dm-backdrop" onClick={onClose} />
      <div className="dm-panel-container">
        <div
          className="dm-panel"
          role="dialog"
          aria-modal="true"
          aria-label={domain ? `${domain.domain} — ${domain.name}` : 'Domain detail'}
        >
          <div className="dm-panel-top">
            <span className="dm-kicker">Domain detail</span>
            <button
              type="button"
              className="dm-panel-close"
              onClick={onClose}
              aria-label="Close panel"
            >
              <CloseGlyph />
            </button>
          </div>

          {domain && (
            <div className="dm-panel-scroll">
              <div className="dm-panel-ref">
                <div className="dm-panel-ref-head">
                  <span className="dm-kicker">SCF Reference</span>
                  <span className="dm-scf-tag">SCF catalog</span>
                </div>
                <h3>
                  <span className="dm-panel-ref-code">{domain.domain}</span> {domain.name}
                </h3>
                <p className="dm-panel-scope">
                  {scoped && scoped.selected > 0
                    ? `${scoped.selected} of ${scoped.total} catalogue control${scoped.total === 1 ? '' : 's'} scoped in this domain.`
                    : 'No controls from this domain are scoped for your organisation.'}
                </p>
              </div>

              {domain.documents.length > 0 ? (
                <div className="dm-panel-bench">
                  <span className="dm-bench-header">Documents placed here</span>
                  {orderDocuments(domain.documents).map((doc) => {
                    const confirmed = doc.intent_source === 'confirmed'
                    return (
                      <div key={doc.cdm_document_id} className="dm-doc-row">
                        <span className="dm-doc-icon">
                          <DocGlyph />
                        </span>
                        <span className="dm-doc-main">
                          <span className="dm-doc-name">{doc.filename}</span>
                          <span className="dm-doc-meta">
                            {doc.mapping_counts.accepted > 0
                              ? `${doc.mapping_counts.accepted} confirmed`
                              : 'None confirmed yet'}
                            {doc.mapping_counts.proposed > 0 &&
                              ` · ${doc.mapping_counts.proposed} awaiting review`}
                          </span>
                          {/*
                            The domain was confirmed here without the document
                            having been placed here to begin with — a person put
                            it here during review. Worth stating, because it is
                            otherwise indistinguishable from a routine placement.
                          */}
                          {confirmed && !doc.claimed_by_model && (
                            <span className="dm-doc-origin">Placed during review</span>
                          )}
                        </span>
                        <span
                          className={`dm-chip ${
                            confirmed ? 'dm-chip-confirmed' : 'dm-chip-suggested'
                          }`}
                        >
                          {confirmed ? <CheckGlyph size={11} /> : <RingGlyph size={11} />}
                          {confirmed ? 'Confirmed' : 'Suggested'}
                        </span>
                      </div>
                    )
                  })}

                  <div className="dm-ladder">
                    <LadderRow label="Documents placed" value={domain.totals.documents} kind="docs" />
                    {domain.totals.controls_with_accepted_mapping > 0 && (
                      <LadderRow
                        label="Mappings confirmed"
                        value={domain.totals.controls_with_accepted_mapping}
                        of={scoped?.selected}
                        kind="conf"
                      />
                    )}
                    {domain.totals.controls_with_proposed_mapping > 0 && (
                      <LadderRow
                        label="Awaiting review"
                        value={domain.totals.controls_with_proposed_mapping}
                        of={scoped?.selected}
                        kind="prop"
                      />
                    )}
                  </div>
                </div>
              ) : (
                <div className="dm-panel-empty">
                  <strong>
                    {domain.state === 'out_of_scope'
                      ? 'Not in scope'
                      : 'No document covers this domain'}
                  </strong>
                  <p>
                    {domain.state === 'out_of_scope'
                      ? 'Nothing is expected here until controls from this domain are brought into scope.'
                      : 'Upload a policy or procedure that covers this area and it will appear here for review.'}
                  </p>
                  {domain.state !== 'out_of_scope' && (
                    <button type="button" className="dm-btn-primary" onClick={onOpenDocuments}>
                      Go to Control Documents
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
