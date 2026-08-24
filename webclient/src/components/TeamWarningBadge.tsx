/**
 * The one warning badge the teams feature has.
 *
 * Lifted verbatim out of TeamManagement (phase 1) when a second surface —
 * "Owning teams" on controls and evidence — needed to say the same kind of
 * thing: something about this team arrangement is incomplete, and you may
 * carry on anyway. Every one of these is advisory. None of them blocks a
 * save, hides a row, or disables a control, and none of them should start to:
 * a control with no accountable team is the state every control is in until
 * somebody assigns one.
 *
 * Extracted rather than copied so the two surfaces cannot drift into looking
 * like two different severities of the same message.
 */

/** One advisory signal. ``title`` is the hover text; say what it means, not that it is a warning. */
export interface TeamWarning {
  key: string
  label: string
  title: string
}

interface TeamWarningBadgesProps {
  warnings: TeamWarning[]
}

export function TeamWarningBadges({ warnings }: TeamWarningBadgesProps) {
  if (warnings.length === 0) return null
  return (
    <span className="team-warning-badges">
      {warnings.map(warning => (
        <span key={warning.key} className="team-warning-badge" title={warning.title}>
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
            <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
            <line x1="12" y1="9" x2="12" y2="13" />
            <line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          {warning.label}
        </span>
      ))}
    </span>
  )
}

export default TeamWarningBadges
