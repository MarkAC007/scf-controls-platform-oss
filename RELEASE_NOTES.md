# v0.37.0

.

## What's new

- Default the window assessment surface on and document it (window parity 6/6) (PR 1042)
- Debounced window assessment on upload and webhook ingest (parity 5/6) (PR 1041)
- Lazy required_artifact_types extraction on first assessment (parity 4/6) (PR 1040)
- Window verdict confirm/override, window review queue, unassessable labels (winparity 3/6) (PR 1039)
- Assurance parity — versions, confirm/override, queue tier, KSI weighting (winparity 2/6) (PR 1038)
- V2 prompt with objectives, membership rule and text budget (parity 1/6) (PR 1037)

## Fixes and improvements

- Render PDFs in the preview modal by dropping the iframe sandbox (PR 1044)
- Compute next R-ORG-N code numerically, not as a string max (PR 1043)

## Migrations

- `winasv2cols1` — Window assessment verdict v2 columns (window parity with #881).
- `winasver1` — Append-only history and human confirmation for window assessments.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.37.0` (read `UPGRADING.md` first).
- New environment variables (all optional unless noted):
  - `ARTIFACT_TYPE_LAZY_EXTRACTION`
  - `WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS`
  - `WINDOW_ASSESSMENT_ON_INGEST`
  - `WINDOW_ASSESSMENT_TEXT_BUDGET`
