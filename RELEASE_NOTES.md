# v0.28.0

Retires the Control Documents Mapper (CDM). The `/organizations/{org_id}/cdm/*` routes are removed; migration cdmdrop001 drops the five `cdm_*` tables (one-way; refuses while rows exist unless SCF_CDM_DROP_ACK=1) and a post-upgrade script purges uploaded CDM files. Document generation is unchanged.

## Fixes and improvements

- Phase 6 — retire CDM docs, openwiki, docs-site pages + redirects (PR 918)
- Phase 5 — drop cdm_* tables behind SCF_CDM_DROP_ACK, purge + probe scripts (PR 917)
- Drop CDM couplings from reconciliation, model registry, env template and CI (PR 916)
- Remove CDM API, tasks, services, schemas and queues from the backend (PR 915)

## Migrations

- `cdmdrop001` — Drop the five retired Control Documents Mapper (CDM) tables (#907).

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.28.0` (read `UPGRADING.md` first).
- No longer read:
  - `GEMINI_API_KEY`
  - `OPENAI_API_KEY`
