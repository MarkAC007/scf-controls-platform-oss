import type { ControlValidation, ValidationState } from '../../types'

export const VALIDATION_STATE_LABELS: Record<ValidationState, string> = {
  current: 'Current',
  due_soon: 'Due soon',
  overdue: 'Overdue',
  never: 'Never validated',
  unknown_cadence: 'Cadence unknown',
}

/** What the state means, in the server's terms (services/due_care.py). */
export function validationStateHint(v: ControlValidation): string {
  switch (v.validation_state) {
    case 'current':
      return 'Validated within its cadence.'
    case 'due_soon':
      return 'The next validation is due within 30 days.'
    case 'overdue':
      return 'The next validation is past its due date: the control no longer counts as validated within its cadence.'
    case 'never':
      return 'This control has never been validated, so it does not count as validated within its cadence.'
    case 'unknown_cadence':
      return v.validation_cadence
        ? `"${v.validation_cadence}" is not a cadence the platform can date (annual, semi-annual, quarterly, monthly), so no next date can be given and the control does not count as validated within its cadence.`
        : 'This control has no validation cadence, so no next date can be given and it does not count as validated within its cadence.'
  }
}
