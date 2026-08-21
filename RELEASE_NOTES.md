# v0.18.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Integrate SCF document generation into the platform (PR 762)

## Fixes and improvements

- Widen the version-bump assessment to the whole unreleased range (PR 765)
- Keep the control id when navigating from the work queue (PR 763)

## Migrations

- `docgen001` — Document generation: generated documents, versions, sections, transitions, settings.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.18.0` (read `UPGRADING.md` first).
