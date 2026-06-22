"""
Capability Themes API — KSI-aligned posture scoring endpoints.

Groups SCF controls into 11 capability themes and provides aggregated
posture scoring per theme for an organization's scoped controls.

Part of Epic #317: KSI-Aligned Platform Evolution
Issue #303: Capability Themes API endpoints with posture scoring
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, case, literal_column, text
from uuid import UUID
from typing import Optional
import logging

from database import get_db
from models import ScopedControl
from catalog_models import CapabilityTheme, CapabilityThemeMapping, SCFCatalogControl
from auth import require_org_role, OrgMembership
from schemas import (
    CapabilityThemePosture,
    CapabilityThemeResponse,
    CapabilityThemeListResponse,
    CapabilityThemeControlItem,
    CapabilityThemeControlsResponse,
    CapabilityThemeEvidencePosture,
    CapabilityThemeEvidencePostureResponse,
    CapabilityThemeScorecardItem,
    CapabilityThemeScorecardResponse,
)
from api.ksi_scoring import (
    band_for_axis,
    compute_ec,
    compute_eq,
    compute_eq_warning,
    compute_ic,
    compute_kps,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["capability_themes"])

# Maturity level string to numeric score mapping
MATURITY_SCORES = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}


def _compute_posture_percentage(posture: CapabilityThemePosture, scoped: int) -> float:
    """Compute posture percentage: (monitored + implemented) / (scoped - not_applicable) * 100.

    DEPRECATED (#549 Phase 3): retained for backward compat. Prefer the multi-axis
    fields populated via _compute_axis_bundle below.
    """
    denominator = scoped - posture.not_applicable
    if denominator <= 0:
        return 0.0
    return round((posture.monitored + posture.implemented) / denominator * 100, 1)


# Issue #549, Phase 1 — multi-axis scoring wiring


_EVIDENCE_METRICS_SQL = text("""
    WITH evidence_control_map AS (
        SELECT
            ce.evidence_id,
            jsonb_array_elements_text(ce.control_mappings) AS scf_id
        FROM scf_catalog_evidence ce
        WHERE jsonb_array_length(COALESCE(ce.control_mappings, '[]'::jsonb)) > 0
    ),
    evidence_theme_map AS (
        SELECT DISTINCT
            ecm.evidence_id,
            ecm.scf_id,
            ct.theme_code
        FROM evidence_control_map ecm
        JOIN capability_theme_mappings ctm ON ecm.scf_id = ctm.scf_id
        JOIN capability_themes ct ON ctm.theme_id = ct.id
    ),
    org_evidence AS (
        SELECT
            ef.evidence_id,
            ef.id AS file_id,
            COALESCE(ea.status, 'unassessed') AS assessment_status,
            ea.relevance_score
        FROM evidence_files ef
        LEFT JOIN evidence_assessments ea ON ea.evidence_file_id = ef.id
        WHERE ef.organization_id = :org_id
          AND ef.is_deleted = false
    )
    SELECT
        etm.theme_code,
        COUNT(DISTINCT etm.scf_id) FILTER (WHERE oe.file_id IS NOT NULL) AS controls_with_evidence,
        COUNT(DISTINCT oe.file_id) AS total_evidence_files,
        COUNT(DISTINCT oe.file_id) FILTER (WHERE oe.assessment_status = 'sufficient') AS sufficient_count,
        COUNT(DISTINCT oe.file_id) FILTER (WHERE oe.assessment_status = 'partial') AS partial_count,
        COUNT(DISTINCT oe.file_id) FILTER (WHERE oe.assessment_status = 'insufficient') AS insufficient_count,
        0 AS insufficient_sample_count,
        COUNT(DISTINCT oe.file_id) FILTER (WHERE oe.assessment_status IN ('pending', 'processing')) AS pending_count,
        COUNT(DISTINCT oe.file_id) FILTER (WHERE oe.assessment_status = 'unassessed') AS unassessed_count,
        AVG(oe.relevance_score) FILTER (WHERE oe.relevance_score IS NOT NULL) AS avg_relevance_score
    FROM evidence_theme_map etm
    LEFT JOIN org_evidence oe ON etm.evidence_id = oe.evidence_id
    GROUP BY etm.theme_code
""")


# ---------------------------------------------------------------------------
# Window-aware variant (M1a).
#
# When ENABLE_WINDOW_ASSESSMENT_KSI=true, the EQ axis prefers the evidence-
# level status from evidence_window_assessments over the file-level status
# from evidence_assessments. Falls back to per-file status for evidence IDs
# that have no window assessment yet.
#
# Counts are now evidence-level when a window exists, else file-level — so
# the sufficient/partial/insufficient/insufficient_sample buckets describe
# what the user sees in the new windowed UX.
# ---------------------------------------------------------------------------

_EVIDENCE_METRICS_WINDOW_AWARE_SQL = text("""
    WITH evidence_control_map AS (
        SELECT
            ce.evidence_id,
            jsonb_array_elements_text(ce.control_mappings) AS scf_id
        FROM scf_catalog_evidence ce
        WHERE jsonb_array_length(COALESCE(ce.control_mappings, '[]'::jsonb)) > 0
    ),
    evidence_theme_map AS (
        SELECT DISTINCT
            ecm.evidence_id,
            ecm.scf_id,
            ct.theme_code
        FROM evidence_control_map ecm
        JOIN capability_theme_mappings ctm ON ecm.scf_id = ctm.scf_id
        JOIN capability_themes ct ON ctm.theme_id = ct.id
    ),
    window_status AS (
        SELECT DISTINCT ON (ewa.evidence_id)
            ewa.evidence_id,
            ewa.status AS window_status,
            ewa.relevance_score AS window_relevance
        FROM evidence_window_assessments ewa
        WHERE ewa.organization_id = :org_id
          AND ewa.status NOT IN ('pending', 'processing', 'error')
        ORDER BY ewa.evidence_id, ewa.assessed_at DESC NULLS LAST
    ),
    org_evidence AS (
        SELECT
            ef.evidence_id,
            ef.id AS file_id,
            -- Prefer window_status; fall back to per-file assessment
            COALESCE(ws.window_status, ea.status, 'unassessed') AS assessment_status,
            COALESCE(ws.window_relevance, ea.relevance_score) AS relevance_score,
            ws.window_status IS NOT NULL AS has_window
        FROM evidence_files ef
        LEFT JOIN evidence_assessments ea ON ea.evidence_file_id = ef.id
        LEFT JOIN window_status ws ON ws.evidence_id = ef.evidence_id
        WHERE ef.organization_id = :org_id
          AND ef.is_deleted = false
    ),
    -- For evidence IDs with a window assessment, collapse to one row per evidence.
    -- For evidence without a window, keep per-file rows (legacy behavior).
    evidence_level AS (
        SELECT DISTINCT ON (oe.evidence_id)
            oe.evidence_id,
            NULL::uuid AS file_id,
            oe.assessment_status,
            oe.relevance_score
        FROM org_evidence oe
        WHERE oe.has_window = true
    ),
    file_level AS (
        SELECT
            oe.evidence_id,
            oe.file_id,
            oe.assessment_status,
            oe.relevance_score
        FROM org_evidence oe
        WHERE oe.has_window = false
    ),
    unified AS (
        SELECT evidence_id, file_id, assessment_status, relevance_score FROM evidence_level
        UNION ALL
        SELECT evidence_id, file_id, assessment_status, relevance_score FROM file_level
    )
    SELECT
        etm.theme_code,
        COUNT(DISTINCT etm.scf_id) FILTER (WHERE u.evidence_id IS NOT NULL) AS controls_with_evidence,
        COUNT(*) FILTER (WHERE u.evidence_id IS NOT NULL) AS total_evidence_files,
        COUNT(*) FILTER (WHERE u.assessment_status = 'sufficient') AS sufficient_count,
        COUNT(*) FILTER (WHERE u.assessment_status = 'partial') AS partial_count,
        COUNT(*) FILTER (WHERE u.assessment_status = 'insufficient') AS insufficient_count,
        COUNT(*) FILTER (WHERE u.assessment_status = 'insufficient_sample') AS insufficient_sample_count,
        COUNT(*) FILTER (WHERE u.assessment_status IN ('pending', 'processing')) AS pending_count,
        COUNT(*) FILTER (WHERE u.assessment_status = 'unassessed') AS unassessed_count,
        AVG(u.relevance_score) FILTER (WHERE u.relevance_score IS NOT NULL) AS avg_relevance_score
    FROM evidence_theme_map etm
    LEFT JOIN unified u ON etm.evidence_id = u.evidence_id
    GROUP BY etm.theme_code
""")


# ---------------------------------------------------------------------------
# Composite-aware variant (M3 PR 3, #575).
#
# When ENABLE_COMPOSITE_KSI=true, the EQ axis prefers the per-control rollup
# from control_assessment_composites over both window-level and per-file
# statuses. Precedence: composite > window > per-file. If a composite row
# exists but is in a non-terminal state (pending/no_evidence), the row is
# ignored and the next tier (window if ENABLE_WINDOW_ASSESSMENT_KSI=true,
# else per-file) takes over. The two flags are independent.
#
# Implementation note: at the theme level, the composite "row" describes a
# control, not a file. We collapse to one synthetic evidence-row per scf_id
# whose composite is terminal, mapping composite_status → assessment_status
# 1:1 (sufficient/partial/insufficient/insufficient_sample carry across
# verbatim). composite_score (0-100) is mapped onto the avg_relevance_score
# bucket so existing EQ aggregation downstream sees a comparable signal in
# the same response shape (evidence_quality, evidence_quality_band,
# evidence_quality_warning).
# ---------------------------------------------------------------------------

# Composite states that are usable as an authoritative source for the EQ axis.
# 'pending' and 'no_evidence' fall back per ISC-19. 'error' is not a composite
# status (composites coalesce errors into 'partial' per ISC-4) so it is not
# enumerated here.
_COMPOSITE_TERMINAL_STATUSES = (
    "sufficient",
    "partial",
    "insufficient",
    "insufficient_sample",
)


def _build_composite_aware_sql(window_enabled: bool) -> "text":
    """Construct the composite-aware metrics SQL.

    Precedence resolved per scf_id:
      1. control_assessment_composites row in a terminal status →
         synthesise one evidence-level entry per scf_id.
      2. else, if ``window_enabled``, use the window-aware fallback (per-
         evidence window status, falling back to per-file).
      3. else, per-file legacy assessment (the path used when both flags are
         off — included only for completeness; this branch is never taken
         from the dispatcher because composite-aware SQL is only selected
         when ENABLE_COMPOSITE_KSI=true).

    Both fallbacks are scoped to evidence rows whose scf_id is NOT covered
    by a composite — this prevents double-counting the same control.
    """
    # The window-aware fallback re-uses the same evidence-level union
    # logic from _EVIDENCE_METRICS_WINDOW_AWARE_SQL; the per-file fallback
    # mirrors the legacy SQL. We keep them as distinct CTE bodies rather
    # than parameterising at runtime — clearer to read, and the planner
    # is happier with stable SQL strings.
    if window_enabled:
        fallback_cte = """
        window_status AS (
            SELECT DISTINCT ON (ewa.evidence_id)
                ewa.evidence_id,
                ewa.status AS window_status,
                ewa.relevance_score AS window_relevance
            FROM evidence_window_assessments ewa
            WHERE ewa.organization_id = :org_id
              AND ewa.status NOT IN ('pending', 'processing', 'error')
            ORDER BY ewa.evidence_id, ewa.assessed_at DESC NULLS LAST
        ),
        org_evidence AS (
            SELECT
                ef.evidence_id,
                ef.id AS file_id,
                COALESCE(ws.window_status, ea.status, 'unassessed') AS assessment_status,
                COALESCE(ws.window_relevance, ea.relevance_score) AS relevance_score,
                ws.window_status IS NOT NULL AS has_window
            FROM evidence_files ef
            LEFT JOIN evidence_assessments ea ON ea.evidence_file_id = ef.id
            LEFT JOIN window_status ws ON ws.evidence_id = ef.evidence_id
            WHERE ef.organization_id = :org_id
              AND ef.is_deleted = false
        ),
        evidence_level AS (
            SELECT DISTINCT ON (oe.evidence_id)
                oe.evidence_id,
                NULL::uuid AS file_id,
                oe.assessment_status,
                oe.relevance_score
            FROM org_evidence oe
            WHERE oe.has_window = true
        ),
        file_level AS (
            SELECT
                oe.evidence_id,
                oe.file_id,
                oe.assessment_status,
                oe.relevance_score
            FROM org_evidence oe
            WHERE oe.has_window = false
        ),
        fallback_unified AS (
            SELECT evidence_id, file_id, assessment_status, relevance_score FROM evidence_level
            UNION ALL
            SELECT evidence_id, file_id, assessment_status, relevance_score FROM file_level
        )
        """
    else:
        fallback_cte = """
        org_evidence AS (
            SELECT
                ef.evidence_id,
                ef.id AS file_id,
                COALESCE(ea.status, 'unassessed') AS assessment_status,
                ea.relevance_score
            FROM evidence_files ef
            LEFT JOIN evidence_assessments ea ON ea.evidence_file_id = ef.id
            WHERE ef.organization_id = :org_id
              AND ef.is_deleted = false
        ),
        fallback_unified AS (
            SELECT evidence_id, file_id, assessment_status, relevance_score
            FROM org_evidence
        )
        """

    return text(f"""
        WITH evidence_control_map AS (
            SELECT
                ce.evidence_id,
                jsonb_array_elements_text(ce.control_mappings) AS scf_id
            FROM scf_catalog_evidence ce
            WHERE jsonb_array_length(COALESCE(ce.control_mappings, '[]'::jsonb)) > 0
        ),
        evidence_theme_map AS (
            SELECT DISTINCT
                ecm.evidence_id,
                ecm.scf_id,
                ct.theme_code
            FROM evidence_control_map ecm
            JOIN capability_theme_mappings ctm ON ecm.scf_id = ctm.scf_id
            JOIN capability_themes ct ON ctm.theme_id = ct.id
        ),
        -- Composite tier: one row per scf_id with a terminal composite.
        composite_status AS (
            SELECT
                cac.scf_id,
                cac.composite_status AS assessment_status,
                cac.composite_score AS relevance_score
            FROM control_assessment_composites cac
            WHERE cac.organization_id = :org_id
              AND cac.composite_status IN (
                  'sufficient', 'partial', 'insufficient', 'insufficient_sample'
              )
        ),
        -- Per-theme aggregation from composites: count distinct controls per
        -- bucket. Each composite row contributes once per theme it maps to.
        composite_theme AS (
            SELECT
                ct.theme_code,
                cs.scf_id,
                cs.assessment_status,
                cs.relevance_score
            FROM composite_status cs
            JOIN capability_theme_mappings ctm ON ctm.scf_id = cs.scf_id
            JOIN capability_themes ct ON ct.id = ctm.theme_id
        ),
        {fallback_cte},
        -- Fallback tier: only consider evidence whose scf_ids are NOT yet
        -- covered by a composite (prevents double-counting a control once
        -- via composite and once via per-evidence/per-file).
        fallback_theme AS (
            SELECT
                etm.theme_code,
                etm.scf_id,
                etm.evidence_id,
                fu.file_id,
                fu.assessment_status,
                fu.relevance_score
            FROM evidence_theme_map etm
            LEFT JOIN fallback_unified fu ON etm.evidence_id = fu.evidence_id
            WHERE NOT EXISTS (
                SELECT 1 FROM composite_status cs WHERE cs.scf_id = etm.scf_id
            )
        )
        SELECT
            theme_code,
            -- controls_with_evidence: distinct scf_ids contributing to this theme
            -- (whether via a composite or via a fallback evidence-row).
            COUNT(DISTINCT scf_id) FILTER (WHERE has_signal) AS controls_with_evidence,
            -- total_evidence_files: a "file-equivalent" count. Composites count
            -- as one synthetic file per control; fallback rows count their
            -- evidence/file rows verbatim. Keeps the EQ formula stable.
            COUNT(*) FILTER (WHERE has_signal) AS total_evidence_files,
            COUNT(*) FILTER (WHERE assessment_status = 'sufficient') AS sufficient_count,
            COUNT(*) FILTER (WHERE assessment_status = 'partial') AS partial_count,
            COUNT(*) FILTER (WHERE assessment_status = 'insufficient') AS insufficient_count,
            COUNT(*) FILTER (WHERE assessment_status = 'insufficient_sample') AS insufficient_sample_count,
            COUNT(*) FILTER (WHERE assessment_status IN ('pending', 'processing')) AS pending_count,
            COUNT(*) FILTER (WHERE assessment_status = 'unassessed') AS unassessed_count,
            AVG(relevance_score) FILTER (WHERE relevance_score IS NOT NULL) AS avg_relevance_score
        FROM (
            SELECT
                theme_code,
                scf_id,
                assessment_status,
                relevance_score,
                true AS has_signal
            FROM composite_theme
            UNION ALL
            SELECT
                theme_code,
                scf_id,
                assessment_status,
                relevance_score,
                (evidence_id IS NOT NULL) AS has_signal
            FROM fallback_theme
        ) merged
        GROUP BY theme_code
    """)


_EVIDENCE_METRICS_COMPOSITE_AWARE_SQL = _build_composite_aware_sql(window_enabled=False)
_EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL = _build_composite_aware_sql(window_enabled=True)


def _window_ksi_enabled() -> bool:
    """Feature flag: prefer windowed assessment in KSI SQL when true."""
    return os.getenv("ENABLE_WINDOW_ASSESSMENT_KSI", "false").lower() == "true"


def _composite_ksi_enabled() -> bool:
    """Feature flag: prefer ControlAssessmentComposite rollup in KSI SQL when true.

    Independent of ENABLE_WINDOW_ASSESSMENT_KSI — composite is the top tier
    when on; window/per-file is the fallback chain. Default ``false`` so
    merging M3 PR 3 produces no user-visible change. See M3 spec ISC-18..19.
    """
    return os.getenv("ENABLE_COMPOSITE_KSI", "false").lower() == "true"


async def _fetch_evidence_metrics_per_theme(db: AsyncSession, org_id: UUID) -> dict:
    """Return {theme_code: row} of evidence aggregates for an org.

    Single source of truth for the evidence-posture SQL; used by both the
    legacy /evidence-posture endpoint and the new multi-axis wiring.

    Tier selection (M3 spec §5):
      * ENABLE_COMPOSITE_KSI=true → composite-aware SQL (composite > window
        if ENABLE_WINDOW_ASSESSMENT_KSI=true, else composite > per-file).
      * else if ENABLE_WINDOW_ASSESSMENT_KSI=true → window-aware SQL.
      * else → legacy per-file SQL (default; identical to pre-M1a behaviour).
    """
    if _composite_ksi_enabled():
        sql = (
            _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL
            if _window_ksi_enabled()
            else _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL
        )
    elif _window_ksi_enabled():
        sql = _EVIDENCE_METRICS_WINDOW_AWARE_SQL
    else:
        sql = _EVIDENCE_METRICS_SQL
    result = await db.execute(sql, {"org_id": str(org_id)})
    return {row.theme_code: row for row in result.all()}


def _compute_axis_bundle(
    posture: CapabilityThemePosture,
    scoped: int,
    maturity_score: Optional[float],
    evidence_row,
) -> dict:
    """Compute IC, M-band, EC, EQ (+warning), and KPS for one theme.

    Returns a dict with the nine axis-related fields ready to splat into
    CapabilityThemeResponse / CapabilityThemeScorecardItem.
    """
    ic = compute_ic(
        monitored=posture.monitored,
        implemented=posture.implemented,
        ready_for_review=posture.ready_for_review,
        in_progress=posture.in_progress,
        scoped=scoped,
        not_applicable=posture.not_applicable,
    )

    if evidence_row is not None:
        controls_with_evidence = evidence_row.controls_with_evidence or 0
        total_files = evidence_row.total_evidence_files or 0
        sufficient = evidence_row.sufficient_count or 0
        partial = evidence_row.partial_count or 0
        insufficient = evidence_row.insufficient_count or 0
        # M1a: insufficient_sample is new — emitted only by window-aware SQL.
        insufficient_sample = getattr(evidence_row, "insufficient_sample_count", 0) or 0
        unassessed = evidence_row.unassessed_count or 0
        avg_relevance = (
            float(evidence_row.avg_relevance_score)
            if evidence_row.avg_relevance_score is not None
            else None
        )
    else:
        controls_with_evidence = total_files = sufficient = partial = insufficient = 0
        insufficient_sample = unassessed = 0
        avg_relevance = None

    ec = compute_ec(controls_with_evidence, scoped, posture.not_applicable)
    eq = compute_eq(sufficient, partial, insufficient, avg_relevance, insufficient_sample)
    eq_warning = compute_eq_warning(unassessed, total_files)
    kps = compute_kps(ic, maturity_score / 5.0 if maturity_score is not None else None, ec, eq)

    return {
        "implementation_coverage": ic,
        "implementation_band": band_for_axis("IC", ic),
        "maturity_band": band_for_axis("M", maturity_score),
        "evidence_coverage": ec,
        "evidence_coverage_band": band_for_axis("EC", ec),
        "evidence_quality": eq,
        "evidence_quality_band": band_for_axis("EQ", eq),
        "evidence_quality_warning": eq_warning,
        "composite_score": kps,
        "composite_band": band_for_axis("KPS", kps),
    }


@router.get(
    "/organizations/{org_id}/capability-themes",
    response_model=CapabilityThemeListResponse,
)
async def list_capability_themes(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    List all capability themes with aggregated posture scores for the organization.

    Returns all 11 KSI-aligned themes with per-theme control counts,
    implementation status breakdown, posture percentage, and maturity score.
    """
    # Step 1: Get all themes
    themes_result = await db.execute(
        select(CapabilityTheme).order_by(CapabilityTheme.display_order)
    )
    themes = themes_result.scalars().all()

    if not themes:
        return CapabilityThemeListResponse(themes=[])

    # Step 2: For each theme, count total mapped controls and aggregate scoped control posture
    # Build a single query that joins themes -> mappings -> scoped_controls
    theme_stats_query = (
        select(
            CapabilityTheme.id,
            CapabilityTheme.theme_code,
            # Total controls mapped to this theme (regardless of org scoping)
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).label("total_controls"),
            # Scoped controls (selected=True for this org)
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                ScopedControl.selected == True
            ).label("scoped_controls"),
            # Status breakdowns (only for scoped controls)
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "monitored")
            ).label("monitored"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "implemented")
            ).label("implemented"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "ready_for_review")
            ).label("ready_for_review"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "in_progress")
            ).label("in_progress"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_started")
            ).label("not_started"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "at_risk")
            ).label("at_risk"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_applicable")
            ).label("not_applicable"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "deferred")
            ).label("deferred"),
            # Maturity: average numeric score for controls with maturity_level set
            func.avg(
                case(
                    (ScopedControl.maturity_level == "L0", 0),
                    (ScopedControl.maturity_level == "L1", 1),
                    (ScopedControl.maturity_level == "L2", 2),
                    (ScopedControl.maturity_level == "L3", 3),
                    (ScopedControl.maturity_level == "L4", 4),
                    (ScopedControl.maturity_level == "L5", 5),
                    else_=None,
                )
            ).filter(
                and_(ScopedControl.selected == True, ScopedControl.maturity_level.isnot(None))
            ).label("maturity_score"),
        )
        .select_from(CapabilityTheme)
        .join(CapabilityThemeMapping, CapabilityTheme.id == CapabilityThemeMapping.theme_id)
        .outerjoin(
            ScopedControl,
            and_(
                CapabilityThemeMapping.scf_id == ScopedControl.scf_id,
                ScopedControl.organization_id == org_id,
            ),
        )
        .group_by(CapabilityTheme.id, CapabilityTheme.theme_code)
        .order_by(CapabilityTheme.display_order)
    )

    stats_result = await db.execute(theme_stats_query)
    stats_rows = {row.theme_code: row for row in stats_result.all()}

    # Issue #549, Phase 1: fetch evidence metrics so EC/EQ axes can be populated.
    evidence_rows = await _fetch_evidence_metrics_per_theme(db, org_id)

    # Step 3: Build response
    theme_responses = []
    for theme in themes:
        row = stats_rows.get(theme.theme_code)
        if row:
            posture = CapabilityThemePosture(
                monitored=row.monitored or 0,
                implemented=row.implemented or 0,
                ready_for_review=row.ready_for_review or 0,
                in_progress=row.in_progress or 0,
                not_started=row.not_started or 0,
                at_risk=row.at_risk or 0,
                not_applicable=row.not_applicable or 0,
                deferred=row.deferred or 0,
            )
            scoped = row.scoped_controls or 0
            maturity = round(float(row.maturity_score), 1) if row.maturity_score is not None else None
        else:
            posture = CapabilityThemePosture()
            scoped = 0
            maturity = None

        axes = _compute_axis_bundle(posture, scoped, maturity, evidence_rows.get(theme.theme_code))

        theme_responses.append(CapabilityThemeResponse(
            theme_code=theme.theme_code,
            name=theme.name,
            description=theme.description,
            ksi_reference=theme.ksi_reference,
            icon=theme.icon,
            display_order=theme.display_order,
            total_controls=row.total_controls if row else 0,
            scoped_controls=scoped,
            posture=posture,
            posture_percentage=_compute_posture_percentage(posture, scoped),
            maturity_score=maturity,
            **axes,
        ))

    return CapabilityThemeListResponse(themes=theme_responses)


