# v1.9.0

.

## What's new

- Publish a subprocessor list from vendors on the trust portal (PR 1322)
- Scan gate accepts unfixed findings under a dated policy (PR 1320)

## Fixes and improvements

- Slim nginx base for the frontend, distribution upgrades at build (PR 1319)

## Migrations

- `subproc001` — Vendor subprocessor listing for the public trust portal.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v1.9.0` (read `UPGRADING.md` first).
