# v0.35.0

.

## What's new

- Log partial-success payloads at WARNING instead of a plain succeeded line (PR 1022)
- Put ownership in the collection record, not below the comments (PR 1011)
- Replace business-function multi-select with add/remove chips (PR 1008)

## Fixes and improvements

- Report FAILURE from /api/tasks/status when the task returned a failure payload (PR 1020)
- Remove dead save path and unused member-type plumbing from EvidenceReview (PR 1024)
- Define the .form-control-sm variant the bulk bars already use (PR 1023)
- Scope .btn-secondary flex:1 to modal footers and settings actions (PR 1021)
- Deflake useTeamFilteredEvidence.ownerType stale-error test (PR 1019)
- Remove the evidence "view by control" mode (PR 1012)
- Show the domain abbreviation in the Evidence domain filter (PR 1007)
- Re-assert the catalogue directory's group on Linux (PR 1004)
- Build every Anthropic client through one keyed constructor (PR 1005)
- Bulk bar gets Set maturity / Set status; paginated endpoint gets a response_model (PR 1009)
- Stop logging a returned failure payload as a success (PR 1006)
- Hydrate evidence collection maturity on load and export (PR 1010)

## Upgrading

- Run `scripts/upgrade.sh v0.35.0` (read `UPGRADING.md` first).
