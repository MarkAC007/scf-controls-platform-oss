# v1.3.2

Release hardening. upgrade.sh now verifies each release's attestations, and --require-verified refuses a release it cannot verify. Each release also publishes the image SBOMs, the VEX statements applied and the image-scan result as attested release assets, and the bundled Postgres, Redis and base images are pinned by digest. No database migration or configuration change.

## What's new

- Verify the release's attestations; --require-verified (plan 6.7) (PR 1224)

## Fixes and improvements

- Retire the legacy OSS publisher (plan 2.2) (PR 1233)
- ZAP baseline scan of the booted stack in Docker Build (plan 8.2) (PR 1231)
- Remove the lint steps that could not fail (plan 8.3) (PR 1230)
- Publish the SBOMs, scan gate result and VEX with each release (plan 7.5) (PR 1229)
- Every VEX waiver is owned, explained and in date (plan 7.4) (PR 1228)
- Gate the image scan findings, report-only until 2026-12-14 (plan 7.3) (PR 1227)
- Scan each image SBOM with a SHA-256-pinned Grype and the repo's VEX (plan 7.2) (PR 1226)
- A CycloneDX SBOM per release image, from a SHA-256-pinned Syft (plan 7.1) (PR 1225)
- Verify what a release really carries; drop the dead image attestation (plan 6.5) (PR 1223)
- Push images by digest; tag the version only after the public release (plan 6.1) (PR 1222)
- Resume from the release's own image record, not the movable tag (plan 6.4) (PR 1221)
- Attest the public release assets and publish the image record (plan 6.3) (PR 1220)
- Record the image digests in the release commit (plan 6.2) (PR 1219)
- Scan the lockfiles for known vulnerabilities and block on high or critical (plan 5.3) (PR 1218)
- Lint every workflow with zizmor and fail on any finding (PR 1213)
- Pin each Python package once so Dependabot can update the locks (PR 1212)
- Frozen frontend install and a type check that can fail (PR 1211)
- Install Python dependencies from hash-locked files (PR 1210)
- Turn on Dependabot with a 7-day cooldown, remove renovate.json (PR 1192)
- Pin base and bundled images by digest (PR 1191)
- Pin every action to a full commit with its exact version (PR 1189)
- Pin CI container images by digest and test on PostgreSQL 15 (PR 1190)
- Make the Docker Build boot test able to fail (PR 1188)
- Stop passing the personal token to the Docker Build checkout (PR 1187)
- The image publisher can push only from the sanitized snapshot (PR 1186)
- Fail the OSS snapshot on an unlisted top-level entry and strip seven leftovers (PR 1185)
- Bump vite 7.3.6 to 7.3.7 to close five open advisories (PR 1184)
- Bump dompurify 3.4.16 to 3.4.17 to close three open advisories (PR 1183)
- Bump Authlib 1.6.12 to 1.9.5 to close nine open advisories (PR 1182)

## Upgrading

- Run `scripts/upgrade.sh v1.3.2` (read `UPGRADING.md` first).
