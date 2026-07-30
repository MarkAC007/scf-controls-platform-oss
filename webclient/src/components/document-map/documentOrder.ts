import type { CDMDocumentMapDocument } from '../../data/apiClient'

/**
 * Order a domain's documents: confirmed placements first, then by rank.
 *
 * Confirmed leads because it is the only part of the list an auditor can rely
 * on, and on a tile only the first entries are visible — a confirmed document
 * must never be the one pushed out of view. Within a group the served rank is
 * preserved; a document with no rank sorts last rather than to the front.
 */
export function orderDocuments(
  documents: CDMDocumentMapDocument[]
): CDMDocumentMapDocument[] {
  return [...documents].sort((a, b) => {
    const aConfirmed = a.intent_source === 'confirmed' ? 0 : 1
    const bConfirmed = b.intent_source === 'confirmed' ? 0 : 1
    if (aConfirmed !== bConfirmed) return aConfirmed - bConfirmed
    return (a.rank ?? Number.MAX_SAFE_INTEGER) - (b.rank ?? Number.MAX_SAFE_INTEGER)
  })
}
