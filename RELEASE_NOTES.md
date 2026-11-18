# v1.7.0

General analytical improvements

## What's new

- ISMS metrics follow-ups: dashboard summary and maturity cards, KSI fixes, readable in every theme (PR 1265)
- Mock-up parity, pace, 9.1 document, measurement tasks and collector feed (phase 3) (PR 1264)
- Metric detail, filters, reminders and supporting measures (phase 2) (PR 1263)
- Security objectives register and ISMS metrics dashboard (PR 1262)

## Migrations

- `ismsobj001` — Security objectives and ISMS metrics: ``security_objectives``, ``objective_metrics``, ``isms_metric_results``.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v1.7.0` (read `UPGRADING.md` first).
