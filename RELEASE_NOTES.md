# v0.41.0

Frontend configuration moves to runtime, and the platform gains a Helm chart.

## What's new

- Read frontend configuration at runtime from `/config.js` instead of baking it into the bundle at build time. Branding and the evidence-review mode are now deployment settings, so one published image serves every deployment. `vite dev` and `webclient/.env` are unaffected.
- Kubernetes support: a Helm chart for the backend, Celery worker, Celery beat and frontend. The database, cache and object store are expected to already exist; credentials come from a Secret the chart does not create.
- `python -m scf_upgrade` — `migrate` applies pending migrations through the upgrade guard, `verify` asserts the schema is at head and the running image is the expected build.

## Fixes and improvements

- Stop logging a missing-`VITE_API_KEY` error on every page load under OIDC, which carries no API key by design.
- `scripts/upgrade.sh` verifies the schema head and running-image identity with the shared `scf_upgrade verify`, so the compose and Kubernetes paths cannot drift.

## Migrations

None. This release adds no Alembic revisions.

## Upgrading

- Run `scripts/upgrade.sh v0.41.0` (read `UPGRADING.md` first).
