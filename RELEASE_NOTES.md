# v0.19.1

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Make the document lifecycle usable — reachable decisions, stable identity, no phantom churn (PR 773)

## Migrations

- `docgen002` — Remap document_sections.section_id after dropping count parentheticals from slugs.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.19.1` (read `UPGRADING.md` first).
