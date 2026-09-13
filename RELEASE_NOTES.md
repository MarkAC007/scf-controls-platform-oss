# v0.33.1

Patching

## Fixes and improvements

- Re-exec the checked-out upgrade.sh after Phase 3 so target-release steps run (PR 980)
- Make the bump's summary a required dispatch input (PR 981)
- Close 12 Dependabot alerts — drop react-router-dom, vitest 4 (PR 976)
- Close 25 Dependabot alerts via Astro 7 / Starlight 0.42 (PR 975)
- Classify GCS by exact host or subdomain, not a bare suffix (PR 974)
- Harden the dispatch-time summary check after review
- Resolve the summary at dispatch instead of failing the bump PR

## Upgrading

- Run `scripts/upgrade.sh v0.33.1` (read `UPGRADING.md` first).
