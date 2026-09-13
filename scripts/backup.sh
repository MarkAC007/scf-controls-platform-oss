#!/usr/bin/env bash
# =============================================================================
# backup.sh — scheduled, self-contained backup for a self-hosted SCF Controls
# Platform. Takes a dual-store snapshot (Postgres + MinIO evidence volume),
# validates it, checksums it, write-protects it, and prunes old sets.
#
# On an install that bundles NO object store (installed with --no-minio, or
# pointed at your own S3/Azure), the evidence half does not exist here: the set
# is Postgres + credentials, the script says so on every run, and backing up the
# external store is the operator's job. See WHAT IS CAPTURED below.
#
# Run this ON THE DOCKER HOST, from the repository root. It is safe to run on a
# LIVE stack: it does NOT stop any service by default (see --quiesce).
#
#   ./scripts/backup.sh                 # take one backup set, then prune
#   KEEP_N=14 KEEP_DAYS=60 ./scripts/backup.sh
#   ./scripts/backup.sh --quiesce       # pause writers for a strictly PIT snapshot
#
# The output files use the SAME names as upgrade.sh's pre-upgrade backup, so a
# set taken here can be restored with `./scripts/upgrade.sh --rollback <TS>`:
#   <TS>_v<version>.dump          Postgres custom-format dump (whole database)
#   <TS>_v<version>_minio.tgz     MinIO evidence volume tarball — ONLY on an
#                                 install that bundles an object store
#   secrets-<TS>.tar.gz           credential files from SCF_SECRETS_DIR (0600),
#                                 minus .provision-token — omitted on a legacy
#                                 .env install, where the credentials are in .env
#   <TS>_ref.txt                  git ref at backup time (rollback code target)
#   <TS>_checksums.sha256         checksums of the data files
# where <TS> is `date +%Y%m%d_%H%M%S`.
#
# WHAT IS CAPTURED
#   * Postgres: a WHOLE-database `pg_dump -Fc` of the application database. This
#     database also holds Keycloak's tables (compose puts them in a `keycloak`
#     schema INSIDE the same database) and the append-only `audit_log` table, so
#     a whole-DB dump captures identity/realm data and the audit trail with no
#     extra flags. We deliberately pin NO --schema/--table filters (see #873).
#   * MinIO: a tar of the evidence volume. For a GRC platform the evidence blobs
#     — including quarantine/, which is the ONLY copy of virus-flagged uploads —
#     are half the dataset, so on a BUNDLED install this is mandatory and
#     symmetric with the DB dump.
#   * NOT MinIO, on an install that bundles no object store: the evidence lives
#     in the external bucket or container you configured, which this script can
#     neither read nor restore. Back it up there (bucket versioning, provider
#     snapshots, or your own copy). The test is a non-empty MINIO_ROOT_USER in
#     .env or SCF_SECRETS_DIR — see bundled_object_store() below and the same
#     function in upgrade.sh.
#   * NOT the evidence of any organisation that brought its OWN store, on ANY
#     install including a bundled one. Since the bring-your-own-storage work an
#     organisation can point its evidence at its own S3, GCS or MinIO and the
#     bytes never touch this host. The DB dump captures the rows describing
#     those files; nothing here captures the files. The run warns about this
#     every time, with a count when the database can be read — see
#     external_store_orgs() below. This is the failure that looks most like
#     success: a backup set that restores to dangling references.
#
# CONSISTENCY MODEL (why this is safe on a live stack)
#   * `pg_dump -Fc` runs in a single serializable snapshot: the DB backup is
#     transactionally consistent even while the app keeps writing. No downtime.
#   * The MinIO tar copies a live volume, so it is only CRASH-consistent: a blob
#     being written at the instant of the tar could be captured partially. In
#     practice evidence objects are written once and never mutated, so a partial
#     capture can only affect an upload in flight during the backup, never an
#     already-stored blob. If you need a strictly point-in-time pair, pass
#     --quiesce to stop backend + celery for the duration (brief write outage).
# =============================================================================
set -Eeuo pipefail

