# v1.5.0

Improvements to Audit Engagements Module

## What's new

- Engagements: evidence review board and dashboard (PR 1252)
- Bump @codemirror/commands from 6.10.4 to 6.11.1 in /webclient (PR 1202)

## Fixes and improvements

- Bump esbuild from 0.28.1 to 0.28.2 in /webclient (PR 1208)
- Bump python-dotenv from 1.2.2 to 1.2.4 in /backend (PR 1215)
- Engagements: clickable controls, control → evidence → artifact tree, no retired controls (PR 1251)

## Migrations

- `engrev001` — Engagement evidence review board: review state, events, and their supports.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v1.5.0` (read `UPGRADING.md` first).
