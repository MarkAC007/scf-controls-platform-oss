# v0.46.0

.

## Fixes and improvements

- Deficiencies Kanban board view (PR 1164)
- In-app ConfirmDialog replaces window.confirm/prompt (PR 1163)
- PIG display fixes — validator name, add-control search, audit label casing (PR 1161)
- Restyle comment thread on bench tokens (PR 1159)
- Restore risk detail field layout CSS (PR 1158)
- End-user guides for the PIG epic (PR 1162)
- PIG `#1123`: Approved Living Control Set revision (PR 1134)
- Merge PIG support stack (`#1132`–`#1152`): SCRMS-PIG epic `#1120`

## Migrations

- `pigbasis001` — Obligation basis: MCR / DSR on framework selections and scoped controls.
- `pigsys001` — System and vendor attributes for the boundary and recovery record.
- `pigtgt001` — Per-control target maturity override.
- `pigmat001` — Material controls and the approved procedure link.
- `pigrisk001` — Risk acceptance and standards exceptions.
- `pigdef001` — Control deficiency record (POA&M).
- `pigcomp001` — Organisation-level compensating controls.
- `pigcare001` — Due-care pack: control validation record and monthly posture snapshots.
- `pigcap001` — Capability test records, and a root cause on each deficiency.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.46.0` (read `UPGRADING.md` first).
