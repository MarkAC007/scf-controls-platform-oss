/**
 * CapabilityTestRow — one capability test in the list, and, opened, what was
 * run, what came of it and its findings (PIG R11).
 *
 * The findings are deficiencies and are listed as the Deficiencies page lists
 * them, with the same actions: they are owned, completed with a retest note,
 * or cancelled if found in error, never deleted. Their forms belong to the
 * page, so a refetch cannot unmount one half typed. Restoring a cancelled
 * finding of a test recorded as passed is refused by the server until the
 * result is corrected; the refusal shows on the finding.
 */
import { useId, type JSX } from 'react'
import { useCapabilityTest } from '../../hooks/useCapabilityTests'
import { formatAuditDate, formatServerDay } from '../../utils/auditValue'
import type { CapabilityTest, Deficiency, UserSimple } from '../../types'
import DeficiencyList from '../deficiencies/DeficiencyList'
import { RESULT_LABELS, TEST_TYPE_LABELS } from './capabilityTestText'

interface Props {
  organizationId: string
  test: CapabilityTest
  /** How the row's buttons name the test: its quoted title, made unique on the page. */
  name: string
  users: UserSimple[] | undefined
  canEdit: boolean
  expanded: boolean
  onToggle: () => void
  onEdit: (t: CapabilityTest) => void
  onAddFinding: (t: CapabilityTest) => void
  onEditFinding: (d: Deficiency) => void
  onCompleteFinding: (d: Deficiency) => void
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

function findingSummary(t: Pick<CapabilityTest, 'finding_count' | 'open_finding_count'>): string {
  if (t.finding_count === 0) return 'No findings'
  return `${t.finding_count} finding${t.finding_count === 1 ? '' : 's'}, ${t.open_finding_count} open`
}

function Detail({
  organizationId,
  test,
  users,
  canEdit,
  onEditFinding,
  onCompleteFinding,
}: Pick<Props, 'organizationId' | 'test' | 'users' | 'canEdit' | 'onEditFinding' | 'onCompleteFinding'>): JSX.Element {
  const detail = useCapabilityTest(organizationId, test.id)
  const findings = detail.data?.findings ?? []
  // Mid-sentence, and the recorder may never have been a member (an API key's user).
  const member = users?.find((u) => u.id === test.recorded_by_user_id)
  const recorder = !test.recorded_by_user_id
    ? null
    : member
      ? member.display_name || member.email
      : users
        ? 'a user outside this organisation'
        : 'someone'

  return (
    <div className="capability-test-detail">
      <dl className="compensating-text">
        <dt>Scenario</dt>
        <dd>{test.scenario}</dd>
        <dt>Outcome</dt>
        <dd>{test.outcome}</dd>
        <dt>Recorded</dt>
        <dd>
          {formatServerDay(test.created_at)}
          {recorder ? ` by ${recorder}` : ''}
        </dd>
      </dl>
      <h4 className="capability-test-findings-heading">Findings</h4>
      {detail.isError && (
        <div role="alert" className="gov-error">
          Could not load the findings: {errorText(detail.error)}
          {detail.data ? '. Showing the last loaded.' : ''}
        </div>
      )}
      {detail.isLoading ? (
        <p className="gov-muted">Loading the findings…</p>
      ) : detail.data && findings.length === 0 ? (
        <p className="gov-muted">No findings recorded.</p>
      ) : detail.data ? (
        <DeficiencyList
          organizationId={organizationId}
          deficiencies={findings}
          users={users}
          canEdit={canEdit}
          showTest={false}
          onEdit={onEditFinding}
          onComplete={onCompleteFinding}
        />
      ) : null}
    </div>
  )
}

export default function CapabilityTestRow({
  organizationId,
  test,
  name,
  users,
  canEdit,
  expanded,
  onToggle,
  onEdit,
  onAddFinding,
  onEditFinding,
  onCompleteFinding,
}: Props): JSX.Element {
  const detailId = useId()
  return (
    <li className="deficiency-row capability-test-row" id={`capability-test-${test.id}`} data-testid={`capability-test-${test.id}`}>
      <div className="deficiency-row-head">
        <span className="deficiency-row-title">{test.title}</span>
        <span className="deficiency-chip">{TEST_TYPE_LABELS[test.test_type]}</span>
        <span className={`deficiency-chip capability-result--${test.result}`}>{RESULT_LABELS[test.result]}</span>
      </div>
      <div className="deficiency-row-meta">
        <span>Tested {formatAuditDate(test.tested_on)}</span>
        <span>{findingSummary(test)}</span>
      </div>
      <div className="deficiency-row-actions">
        <button
          type="button"
          className="btn-secondary btn-small"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-controls={detailId}
          aria-label={`Details of ${name}`}
        >
          Details
        </button>
        {canEdit && (
          <>
            <button
              type="button"
              className="btn-secondary btn-small"
              onClick={() => onEdit(test)}
              aria-label={`Correct ${name}`}
            >
              Correct
            </button>
            <button
              type="button"
              className="btn-primary btn-small"
              onClick={() => onAddFinding(test)}
              aria-label={`Add finding to ${name}`}
            >
              Add finding
            </button>
          </>
        )}
      </div>
      <div id={detailId} hidden={!expanded}>
        {expanded && (
          <Detail
            organizationId={organizationId}
            test={test}
            users={users}
            canEdit={canEdit}
            onEditFinding={onEditFinding}
            onCompleteFinding={onCompleteFinding}
          />
        )}
      </div>
    </li>
  )
}
