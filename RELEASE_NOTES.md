# v0.25.4

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- AI Evidence Assessment v2 — AO-grounded assessment, human confirmation, foundation repair, self-hosted cleanup (PR 882)

## Migrations

- `evassessver1` — Append-only version history for AI evidence assessments (#881).

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.25.4` (read `UPGRADING.md` first).
