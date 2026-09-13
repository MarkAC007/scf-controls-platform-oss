#!/bin/sh
# =============================================================================
# minio-init — create the evidence bucket, and the scoped account that reaches
# only that bucket.
#
# Runs inside the `minio-init` one-shot on every `docker compose up`, and must
# therefore be idempotent: a second run makes no change and exits 0.
#
# Why a separate file rather than an inline compose entrypoint: this logic has
# to be identical in docker-compose.yml and in docker-compose.secrets.yml, whose
# overlay replaces the entrypoint wholesale in order to read credentials from
# /run/secrets. Two copies of a credential-provisioning script drift, and the
# drift is invisible until an install uses the overlay path.
#
# Environment (all read, none optional unless said so):
#   SCF_MINIO_URL            MinIO S3 endpoint, defaults to http://minio:9000
#   SCF_ROOT_USER            MinIO root account. Used ONLY to administer.
#   SCF_ROOT_PASSWORD
#   SCF_APP_ACCESS_KEY_ID    the application's credential. Optional: when empty
#   SCF_APP_SECRET_ACCESS_KEY  no scoped account is created and the bucket is
#                            still made, which is the pre-Phase-4 behaviour.
#   EVIDENCE_BUCKET          bucket name, defaults to `evidence`
#
# The names are SCF_-prefixed deliberately. `mc` reads AWS_ACCESS_KEY_ID and
# MINIO_ROOT_USER from its own environment for its own purposes; passing the
# application's credential under either name would risk mc authenticating AS
# the application while trying to create it.
# =============================================================================
set -eu

MINIO_URL="${SCF_MINIO_URL:-http://minio:9000}"
BUCKET="${EVIDENCE_BUCKET:-evidence}"
POLICY_NAME="scf-evidence-rw"
ALIAS="local"

fatal() { echo "FATAL: minio-init: $*" >&2; exit 1; }
note()  { echo "minio-init: $*"; }

[ -n "${SCF_ROOT_USER:-}" ] || fatal "SCF_ROOT_USER is empty. Run scripts/install.sh, or set MINIO_ROOT_USER in .env."
[ -n "${SCF_ROOT_PASSWORD:-}" ] || fatal "SCF_ROOT_PASSWORD is empty. Run scripts/install.sh, or set MINIO_ROOT_PASSWORD in .env."

# Bounded retry: 30 attempts x 2s. An unbounded `until` loop turns an
# unreachable or mis-credentialled MinIO into a container that spins silently
# while the bucket is never created; the failure then surfaces days later as
# NoSuchBucket on somebody's first evidence upload.
i=0
until mc alias set "$ALIAS" "$MINIO_URL" "$SCF_ROOT_USER" "$SCF_ROOT_PASSWORD" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    fatal "gave up after $i attempts: cannot reach or authenticate to $MINIO_URL"
  fi
  note "waiting for minio... ($i/30)"
  sleep 2
done

mc mb --ignore-existing "$ALIAS/$BUCKET"
note "bucket ready: $BUCKET"

# --- the scoped account ------------------------------------------------------
# Before Phase 4 the installer wrote AWS_ACCESS_KEY_ID equal to MINIO_ROOT_USER,
# so the application held the root account of the object store holding every
# evidence file. New installs now get an independent pair, and this block gives
# it a MinIO user whose policy names one bucket.
if [ -z "${SCF_APP_ACCESS_KEY_ID:-}" ] || [ -z "${SCF_APP_SECRET_ACCESS_KEY:-}" ]; then
  note "no application credential supplied; the bucket exists and no scoped account was created."
  exit 0
fi

# UPGRADE SAFETY. On an install provisioned before Phase 4 the two are the same
# account. `mc admin user add` on the root user would fail, and this one-shot is
# on the `up` path of a running install: failing here would stop the stack from
# coming up over a credential shape that is merely old, not broken. Migrating to
# a scoped account is a documented operator step, not something an upgrade does
# underneath a running install.
if [ "$SCF_APP_ACCESS_KEY_ID" = "$SCF_ROOT_USER" ]; then
  note "the application credential IS the MinIO root account (an install provisioned before the scoped account existed)."
  note "skipping scoped-account creation. To migrate, see docs-site: Evidence storage > Migrating to a scoped MinIO account."
  exit 0
fi

# Evidence bucket only. No admin action, no second bucket, no wildcard resource.
cat > /tmp/scf-evidence-policy.json <<POLICY
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:ListBucket", "s3:GetBucketLocation"],
      "Resource": ["arn:aws:s3:::$BUCKET"]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObjectTagging",
        "s3:PutObjectTagging",
        "s3:AbortMultipartUpload",
        "s3:ListMultipartUploadParts"
      ],
      "Resource": ["arn:aws:s3:::$BUCKET/*"]
    }
  ]
}
POLICY

if ! mc admin policy create "$ALIAS" "$POLICY_NAME" /tmp/scf-evidence-policy.json >/dev/null 2>&1; then
  # Already present from an earlier boot is fine; anything else is not.
  mc admin policy info "$ALIAS" "$POLICY_NAME" >/dev/null 2>&1 \
    || fatal "could not create or read the policy $POLICY_NAME"
fi
note "policy ready: $POLICY_NAME (bucket $BUCKET only)"

# `user add` on an existing user resets its secret key, which is what we want:
# the file in the secrets directory stays authoritative after a rotation.
mc admin user add "$ALIAS" "$SCF_APP_ACCESS_KEY_ID" "$SCF_APP_SECRET_ACCESS_KEY" >/dev/null \
  || fatal "could not create the application user"

# Attaching a policy that is already attached reports "already in effect" and
# exits non-zero. That is the idempotent case, not a failure — and it is the
# case that runs on every boot after the first, so getting it wrong turns a
# working install into a stack that will not come up.
#
# `set -e` is why this is written as an if rather than a `|| true` capture: the
# substitution has to keep the exit status, and there is no grep in the mc image
# to inspect the output with afterwards.
if mc admin policy attach "$ALIAS" "$POLICY_NAME" --user "$SCF_APP_ACCESS_KEY_ID" >/dev/null 2>&1; then
  :
else
  attach_output="$(mc admin policy attach "$ALIAS" "$POLICY_NAME" --user "$SCF_APP_ACCESS_KEY_ID" 2>&1 || :)"
  case "$attach_output" in
    *"already in effect"*) : ;;
    *) fatal "could not attach $POLICY_NAME to the application user: $attach_output" ;;
  esac
fi
note "scoped account ready: the application reaches $BUCKET and nothing else."
