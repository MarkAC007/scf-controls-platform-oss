#!/usr/bin/env python3
"""
Backfill missing S3 objects for inbox EvidenceFile records (Issue #400 fix).

The inbox handler was not writing JSON payloads to S3 after creating
EvidenceFile DB records, leaving records with valid s3_keys but no backing
S3 object.  This script identifies those records and writes the payload
(sourced from the linked WebhookDelivery.payload_json column) back to S3.

Scope: EvidenceFile records joined to WebhookDelivery where
  - s3_key matches pattern evidence/{org_id}/inbox/{delivery_id}.json
  - scan_status = 'pending'  (malware scan never progressed — no S3 object)
  - head_object confirms S3 object is missing

Usage:
    cd /path/to/backend
    DATABASE_URL=<...> EVIDENCE_BUCKET=<...> python scripts/backfill_inbox_s3_payloads.py --dry-run
    DATABASE_URL=<...> EVIDENCE_BUCKET=<...> python scripts/backfill_inbox_s3_payloads.py --apply
"""
import asyncio
import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Dict, Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from models import EvidenceFile, WebhookDelivery

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def get_db_session() -> AsyncSession:
    """Create an async database session."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is required")

    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")

    engine = create_async_engine(database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return async_session()


async def find_affected_records(db: AsyncSession) -> List[Dict[str, Any]]:
    """Find EvidenceFile inbox records that are missing their S3 object.

    Joins EvidenceFile to WebhookDelivery via evidence_file_id.  Filters to
    records whose s3_key matches the inbox pattern — no scan_status filter,
    since the bug manifests as scan_status='skipped' (ClamAV not running on
    staging) as well as 'pending'.  The head_object check is the sole arbiter
    of which records need backfilling.

    Returns:
        List of dicts with keys: evidence_file_id, s3_key, org_id,
        delivery_id, payload_json
    """
    result = await db.execute(
        select(EvidenceFile, WebhookDelivery)
        .join(WebhookDelivery, WebhookDelivery.evidence_file_id == EvidenceFile.id)
        .where(EvidenceFile.s3_key.like("%/inbox/%"))
    )
    rows = result.all()

    affected = []
    for ef, wd in rows:
        # Only inbox records (pattern: evidence/{org_id}/inbox/{delivery_id}.json)
        if "/inbox/" not in (ef.s3_key or ""):
            continue
        if wd.payload_json is None:
            logger.warning(
                "Skipping delivery %s — payload_json is NULL in DB", wd.id
            )
            continue
        affected.append({
            "evidence_file_id": ef.id,
            "s3_key": ef.s3_key,
            "org_id": str(ef.organization_id),
            "delivery_id": wd.id,
            "payload_json": wd.payload_json,
        })

    return affected


def _s3_object_exists(s3_client, bucket: str, key: str) -> bool:
    """Return True if the S3 object exists, False if 404."""
    from botocore.exceptions import ClientError
    try:
        s3_client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey"):
            return False
        raise


def _write_payload(s3_client, bucket: str, key: str, payload_json: Any, org_id: str) -> int:
    """Write JSON payload to S3.  Returns number of bytes written."""
    body = json.dumps(payload_json).encode("utf-8")
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json",
        ServerSideEncryption="AES256",
        Metadata={"x-scf-org-id": org_id},
    )
    return len(body)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill missing S3 objects for inbox EvidenceFile records (Issue #400)"
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Only identify affected records; do not write to S3 (default)",
    )
    mode_group.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write missing payloads to S3",
    )
    args = parser.parse_args()
    apply_mode = args.apply

    evidence_bucket = os.getenv("EVIDENCE_BUCKET", "")
    if not evidence_bucket:
        logger.error("EVIDENCE_BUCKET environment variable is required")
        sys.exit(1)

    import boto3
    aws_region = os.getenv("AWS_DEFAULT_REGION", "eu-west-1")
    s3_client = boto3.client("s3", region_name=aws_region)

    db = await get_db_session()
    try:
        logger.info("Scanning for inbox EvidenceFile records with scan_status=pending…")
        candidates = await find_affected_records(db)
        logger.info("Found %d candidate record(s) with /inbox/ s3_key", len(candidates))

        # Filter to records where S3 object is actually missing
        missing = []
        for rec in candidates:
            exists = _s3_object_exists(s3_client, evidence_bucket, rec["s3_key"])
            if not exists:
                missing.append(rec)
                logger.info(
                    "  MISSING  evidence_file=%s  s3_key=%s",
                    rec["evidence_file_id"], rec["s3_key"],
                )
            else:
                logger.info(
                    "  OK       evidence_file=%s  s3_key=%s (already present)",
                    rec["evidence_file_id"], rec["s3_key"],
                )

        logger.info(
            "Summary: %d candidate(s) found, %d missing S3 object(s)",
            len(candidates), len(missing),
        )

        if not missing:
            logger.info("Nothing to do.")
            return

        if not apply_mode:
            logger.info(
                "[DRY-RUN] Would write %d object(s) to s3://%s. "
                "Re-run with --apply to proceed.",
                len(missing), evidence_bucket,
            )
            return

        # Confirmation prompt
        answer = input(
            f"\nAbout to write {len(missing)} object(s) to s3://{evidence_bucket}. "
            f"Type 'yes' to confirm: "
        ).strip().lower()
        if answer != "yes":
            logger.info("Aborted.")
            return

        written = 0
        failed = 0
        total_bytes = 0

        for rec in missing:
            try:
                nbytes = _write_payload(
                    s3_client,
                    evidence_bucket,
                    rec["s3_key"],
                    rec["payload_json"],
                    rec["org_id"],
                )
                total_bytes += nbytes
                written += 1
                logger.info(
                    "  Wrote  evidence_file=%s  s3_key=%s  (%d bytes)",
                    rec["evidence_file_id"], rec["s3_key"], nbytes,
                )
            except Exception as exc:
                failed += 1
                logger.error(
                    "  FAILED evidence_file=%s  s3_key=%s  error=%s",
                    rec["evidence_file_id"], rec["s3_key"], exc,
                )

        logger.info(
            "Done. Written: %d, Failed: %d, Total bytes: %d",
            written, failed, total_bytes,
        )

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
