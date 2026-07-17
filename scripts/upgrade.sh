#!/usr/bin/env bash
# =============================================================================
# upgrade.sh — safe, in-place upgrade for a self-hosted SCF Controls Platform.
#
# Run this ON THE DOCKER HOST, from the repository root, during a maintenance
# window. It upgrades the platform to a target git tag and — critically — does
# NOT blow up your deployment:
#
#   • it QUIESCES writers, then takes a MANDATORY backup of BOTH data stores
#     (Postgres via pg_dump -Fc, MinIO evidence via a volume tar) and validates
#     the Postgres dump before touching anything;
#   • it checks out the target tag, rebuilds, and runs migrations as an explicit
#     ONE-SHOT (never racing the whole stack against the schema change);
#   • it verifies the ACTUALLY-RUNNING code (alembic head + baked build stamp),
#     and on any failure performs an ATOMIC rollback (restore into a fresh DB,
#     then swap) so a failed upgrade never leaves you worse than before.
#
# ┌───────────────────────────────────────────────────────────────────────────┐
# │  NEVER run `docker compose down -v` on this deployment.                    │
# │  `-v` DELETES the named volumes — your entire database AND all evidence    │
# │  blobs — with no undo. This script only ever uses `up -d --build`.         │
# └───────────────────────────────────────────────────────────────────────────┘
#
# Usage:
#   scripts/upgrade.sh vX.Y.Z [--manifest FILE] [--yes]
#   scripts/upgrade.sh --rollback <backup-timestamp>
#   scripts/upgrade.sh --help
#
#   vX.Y.Z            target release tag (the "v" is optional; 0.9.0 == v0.9.0)
#   --manifest FILE   use a local upgrade-manifest.json instead of fetching it
#                     from the GitHub Release (air-gapped installs)
#   --yes             assume "yes" to the pre-upgrade confirmation (unattended)
#   --rollback <ts>   restore both data stores from the backup set with the
#                     given timestamp (see ./backups/<ts>_*), then rebuild
# =============================================================================
set -euo pipefail

# --- Constants ---------------------------------------------------------------
OSS_REPO="MarkAC007/scf-controls-platform-oss"
HEALTH_URL="http://localhost:8000/health"
HEALTH_TIMEOUT=120            # seconds to wait for /health after start
BACKUPS_DIR="./backups"
COMPOSE_FILE="docker-compose.yml"

# Logical compose volume names (compose maps these to real docker volume names,
# which we DERIVE at runtime rather than hardcoding — see derive_volume_name).
PG_VOL_LOGICAL="postgres_data"
MINIO_VOL_LOGICAL="minio_data"

# --- Colour / logging --------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi

