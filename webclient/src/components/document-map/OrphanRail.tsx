import type { CDMDocumentMapOrphan } from '../../data/apiClient'
import { ClockGlyph, DocGlyph } from './icons'

/**
 * Documents that reached no in-scope domain.
 *
 * A rail, deliberately not a 34th tile: "Unmapped" is not a domain, and giving
 * it a tile would corrupt the fixed 33-domain axis the map's spatial memory
 * depends on.
 */

interface OrphanRailProps {
  orphans: CDMDocumentMapOrphan[]
  awaitingClassification: number
  /** True before anything has been uploaded — the rail states its purpose instead of an outcome. */
  isDayOne: boolean
  onOpenDocuments: () => void
}

/** Why a document has not landed anywhere, in process terms. */
function orphanReason(intentState: string): string {
  switch (intentState) {
    case 'pending':
      return 'Awaiting classification'
    case 'stale':
      return 'Needs reclassification'
    case 'failed':
      return 'Not yet classified'
    case 'classified':
      return 'No in-scope domain'
    default:
      return 'No domain proposed'
  }
}

export default function OrphanRail({
  orphans,
  awaitingClassification,
  isDayOne,
  onOpenDocuments,
}: OrphanRailProps) {
  return (
    <aside className="dm-rail" aria-label="Unmapped documents">
      <div className="dm-section-head dm-section-head-bench">
        <h2>Unmapped</h2>
      </div>
      <div className="dm-rail-card">
        <p className="dm-rail-note">
          Documents that have not landed in an in-scope domain. Reviewing one places it on the
          map.
        </p>

        {awaitingClassification > 0 && (
          <p className="dm-rail-pending">
            <ClockGlyph />
            {awaitingClassification} document{awaitingClassification === 1 ? '' : 's'} awaiting
            classification
          </p>
        )}

        {orphans.length > 0 ? (
          <>
            <ul className="dm-orphan-list">
              {orphans.map((orphan) => (
                <li key={orphan.cdm_document_id} className="dm-orphan">
                  <span className="dm-orphan-name" title={orphan.filename}>
                    <DocGlyph size={14} />
                    {orphan.filename}
                  </span>
                  <span className="dm-orphan-meta">{orphanReason(orphan.intent_state)}</span>
                </li>
              ))}
            </ul>
            <button type="button" className="dm-rail-cta" onClick={onOpenDocuments}>
              Review in Control Documents
            </button>
          </>
        ) : (
          <p className="dm-rail-empty">
            {isDayOne
              ? 'Nothing here yet. A document that reaches no in-scope domain will appear in this rail.'
              : 'Every document has landed in at least one in-scope domain.'}
          </p>
        )}
      </div>
    </aside>
  )
}
