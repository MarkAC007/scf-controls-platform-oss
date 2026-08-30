# v0.25.2

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Give notifications an organization boundary (PR 868)
- `#864` follow-ups — img-src blob: build guard + shared branding helper (PR 866)
- Org logo missing top-left — allow blob: in img-src CSP + fix default logo asset (PR 865)
- Emit HTTPS on API trailing-slash redirects (PR 863)

## Migrations

- `notiforg1` — Add organization_id to notifications (#852).

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.25.2` (read `UPGRADING.md` first).
