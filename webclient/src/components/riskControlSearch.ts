/**
 * The control search behind a custom risk's "Add control" (#1156).
 *
 * The server pages its matches (at most 200 a page) in scf_id order and
 * matches description text as well as id and title. Taking one capped page
 * and dropping already-linked controls afterwards could leave nothing for a
 * term with plenty of unlinked matches, so the search reads every page, and
 * the ranking drops linked controls before it cuts the list.
 */
import { fetchScopedControlsPage, type ScopedControlWithCatalog } from '../data/apiClient'

const PAGE_SIZE = 200 // the endpoint's maximum
const MAX_PAGES = 15 // 3,000 rows: past the whole catalogue

/** Every in-scope control the server matches to `term`. */
export async function searchInScopeControls(orgId: string, term: string): Promise<ScopedControlWithCatalog[]> {
  const found: ScopedControlWithCatalog[] = []
  for (let page = 0; page < MAX_PAGES; page++) {
    const result = await fetchScopedControlsPage(
      { scope_status: 'in_scope', search: term, limit: PAGE_SIZE, offset: found.length },
      orgId,
    )
    found.push(...result.controls)
    if (!result.controls.length || found.length >= result.total) break
  }
  return found
}

/**
 * The matches worth offering: not already linked, those whose id matches the
 * term first (from the start of the id, then anywhere), then those whose
 * title does, then description-only hits, each group in the server's order.
 */
export function rankControlMatches<T extends { scf_id: string; control_name?: string | null }>(
  controls: readonly T[],
  term: string,
  linked: ReadonlySet<string>,
  max = 10,
): T[] {
  const needle = term.trim().toLowerCase()
  const rank = (c: T) => {
    const id = c.scf_id.toLowerCase()
    if (id.startsWith(needle)) return 0
    if (id.includes(needle)) return 1
    if ((c.control_name ?? '').toLowerCase().includes(needle)) return 2
    return 3
  }
  return controls
    .filter((c) => !linked.has(c.scf_id))
    .map((c, i) => ({ c, r: rank(c), i }))
    .sort((a, b) => a.r - b.r || a.i - b.i)
    .slice(0, max)
    .map(({ c }) => c)
}
