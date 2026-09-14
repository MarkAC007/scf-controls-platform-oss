# v0.34.0

KeyCloak user prov

## What's new

- Provision the invitee's Keycloak account on invite
- Wire KC admin credentials to the backend and document provisioning
- Show the IdP account and its one-time password on invite
- Schema and Keycloak admin client for bundled-IdP invite provisioning

## Fixes and improvements

- Mark only the async keycloak_admin tests
- Mark keycloak_admin tests asyncio for root-run pytest
- Badge copy — an IdP account may pre-date the invite
- Document invite-time Keycloak provisioning
- Provision the parsed DSN parts; add screenshot-led first-run setup page (PR 985)

## Migrations

- `invidpcols1` — Record the bundled-Keycloak identity created for an organisation invite.

`scripts/upgrade.sh` runs these after its backup. A plain `docker compose up -d` refuses to migrate an existing database until `SCF_MIGRATE_ACK` is set, so read `UPGRADING.md` before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.34.0` (read `UPGRADING.md` first).
