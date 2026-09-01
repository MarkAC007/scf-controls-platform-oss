# v0.25.5

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Evidence detail/dashboard scroll in their own pane, not the document (iPad) (PR 885)
- Widen waitFor on owning-team pills to stop CI flake (PR 884)

## Upgrading

- Run `scripts/upgrade.sh v0.25.5` (read `UPGRADING.md` first).
