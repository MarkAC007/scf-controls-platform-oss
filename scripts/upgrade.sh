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
#   scripts/upgrade.sh --resume-post-checkout [<backup-timestamp>]
#   scripts/upgrade.sh --help
#
#   vX.Y.Z            target release tag (the "v" is optional; 0.9.0 == v0.9.0)
#   --manifest FILE   use a local upgrade-manifest.json instead of fetching it
#                     from the GitHub Release (air-gapped installs)
#   --yes             assume "yes" to the pre-upgrade confirmation (unattended)
#   --rollback <ts>   restore both data stores from the backup set with the
#                     given timestamp (see ./backups/<ts>_*), then rebuild.
#                     Also restores backups/secrets-<ts>.tar.gz into
#                     SCF_SECRETS_DIR when both are present, after setting the
#                     current credentials aside.
#   --resume-post-checkout [<ts>]
#                     Run ONLY the post-checkout half of an upgrade (build,
#                     migrate, start, verify, plus this release's own .env
#                     fixups) against the code ALREADY checked out here.
#                     upgrade.sh re-execs itself with this flag straight after
#                     it checks the target tag out, so the steps that the
#                     TARGET release adds to this script actually run on the hop
#                     that introduces them. Pass a backup timestamp to finish a
#                     run by hand after the re-exec was skipped.
#
# COMPOSE_FILE is honoured from the environment, then from .env (a colon-
# separated list becomes multiple -f flags), so an install using the file-backed
# credential overlay is upgraded as the overlay, not as the base file alone.
# SCF_SECRET_KEY is generated if absent, before migrations run, and never
# overwritten.
# =============================================================================
set -euo pipefail

# --- Constants ---------------------------------------------------------------
OSS_REPO="MarkAC007/scf-controls-platform-oss"
HEALTH_URL="${HEALTH_URL:-http://localhost:8000/health}"
HEALTH_TIMEOUT=120            # seconds to wait for /health after start
BACKUPS_DIR="./backups"

# Compose file set. Resolved in main() by resolve_compose_files(): environment
# first, then a COMPOSE_FILE= line in .env (which is where scripts/install.sh
# records `docker-compose.yml:docker-compose.secrets.yml` when the file-backed
# credential overlay is in use), then the plain base file.
#
# This matters because docker compose reads COMPOSE_FILE from .env for itself,
# so the RUNNING stack is base+overlay while a hardcoded COMPOSE_FILE here would
# have upgrade.sh validate, grep and rebuild the BASE file alone. backup.sh has
# always honoured ${COMPOSE_FILE:-...}; this brings upgrade.sh into line.
# Capture the caller's environment value BEFORE the default below shadows it.
COMPOSE_FILE_ENV="${COMPOSE_FILE:-}"
COMPOSE_FILE="docker-compose.yml"   # colon-joined, for messages and file greps
COMPOSE_FILE_LIST=("docker-compose.yml")   # one element per file
COMPOSE_FILE_ARGS=()                        # `-f a -f b`, EMPTY when unconfigured

# Logical compose volume names (compose maps these to real docker volume names,
# which we DERIVE at runtime rather than hardcoding — see derive_volume_name).
PG_VOL_LOGICAL="postgres_data"
MINIO_VOL_LOGICAL="minio_data"

# --- Upgrade state shared by Phases 0-3 and Phases 4-6 -----------------------
# Phases 4-6 live in their own function (do_upgrade_post_checkout) because a
# separate PROCESS may run them after the Phase 3 re-exec (#979). These are the
# only values that cross that boundary; declared here so `set -u` cannot turn a
# path that never sets one into an obscure unbound-variable failure mid-upgrade.
SELF_SHA256=""          # sha256 of the RUNNING script, taken before any checkout
TARGET=""               # target version, 'v' stripped
TAG=""                  # "v${TARGET}"
INSTALLED=""            # version this install was on BEFORE the checkout
M_MIN="0.0.0"           # manifest.min_upgradable_version
M_RANGE=""              # manifest.migration_range (JSON array, as a string)
MANIFEST_FILE=""        # path to the validated release manifest
MANIFEST_IS_TMP=0       # 1 when MANIFEST_FILE is ours to delete
ROLLBACK_TS=""          # timestamp of the Phase 2 backup set
PRE_REF=""              # pre-upgrade git ref (also backups/<ts>_ref.txt)
PRE_IMAGE_ID=""         # backend image id before the upgrade
PG_DUMP_FILE=""         # backup set members, for the Phase 6 summary
MINIO_TAR_FILE=""
SUM_FILE=""

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

# sha256 of a file, or "" when neither tool is available. Used to decide whether
# the target release actually changed this script (see maybe_reexec_post_checkout).
# An empty answer is NOT treated as "identical" anywhere — absence of a hash
# means "cannot prove they match", which makes the re-exec happen.
file_sha256() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  if have sha256sum; then
    sha256sum "$f" 2>/dev/null | awk '{print $1}'
  elif have shasum; then
    shasum -a 256 "$f" 2>/dev/null | awk '{print $1}'
  fi
}

# Pass the resolved file set explicitly so the `:latest` scan, the `config -q`
# gate, the migration one-shot and every other call see the same files the
# running stack was brought up from. COMPOSE_FILE_ARGS is deliberately EMPTY
# when nothing was configured, so compose keeps its own default discovery and
# still auto-loads docker-compose.override.yml (UPGRADING.md recommends putting
# local hardening there — passing an explicit -f would silently drop it).
compose() { docker compose ${COMPOSE_FILE_ARGS[@]+"${COMPOSE_FILE_ARGS[@]}"} "$@"; }

# Read KEY=value out of a .env WITHOUT sourcing it — a .env legitimately holds
# values that are not valid shell, and sourcing one to read a path would execute
# them. Last definition wins, matching compose.
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

# Absolute host path of the credential directory, or empty on a legacy install.
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
# is unchanged. Byte-identical to the same function in scripts/backup.sh.
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

