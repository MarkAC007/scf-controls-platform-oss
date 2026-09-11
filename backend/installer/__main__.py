"""``python -m installer serve | unattended | import-env``.

Exit codes (unattended and import-env): 0 ok, 2 validation failed,
3 already provisioned, 4 bad config.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from . import configure_logging, host_secrets_dir, out_dir, secrets_dir
from . import validate as validate_mod
from . import writer
from .app import _provision, create_app
from .generate import SECRET_FILE_NAMES, fernet_key
from .validate import ValidationRejected

EXIT_OK = 0
EXIT_VALIDATION_FAILED = 2
EXIT_ALREADY_PROVISIONED = 3
EXIT_BAD_CONFIG = 4

logger = configure_logging()


def _paths(args: argparse.Namespace) -> tuple[Path, Path, str]:
    """Container paths, plus the ABSOLUTE HOST path recorded in `.env`.

    install.sh passes the host path in SCF_SECRETS_DIR_HOST; when the package is
    driven directly (tests, a dev run) an explicit --secrets-dir is the host path.
    """
    override = getattr(args, "secrets_dir", None)
    sp = Path(override) if override else secrets_dir()
    op = Path(args.out_dir) if getattr(args, "out_dir", None) else out_dir()
    host_dir = os.environ.get("SCF_SECRETS_DIR_HOST") or (
        str(sp) if override else host_secrets_dir()
    )
    return sp, op, host_dir


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    sp, op, host_dir = _paths(args)
    writer.ensure_secrets_dir(sp)
    app = create_app(
        secrets_path=sp,
        out_path=op,
        host_dir=host_dir,
        port=args.port,
        public_port=args.public_port,
    )
    logger.info(
        "installer listening on port %s, published as %s (loopback publish only)",
        args.port,
        args.public_port if args.public_port is not None else args.port,
    )
    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        # An access log would record request lines; the token must never be in one.
        access_log=False,
        log_level="warning",
    )
    return EXIT_OK


def cmd_unattended(args: argparse.Namespace) -> int:
    sp, op, host_dir = _paths(args)
    try:
        config = json.loads(Path(args.config).read_text())
    except FileNotFoundError:
        logger.error("config file not found: %s", args.config)
        return EXIT_BAD_CONFIG
    except json.JSONDecodeError:
        logger.error("config file is not valid JSON: %s", args.config)
        return EXIT_BAD_CONFIG
    if not isinstance(config, dict):
        logger.error("config file must contain a JSON object")
        return EXIT_BAD_CONFIG

    db = dict(config.get("db") or {})
    idp = dict(config.get("idp") or {})

    # The external DB password never travels as a flag (visible in `ps`).
    if args.db_password_stdin:
        db["password"] = sys.stdin.read().strip()
    elif not db.get("password"):
        env_password = os.environ.get("SCF_DB_PASSWORD")
        if env_password:
            db["password"] = env_password

    if writer.is_provisioned(sp):
        logger.error("already provisioned: %s exists", writer.SENTINEL_NAME)
        return EXIT_ALREADY_PROVISIONED

    if str(db.get("type") or "bundled") == "external":
        try:
            parts, _ = validate_mod.parts_from_payload(db)
        except ValidationRejected as exc:
            logger.error("bad database configuration: %s", exc.detail)
            return EXIT_BAD_CONFIG
        result = asyncio.run(validate_mod.run_checks(parts))
        for check in result["checks"]:
            logger.info("  %-12s %s  %s", check["name"], "ok " if check["ok"] else "FAIL", check["detail"])
        if not result["ok"]:
            if result.get("hint"):
                logger.error("%s", result["hint"])
            return EXIT_VALIDATION_FAILED

    try:
        response = _provision(
            payload={"db": db, "idp": idp}, secrets_path=sp, out_path=op, host_dir=host_dir
        )
    except writer.AlreadyProvisioned:
        return EXIT_ALREADY_PROVISIONED
    except ValidationRejected as exc:
        logger.error("bad configuration: %s", exc.detail)
        return EXIT_BAD_CONFIG
    except ValueError as exc:
        logger.error("bad configuration: %s", exc)
        return EXIT_BAD_CONFIG

    print(json.dumps(response, indent=2))
    return EXIT_OK


def cmd_import_env(args: argparse.Namespace) -> int:
    """Move an existing install's credentials out of `.env` and into files."""
    sp, op, host_dir = _paths(args)
    env_path = op / ".env"
    if not env_path.exists():
        logger.error("no .env found at %s", env_path)
        return EXIT_BAD_CONFIG
    if writer.is_provisioned(sp):
        logger.error("already provisioned: %s exists", writer.SENTINEL_NAME)
        return EXIT_ALREADY_PROVISIONED

    original = env_path.read_text()
    values = writer.parse_env(original)

    idp_type = "none"
    if "idp" in (values.get("COMPOSE_PROFILES") or "") or not writer.is_placeholder(
        values.get("KC_ADMIN_PASSWORD")
    ):
        idp_type = "bundled_keycloak"
    elif not writer.is_placeholder(values.get("OIDC_ISSUER")):
        idp_type = "external_oidc"

    writer.ensure_secrets_dir(sp)
    writer.create_sentinel(sp, db="imported", idp=idp_type)

    imported: list[str] = []
    for name in SECRET_FILE_NAMES:
        value = values.get(name)
        if writer.is_placeholder(value):
            # SCF_SECRET_KEY is the one credential we mint when it is absent:
            # without it nothing tier-3 can ever be encrypted.
            value = fernet_key() if name == "SCF_SECRET_KEY" else ""
        if writer.write_secret_file(sp, name, value or "") and value:
            imported.append(name)

    backup = writer.backup_env(op)
    rewritten = writer.strip_secret_lines(
        original,
        SECRET_FILE_NAMES,
        {
            "SCF_SECRETS_DIR": host_dir,
            "COMPOSE_FILE": "docker-compose.yml:docker-compose.secrets.yml",
        },
    )
    writer.write_env(op, rewritten)

    print(
        json.dumps(
            {
                "ok": True,
                "secrets_dir": host_dir,
                "backup": str(backup),
                "imported": imported,
                "idp": idp_type,
            },
            indent=2,
        )
    )
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="installer", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--secrets-dir", default=None, help="container path of the secrets mount")
    common.add_argument("--out-dir", default=None, help="container path of the checkout mount")

    serve = sub.add_parser("serve", parents=[common], help="run the first-run wizard")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument(
        "--public-port",
        type=int,
        default=None,
        help="port the browser reaches the wizard on (the host-side -p port); defaults to --port",
    )
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104 - published loopback-only
    serve.set_defaults(func=cmd_serve)

    unattended = sub.add_parser("unattended", parents=[common], help="provision from a JSON config")
    unattended.add_argument("--config", required=True)
    unattended.add_argument("--db-password-stdin", action="store_true")
    unattended.set_defaults(func=cmd_unattended)

    imp = sub.add_parser("import-env", parents=[common], help="adopt an existing .env")
    imp.set_defaults(func=cmd_import_env)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
