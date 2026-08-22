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

echo "Substituting BACKEND_URL=$BACKEND_URL"
echo "Substituting EXTRA_CONNECT_SRC=${EXTRA_CONNECT_SRC:-<empty>}"

# Substitute BACKEND_URL and EXTRA_CONNECT_SRC in nginx config template.
# envsubst is given an explicit variable list so nginx's own $-variables
# ($host, $csp_policy, $remote_addr, ...) survive untouched.
envsubst '$BACKEND_URL $EXTRA_CONNECT_SRC' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

# Validate that substitution worked (proxy_pass should contain http:// or https://)
if ! grep "proxy_pass" /etc/nginx/nginx.conf | grep -qE "https?://"; then
  echo "ERROR: proxy_pass substitution failed!"
  echo "Generated config around proxy_pass:"
  grep -A 2 -B 2 "proxy_pass" /etc/nginx/nginx.conf || true
  exit 1
fi

echo "Nginx config validated, testing configuration..."

# Test nginx configuration
nginx -t

echo "Starting nginx..."

# Start nginx
exec nginx -g "daemon off;"
