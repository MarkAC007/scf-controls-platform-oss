#!/usr/bin/env bash
# =============================================================================
# idp-init — one-shot bootstrap for the bundled Keycloak IdP (compose profile "idp").
#
# Runs inside the keycloak image (kcadm.sh is already present). It is IDEMPOTENT:
# safe to re-run. On every run it (re)sets the confidential client secret from the
# environment so the secret lives ONLY in env, never in scf-realm.json. On the
# FIRST run, if BOOTSTRAP_ADMIN_EMAIL is set and that user does not yet exist, it
# creates the user with a RANDOM temporary password and prints it once to the logs.
# Re-runs never recreate or re-password an existing user.
# =============================================================================
set -euo pipefail

# Fail loudly if the required secrets/creds are empty (the compose service passes
# them through with empty defaults so the no-profile stack stays parseable — the
# real enforcement is here, at runtime, only when the idp profile is started).
: "${KC_ADMIN_USER:?KC_ADMIN_USER is required when using --profile idp}"
: "${KC_ADMIN_PASSWORD:?KC_ADMIN_PASSWORD is required when using --profile idp}"
: "${OIDC_CLIENT_SECRET:?OIDC_CLIENT_SECRET is required when using --profile idp}"

KCADM=/opt/keycloak/bin/kcadm.sh
KC_URL="http://keycloak:8080"
REALM="scf"
CLIENT_ID="scf-platform"

# --- Wait for Keycloak to answer (depends_on service_healthy already gates us,
# --- but this makes the script robust when run standalone). ------------------
echo "idp-init: waiting for Keycloak at ${KC_URL} ..."
until "${KCADM}" config credentials \
  --server "${KC_URL}" \
  --realm master \
  --user "${KC_ADMIN_USER}" \
  --password "${KC_ADMIN_PASSWORD}" >/dev/null 2>&1; do
  echo "idp-init: keycloak not ready yet, retrying in 3s ..."
  sleep 3
done
echo "idp-init: authenticated to Keycloak admin API."

# --- Set the scf-platform client secret from the environment (idempotent). ---
CID="$("${KCADM}" get clients -r "${REALM}" -q clientId="${CLIENT_ID}" --fields id --format csv --noquotes | head -n1)"
if [ -z "${CID}" ]; then
  echo "idp-init: ERROR — client '${CLIENT_ID}' not found in realm '${REALM}'. Was scf-realm.json imported?" >&2
  exit 1
fi
"${KCADM}" update "clients/${CID}" -r "${REALM}" -s "secret=${OIDC_CLIENT_SECRET}"
echo "idp-init: client '${CLIENT_ID}' secret set from OIDC_CLIENT_SECRET."

# --- Optionally create the bootstrap admin user (idempotent). ----------------
if [ -n "${BOOTSTRAP_ADMIN_EMAIL:-}" ]; then
  EXISTING="$("${KCADM}" get users -r "${REALM}" -q email="${BOOTSTRAP_ADMIN_EMAIL}" --fields id --format csv --noquotes | head -n1)"
  if [ -n "${EXISTING}" ]; then
    echo "idp-init: bootstrap user '${BOOTSTRAP_ADMIN_EMAIL}' already exists — leaving it untouched."
  else
    # Random temporary password (no openssl dependency; coreutils base64 is present).
    TEMP_PW="$(head -c 24 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)"
    "${KCADM}" create users -r "${REALM}" \
      -s username="${BOOTSTRAP_ADMIN_EMAIL}" \
      -s email="${BOOTSTRAP_ADMIN_EMAIL}" \
      -s enabled=true \
      -s emailVerified=true >/dev/null
    # --temporary forces the UPDATE_PASSWORD required action on first login.
    "${KCADM}" set-password -r "${REALM}" \
      --username "${BOOTSTRAP_ADMIN_EMAIL}" \
      --new-password "${TEMP_PW}" \
      --temporary
    echo ""
    echo "=============================================================================="
    echo " SCF BUNDLED IDP — BOOTSTRAP ADMIN CREATED (shown ONCE — copy it now)"
    echo "   email:              ${BOOTSTRAP_ADMIN_EMAIL}"
    echo "   temporary password: ${TEMP_PW}"
    echo "   You will be forced to set a new password at first login."
    echo "=============================================================================="
    echo ""
  fi
else
  echo "idp-init: BOOTSTRAP_ADMIN_EMAIL not set — skipping bootstrap user creation."
fi

echo "idp-init: done."
