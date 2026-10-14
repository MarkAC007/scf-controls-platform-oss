/**
 * CapabilityTestsPage — the organisation's capability tests (PIG R11).
 *
 * An incident response exercise, a continuity test or another test of whether
 * the organisation can do what its controls say: when it was run, the
 * scenario, the result and the outcome, and its findings, each recorded as a
 * deficiency. A test is corrected, never deleted. The journey counts tests run
 * recently, whatever their result.
 *
 * The type filter goes to the server; the result filter and search work on
 * the list loaded. A link from a finding names a test in `?test=`, which opens
 * here.
 */
import { useEffect, useMemo, useRef, useState, type JSX } from 'react'
import { useCapabilityTests } from '../../hooks/useCapabilityTests'
import { useOrgMembersQuery } from '../../hooks/useDeficiencies'
import { useIsOrgEditor } from '../../hooks/useHasOrgRole'
import type { CapabilityTest, CapabilityTestFilters, CapabilityTestResult, CapabilityTestType, Deficiency } from '../../types'
import { formatAuditDate } from '../../utils/auditValue'
import DeficiencyCompleteModal from '../deficiencies/DeficiencyCompleteModal'
import DeficiencyFormModal from '../deficiencies/DeficiencyFormModal'
import FilterRadio from '../explorer/FilterRadio'
import FilterSidebar, { FilterGroup, defaultFiltersCollapsed } from '../explorer/FilterSidebar'
import ListToolbar from '../explorer/ListToolbar'
import AddFindingModal from './AddFindingModal'
import CapabilityTestFormModal from './CapabilityTestFormModal'
import CapabilityTestRow from './CapabilityTestRow'
import { RESULTS, RESULT_LABELS, TEST_TYPES, TEST_TYPE_LABELS } from './capabilityTestText'

const TYPE_OPTIONS = [
  { value: '', label: 'All' },
  ...TEST_TYPES.map((t) => ({ value: t, label: TEST_TYPE_LABELS[t] })),
]
const RESULT_OPTIONS = [
  { value: '', label: 'All' },
  ...RESULTS.map((r) => ({ value: r, label: RESULT_LABELS[r] })),
]

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err))

function matchesSearch(t: CapabilityTest, term: string): boolean {
  if (!term) return true
  return [t.title, t.scenario, t.outcome].some((field) => field.toLowerCase().includes(term))
}

/**
 * The name each row's buttons give its test. Titles need not be unique, and
 * accessible names must be: a shared title adds the day it was run, and a
 * shared title and day adds its place among them.
 */
export function testNames(tests: CapabilityTest[]): Map<string, string> {
  const count = (key: (t: CapabilityTest) => string) => {
    const n = new Map<string, number>()
    for (const t of tests) n.set(key(t), (n.get(key(t)) ?? 0) + 1)
    return n
  }
  const byTitle = count((t) => t.title)
  const byDay = count((t) => `${t.title}\u0000${t.tested_on}`)
  const seen = new Map<string, number>()
  const names = new Map<string, string>()
  for (const t of tests) {
    let name = `"${t.title}"`
    if ((byTitle.get(t.title) ?? 0) > 1) {
      const key = `${t.title}\u0000${t.tested_on}`
      const total = byDay.get(key) ?? 0
      const place = (seen.get(key) ?? 0) + 1
      seen.set(key, place)
      name += total > 1
        ? ` (tested ${formatAuditDate(t.tested_on)}, ${place} of ${total})`
        : ` (tested ${formatAuditDate(t.tested_on)})`
    }
    names.set(t.id, name)
  }
  return names
}

interface Props {
  organizationId: string
  /** A test to open on arrival: the `?test=` a finding's link names. */
  focusTestId?: string | null
}

