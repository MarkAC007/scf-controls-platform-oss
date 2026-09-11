# v0.32.0

Zero-touch credential provisioning. The installer and setup wizard generate every credential, the backend resolves each secret per call from the mounted secrets directory rather than reading it once at import, and integration credentials are stored encrypted. Migration intsec947a1 adds the integration-secret, platform audit-log and encrypted credential columns; it is additive and runs automatically on upgrade.

## What's new

- Zero-touch credential provisioning — wizard, secrets overlay, per-call resolution, encrypted integrations (PR 948)

## Fixes and improvements

- Force-with-lease the version-bump branch push
- External document management integration scoping paper (PR 930)

## Migrations

- `intsec947a1` — Integration secrets, platform audit log, and encrypted credential columns

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.32.0` (read `UPGRADING.md` first).