def _derive_evidence_confidence(
    sufficient: int,
    partial: int,
    total_files: int,
    controls_with_evidence: int,
    scoped_controls: int,
) -> str:
    """Derive evidence confidence level from assessment metrics."""
    if total_files == 0:
        return "none"

    assessed = sufficient + partial
    assessed_total = sufficient + partial + total_files - assessed  # all non-pending assessed files

    # Avoid division by zero
    if assessed_total == 0:
        return "weak"

    sufficient_ratio = sufficient / assessed_total if assessed_total > 0 else 0
    positive_ratio = assessed / assessed_total if assessed_total > 0 else 0
    coverage_ratio = controls_with_evidence / scoped_controls if scoped_controls > 0 else 0

    if sufficient_ratio >= 0.7 and coverage_ratio > 0.5:
        return "strong"
    if positive_ratio >= 0.4:
        return "moderate"
    return "weak"


@router.get(
    "/organizations/{org_id}/capability-themes/evidence-posture",
    response_model=CapabilityThemeEvidencePostureResponse,
    summary="Get evidence assessment posture per capability theme",
    description="""
    Returns evidence assessment metrics aggregated by KSI capability theme.

    For each theme, shows how many controls have evidence, assessment status
    distribution (sufficient/partial/insufficient/pending/unassessed), and
    a derived evidence confidence level (strong/moderate/weak/none).

    Uses the evidence -> control_mappings -> theme linkage chain.
    Fetched independently from the main posture endpoint for parallel loading.
    """,
)
async def get_evidence_posture(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get evidence assessment posture per capability theme.
    Requires: viewer role or higher.
    """
    # Evidence aggregates per theme — shared helper (also used by /scorecard, #549).
    evidence_by_theme = await _fetch_evidence_metrics_per_theme(db, org_id)

    # Get scoped control counts per theme for confidence derivation
    scoped_query = (
        select(
            CapabilityTheme.theme_code,
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                ScopedControl.selected == True
            ).label("scoped_controls"),
        )
        .select_from(CapabilityTheme)
        .join(CapabilityThemeMapping, CapabilityTheme.id == CapabilityThemeMapping.theme_id)
        .outerjoin(
            ScopedControl,
            and_(
                CapabilityThemeMapping.scf_id == ScopedControl.scf_id,
                ScopedControl.organization_id == org_id,
            ),
        )
        .group_by(CapabilityTheme.theme_code)
    )
    scoped_result = await db.execute(scoped_query)
    scoped_by_theme = {r.theme_code: r.scoped_controls or 0 for r in scoped_result.all()}

    # Build all themes (include themes with no evidence as "none")
    all_themes_result = await db.execute(
        select(CapabilityTheme.theme_code).order_by(CapabilityTheme.display_order)
    )
    all_theme_codes = [r[0] for r in all_themes_result.all()]

    themes = []
    for theme_code in all_theme_codes:
        row = evidence_by_theme.get(theme_code)
        scoped = scoped_by_theme.get(theme_code, 0)

        if row:
            controls_with_evidence = row.controls_with_evidence or 0
            total_files = row.total_evidence_files or 0
            sufficient = row.sufficient_count or 0
            partial = row.partial_count or 0
            insufficient = row.insufficient_count or 0
            insufficient_sample = getattr(row, "insufficient_sample_count", 0) or 0
            pending = row.pending_count or 0
            unassessed = row.unassessed_count or 0
            avg_score = round(float(row.avg_relevance_score), 1) if row.avg_relevance_score is not None else None
        else:
            controls_with_evidence = 0
            total_files = 0
            sufficient = 0
            partial = 0
            insufficient = 0
            insufficient_sample = 0
            pending = 0
            unassessed = 0
            avg_score = None

        confidence = _derive_evidence_confidence(
            sufficient=sufficient,
            partial=partial,
            total_files=total_files,
            controls_with_evidence=controls_with_evidence,
            scoped_controls=scoped,
        )

        themes.append(CapabilityThemeEvidencePosture(
            theme_code=theme_code,
            controls_with_evidence=controls_with_evidence,
            total_evidence_files=total_files,
            sufficient_count=sufficient,
            partial_count=partial,
            insufficient_count=insufficient,
            insufficient_sample_count=insufficient_sample,
            pending_count=pending,
            unassessed_count=unassessed,
            average_relevance_score=avg_score,
            evidence_confidence=confidence,
        ))

    return CapabilityThemeEvidencePostureResponse(themes=themes)


@router.get(
    "/organizations/{org_id}/capability-themes/scorecard",
    response_model=CapabilityThemeScorecardResponse,
    summary="Multi-axis KSI scorecard for all themes (issue #549, Phase 1)",
    description=(
        "Returns the four scoring axes (Implementation Coverage, Maturity, Evidence "
        "Coverage, Evidence Quality) plus the composite KSI Posture Score for every "
        "theme in a single call. Replaces the dual-call pattern of fetching themes "
        "and evidence-posture separately."
    ),
)
async def get_capability_themes_scorecard(
    org_id: UUID,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """Multi-axis scorecard across all capability themes — issue #549, Phase 1."""
    themes_result = await db.execute(
        select(CapabilityTheme).order_by(CapabilityTheme.display_order)
    )
    themes = themes_result.scalars().all()
    if not themes:
        return CapabilityThemeScorecardResponse(themes=[])

    theme_stats_query = (
        select(
            CapabilityTheme.theme_code,
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                ScopedControl.selected == True
            ).label("scoped_controls"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "monitored")
            ).label("monitored"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "implemented")
            ).label("implemented"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "ready_for_review")
            ).label("ready_for_review"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "in_progress")
            ).label("in_progress"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_applicable")
            ).label("not_applicable"),
            func.avg(
                case(
                    (ScopedControl.maturity_level == "L0", 0),
                    (ScopedControl.maturity_level == "L1", 1),
                    (ScopedControl.maturity_level == "L2", 2),
                    (ScopedControl.maturity_level == "L3", 3),
                    (ScopedControl.maturity_level == "L4", 4),
                    (ScopedControl.maturity_level == "L5", 5),
                    else_=None,
                )
            ).filter(
                and_(ScopedControl.selected == True, ScopedControl.maturity_level.isnot(None))
            ).label("maturity_score"),
        )
        .select_from(CapabilityTheme)
        .join(CapabilityThemeMapping, CapabilityTheme.id == CapabilityThemeMapping.theme_id)
        .outerjoin(
            ScopedControl,
            and_(
                CapabilityThemeMapping.scf_id == ScopedControl.scf_id,
                ScopedControl.organization_id == org_id,
            ),
        )
        .group_by(CapabilityTheme.theme_code)
    )
    stats_rows = {row.theme_code: row for row in (await db.execute(theme_stats_query)).all()}
    evidence_rows = await _fetch_evidence_metrics_per_theme(db, org_id)

    items = []
    for theme in themes:
        row = stats_rows.get(theme.theme_code)
        if row:
            posture = CapabilityThemePosture(
                monitored=row.monitored or 0,
                implemented=row.implemented or 0,
                ready_for_review=row.ready_for_review or 0,
                in_progress=row.in_progress or 0,
                not_applicable=row.not_applicable or 0,
            )
            scoped = row.scoped_controls or 0
            maturity = round(float(row.maturity_score), 1) if row.maturity_score is not None else None
        else:
            posture = CapabilityThemePosture()
            scoped = 0
            maturity = None

        axes = _compute_axis_bundle(posture, scoped, maturity, evidence_rows.get(theme.theme_code))
        items.append(CapabilityThemeScorecardItem(
            theme_code=theme.theme_code,
            name=theme.name,
            icon=theme.icon,
            display_order=theme.display_order,
            scoped_controls=scoped,
            maturity_score=maturity,
            **axes,
        ))

    return CapabilityThemeScorecardResponse(themes=items)


