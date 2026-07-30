import type { CDMDocumentMapDomain } from '../../data/apiClient'
import { orderDocuments } from './documentOrder'
import { AlertGlyph, CheckGlyph, MinusGlyph, RingGlyph } from './icons'

/**
 * One domain tile.
 *
 * Outer tile is the framework speaking: flat, serif, hairline border. The
 * organisation's own record sits inside it as a raised block, so the presence
 * of raised material is itself the signal that work exists here — a gap tile
 * carries no raised block at all, and reads as empty before any colour is.
 *
 * Confirmed and suggested are separated on four redundant channels (word,
 * glyph, block edge, state strip) so the distinction survives greyscale,
 * colour-blindness, and a screenshot pasted into an audit report.
 */

const MAX_CHIPS = 2

interface DomainCardProps {
  domain: CDMDocumentMapDomain
  onSelect: (domainCode: string) => void
}

function stateModifier(state: CDMDocumentMapDomain['state']): string {
  switch (state) {
    case 'covered':
      return 'dm-tile-covered'
    case 'claimed':
      return 'dm-tile-claimed'
    case 'gap':
      return 'dm-tile-gap'
    default:
      return 'dm-tile-oos'
  }
}

function ariaLabel(domain: CDMDocumentMapDomain): string {
  const { name, state, totals, scoped_control_counts } = domain
  const docs = `${totals.documents} document${totals.documents === 1 ? '' : 's'}`
  switch (state) {
    case 'covered':
      return `${domain.domain}, ${name}. Confirmed, ${docs}.`
    case 'claimed':
      return `${domain.domain}, ${name}. Suggested, ${docs}, none confirmed yet.`
    case 'gap':
      return `${domain.domain}, ${name}. Gap, no document, ${scoped_control_counts.selected} control${scoped_control_counts.selected === 1 ? '' : 's'} scoped.`
    default:
      return `${domain.domain}, ${name}. Not in scope.`
  }
}

export default function DomainCard({ domain, onSelect }: DomainCardProps) {
  const { state, totals, documents, scoped_control_counts } = domain
  const hasRecord = state === 'covered' || state === 'claimed'
  const isConfirmed = state === 'covered'
  // Ordered before slicing so a confirmed document is never the one cut off.
  const visibleDocs = orderDocuments(documents).slice(0, MAX_CHIPS)
  const hiddenDocs = documents.length - visibleDocs.length

  return (
    <button
      type="button"
      className={`dm-tile ${stateModifier(state)}`}
      onClick={() => onSelect(domain.domain)}
      aria-label={ariaLabel(domain)}
    >
      {/* Channel 4 of 4: continuous bar when confirmed, segmented when suggested. */}
      <span
        className={`dm-strip ${
          hasRecord
            ? isConfirmed
              ? 'dm-strip-continuous'
              : 'dm-strip-segmented'
            : 'dm-strip-flat'
        }`}
        aria-hidden="true"
      />

      <span className="dm-ref">
        <span className="dm-code">{domain.domain}</span>
        <span className="dm-name">{domain.name}</span>
      </span>

      <span className="dm-body">
        {hasRecord ? (
          /* Channel 3 of 4: solid edge when confirmed, dashed when suggested. */
          <span className={`dm-record ${isConfirmed ? 'dm-record-solid' : 'dm-record-dashed'}`}>
            <span className="dm-record-status">
              {/* Channel 2 of 4: solid check vs hollow dashed ring. */}
              {isConfirmed ? (
                <CheckGlyph className="dm-glyph dm-glyph-check" />
              ) : (
                <RingGlyph className="dm-glyph dm-glyph-ring" />
              )}
              {/* Channel 1 of 4: the word itself. */}
              <span className="dm-record-word">{isConfirmed ? 'Confirmed' : 'Suggested'}</span>
            </span>

            {visibleDocs.length > 0 && (
              <span className="dm-doc-chips">
                {visibleDocs.map((doc) => (
                  <span
                    key={doc.cdm_document_id}
                    className={`dm-doc-chip ${
                      doc.intent_source === 'confirmed'
                        ? 'dm-doc-chip-confirmed'
                        : 'dm-doc-chip-suggested'
                    }`}
                    title={doc.filename}
                  >
                    {doc.intent_source === 'confirmed' ? (
                      <CheckGlyph size={11} className="dm-glyph dm-glyph-check" />
                    ) : (
                      <RingGlyph size={11} className="dm-glyph dm-glyph-ring" />
                    )}
                    <span className="dm-doc-chip-name">{doc.filename}</span>
                  </span>
                ))}
                {hiddenDocs > 0 && <span className="dm-doc-more">+{hiddenDocs} more</span>}
              </span>
            )}

            {/*
              Depth ladder. A step with no data is absent rather than shown as
              zero — an empty step would read as a measured nothing instead of
              a stage not yet reached.
            */}
            <span className="dm-depth" role="group" aria-label="Depth of coverage">
              <span
                className="dm-pip dm-pip-docs"
                title={`${totals.documents} document${totals.documents === 1 ? '' : 's'} placed on this domain`}
              >
                {totals.documents}
                <span className="dm-pip-key">DOCS</span>
              </span>
              {totals.controls_with_accepted_mapping > 0 && (
                <span
                  className="dm-pip dm-pip-conf"
                  title={`${totals.controls_with_accepted_mapping} control mapping${
                    totals.controls_with_accepted_mapping === 1 ? '' : 's'
                  } confirmed by a person`}
                >
                  {totals.controls_with_accepted_mapping}
                  <span className="dm-pip-key">CONF</span>
                </span>
              )}
            </span>
          </span>
        ) : state === 'gap' ? (
          <span className="dm-flat">
            <span className="dm-flat-lead dm-flat-lead-gap">
              <AlertGlyph />
              Gap
            </span>
            <span className="dm-flat-sub">
              No document · {scoped_control_counts.selected} control{scoped_control_counts.selected === 1 ? '' : 's'} scoped
            </span>
          </span>
        ) : (
          <span className="dm-flat">
            <span className="dm-flat-lead">
              <MinusGlyph />
              Not in scope
            </span>
            <span className="dm-flat-sub">No controls scoped</span>
          </span>
        )}
      </span>
    </button>
  )
}
