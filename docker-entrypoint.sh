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

echo "Substituting BACKEND_URL=$BACKEND_URL"

# Substitute BACKEND_URL in nginx config template
envsubst '$BACKEND_URL' < /etc/nginx/nginx.conf.template > /etc/nginx/nginx.conf

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
