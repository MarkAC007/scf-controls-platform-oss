# v0.31.1

Retires the Control Documents Mapper (CDM). The `/organizations/{org_id}/cdm/*` routes are removed; migration cdmdrop001 drops the five `cdm_*` tables (one-way; refuses while rows exist unless SCF_CDM_DROP_ACK=1) and a post-upgrade script purges uploaded CDM files. Document generation is unchanged.

## Fixes and improvements

- Set client_max_body_size so the SCF workbook upload works (PR 943)

## Upgrading

- Run `scripts/upgrade.sh v0.31.1` (read `UPGRADING.md` first).