log()     { printf '%s\n' "$*"; }
info()    { printf '%s[upgrade]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
step()    { printf '\n%s==>%s %s%s%s\n' "$C_BOLD" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
warn()    { printf '%s[warn]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
success() { printf '%s[ok]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
# die: print WHY it stopped and WHAT to do, then exit non-zero.
die()     { printf '%s[STOP]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# --- Guard: this script must never emit `down -v` ----------------------------
# Defensive self-check so a future edit cannot silently introduce the footgun.
# (We look for the actual command form, not this comment or the help text.)
_selfguard() {
  local self="${BASH_SOURCE[0]}"
  # Look for an actual `compose down` COMMAND at the start of a line (ignoring
  # comments and the quoted mentions in the help/warn text), and refuse if one
  # carries -v/--volumes. This script only ever uses `up -d --build`.
  if grep -nE '^[[:space:]]*(docker[[:space:]]+)?compose[[:space:]]+down' "$self" \
       | grep -Eq '(-v|--volumes)'; then
    die "internal: upgrade.sh contains a 'compose down -v' command — refusing to run."
  fi
}

# --- Small helpers -----------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }

compose() { docker compose "$@"; }

require_prereqs() {
  have docker || die "docker not found on PATH. Install Docker and retry."
  docker compose version >/dev/null 2>&1 \
    || die "'docker compose' (v2) not available. Install the compose plugin."
  have curl || warn "curl not found — manifest fetch will require --manifest FILE."
  have python3 || die "python3 not found — needed to parse the upgrade manifest."
  [[ -f "$COMPOSE_FILE" ]] \
    || die "no $COMPOSE_FILE here. Run this from the repository root."
  [[ -d .git ]] \
    || die "not a git checkout. This deployment must be a 'git clone' of the repo."
}

# Normalise a version/tag: strip a leading 'v'. "v1.2.3" -> "1.2.3".
strip_v() { local s="$1"; printf '%s' "${s#v}"; }

# Compare two dotted semver cores (ignores pre-release/build metadata).
# Prints: -1 if $1 < $2, 0 if equal, 1 if $1 > $2.
semver_cmp() {
  local a b; a="$(strip_v "$1")"; b="$(strip_v "$2")"
  a="${a%%[-+]*}"; b="${b%%[-+]*}"
  local IFS=.
  # shellcheck disable=SC2206
  local A=($a) B=($b) i
  for i in 0 1 2; do
    local x="${A[i]:-0}" y="${B[i]:-0}"
    # non-numeric segments collapse to 0 for a conservative comparison
    [[ "$x" =~ ^[0-9]+$ ]] || x=0
    [[ "$y" =~ ^[0-9]+$ ]] || y=0
    if (( x > y )); then echo 1; return; fi
    if (( x < y )); then echo -1; return; fi
  done
  echo 0
}
semver_ge() { [[ "$(semver_cmp "$1" "$2")" != "-1" ]]; }
semver_gt() { [[ "$(semver_cmp "$1" "$2")" == "1" ]]; }

# Read a JSON scalar/array from the manifest. Arrays come back JSON-encoded.
manifest_field() {
  python3 - "$MANIFEST_FILE" "$1" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(0)
v = d.get(sys.argv[2], "")
print(v if not isinstance(v, (list, dict)) else json.dumps(v))
PY
}

# Derive the REAL docker volume name for a logical compose volume. Prefer
# `docker compose config` (authoritative, honours explicit `name:` and project
# prefixing); fall back to a naive parse, then to the compose-project prefix.
derive_volume_name() {
  local logical="$1" name=""
  name="$(compose config --format json 2>/dev/null \
    | python3 - "$logical" <<'PY' 2>/dev/null || true
import json, sys
try:
    cfg = json.load(sys.stdin)
except Exception:
    sys.exit(0)
vols = cfg.get("volumes", {}) or {}
v = vols.get(sys.argv[1], {}) or {}
print(v.get("name", ""))
PY
)"
  if [[ -z "$name" ]]; then
    # Fallback: explicit `name:` under the volume block in the compose file.
    name="$(awk -v key="  $logical:" '
      $0 ~ "^"key"$" {found=1; next}
      found && /name:/ {gsub(/.*name: */,""); gsub(/[[:space:]]/,""); print; exit}
      found && /^  [a-zA-Z]/ {exit}
    ' "$COMPOSE_FILE" 2>/dev/null || true)"
  fi
  if [[ -z "$name" ]]; then
    # Last resort: <project>_<logical>, the compose default naming.
    local project; project="$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
    name="${project}_${logical}"
  fi
  printf '%s' "$name"
}

# Derive the Postgres user/db from the compose `postgres` service environment,
# falling back to the known project defaults.
derive_pg() {
  local kind="$1" val=""
  val="$(compose config --format json 2>/dev/null \
    | python3 - "$kind" <<'PY' 2>/dev/null || true
import json, sys
try:
    cfg = json.load(sys.stdin)
except Exception:
    sys.exit(0)
svc = (cfg.get("services", {}) or {}).get("postgres", {}) or {}
env = svc.get("environment", {}) or {}
if isinstance(env, list):
    env = dict(e.split("=", 1) for e in env if "=" in e)
key = "POSTGRES_USER" if sys.argv[1] == "user" else "POSTGRES_DB"
print(env.get(key, "") or "")
PY
)"
  if [[ -z "$val" ]]; then
    [[ "$kind" == "user" ]] && val="cg" || val="cg_scf"
  fi
  printf '%s' "$val"
}

confirm() {
  # $1 = prompt. Honours --yes. Any answer other than y/Y aborts.
  local prompt="$1" reply
  if [[ "${ASSUME_YES:-0}" == "1" ]]; then
    info "--yes given; proceeding: $prompt"
    return 0
  fi
  printf '%s%s%s [y/N] ' "$C_BOLD" "$prompt" "$C_RESET"
  read -r reply || true
  [[ "$reply" == "y" || "$reply" == "Y" ]] || die "Aborted by operator."
}

# =============================================================================
# ROLLBACK  (Phase R) — restore both stores from a backup set, then rebuild.
# Restores Postgres into a FRESH database and swaps it in only after the
# restore proves good, so the live database is never destroyed mid-restore.
# =============================================================================
do_rollback() {
  local ts="$1"
  [[ -n "$ts" ]] || die "usage: upgrade.sh --rollback <backup-timestamp>"

  local pg_dump_file minio_file ref_file
  pg_dump_file="$(ls "${BACKUPS_DIR}/${ts}"_v*.dump 2>/dev/null | head -1 || true)"
  minio_file="$(ls "${BACKUPS_DIR}/${ts}"_v*_minio.tgz 2>/dev/null | head -1 || true)"
  ref_file="${BACKUPS_DIR}/${ts}_ref.txt"

  [[ -n "$pg_dump_file" && -f "$pg_dump_file" ]] \
    || die "no Postgres dump for timestamp '$ts' in $BACKUPS_DIR (expected ${ts}_v*.dump)."
  [[ -f "$ref_file" ]] \
    || die "no git ref record ${ref_file}; cannot know which code to roll back to."

  local pg_user pg_db minio_vol prev_ref
  pg_user="$(derive_pg user)"; pg_db="$(derive_pg db)"
  minio_vol="$(derive_volume_name "$MINIO_VOL_LOGICAL")"
  prev_ref="$(tr -d '[:space:]' < "$ref_file")"
  # Uniquified per-backup name for the set-aside forward state: a plain
  # "${pg_db}_failed" would collide with the leftover of any PREVIOUS rollback
  # and abort this one mid-outage at the rename step.
  local failed_db="${pg_db}_failed_${ts}"

  step "ROLLBACK to backup ${ts} (db=${pg_db}, code ref=${prev_ref})"
  warn "This restores the platform to its pre-upgrade state. Current forward state will be set aside."
  confirm "Proceed with rollback from backup ${ts}?"

  # 0. Verify backup integrity BEFORE touching anything. The MinIO restore path
  #    wipes the live volume before extracting, so a corrupt archive must be
  #    caught HERE — finding out after the wipe would mean total evidence loss.
  step "R0. Verifying backup set ${ts} integrity"
  local sum_file="${BACKUPS_DIR}/${ts}_checksums.sha256"
  if [[ -f "$sum_file" ]] && { have sha256sum || have shasum; }; then
    if ( cd "$BACKUPS_DIR" && sha256sum -c "$(basename "$sum_file")" >/dev/null 2>&1 ) \
       || ( cd "$BACKUPS_DIR" && shasum -a 256 -c "$(basename "$sum_file")" >/dev/null 2>&1 ); then
      success "Backup checksums verified."
    else
      die "backup checksums FAILED verification against ${sum_file}. The backup set may be corrupt or tampered with — refusing to restore from it. Nothing was changed."
    fi
  elif [[ -f "$sum_file" ]]; then
    warn "sha256sum/shasum unavailable on this host; cannot verify backup checksums."
  else
    warn "no checksum file for backup set ${ts}; skipping checksum verification."
  fi
  if [[ -n "$minio_file" && -f "$minio_file" ]]; then
    docker run --rm -v "$(pwd)/${BACKUPS_DIR#./}:/b:ro" alpine \
        tar tzf "/b/$(basename "$minio_file")" >/dev/null \
      || die "MinIO backup tar failed structural validation (tar tzf) — refusing: its restore wipes the live evidence volume first. Nothing was changed."
    success "MinIO backup archive validated."
  fi

  # 1. Stop writers (NEVER -v).
  step "R1. Stopping application services"
  compose stop backend celery-worker celery-beat || true

  # 2. Restore Postgres into a FRESH db, atomically. Live db is untouched.
  step "R2. Restoring Postgres into a fresh database (${pg_db}_restore)"
  compose up -d postgres >/dev/null
  _wait_pg "$pg_user" "$pg_db"
  compose exec -T postgres dropdb -U "$pg_user" --if-exists "${pg_db}_restore" >/dev/null 2>&1 || true
  compose exec -T postgres createdb -U "$pg_user" "${pg_db}_restore" \
    || die "could not create ${pg_db}_restore. Your live database is unchanged."
  if ! compose exec -T postgres pg_restore -U "$pg_user" --single-transaction \
        -d "${pg_db}_restore" < "$pg_dump_file"; then
    compose exec -T postgres dropdb -U "$pg_user" --if-exists "${pg_db}_restore" >/dev/null 2>&1 || true
    die "restore into fresh db FAILED (all-or-nothing). Live database left intact; nothing lost."
  fi
  success "Restore into ${pg_db}_restore succeeded."

  # 3. Swap: live -> _failed, restore -> live. Terminate connections first.
  step "R3. Swapping restored database into place"
  compose exec -T postgres psql -U "$pg_user" -d postgres -v ON_ERROR_STOP=1 -c \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname IN ('${pg_db}','${pg_db}_restore') AND pid <> pg_backend_pid();" \
    >/dev/null 2>&1 || true
  compose exec -T postgres psql -U "$pg_user" -d postgres -v ON_ERROR_STOP=1 -c \
    "ALTER DATABASE ${pg_db} RENAME TO ${failed_db};" \
    || die "could not rename live db to ${failed_db}. Investigate manually; restore db is ${pg_db}_restore."
  compose exec -T postgres psql -U "$pg_user" -d postgres -v ON_ERROR_STOP=1 -c \
    "ALTER DATABASE ${pg_db}_restore RENAME TO ${pg_db};" \
    || die "renamed live to ${failed_db} but could NOT promote restore. Fix manually: rename ${pg_db}_restore -> ${pg_db}."
  success "Database swapped. Previous forward state retained as ${failed_db} for inspection."

  # 4. Restore MinIO evidence volume from the tar (symmetric with Postgres).
  if [[ -n "$minio_file" && -f "$minio_file" ]]; then
    step "R4. Restoring MinIO evidence volume from ${minio_file}"
    compose stop minio || true
    # Wipe must cover dotfiles too: '*' skips them and '..?*' only matches names
    # starting with '..', so without '.[!.]*' a post-backup .minio.sys metadata
    # tree would survive and poison the restored volume. (Archive integrity was
    # already proven in R0, so wiping before extraction is safe.)
    docker run --rm -v "${minio_vol}:/data" -v "$(pwd)/${BACKUPS_DIR#./}:/b" alpine \
      sh -c 'rm -rf /data/* /data/.[!.]* /data/..?* 2>/dev/null; tar xzf "/b/'"$(basename "$minio_file")"'" -C /data' \
      || die "MinIO volume restore failed. DB is rolled back; evidence volume may be inconsistent — investigate before starting."
    success "MinIO evidence volume restored."
  else
    warn "No MinIO backup tar for ${ts}; evidence volume left as-is."
  fi

  # 5. Return code to the pre-upgrade ref and rebuild.
  step "R5. Checking out pre-upgrade code ref ${prev_ref} and rebuilding"
  git checkout "$prev_ref" || die "git checkout ${prev_ref} failed. Restore code manually, then 'compose up -d --build'."
  # Deliberately NO SCF_MIGRATE_ACK here: the restored DB matches this ref's
  # Alembic head, so the migration guard (if this ref has one) permits startup
  # without an ack — and compose bakes env vars into containers at CREATE time,
  # so a temporary "any" would persist and pre-acknowledge every FUTURE
  # migration (git pull + restart would then auto-migrate with no backup).
  compose up -d --build \
    || die "rebuild after rollback failed. DB+evidence are restored; fix the build and 'compose up -d --build'."

  # 6. Verify health.
  step "R6. Verifying the rolled-back deployment is healthy"
  if _wait_health; then
    success "Rollback complete. Deployment is healthy on ref ${prev_ref}."
  else
    warn "Services started but /health did not become ready within ${HEALTH_TIMEOUT}s. Check 'compose logs backend'."
  fi
  info "The previous (failed) database is retained as ${failed_db}. Drop it once you're satisfied."
}

_wait_pg() {
  local user="$1" db="$2" i
  for i in $(seq 1 30); do
    if compose exec -T postgres pg_isready -U "$user" -d "$db" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  warn "postgres did not report ready within 60s; continuing best-effort."
  return 0
}

_wait_health() {
  local i deadline
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while (( $(date +%s) < deadline )); do
    if have curl && curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# =============================================================================
# MAIN UPGRADE FLOW
# =============================================================================
do_upgrade() {
  local target_raw="$1"
  local TARGET TAG
  TARGET="$(strip_v "$target_raw")"
  TAG="v${TARGET}"
  [[ "$TARGET" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+].*)?$ ]] \
    || die "target '$target_raw' is not a semantic version like v0.9.0."

  local INSTALLED
  INSTALLED="$(jq_pkg_version)"
  [[ -n "$INSTALLED" ]] || die "could not read installed version from webclient/package.json."

  # -------------------------------------------------------------------------
  step "Phase 0 — Load and validate the release manifest for ${TAG}"
  # -------------------------------------------------------------------------
  local tmp_manifest="" cleanup_manifest=0
  if [[ -n "${MANIFEST_OPT:-}" ]]; then
    [[ -f "$MANIFEST_OPT" ]] || die "--manifest file not found: $MANIFEST_OPT"
    MANIFEST_FILE="$MANIFEST_OPT"
    info "Using local manifest (air-gap): $MANIFEST_FILE"
  else
    have curl || die "curl unavailable and no --manifest given. Provide --manifest FILE for offline installs."
    tmp_manifest="$(mktemp)"; cleanup_manifest=1
    local url="https://github.com/${OSS_REPO}/releases/download/${TAG}/upgrade-manifest.json"
    info "Fetching manifest: $url"
    if ! curl -fsSL "$url" -o "$tmp_manifest"; then
      # Distinguish "no release at all" from "release exists but manifest missing".
      if curl -fsSL -o /dev/null "https://api.github.com/repos/${OSS_REPO}/releases/tags/${TAG}" 2>/dev/null; then
        die "release ${TAG} exists but its upgrade-manifest.json asset is MISSING. Refusing (fail-closed): a release with no manifest cannot be reasoned about safely. Wait for the asset, or supply --manifest FILE."
      fi
      die "could not fetch manifest for ${TAG} (release not found or GitHub unreachable). Check the tag, your network, or use --manifest FILE."
    fi
    MANIFEST_FILE="$tmp_manifest"
  fi
  # Validate the manifest parses and is for the expected version.
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$MANIFEST_FILE" \
    || die "manifest is not valid JSON: $MANIFEST_FILE"
  local m_version m_min m_breaking m_summary m_range m_stops
  m_version="$(manifest_field version)"
  m_min="$(manifest_field min_upgradable_version)"
  m_breaking="$(manifest_field breaking)"
  m_summary="$(manifest_field summary)"
  m_range="$(manifest_field migration_range)"
  m_stops="$(manifest_field required_stops)"
  [[ -z "$m_version" || "$(strip_v "$m_version")" == "$TARGET" ]] \
    || die "manifest version ($m_version) does not match target ($TARGET). Wrong manifest supplied."
  [[ -n "$m_min" ]] || m_min="0.0.0"
  info "Manifest OK: version=${m_version:-$TARGET} min_upgradable=${m_min} breaking=${m_breaking:-false}"
  [[ -n "$m_summary" ]] && info "Summary: $m_summary"

  # Confirm the tag actually exists (yanked-release guard). Remote first; for
  # air-gap/--manifest, a local tag (fetched from a bundle) is acceptable.
  step "Phase 0 — Verify tag ${TAG} exists"
  if git ls-remote --tags origin "refs/tags/${TAG}" 2>/dev/null | grep -q "refs/tags/${TAG}"; then
    info "Tag ${TAG} found on origin."
  elif git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null 2>&1; then
    info "Tag ${TAG} present locally (offline/bundle)."
  else
    die "tag ${TAG} not found on origin or locally. If air-gapped, 'git fetch <bundle> refs/tags/*:refs/tags/*' first (the bundle MUST include the tag ref)."
  fi

  # -------------------------------------------------------------------------
  step "Phase 1 — Preflight (nothing is changed in this phase)"
  # -------------------------------------------------------------------------
  info "Installed version: ${INSTALLED}   Target: ${TARGET}"

  # 1a. Working tree clean for TRACKED files (ignore untracked / gitignored).
  local dirty
  dirty="$(git status --porcelain --untracked-files=no || true)"
  if [[ -n "$dirty" ]]; then
    log "$dirty"
    die "you have uncommitted changes to TRACKED files (above). Upgrading would clobber them or hit a merge conflict. Commit, stash, or move local changes to a docker-compose.override.yml / .env overlay, then retry."
  fi
  success "Working tree clean (tracked files)."

  # 1b. Forward-only floor: installed >= manifest.min_upgradable_version.
  if ! semver_ge "$INSTALLED" "$m_min"; then
    local stops_hint=""
    [[ -n "$m_stops" && "$m_stops" != "[]" ]] && stops_hint=" Required intermediate stop(s): ${m_stops}."
    die "this release requires upgrading FROM >= ${m_min}, but you are on ${INSTALLED}. Upgrade to ${m_min} first, then to ${TARGET}.${stops_hint}"
  fi
  success "Version floor satisfied (installed ${INSTALLED} >= min ${m_min})."

  # 1c. No downgrade.
  if ! semver_gt "$TARGET" "$INSTALLED"; then
    die "target ${TARGET} is not newer than installed ${INSTALLED}. Downgrades are not supported (Alembic downgrade is not trusted). To revert, use: upgrade.sh --rollback <backup-ts>."
  fi
  success "Target ${TARGET} is a forward upgrade from ${INSTALLED}."

  # 1d. .env drift vs .env.example (+ manifest.env_added). WARN, non-fatal.
  check_env_drift "$m_range"  # passes range unused; env_added read inside

  # 1e. Floating :latest base images. WARN (air-gap / reproducibility risk).
  if grep -Eq ':latest' "$COMPOSE_FILE"; then
    warn "compose uses floating ':latest' image tag(s):"
    grep -nE 'image:.*:latest' "$COMPOSE_FILE" | sed 's/^/    /' >&2 || true
    warn "A 'compose up --build' re-resolves these; an upstream bump can land mid-upgrade or fail to pull when air-gapped. Consider pinning to a digest."
  fi

  # 1f. Disk-space check. Hard-stop only when clearly insufficient.
  check_disk_space

  # 1g. Compose config valid.
  compose config -q || die "'docker compose config' failed — your compose file/.env is invalid. Fix it before upgrading."
  success "docker compose config is valid."

  # Record the currently-running backend image id (to prove a rebuild happened).
  PRE_IMAGE_ID="$(_backend_image_id || true)"
  [[ -n "$PRE_IMAGE_ID" ]] && info "Current backend image: ${PRE_IMAGE_ID}" \
    || warn "backend not currently running; cannot record pre-upgrade image id (image-change check will be skipped)."

  # Big fat reminder before we touch anything.
  echo
  warn "About to upgrade ${INSTALLED} -> ${TARGET}. This stops writers, backs up BOTH data stores, checks out ${TAG}, migrates, and rebuilds."
  [[ "${m_breaking:-false}" == "true" || "${m_breaking:-false}" == "True" ]] \
    && warn "This release is flagged BREAKING. Read the release notes before continuing: https://github.com/${OSS_REPO}/releases/tag/${TAG}"
  confirm "Proceed with the upgrade to ${TARGET}?"

  # -------------------------------------------------------------------------
  step "Phase 2 — Quiesce writers and take a MANDATORY dual-store backup (the gate)"
  # -------------------------------------------------------------------------
  local pg_user pg_db minio_vol
  pg_user="$(derive_pg user)"; pg_db="$(derive_pg db)"
  minio_vol="$(derive_volume_name "$MINIO_VOL_LOGICAL")"
  info "Postgres user/db: ${pg_user}/${pg_db}   MinIO volume: ${minio_vol}"

  mkdir -p "$BACKUPS_DIR"
  local TS; TS="$(date +%Y%m%d_%H%M%S)"
  local pg_dump="${BACKUPS_DIR}/${TS}_v${INSTALLED}.dump"
  local minio_tar="${BACKUPS_DIR}/${TS}_v${INSTALLED}_minio.tgz"
  local ref_file="${BACKUPS_DIR}/${TS}_ref.txt"
  local sum_file="${BACKUPS_DIR}/${TS}_checksums.sha256"

  # 2a. Quiesce writers so the two snapshots are a true point-in-time. Keep
  #     postgres + minio UP (we back them up). NEVER -v.
  info "Stopping backend + celery workers (postgres and minio stay up)..."
  compose stop backend celery-worker celery-beat || true
  compose up -d postgres minio >/dev/null 2>&1 || true
  _wait_pg "$pg_user" "$pg_db"

  # Any failure below restarts services and exits — nothing has changed yet.
  restart_and_fail() {
    warn "Backup phase failed — restarting services; NOTHING was changed."
    # No SCF_MIGRATE_ACK: nothing changed, the DB is still at this code's head,
    # so the guard permits — and compose would bake a temporary ack into the
    # containers permanently (pre-acknowledging future migrations).
    compose up -d >/dev/null 2>&1 || true
    die "$1"
  }

  # 2b. pg_dump INSIDE the container (avoids host/server client mismatch), -Fc.
  info "Backing up Postgres -> ${pg_dump}"
  if ! compose exec -T postgres pg_dump -U "$pg_user" -Fc "$pg_db" > "$pg_dump"; then
    rm -f "$pg_dump"
    restart_and_fail "pg_dump failed. Could not create a database backup, so the upgrade will not proceed."
  fi
  [[ -s "$pg_dump" ]] || restart_and_fail "pg_dump produced an empty file. Aborting."

  # 2c. VALIDATE the dump is loadable before trusting it (a dump you can't list
  #     is not a backup). Stream it back through pg_restore --list — never copy
  #     it into the container, where a multi-GB dump would land on the docker
  #     writable layer and could fill the postgres host filesystem mid-upgrade.
  info "Validating the Postgres dump (pg_restore --list)..."
  if ! compose exec -T postgres pg_restore --list < "$pg_dump" >/dev/null; then
    restart_and_fail "the Postgres dump failed validation (pg_restore --list). Refusing to upgrade on an unverifiable backup."
  fi
  success "Postgres dump validated."

  # 2d. MinIO evidence volume tar — MANDATORY, symmetric with pg_dump.
  info "Backing up MinIO evidence volume -> ${minio_tar}"
  if ! docker run --rm -v "${minio_vol}:/data:ro" -v "$(pwd)/${BACKUPS_DIR#./}:/b" alpine \
        tar czf "/b/$(basename "$minio_tar")" -C /data . ; then
    rm -f "$minio_tar"
    restart_and_fail "MinIO evidence backup failed (volume ${minio_vol}). For a GRC platform the evidence blobs are half the dataset; refusing to upgrade without them."
  fi
  [[ -s "$minio_tar" ]] || restart_and_fail "MinIO backup produced an empty file. Aborting."
  # Validate the archive structurally NOW — rollback wipes the live volume
  # before extracting, so this tar must be provably good before we rely on it
  # (the pg_dump gets the equivalent check via pg_restore --list above).
  info "Validating the MinIO backup archive (tar tzf)..."
  if ! docker run --rm -v "$(pwd)/${BACKUPS_DIR#./}:/b:ro" alpine \
        tar tzf "/b/$(basename "$minio_tar")" >/dev/null; then
    rm -f "$minio_tar"
    restart_and_fail "the MinIO evidence backup failed validation (tar tzf). Refusing to upgrade on an unverifiable backup."
  fi
  success "MinIO evidence volume backed up and validated."

  # 2e. Record the current git ref (rollback target) + checksums, make immutable.
  git rev-parse HEAD > "$ref_file"
  ( cd "$BACKUPS_DIR" && sha256sum "$(basename "$pg_dump")" "$(basename "$minio_tar")" > "$(basename "$sum_file")" ) \
    || ( cd "$BACKUPS_DIR" && shasum -a 256 "$(basename "$pg_dump")" "$(basename "$minio_tar")" > "$(basename "$sum_file")" ) \
    || warn "could not compute checksums (sha256sum/shasum missing)."
  chmod a-w "$pg_dump" "$minio_tar" "$sum_file" "$ref_file" 2>/dev/null || true
  success "Backup set ${TS} complete and write-protected:"
  info "  DB:       ${pg_dump}"
  info "  Evidence: ${minio_tar}"
  info "  Ref:      ${ref_file} ($(cat "$ref_file"))"
  info "  Sums:     ${sum_file}"

  # From here on, a failure triggers automatic ATOMIC rollback to this backup.
  local ROLLBACK_TS="$TS"

  # -------------------------------------------------------------------------
  step "Phase 3 — Fetch and checkout ${TAG}"
  # -------------------------------------------------------------------------
  git fetch --tags origin >/dev/null 2>&1 || warn "git fetch --tags failed (offline?); relying on local/bundle tags."
  if ! git checkout "tags/${TAG}"; then
    warn "checkout of ${TAG} failed; restarting services on the current ref."
    # No ack needed (nothing changed) — and a temporary ack would persist in the
    # recreated containers, pre-acknowledging future migrations.
    compose up -d >/dev/null 2>&1 || true
    die "git checkout tags/${TAG} failed. No changes applied; services restarted."
  fi
  success "Checked out ${TAG}."

  # -------------------------------------------------------------------------
  step "Phase 4 — Build, migrate as a one-shot, then start"
  # -------------------------------------------------------------------------
  # Shared build contract: bake the running-code identity into the image.
  export BUILD_STAMP; BUILD_STAMP="$(git rev-parse --short HEAD)"
  export MIN_UPGRADABLE_VERSION
  if [[ -f RELEASE_META.yml ]]; then
    MIN_UPGRADABLE_VERSION="$(read_yaml_scalar RELEASE_META.yml min_upgradable_version)"
  fi
  [[ -n "${MIN_UPGRADABLE_VERSION:-}" ]] || MIN_UPGRADABLE_VERSION="$m_min"
  info "BUILD_STAMP=${BUILD_STAMP}  MIN_UPGRADABLE_VERSION=${MIN_UPGRADABLE_VERSION}"

  info "Building backend image..."
  if ! compose build backend; then
    rollback_after_failure "$ROLLBACK_TS" "backend image build failed."
  fi

  # Migrate ALONE (workers still stopped) so no new-code worker races the schema.
  # SCF_MIGRATE_ACK acks the backend migration guard for this target version.
  info "Running database migrations (one-shot: alembic upgrade head)..."
  if ! compose run --rm -e SCF_MIGRATE_ACK="${TARGET}" backend alembic upgrade head; then
    rollback_after_failure "$ROLLBACK_TS" "alembic migration failed."
  fi
  success "Migrations applied."

  info "Starting the full stack (up -d --build)..."
  # Deliberately NO SCF_MIGRATE_ACK: the one-shot above already migrated the DB
  # to head, so the guard permits startup ack-free. Compose bakes env vars into
  # containers at CREATE time — an ack here would persist and pre-acknowledge a
  # future same-version migration (e.g. a hotfix arriving via the bind mount),
  # letting it auto-run with no backup.
  if ! compose up -d --build; then
    rollback_after_failure "$ROLLBACK_TS" "'compose up -d --build' failed."
  fi

  # -------------------------------------------------------------------------
  step "Phase 5 — Verify the ACTUALLY-RUNNING code"
  # -------------------------------------------------------------------------
  # 5a. Health.
  info "Waiting for backend /health (timeout ${HEALTH_TIMEOUT}s)..."
  if ! _wait_health; then
    rollback_after_failure "$ROLLBACK_TS" "backend did not become healthy within ${HEALTH_TIMEOUT}s."
  fi
  success "Backend is healthy."

  # 5b. Migration actually ran: alembic current == alembic heads (authoritative,
  #     independent of manifest naming). If a manifest head stem is provided we
  #     log it for cross-reference but don't gate on the naming mismatch.
  local cur head
  # NOTE: this repo uses short MNEMONIC alembic revision ids (e.g. uv3w4x5y6z7a),
  # not hex hashes — match the full alphanumeric token, not [0-9a-f].
  cur="$(compose exec -T backend alembic current 2>/dev/null | grep -Eo '^[A-Za-z0-9_]+' | head -1 || true)"
  head="$(compose exec -T backend alembic heads 2>/dev/null | grep -Eo '^[A-Za-z0-9_]+' | head -1 || true)"
  if [[ -z "$cur" || -z "$head" ]]; then
    rollback_after_failure "$ROLLBACK_TS" "could not read alembic current/heads from the running backend (migration state unverifiable)."
  fi
  if [[ "$cur" != "$head" ]]; then
    rollback_after_failure "$ROLLBACK_TS" "alembic current ($cur) != head ($head): the database is not at the code's head revision."
  fi
  success "Database is at Alembic head (${cur})."
  if [[ -n "$m_range" && "$m_range" != "[]" ]]; then
    info "Manifest migration_range head (for reference): $(python3 -c 'import json,sys; a=json.loads(sys.argv[1]); print(a[-1] if a else "")' "$m_range" 2>/dev/null || true)"
  fi

  # 5c. Running-code identity: read the image-baked build_info.json (NOT the
  #     bind-mounted package.json, which git checkout already updated). The file
  #     lives at container root /build_info.json — /app is shadowed by the
  #     ./backend:/app bind mount — with /app/build_info.json kept as a fallback.
  local bi_json bi_version bi_stamp
  bi_json="$(compose exec -T backend sh -c 'cat /build_info.json 2>/dev/null || cat /app/build_info.json 2>/dev/null' 2>/dev/null || true)"
  bi_version="$(printf '%s' "$bi_json" | python3 -c 'import json,sys;
try:
    print(json.load(sys.stdin).get("version",""))
except Exception:
    pass' 2>/dev/null || true)"
  bi_stamp="$(printf '%s' "$bi_json" | python3 -c 'import json,sys;
try:
    print(json.load(sys.stdin).get("build_stamp",""))
except Exception:
    pass' 2>/dev/null || true)"
  if [[ -z "$bi_version" && -z "$bi_stamp" ]]; then
    warn "backend image has no /build_info.json (or /app fallback) — cannot verify the baked build identity."
    warn "(Older images predate the build stamp. Falling back to health + alembic checks only.)"
  else
    [[ "$(strip_v "$bi_version")" == "$TARGET" ]] \
      || rollback_after_failure "$ROLLBACK_TS" "running image reports version '${bi_version}', expected '${TARGET}'. The new code is not running (stale image?)."
    [[ -z "$bi_stamp" || "$bi_stamp" == "$BUILD_STAMP" ]] \
      || rollback_after_failure "$ROLLBACK_TS" "running image build_stamp '${bi_stamp}' != expected '${BUILD_STAMP}'. A stale image is running."
    success "Running image identity verified (version=${bi_version}, build_stamp=${bi_stamp})."
  fi

  # 5d. Image id changed vs pre-upgrade (proves no silently-cached stale image).
  local post_id; post_id="$(_backend_image_id || true)"
  if [[ -n "$PRE_IMAGE_ID" && -n "$post_id" ]]; then
    if [[ "$PRE_IMAGE_ID" == "$post_id" ]]; then
      rollback_after_failure "$ROLLBACK_TS" "backend image id did not change ($post_id) — the rebuild did not take effect."
    fi
    success "Backend image id changed (${PRE_IMAGE_ID} -> ${post_id})."
  else
    warn "Could not compare backend image ids (no pre-upgrade id recorded); relying on version/stamp checks."
  fi

  # -------------------------------------------------------------------------
  step "Phase 6 — Success"
  # -------------------------------------------------------------------------
  echo
  success "Upgrade complete: ${INSTALLED} -> ${TARGET}."
  info "Backups retained (write-protected) under ${BACKUPS_DIR}/:"
  info "  ${pg_dump}"
  info "  ${minio_tar}"
  info "  checksums: ${sum_file}"
  info "Roll back at any time with:  scripts/upgrade.sh --rollback ${ROLLBACK_TS}"
  echo
  warn "Reminder: refresh your browser to load the new UI. And never run 'docker compose down -v' — it deletes your database and evidence."

  # (plain `if`, not `[[ ]] &&`: as the last command of this function a false
  # condition would make a successful --manifest upgrade exit nonzero)
  if [[ "$cleanup_manifest" == "1" && -n "$tmp_manifest" ]]; then
    rm -f "$tmp_manifest"
  fi
}

# Automatic rollback wrapper used inside phases 4/5.
rollback_after_failure() {
  local ts="$1" reason="$2"
  warn "UPGRADE FAILED: $reason"
  warn "Initiating automatic rollback to backup ${ts}..."
  ASSUME_YES=1 do_rollback "$ts" \
    || die "AUTOMATIC ROLLBACK ALSO FAILED. Your backups are intact under ${BACKUPS_DIR}/${ts}_*. Restore manually with: scripts/upgrade.sh --rollback ${ts}"
  die "Upgrade failed and was rolled back to the pre-upgrade state. Reason: $reason"
}

# --- helpers used by the main flow ------------------------------------------
jq_pkg_version() {
  # Read webclient/package.json version without requiring jq.
  python3 -c 'import json,sys; print(json.load(open("webclient/package.json")).get("version",""))' 2>/dev/null
}

_backend_image_id() {
  local cid
  cid="$(compose ps -q backend 2>/dev/null | head -1 || true)"
  [[ -n "$cid" ]] || return 1
  docker inspect -f '{{.Image}}' "$cid" 2>/dev/null
}

read_yaml_scalar() {
  # Minimal YAML scalar reader (pyyaml if available, else grep/sed).
  local file="$1" key="$2"
  if python3 -c 'import yaml' >/dev/null 2>&1; then
    python3 - "$file" "$key" <<'PY'
import yaml, sys
try:
    d = yaml.safe_load(open(sys.argv[1])) or {}
except Exception:
    d = {}
v = d.get(sys.argv[2], "")
print("" if v is None else v)
PY
  else
    grep -E "^${key}:" "$file" 2>/dev/null | head -1 | sed -E "s/^${key}:[[:space:]]*//; s/^[\"']//; s/[\"'][[:space:]]*$//" || true
  fi
}

check_env_drift() {
  [[ -f .env && -f .env.example ]] || { info ".env / .env.example not both present; skipping drift check."; return 0; }
  local example_keys env_keys missing k
  example_keys="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env.example | cut -d= -f1 | sort -u)"
  env_keys="$(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' .env | cut -d= -f1 | sort -u)"
  missing="$(comm -23 <(echo "$example_keys") <(echo "$env_keys") || true)"
  # Also surface manifest.env_added explicitly (may not yet be in .env.example).
  local added; added="$(manifest_field env_added)"
  if [[ -n "$added" && "$added" != "[]" ]]; then
    while IFS= read -r k; do
      [[ -n "$k" ]] || continue
      grep -qE "^${k}=" .env 2>/dev/null || missing="${missing}"$'\n'"${k}"
    done < <(python3 -c 'import json,sys;
try:
    [print(x) for x in json.loads(sys.argv[1])]
except Exception:
    pass' "$added" 2>/dev/null)
  fi
  missing="$(printf '%s\n' "$missing" | sed '/^$/d' | sort -u)"
  if [[ -n "$missing" ]]; then
    warn "Your .env is missing keys present in .env.example / this release (non-fatal — many have safe defaults):"
    printf '%s\n' "$missing" | sed 's/^/    /' >&2
    warn "Add the ones you need before or after the upgrade."
  else
    success ".env has all keys from .env.example."
  fi
}

check_disk_space() {
  local pg_vol minio_vol used_mb free_mb need_mb
  pg_vol="$(derive_volume_name "$PG_VOL_LOGICAL")"
  minio_vol="$(derive_volume_name "$MINIO_VOL_LOGICAL")"
  used_mb="$(measure_vol_mb "$pg_vol")"
  local minio_mb; minio_mb="$(measure_vol_mb "$minio_vol")"
  if [[ -z "$used_mb" || -z "$minio_mb" ]]; then
    warn "Could not measure volume sizes (docker unavailable or volumes absent); skipping disk-space hard check."
    return 0
  fi
  mkdir -p "$BACKUPS_DIR"
  # Conservative: need ~2x the combined data size (two backups + rebuild slack).
  need_mb=$(( (used_mb + minio_mb) * 2 ))
  free_mb="$(df -Pm "$BACKUPS_DIR" 2>/dev/null | awk 'NR==2{print $4}')"
  [[ -n "$free_mb" ]] || { warn "Could not read free space for ${BACKUPS_DIR}; skipping hard check."; return 0; }
  info "Data ~$((used_mb + minio_mb)) MB; recommend >= ${need_mb} MB free; have ${free_mb} MB in ${BACKUPS_DIR}."
  if (( free_mb < (used_mb + minio_mb) )); then
    die "insufficient disk space: need at least $((used_mb + minio_mb)) MB for backups, only ${free_mb} MB free at ${BACKUPS_DIR}. Free space or point backups elsewhere."
  fi
  (( free_mb < need_mb )) && warn "Free space is below the recommended 2x headroom (${need_mb} MB). Proceeding, but consider freeing more."
  return 0
}

measure_vol_mb() {
  local vol="$1"
  docker volume inspect "$vol" >/dev/null 2>&1 || { echo ""; return 0; }
  docker run --rm -v "${vol}:/d:ro" alpine sh -c 'du -sm /d 2>/dev/null | cut -f1' 2>/dev/null || echo ""
}

# =============================================================================
# ARG PARSING + DISPATCH
# =============================================================================
print_help() {
  sed -n '2,44p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

main() {
  _selfguard
  ASSUME_YES=0
  MANIFEST_OPT=""
  local target="" rollback_ts="" mode="upgrade"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) print_help; exit 0 ;;
      --yes|-y) ASSUME_YES=1; shift ;;
      --manifest) MANIFEST_OPT="${2:-}"; [[ -n "$MANIFEST_OPT" ]] || die "--manifest requires a FILE argument."; shift 2 ;;
      --rollback) mode="rollback"; rollback_ts="${2:-}"; shift 2 ;;
      -*) die "unknown option: $1 (see --help)." ;;
      *) [[ -z "$target" ]] && target="$1" || die "unexpected extra argument: $1"; shift ;;
    esac
  done

  require_prereqs

  if [[ "$mode" == "rollback" ]]; then
    do_rollback "$rollback_ts"
    exit 0
  fi

  [[ -n "$target" ]] || { print_help; echo; die "no target version given. Usage: scripts/upgrade.sh vX.Y.Z"; }
  do_upgrade "$target"
}

main "$@"
