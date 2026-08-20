"""
Deprecated-catalog read-path tests for consumers 5 through 11 of the
catalog-upgrade plan (docs/plans/scf-catalog-upgrade.md, section 4.4).

One test class per consumer:

5.  Composites / evidence health — deprecated ERL rows leave the
    denominators (a retired evidence request is not a gap) while existing
    org rows still render, badged.
6.  Engagement views — frozen scope always resolves, deprecated included,
    and responses expose the engagement's own catalog_version.
7.  Trust portal — public aggregates are active-only, no badges.
8.  CDM — new suggestions are active-only; historical mappings resolve,
    badged.
9.  Risk pickers — new links refuse deprecated controls (with successor
    hint); existing mappings resolve, badged.
10. Evidence tracking — NEW tracking of a deprecated ERL entry is refused;
    existing tracked rows resolve, badged.
11. Assessment prompts — deprecated controls still resolve and the LLM
    context gains an explicit deprecation note; active-control context is
    byte-identical to the pre-upgrade shape (hash stability).

Mock-based, no real database — same style as the sibling API tests.
Control ids use the TESTCTL prefix, evidence ids the EVID prefix.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from datetime import datetime, date
from types import SimpleNamespace

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def membership(org_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = uuid4()
    m.user.db_id = str(uuid4())
    m.role = "editor"
    return m


def _result(*, scalars_all=None, all_rows=None, scalar_one_or_none="__unset__",
            scalar_one="__unset__", scalar="__unset__", first="__unset__",
            fetchall=None):
    """Build a MagicMock standing in for a SQLAlchemy Result."""
    r = MagicMock()
    if scalars_all is not None:
        r.scalars.return_value.all.return_value = scalars_all
    if all_rows is not None:
        r.all.return_value = all_rows
        r.__iter__ = lambda self: iter(all_rows)
    if scalar_one_or_none != "__unset__":
        r.scalar_one_or_none.return_value = scalar_one_or_none
    if scalar_one != "__unset__":
        r.scalar_one.return_value = scalar_one
    if scalar != "__unset__":
        r.scalar.return_value = scalar
    if first != "__unset__":
        r.first.return_value = first
    if fetchall is not None:
        r.fetchall.return_value = fetchall
    return r


class _RecordingSyncSession:
    """Sync-session stub that records executed statements, returns empties."""

    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        r = MagicMock()
        r.all.return_value = []
        r.first.return_value = None
        return r

    def get(self, *args, **kwargs):
        return None

    def add(self, obj):
        pass

    def commit(self):
        pass


# ---------------------------------------------------------------------------
# Consumer 5 — composites + evidence health
# ---------------------------------------------------------------------------

class TestConsumer5Composites:

    def test_composite_denominator_excludes_deprecated_erl_rows(self):
        """The evidence-id resolver behind the composite denominator only
        selects active catalog rows."""
        from services.composite_service import _evidence_ids_for_control

        session = _RecordingSyncSession()
        ids = _evidence_ids_for_control(session, "TESTCTL1")

        assert ids == []
        assert len(session.statements) == 1
        sql = str(session.statements[0][0])
        assert "status = 'active'" in sql
        assert session.statements[0][1] == {"scf_id": "TESTCTL1"}


class TestConsumer5EvidenceHealth:

    @pytest.mark.asyncio
    async def test_deprecated_item_renders_badged_but_leaves_denominator(
        self, membership, org_id,
    ):
        from api.evidence_health import get_evidence_health

        tracked = [
            SimpleNamespace(evidence_id="EVID100", collecting_system=None,
                            frequency=None),
            SimpleNamespace(evidence_id="EVID200", collecting_system=None,
                            frequency=None),
        ]
        catalog_rows = [
            SimpleNamespace(evidence_id="EVID100", control_mappings=["TESTCTL1"],
                            status="active", retired_in_version=None,
                            superseded_by=None),
            SimpleNamespace(evidence_id="EVID200", control_mappings=["TESTCTL2"],
                            status="deprecated", retired_in_version="2026.2",
                            superseded_by="EVID300"),
        ]

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalars_all=tracked),        # 1. tracked evidence
            _result(scalars_all=[]),             # 2. health config overrides
            _result(all_rows=[]),                # 3. latest files
            _result(all_rows=[]),                # 3b. latest assessments
            _result(all_rows=catalog_rows),      # 3c. catalog lifecycle
        ])

        from fastapi import Response
        # Unwrap the rate-limit decorator so the test does not depend on the
        # RATE_LIMITING_ENABLED environment setting.
        endpoint = getattr(get_evidence_health, "__wrapped__", get_evidence_health)
        resp = await endpoint(
            request=MagicMock(), response=Response(), org_id=org_id,
            membership=membership, db=db,
        )

        by_eid = {i.evidence_id: i for i in resp.items}
        assert set(by_eid) == {"EVID100", "EVID200"}  # both still render
        assert by_eid["EVID100"].catalog_status == "active"
        assert by_eid["EVID200"].catalog_status == "deprecated"
        assert by_eid["EVID200"].retired_in_version == "2026.2"
        assert by_eid["EVID200"].superseded_by == "EVID300"
        # A retired evidence request is not a gap: only the active item
        # counts toward the traffic-light denominator.
        assert resp.summary.total_tracked == 1
        assert resp.summary.unknown_count == 1


# ---------------------------------------------------------------------------
# Consumer 6 — engagement views
# ---------------------------------------------------------------------------

class TestConsumer6Engagements:

    @pytest.mark.asyncio
    async def test_scope_resolves_deprecated_controls_badged(self, org_id):
        from api.audit_engagements import get_engagement_scope

        engagement_id = uuid4()
        engagement = SimpleNamespace(id=engagement_id,
                                     organization_id=org_id)
        scope_row = (
            uuid4(),                 # EngagementControlScope.id
            uuid4(),                 # scoped_control_id
            datetime(2026, 8, 1),    # added_at
            "TESTCTL7",              # scf_id
            "in_scope",              # scope_status
            None,                    # out_of_scope_justification
            ["soc2"],                # source_frameworks
            "Some retired control",  # control_name (still resolves)
            "deprecated",            # catalog status
            "2026.2",                # retired_in_version
            "TESTCTL8",              # superseded_by
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=engagement),
            _result(fetchall=[scope_row]),
        ])

        items = await get_engagement_scope(
            org_id=org_id, engagement_id=engagement_id,
            access=MagicMock(), db=db,
        )

        assert len(items) == 1
        item = items[0]
        assert item.scf_id == "TESTCTL7"
        assert item.control_name == "Some retired control"
        assert item.catalog_status == "deprecated"
        assert item.retired_in_version == "2026.2"
        assert item.superseded_by == "TESTCTL8"

    @pytest.mark.asyncio
    async def test_engagement_response_exposes_catalog_version(self, org_id):
        from api.audit_engagements import get_engagement

        engagement_id = uuid4()
        engagement = SimpleNamespace(
            id=engagement_id, organization_id=org_id, name="Annual audit",
            frameworks=["soc2"], status="active",
            start_date=date(2026, 1, 1), end_date=None,
            created_by_user_id=None,
            created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 2),
            catalog_version="2026.1",
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=engagement),
            _result(scalar_one=3),
        ])

        resp = await get_engagement(
            org_id=org_id, engagement_id=engagement_id,
            access=MagicMock(), db=db,
        )

        assert resp.catalog_version == "2026.1"
        assert resp.scope_count == 3


# ---------------------------------------------------------------------------
# Consumer 7 — trust portal (public surface, active-only, no badges)
# ---------------------------------------------------------------------------

class TestConsumer7TrustPortal:

    def test_theme_stats_query_filters_to_active_catalog(self):
        from api.trust_portal import _build_theme_stats_query

        sql = str(_build_theme_stats_query(uuid4()))
        assert "LEFT OUTER JOIN scf_catalog_controls" in sql
        # outerjoin-preserving predicate: no catalog row at all, or active
        assert "scf_catalog_controls.scf_id IS NULL" in sql
        assert "scf_catalog_controls.status = :status_1" in sql

    def test_framework_query_filters_to_active_catalog(self):
        from api.trust_portal import _FRAMEWORK_QUERY

        assert "cat.status = 'active'" in str(_FRAMEWORK_QUERY)


# ---------------------------------------------------------------------------
# Consumer 8 — CDM
# ---------------------------------------------------------------------------

class TestConsumer8CDM:

    def test_suggestions_query_is_active_catalog_only(self, org_id):
        from services.cdm_mapping import compute_mappings_v2

        session = _RecordingSyncSession()
        backend = SimpleNamespace(can_produce_mappings=True, name="stub")

        summary = compute_mappings_v2(
            session, org_id,
            extracted_text_loader=lambda doc: None,
            backend=backend,
        )

        assert summary.controls_processed == 0
        assert len(session.statements) == 1
        sql = str(session.statements[0][0])
        assert "scf_catalog_controls.status IS NULL" in sql
        assert "scf_catalog_controls.status =" in sql

    @pytest.mark.asyncio
    async def test_historical_mappings_resolve_badged(self, membership, org_id):
        from api.cdm import list_cdm_mappings

        mapping = SimpleNamespace(
            id=uuid4(), organization_id=org_id, scoped_control_id=uuid4(),
            cdm_document_id=uuid4(), section=None,
            byte_offset_start=0, byte_offset_end=42, relevance_score=0.9,
            status="accepted", kb_revision="rev1",
            accepted_by_user_id=None, accepted_at=None,
            dismiss_reason=None, dismissed_by_user_id=None, dismissed_at=None,
            excerpt="policy text", review_notes=None,
            last_reviewed_at=None, last_reviewed_by_user_id=None,
            created_at=datetime(2026, 6, 1),
            scf_id=None, original_filename=None,
            ts_rank_component=None, objective_coverage_component=None,
            term_overlap_component=None, score_weights=None,
            match_type=None, matched_objective_text=None,
            cdm_document_chunk_id=None, retrieval_tier=None,
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar=1),
            _result(all_rows=[
                (mapping, "TESTCTL5", "policy.pdf",
                 "deprecated", "2026.2", "TESTCTL6"),
            ]),
        ])

        resp = await list_cdm_mappings(
            org_id=org_id, _=None, membership=membership, db=db,
            control_id=None, status=None, limit=50, offset=0,
        )

        assert resp.total == 1
        row = resp.mappings[0]
        assert row.scf_id == "TESTCTL5"
        assert row.catalog_status == "deprecated"
        assert row.retired_in_version == "2026.2"
        assert row.superseded_by == "TESTCTL6"


# ---------------------------------------------------------------------------
# Consumer 9 — risk pickers
# ---------------------------------------------------------------------------

class TestConsumer9Risks:

    @pytest.mark.asyncio
    async def test_new_link_to_deprecated_control_is_refused(
        self, membership, org_id,
    ):
        from fastapi import HTTPException
        from api.custom_risks import add_custom_risk_control
        from schemas import CustomRiskControlMappingCreate

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=SimpleNamespace()),   # risk exists
            _result(scalar_one_or_none=None),                # no duplicate
            _result(first=("deprecated", "TESTCTL2")),       # catalog lifecycle
        ])

        with pytest.raises(HTTPException) as exc:
            await add_custom_risk_control(
                org_id=org_id, risk_code="CUSTOMRISK1",
                data=CustomRiskControlMappingCreate(scf_id="TESTCTL1"),
                request=MagicMock(), membership=membership, db=db,
            )

        assert exc.value.status_code == 409
        assert "deprecated" in exc.value.detail
        assert "TESTCTL2" in exc.value.detail  # successor hint

    @pytest.mark.asyncio
    async def test_existing_control_risk_mapping_resolves_badged(
        self, membership, org_id,
    ):
        from api.risk_assessments import get_risks_for_control

        # Deprecated control with no mapped risk codes: the early-return
        # branch still resolves it and carries the badge.
        catalog_row = ([], "deprecated", "2026.2", "TESTCTL2")

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(first=catalog_row),       # catalog risk_codes + lifecycle
        ])

        resp = await get_risks_for_control(
            org_id=org_id, scf_id="TESTCTL1",
            membership=membership, db=db,
        )

        assert resp["catalog_status"] == "deprecated"
        assert resp["retired_in_version"] == "2026.2"
        assert resp["superseded_by"] == "TESTCTL2"


# ---------------------------------------------------------------------------
# Consumer 10 — evidence tracking
# ---------------------------------------------------------------------------

class TestConsumer10EvidenceTracking:

    @pytest.mark.asyncio
    async def test_new_tracking_of_deprecated_erl_entry_is_refused(
        self, membership, org_id,
    ):
        from fastapi import HTTPException
        from api.evidence_tracking import create_or_update_evidence_tracking
        from schemas import EvidenceTrackingCreate

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=None),           # no existing tracking
            _result(first=("deprecated", "EVID300")),   # catalog lifecycle
        ])

        with pytest.raises(HTTPException) as exc:
            await create_or_update_evidence_tracking(
                org_id=org_id,
                tracking_data=EvidenceTrackingCreate(
                    evidence_id="EVID200", is_tracked=True,
                ),
                membership=membership, db=db,
            )

        assert exc.value.status_code == 409
        assert "deprecated" in exc.value.detail
        assert "EVID300" in exc.value.detail  # successor hint

    @pytest.mark.asyncio
    async def test_badge_helpers_stamp_lifecycle_fields(self):
        from api.evidence_tracking import _apply_badge

        tracking = SimpleNamespace()
        meta = SimpleNamespace(status="deprecated",
                               retired_in_version="2026.2",
                               superseded_by="EVID300")
        _apply_badge(tracking, meta)
        assert tracking.catalog_status == "deprecated"
        assert tracking.retired_in_version == "2026.2"
        assert tracking.superseded_by == "EVID300"

        active = SimpleNamespace()
        _apply_badge(active, None)
        assert active.catalog_status is None
        assert active.retired_in_version is None
        assert active.superseded_by is None


# ---------------------------------------------------------------------------
# Consumer 11 — assessment prompts
# ---------------------------------------------------------------------------

def _catalog_entry():
    return SimpleNamespace(
        artifact_title="Access review export",
        artifact_description="Quarterly access review",
        area_of_focus="Access control",
        control_mappings=["TESTCTL1"],
        catalog_version="2026.1",
    )


def _prompt_ctrl(*, deprecated):
    return SimpleNamespace(
        scf_id="TESTCTL1",
        control_name="Access reviews",
        control_description="Review access quarterly.",
        status="deprecated" if deprecated else "active",
        retired_in_version="2026.2" if deprecated else None,
        superseded_by="TESTCTL2" if deprecated else None,
    )


class TestConsumer11AssessmentPrompts:

    @pytest.mark.asyncio
    async def test_deprecated_control_resolves_with_note_in_prompt(self):
        from services.assessment_prompts import (
            assemble_control_context, build_assessment_prompt,
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=_catalog_entry()),
            _result(scalars_all=[_prompt_ctrl(deprecated=True)]),
        ])

        context = await assemble_control_context(db, "EVID100")

        assert context is not None
        ctrl = context.controls[0]
        assert ctrl["catalog_status"] == "deprecated"
        assert ctrl["retired_in_version"] == "2026.2"
        assert ctrl["superseded_by"] == "TESTCTL2"

        _system, user_prompt = build_assessment_prompt(
            context, extracted_text="some evidence text",
            filename="export.pdf", content_type="application/pdf",
        )
        assert "TESTCTL1 is deprecated in the SCF catalog" in user_prompt
        assert "retired in 2026.2" in user_prompt
        assert "superseded by TESTCTL2" in user_prompt

    @pytest.mark.asyncio
    async def test_active_control_context_shape_is_unchanged(self):
        """Hash stability: an active control's context entry carries exactly
        the pre-upgrade keys, so existing context hashes do not shift."""
        from services.assessment_prompts import (
            assemble_control_context, build_assessment_prompt,
        )

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[
            _result(scalar_one_or_none=_catalog_entry()),
            _result(scalars_all=[_prompt_ctrl(deprecated=False)]),
        ])

        context = await assemble_control_context(db, "EVID100")

        assert context is not None
        assert set(context.controls[0]) == {
            "scf_id", "control_name", "control_description",
        }

        _system, user_prompt = build_assessment_prompt(
            context, extracted_text="some evidence text",
            filename="export.pdf", content_type="application/pdf",
        )
        assert "deprecated" not in user_prompt