@router.get(
    "/organizations/{org_id}/capability-themes/{theme_code}",
    response_model=CapabilityThemeResponse,
)
async def get_capability_theme(
    org_id: UUID,
    theme_code: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
):
    """
    Get a single capability theme with posture scoring.
    """
    # Verify theme exists
    theme_result = await db.execute(
        select(CapabilityTheme).where(CapabilityTheme.theme_code == theme_code.upper())
    )
    theme = theme_result.scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail=f"Capability theme '{theme_code}' not found")

    # Aggregate stats for this theme
    stats_query = (
        select(
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).label("total_controls"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                ScopedControl.selected == True
            ).label("scoped_controls"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "monitored")
            ).label("monitored"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "implemented")
            ).label("implemented"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "ready_for_review")
            ).label("ready_for_review"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "in_progress")
            ).label("in_progress"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_started")
            ).label("not_started"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "at_risk")
            ).label("at_risk"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_applicable")
            ).label("not_applicable"),
            func.count(func.distinct(CapabilityThemeMapping.scf_id)).filter(
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "deferred")
            ).label("deferred"),
            func.avg(
                case(
                    (ScopedControl.maturity_level == "L0", 0),
                    (ScopedControl.maturity_level == "L1", 1),
                    (ScopedControl.maturity_level == "L2", 2),
                    (ScopedControl.maturity_level == "L3", 3),
                    (ScopedControl.maturity_level == "L4", 4),
                    (ScopedControl.maturity_level == "L5", 5),
                    else_=None,
                )
            ).filter(
                and_(ScopedControl.selected == True, ScopedControl.maturity_level.isnot(None))
            ).label("maturity_score"),
        )
        .select_from(CapabilityThemeMapping)
        .outerjoin(
            ScopedControl,
            and_(
                CapabilityThemeMapping.scf_id == ScopedControl.scf_id,
                ScopedControl.organization_id == org_id,
            ),
        )
        .where(CapabilityThemeMapping.theme_id == theme.id)
    )

    row = (await db.execute(stats_query)).one()

    posture = CapabilityThemePosture(
        monitored=row.monitored or 0,
        implemented=row.implemented or 0,
        ready_for_review=row.ready_for_review or 0,
        in_progress=row.in_progress or 0,
        not_started=row.not_started or 0,
        at_risk=row.at_risk or 0,
        not_applicable=row.not_applicable or 0,
        deferred=row.deferred or 0,
    )
    scoped = row.scoped_controls or 0
    maturity = round(float(row.maturity_score), 1) if row.maturity_score is not None else None

    # Issue #549, Phase 1: per-theme evidence metrics for EC/EQ.
    evidence_rows = await _fetch_evidence_metrics_per_theme(db, org_id)
    axes = _compute_axis_bundle(posture, scoped, maturity, evidence_rows.get(theme.theme_code))

    return CapabilityThemeResponse(
        theme_code=theme.theme_code,
        name=theme.name,
        description=theme.description,
        ksi_reference=theme.ksi_reference,
        icon=theme.icon,
        display_order=theme.display_order,
        total_controls=row.total_controls or 0,
        scoped_controls=scoped,
        posture=posture,
        posture_percentage=_compute_posture_percentage(posture, scoped),
        maturity_score=maturity,
        **axes,
    )


