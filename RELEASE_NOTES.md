# v0.20.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Bulk actions on the evidence list (PR 800)
- Surface the guidance the backend already had (PR 799)

## Fixes and improvements

- Stop blocking capture at the point of action (PR 789)
- A notification can open the item it names (PR 797)
- Make evidence items linkable, and stop carrying the target twice (PR 795)
- Assign evidence to the column the schedulers actually read (PR 791)
- Stop the evidence dashboard inventing and discarding facts (PR 794)
- One frequency vocabulary shared by every subsystem (PR 790)
- One model inventory, priced per model — AI evidence review was 404ing (PR 792)
- Regression-test the App Insights logging path (PR 784)

## Migrations

- `freqvocab001` — Normalise evidence_tracking.frequency to the canonical vocabulary (#783).
- `evassign001` — Backfill evidence assignment onto the columns the schedulers read (#781).

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.20.0` (read `UPGRADING.md` first).
