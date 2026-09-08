# v0.29.0

Retires the Control Documents Mapper (CDM). The `/organizations/{org_id}/cdm/*` routes are removed; migration cdmdrop001 drops the five `cdm_*` tables (one-way; refuses while rows exist unless SCF_CDM_DROP_ACK=1) and a post-upgrade script purges uploaded CDM files. Document generation is unchanged.

## What's new

- Header refresh control, focus refetch and a per-org change cursor (PR 924)

## Fixes and improvements

- Suppress the five avoid-sqlalchemy-text false positives blocking the OSS 0.28.0 release (PR 920)

## Migrations

- `auditorgts1` — Composite index on audit_log (organization_id, changed_at) for the change cursor.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.29.0` (read `UPGRADING.md` first).