# Populate COMPOSE_FILE / COMPOSE_FILE_LIST / COMPOSE_FILE_ARGS. Call once,
# from main(), before require_prereqs.
resolve_compose_files() {
  local configured="${COMPOSE_FILE_ENV:-}" f
  [[ -n "$configured" ]] || configured="$(env_file_value COMPOSE_FILE)"
  if [[ -z "$configured" ]]; then
    COMPOSE_FILE="docker-compose.yml"
    COMPOSE_FILE_LIST=("docker-compose.yml")
    COMPOSE_FILE_ARGS=()
    return 0
  fi
  COMPOSE_FILE="$configured"
  COMPOSE_FILE_LIST=()
  COMPOSE_FILE_ARGS=()
  local -a parts=()
  IFS=':' read -r -a parts <<< "$configured"
  for f in "${parts[@]}"; do
    [[ -n "$f" ]] || continue
    COMPOSE_FILE_LIST+=("$f")
    COMPOSE_FILE_ARGS+=(-f "$f")
  done
  if (( ${#COMPOSE_FILE_LIST[@]} == 0 )); then
    COMPOSE_FILE="docker-compose.yml"
    COMPOSE_FILE_LIST=("docker-compose.yml")
    COMPOSE_FILE_ARGS=()
  fi
}

# --- Host-side group on the catalogue directory (Linux only) -----------------
# Parity with `apply_linux_group` in scripts/install.sh:166. The installer
# chgrp's webclient/public/data to ${SCF_APP_GID} and sets mode 2775, because
# that host directory is where the in-app catalogue import writes its JSON.
# Without it the import dies with `PermissionError: /app/data/json/...` and the
# operator cannot get past first login -- OSS #98, #99.
#
# upgrade.sh did none of this, and Phase 3 checks out a new tree: a directory
# (or a file the release adds under it) can come back carrying the operator's
# own group, which turns a correct install into the #98/#99 failure at the
# first import AFTER the upgrade. Re-asserting it here closes that gap.
#
# Only the data-directory half of apply_linux_group is reproduced. The secrets
# directory is not touched by a git checkout, and .env already carries
# SCF_APP_GID, so neither the secrets chgrp nor write_env_app_gid belongs here.
#
# It is a chgrp, never a chown: the operator stays the owner of their checkout.
# Failure is a warning, not a rollback -- this is host-side file hygiene, and a
# migrated, healthy database must not be reverted because a chgrp did not take.

# Host path, relative to the checkout root this script already runs from
# (require_prereqs asserts `.git` is here).
CATALOG_DATA_DIR="webclient/public/data"

# The gid the compose files run the backend as and hand to `group_add`. Read
# from .env, where scripts/install.sh records it (`write_env_app_gid`), falling
# back to the same built-in default the compose files and the installer use.
catalog_app_gid() {
  local gid
  gid="$(env_file_value SCF_APP_GID)"
  [[ "$gid" =~ ^[0-9]+$ ]] || gid=1001
  printf '%s' "$gid"
}

# Preflight + apply. The PREFLIGHT is half the point: #98/#99 were hard to
# diagnose precisely because a wrongly-grouped directory produced no signal
# until an import failed deep inside the app, so say what was wrong out loud
# even when the fix that follows succeeds.
apply_linux_catalog_group() {
  local kernel
  kernel="$(uname -s)"
  if [[ "$kernel" = "Darwin" ]]; then
    info "Darwin detected: Docker Desktop maps file ownership, so no group step is needed."
    return 0
  fi
  if [[ "$kernel" != "Linux" ]]; then
    info "${kernel} detected: skipping the Linux group step."
    return 0
  fi

  local gid dir
  gid="$(catalog_app_gid)"
  dir="$CATALOG_DATA_DIR"

  if [[ ! -d "$dir" ]]; then
    warn "${dir} does not exist after the checkout; the in-app catalogue import will fail until it does."
    return 0
  fi

  local before
  before="$(stat -c '%g %a' "$dir" 2>/dev/null || true)"
  if [[ "$before" = "${gid} 2775" ]]; then
    info "${dir} is already gid ${gid} mode 2775 — nothing to do."
    return 0
  fi
  warn "${dir} is '${before:-unreadable}', but the catalogue import needs gid ${gid} mode 2775 (a checkout, or an install provisioned by hand, can leave it wrong) — fixing."

  # The chgrp runs inside a container because the operator is usually not a
  # member of gid ${gid} and so cannot chgrp to it directly.
  info "granting gid ${gid} write access to ${dir} (catalogue output)"
  if ! docker run --rm -v "${PWD}/${dir}:/d" alpine:3 \
       sh -c "chgrp -R ${gid} /d && chmod 2775 /d && find /d -type d -exec chmod 2775 {} + && find /d -type f -exec chmod 0664 {} +"; then
    warn "could not set gid ${gid} on ${dir}. The upgrade continues, but the next catalogue import will fail with PermissionError until you run: docker run --rm -v \"${PWD}/${dir}:/d\" alpine:3 sh -c 'chgrp -R ${gid} /d && chmod 2775 /d'"
    return 0
  fi

  local after
  after="$(stat -c '%g %a' "$dir" 2>/dev/null || true)"
  if [[ "$after" = "${gid} 2775" ]]; then
    success "${dir} is gid ${gid} mode 2775 — setgid, so imported JSON keeps group ${gid}."
  else
    warn "${dir} is '${after:-unreadable}' after the group step, expected '${gid} 2775'. The next catalogue import may fail with PermissionError."
  fi
}

# --- SCF_SECRET_KEY -----------------------------------------------------------
# The key that encrypts tier-3 integration credentials. It must exist BEFORE the
# migration one-shot runs: a migration that needed a key nobody has yet would
# hard-fail every operator upgrading from a version that had none.
#
# NOTHING here ever prints the key's value.

# True when a key is resolvable from any tier (env, secrets dir file, .env).
secret_key_present() {
  [[ -n "${SCF_SECRET_KEY:-}" ]] && return 0
  local dir; dir="$(resolve_secrets_dir)"
  if [[ -n "$dir" && -s "${dir}/SCF_SECRET_KEY" ]]; then
    [[ -n "$(tr -d '[:space:]' < "${dir}/SCF_SECRET_KEY" 2>/dev/null || true)" ]] && return 0
  fi
  [[ -n "$(env_file_value SCF_SECRET_KEY)" ]] && return 0
  return 1
}

# The backend image reference compose would run, for the key-generation one-shot.
backend_image_ref() {
  compose config --format json 2>/dev/null \
    | python3 -c 'import json, sys
try:
    cfg = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(((cfg.get("services", {}) or {}).get("backend", {}) or {}).get("image", "") or "")
' 2>/dev/null || true
}

# Print a fresh Fernet key (44 chars). Prefers the backend image so the key is
# minted by the same cryptography build that will consume it; falls back to the
# host python3 with the identical construction (32 random bytes, urlsafe base64
# — which is literally what Fernet.generate_key() does) when docker cannot run
# it. NEVER token_urlsafe: a Fernet key is a fixed 32-byte value, not a nonce.
generate_fernet_key() {
  local image key=""
  image="$(backend_image_ref)"
  if [[ -n "$image" ]]; then
    key="$(docker run --rm --entrypoint python "$image" \
             -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())' \
             2>/dev/null | tr -d '\r\n' || true)"
  fi
  if (( ${#key} != 44 )); then
    key="$(python3 -c 'import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())' 2>/dev/null | tr -d '\r\n' || true)"
  fi
  (( ${#key} == 44 )) || return 1
  printf '%s' "$key"
}

# --- does this install bundle an object store? (#956) ------------------------
# ONE signal, used by ensure_storage_profile, by the upgrade's mandatory backup
# and by scripts/backup.sh: a NON-EMPTY MINIO_ROOT_USER, either in .env or as a
# file in the secrets directory.
#
# Why not COMPOSE_PROFILES, and why not EVIDENCE_STORAGE_BOOTSTRAP: both are
# written only by a Phase-4-or-later installer, and by ensure_storage_profile --
# which runs in Phase 4 of this script, LONG AFTER the Phase 2 backup. An
# install created before the storage profile existed has a live bundled MinIO
# full of evidence and NEITHER key, so keying the backup off either of them
# would silently skip the evidence tar on exactly the installs with the most
# evidence to lose. MINIO_ROOT_USER is correct on all three shapes:
#
#   bundled install      non-empty (.env or secrets file)   -> back the volume up
#   --no-minio install   the installer writes the file EMPTY -> skip, loudly
#   pre-profile install  non-empty in .env or secrets dir    -> back the volume up
#
# The minio entrypoint guard refuses to boot without one, so "no MINIO_ROOT_USER"
# really does mean "this install has never run a bundled MinIO".
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

# --- the evidence storage profile (#956) ------------------------------------
# `minio` and `minio-init` moved behind the `storage` compose profile so that an
# install can choose NOT to bundle an object store. An existing install has no
# such profile in its .env, so without this it would come back from the upgrade
# with no object store at all: every evidence upload and download failing, and
# nothing in the logs saying why, because a service behind an inactive profile
# is simply absent rather than broken.
#
# The test for "this install has a bundled MinIO" is a non-empty MINIO_ROOT_USER
# in .env or a non-empty MINIO_ROOT_USER file in the secrets directory. The minio
# entrypoint guard refuses to boot without one, so an install that has neither
# has not been running MinIO and must not be given the profile.
#
# Also writes EVIDENCE_STORAGE_BOOTSTRAP, which is how the BACKEND learns the
# same fact: COMPOSE_PROFILES is read by the docker CLI on the host and is never
# forwarded into a container.
ensure_storage_profile() {
  local profiles="" union=""
  if ! bundled_object_store; then
    info "No bundled MinIO credential found; leaving COMPOSE_PROFILES alone."
    return 0
  fi

  [[ -f .env ]] || { warn "no .env to add the storage profile to."; return 0; }

  profiles="$(env_file_value COMPOSE_PROFILES)"
  case ",${profiles}," in
    *,storage,*)
      success "COMPOSE_PROFILES already includes 'storage' (left untouched)."
      ;;
    *)
      # Does the KEY exist, whatever its value? Test that FIRST. A
      # present-but-empty `COMPOSE_PROFILES=` is still a line in the operator's
      # .env, and appending a second one leaves a duplicate key -- exactly what
      # the in-place edit below exists to avoid.
      if grep -qE '^[[:space:]]*COMPOSE_PROFILES=' .env 2>/dev/null; then
        # Union, in place. A second appended line would shadow the first and
        # switch off whatever profile the operator already runs.
        union="storage"
        [[ -n "$profiles" ]] && union="${profiles},storage"
        if ! sed -i.upgrade-bak -E "s|^[[:space:]]*COMPOSE_PROFILES=.*$|COMPOSE_PROFILES=${union}|" .env 2>/dev/null; then
          warn "could not add the 'storage' profile to COMPOSE_PROFILES in .env. Add it by hand, or the bundled MinIO will not start."
          return 0
        fi
        rm -f .env.upgrade-bak
      else
        printf 'COMPOSE_PROFILES=storage\n' >> .env
      fi
      success "Added the 'storage' compose profile to .env (the bundled MinIO keeps starting)."
      ;;
  esac

  # Test the KEY, not its value -- the same lesson as the COMPOSE_PROFILES
  # block above (D46 R1). A present-but-empty `EVIDENCE_STORAGE_BOOTSTRAP=`
  # line, which is what a .env.example copied verbatim gives you, has a value
  # that fails the non-empty test; appending a second line then leaves a
  # duplicate key. Compose takes the LAST one so the behaviour happens to be
  # right, but a .env with two definitions of the same key is a thing an
  # operator has to reason about at 3am, and this function exists to avoid it.
  if grep -qE '^[[:space:]]*EVIDENCE_STORAGE_BOOTSTRAP=[[:space:]]*[^[:space:]]' .env 2>/dev/null; then
    info "EVIDENCE_STORAGE_BOOTSTRAP already set in .env; leaving it alone."
  elif grep -qE '^[[:space:]]*EVIDENCE_STORAGE_BOOTSTRAP=' .env 2>/dev/null; then
    # The key exists and is empty. Fill it in place.
    if sed -i.upgrade-bak -E "s|^[[:space:]]*EVIDENCE_STORAGE_BOOTSTRAP=.*$|EVIDENCE_STORAGE_BOOTSTRAP=bundled_minio|" .env 2>/dev/null; then
      rm -f .env.upgrade-bak
      success "Filled the empty EVIDENCE_STORAGE_BOOTSTRAP= line in .env (bundled_minio)."
    else
      warn "could not fill the empty EVIDENCE_STORAGE_BOOTSTRAP= line in .env. Set it to bundled_minio by hand, or the backend will not seed its platform storage configuration."
    fi
  else
    printf 'EVIDENCE_STORAGE_BOOTSTRAP=bundled_minio\n' >> .env
    success "Recorded EVIDENCE_STORAGE_BOOTSTRAP=bundled_minio in .env."
  fi
}

# Generate-if-absent. Existing keys are NEVER overwritten — doing so would make
# every already-encrypted row permanently unreadable.
ensure_secret_key() {
  if secret_key_present; then
    success "SCF_SECRET_KEY is already configured (left untouched)."
    return 0
  fi
  local key dir
  key="$(generate_fernet_key || true)"
  if [[ -z "$key" ]]; then
    warn "could not generate an SCF_SECRET_KEY (neither the backend image nor host python3 would run). The migration does not need one, so the upgrade continues — but tier-3 integration credentials cannot be stored until you set one. Generate it later with:"
    warn "    docker compose run --rm --no-deps --entrypoint python backend -c 'from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())'"
    return 0
  fi
  dir="$(resolve_secrets_dir)"
  if [[ -n "$dir" && -d "$dir" ]]; then
    if [[ -e "${dir}/SCF_SECRET_KEY" ]]; then
      info "SCF_SECRET_KEY file already exists at ${dir}/SCF_SECRET_KEY; leaving it alone."
      return 0
    fi
    if ! ( umask 077 && printf '%s\n' "$key" > "${dir}/SCF_SECRET_KEY" ); then
      warn "could not write ${dir}/SCF_SECRET_KEY. Continuing; tier-3 integration credentials will be unavailable until you create it."
      return 0
    fi
    chmod 0600 "${dir}/SCF_SECRET_KEY" 2>/dev/null || true
    success "Generated SCF_SECRET_KEY at ${dir}/SCF_SECRET_KEY (0600)."
    warn "BACK THIS FILE UP. Integration credentials encrypted with it are unrecoverable if it is lost — scripts/backup.sh includes it from now on."
  else
    if grep -qE '^[[:space:]]*SCF_SECRET_KEY=[[:space:]]*[^[:space:]]' .env 2>/dev/null; then
      info "SCF_SECRET_KEY line already present in .env; leaving it alone."
      return 0
    fi
    if grep -qE '^[[:space:]]*SCF_SECRET_KEY=[[:space:]]*$' .env 2>/dev/null; then
      # A bare `SCF_SECRET_KEY=` (an old .env.example copied verbatim) is "absent":
      # fill it in place rather than appending a second, shadowed line.
      if sed -i.upgrade-bak -E "s|^[[:space:]]*SCF_SECRET_KEY=[[:space:]]*$|SCF_SECRET_KEY=${key}|" .env 2>/dev/null; then
        rm -f .env.upgrade-bak
        success "Filled the empty SCF_SECRET_KEY= line in .env (legacy install — no SCF_SECRETS_DIR configured)."
        warn "BACK UP YOUR .env. Integration credentials encrypted with this key are unrecoverable if it is lost."
        return 0
      fi
      warn "could not fill the empty SCF_SECRET_KEY= line in .env. Continuing; tier-3 integration credentials will be unavailable until you set it."
      return 0
    fi
    if ! printf '\n# Added by scripts/upgrade.sh on %s. Encrypts tier-3 integration\n# credentials. Back it up: encrypted values are unrecoverable without it.\nSCF_SECRET_KEY=%s\n' \
           "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$key" >> .env; then
      warn "could not append SCF_SECRET_KEY to .env. Continuing; tier-3 integration credentials will be unavailable until you add it."
      return 0
    fi
    success "Appended SCF_SECRET_KEY to .env (legacy install — no SCF_SECRETS_DIR configured)."
    warn "BACK UP YOUR .env. Integration credentials encrypted with this key are unrecoverable if it is lost."
  fi
}

# Number of encrypted tier-3 rows, or "unknown" when the table or the server is
# not reachable (which is the normal case on the first upgrade to this version).
integration_secrets_rowcount() {
  local pg_user pg_db out
  pg_user="$(derive_pg user)"; pg_db="$(derive_pg db)"
  out="$(compose exec -T postgres psql -U "$pg_user" -d "$pg_db" -tAc \
          'SELECT count(*) FROM integration_secrets;' 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ "$out" =~ ^[0-9]+$ ]]; then printf '%s' "$out"; else printf 'unknown'; fi
}

# Pre-flight gate. A missing key is only fatal once encrypted rows exist —
# before that there is nothing to lose and upgrade.sh mints one in Phase 4.
check_secret_key() {
  if secret_key_present; then
    success "SCF_SECRET_KEY is configured."
    return 0
  fi
  local n; n="$(integration_secrets_rowcount)"
  if [[ "$n" == "unknown" ]]; then
    warn "SCF_SECRET_KEY is not configured and integration_secrets could not be read (the table does not exist on this version yet, or postgres is not up). One will be generated before migrations run."
    return 0
  fi
  if (( n > 0 )); then
    die "SCF_SECRET_KEY is not configured, but integration_secrets holds ${n} encrypted row(s). Those values decrypt only with the key that wrote them — upgrading without it would leave them permanently unreadable and silently switch off the integrations they configure. Restore the key first (the SCF_SECRET_KEY file in your secrets directory, or the SCF_SECRET_KEY line in .env) from your backup, then retry."
  fi
  warn "SCF_SECRET_KEY is not configured; no encrypted integration rows exist yet, so nothing is at risk. One will be generated before migrations run."
}

require_prereqs() {
  have docker || die "docker not found on PATH. Install Docker and retry."
  docker compose version >/dev/null 2>&1 \
    || die "'docker compose' (v2) not available. Install the compose plugin."
  have curl || warn "curl not found — manifest fetch will require --manifest FILE."
  have python3 || die "python3 not found — needed to parse the upgrade manifest."
  local f
  for f in "${COMPOSE_FILE_LIST[@]}"; do
    [[ -f "$f" ]] \
      || die "compose file '$f' not found here (COMPOSE_FILE=${COMPOSE_FILE}). Run this from the repository root, and check the COMPOSE_FILE line in .env."
  done
  (( ${#COMPOSE_FILE_ARGS[@]} > 0 )) && info "Compose files: ${COMPOSE_FILE}"
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
  # NOTE: use `python3 -c` (not a `python3 - <<'PY'` heredoc) here. A heredoc
  # inside `$(... | ... || true)` triggers a bash 5.2 command-substitution
  # re-parse error ("syntax error near unexpected token `||'") that `bash -n`
  # does not catch — it only bites when the substitution runs. See issue #741.
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
    # Fallback: explicit `name:` under the volume block in the compose file.
    local cf
    for cf in "${COMPOSE_FILE_LIST[@]}"; do
      name="$(awk -v key="  $logical:" '
        $0 ~ "^"key"$" {found=1; next}
        found && /name:/ {gsub(/.*name: */,""); gsub(/[[:space:]]/,""); print; exit}
        found && /^  [a-zA-Z]/ {exit}
      ' "$cf" 2>/dev/null || true)"
      [[ -n "$name" ]] && break
    done
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
  # NOTE: `python3 -c` (not a heredoc) — see the derive_volume_name comment and
  # issue #741 for why a heredoc here breaks under bash 5.2.
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

  # 4b. Restore the credential files. The restored database holds tier-3 rows
  #     encrypted under the SCF_SECRET_KEY of that moment, so rolling the DB back
  #     without rolling the key back leaves them undecryptable.
  local secrets_file="${BACKUPS_DIR}/secrets-${ts}.tar.gz"
  local sdir; sdir="$(resolve_secrets_dir)"
  if [[ -f "$secrets_file" && -n "$sdir" ]]; then
    step "R4b. Restoring credential files from ${secrets_file}"
    tar tzf "$secrets_file" >/dev/null 2>&1 \
      || die "the credential tarball ${secrets_file} failed structural validation (tar tzf) — refusing to extract it over ${sdir}. The database and evidence volume are already restored."
    mkdir -p "$sdir"
    # Set the CURRENT credentials aside first. Extraction overwrites any file
    # the tarball carries, and a credential rotated since the backup (a DB
    # password the live postgres volume still uses, say) must stay recoverable.
    local pre="${BACKUPS_DIR}/secrets-prerollback-$(date +%Y%m%d_%H%M%S).tar.gz"
    if ( umask 077 && tar --exclude='./.provision-token' --exclude='.provision-token' \
            -czf "$pre" -C "$sdir" . ) 2>/dev/null; then
      chmod 0600 "$pre" 2>/dev/null || true
      info "Current credentials set aside first: ${pre}"
    else
      rm -f "$pre"
      warn "could not snapshot the current credential directory before restoring; continuing."
    fi
    # -p keeps the modes recorded in the archive (0600 files, and the 0640/group
    # 1001 form scripts/install.sh applies on Linux), rather than re-imposing a
    # mode that would make the files unreadable to the service containers.
    if ! tar xzpf "$secrets_file" -C "$sdir"; then
      die "credential restore into ${sdir} failed. The database and evidence volume ARE restored; fix ${sdir} before starting the stack."
    fi
    success "Credential files restored into ${sdir}. Files added since the backup were left in place."
  elif [[ -f "$secrets_file" ]]; then
    warn "backup set ${ts} contains ${secrets_file}, but no SCF_SECRETS_DIR is configured in the environment or .env — skipping the credential restore. If the restored database holds encrypted integration credentials, extract that tarball into your credential directory by hand before starting."
  elif [[ -n "$sdir" ]]; then
    warn "no credential tarball in backup set ${ts}; ${sdir} left as-is. If the key has changed since that backup, encrypted integration credentials in the restored database will not decrypt."
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

# Wait for the backend to become healthy. We accept EITHER signal:
#   1. a host-side HTTP probe of $HEALTH_URL (works when the backend port is
#      published), or
#   2. the container's own Docker healthcheck reporting "healthy" (the same
#      HTTP probe, but run inside the network namespace).
# Falling back to (2) matters because UPGRADING.md §6 recommends keeping local
# hardening in docker-compose.override.yml, and the natural override unpublishes
# the backend port (`ports: !reset []`, API reached via the frontend /api proxy).
# Without this fallback a fully healthy upgrade times out and auto-rolls-back —
# see issue #741. $HEALTH_URL is also overridable via the environment.
_wait_health() {
  local deadline
  deadline=$(( $(date +%s) + HEALTH_TIMEOUT ))
  while (( $(date +%s) < deadline )); do
    if have curl && curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    if [[ "$(compose ps --format '{{.Health}}' backend 2>/dev/null)" == "healthy" ]]; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# =============================================================================
# POST-CHECKOUT RE-EXEC  (#979)
# =============================================================================
# THE PROBLEM. bash reads and parses this whole file before it runs a line of
# it. Phase 3 then checks out the target tag — which REPLACES this file on disk
# — but the interpreter carries on executing the copy it already parsed: the
# PRE-upgrade release's. So any step a release ADDS to upgrade.sh is skipped on
# the one hop that introduces it, which is the only hop where it matters.
# v0.33.0's ensure_storage_profile() is the worked example: the upgrade to
# v0.33.0 reported success with zero occurrences of COMPOSE_PROFILES in its log,
# because the function did not exist in the process that ran.
#
# THE FIX. After Phase 3 succeeds, exec the freshly checked-out script and let
# the TARGET release run its own Phases 4-6.
#
# ---------------------------------------------------------------------------
# THE HANDOFF CONTRACT  (stable; an OLD script hands these to a NEW one)
# ---------------------------------------------------------------------------
# The exec'ing script is always the OLDER of the two, so it cannot know the new
# script's internals. The contract is therefore deliberately small, entirely
# environment-based, and every field is OPTIONAL to consume: a future release
# that needs something not listed here must be able to re-derive it (from the
# backup set, the checkout, or .env) rather than expect an old runner to supply
# it. Nothing here is secret; none of it is ever logged as a value that matters.
#
#   SCF_UPGRADE_REEXECED=1      Loop guard. Set by the exec'ing process. When
#                               it is already set, NEVER exec again — continue
#                               in-process. This is the only MANDATORY field.
#   SCF_UPGRADE_TARGET          Target version, 'v' stripped (e.g. 0.33.0).
#                               Falls back to webclient/package.json.
#   SCF_UPGRADE_FROM_VERSION    Version this install was on BEFORE the checkout.
#                               Cannot be re-read after Phase 3 (package.json is
#                               the target's by then); falls back to the backup
#                               set's ${TS}_v<version>.dump filename.
#   SCF_UPGRADE_BACKUP_TS       Timestamp of the Phase 2 backup set. This is
#                               what rollback-on-failure restores from, so the
#                               resumed process REFUSES to start Phase 4 until
#                               it has found the matching dump on disk.
#   SCF_UPGRADE_PRE_REF         Pre-upgrade git ref. Also on disk as
#                               backups/${TS}_ref.txt, which is authoritative.
#   SCF_UPGRADE_PRE_IMAGE_ID    Backend image id before the upgrade, for the
#                               Phase 5d "the rebuild took effect" check. Empty
#                               when the backend was not running.
#   SCF_UPGRADE_MANIFEST        Path to the validated release manifest. The
#                               resumed process RE-READS and RE-VALIDATES it
#                               under its own (possibly stricter) rules — the
#                               old script's verdict is not inherited.
#   SCF_UPGRADE_MANIFEST_TMP    1 when the manifest is a tempfile the resumed
#                               process should delete when it finishes.
#   SCF_UPGRADE_ASSUME_YES      1 when --yes was given (unattended runs).
#   SCF_UPGRADE_COMPOSE_FILE    The RESOLVED colon-joined compose file set, so
#                               the resumed process rebuilds and migrates the
#                               same overlay the running stack came up from.
#   SCF_UPGRADE_SECRETS_DIR     The RESOLVED credential directory, so the
#                               resumed process does not have to re-derive it
#                               from an environment the exec did not inherit.
#
# WHY ENV AND NOT FLAGS: an old script passing a flag a new script does not know
# would be a hard `unknown option` failure mid-upgrade. An unknown SCF_UPGRADE_*
# variable is simply ignored, which is the failure mode we want between releases.
#
# ---------------------------------------------------------------------------
# TRAPS AND exec
# ---------------------------------------------------------------------------
# exec REPLACES the process, so nothing is carried across it — no traps, no
# open cleanup, no shell state. This script installs NO ERR/EXIT traps at all
# (deliberately: failure handling is explicit, via rollback_after_failure at
# each fallible step), so there is nothing to clear before the exec and nothing
# that could double-fire after it. The rollback guarantee travels instead as
# SCF_UPGRADE_BACKUP_TS, and resume_post_checkout proves the backup set is on
# disk BEFORE it enters Phase 4 — so the resumed process can always roll back.
# If a future edit adds a trap, it must be cleared with `trap - ERR EXIT`
# immediately before the exec below.
#
# `shopt -s execfail` is set so that a FAILED exec (unreadable script, no
# interpreter) returns here instead of killing the shell: we then continue
# in-process, which is exactly today's behaviour and no worse, but say so.

# Set by the exec'ing process when it decides NOT to re-exec, so Phase 6 can
# warn that the target release's own upgrade steps did not run.
REEXEC_SKIPPED_REASON=""

# Re-exec the checked-out script for Phases 4-6. Returns (without exec'ing) when
# the re-exec is unnecessary or impossible; never returns when it execs.
maybe_reexec_post_checkout() {
  local new_script="scripts/upgrade.sh" new_sha=""

  if [[ "${SCF_UPGRADE_REEXECED:-}" == "1" ]]; then
    info "Re-exec guard is set (SCF_UPGRADE_REEXECED=1) — continuing in this process. This IS the ${TAG} copy of upgrade.sh."
    return 0
  fi

  if [[ ! -f "$new_script" ]]; then
    REEXEC_SKIPPED_REASON="${TAG} has no ${new_script}"
    warn "${TAG} does not contain ${new_script}; continuing in the pre-upgrade copy of this script."
    return 0
  fi

  new_sha="$(file_sha256 "$new_script")"
  if [[ -n "$SELF_SHA256" && -n "$new_sha" && "$SELF_SHA256" == "$new_sha" ]]; then
    info "${TAG} ships a byte-identical ${new_script} (sha256 ${new_sha:0:12}…) — no re-exec needed."
    return 0
  fi
  if [[ -z "$SELF_SHA256" || -z "$new_sha" ]]; then
    info "Could not hash this script or the checked-out one (no sha256sum/shasum) — re-exec'ing anyway, which is the safe default."
  else
    info "${TAG} changed ${new_script} (sha256 ${SELF_SHA256:0:12}… -> ${new_sha:0:12}…) — re-exec'ing it so ${TAG}'s own upgrade steps run."
  fi

  # Build the handoff. Exported explicitly rather than inherited, so what
  # crosses the exec is exactly the documented contract.
  export SCF_UPGRADE_REEXECED=1
  export SCF_UPGRADE_TARGET="$TARGET"
  export SCF_UPGRADE_FROM_VERSION="$INSTALLED"
  export SCF_UPGRADE_BACKUP_TS="$ROLLBACK_TS"
  export SCF_UPGRADE_PRE_REF="$PRE_REF"
  export SCF_UPGRADE_PRE_IMAGE_ID="${PRE_IMAGE_ID:-}"
  export SCF_UPGRADE_MANIFEST="${MANIFEST_FILE:-}"
  export SCF_UPGRADE_MANIFEST_TMP="${MANIFEST_IS_TMP:-0}"
  export SCF_UPGRADE_ASSUME_YES="${ASSUME_YES:-0}"
  export SCF_UPGRADE_COMPOSE_FILE="$COMPOSE_FILE"
  SCF_UPGRADE_SECRETS_DIR="$(resolve_secrets_dir)"; export SCF_UPGRADE_SECRETS_DIR

  info "exec bash ${new_script} --resume-post-checkout  (backup ${ROLLBACK_TS}, target ${TARGET})"
  # `bash "$f"` rather than `"$f"`: the checked-out file's mode bit is whatever
  # git recorded, and an upgrade must not fail on a lost +x.
  shopt -s execfail
  exec bash "$new_script" --resume-post-checkout
  # Only reached when exec itself failed.
  shopt -u execfail
  REEXEC_SKIPPED_REASON="exec of ${new_script} failed"
  warn "could not exec ${new_script}; continuing in the pre-upgrade copy of this script. ${TAG}'s own upgrade steps will NOT run — see the notice at the end of this run."
  return 0
}

# Entry point for --resume-post-checkout. We are ALREADY on the target tag and
# this file is the target release's copy: rebuild the state Phases 4-6 need,
# re-validate the manifest under THIS script's rules, then run them.
resume_post_checkout() {
  local ts_arg="${1:-}"

  step "Resuming the upgrade in the checked-out release's own scripts/upgrade.sh (post-checkout)"

  ROLLBACK_TS="${SCF_UPGRADE_BACKUP_TS:-$ts_arg}"
  [[ -n "$ROLLBACK_TS" ]] \
    || die "--resume-post-checkout needs the backup timestamp of the run it is finishing. Pass it: scripts/upgrade.sh --resume-post-checkout <backup-timestamp> (see ./backups/)."

  # The backup set is the rollback guarantee. Prove it is on disk BEFORE any of
  # Phase 4 runs — a resumed process that cannot find its backup must not be the
  # one to discover that after a failed migration.
  # These names are generated by Phase 2 from a date stamp and a semver, so they
  # hold no whitespace or shell metacharacters; this is the same lookup
  # do_rollback performs over the same set.
  # shellcheck disable=SC2012
  PG_DUMP_FILE="$(ls "${BACKUPS_DIR}/${ROLLBACK_TS}"_v*.dump 2>/dev/null | head -1 || true)"
  [[ -n "$PG_DUMP_FILE" && -f "$PG_DUMP_FILE" ]] \
    || die "no Postgres dump for backup timestamp '${ROLLBACK_TS}' in ${BACKUPS_DIR} (expected ${ROLLBACK_TS}_v*.dump). Refusing to build or migrate without a backup to roll back to."
  # shellcheck disable=SC2012
  MINIO_TAR_FILE="$(ls "${BACKUPS_DIR}/${ROLLBACK_TS}"_v*_minio.tgz 2>/dev/null | head -1 || true)"
  SUM_FILE="${BACKUPS_DIR}/${ROLLBACK_TS}_checksums.sha256"

  # INSTALLED cannot be read from webclient/package.json any more: the checkout
  # already replaced it with the target's. Take it from the handoff, else from
  # the dump filename the Phase 2 backup encoded it into.
  INSTALLED="${SCF_UPGRADE_FROM_VERSION:-}"
  if [[ -z "$INSTALLED" ]]; then
    INSTALLED="$(basename "$PG_DUMP_FILE")"
    INSTALLED="${INSTALLED#"${ROLLBACK_TS}"_v}"
    INSTALLED="${INSTALLED%.dump}"
  fi
  [[ -n "$INSTALLED" ]] || die "could not determine the pre-upgrade version. Pass it as SCF_UPGRADE_FROM_VERSION."

  TARGET="$(strip_v "${SCF_UPGRADE_TARGET:-}")"
  [[ -n "$TARGET" ]] || TARGET="$(strip_v "$(jq_pkg_version)")"
  [[ -n "$TARGET" ]] \
    || die "could not determine the target version (no SCF_UPGRADE_TARGET and webclient/package.json is unreadable)."
  TAG="v${TARGET}"

  PRE_REF="${SCF_UPGRADE_PRE_REF:-}"
  local ref_file="${BACKUPS_DIR}/${ROLLBACK_TS}_ref.txt"
  # The file on disk is authoritative: do_rollback reads it, not our variable.
  [[ -f "$ref_file" ]] && PRE_REF="$(tr -d '[:space:]' < "$ref_file")"
  [[ -n "$PRE_REF" ]] \
    || die "no pre-upgrade git ref for backup ${ROLLBACK_TS} (expected ${ref_file}); a rollback could not know which code to return to."

  PRE_IMAGE_ID="${SCF_UPGRADE_PRE_IMAGE_ID:-}"
  MANIFEST_IS_TMP="${SCF_UPGRADE_MANIFEST_TMP:-0}"

  info "Resumed:  ${INSTALLED} -> ${TARGET}   backup=${ROLLBACK_TS}   pre-upgrade ref=${PRE_REF}"

  # The checkout really did land on the target. A mismatch here means the
  # working tree is not what the handoff says it is; stop before building.
  local checked_out; checked_out="$(strip_v "$(jq_pkg_version)")"
  if [[ -n "$checked_out" && "$checked_out" != "$TARGET" ]]; then
    warn "webclient/package.json reports ${checked_out} but the target is ${TARGET}. Continuing (a release can lag its own version bump), but verify the checkout if the upgrade misbehaves."
  fi

  # --- Requirement: re-validate the manifest under THIS release's rules ------
  # The manifest gate is otherwise applied by the OLD script alone, so a release
  # that tightens validation could never rely on it for the hop that ships the
  # tightening. Re-read the file rather than trust values passed through.
  MANIFEST_FILE="${SCF_UPGRADE_MANIFEST:-${MANIFEST_OPT:-}}"
  M_MIN=""; M_RANGE=""
  if [[ -n "$MANIFEST_FILE" && -f "$MANIFEST_FILE" ]]; then
    step "Re-validating the release manifest under ${TAG}'s own rules"
    revalidate_manifest
  else
    MANIFEST_FILE=""
    warn "no release manifest available to re-validate (none handed over, and no --manifest given). Continuing: the pre-checkout run validated one, and the remaining phases gate on the RUNNING code, not on the manifest."
  fi
  [[ -n "$M_MIN" ]] || M_MIN="0.0.0"

  do_upgrade_post_checkout
}

# Parse + re-gate the manifest from MANIFEST_FILE, filling M_MIN / M_RANGE.
# Any failure here happens BEFORE the build and migration, so the correct
# recovery is to put the code back — not to restore the database, which has not
# been touched. revert_checkout_and_fail does exactly that.
revalidate_manifest() {
  python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$MANIFEST_FILE" \
    || revert_checkout_and_fail "the release manifest is not valid JSON under ${TAG}'s parser: ${MANIFEST_FILE}"

  local m_version m_breaking
  m_version="$(manifest_field version)"
  M_MIN="$(manifest_field min_upgradable_version)"
  M_RANGE="$(manifest_field migration_range)"
  m_breaking="$(manifest_field breaking)"

  [[ -z "$m_version" || "$(strip_v "$m_version")" == "$TARGET" ]] \
    || revert_checkout_and_fail "manifest version (${m_version}) does not match the checked-out target (${TARGET})."

  [[ -n "$M_MIN" ]] || M_MIN="0.0.0"
  if ! semver_ge "$INSTALLED" "$M_MIN"; then
    revert_checkout_and_fail "${TAG} requires upgrading FROM >= ${M_MIN}, but this install was on ${INSTALLED}. (${TAG}'s own manifest rules reject this hop; the pre-upgrade script's did not.)"
  fi
  success "Manifest re-validated by ${TAG}: version=${m_version:-$TARGET} min_upgradable=${M_MIN} breaking=${m_breaking:-false}."
}

# Abort a resumed run BEFORE anything destructive: put the code back on the
# pre-upgrade ref, restart the stack, and point at the backup that is still
# there. Deliberately NOT a full do_rollback — no build and no migration has
# run at this point, so the database is untouched and restoring it would be an
# outage in service of nothing.
revert_checkout_and_fail() {
  local reason="$1"
  warn "UPGRADE STOPPED before any build or migration: ${reason}"
  warn "Returning the working tree to ${PRE_REF} and restarting services. The database and evidence store were never touched."
  if ! git checkout "$PRE_REF"; then
    die "could not check out ${PRE_REF}. Reason for stopping: ${reason}. Your data is unchanged and backup ${ROLLBACK_TS} is intact; restore the code by hand ('git checkout ${PRE_REF}') then 'docker compose up -d --build'."
  fi
  # No SCF_MIGRATE_ACK: nothing migrated, so the DB is still at this ref's head
  # and the guard permits — and compose bakes env into containers at CREATE
  # time, so a temporary ack would persist and pre-acknowledge future migrations.
  compose up -d >/dev/null 2>&1 || true
  die "Upgrade stopped and the code was returned to ${PRE_REF}. Reason: ${reason}. Backup ${ROLLBACK_TS} is retained under ${BACKUPS_DIR}/."
}

# =============================================================================
# MAIN UPGRADE FLOW
# =============================================================================
# Phases 0-3. TARGET / TAG / INSTALLED / M_MIN / M_RANGE / ROLLBACK_TS /
# PRE_REF / PRE_IMAGE_ID / PG_DUMP_FILE / MINIO_TAR_FILE / SUM_FILE /
# MANIFEST_FILE / MANIFEST_IS_TMP are GLOBAL on purpose: do_upgrade_post_checkout
# reads them, and a resumed process (--resume-post-checkout, after the re-exec)
# populates the same names from the handoff contract instead. Keeping one set of
# names means Phases 4-6 cannot tell which way they were entered.
do_upgrade() {
  local target_raw="$1"
  TARGET="$(strip_v "$target_raw")"
  TAG="v${TARGET}"
  [[ "$TARGET" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-+].*)?$ ]] \
    || die "target '$target_raw' is not a semantic version like v0.9.0."

  INSTALLED="$(jq_pkg_version)"
  [[ -n "$INSTALLED" ]] || die "could not read installed version from webclient/package.json."

  # -------------------------------------------------------------------------
  step "Phase 0 — Load and validate the release manifest for ${TAG}"
  # -------------------------------------------------------------------------
  local tmp_manifest=""
  MANIFEST_IS_TMP=0
  if [[ -n "${MANIFEST_OPT:-}" ]]; then
    [[ -f "$MANIFEST_OPT" ]] || die "--manifest file not found: $MANIFEST_OPT"
    MANIFEST_FILE="$MANIFEST_OPT"
    info "Using local manifest (air-gap): $MANIFEST_FILE"
  else
    have curl || die "curl unavailable and no --manifest given. Provide --manifest FILE for offline installs."
    tmp_manifest="$(mktemp)"; MANIFEST_IS_TMP=1
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
  local m_version m_breaking m_summary m_stops
  m_version="$(manifest_field version)"
  M_MIN="$(manifest_field min_upgradable_version)"
  m_breaking="$(manifest_field breaking)"
  m_summary="$(manifest_field summary)"
  M_RANGE="$(manifest_field migration_range)"
  m_stops="$(manifest_field required_stops)"
  [[ -z "$m_version" || "$(strip_v "$m_version")" == "$TARGET" ]] \
    || die "manifest version ($m_version) does not match target ($TARGET). Wrong manifest supplied."
  [[ -n "$M_MIN" ]] || M_MIN="0.0.0"
  info "Manifest OK: version=${m_version:-$TARGET} min_upgradable=${M_MIN} breaking=${m_breaking:-false}"
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
  if ! semver_ge "$INSTALLED" "$M_MIN"; then
    local stops_hint=""
    [[ -n "$m_stops" && "$m_stops" != "[]" ]] && stops_hint=" Required intermediate stop(s): ${m_stops}."
    die "this release requires upgrading FROM >= ${M_MIN}, but you are on ${INSTALLED}. Upgrade to ${M_MIN} first, then to ${TARGET}.${stops_hint}"
  fi
  success "Version floor satisfied (installed ${INSTALLED} >= min ${M_MIN})."

  # 1c. No downgrade.
  if ! semver_gt "$TARGET" "$INSTALLED"; then
    die "target ${TARGET} is not newer than installed ${INSTALLED}. Downgrades are not supported (Alembic downgrade is not trusted). To revert, use: upgrade.sh --rollback <backup-ts>."
  fi
  success "Target ${TARGET} is a forward upgrade from ${INSTALLED}."

  # 1d. .env drift vs .env.example (+ manifest.env_added). WARN, non-fatal.
  check_env_drift "$M_RANGE"  # passes range unused; env_added read inside

  # 1d-bis. SCF_SECRET_KEY. Fatal ONLY when encrypted rows already exist.
  check_secret_key

  # 1e. Floating :latest base images. WARN (air-gap / reproducibility risk).
  if grep -Eq ':latest' "${COMPOSE_FILE_LIST[@]}"; then
    warn "compose uses floating ':latest' image tag(s):"
    grep -nE 'image:.*:latest' "${COMPOSE_FILE_LIST[@]}" | sed 's/^/    /' >&2 || true
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
  # The evidence volume is only part of this backup when the install actually
  # bundles an object store. On a --no-minio install there is no minio_data in
  # the resolved compose config -- but derive_volume_name would still hand back
  # the FIXED name `cg-scf-minio-data` from the compose file's `name:` key, and
  # `docker run -v cg-scf-minio-data:...` CREATES that volume if it is absent
  # (or attaches ANOTHER install's, the name being global to the host). The tar
  # would then be an 86-byte archive of an empty directory that passes both the
  # non-empty and the `tar tzf` checks: a backup set that claims to hold the
  # evidence and holds nothing. So decide first, derive second.
  local bundled_store=0
  bundled_object_store && bundled_store=1
  if (( bundled_store == 1 )); then
    minio_vol="$(derive_volume_name "$MINIO_VOL_LOGICAL")"
    info "Postgres user/db: ${pg_user}/${pg_db}   MinIO volume: ${minio_vol}"
  else
    minio_vol=""
    info "Postgres user/db: ${pg_user}/${pg_db}   MinIO volume: (none -- this install bundles no object store)"
  fi

  mkdir -p "$BACKUPS_DIR"
  local TS; TS="$(date +%Y%m%d_%H%M%S)"
  local pg_dump="${BACKUPS_DIR}/${TS}_v${INSTALLED}.dump"
  local minio_tar="${BACKUPS_DIR}/${TS}_v${INSTALLED}_minio.tgz"
  local ref_file="${BACKUPS_DIR}/${TS}_ref.txt"
  local sum_file="${BACKUPS_DIR}/${TS}_checksums.sha256"

  # 2a. Quiesce writers so the two snapshots are a true point-in-time. Keep
  #     postgres + minio UP (we back them up). NEVER -v.
  #     NEVER name `minio` on the command line when the storage profile is off:
  #     naming a profiled service ACTIVATES its profile, which would boot a
  #     MinIO with an empty root credential on an install that has none.
  if (( bundled_store == 1 )); then
    info "Stopping backend + celery workers (postgres and minio stay up)..."
  else
    info "Stopping backend + celery workers (postgres stays up)..."
  fi
  compose stop backend celery-worker celery-beat || true
  if (( bundled_store == 1 )); then
    compose up -d postgres minio >/dev/null 2>&1 || true
  else
    compose up -d postgres >/dev/null 2>&1 || true
  fi
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

  # 2d. MinIO evidence volume tar — MANDATORY on an install that bundles an
  #     object store, symmetric with pg_dump; SKIPPED, loudly and with a stated
  #     reason, on an install that does not (--no-minio, or an external store).
  if (( bundled_store == 1 )); then
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
  else
    minio_tar=""
    warn "SKIPPING the evidence backup: no bundled object store on this install (MINIO_ROOT_USER is empty)."
    warn "  Evidence lives in the configured external store and is OUTSIDE this backup set."
    warn "  Backing that store up is the operator's responsibility (bucket versioning, provider snapshots, or your own copy)."
    warn "  This backup set covers the database only; a --rollback from it leaves the external store untouched."
  fi

  # 2e. Record the current git ref (rollback target) + checksums, make immutable.
  git rev-parse HEAD > "$ref_file"
  local sum_targets=("$(basename "$pg_dump")")
  local protect=("$pg_dump")
  if [[ -n "$minio_tar" ]]; then
    sum_targets+=("$(basename "$minio_tar")")
    protect+=("$minio_tar")
  fi
  ( cd "$BACKUPS_DIR" && sha256sum "${sum_targets[@]}" > "$(basename "$sum_file")" ) \
    || ( cd "$BACKUPS_DIR" && shasum -a 256 "${sum_targets[@]}" > "$(basename "$sum_file")" ) \
    || warn "could not compute checksums (sha256sum/shasum missing)."
  chmod a-w "${protect[@]}" "$sum_file" "$ref_file" 2>/dev/null || true
  success "Backup set ${TS} complete and write-protected:"
  info "  DB:       ${pg_dump}"
  if [[ -n "$minio_tar" ]]; then
    info "  Evidence: ${minio_tar}"
  else
    info "  Evidence: (not in this set — no bundled object store; see the warning above)"
  fi
  info "  Ref:      ${ref_file} ($(cat "$ref_file"))"
  info "  Sums:     ${sum_file}"

  # From here on, a failure triggers automatic ATOMIC rollback to this backup.
  # Global, and handed across the re-exec as SCF_UPGRADE_BACKUP_TS.
  ROLLBACK_TS="$TS"
  PRE_REF="$(tr -d '[:space:]' < "$ref_file")"
  PG_DUMP_FILE="$pg_dump"
  MINIO_TAR_FILE="$minio_tar"
  SUM_FILE="$sum_file"

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

  # scripts/upgrade.sh on disk is now ${TAG}'s copy, but THIS process is still
  # running the pre-upgrade one. Hand the rest of the upgrade to the new file so
  # the steps ${TAG} adds to it actually run (#979). This either never returns,
  # or returns having explained why it did not re-exec.
  maybe_reexec_post_checkout

  do_upgrade_post_checkout
}

# Phases 4-6. Entered either straight after Phase 3 (when no re-exec was needed
# or possible) or as the first thing a --resume-post-checkout process does. It
# reads only the globals listed above do_upgrade, never do_upgrade's locals, so
# the two entry paths are indistinguishable from here on.
do_upgrade_post_checkout() {
  local pg_dump="$PG_DUMP_FILE" minio_tar="$MINIO_TAR_FILE" sum_file="$SUM_FILE"

  # Host-side group parity with the installer, before anything is built or
  # started: Phase 3 has just checked out a new tree, and the first catalogue
  # import after startup writes into webclient/public/data.
  #
  # Deliberately here and NOT immediately after the checkout in do_upgrade:
  # this function is the ONLY point both entry paths pass through. A run that
  # re-execs the target release's own upgrade.sh (#979) re-enters here, so a
  # call placed up in do_upgrade would be skipped on the very upgrade that
  # introduces it -- the pre-upgrade copy of this script does not have it.
  apply_linux_catalog_group

  # -------------------------------------------------------------------------
  step "Phase 4 — Build, migrate as a one-shot, then start"
  # -------------------------------------------------------------------------
  # Shared build contract: bake the running-code identity into the image.
  export BUILD_STAMP; BUILD_STAMP="$(git rev-parse --short HEAD)"
  export MIN_UPGRADABLE_VERSION
  if [[ -f RELEASE_META.yml ]]; then
    MIN_UPGRADABLE_VERSION="$(read_yaml_scalar RELEASE_META.yml min_upgradable_version)"
  fi
  [[ -n "${MIN_UPGRADABLE_VERSION:-}" ]] || MIN_UPGRADABLE_VERSION="$M_MIN"
  info "BUILD_STAMP=${BUILD_STAMP}  MIN_UPGRADABLE_VERSION=${MIN_UPGRADABLE_VERSION}"

  info "Building backend image..."
  if ! compose build backend; then
    rollback_after_failure "$ROLLBACK_TS" "backend image build failed."
  fi

  # Migrate ALONE (workers still stopped) so no new-code worker races the schema.
  # SCF_MIGRATE_ACK acks the backend migration guard for this target version.
  # SCF_CDM_DROP_ACK (CDM retirement, migration cdmdrop001) is NOT passed here on
  # purpose: it reaches the run through the service's environment: allow-list
  # from .env, so the operator sets it deliberately (see UPGRADING.md).
  # Mint SCF_SECRET_KEY BEFORE the migration one-shot. Ordering is the whole
  # point: a migration is not allowed to require a secret that did not exist
  # before the upgrade, and the one-shot below is the first process that could
  # need it. The image is built by now, so the generator container is available.
  ensure_secret_key
  # Before the stack comes up, so the bundled MinIO is part of the `up` below
  # and the backend sees the bootstrap signal on its first boot.
  ensure_storage_profile

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

  # 5b/5c. Schema head and running-image identity, checked by the SHARED
  #     verifier in the image (backend/scf_upgrade.py) rather than by parsing
  #     `alembic current` and build_info.json here. The Kubernetes pre-sync Job
  #     runs the same command, so the two deployment paths cannot drift, and the
  #     logic is unit-testable instead of living behind `compose exec -T`.
  #
  #     It asserts: database at this image's Alembic head; baked version ==
  #     TARGET; baked build stamp == the stamp we just built. An image with no
  #     build_info.json warns rather than failing (older images predate it).
  info "Verifying schema head and running-image identity..."
  if ! compose exec -T backend python -m scf_upgrade verify \
        --expect-version "$TARGET" --expect-build-stamp "$BUILD_STAMP"; then
    rollback_after_failure "$ROLLBACK_TS" "post-upgrade verification failed (see the messages above)."
  fi
  success "Schema at head and running image identity verified."
  if [[ -n "$M_RANGE" && "$M_RANGE" != "[]" ]]; then
    info "Manifest migration_range head (for reference): $(python3 -c 'import json,sys; a=json.loads(sys.argv[1]); print(a[-1] if a else "")' "$M_RANGE" 2>/dev/null || true)"
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
  [[ -n "$minio_tar" ]] && info "  ${minio_tar}"
  info "  checksums: ${sum_file}"
  info "Roll back at any time with:  scripts/upgrade.sh --rollback ${ROLLBACK_TS}"
  echo
  warn "Reminder: refresh your browser to load the new UI. And never run 'docker compose down -v' — it deletes your database and evidence."

  warn_if_upgrade_script_drifted

  # (plain `if`, not `[[ ]] &&`: as the last command of this function a false
  # condition would make a successful --manifest upgrade exit nonzero)
  if [[ "${MANIFEST_IS_TMP:-0}" == "1" && -n "${MANIFEST_FILE:-}" ]]; then
    rm -f "$MANIFEST_FILE"
  fi
}

# The issue's "at minimum" mitigation (#979), for the runs where the re-exec did
# NOT happen: say plainly that the target release changed upgrade.sh, that this
# run therefore executed the PRE-upgrade copy, and how to apply the missing
# steps. Note that re-running `upgrade.sh vX.Y.Z` is NOT the remedy — Phase 1c
# refuses it as a non-forward upgrade now that the install IS on the target — so
# point at the post-checkout half, which is what was skipped.
#
# Silent in the two cases where there is nothing to say: this process IS the
# target's copy (it re-exec'd, or resumed), and the target shipped a
# byte-identical script.
warn_if_upgrade_script_drifted() {
  [[ -n "${REEXEC_SKIPPED_REASON:-}" ]] || return 0
  local now_sha; now_sha="$(file_sha256 "scripts/upgrade.sh")"
  [[ -n "$SELF_SHA256" && -n "$now_sha" && "$SELF_SHA256" != "$now_sha" ]] || return 0
  echo
  warn "${TAG} CHANGED scripts/upgrade.sh, and this run could not hand over to it (${REEXEC_SKIPPED_REASON})."
  warn "Everything after the checkout therefore ran ${TAG}'s PREDECESSOR's copy of this script, so any upgrade step ${TAG} adds — a new .env key, a new profile, a new preflight — has NOT been applied."
  warn "Apply them now by running the post-checkout half once, against the code that is already checked out:"
  warn "    scripts/upgrade.sh --resume-post-checkout ${ROLLBACK_TS}"
  warn "(It rebuilds, re-runs 'alembic upgrade head' — a no-op now — restarts the stack and re-verifies. Do NOT re-run 'scripts/upgrade.sh ${TAG}': you are already on ${TARGET}, so it stops as a non-forward upgrade.)"
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
  local minio_mb
  if bundled_object_store; then
    minio_mb="$(measure_vol_mb "$minio_vol")"
  else
    # No bundled object store: there is no evidence volume to size, and none
    # will be tarred. Zero, not "unmeasurable" — otherwise the whole hard check
    # (including the Postgres one) is skipped on every --no-minio install.
    minio_mb=0
  fi
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
  # The range must cover the banner comment block at the top of this file: from
  # its opening `# ===` rule to its closing one. Derive it rather than hardcode
  # it, so editing the banner cannot silently truncate --help (or spill the
  # code below it into the help text).
  local first last
  first="$(grep -n '^# =\{10,\}' "${BASH_SOURCE[0]}" | sed -n '1s/:.*//p')"
  last="$(grep -n '^# =\{10,\}' "${BASH_SOURCE[0]}" | sed -n '2s/:.*//p')"
  [[ -n "$first" && -n "$last" ]] || { first=2; last=54; }
  sed -n "${first},${last}p" "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

main() {
  _selfguard

  # Hash THIS file now, before anything can check out a different copy over it.
  # After Phase 3, "$0" names the TARGET release's bytes, so a hash taken then
  # would compare the new script against itself and always report "identical" —
  # the precise blind spot that hid #979.
  SELF_SHA256="$(file_sha256 "${BASH_SOURCE[0]}")"

  # Apply the re-exec handoff (see "THE HANDOFF CONTRACT" above) BEFORE
  # resolve_compose_files, which reads COMPOSE_FILE_ENV, and before any call
  # that resolves the credential directory.
  if [[ "${SCF_UPGRADE_REEXECED:-}" == "1" ]]; then
    if [[ -n "${SCF_UPGRADE_COMPOSE_FILE:-}" ]]; then
      COMPOSE_FILE_ENV="$SCF_UPGRADE_COMPOSE_FILE"
    fi
    if [[ -n "${SCF_UPGRADE_SECRETS_DIR:-}" ]]; then
      export SCF_SECRETS_DIR="$SCF_UPGRADE_SECRETS_DIR"
    fi
  fi

  # Resolve the compose file set (env > .env > docker-compose.yml) before any
  # compose call or file grep.
  resolve_compose_files
  ASSUME_YES="${SCF_UPGRADE_ASSUME_YES:-0}"
  MANIFEST_OPT=""
  local target="" rollback_ts="" mode="upgrade"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) print_help; exit 0 ;;
      --yes|-y) ASSUME_YES=1; shift ;;
      --manifest) MANIFEST_OPT="${2:-}"; [[ -n "$MANIFEST_OPT" ]] || die "--manifest requires a FILE argument."; shift 2 ;;
      --rollback) mode="rollback"; rollback_ts="${2:-}"; shift 2 ;;
      --resume-post-checkout) mode="resume"; shift ;;
      -*) die "unknown option: $1 (see --help)." ;;
      *) [[ -z "$target" ]] && target="$1" || die "unexpected extra argument: $1"; shift ;;
    esac
  done

  require_prereqs

  if [[ "$mode" == "rollback" ]]; then
    do_rollback "$rollback_ts"
    exit 0
  fi

  # In resume mode the positional argument is the BACKUP TIMESTAMP, not a target
  # version: the target is whatever is already checked out here.
  if [[ "$mode" == "resume" ]]; then
    resume_post_checkout "$target"
    exit 0
  fi

  [[ -n "$target" ]] || { print_help; echo; die "no target version given. Usage: scripts/upgrade.sh vX.Y.Z"; }
  do_upgrade "$target"
}

main "$@"
