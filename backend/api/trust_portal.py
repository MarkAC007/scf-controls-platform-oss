"""
Trust Portal Public API — unauthenticated endpoint for trust portal consumers.

Provides aggregated compliance posture data for organizations that have
enabled their trust portal. Data is projected into safe bands (Strong/
Moderate/Developing) — no raw percentages, SCF IDs, or individual control
statuses are ever exposed.

Endpoint: GET /public/trust/{org_slug}
Auth: None (fully public)
Rate limit: 30/minute per IP
Cache: 15-minute Redis TTL
"""
import logging
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, and_, func, case, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import (
    Organization,
    ScopedControl,
    EvidenceFile,
    EvidenceAssessment,
    EvidenceWindowAssessment,
)
from catalog_models import CapabilityTheme, CapabilityThemeMapping, SCFCatalogControl
from schemas import (
    TrustPortalResponse,
    TrustPortalThemeSummary,
    TrustPortalFramework,
    CapabilityThemePosture,
)
from api.capability_themes import (
    _compute_posture_percentage,
    _derive_evidence_confidence,
    _compute_axis_bundle,
    _fetch_evidence_metrics_per_theme,
)
from api.catalog import format_framework_name, INTERNAL_MAPPING_PREFIXES
from cache import make_cache_key
from rate_limiting import rate_limit_trust_portal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/public/trust", tags=["trust_portal"])

# Cache TTL for trust portal data (15 minutes)
TRUST_PORTAL_CACHE_TTL = 900


def _posture_to_band(pct: float) -> str:
    """Convert posture percentage to a display band.

    >=70% = Strong, >=40% = Moderate, <40% = Developing.
    """
    if pct >= 70.0:
        return "Strong"
    elif pct >= 40.0:
        return "Moderate"
    return "Developing"


