"""The live framework registry: read it, and heal it when it is missing.

``catalog_framework_registries`` holds, per catalogue version, each framework's
display name and the publisher's Focal Document Identifier (FDI). The FDI is the
publisher's stable identity for a focal document, and it is the whole basis of
DECLARED framework succession: when ``us_ca_ccpa_2025`` is renamed
``usa_california_ccpa_2025`` the mapping column header changes and the FDI does
not, so the diff can say "same document, new header" instead of "one retirement
plus one unrelated addition".

The failure this module exists for, observed in production on v0.41.0:

    2026.2 was applied by a build that did not write this row. The only row in
    the table was a ``2026.1|seed`` row carrying no identifiers at all (the
    seeder read ``DATA_DIR/frameworks.json``, which has none). Staging the 2026.3
    workbook therefore had NO live FDIs to compare against, every declared tier
    stayed silent, and the framework_churn gate blocked the upgrade with "73 live
    frameworks absent from the workbook; 0 carry the workbook's own
    focal-document identifier". Every one of those 73 is in fact accounted for —
    69 keep their FDI under a new column header and 6 are a new edition of the
    same document — but nothing in the platform could see it.

    The documented remedy was a CLI command (``backfill-framework-registry``)
    that an operator had to know existed, run inside the container, against the
    one workbook version the command would accept. The rollout notes named the
    wrong version. So in practice the platform was stuck.

The platform was never actually short of information. The applied run's own
workbook is still in object storage under
``catalog_import_runs.workbook_object_key``, and it is by construction the
workbook the live rows came from. ``ensure_live_framework_registry`` re-reads the
registry out of it and writes the row, stamped ``source='recovered'`` so the
provenance stays distinguishable from an operator's backfill.

Recovery is best-effort by design. It never raises and never blocks staging: if
there is no such run, the object is gone, or the extraction fails, the returned
status carries a ``reason`` and the ``live_framework_registry`` sanity check
turns that reason into an operator-actionable failure. What it must never do is
write a row that lies — a registry extracted from a workbook whose version does
not match the live catalogue describes different rows, so that case is reported
and NOT written, exactly as the CLI refuses it.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import CatalogFrameworkRegistry
from models import CatalogImportRun

logger = logging.getLogger(__name__)

# Written by this module's self-heal path, as opposed to 'backfill' (an operator
# supplied the workbook) or 'apply' (the upgrade transaction wrote it).
SOURCE_RECOVERED = "recovered"
SOURCE_BACKFILL = "backfill"


@dataclass
class LiveRegistryStatus:
    """What the live catalogue version's registry row looks like right now.

    ``registry`` None with a ``reason`` set is the interesting state: it is the
    one that blocks an upgrade, and the reason is what the operator needs to
    read. ``recovered_from_run_id`` is set only when THIS call wrote the row.
    """

    catalog_version: Optional[str] = None
    registry: Optional[dict] = None
    source: Optional[str] = None
    entries: int = 0
    with_focal_document_id: int = 0
    recovered_from_run_id: Optional[str] = None
    reason: Optional[str] = None
    # The version read out of the workbook the registry came from, when one was
    # read. Equal to ``catalog_version`` on every write except a CLI
    # ``--allow-version-mismatch`` override, which is the one case an operator
    # needs both numbers echoed back at them.
    workbook_version: Optional[str] = None
    # Registry rows the write touched (0 when nothing was written). Reported
    # rather than assumed so the CLI's output states what happened instead of a
    # constant.
    rows_written: int = 0

    @property
    def usable(self) -> bool:
        """Whether declared framework succession can actually fire.

        A row with zero focal-document identifiers is no more useful than no row
        at all — that is precisely the ``2026.1|seed`` row production had — so
        the two are one state here.
        """
        return bool(self.registry) and self.with_focal_document_id > 0


class RegistryVersionMismatch(Exception):
    """The workbook's catalogue version is not the live one.

    Raised rather than silently accepted because the registry row is stamped
    with the LIVE version: writing identifiers read from a different release
    claims they describe rows they do not describe, and the next upgrade's diff
    would trust that claim.
    """

    def __init__(self, workbook_version: Optional[str], live_version: Optional[str]):
        super().__init__(
            f"workbook is catalog version {workbook_version!r} but the live "
            f"catalog is {live_version!r}"
        )
        self.workbook_version = workbook_version
        self.live_version = live_version


def _counts(registry: Optional[dict]) -> tuple:
    entries = len(registry or {})
    with_fdi = sum(
        1 for entry in (registry or {}).values() if (entry or {}).get("focal_document_id")
    )
    return entries, with_fdi


def _load_extractor():
    """The workbook extractor, from wherever it ships.

    Delegates to ``catalog_diff`` rather than repeating its two-candidate path
    search, so there is one answer to "which extractor is in play" and a
    bind-mounted worktree cannot end up testing the image's baked copy in one
    module and its own in another.
    """
    from services import catalog_diff

    return catalog_diff._load_extractor()


def _download_workbook_to_temp(object_key: str) -> str:
    """Stream one stored workbook to a private temp file and return its path.

    A local copy of ``tasks_catalog._download_to_temp``. Deliberately not
    imported from there: ``tasks_catalog`` imports celery_app and the seeder at
    module scope, and pulling a Celery app into the request path that serves the
    admin console is a dependency this file should not own. The ``.xlsx`` suffix
    is load-bearing — pandas picks its reader from the extension.
    """
    from services import storage_service

    chunks = storage_service.download_blob_stream(object_key)
    if chunks is None:
        raise FileNotFoundError(f"object not found in storage: {object_key}")
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        for chunk in chunks:
            tmp.write(chunk)
        return tmp.name


async def _stored_registry_row(
    session: AsyncSession, version: str
) -> Optional[CatalogFrameworkRegistry]:
    result = await session.execute(
        select(CatalogFrameworkRegistry).where(
            CatalogFrameworkRegistry.catalog_version == version
        )
    )
    rows = [
        row
        for row in result.scalars().all()
        # Re-checked in Python: the WHERE is the authority in Postgres, but this
        # function is also driven by scripted fake sessions that answer a SELECT
        # by table, and a row for a DIFFERENT version must never be mistaken for
        # this version's.
        if getattr(row, "catalog_version", None) == version
    ]
    return rows[0] if rows else None


async def _applied_run_with_workbook(
    session: AsyncSession, version: str
) -> Optional[CatalogImportRun]:
    """The applied run that produced ``version`` and still has its workbook.

    Latest first. Every predicate is re-applied in Python for the same reason as
    above, and because ``workbook_object_key`` is nulled by the cleanup beat
    task: acting on a stale row would mean downloading nothing and reporting a
    recovery that did not happen.
    """
    result = await session.execute(
        select(CatalogImportRun)
        .where(
            CatalogImportRun.status == "applied",
            CatalogImportRun.to_version == version,
            CatalogImportRun.workbook_object_key.isnot(None),
        )
        .order_by(CatalogImportRun.created_at.desc())
    )
    candidates = [
        run
        for run in result.scalars().all()
        if getattr(run, "status", None) == "applied"
        and getattr(run, "to_version", None) == version
        and getattr(run, "workbook_object_key", None)
    ]
    if not candidates:
        return None
    candidates.sort(
        key=lambda run: (
            getattr(run, "completed_at", None) or getattr(run, "created_at", None),
        ),
        reverse=True,
    )
    return candidates[0]


async def ensure_live_framework_registry(session: AsyncSession) -> LiveRegistryStatus:
    """Return the live catalogue version's registry, recovering it if absent.

    Called at the top of staging, BEFORE the live catalogue is loaded, so a row
    written here is the one ``load_live_framework_registry`` then reads. Commits
    when it writes — the recovery is a correction to the live record, not part of
    the upgrade being staged, and it must survive a run that is subsequently
    blocked or cancelled.

    Never raises. Every unhappy path returns a status whose ``reason`` names what
    stopped it.
    """
    from services import catalog_diff

    version = await catalog_diff.resolve_live_catalog_version(session)
    if not version:
        return LiveRegistryStatus(
            reason="no live catalog version could be resolved (is the catalog seeded?)"
        )

    row = await _stored_registry_row(session, version)
    if row is not None:
        entries, with_fdi = _counts(row.registry)
        return LiveRegistryStatus(
            catalog_version=version,
            registry=row.registry,
            source=getattr(row, "source", None),
            entries=entries,
            with_focal_document_id=with_fdi,
            reason=(
                None
                if with_fdi
                else (
                    f"the stored registry for {version} carries no focal-document "
                    f"identifiers (source: {getattr(row, 'source', None)})"
                )
            ),
        )

    run = await _applied_run_with_workbook(session, version)
    if run is None:
        return LiveRegistryStatus(
            catalog_version=version,
            reason=(
                f"no applied upgrade run for {version} still holds its workbook in "
                f"object storage"
            ),
        )

    run_id = str(getattr(run, "id", "")) or None
    try:
        workbook_path = _download_workbook_to_temp(run.workbook_object_key)
    except Exception as exc:  # noqa: BLE001 — recovery is best-effort
        logger.warning(
            "Framework registry recovery: could not fetch the workbook for run %s "
            "(%s): %s",
            run_id,
            run.workbook_object_key,
            exc,
        )
        return LiveRegistryStatus(
            catalog_version=version,
            reason=(
                f"the workbook stored for the applied {version} run could not be "
                f"read from object storage ({exc})"
            ),
        )

    try:
        extractor = _load_extractor()
        workbook_version, registry = extractor.extract_framework_registry_only(
            workbook_path
        )
    except Exception as exc:  # noqa: BLE001 — recovery is best-effort
        logger.warning(
            "Framework registry recovery: could not extract the registry from the "
            "workbook for run %s: %s",
            run_id,
            exc,
        )
        return LiveRegistryStatus(
            catalog_version=version,
            reason=(
                f"the workbook stored for the applied {version} run could not be "
                f"read as an SCF workbook ({exc})"
            ),
        )
    finally:
        try:
            os.unlink(workbook_path)
        except OSError:  # pragma: no cover - already gone
            pass

    if str(workbook_version) != str(version):
        logger.warning(
            "Framework registry recovery: the workbook stored for run %s is "
            "catalog version %s, not the live %s; refusing to write it",
            run_id,
            workbook_version,
            version,
        )
        return LiveRegistryStatus(
            catalog_version=version,
            reason=(
                f"the workbook stored for the applied run is catalog version "
                f"{workbook_version}, not the live {version}, so its identifiers "
                f"do not describe the live rows"
            ),
        )

    if not registry:
        return LiveRegistryStatus(
            catalog_version=version,
            reason=(
                f"the workbook stored for the applied {version} run carries no "
                f"framework registry (a pre-2026.1 workbook)"
            ),
        )

    from services.catalog_apply import _now, _upsert_framework_registry

    rows = await _upsert_framework_registry(
        session, version, registry, SOURCE_RECOVERED, _now()
    )
    await session.commit()

    entries, with_fdi = _counts(registry)
    logger.info(
        "Framework registry recovered for catalog version %s from the applied run "
        "%s: %d entries, %d carrying a focal-document identifier",
        version,
        run_id,
        entries,
        with_fdi,
    )
    return LiveRegistryStatus(
        catalog_version=version,
        registry=registry,
        source=SOURCE_RECOVERED,
        entries=entries,
        with_focal_document_id=with_fdi,
        recovered_from_run_id=run_id,
        workbook_version=str(workbook_version),
        rows_written=rows,
        reason=(
            None
            if with_fdi
            else (
                f"the workbook stored for the applied {version} run carries no "
                f"focal-document identifiers"
            )
        ),
    )


async def read_live_framework_registry(session: AsyncSession) -> LiveRegistryStatus:
    """The stored row for the live version, with NO recovery attempt.

    What the admin console's catalogue card reads. Kept separate from
    ``ensure_live_framework_registry`` so a GET can never have the side effect of
    writing a row and committing.
    """
    from services import catalog_diff

    version = await catalog_diff.resolve_live_catalog_version(session)
    if not version:
        return LiveRegistryStatus(
            reason="no live catalog version could be resolved (is the catalog seeded?)"
        )
    row = await _stored_registry_row(session, version)
    if row is None:
        return LiveRegistryStatus(
            catalog_version=version,
            reason=f"no framework registry is stored for the live catalog {version}",
        )
    entries, with_fdi = _counts(row.registry)
    return LiveRegistryStatus(
        catalog_version=version,
        registry=row.registry,
        source=getattr(row, "source", None),
        entries=entries,
        with_focal_document_id=with_fdi,
    )


async def register_framework_registry_from_workbook(
    session: AsyncSession,
    workbook_path,
    *,
    source: str = SOURCE_BACKFILL,
    allow_version_mismatch: bool = False,
    extractor=None,
) -> LiveRegistryStatus:
    """Write the live version's registry from an operator-supplied workbook.

    The shared implementation behind ``cli.admin backfill-framework-registry``
    and ``POST /api/admin/catalog/framework-registry``. Unlike
    ``ensure_live_framework_registry`` this one RAISES, because both callers have
    an operator in front of them who supplied the wrong file and needs to be told
    which file to supply instead:

    * ``ValueError``            — the workbook carries no registry at all. Writing
      an empty row would assert that this catalogue version has no
      focal-document identifiers, which is a stronger and falser claim than
      having no row.
    * ``RegistryVersionMismatch`` — the workbook is a different release.
      ``allow_version_mismatch`` is a deliberate CLI-only escape hatch for an
      operator who knows two releases' registries are equivalent; the HTTP
      surface does not offer it.

    The row is always stamped with the LIVE version, never the workbook's: it
    describes the rows in the database, not the file it was read from.
    """
    from services import catalog_diff

    extractor = extractor or _load_extractor()
    workbook_version, registry = extractor.extract_framework_registry_only(
        workbook_path
    )

    if not registry:
        raise ValueError(
            f"no framework registry in {workbook_path} (a pre-2026.1 workbook?)"
        )

    live_version = await catalog_diff.resolve_live_catalog_version(session)
    if not live_version:
        raise ValueError("no live catalog version — seed the catalog first")

    if str(workbook_version) != str(live_version) and not allow_version_mismatch:
        raise RegistryVersionMismatch(workbook_version, live_version)

    from services.catalog_apply import _now, _upsert_framework_registry

    rows = await _upsert_framework_registry(
        session, live_version, registry, source, _now()
    )
    await session.commit()

    entries, with_fdi = _counts(registry)
    return LiveRegistryStatus(
        catalog_version=live_version,
        registry=registry,
        source=source,
        entries=entries,
        with_focal_document_id=with_fdi,
        workbook_version=str(workbook_version),
        rows_written=rows,
    )
