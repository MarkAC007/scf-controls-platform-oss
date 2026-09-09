# v0.30.0

Retires the Control Documents Mapper (CDM). The `/organizations/{org_id}/cdm/*` routes are removed; migration cdmdrop001 drops the five `cdm_*` tables (one-way; refuses while rows exist unless SCF_CDM_DROP_ACK=1) and a post-upgrade script purges uploaded CDM files. Document generation is unchanged.

## What's new

- Add Word (.docx) export alongside PDF and Markdown (PR 926)

## Upgrading

- Run `scripts/upgrade.sh v0.30.0` (read `UPGRADING.md` first).