@router.get(
    "/{org_slug}",
    response_model=TrustPortalResponse,
    summary="Get public trust portal data",
    description="Returns aggregated compliance posture for an organization's trust portal. "
    "No authentication required. Data is projected into bands — no raw metrics exposed.",
)
@rate_limit_trust_portal
async def get_trust_portal(
    org_slug: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    # ------------------------------------------------------------------
    # 1. Check Redis cache
    # ------------------------------------------------------------------
    cache_key = make_cache_key(org_slug, prefix="trust_portal")
    try:
        from redis_client import get_redis_client
        import json as _json

        redis = await get_redis_client()
        cached = await redis.get(cache_key)
        if cached is not None:
            logger.debug(f"Trust portal cache hit: {org_slug}")
            return TrustPortalResponse(**_json.loads(cached))
    except Exception as e:
        logger.warning(f"Trust portal cache read failed: {e}")

    # ------------------------------------------------------------------
    # 2. Look up organization by slug
    # ------------------------------------------------------------------
    org_result = await db.execute(
        select(Organization).where(Organization.slug == org_slug)
    )
    org = org_result.scalar_one_or_none()

    if not org:
        raise HTTPException(status_code=404, detail="Not found")

    # ------------------------------------------------------------------
    # 3. Check feature flag
    # ------------------------------------------------------------------
    settings = org.settings or {}
    if not settings.get("is_trust_portal_enabled", False):
        raise HTTPException(status_code=404, detail="Not found")

    org_id = org.id

    # ------------------------------------------------------------------
    # 4. Query theme posture data (reuses capability_themes pattern)
    # ------------------------------------------------------------------
    themes_result = await db.execute(
        select(CapabilityTheme).order_by(CapabilityTheme.display_order)
    )
    themes = themes_result.scalars().all()

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
                and_(ScopedControl.selected == True, ScopedControl.implementation_status == "not_applicable")
            ).label("not_applicable"),
            # Maturity: weighted average numeric level (L0=0..L5=5) across scoped controls
            # that have a maturity_level set. Matches the pattern used in
            # api/capability_themes.py so the public and private endpoints agree.
            func.avg(
                case(
                    (ScopedControl.maturity_level == "L0", 0),
                    (ScopedControl.maturity_level == "L1", 1),
                    (ScopedControl.maturity_level == "L2", 2),
                    (ScopedControl.maturity_level == "L3", 3),
                    (ScopedControl.maturity_level == "L4", 4),
                    (ScopedControl.maturity_level == "L5", 5),
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
        .group_by(CapabilityTheme.theme_code, CapabilityTheme.display_order)
        .order_by(CapabilityTheme.display_order)
    )

    stats_result = await db.execute(theme_stats_query)
    stats_by_theme = {row.theme_code: row for row in stats_result.all()}

    # ------------------------------------------------------------------
    # 5. Query evidence metrics per theme (unified helper shared with
    #    the authenticated scorecard endpoint — single source of truth)
    # ------------------------------------------------------------------
    evidence_by_theme = await _fetch_evidence_metrics_per_theme(db, org_id)

    # ------------------------------------------------------------------
    # 6. Build theme summaries
    # ------------------------------------------------------------------
    theme_summaries = []
    for theme in themes:
        row = stats_by_theme.get(theme.theme_code)
        if row:
            posture = CapabilityThemePosture(
                monitored=row.monitored or 0,
                implemented=row.implemented or 0,
                not_applicable=row.not_applicable or 0,
            )
            scoped = row.scoped_controls or 0
            pct = _compute_posture_percentage(posture, scoped)
            maturity = (
                round(float(row.maturity_score), 1)
                if getattr(row, "maturity_score", None) is not None
                else None
            )
        else:
            posture = CapabilityThemePosture(monitored=0, implemented=0, not_applicable=0)
            scoped = 0
            pct = 0.0
            maturity = None

        ev_row = evidence_by_theme.get(theme.theme_code)
        if ev_row:
            confidence = _derive_evidence_confidence(
                sufficient=ev_row.sufficient_count or 0,
                partial=ev_row.partial_count or 0,
                total_files=ev_row.total_evidence_files or 0,
                controls_with_evidence=ev_row.controls_with_evidence or 0,
                scoped_controls=scoped,
            )
        else:
            confidence = "none"

        # Multi-axis bundle: IC / M / EC / EQ + composite. We surface the five BANDS
        # publicly — the raw composite_score float and evidence_quality_warning string
        # stay internal per the Trust Portal privacy model.
        axes = _compute_axis_bundle(posture, scoped, maturity, ev_row)

        theme_summaries.append(TrustPortalThemeSummary(
            name=theme.name,
            icon=theme.icon,
            display_order=theme.display_order,
            posture_band=_posture_to_band(pct),
            evidence_confidence=confidence,
            implementation_band=axes["implementation_band"],
            maturity_band=axes["maturity_band"],
            evidence_coverage_band=axes["evidence_coverage_band"],
            evidence_quality_band=axes["evidence_quality_band"],
            composite_band=axes["composite_band"],
        ))

    # ------------------------------------------------------------------
    # 7. Query scoped frameworks
    # ------------------------------------------------------------------
    framework_query = text("""
        SELECT
            fw_key,
            COUNT(DISTINCT sc.scf_id) AS control_count
        FROM scoped_controls sc
        JOIN scf_catalog_controls cat ON sc.scf_id = cat.scf_id,
             jsonb_object_keys(cat.framework_mappings) AS fw_key
        WHERE sc.organization_id = :org_id
          AND sc.selected = true
        GROUP BY fw_key
        ORDER BY control_count DESC
    """)

    fw_result = await db.execute(framework_query, {"org_id": str(org_id)})
    frameworks = []
    for row in fw_result.all():
        if row.fw_key.startswith(INTERNAL_MAPPING_PREFIXES):
            continue
        frameworks.append(TrustPortalFramework(
            name=format_framework_name(row.fw_key),
            control_count=row.control_count,
        ))

    # ------------------------------------------------------------------
    # 8. Query last updated timestamp
    # ------------------------------------------------------------------
    # MAX across the four posture-meaningful activity surfaces so the public
    # trust date advances when evidence/assessment activity occurs, not only
    # when scoped controls are reconfigured. Audit-log and other internal
    # system writes are deliberately excluded.
    activity_timestamps: list[datetime] = []

    scoped_control_max = (await db.execute(
        select(func.max(ScopedControl.updated_at)).where(
            ScopedControl.organization_id == org_id
        )
    )).scalar()
    activity_timestamps.append(scoped_control_max)

    evidence_file_max = (await db.execute(
        select(func.max(EvidenceFile.uploaded_at)).where(
            and_(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.is_deleted == False,
            )
        )
    )).scalar()
    activity_timestamps.append(evidence_file_max)

    evidence_assessment_max = (await db.execute(
        select(func.max(EvidenceAssessment.assessed_at)).where(
            EvidenceAssessment.organization_id == org_id
        )
    )).scalar()
    activity_timestamps.append(evidence_assessment_max)

    window_assessment_max = (await db.execute(
        select(func.max(EvidenceWindowAssessment.assessed_at)).where(
            EvidenceWindowAssessment.organization_id == org_id
        )
    )).scalar()
    activity_timestamps.append(window_assessment_max)

    activity_timestamps = [ts for ts in activity_timestamps if ts is not None]
    last_updated = max(activity_timestamps) if activity_timestamps else datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # 9. Assemble response
    # ------------------------------------------------------------------
    portal_response = TrustPortalResponse(
        organization_name=org.name,
        organization_slug=org.slug,
        description=settings.get("trust_portal_description"),
        themes=theme_summaries,
        frameworks=frameworks,
        last_updated=last_updated,
        generated_at=datetime.now(timezone.utc),
        show_axes=bool(settings.get("trust_portal_show_axes", True)),
    )

    # ------------------------------------------------------------------
    # 10. Cache the response
    # ------------------------------------------------------------------
    try:
        import json as _json

        redis = await get_redis_client()
        await redis.setex(
            cache_key,
            TRUST_PORTAL_CACHE_TTL,
            _json.dumps(portal_response.model_dump(), default=str),
        )
        logger.debug(f"Trust portal cached: {org_slug} (TTL: {TRUST_PORTAL_CACHE_TTL}s)")
    except Exception as e:
        logger.warning(f"Trust portal cache write failed: {e}")

    return portal_response
