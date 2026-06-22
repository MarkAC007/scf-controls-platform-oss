#!/usr/bin/env python3
"""CLI: extract required_artifact_types for SCF controls.

Populates SCFCatalogControl.required_artifact_types via LLM extraction for the
controls mapped to a given list of evidence IDs. Intended as a one-off catalog
seed step — run once per catalog version, or after the extractor prompt is
materially revised.

Usage:
  # Extract for all controls mapped to specific evidence IDs
  python backend/scripts/extract_artifact_types.py \
      --evidence-ids E-BCM-11,E-BCM-12,E-BCM-15

  # Extract for specific controls directly
  python backend/scripts/extract_artifact_types.py --scf-ids BCD-11,BCD-11.1,BCD-11.5

  # Force re-extract even if already populated
  python backend/scripts/extract_artifact_types.py --evidence-ids E-IAM-01 --force

  # Dry-run (list controls that would be processed; no LLM calls, no writes)
  python backend/scripts/extract_artifact_types.py --evidence-ids E-BCM-11 --dry-run

Requires:
  ANTHROPIC_API_KEY environment variable.
  DATABASE_URL environment variable (falls back to asyncpg default).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import SCFCatalogControl, SCFCatalogEvidence  # noqa: E402
from services.artifact_type_extraction_service import extract_batch  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _get_sync_session():
    """Build a sync psycopg2 session from DATABASE_URL."""
    url = (
        os.getenv("DATABASE_URL", "postgresql+asyncpg://cg:cg@localhost:5432/cg_scf")
        .replace("+asyncpg", "+psycopg2")
        .replace("?ssl=require", "?sslmode=require")
    )
    engine = create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=3)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _resolve_scf_ids_from_evidence(session, evidence_ids: list[str]) -> list[str]:
    """Return sorted unique list of scf_ids mapped by the given evidence IDs."""
    rows = session.execute(
        select(SCFCatalogEvidence).where(SCFCatalogEvidence.evidence_id.in_(evidence_ids))
    ).scalars().all()

    missing = set(evidence_ids) - {r.evidence_id for r in rows}
    if missing:
        logger.warning("Evidence IDs not in catalog: %s", sorted(missing))

    scf_ids: set[str] = set()
    for r in rows:
        for scf_id in (r.control_mappings or []):
            if isinstance(scf_id, str) and scf_id.strip():
                scf_ids.add(scf_id.strip())
    return sorted(scf_ids)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--evidence-ids", help="Comma-separated evidence IDs; all mapped controls will be extracted")
    group.add_argument("--scf-ids", help="Comma-separated SCF control IDs to extract")
    parser.add_argument("--force", action="store_true", help="Re-extract controls that already have artifact types")
    parser.add_argument("--dry-run", action="store_true", help="List target controls without calling the LLM or writing")
    args = parser.parse_args()

    session = _get_sync_session()

    try:
        if args.evidence_ids:
            evidence_ids = [e.strip() for e in args.evidence_ids.split(",") if e.strip()]
            scf_ids = _resolve_scf_ids_from_evidence(session, evidence_ids)
            if not scf_ids:
                logger.error("No controls resolved from evidence IDs %s", evidence_ids)
                return 2
            logger.info("Resolved %d controls from %d evidence IDs", len(scf_ids), len(evidence_ids))
        else:
            scf_ids = [s.strip() for s in args.scf_ids.split(",") if s.strip()]
            if not scf_ids:
                logger.error("No SCF IDs provided")
                return 2

        if args.dry_run:
            present = session.execute(
                select(SCFCatalogControl.scf_id, SCFCatalogControl.required_artifact_types)
                .where(SCFCatalogControl.scf_id.in_(scf_ids))
            ).all()
            logger.info("DRY RUN — would extract the following controls:")
            for scf_id, existing in present:
                status = f"{len(existing or [])} existing" if existing else "empty"
                logger.info("  %s  (%s)", scf_id, status)
            return 0

        if not os.getenv("ANTHROPIC_API_KEY"):
            logger.error("ANTHROPIC_API_KEY is required to run extraction")
            return 3

        summary = extract_batch(session, scf_ids, force=args.force)

        print(json.dumps(summary, indent=2, default=str))

        if summary["errors"] > 0:
            return 1
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
