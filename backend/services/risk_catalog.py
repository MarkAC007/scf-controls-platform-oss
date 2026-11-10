"""The SCF Risk and Threat Catalogs: upsert and the control-mapping refresh.

Two callers share this module so they cannot drift:

* ``catalog_seeder`` / the ``refresh-risk-catalog`` admin command load the
  catalogs from the extractor's ``risks.json`` / ``threats.json`` and rewrite
  the risk/threat mapping columns of existing control rows from
  ``control_guidance.json``;
* ``services.catalog_diff`` / ``catalog_apply`` diff and apply the same two
  entities through the generic entity path, using the column lists here.

The refresh is the augmentation path for an install whose catalogue is already
on a version that has no "Risk Threat Summary" columns (2026.3+): it touches
exactly four columns on ``scf_catalog_controls`` (``risk_codes``,
``threat_codes``, ``risk_ratings``, ``threat_ratings``) plus ``updated_at``,
and never reads or writes an organisation table. Deprecated control rows,
which the new extraction no longer carries, keep their codes but are remapped
through the publisher's renumbering (``old_code``) and lose codes the new
catalogue does not define, so a code never means two different things across
active and deprecated rows.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from catalog_models import SCFCatalogControl, SCFCatalogRisk, SCFCatalogThreat

logger = logging.getLogger(__name__)

# The ONLY data columns the diff compares and apply/refresh write, per entity.
RISK_COMPARED_FIELDS = ("grouping", "old_code", "title", "description", "nist_csf_function")
THREAT_COMPARED_FIELDS = ("grouping", "title", "description")

# The control columns the refresh rewrites. Everything else on the row is
# untouched by construction: _assign() only ever sees these names.
CONTROL_MAPPING_FIELDS = ("risk_codes", "threat_codes", "risk_ratings", "threat_ratings")

RISKS_FILE = "risks.json"
THREATS_FILE = "threats.json"
CONTROLS_FILE = "control_guidance.json"
META_FILE = "catalog_meta.json"


class RiskCatalogEmptyExtraction(ValueError):
    """risks.json or threats.json carries no entries: refusing to deprecate everything."""


class RiskCatalogVersionMismatch(ValueError):
    """The extraction describes a different catalogue version than the live rows."""

    def __init__(self, file_version: str, live_version: Optional[str]):
        self.file_version = file_version
        self.live_version = live_version
        super().__init__(
            f"extraction is catalog version {file_version!r} but the live catalog "
            f"is {live_version!r}"
        )


def risk_to_columns(risk: dict) -> dict:
    """``risks.json`` entry -> SCFCatalogRisk data columns (mirrors the seeder)."""
    return {
        "grouping": risk.get("grouping") or "Ungrouped",
        "old_code": (risk.get("old_code") or None),
        "title": risk.get("title") or risk.get("code") or "",
        "description": risk.get("description"),
        "nist_csf_function": risk.get("nist_csf_function"),
    }


def threat_to_columns(threat: dict) -> dict:
    return {
        "grouping": threat.get("grouping") or "Ungrouped",
        "title": threat.get("title") or threat.get("code") or "",
        "description": threat.get("description"),
    }


def _now() -> datetime:
    return datetime.utcnow()


def _assign(row: Any, values: Dict[str, Any], allowed: Iterable[str]) -> bool:
    """Set the allowed columns that differ. Returns whether anything changed."""
    changed = False
    for name in allowed:
        if name not in values:
            continue
        if getattr(row, name, None) != values[name]:
            setattr(row, name, values[name])
            changed = True
    return changed


@dataclass
class UpsertCounts:
    added: int = 0
    updated: int = 0
    deprecated: int = 0
    unchanged: int = 0


async def _upsert_catalog(
    session: AsyncSession,
    model: type,
    entries: List[dict],
    to_columns,
    compared: tuple,
    version: str,
    now: datetime,
) -> UpsertCounts:
    counts = UpsertCounts()
    wanted = {}
    for entry in entries:
        code = str(entry.get("code") or "").strip().upper()
        if code:
            wanted[code] = to_columns(entry)

    live = {row.code: row for row in (await session.execute(select(model))).scalars().all()}

    for code, columns in wanted.items():
        row = live.get(code)
        if row is None:
            row = model(code=code, **columns)
            row.status = "active"
            row.retired_in_version = None
            row.catalog_version = version
            row.updated_at = now
            session.add(row)
            counts.added += 1
            continue
        changed = _assign(row, columns, compared)
        if row.status != "active":
            row.status = "active"
            row.retired_in_version = None
            changed = True
        if row.catalog_version != version:
            row.catalog_version = version
            changed = True
        if changed:
            row.updated_at = now
            counts.updated += 1
        else:
            counts.unchanged += 1

    # Absent from the new catalogue: a status flip, never a DELETE.
    for code, row in live.items():
        if code in wanted or row.status == "deprecated":
            continue
        row.status = "deprecated"
        row.retired_in_version = version
        row.updated_at = now
        counts.deprecated += 1

    return counts


async def upsert_risks(
    session: AsyncSession, risks: List[dict], version: str, now: Optional[datetime] = None
) -> UpsertCounts:
    return await _upsert_catalog(
        session, SCFCatalogRisk, risks, risk_to_columns, RISK_COMPARED_FIELDS, version, now or _now()
    )


async def upsert_threats(
    session: AsyncSession, threats: List[dict], version: str, now: Optional[datetime] = None
) -> UpsertCounts:
    return await _upsert_catalog(
        session, SCFCatalogThreat, threats, threat_to_columns, THREAT_COMPARED_FIELDS, version, now or _now()
    )


def renumbering(risks: List[dict]) -> Dict[str, str]:
    """old code -> new code, from the publisher's 'Old Risk #' column."""
    out = {}
    for risk in risks:
        code = str(risk.get("code") or "").upper()
        old = str(risk.get("old_code") or "").upper()
        if code and old and old != code:
            out[old] = code
    return out


