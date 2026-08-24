# v0.22.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Internal vs external-contractor membership, with the badge, filter and report that make it visible (PR 827)
- Team assignment for controls and evidence, with one accountable team enforced by the database (PR 826)
- Functions, teams and team membership with cross-tenant isolation enforced by the database (PR 824)

## Fixes and improvements

- Derive env_added instead of hand-maintaining it (PR 825)

## Migrations

- `teamsfunctions1` — Functions, teams and team members: accountability with tenant isolation in the database.
- `ctrlteamassign1` — Team assignment of controls and evidence, with tenant isolation in the database.
- `orgmembertype1` — Employment type on organisation membership, scoped to the membership.
- `invitemembertype1` — Employment type on the organisation invite, so the invite can carry it.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.22.0` (read `UPGRADING.md` first).
