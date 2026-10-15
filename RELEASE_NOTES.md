# v1.0.0

1.0.0 marks the platform's first stable release. The data model, REST API and Docker Compose deployment shape are now considered stable: future 1.x releases keep schema and API changes backward-compatible, and upgrades remain a single run of scripts/upgrade.sh. This release also closes the open Dependabot alerts in the web client and docs site and tightens the release pipeline so Release OSS only runs on a green CI. No migration steps are required beyond the standard upgrade.

## Fixes and improvements

- Skip heavy jobs on version-bump-only PRs; gate Release OSS on green CI (PR 1168)
- Close 23 of 24 open Dependabot alerts in webclient and docs-site (PR 1166)

## Upgrading

- Run `scripts/upgrade.sh v1.0.0` (read `UPGRADING.md` first).
