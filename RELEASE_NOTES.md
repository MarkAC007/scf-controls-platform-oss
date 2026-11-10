# v1.3.0

.

## What's new

- Load the SCF 2026.3 Risk/Threat Catalogs and restore risk→control links
- Procedure tier switch preview and draft seeding (PR 1177)
- Procedure tiers per document shape (21/12/8) (PR 1176)

## Fixes and improvements

- Register risk/threat catalog tables in CI fixtures

## Migrations

- `riskcat001` — SCF Risk and Threat Catalogs as catalog tables; per-code ratings on controls.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v1.3.0` (read `UPGRADING.md` first).
