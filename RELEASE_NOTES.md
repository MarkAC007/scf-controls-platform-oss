# v1.8.0

.

## What's new

- Harden file readers after code and security review (PR 1309)

## Fixes and improvements

- Add WebP, PowerPoint and email samples for the evidence file type tests
- Add Security Objectives and ISMS Metrics pages
- Drive the evidence file type tests from the deployed allowlist
- Upload every accepted evidence file type and open it in the viewer
- Override glob to ^14.0.2 under workbox-build in webclient
- Update serialize-javascript in webclient
- Update fast-uri to 3.1.9 in webclient
- Bump vitest to 4.1.12 in webclient

## Upgrading

- Run `scripts/upgrade.sh v1.8.0` (read `UPGRADING.md` first).
