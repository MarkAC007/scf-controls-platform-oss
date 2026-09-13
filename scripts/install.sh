#!/usr/bin/env bash
# =============================================================================
# install.sh — first-run provisioning for the SCF Controls Platform.
#
# Generates every credential the stack needs, writes them as 0600 files in a
# 0700 directory, and writes a .env that holds NON-SECRET configuration only.
# The operator invents no password: the only things you type are your own email
# address and, on the external-database path, the credentials of a server you
# already run.
#
#   ./scripts/install.sh                        # interactive wizard on 127.0.0.1:8765
#   ./scripts/install.sh --unattended setup.json
#   ./scripts/install.sh --import-env           # adopt an existing .env
#   ./scripts/install.sh --up                   # start the stack afterwards
#   ./scripts/install.sh --no-minio             # bring your own object store
#
# The wizard runs from the backend image as YOUR uid, publishes on loopback
# ONLY, and stops itself the moment provisioning succeeds.
# =============================================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

PORT=8765
IMAGE="ghcr.io/markac007/scf-backend:${SCF_IMAGE_TAG:-latest}"
SECRETS_DIR="${SCF_SECRETS_DIR:-$HOME/.scf/secrets}"
MODE="serve"
UNATTENDED_CONFIG=""
START_STACK=0
CONTAINER_NAME="scf-installer"
# Empty means "the operator did not choose", which the installer reads as the
# bundled MinIO — what every install produced before this flag existed.
STORAGE_TYPE=""


RED=$'\033[31m'; GREEN=$'\033[32m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; OFF=$'\033[0m'
if [ ! -t 1 ]; then RED=""; GREEN=""; BOLD=""; DIM=""; OFF=""; fi

log()  { printf '%s\n' "$*"; }
info() { printf '%s==>%s %s\n' "${BOLD}" "${OFF}" "$*"; }
die()  { printf '%serror:%s %s\n' "${RED}" "${OFF}" "$*" >&2; exit 1; }

usage() {
  sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'USAGE'

Flags:
  --unattended FILE   provision from a JSON config, no browser
  --import-env        move an existing .env's credentials into secret files
  --port N            wizard port on 127.0.0.1 (default 8765)
  --image IMG         backend image to run the wizard from
  --secrets-dir DIR   absolute host path for the secrets directory
  --up                start the stack after provisioning
  --no-minio          do not bundle an object store. The stack starts without
                      MinIO and stores no evidence until you configure your own
                      S3-compatible bucket in Settings. Without this flag the
                      bundled MinIO is provisioned as before.
  -h, --help          this text

Environment:
  SCF_SECRETS_DIR           default secrets directory
  SCF_IMAGE_TAG             tag for the default image
  SCF_INSTALLER_DEV_MOUNT   path to a backend/ directory to bind over /app,
                            so the installer can be tested from a worktree
                            without rebuilding the image
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --unattended) MODE="unattended"; UNATTENDED_CONFIG="${2:-}"; shift 2 ;;
    --import-env) MODE="import-env"; shift ;;
    --port)       PORT="${2:-}"; shift 2 ;;
    --image)      IMAGE="${2:-}"; shift 2 ;;
    --secrets-dir) SECRETS_DIR="${2:-}"; shift 2 ;;
    --up)         START_STACK=1; shift ;;
    --no-minio)   STORAGE_TYPE="none"; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "unknown argument: $1 (try --help)" ;;
  esac
done

