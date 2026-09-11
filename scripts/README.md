# Scripts

This directory ships the operator tools for the open-source release: the
**first-run installer**, the **in-place upgrade tool**, the **backup tool**, the
**SCF catalogue importer**, and a post-deploy smoke test.

## First-run install and file-backed credentials (`install.sh`)

`install.sh` runs the first-run wizard, which generates every credential the
stack needs into a `0600`-per-file secrets directory and writes a `.env` that
holds no secrets. On an existing `.env` install, `scripts/install.sh --import-env`
moves the credentials already in `.env` into that directory and switches the
checkout to the `docker-compose.secrets.yml` overlay. `docker/with-file-secrets.sh`
is the overlay's entrypoint for the backend and Celery services: it exports each
`*_FILE` secret into the environment before handing over to the image's own
command. See `UPGRADING.md` ("Upgrade path from a `.env` install").

## Backups (`backup.sh`)

`backup.sh` takes a validated backup set (Postgres dump, MinIO evidence volume,
and the credentials tarball when the secrets overlay is in use). `upgrade.sh`
runs it before every upgrade; run it yourself before any manual change.

## In-place upgrade (`upgrade.sh`)

`upgrade.sh` safely upgrades a self-hosted deployment to a newer release. Run it
on the Docker host, from the repository root, during a maintenance window:

```bash
scripts/upgrade.sh v0.9.0
```

It quiesces writers, takes a mandatory validated backup of **both** data stores
(Postgres + MinIO evidence), checks out the target tag, migrates as a one-shot,
verifies the running code, and rolls back automatically on failure. Roll back an
earlier upgrade with `scripts/upgrade.sh --rollback <backup-timestamp>`. See the
upgrade guide (`UPGRADING.md`) for details, including the air-gapped path.

> **Never run `docker compose down -v`** — the `-v` deletes your database and all
> evidence blobs with no undo. The upgrade script only ever uses `up -d --build`.

## SCF catalogue importer

`extract_scf_data.py` converts a user-supplied SCF controls workbook (`.xlsx`) into the JSON
the backend seeds on startup. The Secure Controls Framework content is licensed (CC BY-ND 4.0),
so the platform ships the importer **code only** — bring your own workbook.

Run it via the one-shot importer container:

```bash
docker compose --profile init run --rm catalog-importer
```

Mount your SCF `.xlsx` as described in the project README ("Bring your own SCF Excel catalogue").
The importer is version-agnostic (auto-detects the SCF release and resolves sheets dynamically).

`requirements-importer.txt` pins the importer's Python dependencies (pandas, openpyxl).

## Post-deploy smoke test (`verify-prod-build.sh`)

`scripts/verify-prod-build.sh http://localhost:5173` confirms the frontend is
serving a production build with its security headers rather than a dev server.
Read-only; safe against a live host.
