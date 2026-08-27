# Task 7 Report: DocumentDetail refinement — breadcrumb + pager

## Status: COMPLETE

## What was done

### Changes (webclient/ only)

**`webclient/src/components/documents/DocumentReader.tsx`**
- Exported `DocumentReaderProps` interface (new props: `documentTitle`, `position`, `onPrev`, `onNext`)
- Added `isSuppressed()` helper (same pattern as `ControlDetailPage`)
- Added keyboard effect: `ArrowLeft → onPrev`, `ArrowRight → onNext`, `Escape → onBack`; suppressed in input/textarea/select/contentEditable; dropdown guard (`.theme-menu-panel`, `.user-dropdown-menu`)
- Added `breadcrumbBar` JSX variable (rendered even while doc loads, using `documentTitle` prop as fast-path title): back button → "Generated Documents", separator, doc name, pager position ("k of N documents"), prev/next icon buttons
- Breadcrumb also rendered in the loading state (wrapper `div.doc-reader` wraps breadcrumb + loading message)
- Derived pager values: `isFirst`, `isLast`, `positionText` (null → hidden; `index: null` → "— of N documents"; normal → "k of N documents")

**`webclient/src/components/documents/DocumentsPage.tsx`**
- Computes `docIndex` (findIndex in flat `documents` array) and `docPosition` (`{index, total}` or `null` when list not yet loaded)
- Adds `navigateToDoc`, `pagerPrev`, `pagerNext` helpers
- Passes `documentTitle`, `position`, `onPrev`, `onNext` to `<DocumentReader>`
- `?doc=`/`?mode=` semantics byte-identical (uses same `replaceState`/`readDocLocation` mechanism)

**`webclient/src/styles.css`** (append-only)
- Added `.doc-reader-breadcrumb`, `.doc-reader-back-btn`, `.doc-reader-back-icon`, `.doc-reader-breadcrumb-sep`, `.doc-reader-breadcrumb-name`, `.doc-reader-pager`, `.doc-reader-position`, `.doc-reader-pager-buttons`, `.doc-reader-pager-btn` (mirrors `control-detail-breadcrumb` CSS; all tokens from base palettes: `--border`, `--border-visible`, `--primary`, `--muted`, `--text`, `--accent-muted`)

**`webclient/src/components/documents/__tests__/DocumentReader.test.tsx`** (new)
- 18 TDD tests: breadcrumb renders, back fires onBack, pager position text ("k of N", "— of N", null), prev/next fire handlers, bounds disabled, keyboard ArrowLeft/ArrowRight/Esc fire, keyboard suppressed in input/textarea

## Gate totals

- `npx vitest run`: **96 test files, 1370 tests — all passed** (was 95/1352)
- `npm run build`: **✓ built in 7.79s**
- `npx tsc --noEmit`: **1 error** (pre-existing `AccountableOwnerTypeFilter.test.tsx` baseline; unchanged)

## Esc-semantics note

`DocumentReader` is only mounted when `editing=false` (DocumentsPage renders `DocumentEditor` instead when `editing=true`). There is no Esc conflict with edit mode — the reader and editor are mutually exclusive. The `isSuppressed()` guard covers any inline interactive elements (text selections, etc.) that might legitimately need Escape.

## Rail-badge outcome

The outline rail already implements the correct badge logic (lines 557–562 of the pre-existing code):
- Badge rendered only when `s.status !== 'unchanged'`
- Further suppressed by `quietChrome` (v1 docs or uniform-status docs) — **except** `ALWAYS_FLAGGED` (`conflict`, `pending_retirement`) which always show
- No change needed; noted here as confirmed correct.

## URL semantics

`?doc=` and `?mode=` parameters are byte-identical. The pager calls `setOpenDocument(id)` via `navigateToDoc`, which triggers the existing `useEffect` sync that writes `replaceState` — same mechanism, same guard.
