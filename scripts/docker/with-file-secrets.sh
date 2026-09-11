#!/bin/sh
# =============================================================================
# with-file-secrets — export file-backed credentials into the environment, then
# exec the real command. Used ONLY by docker-compose.secrets.yml (#947).
#
# Why this exists: most of the backend's credentials are resolved through
# backend/services/secrets.py, which understands the {NAME}_FILE convention on
# its own. boto3 does not. It reads AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
# from the process environment and nowhere else, so under the secrets overlay
# those two have to be materialised here, inside the container, from the
# mounted secret files — never interpolated into compose on the host, where
# they would end up visible in `docker inspect`.
#
# Usage (compose):
#   entrypoint: ["/bin/sh", "/with-file-secrets.sh"]
#   command:    [ ...the service's original command... ]
#
# Anything exported here is visible only inside this container's process tree.
# The values are never echoed and never appear in argv.
# =============================================================================
set -eu

# $1 = target variable name, $2 = path of the file holding its value.
# The caller expands ${NAME_FILE} itself, so no indirect expansion is needed.
export_from_file() {
    _name="$1"
    _file="$2"

    # No _FILE set for this credential: leave whatever the environment already
    # has. A legacy .env install therefore behaves byte-for-byte as before.
    [ -n "$_file" ] || return 0

    if [ ! -r "$_file" ]; then
        echo "FATAL: ${_name}_FILE points at $_file, which does not exist or is not readable." >&2
        echo "       On Linux the secret files must be readable by uid 1001 —" >&2
        echo "       scripts/install.sh arranges this for you; see UPGRADING.md." >&2
        exit 1
    fi

    # An EMPTY secret file means "unset" (contract §1), so fall through to the
    # environment rather than exporting an empty credential that then fails
    # later and further away. Trailing newlines are stripped: every reader
    # .strip()s, and a stray \n inside an AWS key breaks the SigV4 signature.
    _value="$(tr -d '\n\r' < "$_file")"
    [ -n "$_value" ] || return 0

    export "$_name=$_value"
    unset _value
}

# Keep this list short: it is only for consumers that cannot read a file
# themselves. Everything the backend resolves through services/secrets.py
# already understands {NAME}_FILE and must NOT be listed here.
export_from_file AWS_ACCESS_KEY_ID "${AWS_ACCESS_KEY_ID_FILE:-}"
export_from_file AWS_SECRET_ACCESS_KEY "${AWS_SECRET_ACCESS_KEY_FILE:-}"

unset _name _file

exec "$@"