# --- Config (override via environment) ---------------------------------------
BACKUPS_DIR="${BACKUPS_DIR:-./backups}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
KEEP_N="${KEEP_N:-7}"          # keep at least this many newest sets (0 = ignore)
KEEP_DAYS="${KEEP_DAYS:-30}"   # delete sets older than this many days (0 = ignore)
QUIESCE=0                      # --quiesce sets this to 1

# Host directory holding the 0600 credential files (written by scripts/install.sh).
# Environment wins; otherwise it is read from .env below. Empty means this is a
# legacy install whose credentials still live in .env — see the warning in main().
SCF_SECRETS_DIR="${SCF_SECRETS_DIR:-}"

# Logical compose volume name (mapped to the real docker volume name at
# runtime). Postgres is captured via pg_dump, not a volume tar, so only the
# MinIO evidence volume is tarred here.
MINIO_VOL_LOGICAL="minio_data"

# --- Colour / logging --------------------------------------------------------
if [[ -t 1 ]]; then
  C_RESET=$'\033[0m'; C_RED=$'\033[31m'; C_GREEN=$'\033[32m'
  C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'
else
  C_RESET=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""
fi
info()    { printf '%s[backup]%s %s\n' "$C_BLUE" "$C_RESET" "$*"; }
step()    { printf '\n%s==>%s %s%s%s\n' "$C_BOLD" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
warn()    { printf '%s[warn]%s %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
success() { printf '%s[ok]%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
die()     { printf '%s[STOP]%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# --- Guard: this script must never emit `down -v` ----------------------------
# Defensive self-check so a future edit cannot silently introduce the footgun
# of destroying a live volume from a scheduled job.
_selfguard() {
  local self="${BASH_SOURCE[0]}"
  if grep -nE '^[[:space:]]*(docker[[:space:]]+)?compose[[:space:]]+down' "$self" \
       | grep -Eq '(-v|--volumes)'; then
    die "internal: backup.sh contains a 'compose down -v' command — refusing to run."
  fi
}

# --- Small helpers -----------------------------------------------------------
have() { command -v "$1" >/dev/null 2>&1; }
compose() { docker compose "$@"; }

# Read KEY=value out of a .env WITHOUT sourcing it — a .env legitimately holds
# values that are not valid shell (unquoted #, $, spaces), and sourcing one to
# find a path would execute them. Last definition wins, matching compose.
env_file_value() {
  local key="$1" file="${2:-.env}" val=""
  [[ -f "$file" ]] || return 0
  val="$(grep -E "^[[:space:]]*${key}=" "$file" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  val="${val%$'\r'}"
  # strip one layer of surrounding quotes
  [[ "$val" == \"*\" ]] && val="${val:1:${#val}-2}"
  [[ "$val" == \'*\' ]] && val="${val:1:${#val}-2}"
  printf '%s' "$val"
}

# Absolute host path of the secrets directory, or empty on a legacy .env install.
#
# Falls back to the installer's own default (D46 R2). `scripts/install.sh:27`
# uses `${SCF_SECRETS_DIR:-$HOME/.scf/secrets}`, and it writes SCF_SECRETS_DIR
# into .env -- but an operator who has since tidied that line out of .env, or
# who runs this from a shell without the variable exported, leaves this
# function with nothing while the credential files sit exactly where the
# installer put them. The consequence was not a loud failure: it was
# `bundled_object_store` answering NONE on an install that bundles one, and so
# an evidence backup silently skipped on the install with the most to lose.
#
# The fallback is used ONLY when it actually contains credential files, so an
# empty or absent ~/.scf/secrets still resolves to "" and the legacy .env path
# is unchanged.
resolve_secrets_dir() {
  local d="${SCF_SECRETS_DIR:-}"
  [[ -n "$d" ]] || d="$(env_file_value SCF_SECRETS_DIR)"
  if [[ -z "$d" && -d "${HOME:-}/.scf/secrets" ]]; then
    # Any one installer-written file is enough to identify the directory.
    if [[ -s "${HOME}/.scf/secrets/MINIO_ROOT_USER" || -s "${HOME}/.scf/secrets/SCF_SECRET_KEY" ]]; then
      d="${HOME}/.scf/secrets"
    fi
  fi
  printf '%s' "$d"
}

# --- does this install bundle an object store? (#956) ------------------------
# THE signal for "is there an evidence volume to back up", identical in
# scripts/upgrade.sh (see the long comment there): a NON-EMPTY MINIO_ROOT_USER,
# in .env or as a file in the secrets directory. Not COMPOSE_PROFILES and not
# EVIDENCE_STORAGE_BOOTSTRAP -- an install created before the storage profile
# existed has a live bundled MinIO full of evidence and neither key, and keying
# off them would silently skip its evidence backup. The minio entrypoint guard
# refuses to boot without a root user, so an empty one really does mean this
# install has never run a bundled object store; the --no-minio installer writes
# the file deliberately EMPTY.
bundled_object_store() {
  local dir root_user=""
  root_user="$(env_file_value MINIO_ROOT_USER)"
  if [[ -z "$root_user" ]]; then
    dir="$(resolve_secrets_dir)"
    if [[ -n "$dir" && -s "${dir}/MINIO_ROOT_USER" ]]; then
      root_user="$(tr -d '\r\n' < "${dir}/MINIO_ROOT_USER" 2>/dev/null || true)"
    fi
  fi
  [[ -n "$root_user" ]]
}

# --- how many organisations keep evidence OUTSIDE this backup set? (#956) -----
# ISC 56. Since Phase 1 an organisation can point its evidence at its own S3,
# GCS or MinIO, and the bytes never touch this host. `pg_dump` captures the
# EvidenceFile rows that describe those objects; nothing here captures the
# objects. A backup set that looks complete and restores to dangling references
# is the worst failure this script has, so it has to be said out loud, every
# run, with a number when a number is obtainable.
#
# The count is deliberately cheap and deliberately optional: one SELECT against
# a database we are already talking to, and "unknown" the moment anything is
# not as expected -- the table not existing (a pre-Phase-1 install), postgres
# not up yet, a psql that answers something that is not a number. The warning
# is printed either way; only the precision of it depends on this.
external_store_orgs() {
  local pg_user pg_db out
  pg_user="$(derive_pg user)"; pg_db="$(derive_pg db)"
  out="$(compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -tAc \
          "SELECT count(*) FROM evidence_storage_configs WHERE organization_id IS NOT NULL AND status = 'active';" \
          2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$out" =~ ^[0-9]+$ ]]; then printf '%s' "$out"; else printf 'unknown'; fi
}

require_prereqs() {
  have docker || die "docker not found on PATH. Install Docker and retry."
  docker compose version >/dev/null 2>&1 \
    || die "'docker compose' (v2) not available. Install the compose plugin."
  have python3 || die "python3 not found — needed to derive volume/db names."
  [[ -f "$COMPOSE_FILE" ]] \
    || die "no $COMPOSE_FILE here. Run this from the repository root."
}

# Read webclient/package.json version without requiring jq (matches upgrade.sh).
jq_pkg_version() {
  python3 -c 'import json,sys; print(json.load(open("webclient/package.json")).get("version",""))' 2>/dev/null
}

# Derive the REAL docker volume name for a logical compose volume. Prefer
# `docker compose config` (authoritative); fall back to a naive parse, then to
# the compose-project prefix. Identical logic to upgrade.sh.
derive_volume_name() {
  local logical="$1" name=""
  # NOTE: use `python3 -c` (not a `python3 - <<'PY'` heredoc). A heredoc inside
  # `$(... | ... || true)` triggers a bash 5.2 command-substitution re-parse
  # error that `bash -n` does not catch — it only bites when the substitution
  # runs. See issue #741.
  name="$(compose config --format json 2>/dev/null \
    | python3 -c 'import json, sys
try:
    cfg = json.load(sys.stdin)
except Exception:
    sys.exit(0)
vols = cfg.get("volumes", {}) or {}
v = vols.get(sys.argv[1], {}) or {}
print(v.get("name", ""))
' "$logical" 2>/dev/null || true)"
  if [[ -z "$name" ]]; then
    name="$(awk -v key="  $logical:" '
      $0 ~ "^"key"$" {found=1; next}
      found && /name:/ {gsub(/.*name: */,""); gsub(/[[:space:]]/,""); print; exit}
      found && /^  [a-zA-Z]/ {exit}
    ' "$COMPOSE_FILE" 2>/dev/null || true)"
  fi
  if [[ -z "$name" ]]; then
    local project; project="$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9')"
    name="${project}_${logical}"
  fi
  printf '%s' "$name"
}

# Derive the Postgres user/db from the compose `postgres` service environment,
# falling back to the known project defaults. Identical logic to upgrade.sh.
derive_pg() {
  local kind="$1" val=""
  val="$(compose config --format json 2>/dev/null \
    | python3 -c 'import json, sys
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
' "$kind" 2>/dev/null || true)"
  if [[ -z "$val" ]]; then
    [[ "$kind" == "user" ]] && val="cg" || val="cg_scf"
  fi
  printf '%s' "$val"
}

# Wait for Postgres to accept connections (bounded).
_wait_pg() {
  local user="$1" db="$2" i
  for i in $(seq 1 30); do
    if compose exec -T postgres pg_isready -U "$user" -d "$db" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  die "Postgres did not become ready in time."
}

# --- Prune old backup sets ---------------------------------------------------
# Keep the newest KEEP_N sets regardless of age; among the rest, delete sets
# older than KEEP_DAYS. A "set" is all files sharing a <TS> prefix, anchored on
# the .dump file. Both knobs at 0 disables pruning (keep everything).
prune_backups() {
  if (( KEEP_N <= 0 && KEEP_DAYS <= 0 )); then
    info "Pruning disabled (KEEP_N=0 and KEEP_DAYS=0) — keeping all sets."
    return 0
  fi
  local dumps=() f
  while IFS= read -r f; do
    [[ -n "$f" ]] && dumps+=("$f")
  done < <(ls -1t "${BACKUPS_DIR}"/*_v*.dump 2>/dev/null || true)
  (( ${#dumps[@]} == 0 )) && { info "No backup sets to prune."; return 0; }

  local now i=0 dump base ts mtime age
  now="$(date +%s)"
  for dump in "${dumps[@]}"; do
    i=$((i + 1))
    base="$(basename "$dump")"
    ts="${base%%_v*}"                       # YYYYMMDD_HHMMSS
    # Always keep the newest KEEP_N sets.
    if (( KEEP_N > 0 && i <= KEEP_N )); then continue; fi
    # Beyond KEEP_N: only delete when older than KEEP_DAYS (if age gating is on).
    if (( KEEP_DAYS > 0 )); then
      mtime="$(stat -f %m "$dump" 2>/dev/null || stat -c %Y "$dump" 2>/dev/null || echo "$now")"
      age=$(( (now - mtime) / 86400 ))
      if (( age < KEEP_DAYS )); then continue; fi
    fi
    info "Pruning backup set ${ts}"
    # -f removes the write-protected data files without prompting (the directory
    # is writable, so chmod a-w on the files does not block deletion).
    rm -f "${BACKUPS_DIR}/${ts}"_v*.dump \
          "${BACKUPS_DIR}/${ts}"_v*_minio.tgz \
          "${BACKUPS_DIR}/secrets-${ts}.tar.gz" \
          "${BACKUPS_DIR}/${ts}_ref.txt" \
          "${BACKUPS_DIR}/${ts}_checksums.sha256" 2>/dev/null || true
  done
}

# --- Main --------------------------------------------------------------------
usage() {
  cat <<'EOF'
Usage: scripts/backup.sh [--quiesce] [-h|--help]

  --quiesce   Stop backend + celery workers for the duration so the Postgres and
              MinIO snapshots are a strict point-in-time pair. Causes a brief
              write outage. Omit for a zero-downtime backup (the default).

Environment:
  BACKUPS_DIR       Output directory            (default: ./backups)
  KEEP_N            Keep this many newest sets   (default: 7,  0 = ignore)
  KEEP_DAYS         Delete sets older than N days(default: 30, 0 = ignore)
  SCF_SECRETS_DIR   Credential directory to include in the set. Read from .env
                    when unset. On a legacy install whose credentials are still
                    in .env, no credential tarball is written and .env must be
                    backed up separately.

Schedule it from host cron on the docker host, e.g. daily at 02:30:
  30 2 * * *  cd /opt/scf-controls-platform && KEEP_N=14 KEEP_DAYS=60 ./scripts/backup.sh >> ./backups/backup.log 2>&1

Run it from the repository root (same directory the stack was brought up from),
so `docker compose` resolves the running project. See
https://docs.scfcontrolsplatform.app/admin-guide/backup-and-restore/
EOF
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --quiesce) QUIESCE=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "unknown argument: $1 (see --help)" ;;
    esac
  done

  _selfguard
  require_prereqs

  local pg_user pg_db minio_vol version
  pg_user="$(derive_pg user)"; pg_db="$(derive_pg db)"
  version="$(jq_pkg_version)"; version="${version:-unknown}"

  # Decide BEFORE deriving. On a --no-minio install minio_data is absent from
  # the resolved compose config, but derive_volume_name still returns the FIXED
  # name `cg-scf-minio-data` from the compose file's `name:` key -- and
  # `docker run -v cg-scf-minio-data:...` CREATES that volume when it is absent,
  # or attaches ANOTHER install's, the name being global to this host. The tar
  # would be an 86-byte archive of an empty directory that passes both the
  # non-empty and the `tar tzf` checks: a backup set that claims to hold the
  # evidence and holds nothing.
  local bundled_store=0
  bundled_object_store && bundled_store=1
  if (( bundled_store == 1 )); then
    minio_vol="$(derive_volume_name "$MINIO_VOL_LOGICAL")"
    step "Scheduled backup (db=${pg_db}, minio_vol=${minio_vol}, version=${version})"
    info "Postgres user/db: ${pg_user}/${pg_db}   MinIO volume: ${minio_vol}"
  else
    minio_vol=""
    step "Scheduled backup (db=${pg_db}, minio_vol=none, version=${version})"
    info "Postgres user/db: ${pg_user}/${pg_db}   MinIO volume: (none -- this install bundles no object store)"
  fi

  mkdir -p "$BACKUPS_DIR"
  # Resolve to an ABSOLUTE path for the docker bind-mount. Building the mount as
  # "$(pwd)/$BACKUPS_DIR" only works when BACKUPS_DIR is relative; an absolute
  # override (or a cron using a full path) would mount a bogus directory and the
  # tar would land nowhere. Resolve once, here.
  local BACKUPS_ABS; BACKUPS_ABS="$(cd "$BACKUPS_DIR" && pwd)"
  local TS; TS="$(date +%Y%m%d_%H%M%S)"
  local pg_dump="${BACKUPS_DIR}/${TS}_v${version}.dump"
  local minio_tar="${BACKUPS_DIR}/${TS}_v${version}_minio.tgz"
  local secrets_dir; secrets_dir="$(resolve_secrets_dir)"
  local secrets_tar="${BACKUPS_DIR}/secrets-${TS}.tar.gz"
  local ref_file="${BACKUPS_DIR}/${TS}_ref.txt"
  local sum_file="${BACKUPS_DIR}/${TS}_checksums.sha256"

  # Optional quiesce for a strict point-in-time pair. Always restart writers on
  # exit so a failure mid-backup can never leave the platform paused.
  if (( QUIESCE == 1 )); then
    if (( bundled_store == 1 )); then
      info "Quiescing writers (backend + celery); postgres and minio stay up..."
    else
      info "Quiescing writers (backend + celery); postgres stays up..."
    fi
    compose stop backend celery-worker celery-beat || true
    # shellcheck disable=SC2064
    trap "warn 'restarting writers...'; docker compose up -d backend celery-worker celery-beat >/dev/null 2>&1 || true" EXIT
    # NEVER name `minio` here when the storage profile is off: naming a profiled
    # service on the command line ACTIVATES its profile, so this would boot a
    # MinIO with an empty root credential on an install that has none.
    if (( bundled_store == 1 )); then
      compose up -d postgres minio >/dev/null 2>&1 || true
    else
      compose up -d postgres >/dev/null 2>&1 || true
    fi
  fi
  _wait_pg "$pg_user" "$pg_db"

  # 1. pg_dump INSIDE the container (-Fc), whole DB — captures keycloak schema
  #    and audit_log. No --schema/--table pinning (see #873).
  info "Backing up Postgres -> ${pg_dump}"
  if ! compose exec -T postgres pg_dump -U "$pg_user" -Fc "$pg_db" > "$pg_dump"; then
    rm -f "$pg_dump"
    die "pg_dump failed. No database backup was created."
  fi
  [[ -s "$pg_dump" ]] || { rm -f "$pg_dump"; die "pg_dump produced an empty file. Aborting."; }

  # 2. VALIDATE the dump is loadable (a dump you can't list is not a backup).
  #    Stream it back through pg_restore --list — never copy it into the
  #    container where a multi-GB dump could fill the postgres filesystem.
  info "Validating the Postgres dump (pg_restore --list)..."
  if ! compose exec -T postgres pg_restore --list < "$pg_dump" >/dev/null; then
    die "the Postgres dump failed validation (pg_restore --list). Not trusting it."
  fi
  success "Postgres dump validated."

  # 3. MinIO evidence volume tar — MANDATORY on an install that bundles an
  #    object store, symmetric with pg_dump. Read-only mount of the live volume;
  #    quarantine/ is the only copy of flagged files. SKIPPED, loudly and with a
  #    stated reason, on an install that bundles none.
  if (( bundled_store == 1 )); then
    info "Backing up MinIO evidence volume -> ${minio_tar}"
    if ! docker run --rm -v "${minio_vol}:/data:ro" -v "${BACKUPS_ABS}:/b" alpine \
          tar czf "/b/$(basename "$minio_tar")" -C /data . ; then
      rm -f "$minio_tar"
      die "MinIO evidence backup failed (volume ${minio_vol}). Evidence blobs are half the dataset."
    fi
    [[ -s "$minio_tar" ]] || { rm -f "$minio_tar"; die "MinIO backup produced an empty file. Aborting."; }

    # 4. Validate the archive structurally (tar tzf) — symmetric with the DB check.
    info "Validating the MinIO backup archive (tar tzf)..."
    if ! docker run --rm -v "${BACKUPS_ABS}:/b:ro" alpine \
          tar tzf "/b/$(basename "$minio_tar")" >/dev/null; then
      rm -f "$minio_tar"
      die "the MinIO evidence backup failed validation (tar tzf). Not trusting it."
    fi
    success "MinIO evidence volume backed up and validated."
  else
    minio_tar=""
    warn "SKIPPING the evidence backup: no bundled object store on this install (MINIO_ROOT_USER is empty)."
    warn "  Evidence lives in the configured external store and is OUTSIDE this backup set."
    warn "  Backing that store up is the operator's responsibility (bucket versioning, provider snapshots, or your own copy)."
    warn "  This backup set covers the database and your credential files only."
  fi

  # 3b. ISC 56 -- the per-organisation warning, on EVERY install including a
  #     bundled one. A bundled install is not covered just because its volume
  #     was tarred: any organisation that has brought its own store keeps its
  #     evidence somewhere this script has never heard of, and the tar above
  #     does not contain it. This is #940's second-order finding, now
  #     multiplied per organisation.
  local external_orgs; external_orgs="$(external_store_orgs)"
  if [[ "$external_orgs" == "unknown" ]]; then
    warn "Could not count organisations on their own evidence store (the table does not exist on this version, or postgres is not reachable)."
    warn "  If any organisation has configured its own store under Settings, Evidence storage, its evidence is NOT in this backup set."
  elif (( external_orgs > 0 )); then
    warn "${external_orgs} organisation(s) keep their evidence in a store of their OWN, which is OUTSIDE this backup set."
    warn "  The database dump above captures the rows that describe those files. It does not capture the files."
    warn "  Restoring this set alone would leave those organisations with evidence records pointing at objects nobody here has a copy of."
    warn "  Back each of those stores up where it lives: bucket versioning, provider snapshots, or your own copy."
    warn "  Settings, Evidence storage names the provider and bucket for each organisation."
  else
    info "No organisation is on an evidence store of its own; nothing is outside this set on that account."
  fi

  # 4b. Credential files from SCF_SECRETS_DIR. Without these a restored pg_dump
  #     is inert: the tier-3 integration rows are encrypted under SCF_SECRET_KEY
  #     and every evidence download link is signed with DOWNLOAD_TOKEN_SECRET.
  #     .provision-token is EXCLUDED — it authorises the install wizard and has
  #     no restore value, so it must never be copied into a backup.
  if [[ -z "$secrets_dir" ]]; then
    secrets_tar=""
    warn "No SCF_SECRETS_DIR configured — this is a legacy .env install. Your credentials are in .env; back that file up separately, and store it somewhere the backup set is not."
    warn "A backup set without them cannot decrypt tier-3 integration credentials or re-sign existing evidence download links."
  elif [[ ! -d "$secrets_dir" ]]; then
    secrets_tar=""
    warn "SCF_SECRETS_DIR points at '${secrets_dir}', which is not a directory — no credential tarball in this set. Fix the path, or the restore of this set will not carry your credentials."
  else
    info "Backing up credential files -> ${secrets_tar}"
    if ! ( umask 077 && tar --exclude='./.provision-token' --exclude='.provision-token' \
              -czf "$secrets_tar" -C "$secrets_dir" . ); then
      rm -f "$secrets_tar"
      die "credential backup failed (${secrets_dir}). Refusing to write a backup set that cannot be restored."
    fi
    chmod 0600 "$secrets_tar" 2>/dev/null || true
    if ! tar tzf "$secrets_tar" >/dev/null 2>&1; then
      rm -f "$secrets_tar"
      die "the credential tarball failed validation (tar tzf). Not trusting it."
    fi
    # Belt and braces: prove the provisioning token did not make it in.
    if tar tzf "$secrets_tar" 2>/dev/null | grep -qE '(^|/)\.provision-token$'; then
      rm -f "$secrets_tar"
      die "internal: the credential tarball contains .provision-token — refusing to put a provisioning token in a backup."
    fi
    success "Credential files backed up from ${secrets_dir} and validated (0600, .provision-token excluded)."
  fi

  # 5. Record git ref (rollback code target) + checksums, then write-protect.
  git rev-parse HEAD > "$ref_file" 2>/dev/null || echo "unknown" > "$ref_file"
  local sum_targets=("$(basename "$pg_dump")")
  local protect=("$pg_dump")
  if [[ -n "$minio_tar" ]]; then
    sum_targets+=("$(basename "$minio_tar")")
    protect+=("$minio_tar")
  fi
  [[ -n "$secrets_tar" ]] && sum_targets+=("$(basename "$secrets_tar")")
  ( cd "$BACKUPS_DIR" && sha256sum "${sum_targets[@]}" > "$(basename "$sum_file")" ) \
    || ( cd "$BACKUPS_DIR" && shasum -a 256 "${sum_targets[@]}" > "$(basename "$sum_file")" ) \
    || warn "could not compute checksums (sha256sum/shasum missing)."
  # The credential tarball is deliberately NOT chmod a-w'd: it stays exactly
  # 0600 (owner read/write), the mode the credential files themselves carry.
  chmod a-w "${protect[@]}" "$sum_file" "$ref_file" 2>/dev/null || true

  success "Backup set ${TS} complete and write-protected:"
  info "  DB:       ${pg_dump}"
  if [[ -n "$minio_tar" ]]; then
    info "  Evidence: ${minio_tar}"
  else
    info "  Evidence: (not in this set — no bundled object store; see the warning above)"
  fi
  [[ -n "$secrets_tar" ]] && info "  Secrets:  ${secrets_tar}"
  info "  Ref:      ${ref_file} ($(cat "$ref_file"))"
  info "  Sums:     ${sum_file}"

  step "Pruning old backup sets (KEEP_N=${KEEP_N}, KEEP_DAYS=${KEEP_DAYS})"
  prune_backups
  success "Backup run finished."
}

main "$@"
