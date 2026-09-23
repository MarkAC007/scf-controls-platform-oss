# v0.41.1

.

## Fixes and improvements

- The workbook is the authority for successor pairings (PR 1071)
- Self-heal the live framework registry and convey publisher-declared changes (PR 1070)

## Migrations

- `fwreg002` — Framework registry: allow the 'recovered' source.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.41.1` (read `UPGRADING.md` first).