@router.get(
    "/organizations/{org_id}/capability-themes/{theme_code}/controls",
    response_model=CapabilityThemeControlsResponse,
)
async def list_capability_theme_controls(
    org_id: UUID,
    theme_code: str,
    membership: OrgMembership = Depends(require_org_role("viewer")),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200, description="Max results per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    scope_status: str = Query("in_scope", description="Filter: in_scope (default), out_of_scope, all"),
):
    """
    List controls mapped to a capability theme with their scoping status.
    Supports pagination and scope filtering.
    """
    # Verify theme exists
    theme_result = await db.execute(
        select(CapabilityTheme).where(CapabilityTheme.theme_code == theme_code.upper())
    )
    theme = theme_result.scalar_one_or_none()
    if not theme:
        raise HTTPException(status_code=404, detail=f"Capability theme '{theme_code}' not found")

    # Build query: mappings -> catalog controls LEFT JOIN scoped controls
    base_query = (
        select(
            CapabilityThemeMapping.scf_id,
            CapabilityThemeMapping.relevance,
            SCFCatalogControl.control_name,
            SCFCatalogControl.scf_domain,
            ScopedControl.selected,
            ScopedControl.implementation_status,
            ScopedControl.maturity_level,
        )
        .select_from(CapabilityThemeMapping)
        .join(SCFCatalogControl, CapabilityThemeMapping.scf_id == SCFCatalogControl.scf_id)
        .outerjoin(
            ScopedControl,
            and_(
                CapabilityThemeMapping.scf_id == ScopedControl.scf_id,
                ScopedControl.organization_id == org_id,
            ),
        )
        .where(CapabilityThemeMapping.theme_id == theme.id)
    )

    # Apply scope filter
    if scope_status == "in_scope":
        base_query = base_query.where(ScopedControl.selected == True)
    elif scope_status == "out_of_scope":
        base_query = base_query.where(
            (ScopedControl.selected == False) | (ScopedControl.selected.is_(None))
        )

    # Get total count
    count_subquery = base_query.subquery()
    total = await db.scalar(select(func.count()).select_from(count_subquery))

    # Apply pagination
    paginated_query = base_query.order_by(CapabilityThemeMapping.scf_id).offset(offset).limit(limit)
    result = await db.execute(paginated_query)
    rows = result.all()

    controls = [
        CapabilityThemeControlItem(
            scf_id=row.scf_id,
            control_name=row.control_name,
            scf_domain=row.scf_domain,
            selected=row.selected or False,
            implementation_status=row.implementation_status,
            maturity_level=row.maturity_level,
            relevance=row.relevance,
        )
        for row in rows
    ]

    return CapabilityThemeControlsResponse(
        theme_code=theme.theme_code,
        theme_name=theme.name,
        controls=controls,
        total=total or 0,
        offset=offset,
        limit=limit,
    )
