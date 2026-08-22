# v0.19.2

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Document status tracks the lifecycle, and revision history says what changed (PR 775)

## Migrations

- `docgen003` — Record what each generated version actually changed.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.19.2` (read `UPGRADING.md` first).
