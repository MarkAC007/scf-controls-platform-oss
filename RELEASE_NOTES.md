# v0.23.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Allow teams to serve multiple functions (PR 830)
- A task's owning team, one rule for who hears about an item, and a queue that reads what assignment writes (PR 829)

## Migrations

- `evtaskteam1` — Evidence collection tasks gain an organisation and an optional owning team.
- `teamfunctions2` — Allow each team to serve one or more business functions.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.23.0` (read `UPGRADING.md` first).
