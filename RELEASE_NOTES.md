# v0.25.7

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Wave 8/8: 44px touch targets — login theme, footer/toolbar extenders, phone toolbar wrap (D-11, D-14, D-16) (PR 896)
- Escape-to-close + scroll lock on every overlay, drawer ✕ z-index, webhook create navigates (Wave 7/8, usability sweep — D-09, D-10, D-12) (PR 895)
- Default-collapse filter rail on phones + contain tasks table columns (Wave 6/8, usability sweep — D-06, D-07) (PR 894)
- Footer clearance for scroll panes + complete dvh sweep (Wave 5/8, usability sweep — D-05, D-08, D-13) (PR 893)
- File preview modal cannot scroll — assertion + AI panels unreachable (Wave 4/8, usability sweep — D-20) (PR 892)
- Virtualized row title overlap in library/scoping (Wave 3/8, usability sweep — D-04; D-03 closed as artifact) (PR 891)
- Add GET /api/evidence-tasks/{task_id} — task detail rendered 'Method Not Allowed' (Wave 2/8, usability sweep) (PR 890)
- Accept NULL changed_by_user_id — Audit Log page dead on every surface (Wave 1/8, usability sweep) (PR 889)

## Upgrading

- Run `scripts/upgrade.sh v0.25.7` (read `UPGRADING.md` first).
