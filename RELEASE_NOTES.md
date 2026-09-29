# v0.42.0

.

## What's new

- Jev (TypeSafe System One) shadow and primary evidence-assessment engine (PR 1093)

## Fixes and improvements

- Kubernetes support: Helm chart, runtime frontend config, shared upgrade verifier (OSS `#112`) (PR 1094)

## Migrations

- `jevshadow001` — Evidence assessment: shadow verdicts from a second engine.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.42.0` (read `UPGRADING.md` first).
- New environment variables (all optional unless noted):
  - `EVIDENCE_JEV_MODEL`
  - `JEV_CONFIDENCE_CUTOFF`
  - `JEV_STATE_CHAR_BUDGET`
  - `TYPESAFE_API_KEY`
  - `TYPESAFE_API_URL`
