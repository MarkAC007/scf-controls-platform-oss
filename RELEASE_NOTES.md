# v0.21.0

Adds SCF document generation to the platform — generated documents with versions, sections and review transitions. Migration docgen001 creates five new tables and changes no existing schema.

## What's new

- Make audit_log append-only at the database level (PR 818)
- Attestation gate, segregation of duties, constrained review transitions (`#787`, `#803`) (PR 817)
- Anchor freshness on what evidence covers, not when it arrived (PR 816)
- Capture what the preparer asserts, not only what the file is (`#786`, `#802`) (PR 815)

## Fixes and improvements

- Anchor the coverage date tests to UTC, not the local clock (PR 819)
- Six defects from the v0.20.0 production console recon (`#807`–`#812`) (PR 814)
- Point the integrity work at `#57`, not its already-closed neighbour (PR 813)
- Verify evidence bytes server-side, scan every upload path, log downloads (PR 806)
- Let the deployment sweep run in the OSS snapshot (PR 805)
- Stop inheriting review status onto new window assessments (PR 804)

## Migrations

- `evintegrity001` — Record what the server measured about evidence bytes, not only what was claimed (#57).
- `evassertions001` — Record what the preparer asserts about evidence, not only what the file is (#786, #802).
- `orgassurance01` — Per-organisation assurance policy: attestation gate + reviewer independence
- `promptversion01` — Persist prompt_version on the AI assessment tables.
- `auditappendonly1` — Make audit_log append-only at the database level.

Migrations run automatically on upgrade. Review them before upgrading a deployment you cannot restore.

## Upgrading

- Run `scripts/upgrade.sh v0.21.0` (read `UPGRADING.md` first).
- New environment variables (all optional unless noted):
  - `CLAMD_HOST`
  - `CLAMD_PORT`
  - `DOWNLOAD_TOKEN_SECRET`
  - `EVIDENCE_INTEGRITY_SWEEP_BATCH`
  - `EVIDENCE_INTEGRITY_SWEEP_ENABLED`
  - `MALWARE_SCAN_ENABLED`