command -v docker >/dev/null 2>&1 || die "docker is required but was not found on PATH"
case "${PORT}" in ''|*[!0-9]*) die "--port must be a number" ;; esac
case "${SECRETS_DIR}" in /*) ;; *) die "--secrets-dir must be an absolute path" ;; esac
# The same closed vocabulary the installer package validates against; an empty
# value is "unset" and resolves to bundled_minio there.
case "${STORAGE_TYPE}" in
  ''|none|bundled_minio) ;;
  *) die "unknown storage type: ${STORAGE_TYPE} (expected: bundled_minio, none)" ;;
esac
[ "${MODE}" = "unattended" ] && [ -z "${UNATTENDED_CONFIG}" ] && die "--unattended needs a config file"

# --- refuse to clobber an existing install ----------------------------------
# A bare re-run would generate a fresh DB_PASSWORD against an already-initialised
# postgres volume and a fresh SCF_SECRET_KEY that cannot decrypt stored secrets.
if [ -f "${CHECKOUT_DIR}/.env" ] && [ "${MODE}" != "import-env" ]; then
  die "$(printf '%s' "this checkout already has a .env. Run './scripts/install.sh --import-env' to move its credentials into ${SECRETS_DIR}, or move the file aside first.")"
fi

mkdir -p "${SECRETS_DIR}"
chmod 0700 "${SECRETS_DIR}"

if [ -e "${SECRETS_DIR}/.provisioned" ]; then
  die "${SECRETS_DIR} has already been provisioned (.provisioned exists). Nothing was changed."
fi

# --- the wizard container ----------------------------------------------------
DEV_MOUNT_ARGS=()
if [ -n "${SCF_INSTALLER_DEV_MOUNT:-}" ]; then
  [ -d "${SCF_INSTALLER_DEV_MOUNT}" ] || die "SCF_INSTALLER_DEV_MOUNT is not a directory"
  DEV_MOUNT_ARGS=(-v "${SCF_INSTALLER_DEV_MOUNT}:/app")
  info "development mount: ${SCF_INSTALLER_DEV_MOUNT} -> /app"
fi

COMMON_DOCKER_ARGS=(
  --user "$(id -u):$(id -g)"
  -e HOME=/tmp
  -e "SCF_SECRETS_DIR_HOST=${SECRETS_DIR}"
  -e "SCF_STORAGE_TYPE=${STORAGE_TYPE}"
  -v "${SECRETS_DIR}:/secrets"
  -v "${CHECKOUT_DIR}:/out"
  "${DEV_MOUNT_ARGS[@]+"${DEV_MOUNT_ARGS[@]}"}"
)

run_headless() {
  docker run --rm -i "${COMMON_DOCKER_ARGS[@]}" "${IMAGE}" python -m installer "$@"
}

# --- Linux only: make the files readable by the service uid ------------------
# The backend and celery containers run as uid 1001.  A 0600 file owned by the
# operator is unreadable to them under a plain bind mount; Docker Desktop on
# macOS remaps ownership, so this step is unnecessary there.
apply_linux_group() {
  local kernel
  kernel="$(uname -s)"
  if [ "${kernel}" = "Darwin" ]; then
    info "Darwin detected: Docker Desktop maps file ownership, so no group step is needed."
    return 0
  fi
  if [ "${kernel}" != "Linux" ]; then
    info "${kernel} detected: skipping the Linux group step."
    return 0
  fi
  info "Linux detected: granting gid 1001 read access to ${SECRETS_DIR}"
  log "${DIM}    docker run --rm -v \"${SECRETS_DIR}:/s\" alpine:3 sh -c 'chgrp -R 1001 /s && chmod 0750 /s && chmod 0640 /s/*'${OFF}"
  docker run --rm -v "${SECRETS_DIR}:/s" alpine:3 \
    sh -c 'chgrp -R 1001 /s && chmod 0750 /s && chmod 0640 /s/*'
  log "    files stay owner-only-writable; group 1001 gained read."
}

# --- start the stack ---------------------------------------------------------
compose_field() {  # compose_field SERVICE TEMPLATE  (e.g. '{{.Health}}')
  ( cd "${CHECKOUT_DIR}" && docker compose ps -a --format "$2" "$1" 2>/dev/null ) || true
}

start_stack() {
  info "starting the stack"
  # NOT `up -d --wait`: Compose (observed on v5.4) treats a one-shot service that
  # has already exited 0 (minio-init, keycloak-schema-init, idp-init) as a wait
  # failure and returns 1 while the stack is perfectly healthy.  Poll the
  # backend's own healthcheck instead — that is the thing the next step needs.
  ( cd "${CHECKOUT_DIR}" && docker compose up -d )
  info "waiting for the backend healthcheck (up to 180s)"
  local i state=""
  for i in $(seq 1 90); do
    state="$(compose_field backend '{{.Health}}')"
    [ "${state}" = "healthy" ] && break
    sleep 2
  done
  [ "${state}" = "healthy" ] || \
    die "the backend did not report healthy within 180s (state: '${state:-unknown}'). Inspect: docker compose ps; docker compose logs backend"

  local admin_email=""
  admin_email="$(grep -E '^BOOTSTRAP_ADMIN_EMAIL=' "${CHECKOUT_DIR}/.env" 2>/dev/null | cut -d= -f2- || true)"
  if [ -n "${admin_email}" ]; then
    info "creating the platform administrator for ${admin_email}"
    ( cd "${CHECKOUT_DIR}" && docker compose exec -T backend \
        python -m cli.admin setup --admin-email "${admin_email}" )
  fi

  # COMPOSE_PROFILES now carries `storage` as well, so the value may be `idp`,
  # `storage`, or either order of both. Anchored on the whole field so that a
  # profile merely CONTAINING "idp" is not mistaken for the bundled Keycloak.
  if grep -qE '^COMPOSE_PROFILES=(.*,)?idp(,.*)?$' "${CHECKOUT_DIR}/.env" 2>/dev/null; then
    info "waiting for idp-init to finish (up to 180s)"
    for i in $(seq 1 90); do
      state="$(compose_field idp-init '{{.State}}')"
      [ "${state}" = "exited" ] && break
      sleep 2
    done
    info "one-time Keycloak password (printed once, by Keycloak, to the idp-init log)"
    log ""
    ( cd "${CHECKOUT_DIR}" && docker compose logs idp-init 2>/dev/null ) \
      | grep -iE 'temporary password|password:' || \
      log "  not in the log yet — run: docker compose logs idp-init"
    log ""
    log "  Sign in with it once; Keycloak will require you to set your own password."
  fi
}

case "${MODE}" in
  unattended)
    info "provisioning from ${UNATTENDED_CONFIG}"
    config_name="$(basename -- "${UNATTENDED_CONFIG}")"
    cp -- "${UNATTENDED_CONFIG}" "${CHECKOUT_DIR}/.scf-install-config.json"
    trap 'rm -f "${CHECKOUT_DIR}/.scf-install-config.json"' EXIT
    if [ -n "${SCF_DB_PASSWORD:-}" ]; then
      printf '%s' "${SCF_DB_PASSWORD}" | docker run --rm -i \
        "${COMMON_DOCKER_ARGS[@]}" "${IMAGE}" \
        python -m installer unattended --config /out/.scf-install-config.json --db-password-stdin
    else
      run_headless unattended --config /out/.scf-install-config.json
    fi
    rm -f "${CHECKOUT_DIR}/.scf-install-config.json"
    trap - EXIT
    log "${DIM}    (config copied from ${config_name})${OFF}"
    apply_linux_group
    ;;

  import-env)
    info "moving credentials out of ${CHECKOUT_DIR}/.env and into ${SECRETS_DIR}"
    run_headless import-env
    apply_linux_group
    ;;

  serve)
    # The token is a file, never an argument and never a URL: an argument shows
    # up in `ps` on a shared host and a URL shows up in every access log.
    ( umask 077
      { head -c 64 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' ; } | cut -c1-40 \
        > "${SECRETS_DIR}/.provision-token" )
    chmod 0600 "${SECRETS_DIR}/.provision-token"
    TOKEN="$(cat "${SECRETS_DIR}/.provision-token")"

    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true
    info "starting the setup wizard from ${IMAGE}"
    docker run -d --name "${CONTAINER_NAME}" \
      -p 127.0.0.1:${PORT}:8765 \
      "${COMMON_DOCKER_ARGS[@]}" \
      "${IMAGE}" python -m installer serve --port 8765 --public-port "${PORT}" >/dev/null

    cleanup_wizard() { docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true; }
    trap cleanup_wizard EXIT INT TERM

    log ""
    log "${BOLD}Open this address in your browser:${OFF}"
    log "  http://127.0.0.1:${PORT}/"
    log ""
    log "${BOLD}Setup token (paste it into the first field):${OFF}"
    log "  ${TOKEN}"
    log ""
    log "${DIM}Remote host? Tunnel it: ssh -L ${PORT}:127.0.0.1:${PORT} <user>@<host>${OFF}"
    log "${DIM}The wizard is published on loopback only and stops itself when done.${OFF}"
    log ""
    info "waiting for you to finish (Ctrl-C to abort — nothing has been written yet)"

    # `docker wait` prints the container's exit code; its own status is not it.
    EXIT_CODE="$(docker wait "${CONTAINER_NAME}" 2>/dev/null || echo 1)"
    case "${EXIT_CODE}" in ''|*[!0-9]*) EXIT_CODE=1 ;; esac
    trap - EXIT INT TERM
    docker rm -f "${CONTAINER_NAME}" >/dev/null 2>&1 || true

    if [ ! -e "${SECRETS_DIR}/.provisioned" ]; then
      rm -f "${SECRETS_DIR}/.provision-token"
      # 5 is the wizard's own lockout code (installer/app.py LOCKOUT_EXIT_CODE).
      if [ "${EXIT_CODE}" -eq 5 ]; then
        die "the wizard shut itself down after five wrong setup tokens. Nothing was written. Re-run this script for a fresh token and paste it exactly."
      fi
      die "the wizard stopped before provisioning completed. Nothing was written. Re-run this script for a fresh token."
    fi
    apply_linux_group
    ;;
esac

log ""
printf '%s✓%s provisioning complete\n' "${GREEN}" "${OFF}"
log "  secrets:  ${SECRETS_DIR}  (0700, one 0600 file per credential)"
log "  config:   ${CHECKOUT_DIR}/.env  (non-secret keys only)"
log ""

if [ "${START_STACK}" -eq 1 ]; then
  start_stack
else
  log "Next: ${BOLD}docker compose up -d${OFF}   (or re-run this script with --up)"
fi
