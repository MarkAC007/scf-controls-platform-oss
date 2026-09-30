# v0.44.0

.

## What's new

- Readable framework names, correct grouping, foldable browser with status coverage bars (PR 1104)
- Split detail pages, breadcrumb scope action, full-window control graph
- Faster Framework Mappings, angled headers, domain filters, single page titles
- Scope-aware evidence tasks and orphan handling on unscope (PR 1101)

## Migrations

- `rescope001` — Scoped controls: re-scope staleness stamps.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.44.0` (read `UPGRADING.md` first).
