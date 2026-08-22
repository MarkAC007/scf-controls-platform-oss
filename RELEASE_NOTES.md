# v0.19.3

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## Fixes and improvements

- Keep the literal token add_header out of location-block comments
- Move all security headers to server level, drop per-location add_header
- Serve a production build by default, restore the missing headers

## Upgrading

- Run `scripts/upgrade.sh v0.19.3` (read `UPGRADING.md` first).
