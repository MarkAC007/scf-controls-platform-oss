#!/usr/bin/env bash
# Scan the lockfiles for known vulnerabilities and apply the blocking policy.
#
#   scripts/osv-scan.sh               scan this checkout
#   scripts/osv-scan.sh --root DIR    scan another tree with the same layout (the release snapshot)
#
# What it does:
#   1. Copies the lockfiles below, and nothing else, into a scratch directory.
#   2. Runs OSV-Scanner over them in a container. The container sees only that
#      directory, read-only. The scanner sends package names and versions to
#      api.osv.dev and gets back the advisories that match.
#   3. Hands the scanner's JSON to scripts/osv_policy.py, which decides.
#
# The policy (scripts/osv_policy.py has the detail):
#   - a high or critical finding with a fixed version published blocks;
#   - anything else is listed and does not block;
#   - a waiver lives in osv-scanner.toml with a reason and a date, and a waiver
#     past its date blocks;
#   - a scanner that fails, or output that does not cover every lockfile, is a
#     failure. A scan that did not happen never counts as a clean scan.
#
# Exit status: 0 nothing blocks, 1 a finding or an expired waiver blocks,
# 2 the scan did not complete.
#
# Used by the "OSV Scan" job in ci.yml, the nightly scan in osv-nightly.yml and
# the snapshot job of release-oss.yml. Needs docker and python3 (3.11 or later).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

# One exact image: a version tag for the reader, a digest for the registry.
# To move it: pick a release at least 7 days old, then
#   docker buildx imagetools inspect ghcr.io/google/osv-scanner:vX.Y.Z
# and put the tag and the index digest it prints here.
OSV_SCANNER_IMAGE="ghcr.io/google/osv-scanner:v2.7.0@sha256:4f3b7e2c64b9d38a5febc5a7e4a3dfef1d1362f726dea7a2ef4762b46469fcd0"

# parser:path. Every lockfile an image or CI installs from. The parser is
# named because the scanner picks one from the file name and does not know
# "requirements-importer.txt". backend/tests/test_osv_scan.py keeps this list
# in step with the locks the project has.
LOCKFILES=(
  "requirements.txt:backend/requirements.txt"
  "requirements.txt:backend/requirements-dev.txt"
  "requirements.txt:scripts/requirements-importer.txt"
  "package-lock.json:webclient/package-lock.json"
)

usage() { sed -n '2,5p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --root)
      [ $# -ge 2 ] || { echo "--root needs a directory" >&2; exit 2; }
      ROOT="$(cd "$2" && pwd)"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "docker is not installed; the scan did not run" >&2; exit 2; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null || {
  echo "python3 3.11 or later is needed for scripts/osv_policy.py; the scan did not run" >&2; exit 2
}

scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
mkdir -p "$scratch/src"

scan_args=()
expect_args=()
for entry in "${LOCKFILES[@]}"; do
  parser="${entry%%:*}"
  path="${entry#*:}"
  [ -f "$ROOT/$path" ] || { echo "missing lockfile: $path (under $ROOT); the scan did not run" >&2; exit 2; }
  mkdir -p "$scratch/src/$(dirname "$path")"
  cp "$ROOT/$path" "$scratch/src/$path"
  scan_args+=("--lockfile" "$parser:/src/$path")
  expect_args+=("--expect" "$path")
done

# --all-packages: the output lists every package the scanner read, not only
# the vulnerable ones, so the policy can see that each lockfile was scanned.
set +e
docker run --rm -v "$scratch/src:/src:ro" "$OSV_SCANNER_IMAGE" \
  scan source --format json --all-packages "${scan_args[@]}" \
  >"$scratch/results.json" 2>"$scratch/scanner.log"
scanner_status=$?
set -e

# The scanner exits 0 when it finds nothing and 1 when it finds something.
# Anything else means it did not finish: no network, no image, a lockfile it
# could not parse.
if [ "$scanner_status" -ne 0 ] && [ "$scanner_status" -ne 1 ]; then
  echo "OSV-Scanner did not complete (exit $scanner_status). The scan did not run:" >&2
  tail -20 "$scratch/scanner.log" >&2
  exit 2
fi

python3 "$HERE/osv_policy.py" \
  --results "$scratch/results.json" \
  --waivers "$ROOT/osv-scanner.toml" \
  --strip-prefix /src/ \
  "${expect_args[@]}"
