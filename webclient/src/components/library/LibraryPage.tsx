/**
 * LibraryPage — container for the Control Library tab.
 *
 * Owns: filters, search, scrollOffset state (survive view switches because this
 * component stays mounted; the container never unmounts while on the library tab).
 *
 * Renders:
 *   - LibraryList  when props.item == null
 *   - ControlDetailPage for the resolved control when props.item is set
 *
 * Navigation contract (matches evidence idiom):
 *   - List → detail : onItemChange(id)  — App does pushSearch so Back returns to list
 *   - Prev / next   : onItemChange(id)  — App does replaceSearch (one history entry)
 *   - Back / Esc    : onItemChange(null) — App does pushSearch, scroll position
 *                     is preserved because LibraryPage stays mounted
 *
 * Deep-link resolution (ruling 5):
 *   When the item param arrives from the URL but is not in the current filtered
 *   set (e.g., different filter active), a one-shot fetchControlsPage({ search: scf_id })
 *   finds the control. Pager is disabled (position=null) in this case.
 *
 * URL writes (pushSearch / replaceSearch / withLibraryItem) are owned by App.tsx,
 * not this component. App reads libraryItem from readAppLocation and passes it down
 * as `item`; onItemChange notifies App to write the URL. This keeps LibraryPage
 * testable without a browser history stub.
 */
import {
  useState,
  useMemo,
  useCallback,
  useEffect,
  useRef,
  type JSX,
} from 'react'
import type { ERLFile, FrameworkNameMap, ScopedControlsFile, EnrichedControl } from '../../types'
import { useControlsQuery, flattenControlPages } from '../../hooks/useControlsQuery'
import { useDebounce } from '../../hooks/useDebounce'
import { fetchControlsPage } from '../../data/catalogApi'
import { enrichControl } from '../../data/loaders'
import type { LibraryFilters } from './LibraryList'
import LibraryList from './LibraryList'
import ControlDetailPage from './ControlDetailPage'

// ─── Props ────────────────────────────────────────────────────────────────────

