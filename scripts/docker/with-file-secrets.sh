#!/bin/sh
# =============================================================================
# with-file-secrets — export file-backed credentials into the environment, then
# exec the real command. Used ONLY by docker-compose.secrets.yml (#947).
#
# Why this exists: a credential that some third-party library insists on
# reading from the process environment has to be materialised here, inside the
# container, from the mounted secret file — never interpolated into compose on
# the host, where it would end up visible in `docker inspect`.
#
# It held the two AWS_* variables because boto3 reads them from the environment
# and nowhere else, and the S3 client relied on boto3's ambient credential
# chain to find them. That is no longer true: the client is built with
# credentials passed explicitly, resolved through backend/services/secrets.py,
# which understands the {NAME}_FILE convention itself. So the export list is
# now empty and this script is a pass-through — kept, with its helper, because
# the next such credential is one line rather than a rewrite.
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
#
# The list is currently EMPTY, and that is the finished state rather than an
# oversight. It held AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY because boto3
# reads those two from the process environment and nowhere else, and because
# the S3 client used to let boto3's ambient credential chain find them. It no
# longer does: the driver builds every client with credentials passed
# explicitly, and they are resolved through services/secrets.py, which
# understands {NAME}_FILE on its own. The overlay still mounts both files and
# still sets both *_FILE variables; the difference is only who reads them.
#
# `export_from_file` is kept deliberately. It is the whole point of this script
# and the next credential that cannot read a file for itself needs one line
# here, not a rewrite. A shellcheck-style "unused function" warning on it is
# expected.
unset _name _file

exec "$@"
