/**
 * ContractorBadge — says that this person works for this organisation under a
 * contract rather than on its payroll (#822 phase 2).
 *
 * One component, not five copies of the same span, for the same reason
 * ``TeamWarningBadge`` was extracted in phase 3: the moment two surfaces
 * render the idea themselves they drift into looking like two different
 * claims about the same person.
 *
 * It renders NOTHING for an internal member, and nothing for an unknown one.
 * The absence of a badge is not an assertion that somebody is staff — it is
 * the ordinary case, and an "Internal" badge on every row would be noise that
 * makes the one row that matters harder to see.
 *
 * It grants and denies nothing. A contractor badge must never be read as a
 * reason to hide a control, disable an action, exclude a person from a
 * picker, or gate anything else: ``member_type`` is a label and capability
 * comes from ``organization_members.role`` alone. If a future change wants to
 * branch on the value returned here, that is the change to refuse.
 *
 * Accessible by text, not by colour: the word "Contractor" is in the DOM, so
 * the badge survives a monochrome display, a colour-blind reader and a screen
 * reader without any of them needing the amber to mean something.
 */
import type { MemberType } from '../types'

/** The badge's own wording, so the select-option suffix below cannot drift from it. */
const CONTRACTOR_LABEL = 'Contractor'

/** Hover text. Says what the label means, and what it does not. */
const CONTRACTOR_TITLE =
  'External contractor in this organisation. A label only — it grants and ' +
  'restricts nothing; access comes from the organisation role.'

interface ContractorBadgeProps {
  /**
   * The membership's ``member_type``. Undefined is treated exactly like
   * ``internal``: a lookup that has not landed yet must render nothing rather
   * than guess, and certainly rather than flash a badge and take it back.
   */
  memberType?: MemberType | null
  /**
   * Who the badge is about. Folded into the accessible name so a row of
   * badges in a table does not read as "Contractor, Contractor, Contractor"
   * with no way to tell which person each belongs to.
   */
  personName?: string | null
  className?: string
}

export function ContractorBadge({ memberType, personName, className }: ContractorBadgeProps) {
  if (memberType !== 'external_contractor') return null
  const accessibleLabel = personName
    ? `${personName} is an external contractor`
    : 'External contractor'
  return (
    <span
      className={className ? `contractor-badge ${className}` : 'contractor-badge'}
      title={CONTRACTOR_TITLE}
      aria-label={accessibleLabel}
    >
      <svg
        width="12"
        height="12"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
        <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
      </svg>
      {CONTRACTOR_LABEL}
    </span>
  )
}

/**
 * The same statement as plain text, for the places that cannot hold an element.
 *
 * A ``<select>`` is one of them: HTML allows only text inside ``<option>``, so
 * an assignee picker physically cannot render the badge against each name. The
 * honest fix is a suffix on the option's own label rather than either dropping
 * the information from every picker in the app or replacing four working
 * selects with bespoke listboxes. Same word as the badge, from the same
 * constant, so the two surfaces say one thing.
 *
 * Returns the name untouched for internal and unknown members.
 */
export function withContractorSuffix(
  label: string,
  memberType?: MemberType | null
): string {
  return memberType === 'external_contractor' ? `${label} (${CONTRACTOR_LABEL})` : label
}

export default ContractorBadge
