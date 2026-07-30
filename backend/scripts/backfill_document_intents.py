#!/usr/bin/env python3
"""
Classify already-ingested CDM documents that have no document intent.

Intent classification is dispatched from ``cdm.ingest``, so documents ingested
before the feature existed — or while ``CDM_INTENT_PROVIDER`` was ``disabled``
— sit at ``intent_status='pending'`` forever. This script walks them.

Idempotent by design: documents already at ``classified`` are skipped unless
``--force`` is passed, so a re-run after an interruption resumes rather than
repeating. Work is done synchronously in-process rather than enqueued, so the
operator sees each result and can stop; ``--limit`` and ``--sleep`` exist
because the far side is a metered hosted API, not a database.

Usage:
    cd /path/to/backend
    DATABASE_URL=<...> CDM_INTENT_PROVIDER=claude ANTHROPIC_API_KEY=<...> \\
        python scripts/backfill_document_intents.py --dry-run
    DATABASE_URL=<...> CDM_INTENT_PROVIDER=claude ANTHROPIC_API_KEY=<...> \\
        python scripts/backfill_document_intents.py --apply --limit 50
"""
import argparse
import logging
import sys
import time
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from models import CDMDocument
from services import cdm_intent
from tasks_cdm import _get_sync_session, classify_cdm_document_intent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

DEFAULT_SLEEP_SECONDS = 1.0


def find_candidates(session, *, org_id: str | None, force: bool, limit: int | None):
    """Documents needing classification, oldest first."""
    query = select(CDMDocument.id, CDMDocument.original_filename, CDMDocument.intent_status)
    if not force:
        query = query.where(CDMDocument.intent_status != "classified")
    if org_id:
        query = query.where(CDMDocument.organization_id == org_id)
    query = query.order_by(CDMDocument.created_at)
    if limit:
        query = query.limit(limit)
    return session.execute(query).all()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="List candidates and exit")
    mode.add_argument("--apply", action="store_true", help="Classify the candidates")
    parser.add_argument("--org-id", help="Restrict to a single organization")
    parser.add_argument("--limit", type=int, help="Process at most this many documents")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-classify documents already at intent_status='classified'",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP_SECONDS,
        help=f"Seconds to pause between calls (default {DEFAULT_SLEEP_SECONDS})",
    )
    args = parser.parse_args()

    if cdm_intent.get_intent_provider() is None:
        logger.error(
            "CDM_INTENT_PROVIDER is disabled or unrecognised; nothing to do. "
            "Set it to 'claude' or 'gpt' along with the provider's API key."
        )
        return 1

    session = _get_sync_session()
    try:
        candidates = find_candidates(
            session, org_id=args.org_id, force=args.force, limit=args.limit
        )
    finally:
        session.close()

    logger.info("Found %d document(s) to classify", len(candidates))
    if args.dry_run:
        for document_id, filename, intent_status in candidates:
            logger.info("  %s  %s  (%s)", document_id, filename, intent_status)
        return 0

    classified = unclassified = failed = 0
    for index, (document_id, filename, _status) in enumerate(candidates, start=1):
        # Called directly rather than dispatched: the operator running a
        # backfill wants to watch it, and a queue would hide the failures.
        # Outside a worker there is no retry machinery, so a transient fault
        # surfaces as an exception here and is counted rather than fatal —
        # one flaky call must not end a backfill of hundreds.
        try:
            result = classify_cdm_document_intent(str(document_id), force=args.force)
        except Exception as exc:
            logger.warning("[%d/%d] %s -> error: %s", index, len(candidates), filename, exc)
            failed += 1
            if args.sleep and index < len(candidates):
                time.sleep(args.sleep)
            continue
        status = result.get("status")
        if status == "classified":
            classified += 1
        elif status == "unclassified":
            unclassified += 1
        else:
            failed += 1
        logger.info(
            "[%d/%d] %s -> %s %s",
            index, len(candidates), filename, status, result.get("domains") or "",
        )
        if args.sleep and index < len(candidates):
            time.sleep(args.sleep)

    logger.info(
        "Done: %d classified, %d unclassified, %d failed",
        classified, unclassified, failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