def _remap_codes(codes: Optional[list], remap: Dict[str, str], valid: set) -> list:
    out: List[str] = []
    for code in codes or []:
        new = remap.get(code, code)
        if new in valid and new not in out:
            out.append(new)
    return out


def _remap_ratings(ratings: Optional[dict], remap: Dict[str, str], valid: set) -> Optional[dict]:
    if not ratings:
        return ratings
    out = {}
    for code, rating in ratings.items():
        new = remap.get(code, code)
        if new in valid and new not in out:
            out[new] = rating
    return out


@dataclass
class MappingRefreshCounts:
    controls_updated: int = 0
    controls_unchanged: int = 0
    deprecated_remapped: int = 0
    extraction_only: List[str] = field(default_factory=list)  # in the JSON, not in the DB


async def refresh_control_risk_mappings(
    session: AsyncSession,
    controls: List[dict],
    risks: List[dict],
    threats: List[dict],
    now: Optional[datetime] = None,
    *,
    version: Optional[str] = None,
) -> MappingRefreshCounts:
    """Rewrite the four mapping columns on existing control rows.

    Rows present in ``controls`` (the extraction) take its mapping verbatim.
    Rows absent from it are deprecated. Those retired in ``version`` itself
    carry the PREVIOUS catalogue's codes, which is exactly what the catalogue's
    ``Old Risk #`` column relates to the current codes, so they are remapped
    through it. Rows retired earlier carry older codes the column says nothing
    about, so they are only stripped of codes the catalogue no longer defines.
    (``version=None`` remaps every deprecated row; callers that know the live
    version pass it.) No row is inserted or deleted and no other column is written.
    """
    now = now or _now()
    counts = MappingRefreshCounts()
    by_id = {str(c.get("scf_id") or "").strip(): c for c in controls if c.get("scf_id")}
    valid_risks = {str(r.get("code") or "").upper() for r in risks if r.get("code")}
    valid_threats = {str(t.get("code") or "").upper() for t in threats if t.get("code")}
    remap = renumbering(risks)

    rows = (await session.execute(select(SCFCatalogControl))).scalars().all()
    seen = set()
    for row in rows:
        seen.add(row.scf_id)
        ctrl = by_id.get(row.scf_id)
        if ctrl is not None:
            mapping = ctrl.get("risk_threat_mapping") or {}
            values = {
                "risk_codes": list(mapping.get("risk_codes") or []),
                "threat_codes": list(mapping.get("threat_codes") or []),
                "risk_ratings": mapping.get("risk_ratings"),
                "threat_ratings": mapping.get("threat_ratings"),
            }
            if _assign(row, values, CONTROL_MAPPING_FIELDS):
                row.updated_at = now
                counts.controls_updated += 1
            else:
                counts.controls_unchanged += 1
            continue

        row_remap = remap if (version is None or row.retired_in_version == version) else {}
        values = {
            "risk_codes": _remap_codes(row.risk_codes, row_remap, valid_risks),
            "threat_codes": _remap_codes(row.threat_codes, {}, valid_threats),
            "risk_ratings": _remap_ratings(row.risk_ratings, row_remap, valid_risks),
            "threat_ratings": _remap_ratings(row.threat_ratings, {}, valid_threats),
        }
        if _assign(row, values, CONTROL_MAPPING_FIELDS):
            row.updated_at = now
            counts.deprecated_remapped += 1
        else:
            counts.controls_unchanged += 1

    counts.extraction_only = sorted(set(by_id) - seen)
    return counts


