# v0.38.0

.

## What's new

- Unify control library and framework scoping (PR 1050)

## Fixes and improvements

- Review queue defaults to the per-file tier; window is opt-in (PR 1048)
- Skip private-only file assertions in the OSS snapshot (unblocks Release OSS v0.37.0) (PR 1047)
- Review-queue window tier and window verdict tools are now MCP tools (PR 1046)

## Migrations

- `scopeoverride1` — Durable individual control scope overrides.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.38.0` (read `UPGRADING.md` first).
