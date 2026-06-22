"""Celery task for the OSS "bring your own SCF Excel" live catalogue import.

A self-hosted operator uploads their licensed SCF .xlsx through the UI; the
/api/admin/catalog/import endpoint stashes it in object storage and enqueues
this task. The worker downloads the workbook, extracts it into the seeder JSON
the backend consumes, and reseeds the catalogue tables.

Runs ONLY in single-tenant (self-hosted) mode — guarded on OSS_SINGLE_TENANT so
catalogue tables can never be force-reseeded in a multi-tenant/SaaS deployment.
The legacy one-shot `catalog-importer` compose service remains available as a
fallback; this task does not replace it.
"""
import asyncio
import logging
import os
import sys
import tempfile

from celery_app import celery_app
from services import s3_service
from services.single_tenant import single_tenant_flag_set

# The extractor ships at /app/scripts in the backend image (see Dockerfile.backend).
if "/app/scripts" not in sys.path:
    sys.path.insert(0, "/app/scripts")

# DATA_DIR / reseed_catalog mirror what the backend seeds from on startup.
from catalog_seeder import DATA_DIR, reseed_catalog

logger = logging.getLogger(__name__)


@celery_app.task(
    name="catalog.import",
    queue="catalog",
    bind=True,
    autoretry_for=(),
    max_retries=0,
)
def import_catalog(self, object_key: str, original_filename: str = "scf.xlsx") -> dict:
    """Download the stashed SCF workbook, extract it, and reseed the catalogue."""
    if not single_tenant_flag_set():
        # Defence-in-depth: the endpoint already gates on single-tenant, but a
        # force-reseed must never run in a multi-tenant deployment.
        raise RuntimeError("catalog import refused: OSS_SINGLE_TENANT not set")

    self.update_state(state="PROGRESS", meta={"step": "downloading"})
    chunks = s3_service.download_blob_stream(object_key)
    if chunks is None:
        raise RuntimeError(f"uploaded workbook not found in storage: {object_key}")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=True) as tmp:
        for chunk in chunks:
            tmp.write(chunk)
        tmp.flush()

        self.update_state(state="PROGRESS", meta={"step": "extracting"})
        import extract_scf_data  # noqa: E402 — path injected above; pandas loads here

        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            meta = extract_scf_data.extract_to_dir(tmp.name, DATA_DIR)
        except ValueError as exc:
            raise RuntimeError(f"not a valid SCF catalogue workbook: {exc}") from exc

    self.update_state(state="PROGRESS", meta={"step": "seeding", **meta})
    seed_results = asyncio.run(reseed_catalog(force=True))

    logger.info(
        "Catalogue import complete from %s: %s controls",
        original_filename,
        meta.get("controls"),
    )
    return {
        "status": "complete",
        "source_filename": original_filename,
        "catalog_meta": meta,
        "seed_results": seed_results,
    }
