# v1.0.1

.

## Fixes and improvements

- Document generation: document profiles, ISMS Profile and the ISO 27001 core pack (PR 1170)

## Migrations

- `docprof001` — Document profiles: bundled document scopes.
- `ismsprof001` — ISMS profile: the organisation facts ISO 27001 clause documents need.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v1.0.1` (read `UPGRADING.md` first).
