import type { JSX } from 'react'
import type { ObligationBasis } from '../../data/apiClient'

/* PIG R2: a control or framework is a mandatory (MCR) or discretionary (DSR)
   obligation, or unclassified when nothing says which. */
const LABELS: Record<ObligationBasis, { short: string; long: string }> = {
  mcr: { short: 'MCR', long: 'Mandatory (MCR)' },
  dsr: { short: 'DSR', long: 'Discretionary (DSR)' },
}

export function basisLabel(basis: ObligationBasis | null | undefined, long = false): string {
  if (!basis) return 'Unclassified'
  return long ? LABELS[basis].long : LABELS[basis].short
}

export default function ObligationBasisBadge({
  basis,
  long = false,
  title,
}: {
  basis: ObligationBasis | null | undefined
  long?: boolean
  title?: string
}): JSX.Element {
  return (
    <span className={`basis-badge basis-badge--${basis ?? 'none'}`} title={title}>
      {basisLabel(basis, long)}
    </span>
  )
}
