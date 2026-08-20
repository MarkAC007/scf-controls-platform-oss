"""Transactional catalog apply/revert for the SCF catalog upgrade (WP1b, plan §4.2.4-7).

Consumes a staged ``DiffDetail`` (produced by ``services/catalog_diff.py`` and
stored against the run — the diff IS the platform revert anchor, plan §4.1 M4)
and applies or reverts it against the live catalog tables in ONE transaction
under an exclusive ``pg_advisory_xact_lock`` on the global catalog lock key.

Apply semantics (plan §4.2.4):
- Explicit-column UPSERTs for controls / domains / evidence / assessment
  objectives, restricted to the compared-column allowlists imported from
  ``catalog_diff`` — ``required_artifact_types`` (+``_extracted_at``) and
  ``created_at`` are preserved by construction because they are simply never
  written.
- ``updated_at = now()`` on every touched row, which rotates the
  count+max(updated_at) ETag in ``api/catalog.py`` so clients refetch.
- Deprecations are status flips (+``retired_in_version``), NEVER deletes;
  resurrections re-activate (clear ``retired_in_version``/``superseded_by``).
- Admin-confirmed superseded pairings (``run.superseded_pairings``) are written
  onto the deprecated control rows, with successor existence/active validation
  here in the apply service (the column has no DB FK by design, §4.1 M2).
- Capability themes are upserted by ``theme_code`` from the curated
  ``capability_themes.json`` (not workbook-sourced); theme mappings are
  recomputed wholesale in-transaction from the post-apply control rows.
- ``catalog_version`` is restamped on touched rows only.
- Any failure propagates after ``session.rollback()`` — catalog untouched.

Revert semantics (plan §4.2.6) — latest applied run only, REFUSED while any
org's ``organization_catalog_state.reconciled_catalog_version`` equals the
run's ``to_version``:
- Changed fields are restored from the stored diff-detail OLD values. Per the
  ``catalog_diff`` module docstring, the CONTROLS entity's changed-field
  records are the single revert authority — including the
  ``framework_mappings`` column; the informational ``framework_mappings``
  diff entity and the always-empty ``capability_themes`` entity are never
  consumed here.
- Run-added rows -> status='deprecated' (never delete); run-deprecated rows ->
  re-activated; run-resurrected rows -> re-deprecated (their pre-run
  ``retired_in_version``/``superseded_by`` are not stored in the diff and are
  left NULL — an accepted, documented loss).
- Same single transaction + advisory lock; mappings recomputed afterwards.

Post-apply cache handling (plan §4.2.7) is exposed as
``purge_trust_portal_cache()`` (Redis) and the ``STALE_MODULE_CACHES``
constant naming the two module-global collection-interface caches that stay
stale until process restart; both are surfaced in the apply report.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from cache import CACHE_PREFIX, CACHE_VERSION
from catalog_models import (
    CapabilityTheme,
    CapabilityThemeMapping,
    SCFCatalogAssessmentObjective,
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
)
from models import CatalogImportRun, OrganizationCatalogState
from schemas_catalog_upgrade import (
    CatalogEntityType,
    DiffDetail,
    SupersededPairing,
)
from services.catalog_diff import (
    AO_COMPARED_FIELDS,
    CONTROL_COMPARED_FIELDS,
    DOMAIN_COMPARED_FIELDS,
    EVIDENCE_COMPARED_FIELDS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global catalog advisory lock key (plan §4.2.4)
# ---------------------------------------------------------------------------

# int64 of b"SCFCATLG". ONE key for every catalog-shape mutation: platform
# apply/revert here, and org reconciliation (WP2c) takes the SAME key shared
# (plus its org-keyed exclusive lock) so platform applies cannot interleave
# with org scope re-materialisation. pg_advisory_xact_lock => released at
# COMMIT/ROLLBACK, never leaked by a dying worker.
CATALOG_LOCK_KEY = 0x5343_4643_4154_4C47

_ADVISORY_LOCK_SQL = text("SELECT pg_advisory_xact_lock(:key)")


async def acquire_catalog_lock(session: AsyncSession) -> None:
    """Take the exclusive transaction-scoped catalog lock (blocks until held)."""
    await session.execute(_ADVISORY_LOCK_SQL, {"key": CATALOG_LOCK_KEY})


# ---------------------------------------------------------------------------
# Module caches that go stale on apply (plan §4.2.7)
# ---------------------------------------------------------------------------

# Redis-side: purged by purge_trust_portal_cache() right after apply commits.
# make_cache_key() emits "scf:cache:v1:trust_portal:{slug}" for short keys and
# "scf:cache:trust_portal:{md5}" for long ones — purge both shapes.
TRUST_PORTAL_CACHE_PATTERNS = (
    f"{CACHE_PREFIX}{CACHE_VERSION}:trust_portal:*",
    f"{CACHE_PREFIX}trust_portal:*",
)

# Process-local module-global caches of collection_interfaces.json. They are
# NOT purgeable across workers from here; accepted as stale-until-restart and
# named in the apply report so the operator knows (plan §4.2.7).
STALE_MODULE_CACHES = (
    "api.capabilities._load_collection_interfaces",  # functools.lru_cache(maxsize=1)
    "services.validation_service._collection_interfaces",  # module-global dict
)


async def purge_trust_portal_cache() -> int:
    """Delete every trust-portal Redis cache entry; returns keys deleted.

    Failures are the caller's to tolerate — an applied catalog with a stale
    (≤15 min TTL) public portal beats a failed-looking apply.
    """
    from redis_client import get_redis_client

    redis = await get_redis_client()
    deleted = 0
    for pattern in TRUST_PORTAL_CACHE_PATTERNS:
        async for key in redis.scan_iter(match=pattern):
            await redis.delete(key)
            deleted += 1
    return deleted


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CatalogApplyError(Exception):
    """Base error for catalog apply/revert."""


class ApplyConsistencyError(CatalogApplyError):
    """The staged diff no longer matches the live catalog or the run."""


class PairingValidationError(CatalogApplyError):
    """A superseded pairing references a missing or non-active successor."""

    def __init__(self, message: str, invalid: List[str]):
        super().__init__(message)
        self.invalid = invalid


class RevertRefusedError(CatalogApplyError):
    """Base for typed revert refusals (WP1c maps these to 409)."""


class RevertBlockedError(RevertRefusedError):
    """Orgs are reconciled to the run's to_version; revert refused.

    ``blockers`` lists the organization_ids pinning the version.
    """

    def __init__(self, to_version: str, blockers: List[str]):
        self.to_version = to_version
        self.blockers = blockers
        super().__init__(
            f"revert refused: {len(blockers)} organisation(s) are reconciled "
            f"to catalog {to_version}: {', '.join(blockers)}"
        )


class RevertNotLatestError(RevertRefusedError):
    """Only the latest applied run may be reverted."""


# ---------------------------------------------------------------------------
# Entity registry — model + natural key + explicit UPSERT column allowlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EntitySpec:
    entity: CatalogEntityType
    model: type
    key_attr: str
    columns: Tuple[str, ...]  # the ONLY data columns apply/revert ever write


# The column allowlists are imported from catalog_diff (single source with the
# diff engine). required_artifact_types(+extracted_at) and created_at are
# excluded by construction: they appear in no allowlist.
ENTITY_SPECS: Tuple[_EntitySpec, ...] = (
    _EntitySpec(CatalogEntityType.DOMAINS, SCFCatalogDomain, "identifier", DOMAIN_COMPARED_FIELDS),
    _EntitySpec(CatalogEntityType.CONTROLS, SCFCatalogControl, "scf_id", CONTROL_COMPARED_FIELDS),
    _EntitySpec(CatalogEntityType.EVIDENCE, SCFCatalogEvidence, "evidence_id", EVIDENCE_COMPARED_FIELDS),
    _EntitySpec(
        CatalogEntityType.ASSESSMENT_OBJECTIVES,
        SCFCatalogAssessmentObjective,
        "ao_id",
        AO_COMPARED_FIELDS,
    ),
)

# Revert consumes exactly these entities. FRAMEWORK_MAPPINGS is informational
# (controls own the column) and CAPABILITY_THEMES is always empty + re-derived.
REVERTED_ENTITIES = tuple(spec.entity for spec in ENTITY_SPECS)


@dataclass
class EntityApplyCounts:
    added: int = 0
    changed: int = 0
    deprecated: int = 0
    resurrected: int = 0


@dataclass
class CatalogApplyReport:
    """Returned by apply/revert; the Celery task serialises it."""

    run_id: str
    action: str  # "applied" | "reverted"
    from_version: str
    to_version: str
    entities: Dict[str, EntityApplyCounts] = field(default_factory=dict)
    themes_upserted: int = 0
    mappings_recomputed: int = 0
    stale_module_caches: Tuple[str, ...] = STALE_MODULE_CACHES

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "action": self.action,
            "from_version": self.from_version,
            "to_version": self.to_version,
            "entities": {
                name: vars(counts).copy() for name, counts in self.entities.items()
            },
            "themes_upserted": self.themes_upserted,
            "mappings_recomputed": self.mappings_recomputed,
            "stale_module_caches": list(self.stale_module_caches),
        }


# ---------------------------------------------------------------------------
# Version authority helper (plan §4.2.5 — consumed by WP1c)
# ---------------------------------------------------------------------------


async def get_current_catalog_version(session: AsyncSession) -> Optional[str]:
    """Canonical catalog version: latest applied import run's to_version.

    None when no run has ever been applied (pre-first-upgrade installs) — the
    caller decides its bootstrap fallback. A reverted run no longer counts, so
    the authority naturally falls back to the previous applied run.
    """
    result = await session.execute(
        select(CatalogImportRun)
        .where(CatalogImportRun.status == "applied")
        .order_by(CatalogImportRun.completed_at.desc().nulls_last())
        .limit(1)
    )
    run = result.scalars().first()
    return run.to_version if run else None


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    # Catalog tables use naive UTC timestamps (DateTime(timezone=False)).
    return datetime.utcnow()


async def _load_rows_by_key(
    session: AsyncSession, spec: _EntitySpec, keys: List[str]
) -> Dict[str, Any]:
    if not keys:
        return {}
    key_col = getattr(spec.model, spec.key_attr)
    result = await session.execute(select(spec.model).where(key_col.in_(keys)))
    return {getattr(row, spec.key_attr): row for row in result.scalars().all()}


def _touch(row: Any, version: str, now: datetime) -> None:
    row.catalog_version = version
    row.updated_at = now


def _set_allowed_fields(row: Any, values: Dict[str, Any], allowed: Tuple[str, ...]) -> None:
    """Write only allowlisted columns — the preservation-by-construction gate."""
    for name, value in values.items():
        if name in allowed:
            setattr(row, name, value)


# ---------------------------------------------------------------------------
# Themes + mappings (plan §4.2.4: themes upserted by theme_code, mappings
# recomputed in-transaction — no org FKs point into either table)
# ---------------------------------------------------------------------------


def _load_capability_themes_json() -> dict:
    """Curated theme definitions + NIST-family mapping rules (not workbook-sourced)."""
    from catalog_seeder import DATA_DIR

    json_path = Path(DATA_DIR) / "capability_themes.json"
    if not json_path.exists():
        raise ApplyConsistencyError(f"capability_themes.json not found: {json_path}")
    with open(json_path, "r") as f:
        return json.load(f)


async def _upsert_themes(
    session: AsyncSession, themes_data: List[dict], version: str, now: datetime
) -> int:
    """Upsert CapabilityTheme rows by theme_code. Removed themes are kept
    (mappings FK them; catalog rows are never deleted)."""
    result = await session.execute(select(CapabilityTheme))
    existing = {row.theme_code: row for row in result.scalars().all()}

    upserted = 0
    for theme in themes_data:
        code = theme["theme_code"]
        row = existing.get(code)
        if row is None:
            row = CapabilityTheme(theme_code=code)
            session.add(row)
        row.name = theme["name"]
        row.description = theme["description"]
        row.ksi_reference = theme.get("ksi_reference")
        row.display_order = theme.get("display_order", 0)
        row.icon = theme.get("icon")
        _touch(row, version, now)
        upserted += 1
    return upserted


def compute_theme_mappings(
    controls: List[Any], nist_family_mappings: Dict[str, dict], theme_code_to_id: Dict[str, int]
) -> List[Tuple[int, str, str]]:
    """(theme_id, scf_id, relevance) triples — same derivation as the seeder:
    each control's nist_800_53_r5 references reduce to NIST families, crossed
    with the JSON's family->theme rules. All controls are mapped regardless of
    lifecycle status — the §4.4 read paths filter on control status themselves,
    and drill-downs still resolve (badged) org data on deprecated controls."""
    triples: List[Tuple[int, str, str]] = []
    seen = set()
    for ctrl in controls:
        scf_id = getattr(ctrl, "scf_id", None)
        if not scf_id:
            continue
        nist_refs = (getattr(ctrl, "framework_mappings", None) or {}).get(
            "nist_800_53_r5", []
        )
        families = {ref.split("-")[0] if "-" in ref else ref for ref in nist_refs}
        for family in sorted(families):
            rule = nist_family_mappings.get(family)
            if not rule:
                continue
            primary = rule.get("primary")
            if primary in theme_code_to_id:
                pair = (theme_code_to_id[primary], scf_id)
                if pair not in seen:
                    seen.add(pair)
                    triples.append((pair[0], scf_id, "primary"))
            for supporting in rule.get("supporting", []):
                if supporting in theme_code_to_id:
                    pair = (theme_code_to_id[supporting], scf_id)
                    if pair not in seen:
                        seen.add(pair)
                        triples.append((pair[0], scf_id, "supporting"))
    return triples


async def _recompute_theme_mappings(
    session: AsyncSession, themes_json: dict, version: str, now: datetime
) -> int:
    """Wipe + rebuild capability_theme_mappings from the in-transaction control
    rows. The only DELETE in this module — the mapping table is derived data
    with no org FKs into it (plan §4.2.4); the four catalog entity tables are
    never deleted from."""
    await session.flush()  # pending entity upserts must be visible to the selects

    await session.execute(delete(CapabilityThemeMapping))

    result = await session.execute(select(CapabilityTheme))
    theme_code_to_id = {row.theme_code: row.id for row in result.scalars().all()}

    result = await session.execute(select(SCFCatalogControl))
    controls = result.scalars().all()

    triples = compute_theme_mappings(
        controls, themes_json.get("nist_family_mappings", {}), theme_code_to_id
    )
    for theme_id, scf_id, relevance in triples:
        session.add(
            CapabilityThemeMapping(
                theme_id=theme_id,
                scf_id=scf_id,
                relevance=relevance,
                catalog_version=version,
                updated_at=now,
            )
        )
    return len(triples)


# ---------------------------------------------------------------------------
# Superseded pairings (plan §4.2.3/4 — validated here; no DB FK by design)
# ---------------------------------------------------------------------------


def _parse_pairings(run: CatalogImportRun) -> List[SupersededPairing]:
    raw = run.superseded_pairings or []
    return [SupersededPairing.model_validate(item) for item in raw]


async def _apply_pairings(
    session: AsyncSession,
    pairings: List[SupersededPairing],
    deprecated_controls: Dict[str, Any],
    version: str,
    now: datetime,
) -> None:
    """Write admin-confirmed successors onto the run's deprecated control rows."""
    if not pairings:
        return
    successor_ids = sorted(
        {p.superseded_by for p in pairings if p.superseded_by is not None}
    )
    successors: Dict[str, Any] = {}
    if successor_ids:
        result = await session.execute(
            select(SCFCatalogControl).where(SCFCatalogControl.scf_id.in_(successor_ids))
        )
        successors = {row.scf_id: row for row in result.scalars().all()}

    invalid = []
    for pairing in pairings:
        target = deprecated_controls.get(pairing.deprecated_scf_id)
        if target is None:
            # Pairing for a control this run does not deprecate — stale UI state.
            invalid.append(pairing.deprecated_scf_id)
            continue
        if pairing.superseded_by is None:
            target.superseded_by = None  # explicit "no successor"
            continue
        successor = successors.get(pairing.superseded_by)
        if successor is None or getattr(successor, "status", "active") != "active":
            invalid.append(f"{pairing.deprecated_scf_id}->{pairing.superseded_by}")
            continue
        target.superseded_by = pairing.superseded_by
        _touch(target, version, now)

    if invalid:
        raise PairingValidationError(
            f"invalid superseded pairings: {', '.join(invalid)}", invalid
        )


