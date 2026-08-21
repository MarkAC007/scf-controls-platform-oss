# v0.19.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Document generation: correct section identity, enforce scope, add a reader, brand the PDF (PR 771)

## Upgrading

- Run `scripts/upgrade.sh v0.19.0` (read `UPGRADING.md` first).
