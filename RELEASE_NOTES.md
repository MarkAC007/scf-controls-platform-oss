# v0.40.1

.

## What's new

- Bundle OIDC into the published frontend image (PR 1059)

## Fixes and improvements

- Add a guided journey surface for practitioner-led engagements (PR 1058)

## Migrations

- `orgjourney1` — Organisational journey: an ordered path of stages an org walks.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.40.1` (read `UPGRADING.md` first).
