# v0.27.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Kill switch — hide CDM UI behind resolved cdm_enabled (default off) (PR 911)

## Upgrading

- Run `scripts/upgrade.sh v0.27.0` (read `UPGRADING.md` first).
