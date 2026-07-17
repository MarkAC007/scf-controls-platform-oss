# v0.9.0

Adds safe-upgrade tooling — a fail-closed migration guard, scripts/upgrade.sh with mandatory backups and atomic rollback, and an in-app update checker. Upgrading an existing deployment now requires scripts/upgrade.sh (it backs up first) or SCF_MIGRATE_ACK; a bare 'docker compose up --build' will refuse to auto-migrate.

## What's new

- **Safe, guided upgrades** — `scripts/upgrade.sh vX.Y.Z` quiesces writers,
  takes a mandatory validated backup of **both** data stores (Postgres +
  MinIO evidence), migrates as a one-shot, verifies the running code, and
  performs an **atomic rollback** on any failure. See `UPGRADING.md`.
- **Fail-closed migration guard** — the backend no longer silently
  auto-migrates an existing production database on startup. A bare
  `docker compose up --build` now **refuses** unless you either run
  `scripts/upgrade.sh` (recommended — it backs up first) or set
  `SCF_MIGRATE_ACK` to acknowledge you have your own backup.
- **In-app update checker** — a daily poller surfaces "an update is
  available" in the footer and the Database Stats panel for any signed-in
  user. Opt out with `SCF_UPDATE_CHECK=false`.

## Action required before upgrading

- **Do not** upgrade with `git pull && docker compose up --build`. Use
  `scripts/upgrade.sh v0.9.0` instead (read `UPGRADING.md` first).
- `/api/version` no longer returns precise version/build detail to
  **anonymous** callers — only to authenticated users. Update any tooling
  that scraped it unauthenticated.

## Migrations

- Adds `platform_upgrade_state` (new table; additive, safe).
