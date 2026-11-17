#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Regenerate, or check, the hash-locked Python requirements.
#
#   scripts/lock-python-deps.sh                          recompile; pins already in a lock are kept
#   scripts/lock-python-deps.sh --upgrade-package NAME   move one package (repeat the flag for more)
#   scripts/lock-python-deps.sh --check                  recompile into a scratch copy; exit 1 on any difference
#
# Each lock is compiled from the .in file beside it:
#
#   backend/requirements.in            -> backend/requirements.txt            the backend image and CI
#   backend/requirements-dev.in        -> backend/requirements-dev.txt        CI's lint and test tools
#   scripts/requirements-importer.in   -> scripts/requirements-importer.txt   the catalogue importer image
#
# The .in files say what the project asks for. The .txt files pin every
# package, direct and transitive, to one version and list the sha256 of every
# file PyPI publishes for it. The Dockerfiles and CI install the .txt files
# with `pip install --require-hashes`, so a build installs exactly what was
# tested or it stops.
#
# The locks are universal: one file resolves for Linux, macOS and Windows on
# amd64 and arm64, with environment markers where a platform differs.
#
# Each lock pins a package once. When a package's newest release drops the
# Python the images run, uv would pin it twice (one version for 3.11, another
# for newer Pythons), and Dependabot cannot update a lock like that. The .in
# file caps such a package instead; see the block at the end of
# backend/requirements.in. backend/tests/test_python_lock.py fails on a second
# pin and says what to do.
#
# Edit a .in file, run this script, commit both files. The "Python Lock" job in
# .github/workflows/ci.yml runs --check and fails when they are out of step.
#
# Cooldown: a version chosen here must have been on PyPI for at least 7 days.
# The check does not apply the cooldown, because it only confirms that the
# committed pins and hashes still follow from the .in files and PyPI. UV_EXCLUDE_NEWER, uv's own
# variable, overrides the cutoff if it is already set.
#
# Needs uv. CI installs the version in .github/requirements/uv.txt; another
# version may format the output differently and fail the check.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The Python version the images run (FROM python:3.11-slim in the Dockerfiles).
PYTHON_VERSION="3.11"
COOLDOWN_DAYS=7

# directory:input:lock. Order matters: requirements-dev.in is constrained by
# requirements.txt, so the backend lock is compiled first.
LOCKS=(
  "backend:requirements.in:requirements.txt"
  "backend:requirements-dev.in:requirements-dev.txt"
  "scripts:requirements-importer.in:requirements-importer.txt"
)

usage() { sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

mode="write"
upgrades=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) mode="check"; shift ;;
    --upgrade-package)
      [ $# -ge 2 ] || { echo "--upgrade-package needs a package name" >&2; exit 2; }
      upgrades+=("--upgrade-package" "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
if [ "$mode" = "check" ] && [ ${#upgrades[@]} -gt 0 ]; then
  echo "--check does not take --upgrade-package" >&2; exit 2
fi

command -v uv >/dev/null 2>&1 || {
  echo "uv is not installed. Install the version CI uses:" >&2
  echo "  python -m pip install --require-hashes --only-binary :all: -r .github/requirements/uv.txt" >&2
  exit 2
}

pinned_uv="$(sed -n 's/^uv==\([0-9.]*\).*/\1/p' "$ROOT/.github/requirements/uv.txt" | head -1)"
have_uv="$(uv --version | awk '{print $2}')"
if [ -n "$pinned_uv" ] && [ "$have_uv" != "$pinned_uv" ]; then
  echo "note: uv $have_uv here, CI uses uv $pinned_uv; output may differ" >&2
fi

# compile BASE DIR INPUT LOCK [extra uv arguments]
# Runs from the lock's own directory with the same relative arguments every
# time, so the command uv records in the file header never changes.
compile() {
  local base="$1" dir="$2" input="$3" lock="$4"; shift 4
  ( cd "$base/$dir" && uv pip compile "$input" \
      --universal --python-version "$PYTHON_VERSION" --generate-hashes \
      -o "$lock" --quiet "$@" )
}

if [ "$mode" = "write" ]; then
  if [ -z "${UV_EXCLUDE_NEWER:-}" ]; then
    UV_EXCLUDE_NEWER="$(python3 -c "
import datetime as d
cutoff = d.datetime.now(d.timezone.utc) - d.timedelta(days=$COOLDOWN_DAYS)
print(cutoff.strftime('%Y-%m-%dT%H:%M:%SZ'))")"
  fi
  export UV_EXCLUDE_NEWER
  echo "Versions uploaded after $UV_EXCLUDE_NEWER are not considered."
  for entry in "${LOCKS[@]}"; do
    IFS=: read -r dir input lock <<<"$entry"
    compile "$ROOT" "$dir" "$input" "$lock" ${upgrades[@]+"${upgrades[@]}"}
    echo "wrote $dir/$lock"
  done
  exit 0
fi

# --check: copy each .in and its lock into a scratch tree with the same layout,
# recompile there, and compare. The committed files are never written.
#
# The scratch copy of a lock keeps its versions and loses its hashes. uv keeps
# the versions it finds in an existing lock, which is what makes the result
# repeatable, but it also copies that lock's hashes instead of asking PyPI
# again. With the hashes removed it has to fetch them, so a hash added to or
# deleted from a committed lock by hand shows up as a difference. So does a
# file PyPI has gained or lost for a pinned version since the lock was made:
# regenerate, and look at what changed before committing it.
unset UV_EXCLUDE_NEWER
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT
for entry in "${LOCKS[@]}"; do
  IFS=: read -r dir input lock <<<"$entry"
  mkdir -p "$scratch/$dir"
  for file in "$input" "$lock"; do
    [ -f "$ROOT/$dir/$file" ] || { echo "missing: $dir/$file" >&2; exit 1; }
  done
  cp "$ROOT/$dir/$input" "$scratch/$dir/$input"
  sed -e '/^[[:space:]]*--hash=/d' -e 's/ \\$//' "$ROOT/$dir/$lock" >"$scratch/$dir/$lock"
done

stale=0
for entry in "${LOCKS[@]}"; do
  IFS=: read -r dir input lock <<<"$entry"
  compile "$scratch" "$dir" "$input" "$lock"
  if diff -u "$ROOT/$dir/$lock" "$scratch/$dir/$lock" >"$scratch/diff.txt"; then
    echo "ok     $dir/$lock"
  else
    echo "STALE  $dir/$lock does not follow from $dir/$input:"
    sed -e 's|^--- .*|--- committed|' -e 's|^+++ .*|+++ recompiled|' "$scratch/diff.txt" | head -80
    stale=1
  fi
done

if [ "$stale" -ne 0 ]; then
  echo
  echo "Run scripts/lock-python-deps.sh and commit the result."
  exit 1
fi