# ---------------------------------------------------------------------------
# Apply (plan §4.2.4)
# ---------------------------------------------------------------------------


async def apply_catalog_run(
    session: AsyncSession,
    run: CatalogImportRun,
    detail: DiffDetail,
    themes_json: Optional[dict] = None,
) -> CatalogApplyReport:
    """Apply a staged diff to the live catalog in one advisory-locked transaction.

    Commits on success (run flipped to 'applied' atomically with the catalog);
    on any failure rolls back and re-raises — catalog untouched.
    ``themes_json`` is injectable for tests; defaults to the curated file.
    """
    if detail.to_version != run.to_version:
        raise ApplyConsistencyError(
            f"diff detail targets {detail.to_version!r} but run "
            f"{run.id} expects {run.to_version!r}"
        )

    to_version = detail.to_version
    now = _now()
    report = CatalogApplyReport(
        run_id=str(run.id),
        action="applied",
        from_version=detail.from_version,
        to_version=to_version,
    )

    try:
        await acquire_catalog_lock(session)

        themes_data = themes_json if themes_json is not None else _load_capability_themes_json()

        deprecated_controls: Dict[str, Any] = {}
        for spec in ENTITY_SPECS:
            diff = detail.entities.get(spec.entity)
            if diff is None:
                continue
            counts = EntityApplyCounts()
            report.entities[spec.entity.value] = counts

            touched_keys = (
                [e.key for e in diff.added]
                + [e.key for e in diff.changed]
                + [e.key for e in diff.deprecated]
                + [e.key for e in diff.resurrected]
            )
            rows = await _load_rows_by_key(session, spec, touched_keys)

            for added in diff.added:
                row = rows.get(added.key)
                if row is None:
                    # Explicit-column INSERT (the allowlist filter IS the
                    # preservation guarantee for excluded columns).
                    row = spec.model(**{spec.key_attr: added.key})
                    session.add(row)
                    rows[added.key] = row
                # Present already => idempotent re-apply; update the same columns.
                _set_allowed_fields(row, added.data, spec.columns)
                row.status = "active"
                row.retired_in_version = None
                _touch(row, to_version, now)
                counts.added += 1

            for changed in diff.changed:
                row = rows.get(changed.key)
                if row is None:
                    raise ApplyConsistencyError(
                        f"{spec.entity.value}: changed row {changed.key!r} missing "
                        f"from live catalog — staged diff is stale"
                    )
                _set_allowed_fields(
                    row,
                    {name: fc.new for name, fc in changed.fields.items()},
                    spec.columns,
                )
                _touch(row, to_version, now)
                counts.changed += 1

            for deprecated in diff.deprecated:
                row = rows.get(deprecated.key)
                if row is None:
                    raise ApplyConsistencyError(
                        f"{spec.entity.value}: deprecated row {deprecated.key!r} "
                        f"missing from live catalog — staged diff is stale"
                    )
                # Status flip, NEVER a DELETE (plan §4.2.4).
                row.status = "deprecated"
                row.retired_in_version = to_version
                _touch(row, to_version, now)
                counts.deprecated += 1
                if spec.entity is CatalogEntityType.CONTROLS:
                    deprecated_controls[deprecated.key] = row

            for resurrected in diff.resurrected:
                row = rows.get(resurrected.key)
                if row is None:
                    raise ApplyConsistencyError(
                        f"{spec.entity.value}: resurrected row {resurrected.key!r} "
                        f"missing from live catalog — staged diff is stale"
                    )
                row.status = "active"
                row.retired_in_version = None
                row.superseded_by = None
                _set_allowed_fields(
                    row,
                    {name: fc.new for name, fc in resurrected.fields.items()},
                    spec.columns,
                )
                _touch(row, to_version, now)
                counts.resurrected += 1

        await _apply_pairings(
            session, _parse_pairings(run), deprecated_controls, to_version, now
        )

        report.themes_upserted = await _upsert_themes(
            session, themes_data.get("themes", []), to_version, now
        )
        report.mappings_recomputed = await _recompute_theme_mappings(
            session, themes_data, to_version, now
        )

        run.status = "applied"
        run.completed_at = now
        run.updated_at = now

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "Catalog apply committed: run %s %s -> %s", run.id, detail.from_version, to_version
    )
    return report


