/**
 * How a verdict is allowed to be worded (#881 WS3).
 *
 * The product rule this file exists to enforce: **an AI verdict is a
 * suggestion until a person confirms it.** An unconfirmed verdict must read as
 * something the machine proposed; only a human-reviewed one may read as
 * settled. Getting that wrong is not a cosmetic bug — it is the platform
 * presenting an unreviewed model output as an assurance position.
 *
 * The wording lived in two independent maps before this (``AI_STATUS_CONFIG``
 * in the file list and ``AI_STATUS_LABELS`` in the preview modal), which meant
 * a rule stated in one place could be silently absent in the other. There is
 * one map now, and every surface that shows a verdict reads it.
 *
 * The confirmed/suggested distinction is carried by **prefix and shape, not
 * colour**: "AI suggests: Partial" versus "Confirmed: Partial", and a dashed
 * versus solid leading marker in CSS. A reader who cannot separate hues, or
 * who is looking at a greyscale print, still gets the distinction — the same
 * commitment the existing `.ai-chip` styles already make between `error` and
 * `unassessable`.
 */

/** The bare status word, with no claim about who stands behind it. */
export const ASSESSMENT_STATUS_LABELS: Record<string, string> = {
  sufficient: 'Sufficient',
  partial: 'Partial',
  insufficient: 'Insufficient',
  unassessable: 'Unassessable',
  pending: 'Assessing...',
  processing: 'Assessing...',
  error: 'Error',
}

/** Statuses that are a verdict about the evidence, rather than a state of the run. */
export const TERMINAL_STATUSES = ['sufficient', 'partial', 'insufficient', 'unassessable']

/**
 * Deliberately widened to plain ``string``. The wire type is a string and the
 * meaningful values are 'confirmed' and 'overridden', but a value this code
 * does not recognise must be treated as *not reviewed* rather than rejected at
 * the type boundary — a backend that grows a third decision should make the UI
 * cautious, not make it fail to compile.
 */
export type ReviewDecision = string | null | undefined

export interface VerdictPresentation {
  /** Full text, e.g. "AI suggests: Partial" or "Confirmed: Sufficient". */
  text: string
  /** The status word on its own, for places with their own framing. */
  statusLabel: string
  /** "AI suggests" / "Confirmed" / "Corrected", or '' for non-verdict states. */
  qualifier: string
  /** Class list for the chip. Always includes a confirmation-state class. */
  className: string
  /** True once a person has confirmed or overridden this verdict. */
  isReviewed: boolean
}

/**
 * How to render one verdict, given whether a human has decided on it.
 *
 * ``overridden`` reads as "Corrected" rather than "Confirmed": a reviewer who
 * disagreed with the model did not endorse it, and collapsing the two would
 * lose the one piece of information a later reader most wants — that this
 * status is a human's, not the machine's.
 *
 * Pending, processing and error states get no qualifier at all. There is no
 * verdict there to be suggested or confirmed, and prefixing "AI suggests" to
 * "Error" would describe a failed run as an opinion about the evidence.
 */
export function verdictPresentation(
  status: string | null | undefined,
  reviewDecision: ReviewDecision,
): VerdictPresentation {
  const key = status || 'unknown'
  const statusLabel = ASSESSMENT_STATUS_LABELS[key] || key
  const isTerminal = TERMINAL_STATUSES.includes(key)

  if (!isTerminal) {
    return {
      text: `AI: ${statusLabel}`,
      statusLabel,
      qualifier: '',
      className: `ai-chip ai-chip-${key}`,
      isReviewed: false,
    }
  }

  if (reviewDecision === 'confirmed' || reviewDecision === 'overridden') {
    const qualifier = reviewDecision === 'overridden' ? 'Corrected' : 'Confirmed'
    return {
      text: `${qualifier}: ${statusLabel}`,
      statusLabel,
      qualifier,
      className: `ai-chip ai-chip-${key} ai-chip-reviewed`,
      isReviewed: true,
    }
  }

  return {
    text: `AI suggests: ${statusLabel}`,
    statusLabel,
    qualifier: 'AI suggests',
    className: `ai-chip ai-chip-${key} ai-chip-suggested`,
    isReviewed: false,
  }
}

/**
 * The four designations a reviewer may give an objective.
 *
 * Deliberately the same vocabulary the model is given, and deliberately not
 * the CAP assessor vocabulary: this platform advises a preparer and must never
 * read as though it has issued an assessor's determination.
 */
export const AO_DESIGNATIONS = [
  'appears_satisfied',
  'gap_identified',
  'not_applicable',
  'cannot_assess',
] as const

export const AO_DESIGNATION_LABELS: Record<string, string> = {
  appears_satisfied: 'Appears satisfied',
  gap_identified: 'Gap identified',
  not_applicable: 'Not applicable',
  cannot_assess: 'Cannot assess',
}

export function designationLabel(designation: string): string {
  return AO_DESIGNATION_LABELS[designation] || designation
}

export function designationClass(designation: string): string {
  return `ao-designation ao-designation-${designation.replace(/_/g, '-')}`
}
