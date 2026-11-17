# v1.6.0

update to Auditor invite workflow

## What's new

- Invite external auditors across the email-domain rule (PR 1260)
- Accept Markdown uploads and render them in the preview (PR 1259)

## Migrations

- `orgaud001` — External auditor roster: ``organization_auditors``.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v1.6.0` (read `UPGRADING.md` first).
