import type { EvidenceFileResponse } from '../../data/apiClient'

/**
 * Read-back of what the preparer asserted about an artefact (#786, #802).
 *
 * The single rule this component exists to hold: **"not asserted" is a fact,
 * and it is displayed as one.** A blank cell reads as an oversight or a
 * rendering bug; an explicit "Not asserted" reads as what it is — nobody has
 * made this claim, so nobody should rely on it. That distinction is the entire
 * reason the underlying columns are nullable and are never back-filled.
 *
 * The panel is always rendered, including for a file with nothing asserted at
 * all. Hiding it in that case would hide precisely the information an auditor
 * most needs: that the evidence set makes no claim about its period, its
 * population or how its data was produced.
 */

function formatDay(value: string | null): string | null {
  if (!value) return null
  // The 'T00:00:00' guard is the house convention for a date-only string —
  // without it a UTC-midnight parse shifts the day backwards west of Greenwich,
  // which on a period boundary is a wrong answer, not a cosmetic one.
  const day = value.length === 10 ? `${value}T00:00:00` : value
  const parsed = new Date(day)
  if (Number.isNaN(parsed.getTime())) return value
  return parsed.toLocaleDateString('en-GB', { year: 'numeric', month: 'short', day: 'numeric' })
}

function AssertionRow({
  label,
  value,
  testId,
  wrap = false,
}: {
  label: string
  value: string | null
  testId: string
  wrap?: boolean
}) {
  const asserted = value !== null && value !== ''
  return (
    <div className="preparer-panel-row">
      <span className="preparer-panel-label">{label}</span>
      <span
        className={[
          'preparer-panel-value',
          asserted ? '' : 'preparer-panel-value--unasserted',
          wrap ? 'preparer-panel-value--wrap' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        data-testid={testId}
        title={asserted ? (value as string) : undefined}
      >
        {asserted ? value : 'Not asserted'}
      </span>
    </div>
  )
}

export function PreparerAssertionPanel({ file }: { file: EvidenceFileResponse }) {
  const start = formatDay(file.effective_period_start)
  const end = formatDay(file.effective_period_end)
  const period = start && end ? `${start} — ${end}` : null

  // Coverage is only as good as its denominator. "25" on its own is not a
  // coverage statement, so the two are rendered as one fact and the missing
  // half is named rather than left to inference.
  let coverage: string | null = null
  if (file.sample_size !== null && file.population_size !== null) {
    coverage = `${file.sample_size} of ${file.population_size}`
  } else if (file.sample_size !== null) {
    coverage = `${file.sample_size} examined, population not declared`
  } else if (file.population_size !== null) {
    coverage = `Population ${file.population_size}, sample not declared`
  }

  return (
    <div className="preparer-panel" data-testid="preparer-assertions-panel">
      <div className="preparer-panel-header">
        <h4 className="preparer-panel-title">Preparer assertions</h4>
        <span className="preparer-panel-subtitle">
          Claims made by the person who supplied this file — not measured by the platform
        </span>
      </div>

      <div className="preparer-panel-section">
        <AssertionRow
          label="Effective period"
          value={period}
          testId="assertion-effective-period"
        />
        <AssertionRow label="Coverage" value={coverage} testId="assertion-coverage" />
        <AssertionRow
          label="Population"
          value={file.population_source}
          testId="assertion-population-source"
          wrap
        />
        <AssertionRow
          label="Selection method"
          value={file.sample_method}
          testId="assertion-sample-method"
        />
        <AssertionRow
          label="Sampling basis"
          value={file.sample_basis}
          testId="assertion-sample-basis"
          wrap
        />
      </div>

      <div className="preparer-panel-section">
        <AssertionRow
          label="IPE source system"
          value={file.ipe_source_system}
          testId="assertion-ipe-system"
        />
        <AssertionRow
          label="Extract run on"
          value={formatDay(file.ipe_extracted_at)}
          testId="assertion-ipe-extracted-at"
        />
        <AssertionRow
          label="Report or filter"
          value={file.ipe_query_or_filter}
          testId="assertion-ipe-query"
          wrap
        />
        <AssertionRow
          label="Completeness check"
          value={file.ipe_completeness_check}
          testId="assertion-ipe-completeness"
          wrap
        />
      </div>
    </div>
  )
}