# ---------------------------------------------------------------------------
# Revert (plan §4.2.6)
# ---------------------------------------------------------------------------


async def _check_revert_allowed(session: AsyncSession, run: CatalogImportRun) -> None:
    if run.status != "applied":
        raise RevertNotLatestError(
            f"run {run.id} has status {run.status!r}; only an applied run can revert"
        )

    result = await session.execute(
        select(CatalogImportRun).where(CatalogImportRun.status == "applied")
    )
    for other in result.scalars().all():
        if other.id == run.id:
            continue
        if (other.completed_at or other.created_at) > (run.completed_at or run.created_at):
            raise RevertNotLatestError(
                f"run {run.id} is not the latest applied run "
                f"(run {other.id} applied later); revert refused"
            )

    result = await session.execute(select(OrganizationCatalogState))
    blockers = sorted(
        str(state.organization_id)
        for state in result.scalars().all()
        if state.reconciled_catalog_version == run.to_version
    )
    if blockers:
        raise RevertBlockedError(run.to_version, blockers)


async def revert_catalog_run(
    session: AsyncSession,
    run: CatalogImportRun,
    detail: DiffDetail,
    themes_json: Optional[dict] = None,
) -> CatalogApplyReport:
    """Invert an applied run from its stored diff detail (latest applied only).

    Refusals are typed (RevertBlockedError lists the pinned organisations;
    RevertNotLatestError for ordering/status) and leave the run untouched.
    Same single-transaction + advisory-lock discipline as apply.
    """
    if detail.to_version != run.to_version:
        raise ApplyConsistencyError(
            f"diff detail targets {detail.to_version!r} but run "
            f"{run.id} expects {run.to_version!r}"
        )

    from_version = detail.from_version
    now = _now()
    report = CatalogApplyReport(
        run_id=str(run.id),
        action="reverted",
        from_version=detail.from_version,
        to_version=detail.to_version,
    )

    try:
        # Lock BEFORE the guards: org reconciliation (WP2c) takes the same key,
        # so an org cannot pin itself to to_version between check and revert.
        await acquire_catalog_lock(session)

        await _check_revert_allowed(session, run)

        themes_data = themes_json if themes_json is not None else _load_capability_themes_json()

        for spec in ENTITY_SPECS:
            # REVERTED_ENTITIES only: the controls entity's changed-field
            # records carry the framework_mappings column (revert authority).
            diff = detail.entities.get(spec.entity)
            if diff is None:
                continue
            counts = EntityApplyCounts()
            report.entities[spec.entity.value] = counts

            touched_keys = (
                [e.key for e in diff.added]
                + [e.key for e in diff.changed]
                + [e.key for e in diff.deprecated]
                + [e.key for e in diff.resurrected]
            )
            rows = await _load_rows_by_key(session, spec, touched_keys)

            for changed in diff.changed:
                row = rows.get(changed.key)
                if row is None:
                    raise ApplyConsistencyError(
                        f"{spec.entity.value}: changed row {changed.key!r} missing "
                        f"from live catalog — cannot restore old values"
                    )
                _set_allowed_fields(
                    row,
                    {name: fc.old for name, fc in changed.fields.items()},
                    spec.columns,
                )
                _touch(row, from_version, now)
                counts.changed += 1

            for added in diff.added:
                row = rows.get(added.key)
                if row is None:
                    # Already absent (never applied / hand-removed): nothing to
                    # deprecate, and we never invent rows on revert.
                    continue
                # Never DELETE — run-added rows are deprecated (plan §4.2.6).
                row.status = "deprecated"
                row.retired_in_version = run.to_version
                _touch(row, from_version, now)
                counts.added += 1

            for deprecated in diff.deprecated:
                row = rows.get(deprecated.key)
                if row is None:
                    raise ApplyConsistencyError(
                        f"{spec.entity.value}: deprecated row {deprecated.key!r} "
                        f"missing from live catalog — cannot re-activate"
                    )
                row.status = "active"
                row.retired_in_version = None
                row.superseded_by = None  # pairings were written by this run's apply
                _touch(row, from_version, now)
                counts.deprecated += 1

            for resurrected in diff.resurrected:
                row = rows.get(resurrected.key)
                if row is None:
                    raise ApplyConsistencyError(
                        f"{spec.entity.value}: resurrected row {resurrected.key!r} "
                        f"missing from live catalog — cannot re-deprecate"
                    )
                row.status = "deprecated"
                # Pre-run retired_in_version/superseded_by are not in the diff;
                # left NULL (documented loss, module docstring).
                row.retired_in_version = None
                _set_allowed_fields(
                    row,
                    {name: fc.old for name, fc in resurrected.fields.items()},
                    spec.columns,
                )
                _touch(row, from_version, now)
                counts.resurrected += 1

        report.themes_upserted = await _upsert_themes(
            session, themes_data.get("themes", []), from_version, now
        )
        report.mappings_recomputed = await _recompute_theme_mappings(
            session, themes_data, from_version, now
        )

        run.status = "reverted"
        run.updated_at = now

        await session.commit()
    except RevertRefusedError:
        # Refusals happen before any mutation; leave the session clean.
        await session.rollback()
        raise
    except Exception:
        await session.rollback()
        raise

    logger.info(
        "Catalog revert committed: run %s back to %s", run.id, detail.from_version
    )
    return report
