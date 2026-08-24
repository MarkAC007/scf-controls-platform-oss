import { describe, expect, it } from 'vitest'

/**
 * Source assertions over team ownership in the two list views.
 *
 * `TeamAssignmentList.test.tsx` proves the batching works; this proves the
 * real lists use it. Rendering ControlScoping or EvidenceReview to count
 * requests would need eight mocks between them and would end up asserting on
 * the mocks — what actually matters here is structural, and it is the kind of
 * thing a later edit removes by accident: no row, and nothing a row calls,
 * may fetch its own ownership.
 *
 * Modelled on `data/__tests__/interactiveRow.usage.test.ts`.
 */
const sources = import.meta.glob('../../**/*.{ts,tsx}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

/** Glob keys arrive relative to this file with redundant segments collapsed. */
function underSrc(relative: string): string {
  const from = ['src', 'components', '__tests__']
  const parts = relative.split('/')
  const out = [...from]
  for (const part of parts) {
    if (part === '.' || part === '') continue
    if (part === '..') out.pop()
    else out.push(part)
  }
  return out.join('/')
}

function source(path: string): string {
  const key = Object.keys(sources).find(k => underSrc(k) === `src/${path}`)
  if (!key) {
    throw new Error(
      `no source loaded for src/${path} — the glob matched ${Object.keys(sources).length} files`,
    )
  }
  return sources[key]
}

/** The org-scoped lists that carry an accountable-team column. */
const LIST_VIEWS = [
  'components/ControlScoping.tsx',
  'components/EvidenceReview.tsx',
]

/** Rendered once per row. Anything that fetches in here is the N+1 itself. */
const ROW_COMPONENTS = ['components/SidebarControlCard.tsx']

function occurrences(haystack: string, needle: string): number {
  return haystack.split(needle).length - 1
}

describe('team ownership in list views is batch-loaded', () => {
  it.each(LIST_VIEWS)('%s calls the batch hook exactly once', file => {
    const text = source(file)
    expect(occurrences(text, 'useTeamAssignments(')).toBe(1)
  })

  it.each(LIST_VIEWS)('%s never reads one item’s assignments', file => {
    const text = source(file)
    // The single-item read is for detail panels. In a list it is the N+1.
    expect(text).not.toContain('getItemTeamAssignments')
    // And the batch endpoint is reached through the hook, which dedupes it,
    // never called straight from the component.
    expect(text).not.toContain('listTeamAssignments(')
  })

  it('the controls list scopes the read to the page it has loaded', () => {
    const text = source('components/ControlScoping.tsx')
    // Server-paginated: asking for every assignment in the organisation to
    // render fifty rows fetches thousands of records to show fifty.
    expect(text).toContain("useTeamAssignments(organizationId, 'control', { itemIds: loadedControlDbIds })")
    expect(text).toContain('const loadedControlDbIds')
  })

  it('the evidence list deliberately does not, because it has no pages', () => {
    const text = source('components/EvidenceReview.tsx')
    // Evidence arrives complete in the scoping payload, so there is no page to
    // scope to — and an unpaginated list is the one thing here that could
    // reach the API's 1000-id ceiling.
    expect(text).toContain("useTeamAssignments(scopingData.organizationId, 'evidence')")
    expect(text).not.toContain('itemIds:')
  })

  it('a new page merges into the map instead of replacing it', () => {
    const hook = source('hooks/useTeamAssignments.ts')
    // Replacing would blank the badge column on every row already on screen
    // the moment the next page arrived.
    expect(hook).toContain('setByItemId(prev => ({ ...prev, ...map }))')
    // Items that came back absent are still recorded as answered, or every
    // scroll re-asks about everything nothing owns.
    expect(hook).toContain('for (const id of batch) fetchedIds.current.add(id)')
  })

  it('stays under the API ceiling on ids per request', () => {
    const hook = source('hooks/useTeamAssignments.ts')
    // MAX_ITEM_IDS is 1000 server-side and a 422 above it.
    expect(hook).toContain('MAX_ITEM_IDS_PER_REQUEST = 500')
    expect(hook).toContain('chunk(ids, MAX_ITEM_IDS_PER_REQUEST)')
  })

  it.each(ROW_COMPONENTS)('%s takes its team as a value and fetches nothing', file => {
    const text = source(file)
    expect(text).toContain('accountableTeam')
    expect(text).not.toContain('useTeamAssignments')
    expect(text).not.toContain('apiClient')
  })
})

describe('the team filter is pushed into the query, not only applied in the browser', () => {
  it('the controls list sends team_id and function_id to the server', () => {
    const text = source('components/ControlScoping.tsx')
    // Filtering only in the browser would narrow the pages already loaded and
    // quietly under-report the rest of the catalogue.
    expect(text).toContain('team_id: teamFilter !== ALL_TEAMS')
    expect(text).toContain('function_id: functionFilter !== ALL_TEAMS')
  })

  it('the fetcher forwards both to the paginated endpoint', () => {
    const text = source('data/apiClient.ts')
    expect(text).toContain("queryParams.set('team_id', params.team_id)")
    expect(text).toContain("queryParams.set('function_id', params.function_id)")
    // The filter lives on the paginated route, which is the one the infinite
    // query calls; the unpaginated /scoped-controls has no predicate.
    expect(text).toContain('scoped-controls-paginated')
  })

  it('both filters sit in the query key so a change resets pagination', () => {
    const text = source('hooks/useScopedControlsQuery.ts')
    // They ride in the `filters` object, which is already the query key —
    // appending a differently-filtered page to the last one would interleave
    // two result sets in one list.
    expect(text).toContain("queryKey: ['scoped-controls', orgId, filters]")
    expect(text).toContain('team_id: filters.team_id || undefined')
    expect(text).toContain('function_id: filters.function_id || undefined')
  })

  it('the evidence list asks the server too, so both lists agree what a team owns', () => {
    const text = source('components/EvidenceReview.tsx')
    expect(text).toContain('useTeamFilteredEvidence(')
    expect(text).toContain('serverFilteredTrackingIds.has(dbId)')
  })

  it('the evidence fetch forwards both filters', () => {
    const text = source('data/apiClient.ts')
    expect(text).toContain("params.set('team_id', filters.team_id)")
    expect(text).toContain("params.set('function_id', filters.function_id)")
  })

  it('a failed or in-flight evidence filter falls back instead of blanking', () => {
    const hook = source('hooks/useTeamFilteredEvidence.ts')
    // null means "do not narrow on me" — the caller still has the assignment
    // map, which carries the same semantics.
    expect(hook).toContain('setTrackingIds(null)')
    const list = source('components/EvidenceReview.tsx')
    expect(list).toContain('if (serverFilteredTrackingIds) return serverFilteredTrackingIds.has(dbId)')
  })

  it('the shared scoping loader is never itself filtered by team', () => {
    const loader = source('data/scopingService.ts')
    // Every screen reads this one payload. Narrowing it to a team would empty
    // the evidence tab, the dashboards and the control detail panel with it.
    expect(loader).toContain('await api.getEvidenceTracking()')
    expect(loader).not.toContain('getEvidenceTracking(orgId, {')
  })

  it('the controls list does not re-filter what the server already filtered', () => {
    const text = source('components/ControlScoping.tsx')
    // The rows the server returns ARE the filtered list. A second predicate
    // over the same data is two answers to one question, and the wrong one
    // still renders — it is the shape of defect this issue exists to remove.
    expect(text).not.toContain('matchesTeamFilters')
  })

  it('no longer tells the user the filter only reaches loaded pages', () => {
    const text = source('components/ControlScoping.tsx')
    // The caveat was true while filtering was client-side only. The server
    // filters now, so leaving it would be the documented-but-false defect in
    // the other direction.
    // Match the rendered sentence, not the phrase: "loaded so far" is
    // legitimate in a comment about which ids the page holds, and a guard that
    // cannot tell those apart fails on unrelated edits.
    expect(text).not.toContain('Team ownership is')
    expect(text).not.toContain('scroll to bring more into range')
    expect(text).not.toContain('team-filter-scope-hint')
  })
})

describe('team assignments are written with the verbs the API has', () => {
  /** Just the phase-3 block of the client — the file is 4k lines of other APIs. */
  const section = (() => {
    const text = source('data/apiClient.ts')
    return text.slice(text.indexOf('Team ownership of controls and evidence (Issue #822, phase 3)'))
  })()

  it('never PATCHes: the API exposes GET, POST and DELETE only', () => {
    expect(section).not.toContain('apiClient.patch')
  })

  it('promotes to accountable through the POST upsert', () => {
    // One call, whether or not the team was already assigned. The backend
    // demotes the incumbent in the same transaction.
    expect(section).toContain('export async function setAccountableTeam')
    expect(section).toContain('is_accountable: true')
    const promote = section.slice(
      section.indexOf('export async function setAccountableTeam'),
      section.indexOf('export async function clearAccountableTeam'),
    )
    expect(promote).toContain('assignTeamToItem')
    // Not a delete-then-recreate, which is the two-call race.
    expect(promote).not.toContain('removeTeamAssignment')
  })

  it('narrows the batch read with item_ids, the list parameter the API declares', () => {
    expect(section).toContain("params.append('item_ids'")
  })
})

describe('team ownership never claims to grant access', () => {
  const FEATURE_FILES = [
    'components/OwningTeams.tsx',
    'components/TeamListFilters.tsx',
    'hooks/useTeamAssignments.ts',
  ]

  it.each(FEATURE_FILES)('%s does not gate anything on team membership', file => {
    const text = source(file)
    // Permissions come from `organization_members.role` alone. If one of these
    // files starts reasoning about membership roles, the invariant has slipped.
    expect(text).not.toContain('membership_role')
    expect(text).not.toContain('listTeamMembers')
  })

  it('the panel says out loud that teams grant no access', () => {
    // Collapsed first: the sentence is wrapped across lines in the JSX.
    const flattened = source('components/OwningTeams.tsx').replace(/\s+/g, ' ')
    expect(flattened).toContain(
      'Teams grant no access — permissions come from organisation roles'
    )
  })
})

describe('the legacy free-text owner column is left alone', () => {
  const text = source('components/ControlScoping.tsx')

  it('still writes scoped_controls.owner from the settings list', () => {
    expect(text).toContain("updateField('owner', e.target.value)")
    expect(text).toContain('DEFAULT_OWNER_TEAMS')
    expect(text).toContain('orgOwnerTeams')
  })

  it('is labelled so it cannot be mistaken for a real team', () => {
    const flattened = text.replace(/\s+/g, ' ')
    expect(flattened).toContain('Owner Team Label')
    expect(flattened).toContain('it is not one of the teams under Users → Teams')
  })

  it('does not put a second team picker beside it', () => {
    // The one team selector on the details form is the legacy label. Owning
    // teams lives on the Assignments tab, next to the per-user picker.
    const detailsForm = text.slice(
      text.indexOf('Owner Team Label'),
      text.indexOf("activeTab === 'notes'"),
    )
    expect(detailsForm).not.toContain('OwningTeams')
  })
})