export default function CapabilityTestsPage({ organizationId, focusTestId = null }: Props): JSX.Element {
  const canEdit = useIsOrgEditor(organizationId)
  const [filtersCollapsed, setFiltersCollapsed] = useState(defaultFiltersCollapsed)
  const [testType, setTestType] = useState<CapabilityTestType | ''>('')
  const [result, setResult] = useState<CapabilityTestResult | ''>('')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set(focusTestId ? [focusTestId] : []))
  // Owned here, not by the rows, so a refetch that fails or drops a row from
  // the filter cannot unmount a form someone is typing in.
  const [recording, setRecording] = useState(false)
  const [correcting, setCorrecting] = useState<CapabilityTest | null>(null)
  const [addingTo, setAddingTo] = useState<CapabilityTest | null>(null)
  const [editingFinding, setEditingFinding] = useState<Deficiency | null>(null)
  const [completingFinding, setCompletingFinding] = useState<Deficiency | null>(null)

  const filters = useMemo<CapabilityTestFilters>(() => (testType ? { test_type: testType } : {}), [testType])
  // The whole record, for the counts; the same query as the list when no type is chosen.
  const all = useCapabilityTests(organizationId)
  const listed = useCapabilityTests(organizationId, filters)
  const members = useOrgMembersQuery(organizationId)

  // A finding's link arriving while the page is open (Back, Forward).
  useEffect(() => {
    if (focusTestId) setExpanded((s) => (s.has(focusTestId) ? s : new Set(s).add(focusTestId)))
  }, [focusTestId])

  // Once per link: a filter that hides the test and shows it again must not jump back to it.
  const scrolledTo = useRef<string | null>(null)
  const focusLoaded = Boolean(focusTestId && listed.data?.some((t) => t.id === focusTestId))
  useEffect(() => {
    if (!focusLoaded || scrolledTo.current === focusTestId) return
    scrolledTo.current = focusTestId
    document.getElementById(`capability-test-${focusTestId}`)?.scrollIntoView?.({ block: 'start' })
  }, [focusLoaded, focusTestId])

  const toggle = (id: string) =>
    setExpanded((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const counts = useMemo(() => {
    const rows = all.data ?? []
    const by = (r: CapabilityTestResult) => rows.filter((t) => t.result === r).length
    return {
      total: rows.length,
      passed: by('passed'),
      withFindings: by('passed_with_findings'),
      failed: by('failed'),
      openFindings: rows.reduce((n, t) => n + t.open_finding_count, 0),
    }
  }, [all.data])

  const term = search.trim().toLowerCase()
  const visible = (listed.data ?? []).filter((t) => (!result || t.result === result) && matchesSearch(t, term))
  const names = testNames(visible)
  const filtered = testType || result || term

  // The last counts loaded stand through a failed refresh, as the list does.
  const stat = (n: number) => (all.data ? n : all.isLoading ? '…' : '–')

  return (
    <div className="tasks-page tasks-explorer-page capability-tests-page">
      <div className="tasks-explorer-shell">
        <FilterSidebar
          collapsed={filtersCollapsed}
          onToggleCollapsed={() => setFiltersCollapsed((c) => !c)}
          aria-label="Capability test filters"
        >
          <FilterGroup label="TYPE">
            <FilterRadio
              label="TYPE"
              name="capability-test-type-filter"
              options={TYPE_OPTIONS}
              value={testType}
              onChange={(v) => setTestType(v as CapabilityTestType | '')}
            />
          </FilterGroup>
          <FilterGroup label="RESULT">
            <FilterRadio
              label="RESULT"
              name="capability-test-result-filter"
              options={RESULT_OPTIONS}
              value={result}
              onChange={(v) => setResult(v as CapabilityTestResult | '')}
            />
          </FilterGroup>
        </FilterSidebar>

        <div className="tasks-explorer-main">
          <ListToolbar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder="Search tests — title, scenario, outcome…"
            count={listed.isSuccess ? `${visible.length} test${visible.length === 1 ? '' : 's'}` : undefined}
            actions={
              canEdit ? (
                <button type="button" className="btn-primary btn-small" onClick={() => setRecording(true)}>
                  Record test
                </button>
              ) : undefined
            }
          />

          <div className="tasks-stats-strip deficiency-stats" role="group" aria-label="Capability test counts">
            <div className="tasks-stat-item" data-testid="capability-stat-total">
              <span className="tasks-stat-value tasks-stat-total">{stat(counts.total)}</span>
              <span className="tasks-stat-label">TESTS</span>
            </div>
            <div className="tasks-stat-item" data-testid="capability-stat-passed">
              <span className="tasks-stat-value tasks-stat-completed">{stat(counts.passed)}</span>
              <span className="tasks-stat-label">PASSED</span>
            </div>
            <div className="tasks-stat-item" data-testid="capability-stat-with-findings">
              <span className="tasks-stat-value tasks-stat-in-progress">{stat(counts.withFindings)}</span>
              <span className="tasks-stat-label">PASSED WITH FINDINGS</span>
            </div>
            <div className="tasks-stat-item" data-testid="capability-stat-failed">
              <span className="tasks-stat-value tasks-stat-overdue">{stat(counts.failed)}</span>
              <span className="tasks-stat-label">FAILED</span>
            </div>
            <div className="tasks-stat-item" data-testid="capability-stat-open-findings">
              <span className="tasks-stat-value tasks-stat-total">{stat(counts.openFindings)}</span>
              <span className="tasks-stat-label">OPEN FINDINGS</span>
            </div>
          </div>

          <div className="deficiency-page-body">
            {!canEdit && (
              <p className="gov-muted">Only an editor or admin can record a test, correct one or add a finding.</p>
            )}
            {members.isError && (
              <div role="alert" className="gov-error">
                Could not load the members, so people show as unknown: {errorText(members.error)}
              </div>
            )}
            {listed.isError && listed.data && (
              <div role="alert" className="gov-error">
                Could not refresh the capability tests: {errorText(listed.error)}. Showing the last list loaded.
              </div>
            )}
            {listed.isLoading ? (
              <p className="tasks-loading">Loading capability tests…</p>
            ) : !listed.data ? (
              <div role="alert" className="gov-error">
                Could not load the capability tests: {errorText(listed.error)}
              </div>
            ) : visible.length === 0 ? (
              <p className="deficiency-empty">
                {filtered
                  ? 'No capability tests match these filters.'
                  : 'No capability tests recorded. Record an incident response exercise, a continuity test or another test of what the controls say the organisation can do; each finding becomes a deficiency.'}
              </p>
            ) : (
              <ul className="deficiency-list">
                {visible.map((t) => (
                  <CapabilityTestRow
                    key={t.id}
                    organizationId={organizationId}
                    test={t}
                    name={names.get(t.id)!}
                    users={members.data}
                    canEdit={canEdit}
                    expanded={expanded.has(t.id)}
                    onToggle={() => toggle(t.id)}
                    onEdit={setCorrecting}
                    onAddFinding={setAddingTo}
                    onEditFinding={setEditingFinding}
                    onCompleteFinding={setCompletingFinding}
                  />
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
      {recording && <CapabilityTestFormModal organizationId={organizationId} onClose={() => setRecording(false)} />}
      {correcting && (
        <CapabilityTestFormModal
          key={correcting.id}
          organizationId={organizationId}
          test={correcting}
          onClose={() => setCorrecting(null)}
        />
      )}
      {addingTo && (
        <AddFindingModal key={addingTo.id} organizationId={organizationId} test={addingTo} onClose={() => setAddingTo(null)} />
      )}
      {editingFinding && (
        <DeficiencyFormModal
          key={editingFinding.id}
          organizationId={organizationId}
          deficiency={editingFinding}
          onClose={() => setEditingFinding(null)}
        />
      )}
      {completingFinding && (
        <DeficiencyCompleteModal
          key={completingFinding.id}
          organizationId={organizationId}
          deficiency={completingFinding}
          onClose={() => setCompletingFinding(null)}
        />
      )}
    </div>
  )
}
