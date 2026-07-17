# Scripts

This directory ships two tools for the open-source release: the **SCF catalogue
importer** and the **in-place upgrade tool**.

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
