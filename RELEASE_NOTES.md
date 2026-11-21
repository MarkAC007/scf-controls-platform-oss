# v1.7.1

QA fixes

## What's new

- Seeded baseline, multi-user, metrics and MCP suites for the QA gate (PR 1286)
- QA gate — deploy to rig QA, run the browser test plan, record a commit status (PR 1270)

## Fixes and improvements

- Align MCP-07 and R170-06 with the code (PR 1305)
- Stop running MCP servers when the gate deploys (PR 1303)
- Cover engagement edits and member invites (`#1284`, `#1285`) (PR 1302)
- Source ui for OIDC browser changes; track vendor criticality (`#1269`, `#1276`) (PR 1299)
- Gate write and admin controls by organisation role (PR 1301)
- Risk-summary counts the same risks throughout (PR 1300)
- Evidence not yet tracked no longer counts tracked evidence without a collecting system (PR 1294)
- Make bearer auth work on the evidence download endpoint (PR 1289)
- Stamp completed_date on PATCH and answer writes with the stored task (`#1268`, `#1283`) (PR 1292)
- Keep the path prefix in the admin base URL (PR 1298)
- Date an objective's 60-day change on the period a reading covers (PR 1291)
- Send the ready-for-review notification from the POST upsert route (PR 1288)
- Overdue tile uses the same calendar-day rule as the rows (PR 1296)
- Count each at-risk control once in the headline tile (PR 1293)
- Group Controls by owner on the accountable team (PR 1295)
- Editing an audit engagement no longer answers 500 (PR 1297)
- Save only the changed fields from the risk page (PR 1290)
- Refuse a risk owner from outside the organisation (PR 1287)
- Move the frontend's nginx from 1.31.8 to 1.31.9 (PR 1267)

## Upgrading

- Run `scripts/upgrade.sh v1.7.1` (read `UPGRADING.md` first).
