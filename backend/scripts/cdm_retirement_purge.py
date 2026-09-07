#!/usr/bin/env python3
"""Remove the retired Control Documents Mapper's uploads from object storage.

CDM retirement, phase 5 (#907, design doc ``docs/plans/cdm-retirement.md``
§6). Migration ``cdmdrop001`` drops the five ``cdm_*`` tables; this script is
the second half of that release and removes the files those rows pointed at.

Run it AFTER ``scripts/upgrade.sh`` has completed. It has **no database
dependency** on purpose: by the time it runs, ``cdm_documents`` no longer
exists, so the sweep is driven by the storage key prefix alone. Every CDM
upload was written under ``cdm/{organization_id}/{document_id}/{filename}``
(the retired ``services/cdm_storage.py``), so the bare ``cdm/`` prefix is the
whole corpus and nothing else lives under it. Evidence files live under
``evidence/`` and are never touched.

Usage (from the Docker host, after the upgrade)::

    docker compose exec backend python scripts/cdm_retirement_purge.py            # dry run: report only
    docker compose exec backend python scripts/cdm_retirement_purge.py --apply    # delete
    docker compose exec backend python scripts/cdm_retirement_purge.py            # → 0 objects remain

Dry run is the default (``--dry-run`` is accepted and means the same);
``--apply`` is required to delete anything. The script is idempotent: run
``--apply`` twice and the second run finds nothing, reports 0 and exits 0.
Exit status is 0 when nothing remains under the prefix after the run (or in
dry-run mode), 1 when objects remain after ``--apply``.

Storage backends, resolved exactly as the platform resolves them
(``services.storage_service.get_backend()``). One driver (``_purge``) runs the
report → list → dry-run gate → delete → re-list → exit-code sequence; each
backend is a small adapter with three methods:

* **S3 / MinIO** — ``services.s3_service._get_s3_client()`` against
  ``EVIDENCE_BUCKET``: ``list_objects_v2`` paginator over the prefix, then
  ``delete_objects`` in batches of at most 1000 keys (the API maximum).
  Bucket versioning is reported first: with versioning on, deleted keys
  survive as non-current versions, and whether to expire those is the
  operator's decision — this script never touches versions.
* **Azure Blob** — ``services.azure_blob_service._get_container_client()``:
  ``list_blobs(name_starts_with="cdm/")`` + ``delete_blob`` one at a time.
  **UNTESTED**: no live Azure installation was available when this was
  written, which is also why it does not use the Blob Batch API. Soft-delete
  retention is reported the same way versioning is for S3.
* **none** — no object store configured: prints "nothing to purge", exit 0.

``storage_service`` itself has no list or delete API and this script does not
add one: deleting by prefix is a one-off retirement operation, not a platform
capability.

Rollback of the files is ``scripts/upgrade.sh --rollback <ts>``, which
restores the object store from the pre-upgrade backup — which is why the
release notes tell you to copy that backup set out of ``./backups/`` before
``backup.sh`` prunes it.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Iterable, List, Protocol

# Runs from /app in the container or from backend/ on a checkout.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import storage_service  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("cdm_retirement_purge")

#: The retired uploader's key prefix. Deliberately not configurable: a typo in a
#: prefix argument on a delete-by-prefix tool is how the wrong data disappears.
CDM_PREFIX = "cdm/"
S3_DELETE_BATCH = 1000  # DeleteObjects hard limit


def _chunks(items: List[str], size: int) -> Iterable[List[str]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


class _Store(Protocol):
    """What the driver needs from a backend."""

    where: str  # human-readable location for log lines

    def report_retention(self) -> None: ...

    def list_keys(self) -> List[str]: ...

    def delete_keys(self, keys: List[str]) -> int:
        """Delete ``keys``; return the number that failed (each logged)."""
        ...


# ---------------------------------------------------------------------------
# S3 / MinIO
# ---------------------------------------------------------------------------
class _S3Store:
    def __init__(self) -> None:
        from services import s3_service

        self.bucket = s3_service.EVIDENCE_BUCKET
        self.client = s3_service._get_s3_client()
        self.where = f"bucket {self.bucket}"

    def report_retention(self) -> None:
        try:
            status = self.client.get_bucket_versioning(Bucket=self.bucket).get("Status") or "Disabled"
        except Exception as exc:  # MinIO without versioning support, or no permission
            status = f"unknown ({exc.__class__.__name__})"
        logger.info("bucket %s versioning: %s", self.bucket, status)
        if status == "Enabled":
            logger.warning(
                "Versioning is ON: deleted keys remain as non-current versions. "
                "This script does not expire versions; decide that separately."
            )

    def list_keys(self) -> List[str]:
        keys: List[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=CDM_PREFIX):
            for obj in page.get("Contents", []) or []:
                keys.append(obj["Key"])
        return keys

    def delete_keys(self, keys: List[str]) -> int:
        failed = 0
        for batch in _chunks(keys, S3_DELETE_BATCH):
            resp = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )
            for err in resp.get("Errors") or []:
                logger.error("failed to delete %s: %s %s", err.get("Key"), err.get("Code"), err.get("Message"))
                failed += 1
        return failed


# ---------------------------------------------------------------------------
# Azure Blob — UNTESTED (no live Azure installation available; see module doc)
# ---------------------------------------------------------------------------
class _AzureStore:
    def __init__(self) -> None:
        from services import azure_blob_service

        self._service = azure_blob_service
        self.container = azure_blob_service._get_container_client()
        self.where = f"container {getattr(self.container, 'container_name', '?')}"
        logger.warning("Azure path is UNTESTED against a live account — verify with a dry run first.")

    def report_retention(self) -> None:
        try:
            props = self._service._get_blob_service_client().get_service_properties()
            policy = props.get("delete_retention_policy") if isinstance(props, dict) else None
            enabled = getattr(policy, "enabled", None) if policy is not None else None
            logger.info("container soft-delete retention: %s", "enabled" if enabled else "disabled/unknown")
        except Exception as exc:
            logger.info("container soft-delete retention: unknown (%s)", exc.__class__.__name__)

    def list_keys(self) -> List[str]:
        return [b.name for b in self.container.list_blobs(name_starts_with=CDM_PREFIX)]

    def delete_keys(self, keys: List[str]) -> int:
        failed = 0
        for name in keys:
            try:
                self.container.delete_blob(name)
            except Exception as exc:
                logger.error("failed to delete %s: %s", name, exc)
                failed += 1
        return failed


# ---------------------------------------------------------------------------
def _purge(store: _Store, apply: bool) -> int:
    store.report_retention()
    before = store.list_keys()
    logger.info("%d object(s) under %s in %s", len(before), CDM_PREFIX, store.where)
    if not apply:
        logger.info("dry run — nothing deleted (pass --apply to delete)")
        return 0
    failed = store.delete_keys(before)
    after = store.list_keys()
    logger.info("deleted %d; %d object(s) remain under %s", len(before) - failed, len(after), CDM_PREFIX)
    return 0 if not after else 1


def purge_s3(apply: bool) -> int:
    return _purge(_S3Store(), apply)


def purge_azure(apply: bool) -> int:
    return _purge(_AzureStore(), apply)


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            f"Delete every object under the '{CDM_PREFIX}' prefix of the platform's "
            "evidence object store (retired CDM uploads). Dry run unless --apply."
        )
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report what would be deleted (the default)")
    mode.add_argument("--apply", action="store_true", help="actually delete the objects")
    args = parser.parse_args(argv)

    backend = storage_service.get_backend()
    logger.info("storage backend: %s", backend)
    if backend == "none":
        logger.info("no object store configured — nothing to purge")
        return 0
    if backend == "s3":
        return purge_s3(apply=args.apply)
    if backend == "azure":
        return purge_azure(apply=args.apply)
    logger.error("unknown storage backend %r", backend)
    return 2


if __name__ == "__main__":
    sys.exit(main())
