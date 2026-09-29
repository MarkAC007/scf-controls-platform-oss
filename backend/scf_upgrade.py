"""Upgrade steps shared by every deployment target.

``migrate`` and ``verify`` have to behave identically whether the platform is
upgraded by ``scripts/upgrade.sh`` on a compose host or by a pre-sync Job in
Kubernetes. They live here, in the image, because a second implementation of
either is a second thing to keep in step — and the bash one could only ever be
exercised by running a real upgrade.

    python -m scf_upgrade migrate
    python -m scf_upgrade verify [--expect-version X] [--expect-build-stamp Y]

Both exit non-zero on refusal, with the reason on stderr.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional, Set, Tuple

from upgrade_guard import read_build_info

logger = logging.getLogger("scf_upgrade")


def _fmt(revisions: Set[str]) -> str:
    return ", ".join(sorted(revisions)) if revisions else "<empty>"


def _strip_v(version: str) -> str:
    """``"v0.41.6"`` and ``"0.41.6"`` name the same release; nothing else is folded."""
    cleaned = version.strip()
    return cleaned[1:] if cleaned[:1] in ("v", "V") else cleaned


def same_release(running: str, expected: str) -> bool:
    """Exact match after the leading ``v``, deliberately NOT ``compare_versions``.

    ``upgrade_guard.compare_versions`` keeps only the leading integer of each
    component, so ``0.40.0-rc1`` compares equal to ``0.40.0``. That is the right
    coarseness for the upgrade floor, and the wrong one here: this check exists
    to prove the image just built is the one running, and a release candidate
    passing as the release is exactly the stale-image case it must catch. The
    bash this replaced compared the stripped strings byte for byte; so does this.
    """
    return _strip_v(running) == _strip_v(expected)


def _script_config():
    """Alembic config pointing at this image's migration scripts."""
    from alembic.config import Config

    backend_dir = os.path.dirname(os.path.abspath(__file__))
    config = Config(os.path.join(backend_dir, "alembic.ini"))
    config.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    return config


def _revisions() -> Tuple[Set[str], Set[str]]:
    """(heads recorded in the database, head revisions shipped in this image)."""
    import sqlalchemy as sa
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from db_url import get_sync_database_url

    expected = set(ScriptDirectory.from_config(_script_config()).get_heads())

    engine = sa.create_engine(get_sync_database_url(), poolclass=sa.pool.NullPool)
    try:
        with engine.connect() as conn:
            current = set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()
    return current, expected


def migrate() -> int:
    """Run the application's own migration path: guard, upgrade, record version.

    Deliberately not a bare ``alembic upgrade head``. That bypasses
    ``upgrade_guard`` — which is correct for scripts/upgrade.sh, because it has
    already done the equivalent floor check against the release manifest and
    taken a verified backup, but is not correct for anything that has not.
    """
    from database import run_alembic_migrations

    run_alembic_migrations()
    print("migrate: database is at head")
    return 0


def verify(
    expect_version: Optional[str] = None,
    expect_build_stamp: Optional[str] = None,
) -> int:
    """Assert the running code and the schema are the ones we think they are."""
    failures = []

    current, expected = _revisions()
    if current == expected:
        print(f"verify: schema at head ({_fmt(expected)})")
    else:
        failures.append(
            f"database is at {_fmt(current)}, but this image's head is {_fmt(expected)}"
        )

    info = read_build_info()
    if info is None:
        # Images older than the build stamp have none. Not fatal on its own:
        # the schema check above still ran.
        print(
            "verify: no build_info.json — cannot check the running image identity",
            file=sys.stderr,
        )
    else:
        version = str(info.get("version") or "")
        stamp = str(info.get("build_stamp") or "")
        print(
            f"verify: running image version={version or '<unset>'} "
            f"build_stamp={stamp or '<unset>'}"
        )
        if not version and not stamp:
            # A build_info.json with neither field is the same "cannot tell" as
            # no file at all; scripts/upgrade.sh 5c warned here rather than
            # rolling back, and so does this.
            print(
                "verify: build_info.json carries no version or build stamp — "
                "cannot check the running image identity",
                file=sys.stderr,
            )
        elif expect_version and not same_release(version, expect_version):
            failures.append(
                f"running image reports version {version!r}, expected "
                f"{expect_version!r} — the new code is not running (stale image?)"
            )
        # An image with no stamp predates the mechanism; only a MISMATCH is a
        # failure, which is what distinguishes "cannot tell" from "wrong".
        if expect_build_stamp and stamp and stamp != expect_build_stamp:
            failures.append(
                f"running image build_stamp {stamp!r}, expected "
                f"{expect_build_stamp!r} — a stale image is running"
            )

    for failure in failures:
        print(f"verify: FAILED: {failure}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scf_upgrade", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply pending migrations through the guard")

    verify_parser = sub.add_parser(
        "verify", help="assert the schema and the running image are as expected"
    )
    verify_parser.add_argument("--expect-version")
    verify_parser.add_argument("--expect-build-stamp")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.command == "migrate":
        return migrate()
    return verify(args.expect_version, args.expect_build_stamp)


if __name__ == "__main__":
    sys.exit(main())