export interface LibraryPageProps {
  /** The scf_id from the URL ?item= param, or null for the list view. */
  item: string | null
  /**
   * Called when the user navigates to a control or back to the list.
   * App receives this and does pushSearch (list→detail, back) or
   * replaceSearch (prev/next) then updates item via readAppLocation.
   */
  onItemChange: (id: string | null) => void
  scopingData: ScopedControlsFile | null
  erlData?: unknown
  frameworkNames?: Record<string, string>
  onNavigateToEvidence?: (evidenceId: string) => void
  organizationId?: string
  /**
   * Bulk-loaded EnrichedControl[] from App.tsx (loaded via /api/catalog/bulk/controls
   * with ALL fields including framework_mappings, pptdf_applicability, control_question,
   * evidence_requests, cmm_maturity etc.). Used to resolve full control data for the
   * detail view — the paginated list endpoint returns only a slim serializer.
   */
  controls?: EnrichedControl[]
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function LibraryPage({
  item,
  onItemChange,
  scopingData,
  erlData,
  frameworkNames,
  onNavigateToEvidence,
  organizationId,
  controls: bulkControls,
}: LibraryPageProps): JSX.Element {
  // ── Owned state ──────────────────────────────────────────────────────────

  const [filters, setFilters] = useState<LibraryFilters>({
    domain: 'all',
    csf: 'all',
    weight: 'all',
  })
  const [search, setSearch] = useState('')
  const [scrollOffset, setScrollOffset] = useState(0)

  // ── Bulk catalog index (memoised from App-level bulk load) ─────────────
  //
  // App.tsx loads all controls via /api/catalog/bulk/controls (full serializer).
  // We index them by scf_id so we can resolve the full EnrichedControl for
  // detail/deep-link without depending on the slim paginated serializer.

  const bulkById = useMemo<Map<string, EnrichedControl>>(
    () => new Map((bulkControls ?? []).map((c) => [c.scf_id, c])),
    [bulkControls],
  )

  // ── Scope map (memoised from scopingData — ruling 3/4) ───────────────────

  const { scopeById, inScopeCount } = useMemo(() => {
    if (!scopingData) return { scopeById: undefined, inScopeCount: undefined }
    const map = new Map<string, boolean>()
    let count = 0
    for (const sc of scopingData.scoped_controls) {
      map.set(sc.scf_id, sc.selected)
      if (sc.selected) count++
    }
    return { scopeById: map, inScopeCount: count }
  }, [scopingData])

  // ── Filtered list query (same cache key as LibraryList) ──────────────────
  //
  // We debounce search here with the SAME 300 ms delay as LibraryList so that
  // both hooks produce identical query keys once the debounce settles — sharing
  // the React Query cache and avoiding duplicate in-flight requests.
  // LibraryList still receives the RAW `search` for its input display and
  // debounces internally; the two debounced values converge at the same key.

  const debouncedSearch = useDebounce(search, 300)

  const { data } = useControlsQuery({
    search: debouncedSearch || undefined,
    domain: filters.domain !== 'all' ? filters.domain : undefined,
    csf_function: filters.csf !== 'all' ? filters.csf : undefined,
    control_weighting:
      filters.weight !== 'all' ? parseInt(filters.weight, 10) : undefined,
  })
  const { controls: rawControls } = flattenControlPages(data?.pages)

  const filteredControls = useMemo<EnrichedControl[]>(() => {
    return rawControls.map((c) =>
      enrichControl(c, {}, erlData as ERLFile, frameworkNames as FrameworkNameMap),
    )
  }, [rawControls, erlData, frameworkNames])

  // ── Deep-link resolution (ruling 5) ─────────────────────────────────────
  //
  // Resolution priority:
  //   1. inListControl — control appears in the current filtered paginated results
  //   2. bulkById — App-level bulk load has full data; short-circuits one-shot fetch
  //   3. one-shot fetchControlsPage — fallback when bulk hasn't arrived yet (hard refresh)
  //
  // Pager is disabled (position=null) when item is resolved via bulk/fetch but not in
  // the filtered set.

  const [resolvedControl, setResolvedControl] = useState<EnrichedControl | null>(null)
  const [resolvingDeepLink, setResolvingDeepLink] = useState(false)

  // Find the control in the filtered list
  const inListControl = item
    ? filteredControls.find((c) => c.scf_id === item) ?? null
    : null

  // Prefer bulk-loaded full data over the slim paginated version
  const activeControl = item
    ? (inListControl ? (bulkById.get(item) ?? inListControl) : null) ??
      (bulkById.get(item) ?? resolvedControl)
    : null

  useEffect(() => {
    if (!item) {
      setResolvedControl(null)
      return
    }

    if (inListControl) {
      // Found in the current filtered list — no deep-link fetch needed
      setResolvedControl(null)
      return
    }

    // Check bulk index first — if we have it, no fetch needed
    if (bulkById.has(item)) {
      setResolvedControl(null)
      return
    }

    // Not in filtered list, not in bulk — resolve via one-shot fetch
    let cancelled = false
    setResolvingDeepLink(true)
    fetchControlsPage({ search: item, limit: 1, offset: 0 })
      .then((res) => {
        if (cancelled) return
        const found = res.controls.find((c) => c.scf_id === item)
        if (found) {
          const enriched = enrichControl(found, {}, erlData as ERLFile, frameworkNames as FrameworkNameMap)
          setResolvedControl(enriched)
        } else {
          setResolvedControl(null)
        }
      })
      .catch(() => {
        if (!cancelled) setResolvedControl(null)
      })
      .finally(() => {
        if (!cancelled) setResolvingDeepLink(false)
      })

    return () => {
      cancelled = true
    }
  }, [item, inListControl, bulkById, erlData, frameworkNames])

  // ── Position in filtered list ────────────────────────────────────────────
  //
  // If the item is resolved via deep-link (not in filtered set), pass
  // { index: null, total } so ControlDetailPage renders "— of N" with both
  // pager buttons disabled. If total is unknown (filteredControls empty and
  // still loading), fall back to position=null (no position text at all).

  const position = useMemo<{ index: number | null; total: number } | null>(() => {
    if (!item) return null
    if (inListControl) {
      const index = filteredControls.findIndex((c) => c.scf_id === item)
      if (index < 0) return null
      return { index, total: filteredControls.length }
    }
    // Deep-link item resolved via bulk or one-shot fetch — not in filtered set
    if ((bulkById.has(item) || resolvedControl) && filteredControls.length > 0) {
      return { index: null, total: filteredControls.length }
    }
    return null
  }, [item, inListControl, filteredControls, bulkById, resolvedControl])

  // ── Pager callbacks ──────────────────────────────────────────────────────

  const handlePrev = useCallback(() => {
    if (!position || position.index === null || position.index <= 0) return
    const prev = filteredControls[position.index - 1]
    if (prev) onItemChange(prev.scf_id)
  }, [position, filteredControls, onItemChange])

  const handleNext = useCallback(() => {
    if (!position || position.index === null || position.index >= position.total - 1) return
    const next = filteredControls[position.index + 1]
    if (next) onItemChange(next.scf_id)
  }, [position, filteredControls, onItemChange])

  const handleBack = useCallback(() => {
    onItemChange(null)
  }, [onItemChange])

  // ── Scoping entry for the active control ────────────────────────────────

  const scopingEntry = useMemo(() => {
    if (!activeControl || !scopingData) return null
    const sc = scopingData.scoped_controls.find(
      (s) => s.scf_id === activeControl.scf_id,
    )
    if (!sc) return null
    return {
      selected: sc.selected,
      implementation_status: (sc as { implementation_status?: string })
        .implementation_status,
      maturity: (sc as { maturity?: string | number }).maturity,
      owner: (sc as { owner?: string }).owner,
    }
  }, [activeControl, scopingData])

  // ── Deep-link miss: clear item via effect (not during render) ───────────
  //
  // When we have an item but it wasn't found in the filtered list AND the
  // one-shot fetch finished without finding it, we need to call onItemChange(null)
  // to fall back to the list view. We MUST do this in a useEffect — calling
  // onItemChange synchronously during render triggers React's "cannot update
  // while rendering" warning and causes a history push as a render side-effect.

  const deepLinkMissRef = useRef(false)
  // A miss occurs only when: item set, not found anywhere (filtered/bulk/resolved),
  // and the one-shot fetch has finished (not still loading)
  const deepLinkMiss = item != null && !activeControl && !resolvingDeepLink && !bulkById.has(item)
  deepLinkMissRef.current = deepLinkMiss

  useEffect(() => {
    if (deepLinkMissRef.current) {
      onItemChange(null)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deepLinkMiss, onItemChange])

  // ── Render ───────────────────────────────────────────────────────────────

  if (item && (activeControl || resolvingDeepLink || bulkById.has(item))) {
    if (!activeControl) {
      // Still resolving — show nothing (brief, then detail appears)
      return <div className="library-page-loading" aria-busy="true" />
    }

    return (
      <ControlDetailPage
        control={activeControl}
        scopingEntry={scopingEntry}
        position={position}
        onPrev={handlePrev}
        onNext={handleNext}
        onBack={handleBack}
        onNavigateToEvidence={onNavigateToEvidence}
        organizationId={organizationId ?? scopingData?.organizationId ?? undefined}
        scopingData={scopingData ?? undefined}
        frameworkNames={frameworkNames}
      />
    )
  }

  // deepLinkMiss case: onItemChange(null) scheduled via effect above
  // while resolving → show the loading placeholder
  if (item && !activeControl) {
    return <div className="library-page-loading" aria-busy="true" />
  }

  return (
    <LibraryList
      filters={filters}
      onFiltersChange={setFilters}
      search={search}
      onSearchChange={setSearch}
      onOpenControl={onItemChange}
      scopeById={scopeById}
      inScopeCount={inScopeCount}
      initialScrollOffset={scrollOffset}
      onScrollOffsetChange={setScrollOffset}
      erlData={erlData}
      frameworkNames={frameworkNames}
      bulkById={bulkById.size > 0 ? bulkById : undefined}
    />
  )
}
