# v0.10.0

Adds optional OIDC single sign-on — run the bundled Keycloak identity provider (compose profile 'idp') or bring your own IdP (Okta, Entra, Auth0, ...). Entirely opt-in; migration adds users.oidc_issuer (nullable, backfilled, safe). Also bumps Authlib to 1.6.9 for OIDC-related CVE fixes.

## What's new

- **Optional OIDC single sign-on** — redirect-based OIDC login for the web
  client, replacing/augmenting API-key and Google sign-in. Two ways to use it:
  - **Bundled Keycloak** — `docker compose --profile idp up -d` starts a
    Keycloak identity provider backed by the existing Postgres, auto-imports
    the `scf` realm, and (optionally) bootstraps an admin user. See the new
    *Identity Provider* admin guide.
  - **Bring your own IdP** — point the `OIDC_*` variables at Okta, Entra ID,
    Auth0, Google, or any standards-compliant OIDC provider; no extra
    containers needed.
- Everything is **opt-in**: with `VITE_OIDC_ENABLED=false` (the default)
  nothing changes — API-key and Google sign-in behave exactly as before.
- **Dependency security** — Authlib bumped 1.3.2 → 1.6.9 for OIDC-related
  CVE fixes.

## Upgrading

- Use `scripts/upgrade.sh v0.10.0` (read `UPGRADING.md` first). No breaking
  changes; no action required if you don't enable OIDC.
- To enable OIDC, copy the new *Bundled Identity Provider* block from
  `.env.example` into your `.env` (`VITE_OIDC_ENABLED`, `OIDC_*`, and — for
  the bundled profile — `KC_ADMIN_*`), then rebuild the frontend so the flag
  is baked into the bundle.

## Migrations

- Adds nullable `users.oidc_issuer`, backfills existing Google users, and
  replaces the single-column `google_sub` unique with a composite
  `(oidc_issuer, google_sub)` unique (additive, safe).
