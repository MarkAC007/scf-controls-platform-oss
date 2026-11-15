# v1.4.0

.

## What's new

- Publish a summary of each source scan with the release (PR 1248)

## Fixes and improvements

- Gitleaks evidence from push runs only; preflight needs OSV and Workflow Lint (PR 1249)
- Mask user emails in all backend log lines (PR 1241)
- Remove the agent-triage and claude-code-review workflows (PR 1247)
- Pin upload-pages-artifact to v4.0.0 so the docs deploy passes SHA pinning (PR 1246)
- Close the open docs-site Dependabot alerts and scan docs-site (PR 1242)
- A runbook for cutting a public release (PR 1240)
- Remove the unused agent-worker workflow (PR 1239)
- Drop unused system packages from the backend image (PR 1238)
- Clear the fixable High/Critical findings the release gate reports (PR 1237)
- Retire the scheduled OpenWiki workflow (PR 1232)
- Scope the public-repo token to an oss-release environment (plan 2.3) (PR 1234)

## Upgrading

- Run `scripts/upgrade.sh v1.4.0` (read `UPGRADING.md` first).
