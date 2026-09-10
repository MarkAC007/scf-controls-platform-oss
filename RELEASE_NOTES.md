# v0.32.0

Retires the Control Documents Mapper (CDM). The `/organizations/{org_id}/cdm/*` routes are removed; migration cdmdrop001 drops the five `cdm_*` tables (one-way; refuses while rows exist unless SCF_CDM_DROP_ACK=1) and a post-upgrade script purges uploaded CDM files. Document generation is unchanged.

## What's new

- Zero-touch credential provisioning — wizard, secrets overlay, per-call resolution, encrypted integrations (PR 948)

## Fixes and improvements

- Force-with-lease the version-bump branch push
- External document management integration scoping paper (PR 930)

## Migrations

- `intsec947a1` — Integration secrets, platform audit log, and encrypted credential columns

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.32.0` (read `UPGRADING.md` first).
