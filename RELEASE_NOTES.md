# v0.24.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Phase 1 Foundation — Plex type, nav tokens, chrome components, dark nav, utility bar, footer (PR 834)

## Fixes and improvements

- Evidence workspace layout, pinned nav, tighter filters (PR 840)
- Phase 5 — remaining pages in explorer chrome (zero functionality loss) (PR 839)
- Constant format string in bulk-update error log (PR 838)
- Phase 4 Details & Dashboards — six detail pages, dashboard tabs, modal promotions (PR 837)
- Phase 3 List Rollout — Scoping bulk bar, Evidence, Risk, Vendors, Systems, Tasks, Users (PR 836)
- Phase 2 Reference Pair — Control Library full-width list + detail with pager (PR 835)
- Option A design mockups export (spec assets for `#832`) (PR 833)

## Upgrading

- Run `scripts/upgrade.sh v0.24.0` (read `UPGRADING.md` first).
