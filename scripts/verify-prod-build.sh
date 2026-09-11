#!/usr/bin/env bash
#
# verify-prod-build.sh — post-deploy smoke test for the SCF frontend.
#
# Answers one question: is this URL serving a production build with its security
# headers, or a development server? Issue #777 was a production host quietly
# serving `vite dev` — full TypeScript source, the HMR client, Vite's /@fs/
# file-read surface, and not one security header — for an unknown length of
# time, because nothing ever checked.
#
# Read-only: HEAD/GET only, no mutation, safe to run against production.
#
# Usage:
#   scripts/verify-prod-build.sh https://scf.compliancegenie.io
#   scripts/verify-prod-build.sh http://localhost:5173
#
# Exit status: 0 = production build with headers, 1 = at least one check failed.
# Wire it into deploy pipelines as the gate after the new frontend is live.

set -uo pipefail

BASE_URL="${1:-}"
if [ -z "$BASE_URL" ]; then
  echo "usage: $0 <base-url>" >&2
  exit 2
fi
BASE_URL="${BASE_URL%/}"

CURL=(curl --silent --show-error --location --max-time 20)

failures=0
pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; failures=$((failures + 1)); }

echo "Verifying $BASE_URL"
echo

# ---------------------------------------------------------------------------
# 1. The document itself must be a built bundle, not a dev-server entrypoint.
# ---------------------------------------------------------------------------
echo "Build mode"
index_html="$("${CURL[@]}" "$BASE_URL/" || true)"

if [ -z "$index_html" ]; then
  fail "could not fetch $BASE_URL/ — nothing else can be checked"
  echo
  echo "1 check failed"
  exit 1
fi

if printf '%s' "$index_html" | grep -q '/@vite/client'; then
  fail "index.html loads /@vite/client — this is the Vite DEV server (#777)"
else
  pass "index.html does not load the Vite HMR client"
fi

if printf '%s' "$index_html" | grep -qE 'src="/src/[^"]+\.(tsx?|jsx?)"'; then
  fail "index.html loads raw source from /src/ — this is the Vite DEV server (#777)"
else
  pass "index.html does not load raw /src/ modules"
fi

if printf '%s' "$index_html" | grep -qE 'src="/assets/[^"]+\.js"'; then
  pass "index.html loads a hashed /assets/ bundle"
else
  fail "index.html loads no hashed /assets/*.js bundle — not a production build"
fi

# ---------------------------------------------------------------------------
# 2. Dev-server surfaces and repo files must not be real responses.
#
# A SPA sends index.html for unknown paths, so "200" alone proves nothing —
# the giveaway is the content type. A real /vite.config.ts comes back as
# video/mp2t or text/plain; the SPA fallback comes back as text/html.
# ---------------------------------------------------------------------------
echo
echo "Dev-server and repo-file exposure"
for path in /@vite/client /@react-refresh /src/main.tsx /vite.config.ts /package.json /package-lock.json /README.md; do
  ctype="$("${CURL[@]}" -o /dev/null -w '%{content_type}' "$BASE_URL$path" || true)"
  case "$ctype" in
    text/html*|"")
      pass "$path is not served (SPA fallback / absent)"
      ;;
    *)
      fail "$path is served as a real file (content-type: $ctype)"
      ;;
  esac
done

# ---------------------------------------------------------------------------
# 3. Security response headers.
# ---------------------------------------------------------------------------
echo
echo "Security headers"
headers="$("${CURL[@]}" -o /dev/null -D - "$BASE_URL/" | tr -d '\r' || true)"
header_value() { printf '%s' "$headers" | grep -i "^$1:" | head -n1 | cut -d' ' -f2-; }

for h in \
  Content-Security-Policy \
  Strict-Transport-Security \
  X-Content-Type-Options \
  X-Frame-Options \
  Referrer-Policy \
  Permissions-Policy \
  Cross-Origin-Opener-Policy
do
  if [ -n "$(header_value "$h")" ]; then
    pass "$h present"
  else
    fail "$h absent"
  fi
done

# ---------------------------------------------------------------------------
# 4. CSP content regressions we have already paid for once.
# ---------------------------------------------------------------------------
echo
echo "CSP content"
csp="$(header_value 'Content-Security-Policy')"

if [ -z "$csp" ]; then
  fail "no CSP to inspect"
else
  script_src="$(printf '%s' "$csp" | tr ';' '\n' | grep -i 'script-src' || true)"

  if printf '%s' "$script_src" | grep -q "'unsafe-inline'"; then
    fail "script-src still allows 'unsafe-inline' (#502, #133)"
  else
    pass "script-src does not allow 'unsafe-inline'"
  fi

  if printf '%s' "$csp" | grep -qF 's3.*.amazonaws.com'; then
    fail "CSP contains the invalid source expression *.s3.*.amazonaws.com (#403)"
  else
    pass "CSP contains no double-wildcard S3 source"
  fi

  img_src="$(printf '%s' "$csp" | tr ';' '\n' | grep -i 'img-src' || true)"

  if printf '%s' "$img_src" | grep -q 'blob:'; then
    pass "img-src allows blob: (org logo object URLs)"
  else
    fail "img-src is missing blob: — uploaded org logos will be blocked (#864)"
  fi
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "All checks passed."
  exit 0
fi
echo "$failures check(s) failed."
exit 1
