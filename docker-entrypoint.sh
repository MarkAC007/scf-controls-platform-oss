#!/bin/sh
# Entrypoint script for nginx frontend container
# Substitutes BACKEND_URL in nginx.conf template at runtime

set -e

# Check if BACKEND_URL is set
if [ -z "$BACKEND_URL" ]; then
  # When using a load balancer, BACKEND_URL may not be set.
  # In this case, use a placeholder that nginx won't resolve
  # (the LB routes /api/* directly to backend, so nginx proxy isn't used)
  echo "BACKEND_URL not set - using load balancer mode (nginx proxy disabled)"
  export BACKEND_URL="http://localhost:9999"  # Placeholder, won't be used
fi

# Extra CSP connect-src origins for this deployment's object storage — the
# bundled MinIO, a non-eu-west-1 S3 region, a custom S3-compatible endpoint.
# Space-separated list of origins, e.g.
#   EXTRA_CONNECT_SRC="http://localhost:9000 https://*.s3.us-east-2.amazonaws.com"
# Empty by default: the shipped CSP already covers Google auth, generic S3,
# eu-west-1 S3 and Azure Blob. Evidence upload/download from any OTHER storage
# origin is blocked by the browser until it is listed here.
: "${EXTRA_CONNECT_SRC:=}"
export EXTRA_CONNECT_SRC

# Largest request body this nginx will proxy (client_max_body_size). Must never
# be empty: envsubst would render `client_max_body_size ;` and nginx -t would
# fail the container at boot. 64m clears the backend's own 50 MB workbook cap
# with headroom for multipart overhead, so an oversized workbook is refused by
# the application (which says why) rather than by nginx (which cannot).
: "${MAX_UPLOAD_SIZE:=64m}"
export MAX_UPLOAD_SIZE

echo "Substituting BACKEND_URL=$BACKEND_URL"
echo "Substituting EXTRA_CONNECT_SRC=${EXTRA_CONNECT_SRC:-<empty>}"
echo "Substituting MAX_UPLOAD_SIZE=$MAX_UPLOAD_SIZE"

# Substitute BACKEND_URL, EXTRA_CONNECT_SRC and MAX_UPLOAD_SIZE in the nginx
# config template. envsubst is given an explicit variable list so nginx's own
# $-variables ($host, $csp_policy, $remote_addr, ...) survive untouched.
# Render to /tmp, not /etc/nginx (#947). The container runs with
# `read_only: true`, so the image layer that holds /etc/nginx is not writable —
# the old in-place write to /etc/nginx/nginx.conf died at boot. /tmp is a tmpfs.
#
# /etc/nginx itself is deliberately NOT a tmpfs: mounting one there would mask
# the mime.types the image ships, and nginx would then serve every asset as
# application/octet-stream. That presents as a broken UI, not as a config error.
NGINX_CONF=/tmp/nginx.conf
envsubst '$BACKEND_URL $EXTRA_CONNECT_SRC $MAX_UPLOAD_SIZE' < /etc/nginx/nginx.conf.template > "$NGINX_CONF"

# Validate that substitution worked (proxy_pass should contain http:// or https://)
if ! grep "proxy_pass" "$NGINX_CONF" | grep -qE "https?://"; then
  echo "ERROR: proxy_pass substitution failed!"
  echo "Generated config around proxy_pass:"
  grep -A 2 -B 2 "proxy_pass" "$NGINX_CONF" || true
  exit 1
fi

# ---------------------------------------------------------------------------
# Runtime application config (/config.js).
#
# Vite compiles VITE_* values into the JS bundle, so anything that varies per
# deployment would otherwise mean a rebuild per deployment. index.html loads
# /config.js before the app bundle; this renders it from the SCF_* environment,
# and src/data/runtimeConfig.ts falls back to the build-time value for any key
# not written here.
#
# Rendered to /tmp for the same reason the nginx config is: the container runs
# with a read-only root, so the document root cannot be written. nginx serves it
# through `location = /config.js`.
#
# An UNSET variable is omitted rather than written as "". The two are not the
# same: SCF_APP_LOGO="" hides the logo, while unset means "use the bundled
# default". Collapsing them would make it impossible to ask for no logo.
#
# The object is built by jq and emitted as a single JSON.parse() argument rather
# than hand-escaped. This is environment-to-JavaScript codegen, where a value is
# arbitrary operator input: a newline breaks the file, and a `</script>` ends it
# early, which is worse than breaking it. jq -Rn owns the escaping, and JSON is
# a subset of JavaScript object syntax, so a correctly escaped JSON string is a
# correct JavaScript string.
# ---------------------------------------------------------------------------
CONFIG_JS=/tmp/config.js
CONFIG_KEYS="APP_TITLE APP_LOGO MARKETING_WEBSITE_URL ENABLE_PER_WINDOW_REVIEW DEBUG_API"

_config_json() {
  # Built one key at a time and merged. `eval` is used only to dereference the
  # variable NAME; the value itself is passed to jq as an ordinary argument, so
  # it is never re-parsed by the shell. Interpolating it into an eval'd jq
  # command line instead silently eats any quote the operator set.
  _json='{}'
  for _key in $CONFIG_KEYS; do
    eval "_isset=\${SCF_${_key}+yes}"
    [ "${_isset:-}" = yes ] || continue
    eval "_value=\$SCF_${_key}"
    _json="$(printf '%s' "$_json" | jq --arg k "$_key" --arg v "$_value" '. + {($k): $v}')"
    echo "Runtime config: ${_key} set" >&2
  done
  printf '%s' "$_json"
}

# The JSON is embedded as a JavaScript string literal, so jq escapes it a second
# time. `<` then becomes \u003c, which decodes to the same character but cannot
# spell `</script>` — this file is external, where that is already harmless, but
# the escape keeps it harmless if it is ever inlined.
_config_literal="$(_config_json | jq -Rs . | sed -e 's/</\\u003c/g')"
{
  echo "// Generated at container start by docker-entrypoint.sh. Do not edit."
  printf 'window.__SCF_CONFIG__ = JSON.parse(%s);\n' "$_config_literal"
} > "$CONFIG_JS"

echo "Nginx config validated, testing configuration..."

# Test nginx configuration (against the rendered file, not the image default)
nginx -t -c "$NGINX_CONF"

echo "Starting nginx..."

# Start nginx
exec nginx -c "$NGINX_CONF" -g "daemon off;"