# ---------------------------------------------------------------------------
# The refresh command's orchestration (CLI + anything else that has a data dir)
# ---------------------------------------------------------------------------


@dataclass
class RefreshReport:
    file_version: str
    live_version: Optional[str]
    risks: UpsertCounts
    threats: UpsertCounts
    controls: MappingRefreshCounts
    dry_run: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def load_extraction(data_dir: Path) -> dict:
    """The four extraction files the refresh needs, or FileNotFoundError."""
    data_dir = Path(data_dir)
    out = {}
    for key, name in (("meta", META_FILE), ("risks", RISKS_FILE), ("threats", THREATS_FILE), ("controls", CONTROLS_FILE)):
        path = data_dir / name
        if not path.exists():
            raise FileNotFoundError(f"{name} not found in {data_dir}")
        with open(path) as f:
            out[key] = json.load(f)
    return out


async def refresh_risk_catalog(
    session: AsyncSession,
    data_dir: Path,
    *,
    live_version: Optional[str],
    allow_version_mismatch: bool = False,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> RefreshReport:
    """Upsert the risk and threat catalogs and refresh every control's mapping.

    Refuses when the extraction's catalogue version differs from the live
    one unless told otherwise: a mapping written from the wrong workbook would
    describe controls the live rows are not. ``dry_run`` computes and reports,
    then rolls back.
    """
    files = load_extraction(data_dir)
    file_version = str(files["meta"].get("catalog_version") or files["risks"].get("catalog_version") or "")
    if live_version is not None and file_version != live_version and not allow_version_mismatch:
        raise RiskCatalogVersionMismatch(file_version, live_version)

    now = now or _now()
    version = live_version or file_version
    risks = files["risks"].get("risks", [])
    threats = files["threats"].get("threats", [])
    controls = files["controls"].get("controls", [])
    if not risks or not threats:
        # An upsert from an empty list would deprecate the whole live
        # catalogue. An empty file means the workbook had no such sheet (a
        # pre-2026.3 release) or the extraction failed; neither is a reason
        # to retire 65 risks.
        raise RiskCatalogEmptyExtraction(
            f"{RISKS_FILE if not risks else THREATS_FILE} in {data_dir} carries no entries; "
            "refusing to deprecate the live catalogue. Re-extract a workbook that has the "
            "Risk Catalog and Threat Catalog sheets (SCF 2026.3 or later)."
        )

    risk_counts = await upsert_risks(session, risks, version, now)
    threat_counts = await upsert_threats(session, threats, version, now)
    await session.flush()
    control_counts = await refresh_control_risk_mappings(
        session, controls, risks, threats, now, version=version
    )

    if dry_run:
        await session.rollback()
    else:
        await session.commit()

    report = RefreshReport(file_version, live_version, risk_counts, threat_counts, control_counts, dry_run)
    logger.info("risk catalog refresh: %s", report.as_dict())
    return report
