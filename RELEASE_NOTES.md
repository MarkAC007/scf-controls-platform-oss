# v0.34.2

Three fixes.

## Fixes and improvements

- The override workaround was inert, and the pin's provenance was wrong
- Give the hardened root services the gid they need on bind mounts
- Pull the bundled MinIO images from quay.io, not Docker Hub

## Upgrading

- Run `scripts/upgrade.sh v0.34.2` (read `UPGRADING.md` first).
