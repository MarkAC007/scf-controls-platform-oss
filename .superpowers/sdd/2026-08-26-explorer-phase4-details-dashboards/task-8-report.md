# Task 8 Report — Phase-4 Integration Sweep

**Commits:** d5c886a + ee888a5
**Branch:** scf-redesign/phase-4
**Gate:** 97 test files / 1376 tests passed | build ✓ | tsc = 1 error (AccountableOwnerTypeFilter baseline)

---

## Item 1: Dead CSS Removal

All deletions grep-verified (zero `.tsx`/`.ts` consumers) before deletion.

### Risk slide-over CSS

**Deleted:**
- `.risk-detail-overlay` / `.risk-detail-backdrop` / `.risk-detail-panel-container` block (~18042–18115 pre-edit) — 0 TSX refs
- Responsive refs at @media 640px + @media 900px (`.risk-detail-panel-container`, `.risk-detail-area`)
- `.risk-detail-area` (×3 blocks: ~17187, ~17993, ~18105) — 0 TSX refs
- Full old RiskAssessmentDetail slide-over CSS: `.risk-detail-panel`, `.risk-detail-empty`, `.risk-detail-header`, `.risk-detail-title-row`, `.risk-detail-content`, `.risk-detail-actions` and all sub-rules (~17399–17543 pre-edit)

**Kept:**
- `.risk-category-badge` — live in RiskAssessmentList.tsx:326
- `.risk-score-row` — live in RiskDetailPage.tsx:536,608
- `dm-*` classes — live in DomainDetailPanel.tsx (DocumentMap, not risk)

### Scoping-era orphan CSS

**Deleted (0 TSX refs each):**
- `.bulk-scope-toast` + sub-rules + `@keyframes toastSlideIn`
- `.filters-toggle-btn` / `.filter-badge` / `.filters-dropdown`
- `.scope-filter-toggle` (wrapper only; `.scope-toggle-btn` KEPT — live in Dashboard.tsx:557,563 + WorkQueuePanel.tsx:29,35)
- `.scoping-stats-modern` / `.scoping-stat-main` / `.scoping-stat-value` / `.scoping-stat-label` / `.scoping-stat-secondary` / `.scoping-mini-stat` / `.mini-stat-value` / `.mini-stat-label` / `.scoping-progress-mini*` / `.btn-advanced-toggle` (×2 occurrences each cleared)
- `.detail-tabs` / `.detail-tab` / `.detail-tab-icon` / `.detail-tab-count` (×2 occurrences each at ~14285 + ~22614)
- `.sidebar-control-card` / `.sidebar-card-checkbox` / `.sidebar-card-content` / `.sidebar-card-header` / `.sidebar-card-name`
- `.sidebar-card-team` / `.evidence-card-team` combined selector (dead); `.sidebar-card-team-empty` dropped
- `.detail-header-redesign` / `.detail-header-badges` (×2 occurrences)
- `.scoping-stats-card` / `.scope-badge-compact` / `.control-card-desc`

**Kept (live):**
- `.detail-header-split` / `.detail-header-left` / `.detail-header-right` / `.detail-header-scrm` — live in ScopingDetailPage.tsx:440-484
- `.filter-select` / `.filter-group` — live in TeamListFilters.tsx + EvidenceReporting.tsx
- `.form-hint-block` / `.form-hint` / `.char-counter` / `.form-control-readonly` — live in ScopingDetailPage + InviteClientModal
- `.evidence-card-team-empty` — live in EvidenceReview.tsx:999

### TaskEditModal

- **STAYS** — EvidenceTaskList.tsx:4 imports it, EvidenceTaskList.tsx:312 uses it. Noted in brief; confirmed.

---

## Item 2: Comments

- **RiskDashboard.tsx ~line 11 + 329:** "list stays mounted beneath" corrected to "list NOT mounted (early return); pager position tracked in filteredIds/pagerPosition state that persists across renders." (The code uses an early return so the list genuinely unmounts when detail is open — the old comment was wrong.)
- **VendorManagement.tsx ~line 83:** Added display:contents explanation: why `contents` not `block` when visible (no stacking context/box model), why `none` makes list non-focusable while keeping it mounted.

---

## Item 3: A11y Pass

- **VendorRegistry.tsx delete button (~357):** Added `aria-label={\`Delete ${vendor.name}\`}` (SVG already had `aria-hidden="true"`)
- **UserManagement.tsx remove button (~443):** Added `aria-label={\`Remove ${name} from organization\`}` + `aria-hidden="true"` on decorative SVG
- **SystemsRegistry edit/delete buttons:** Text-labelled ("Edit", "Delete", "Yes", "No") — no change needed, already accessible
- **VendorManagement hidden-list wrapper:** Added `aria-hidden={vendorItem ? true : undefined}` (item 5 defence)

---

## Item 4: FilterRadio Primitive

**Created:** `webclient/src/components/explorer/FilterRadio.tsx`
- Props: `label` (group aria-label), `options` ({value,label}[]), `value`, `onChange`, optional `name`
- Renders `role="radiogroup"` with custom radio dots styled as `explorer-filter-radio-*` (mirrors ScopingList's `scoping-scope-radio-*` pattern)
- CSS added in styles.css after `.explorer-filter-check-count` block

**Tests:** `webclient/src/components/explorer/__tests__/FilterRadio.test.tsx` — 6 tests
- radiogroup role with aria-label
- one radio per option
- checked state matches value prop
- onChange dispatches new value
- name prop plumbed through
- auto-generated name when name omitted

**Adopted in TasksPage:**
- STATUS filter: replaced 3 single-select FilterCheckboxes with `FilterRadio` (added "All" option)
- TASK TYPE filter: replaced 6 single-select FilterCheckboxes with `FilterRadio` (added "All types" option)
- `FilterCheckbox` import removed from TasksPage.tsx

**ScopingList adoption:** SKIPPED — ScopingList's radio implementation is bespoke (`scoping-scope-radio-*` CSS, nested inside its own filter group, different change signature `onFiltersChange({...filters, scope: value})`). Adopting FilterRadio would require restructuring ScopingList's filter group or adding an adapter; its existing tests all pass and the component is clean. Note only, as permitted by brief.

**Test fix:** `TasksPage.explorer.test.tsx` line 248 updated from `queryByLabelText(/status/i)` to `queryByRole('combobox', { name: /status/i })` — the old query now also matches the STATUS radiogroup's aria-label which is always in the DOM.

---

## Item 5: Vendor Hidden-List aria-hidden

Done in Item 3 above. VendorManagement wrapper div gets `aria-hidden={vendorItem ? true : undefined}`.

---

## Gate Results (pasted)

```
Test Files  97 passed (97)
     Tests  1376 passed (1376)
  Start at  20:01:58
  Duration  57.93s

build: ✓ built in 7.72s

tsc: src/components/__tests__/AccountableOwnerTypeFilter.test.tsx(26,38): error TS2322: Type 'string' is not assignable to type 'AccountableOwnerTypeValue'.
(1 error — known baseline from Task 4, sanctioned)
```
