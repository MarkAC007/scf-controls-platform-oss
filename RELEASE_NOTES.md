# v0.33.0

Adds bring-your-own evidence storage: an organisation can keep its evidence in its own S3-compatible bucket, configured and connection-tested under Settings, Evidence storage, and copy existing evidence between stores; installs that configure nothing keep the bundled MinIO untouched. Azure Blob is retired and the AZURE_STORAGE_* variables are ignored, so an install that relied on them must configure its store in the app before upgrading. A missing evidence store now refuses uploads with a 409 that names the screen to fix it, instead of a 503. One migration (evstorcfg1a1); the deployment guide was rewritten and both READMEs now point at docs.scfcontrolsplatform.app.

## What's new

**Bring your own evidence storage.** An organisation can now keep its evidence in a store of
its own. Under Settings, Evidence storage you create a configuration as a draft, test it, and
activate it; activation writes, reads back and deletes a real object first, so nothing goes live
unproved. Presets cover the common S3-compatible providers, and the credential is encrypted in the
database under `SCF_SECRET_KEY` and is never returned, not even masked. Evidence can be copied
between stores from the same screen. Resolution is the organisation's store, then the platform
store, then the process environment, so an existing installation keeps working untouched and an
organisation that configures nothing is never worse off. See
[Evidence storage](https://docs.scfcontrolsplatform.app/admin-guide/deployment/#evidence-storage).

## Fixes

- The "Enable Trust Portal" toggle in Organisation settings is readable again in the light theme;
  its label had been rendering white on white.
- Documentation: 22 pages corrected against shipped behaviour, the deployment guide rewritten
  end to end (requirements, every installer option, first sign-in, upgrades, backups), and both
  READMEs now point at https://docs.scfcontrolsplatform.app/ instead of repeating it.

## Migrations

- `evstorcfg1a1` — adds the evidence storage configuration table and a nullable column on
  evidence files that pins each stored object to the store it lives in. It seeds nothing.

`scripts/upgrade.sh` runs this after its backup. A plain `docker compose up -d` refuses to
migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before
upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.33.0` (read `UPGRADING.md` first).
- **Azure Blob is retired.** `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY` are no
  longer read; the backend only logs that they are being ignored. Because that setting used to
  override every S3 setting in `.env`, an install that relied on it will resolve to a different
  store after upgrading. Configure the bucket under Settings, Evidence storage first, then upgrade.
- **A missing evidence store now refuses uploads with a `409`** that names the screen to fix it,
  instead of a `503`. Installs with the bundled MinIO or a configured bucket are unaffected.
