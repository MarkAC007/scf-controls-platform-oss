# v0.25.3

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Self-hosted backup docs: remove orphaned aws-backup.sh + banners (PR 879)
- Document audit_log backup capture and restore safety (PR 878)
- Make the tenant export honest about its scope (PR 877)
- Add scheduled dual-store backup with retention (PR 876)
- Fix cross-tenant write authorization in database restore (PR 875)

## Upgrading

- Run `scripts/upgrade.sh v0.25.3` (read `UPGRADING.md` first).
