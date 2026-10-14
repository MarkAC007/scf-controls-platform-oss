import type { CompensatingControl, EffectivenessRating } from '../../types'

export const EFFECTIVENESS_RATINGS: EffectivenessRating[] = ['full', 'partial', 'minimal']

export const EFFECTIVENESS_LABELS: Record<EffectivenessRating, string> = {
  full: 'Full',
  partial: 'Partial',
  minimal: 'Minimal',
}

/**
 * Why the server reads no acceptance for a record's risk: the risk is no
 * longer linked to the control, is linked but unassessed, or is assessed and
 * not accepted. 'unknown' while the links are unread, or could not be read.
 */
export type MissingAcceptance = 'unlinked' | 'unassessed' | 'unaccepted' | 'unknown'

const MISSING_ACCEPTANCE_TEXT: Record<MissingAcceptance, string> = {
  unlinked: 'Risk no longer linked',
  unassessed: 'Risk not assessed',
  unaccepted: 'Risk not accepted',
  unknown: 'Not accepted, or no longer linked',
}

/**
 * The acceptance chip for a record, from the server's reading now. Its kind
 * reuses the risk register's chip colours; 'none' is a plain chip.
 */
export function acceptanceChip(
  cc: Pick<CompensatingControl, 'risk_code' | 'risk_acceptance_state'>,
  missing: MissingAcceptance = 'unknown',
): { kind: 'approved' | 'expired' | 'unapproved' | 'none'; text: string } {
  if (!cc.risk_code) return { kind: 'none', text: 'No risk named' }
  switch (cc.risk_acceptance_state) {
    case 'approved':
      return { kind: 'approved', text: 'Risk accepted · approved' }
    case 'expired':
      return { kind: 'expired', text: 'Acceptance expired' }
    case 'unapproved':
      return { kind: 'unapproved', text: 'Accepted · not approved' }
    default:
      // Null: not accepted, unassessed, or no longer linked to the control.
      return { kind: 'none', text: MISSING_ACCEPTANCE_TEXT[missing] }
  }
}

export const withdrawMessage = (scfId: string) =>
  `Withdraw this compensating control for ${scfId}? It is deleted: only the audit log keeps what it said.`
