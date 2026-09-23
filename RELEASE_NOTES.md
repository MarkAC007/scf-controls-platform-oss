# v0.41.3

.

## Fixes and improvements

- Mark the async tests so they run under CI's strict asyncio mode
- A refused reseed must fail the import task, not report "complete"
- The reseed guard must reject unparseable inputs, not just absent ones
- Validate inputs before destructive reseed; stage catalogue imports

## Upgrading

- Run `scripts/upgrade.sh v0.41.3` (read `UPGRADING.md` first).
