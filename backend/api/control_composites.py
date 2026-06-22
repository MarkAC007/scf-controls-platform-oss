"""
Control Assessment Composite API endpoints (M3, #575, PR 2).

Read-only API for ``ControlAssessmentComposite`` rows produced by the rollup
service shipped in PR 1 (#588). Composite recomputes already fire silently
in staging on every terminal-status ``EvidenceWindowAssessment`` write; this
module wires the consumer surface dashboards (M4) and the upcoming KSI
integration (PR 3) will read from.

Endpoints:
  GET /organizations/{org_id}/controls/{scf_id}/assessment-composite        — Single (ISC-15)
  GET /organizations/{org_id}/controls/assessment-composites                — List   (ISC-16)

Both endpoints return ETag headers per ISC-17 and honour ``If-None-Match``
returning 304 when the client already has the current representation.
The list endpoint is wrapped in a per-org 30s in-memory TTL cache.

Spec:    /tmp/m3-design-spec.md  §4 (ISC-15..17), §8a (RBAC).
RBAC:    viewer or higher via ``require_org_role("viewer")`` (§8a).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy import and_, asc, case, desc, nulls_last, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import OrgMembership, require_org_viewer
from catalog_models import SCFCatalogControl
from database import get_db
from models import ControlAssessmentComposite, EvidenceWindowAssessment
from schemas import (
    CompositeMandatoryGap,
    CompositeWindowSummary,
    ControlAssessmentCompositeListResponse,
    ControlAssessmentCompositeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["control-assessment-composites"])


# ---------------------------------------------------------------------------
# Status-band ordering — ISC-16 stable sort.
# ---------------------------------------------------------------------------
# Spec: "stable ordering by (composite_status band, -composite_score, scf_id)".
# Spec under-specifies the band order itself; we choose worst-first so that
# dashboards render the rows that matter most at the top of every page. This
# constant is the single source of truth used by both the SQL CASE expression
# and the cursor encoding.
_STATUS_BAND: Dict[str, int] = {
    "insufficient": 1,
    "insufficient_sample": 2,
    "partial": 3,
    "pending": 4,
    "no_evidence": 5,
    "sufficient": 6,
}
_DEFAULT_BAND = 99  # unknown statuses sort to the end deterministically


# ---------------------------------------------------------------------------
# Pagination — opaque cursor.
# ---------------------------------------------------------------------------
# Cursor encodes the sort tuple of the LAST row of the previous page so the
# next page can resume strictly after it. We negate ``composite_score`` in the
# cursor payload so the cursor key sequence is monotonically non-decreasing
# (band ASC, neg-score ASC, scf_id ASC); this makes the SQL "row > cursor"
# semantics straightforward without a custom comparator.

_CURSOR_VERSION = 1  # bump on schema break


def _encode_cursor(band: int, score: Optional[Decimal], scf_id: str) -> str:
    """Encode sort tuple as a urlsafe-b64 JSON cursor.

    Stores ``score`` negated (None → null) so cursor compare is purely lex on
    the (band, neg_score, scf_id) tuple. ``Decimal`` values are stringified so
    round-trip equality holds across processes.
    """
    payload: Dict[str, Any] = {
        "v": _CURSOR_VERSION,
        "b": band,
        "n": (str(-score) if score is not None else None),
        "k": scf_id,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str) -> Tuple[int, Optional[Decimal], str]:
    """Decode a previously-issued cursor.

    Raises ``HTTPException(400)`` on malformed input — never silently coerces.
    """
    try:
        # restore base64 padding
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad)
        payload = json.loads(raw.decode("utf-8"))
        if payload.get("v") != _CURSOR_VERSION:
            raise ValueError("cursor version mismatch")
        band = int(payload["b"])
        neg_score_str = payload.get("n")
        # Return the negated value as stored — caller un-negates if it wants
        # the original score. Keeps cursor compare semantics symmetric with
        # _encode_cursor.
        neg_score = Decimal(neg_score_str) if neg_score_str is not None else None
        scf_id = str(payload["k"])
        return band, neg_score, scf_id
    except Exception as exc:  # noqa: BLE001 — cursor bytes are fully untrusted
        logger.debug("Rejected malformed cursor: %s", exc)
        raise HTTPException(status_code=400, detail="Malformed cursor") from exc


# ---------------------------------------------------------------------------
# ETag — ISC-17.
# ---------------------------------------------------------------------------

def _etag_for_row(row: ControlAssessmentComposite) -> str:
    """Per-row ETag = sha256(computation_version || computed_at).

    Format note: ``computed_at`` is rendered with isoformat for stability;
    micro-second precision is preserved by SQLAlchemy's ``DateTime`` column.
    """
    base = f"{int(row.computation_version)}:{row.computed_at.isoformat()}"
    digest = hashlib.sha256(base.encode("utf-8")).hexdigest()
    return f'W/"{digest}"'


def _etag_for_page(rows: List[ControlAssessmentComposite], next_cursor: Optional[str]) -> str:
    """Page-level ETag — folds every row's ETag plus the page cursor.

    Two requests with identical filters and unchanged data produce the same
    ETag; any change to a row, ordering, or page boundary changes it.
    """
    h = hashlib.sha256()
    for r in rows:
        h.update(f"{r.scf_id}|{int(r.computation_version)}|{r.computed_at.isoformat()}\n".encode("utf-8"))
    h.update(b"|cursor=")
    h.update((next_cursor or "").encode("utf-8"))
    return f'W/"{h.hexdigest()}"'


def _etag_match(if_none_match: Optional[str], etag: str) -> bool:
    """Honour weak-ETag matching per RFC 7232 §3.2.

    ``If-None-Match`` may carry a comma-separated list and may include or omit
    the ``W/`` prefix. We strip both and compare bare digests.
    """
    if not if_none_match:
        return False

    def _strip(token: str) -> str:
        token = token.strip()
        if token.startswith("W/"):
            token = token[2:]
        return token.strip().strip('"')

    candidates = {_strip(t) for t in if_none_match.split(",")}
    return _strip(etag) in candidates or "*" in candidates


# ---------------------------------------------------------------------------
# Per-org TTL cache — ISC-17 (30s).
# ---------------------------------------------------------------------------
# Prefer ``cachetools.TTLCache`` if installed; otherwise a tiny dict-with-
# timestamps shim. Cache key is the entire query signature so concurrent
# users with different filter combinations don't collide.

try:  # pragma: no cover — optional dep, exercised in fallback path below
    from cachetools import TTLCache  # type: ignore

    _list_cache: Any = TTLCache(maxsize=2048, ttl=30.0)
    _list_cache_lock = threading.Lock()

    def _cache_get(key: tuple) -> Any:
        with _list_cache_lock:
            return _list_cache.get(key)

    def _cache_set(key: tuple, value: Any) -> None:
        with _list_cache_lock:
            _list_cache[key] = value

except ImportError:
    class _TTLCacheShim:
        """Minimal TTL cache: dict + per-key timestamps + lazy eviction.

        Used when ``cachetools`` is not available. Not LRU; we evict expired
        entries on read and stochastically cap size on write.
        """

        def __init__(self, maxsize: int = 2048, ttl: float = 30.0) -> None:
            self.maxsize = maxsize
            self.ttl = ttl
            self._store: Dict[tuple, Tuple[float, Any]] = {}
            self._lock = threading.Lock()

        def get(self, key: tuple) -> Any:
            with self._lock:
                entry = self._store.get(key)
                if not entry:
                    return None
                expires_at, value = entry
                if expires_at < time.monotonic():
                    self._store.pop(key, None)
                    return None
                return value

        def set(self, key: tuple, value: Any) -> None:
            with self._lock:
                if len(self._store) >= self.maxsize:
                    # Stochastic eviction — drop the first 10% of expired or
                    # oldest entries. Cheaper than a full LRU walk for our
                    # 30s TTL window.
                    now = time.monotonic()
                    drop = max(1, len(self._store) // 10)
                    expired = [k for k, (exp, _) in self._store.items() if exp < now]
                    for k in expired[:drop]:
                        self._store.pop(k, None)
                    if len(self._store) >= self.maxsize:
                        for k in list(self._store.keys())[:drop]:
                            self._store.pop(k, None)
                self._store[key] = (time.monotonic() + self.ttl, value)

    _list_cache_shim = _TTLCacheShim(maxsize=2048, ttl=30.0)

    def _cache_get(key: tuple) -> Any:
        return _list_cache_shim.get(key)

    def _cache_set(key: tuple, value: Any) -> None:
        _list_cache_shim.set(key, value)


# ---------------------------------------------------------------------------
# Helpers — composite row → response.
# ---------------------------------------------------------------------------

def _band_for(row: ControlAssessmentComposite) -> int:
    return _STATUS_BAND.get(row.composite_status, _DEFAULT_BAND)


async def _fetch_windows_for_composite(
    db: AsyncSession,
    organization_id: UUID,
    included_window_ids: List[Any],
) -> List[CompositeWindowSummary]:
    """JOIN-on-read: pull the windows referenced by ``included_window_ids``.

    Per ISC-15 the response carries an array of ``(evidence_id, window_id,
    status, relevance_score)`` tuples — provenance for the composite that the
    UI uses to drill into the underlying assessments.
    """
    if not included_window_ids:
        return []

    # JSONB list may surface as plain strings; coerce to UUID for the query.
    window_uuids: List[UUID] = []
    for raw in included_window_ids:
        if isinstance(raw, UUID):
            window_uuids.append(raw)
        else:
            try:
                window_uuids.append(UUID(str(raw)))
            except (TypeError, ValueError):
                logger.warning("Skipping malformed window id in composite: %r", raw)

    if not window_uuids:
        return []

    result = await db.execute(
        select(
            EvidenceWindowAssessment.id,
            EvidenceWindowAssessment.evidence_id,
            EvidenceWindowAssessment.status,
            EvidenceWindowAssessment.relevance_score,
        ).where(
            and_(
                EvidenceWindowAssessment.organization_id == organization_id,
                EvidenceWindowAssessment.id.in_(window_uuids),
            )
        )
    )
    rows = result.all()
    return [
        CompositeWindowSummary(
            evidence_id=row.evidence_id,
            window_id=row.id,
            status=row.status,
            relevance_score=(float(row.relevance_score) if row.relevance_score is not None else None),
        )
        for row in rows
    ]


def _missing_evidence_ids(row: ControlAssessmentComposite) -> List[str]:
    """Surface evidence IDs flagged with ``missing_window`` in mandatory_gaps.

    The response shape (ISC-15) splits ``included_evidence_ids`` (the ones
    that did contribute) from ``missing_evidence_ids`` (the ones we expected
    but never found). The composite row carries this implicitly inside
    ``mandatory_gaps`` — surface it explicitly here.
    """
    gaps = row.mandatory_gaps or []
    return sorted(
        {
            g.get("evidence_id")
            for g in gaps
            if isinstance(g, dict)
            and g.get("reason") == "missing_window"
            and g.get("evidence_id")
        }
    )


def _build_response(
    row: ControlAssessmentComposite,
    windows: List[CompositeWindowSummary],
) -> ControlAssessmentCompositeResponse:
    return ControlAssessmentCompositeResponse(
        scf_id=row.scf_id,
        composite_status=row.composite_status,
        composite_score=(float(row.composite_score) if row.composite_score is not None else None),
        included_evidence_ids=list(row.included_evidence_ids or []),
        missing_evidence_ids=_missing_evidence_ids(row),
        mandatory_gaps=[
            CompositeMandatoryGap(**g) if isinstance(g, dict) else g
            for g in (row.mandatory_gaps or [])
        ],
        computation_version=int(row.computation_version),
        computed_at=row.computed_at,
        windows=windows,
    )


# ---------------------------------------------------------------------------
# GET single composite — ISC-15.
# ---------------------------------------------------------------------------

@router.get(
    "/organizations/{org_id}/controls/{scf_id}/assessment-composite",
    response_model=ControlAssessmentCompositeResponse,
    summary="Get the assessment composite for one control",
    description=(
        "Return the rolled-up assessment composite for a single SCF control "
        "in the given organisation. ``windows`` is computed at read time via "
        "JOIN against ``evidence_window_assessments`` for the window IDs the "
        "composite folded in. Returns 404 if no composite row exists yet "
        "(the rollup service produces rows asynchronously). ETag is set on "
        "every 200 response and honoured for ``If-None-Match`` (304).\n\n"
        "ISC-15, ISC-17. RBAC: viewer or higher (§8a)."
    ),
)
async def get_control_composite(
    org_id: UUID,
    scf_id: str,
    response: Response,
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ControlAssessmentComposite).where(
            and_(
                ControlAssessmentComposite.organization_id == org_id,
                ControlAssessmentComposite.scf_id == scf_id,
            )
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="No composite for this control")

    etag = _etag_for_row(row)
    if _etag_match(if_none_match, etag):
        # 304 must carry the ETag and no body — return Response directly.
        return Response(status_code=304, headers={"ETag": etag})

    windows = await _fetch_windows_for_composite(
        db, org_id, list(row.included_window_ids or [])
    )
    response.headers["ETag"] = etag
    return _build_response(row, windows)


# ---------------------------------------------------------------------------
# GET list of composites — ISC-16, ISC-17.
# ---------------------------------------------------------------------------


def _parse_status_filter(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    statuses = [s.strip() for s in raw.split(",") if s.strip()]
    invalid = [s for s in statuses if s not in _STATUS_BAND]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown status value(s): {invalid}. Allowed: {sorted(_STATUS_BAND)}",
        )
    return statuses or None


@router.get(
    "/organizations/{org_id}/controls/assessment-composites",
    response_model=ControlAssessmentCompositeListResponse,
    summary="List assessment composites for an organisation",
    description=(
        "Return composites for every SCF control in the org with optional "
        "``status``/``domain``/``computation_version`` filters. Cursor-paginated; "
        "the server orders rows by (status band worst-first, descending score, "
        "scf_id). Pass the response's ``next_cursor`` back to get the next "
        "page.\n\n"
        "Cache: per-org 30s in-memory TTL; ETag set on the page payload.\n\n"
        "ISC-16, ISC-17. RBAC: viewer or higher (§8a)."
    ),
)
async def list_control_composites(
    org_id: UUID,
    response: Response,
    status: Optional[str] = Query(
        default=None,
        description="Comma-separated composite_status values to include.",
    ),
    domain: Optional[str] = Query(
        default=None,
        description="Filter by SCFCatalogControl.scf_domain (e.g. 'BCD').",
    ),
    computation_version: Optional[int] = Query(
        default=None,
        ge=1,
        description="Restrict to composites computed at this algorithm version.",
    ),
    limit: int = Query(default=100, ge=1, le=500),
    cursor: Optional[str] = Query(default=None),
    if_none_match: Optional[str] = Header(default=None, alias="If-None-Match"),
    membership: OrgMembership = Depends(require_org_viewer),
    db: AsyncSession = Depends(get_db),
):
    statuses = _parse_status_filter(status)
    cache_key = (
        str(org_id),
        ",".join(sorted(statuses)) if statuses else "",
        domain or "",
        computation_version if computation_version is not None else 0,
        cursor or "",
        limit,
    )

    cached = _cache_get(cache_key)
    if cached is not None:
        # cached is a fully-built (payload_dict, etag) pair — applies to every
        # request with this signature within the 30s window.
        payload, etag = cached
        if _etag_match(if_none_match, etag):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return ControlAssessmentCompositeListResponse(**payload)

    # Build the band CASE for ordering and (when needed) for the status filter
    # fast-path. Using a CASE keeps ordering deterministic across PG versions
    # without depending on enum sort order.
    band_case = case(
        *((ControlAssessmentComposite.composite_status == s, b) for s, b in _STATUS_BAND.items()),
        else_=_DEFAULT_BAND,
    )

    stmt = select(ControlAssessmentComposite).where(
        ControlAssessmentComposite.organization_id == org_id
    )

    if statuses:
        stmt = stmt.where(ControlAssessmentComposite.composite_status.in_(statuses))

    if computation_version is not None:
        stmt = stmt.where(
            ControlAssessmentComposite.computation_version == computation_version
        )

    if domain:
        # Catalog row may be missing for stale composites; LEFT JOIN keeps the
        # query honest when the filter is unset, and the inner predicate is
        # only added when the user asked for a domain.
        stmt = stmt.join(
            SCFCatalogControl,
            SCFCatalogControl.scf_id == ControlAssessmentComposite.scf_id,
        ).where(SCFCatalogControl.scf_domain == domain)

    # Cursor — strict-tail filter on the (band, -score, scf_id) tuple.
    if cursor:
        c_band, c_neg_score, c_scf_id = _decode_cursor(cursor)
        # Recover original score from negated cursor value (None preserved).
        c_score = -c_neg_score if c_neg_score is not None else None

        # neg_score expression so we can compare lex against the cursor's
        # negated score (NULL sorts as the largest neg-score by convention).
        neg_score = -ControlAssessmentComposite.composite_score

        # We want rows STRICTLY AFTER the cursor in the (band ASC, neg_score
        # ASC NULLS LAST, scf_id ASC) ordering. Express as:
        #   band > c_band
        #   OR (band = c_band AND neg_score > c_neg_score)
        #   OR (band = c_band AND neg_score = c_neg_score AND scf_id > c_scf_id)
        # NULL handling: a NULL score row sorts after every non-NULL; the
        # explicit comparisons below encode that.
        if c_score is None:
            # Cursor was on a NULL-score row → only NULL-score rows with a
            # later scf_id within the same band, or any later band.
            tail = (band_case > c_band) | (
                (band_case == c_band)
                & (ControlAssessmentComposite.composite_score.is_(None))
                & (ControlAssessmentComposite.scf_id > c_scf_id)
            )
        else:
            tail = (
                (band_case > c_band)
                | (
                    (band_case == c_band)
                    & (ControlAssessmentComposite.composite_score.is_(None))
                )
                | (
                    (band_case == c_band)
                    & (neg_score > -c_score)
                )
                | (
                    (band_case == c_band)
                    & (ControlAssessmentComposite.composite_score == c_score)
                    & (ControlAssessmentComposite.scf_id > c_scf_id)
                )
            )
        stmt = stmt.where(tail)

    stmt = stmt.order_by(
        asc(band_case),
        nulls_last(desc(ControlAssessmentComposite.composite_score)),
        asc(ControlAssessmentComposite.scf_id),
    ).limit(limit + 1)  # +1 to detect another page without a count query

    result = await db.execute(stmt)
    rows: List[ControlAssessmentComposite] = list(result.scalars().all())

    has_more = len(rows) > limit
    page_rows = rows[:limit]

    next_cursor: Optional[str] = None
    if has_more and page_rows:
        last = page_rows[-1]
        next_cursor = _encode_cursor(
            band=_band_for(last),
            score=last.composite_score,
            scf_id=last.scf_id,
        )

    # Build the response items — each carries its own JOIN-on-read ``windows``
    # array. We do this in a loop rather than a giant join so individual rows
    # with many windows don't blow up the response payload silently.
    items: List[ControlAssessmentCompositeResponse] = []
    for row in page_rows:
        windows = await _fetch_windows_for_composite(
            db, org_id, list(row.included_window_ids or [])
        )
        items.append(_build_response(row, windows))

    payload_obj = ControlAssessmentCompositeListResponse(
        items=items,
        next_cursor=next_cursor,
    )
    etag = _etag_for_page(page_rows, next_cursor)
    _cache_set(cache_key, (payload_obj.model_dump(mode="json"), etag))

    if _etag_match(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag})

    response.headers["ETag"] = etag
    return payload_obj
