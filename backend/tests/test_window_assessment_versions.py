"""Assurance parity for window assessments (winparity PR2).

What the per-file layer got in #881 WS3, the window layer gets here:

- ``evidence_window_assessment_versions``: a frozen, append-only copy of
  every verdict the window has received, with a one-shot review block
- a terminal-verdict writer that freezes the row *before* anyone sees it
- confirm / override on the window's current verdict, reason required on
  override, one decision per version
- the review queue listing windows (``tier=window``) in the same severity
  order as files
- confirmation weighting on the window and composite KSI tiers, keyed on
  the verdict-confirmation block (``review_decision``), not on the
  acceptance verb the attestation gate reads (``review_status``)
- composite recompute when a window's verdict is confirmed or overridden

No database. Migration and SQL are inspected as text; endpoints are called
directly with a mocked AsyncSession, the idiom of
``test_assessment_review_api.py``.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.assurance_policy import AssurancePolicy  # noqa: E402
# The v2 module's stub session and LLM patches; pytest picks the fixture up
# by name once it is bound here.
from test_window_assessment_v2 import stubbed  # noqa: E402,F401

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(
    BACKEND, "alembic", "versions", "20260916_150000_window_assessment_versions.py"
)

OPEN_POLICY = AssurancePolicy(require_evidence_attestation=False, require_reviewer_independence=False)
INDEPENDENT_POLICY = AssurancePolicy(require_evidence_attestation=False, require_reviewer_independence=True)

AO_FINDINGS = [
    {"ao_id": "ao_alpha", "suggested_designation": "appears_satisfied", "rationale": "Shown.", "suggestion": "", "evidence_file_ids": []},
    {"ao_id": "ao_bravo", "suggested_designation": "gap_identified", "rationale": "Missing.", "suggestion": "Add it.", "evidence_file_ids": []},
    {"ao_id": "ao_charlie", "suggested_designation": "appears_satisfied", "rationale": "Shown.", "suggestion": "", "evidence_file_ids": []},
]


# ---------------------------------------------------------------------------
# Migration: a true mirror of evidence_assessment_versions
# ---------------------------------------------------------------------------

def _migration_source() -> str:
    with open(MIGRATION) as fh:
        return fh.read()


class TestMigration:
    def test_chains_off_the_window_v2_columns(self):
        src = _migration_source()
        assert re.search(r'^revision = "winasver1"$', src, re.M)
        assert re.search(r'^down_revision = "winasv2cols1"$', src, re.M)

    def test_parses(self):
        ast.parse(_migration_source())

    def test_table_and_parent_pointer_columns(self):
        src = _migration_source()
        assert '"evidence_window_assessment_versions"' in src
        for col in ("current_version_id", "version_number", "review_decision", "review_reason",
                    "verdict_reviewed_by_user_id", "verdict_reviewed_at"):
            assert f'sa.Column("{col}"' in src, col
        assert "fk_evidence_window_assessments_current_version" in src

    def test_append_only_triggers_and_one_shot_review_block(self):
        src = _migration_source()
        for fn in ("evidence_window_assessment_versions_refuse_update",
                   "evidence_window_assessment_versions_refuse_delete",
                   "evidence_window_assessment_versions_refuse_truncate"):
            assert fn in src, fn
        for trg in ("evidence_window_assessment_versions_no_update",
                    "evidence_window_assessment_versions_no_delete",
                    "evidence_window_assessment_versions_no_truncate"):
            assert trg in src, trg
        # The review block may be written once, from NULL; nothing else moves.
        assert "IF OLD.review_decision IS NULL" in src
        for field in ("review_decision", "review_reason", "reviewed_by_user_id", "reviewed_at", "ao_overrides"):
            assert f"- '{field}'" in src, field

    def test_delete_is_permitted_only_when_the_parent_is_gone(self):
        src = _migration_source()
        assert "organizations" in src and "evidence_window_assessments" in src
        assert "NOT EXISTS" in src

    def test_backfill_freezes_every_terminal_row_as_version_one(self):
        src = _migration_source()
        for status in ("sufficient", "partial", "insufficient", "insufficient_sample", "unassessable", "error"):
            assert f"'{status}'" in src, status
        assert "COALESCE(ewa.schema_version, 1)" in src

    def test_awaiting_queue_index_is_partial_on_undecided_reviewable_rows(self):
        src = _migration_source()
        assert "ix_evidence_window_assessments_org_awaiting" in src
        assert "review_decision IS NULL" in src

    def test_downgrade_reverses_everything(self):
        src = _migration_source()
        down = src.split("def downgrade")[1]
        for name in ("evidence_window_assessment_versions_no_update",
                     "evidence_window_assessment_versions_refuse_update",
                     "ix_evidence_window_assessments_org_awaiting",
                     "fk_evidence_window_assessments_current_version",
                     '"evidence_window_assessment_versions"'):
            assert name in down, name
        for col in ("current_version_id", "version_number", "review_decision", "review_reason",
                    "verdict_reviewed_by_user_id", "verdict_reviewed_at"):
            assert f'"{col}"' in down, col


class TestModel:
    def test_version_model_mirrors_the_table(self):
        from models import EvidenceWindowAssessment, EvidenceWindowAssessmentVersion

        cols = {c.name for c in EvidenceWindowAssessmentVersion.__table__.columns}
        for col in ("window_assessment_id", "organization_id", "evidence_id", "version_number",
                    "schema_version", "file_ids", "file_membership", "status", "ao_findings",
                    "gap_count", "cannot_assess_count", "file_effective_dates", "window_hash",
                    "prompt_version", "review_decision", "review_reason", "reviewed_by_user_id",
                    "reviewed_at", "ao_overrides"):
            assert col in cols, col
        parent = {c.name for c in EvidenceWindowAssessment.__table__.columns}
        for col in ("current_version_id", "version_number", "review_decision", "review_reason",
                    "verdict_reviewed_by_user_id", "verdict_reviewed_at"):
            assert col in parent, col

    def test_confirmation_block_is_distinct_from_the_acceptance_verb(self):
        # review_status (approved / rejected / needs_revision) says what the org
        # decided to do with the evidence; review_decision says whether a person
        # stood behind the AI's reading. They must not share a column.
        from models import EvidenceWindowAssessment

        parent = {c.name for c in EvidenceWindowAssessment.__table__.columns}
        assert {"review_status", "review_decision"} <= parent


# ---------------------------------------------------------------------------
# Terminal-verdict writer
# ---------------------------------------------------------------------------

class _WriterSession:
    def __init__(self, returning):
        self.returning = returning
        self.log = []

    def flush(self):
        self.log.append(("flush",))

    def execute(self, stmt, params=None):
        self.log.append(("execute", str(stmt), params))
        r = MagicMock()
        r.first.return_value = self.returning
        return r

    def commit(self):
        self.log.append(("commit",))

    def expire(self, obj, names=None):
        self.log.append(("expire", tuple(names or ())))


class TestTerminalVerdictWriter:
    def test_freezes_then_points_then_commits_then_expires(self):
        from services import window_assessment_service as svc

        session = _WriterSession(returning=(3,))
        ewa = MagicMock(); ewa.id = uuid4()
        got = svc._write_window_terminal_verdict(session, ewa)

        kinds = [e[0] for e in session.log]
        assert kinds == ["flush", "execute", "execute", "commit", "expire"]
        insert, update = session.log[1], session.log[2]
        assert "INSERT INTO evidence_window_assessment_versions" in insert[1]
        assert "RETURNING version_number" in insert[1]
        assert insert[2]["assessment_id"] == ewa.id
        assert "UPDATE evidence_window_assessments" in update[1]
        assert update[2]["version_number"] == 3
        assert update[2]["version_id"] == insert[2]["version_id"]
        assert got == 3
        assert set(session.log[-1][1]) >= {"current_version_id", "version_number", "review_decision"}

    def test_version_number_is_computed_in_sql_from_the_parent(self):
        from services import window_assessment_service as svc

        sql = str(svc._INSERT_WINDOW_VERSION_SQL)
        assert "COALESCE(ewa.version_number, 0) + 1" in sql
        assert "FROM evidence_window_assessments ewa" in sql

    def test_pointer_update_resets_the_confirmation_block(self):
        from services import window_assessment_service as svc

        sql = str(svc._POINT_CURRENT_VERSION_SQL)
        for clause in ("review_decision = NULL", "review_reason = NULL",
                       "verdict_reviewed_by_user_id = NULL", "verdict_reviewed_at = NULL"):
            assert clause in sql, clause

    def test_missing_parent_row_commits_without_a_pointer(self):
        from services import window_assessment_service as svc

        session = _WriterSession(returning=None)
        ewa = MagicMock(); ewa.id = uuid4()
        got = svc._write_window_terminal_verdict(session, ewa)
        kinds = [e[0] for e in session.log]
        assert kinds == ["flush", "execute", "commit"]
        assert got is None

    def test_the_acceptance_verb_is_not_touched_by_a_new_verdict(self):
        # review_status belongs to the org's acceptance workflow; a re-run of
        # the AI must not silently un-approve evidence.
        from services import window_assessment_service as svc

        assert "review_status" not in str(svc._POINT_CURRENT_VERSION_SQL)


@pytest.mark.usefixtures("stubbed")
class TestAssessWindowWritesVersions:
    """Every terminal path freezes a version — the reviewable ones and the
    ones a reviewer must be able to see failed."""

    def _version_writes(self, session):
        return [s for s in session.statements if "INSERT INTO evidence_window_assessment_versions" in str(s[0])]

    def test_success_path_freezes_a_version(self):
        from test_window_assessment_v2 import _Row, _Session, _answer, _tracking
        from services import window_assessment_service as svc

        rows = [_Row()]
        fake_llm = lambda s, u: {"content": _answer([str(rows[0].id)]), "model": "m", "input_tokens": 1, "output_tokens": 1, "stop_reason": "end_turn"}
        session = _Session(_tracking(), rows)
        with patch.object(svc, "_call_llm", fake_llm):
            result = svc.assess_window(session, organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.status in ("sufficient", "partial", "insufficient", "unassessable")
        assert len(self._version_writes(session)) == 1
        assert session.flushes >= 1 and session.commits >= 1
        assert any("current_version_id" in names for _, names in session.expired)

    def test_error_path_freezes_a_version(self):
        from test_window_assessment_v2 import _Row, _Session, _tracking
        from services import window_assessment_service as svc

        fake_llm = lambda s, u: {"content": '{"relevance_score": 8', "model": "m", "input_tokens": 1, "output_tokens": 32000, "stop_reason": "max_tokens"}
        session = _Session(_tracking(), [_Row()])
        with patch.object(svc, "_call_llm", fake_llm):
            result = svc.assess_window(session, organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.status == "error"
        assert len(self._version_writes(session)) == 1

    def test_no_files_path_freezes_a_version(self):
        from test_window_assessment_v2 import _Session, _tracking
        from services import window_assessment_service as svc

        session = _Session(_tracking(), [])
        result = svc.assess_window(session, organization_id=uuid4(), evidence_id="E-BCD-01")
        assert result.status == "insufficient_sample"
        assert len(self._version_writes(session)) == 1

    def test_processing_branch_clears_a_prior_decision(self):
        from services import window_assessment_service as svc
        import inspect

        src = inspect.getsource(svc.assess_window)
        idx = src.index('assessment.status = "processing"')
        tail = src[idx:idx + 1500]
        for field in ("review_decision", "review_reason", "verdict_reviewed_by_user_id", "verdict_reviewed_at"):
            assert f"assessment.{field} = None" in tail, field


# ---------------------------------------------------------------------------
# Confirm / override endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


@pytest.fixture
def membership(org_id, user_id):
    m = MagicMock()
    m.organization_id = org_id
    m.user = MagicMock()
    m.user.id = user_id
    m.user.db_id = str(user_id)
    m.role = "editor"
    return m


@pytest.fixture
def request_obj():
    return MagicMock()


def make_ewa(org_id, *, status="partial", version_id=None, review_decision=None, file_ids=None, ao_findings=None):
    e = MagicMock()
    e.id = uuid4()
    e.organization_id = org_id
    e.evidence_id = "E-BCD-01"
    e.status = status
    e.relevance_score = 70.0
    e.summary = "Mostly."
    e.findings = []
    e.ao_findings = [dict(f) for f in (AO_FINDINGS if ao_findings is None else ao_findings)]
    e.gap_count = 1
    e.cannot_assess_count = 0
    e.unassessable_reason = None
    e.file_ids = file_ids if file_ids is not None else [str(uuid4()), str(uuid4())]
    e.current_version_id = version_id if version_id is not None else uuid4()
    e.version_number = 2
    e.review_decision = review_decision
    e.review_reason = None
    e.verdict_reviewed_by_user_id = None
    e.verdict_reviewed_at = None
    return e


def make_version(ewa, *, review_decision=None, ao_findings=None):
    v = MagicMock()
    v.id = ewa.current_version_id
    v.window_assessment_id = ewa.id
    v.organization_id = ewa.organization_id
    v.version_number = ewa.version_number
    v.schema_version = 2
    v.status = ewa.status
    v.ao_findings = [dict(f) for f in (AO_FINDINGS if ao_findings is None else ao_findings)]
    v.review_decision = review_decision
    v.review_reason = None
    v.reviewed_by_user_id = None
    v.reviewed_at = None
    v.ao_overrides = None
    return v


def make_db(*results, uploaders=None):
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    seq = []
    for value in results:
        r = MagicMock()
        r.scalar_one_or_none = MagicMock(return_value=value)
        seq.append(r)
    if uploaders is not None:
        r = MagicMock()
        r.scalars.return_value.all.return_value = uploaders
        seq.append(r)
    db.execute = AsyncMock(side_effect=seq)
    return db


def body(**kwargs):
    from schemas import EvidenceAssessmentReviewRequest

    return EvidenceAssessmentReviewRequest(**kwargs)


async def call_verdict(db, membership, org_id, ewa_id, req_body, request_obj, policy=OPEN_POLICY):
    from api.evidence_window_assessment import review_window_verdict

    with patch("api.evidence_window_assessment.log_entity_changes", new=AsyncMock()) as audit, \
         patch("api.evidence_window_assessment.get_assurance_policy", new=AsyncMock(return_value=policy)):
        result = await review_window_verdict(
            org_id=org_id, assessment_id=ewa_id, body=req_body, request=request_obj,
            membership=membership, db=db,
        )
    return result, audit


class TestConfirm:
    @pytest.mark.asyncio
    async def test_confirm_lands_on_the_version_and_the_parent(self, org_id, user_id, membership, request_obj):
        ewa = make_ewa(org_id)
        version = make_version(ewa)
        db = make_db(ewa, version)
        result, audit = await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj)

        assert result is ewa
        assert version.review_decision == "confirmed"
        assert version.reviewed_by_user_id == user_id
        assert isinstance(version.reviewed_at, datetime)
        assert version.ao_overrides is None
        assert ewa.review_decision == "confirmed"
        assert ewa.verdict_reviewed_by_user_id == user_id
        assert ewa.verdict_reviewed_at == version.reviewed_at
        # A confirmation changes no verdict.
        assert ewa.status == "partial" and ewa.gap_count == 1
        db.commit.assert_awaited_once()
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["entity_type"] == "evidence_window_assessment"

    @pytest.mark.asyncio
    async def test_confirm_does_not_touch_the_acceptance_verb(self, org_id, membership, request_obj):
        ewa = make_ewa(org_id)
        ewa.review_status = "approved"
        db = make_db(ewa, make_version(ewa))
        await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj)
        assert ewa.review_status == "approved"

    @pytest.mark.asyncio
    async def test_missing_window_is_404(self, org_id, membership, request_obj):
        from fastapi import HTTPException

        db = make_db(None)
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, uuid4(), body(decision="confirmed"), request_obj)
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["pending", "processing", "error"])
    async def test_non_reviewable_status_is_409(self, org_id, membership, request_obj, status):
        from fastapi import HTTPException

        ewa = make_ewa(org_id, status=status)
        db = make_db(ewa)
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj)
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_window_without_a_version_is_409(self, org_id, membership, request_obj):
        from fastapi import HTTPException

        ewa = make_ewa(org_id)
        ewa.current_version_id = None
        db = make_db(ewa)
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj)
        assert exc.value.status_code == 409
        assert "no recorded version" in exc.value.detail

    @pytest.mark.asyncio
    async def test_one_decision_per_version(self, org_id, membership, request_obj):
        from fastapi import HTTPException

        ewa = make_ewa(org_id, review_decision="confirmed")
        db = make_db(ewa, make_version(ewa, review_decision="confirmed"))
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, body(decision="overridden", reason="changed my mind",
                                                                     ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "gap_identified"}]), request_obj)
        assert exc.value.status_code == 409
        assert "already confirmed" in exc.value.detail

    @pytest.mark.asyncio
    async def test_insufficient_sample_is_confirmable(self, org_id, membership, request_obj):
        ewa = make_ewa(org_id, status="insufficient_sample", ao_findings=[])
        db = make_db(ewa, make_version(ewa, ao_findings=[]))
        result, _ = await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj)
        assert result.review_decision == "confirmed"


class TestOverride:
    @pytest.mark.asyncio
    async def test_override_needs_a_reason(self):
        with pytest.raises(Exception):
            body(decision="overridden", ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "gap_identified"}])

    @pytest.mark.asyncio
    async def test_override_needs_at_least_one_objective(self):
        with pytest.raises(Exception):
            body(decision="overridden", reason="because", ao_overrides=[])

    @pytest.mark.asyncio
    async def test_override_recomputes_status_and_counts_and_snapshots_the_ai_designation(self, org_id, user_id, membership, request_obj):
        ewa = make_ewa(org_id)
        version = make_version(ewa)
        db = make_db(ewa, version)
        req = body(decision="overridden", reason="The gap is a wording quirk, not a gap.",
                   ao_overrides=[{"ao_id": "ao_bravo", "human_designation": "appears_satisfied", "note": "cadence is in the appendix"}])
        result, _ = await call_verdict(db, membership, org_id, ewa.id, req, request_obj)

        assert result.status == "sufficient"
        assert result.gap_count == 0 and result.cannot_assess_count == 0
        bravo = next(f for f in result.ao_findings if f["ao_id"] == "ao_bravo")
        assert bravo["suggested_designation"] == "appears_satisfied"
        assert bravo["overridden_by_human"] is True
        assert bravo["override_note"] == "cadence is in the appendix"
        alpha = next(f for f in result.ao_findings if f["ao_id"] == "ao_alpha")
        assert "overridden_by_human" not in alpha

        assert version.review_decision == "overridden"
        assert version.review_reason == req.reason
        assert version.ao_overrides == [{"ao_id": "ao_bravo", "ai_designation": "gap_identified",
                                         "human_designation": "appears_satisfied", "note": "cadence is in the appendix"}]
        # The frozen answers are untouched: the override is recorded beside them.
        assert next(f for f in version.ao_findings if f["ao_id"] == "ao_bravo")["suggested_designation"] == "gap_identified"

    @pytest.mark.asyncio
    async def test_override_can_downgrade(self, org_id, membership, request_obj):
        ewa = make_ewa(org_id, status="sufficient")
        ewa.ao_findings = [dict(f, suggested_designation="appears_satisfied") for f in AO_FINDINGS]
        ewa.gap_count = 0
        version = make_version(ewa, ao_findings=ewa.ao_findings)
        db = make_db(ewa, version)
        req = body(decision="overridden", reason="None of these are shown.",
                   ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "gap_identified"},
                                 {"ao_id": "ao_bravo", "human_designation": "gap_identified"},
                                 {"ao_id": "ao_charlie", "human_designation": "gap_identified"}])
        result, _ = await call_verdict(db, membership, org_id, ewa.id, req, request_obj)
        assert result.status == "insufficient"
        assert result.gap_count == 3

    @pytest.mark.asyncio
    async def test_insufficient_sample_keeps_its_status_through_an_override(self, org_id, membership, request_obj):
        ewa = make_ewa(org_id, status="insufficient_sample")
        version = make_version(ewa)
        db = make_db(ewa, version)
        req = body(decision="overridden", reason="Objective bravo is met.",
                   ao_overrides=[{"ao_id": "ao_bravo", "human_designation": "appears_satisfied"}])
        result, _ = await call_verdict(db, membership, org_id, ewa.id, req, request_obj)
        assert result.status == "insufficient_sample"
        assert result.gap_count == 0
        assert version.review_decision == "overridden"

    @pytest.mark.asyncio
    async def test_unknown_objective_is_422(self, org_id, membership, request_obj):
        from fastapi import HTTPException

        ewa = make_ewa(org_id)
        db = make_db(ewa, make_version(ewa))
        req = body(decision="overridden", reason="x", ao_overrides=[{"ao_id": "ao_zulu", "human_designation": "gap_identified"}])
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, req, request_obj)
        assert exc.value.status_code == 422
        assert "ao_zulu" in exc.value.detail

    @pytest.mark.asyncio
    async def test_duplicate_objective_is_422(self, org_id, membership, request_obj):
        from fastapi import HTTPException

        ewa = make_ewa(org_id)
        db = make_db(ewa, make_version(ewa))
        req = body(decision="overridden", reason="x",
                   ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "gap_identified"},
                                 {"ao_id": "ao_alpha", "human_designation": "not_applicable"}])
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, req, request_obj)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_legacy_verdict_without_objectives_cannot_be_overridden(self, org_id, membership, request_obj):
        from fastapi import HTTPException

        ewa = make_ewa(org_id, ao_findings=[])
        db = make_db(ewa, make_version(ewa, ao_findings=[]))
        req = body(decision="overridden", reason="x", ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "gap_identified"}])
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, req, request_obj)
        assert exc.value.status_code == 422
        assert "no per-objective answers" in exc.value.detail

    @pytest.mark.asyncio
    async def test_reviewer_uses_advisory_vocabulary_only(self):
        with pytest.raises(Exception):
            body(decision="overridden", reason="x", ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "compliant"}])


class TestSegregationOfDuties:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("decision", ["confirmed", "overridden"])
    async def test_sole_uploader_is_refused_when_independence_is_required(self, org_id, user_id, membership, request_obj, decision):
        from fastapi import HTTPException
        from services.review_workflow import SOD_REFUSAL_DETAIL

        ewa = make_ewa(org_id)
        db = make_db(ewa, make_version(ewa), uploaders=[user_id, user_id])
        kwargs = {"decision": decision}
        if decision == "overridden":
            kwargs.update(reason="x", ao_overrides=[{"ao_id": "ao_alpha", "human_designation": "gap_identified"}])
        with pytest.raises(HTTPException) as exc:
            await call_verdict(db, membership, org_id, ewa.id, body(**kwargs), request_obj, policy=INDEPENDENT_POLICY)
        assert exc.value.status_code == 403
        assert exc.value.detail == SOD_REFUSAL_DETAIL
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_second_uploader_makes_the_reviewer_independent(self, org_id, user_id, membership, request_obj):
        ewa = make_ewa(org_id)
        db = make_db(ewa, make_version(ewa), uploaders=[user_id, uuid4()])
        result, _ = await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj, policy=INDEPENDENT_POLICY)
        assert result.review_decision == "confirmed"

    @pytest.mark.asyncio
    async def test_open_policy_does_not_look_up_uploaders(self, org_id, user_id, membership, request_obj):
        ewa = make_ewa(org_id)
        db = make_db(ewa, make_version(ewa))  # a third execute would raise StopIteration
        result, _ = await call_verdict(db, membership, org_id, ewa.id, body(decision="confirmed"), request_obj, policy=OPEN_POLICY)
        assert result.review_decision == "confirmed"


class TestVersionsEndpoint:
    @pytest.mark.asyncio
    async def test_lists_newest_first_within_the_org(self, org_id, membership):
        from api.evidence_window_assessment import list_window_assessment_versions

        ewa = make_ewa(org_id)
        rows = []
        for n in (3, 2, 1):
            v = make_version(ewa)
            v.id = uuid4(); v.version_number = n
            v.window_start = datetime(2026, 1, 1); v.window_end = datetime(2026, 2, 5)
            v.frequency_used = "monthly"; v.file_ids = []; v.file_membership = {}
            v.relevance_score = 70.0; v.summary = "s"; v.findings = []
            v.gap_count = 1; v.cannot_assess_count = 0; v.file_effective_dates = []
            v.unassessable_reason = None; v.model_id = "m"; v.prompt_version = "2.1.0"
            v.assessment_source = "on_demand"; v.assessed_at = datetime.utcnow(); v.created_at = datetime.utcnow()
            rows.append(v)
        db = AsyncMock()
        r = MagicMock(); r.scalars.return_value.all.return_value = rows
        db.execute = AsyncMock(return_value=r)
        out = await list_window_assessment_versions(org_id=org_id, assessment_id=ewa.id, membership=membership, db=db)
        assert [v.version_number for v in out] == [3, 2, 1]
        stmt = str(db.execute.await_args.args[0])
        assert "organization_id" in stmt and "window_assessment_id" in stmt
        assert "ORDER BY evidence_window_assessment_versions.version_number DESC" in stmt


# ---------------------------------------------------------------------------
# Review queue: tier=window
# ---------------------------------------------------------------------------

class TestWindowReviewQueue:
    def test_window_query_orders_worst_first_and_is_tenant_scoped(self, org_id):
        from api.evidence_assessment import build_window_review_queue_query

        sql = str(build_window_review_queue_query(org_id, "awaiting", 50, 0))
        assert "evidence_window_assessments.organization_id = " in sql
        assert re.search(r"ORDER BY evidence_window_assessments\.gap_count DESC, "
                         r"evidence_window_assessments\.cannot_assess_count DESC, "
                         r"evidence_window_assessments\.relevance_score ASC NULLS LAST, "
                         r"evidence_window_assessments\.assessed_at ASC", sql), sql
        assert "evidence_window_assessments.review_decision IS NULL" in sql
        assert "evidence_window_assessments.current_version_id IS NOT NULL" in sql

    def test_reviewed_filter_uses_the_confirmation_block_not_the_acceptance_verb(self, org_id):
        from api.evidence_assessment import build_window_review_queue_query

        sql = str(build_window_review_queue_query(org_id, "reviewed", 50, 0))
        assert "review_decision IS NOT NULL" in sql
        assert "review_status" not in sql

    def test_all_filter_has_no_decision_predicate(self, org_id):
        from api.evidence_assessment import build_window_review_queue_query

        sql = str(build_window_review_queue_query(org_id, "all", 50, 0))
        assert "review_decision IS" not in sql

    def test_queue_statuses_match_what_the_review_endpoint_accepts(self):
        from api.evidence_assessment import _WINDOW_QUEUE_STATUSES
        from api.evidence_window_assessment import WINDOW_REVIEWABLE_STATUSES

        assert set(_WINDOW_QUEUE_STATUSES) == set(WINDOW_REVIEWABLE_STATUSES)

    @pytest.mark.asyncio
    async def test_tier_window_returns_window_items(self, org_id, membership):
        from api.evidence_assessment import get_review_queue

        row = MagicMock()
        row.window_assessment_id = uuid4(); row.evidence_id = "E-BCD-01"
        row.window_start = datetime(2026, 1, 1); row.window_end = datetime(2026, 2, 5)
        row.frequency_used = "monthly"; row.file_ids = ["a", "b", "c"]
        row.status = "partial"; row.relevance_score = 61.5; row.gap_count = 2; row.cannot_assess_count = 1
        row.version_number = 3; row.assessed_at = datetime.utcnow(); row.review_decision = None; row.verdict_reviewed_at = None
        rows_r = MagicMock(); rows_r.all.return_value = [row]
        count_r = MagicMock(); count_r.scalar.return_value = 1
        db = AsyncMock(); db.execute = AsyncMock(side_effect=[rows_r, count_r])

        out = await get_review_queue(org_id=org_id, status="awaiting", tier="window", limit=50, offset=0, membership=membership, db=db)
        assert out.total == 1
        item = out.items[0]
        assert item.kind == "window"
        assert item.window_assessment_id == row.window_assessment_id
        assert item.file_id is None
        assert item.file_count == 3
        assert item.frequency_used == "monthly"
        assert item.gap_count == 2 and item.cannot_assess_count == 1
        assert item.version_number == 3

    @pytest.mark.asyncio
    async def test_unknown_tier_is_422(self, org_id, membership):
        from fastapi import HTTPException
        from api.evidence_assessment import get_review_queue

        with pytest.raises(HTTPException) as exc:
            await get_review_queue(org_id=org_id, status="awaiting", tier="composite", limit=50, offset=0, membership=membership, db=AsyncMock())
        assert exc.value.status_code == 422

    def test_file_items_say_so(self):
        from schemas import AssessmentReviewQueueItem

        assert AssessmentReviewQueueItem.model_fields["kind"].default == "file"


# ---------------------------------------------------------------------------
# KSI: confirmation weighting on the window and composite tiers
# ---------------------------------------------------------------------------

class TestKsiConfirmationCounts:
    @pytest.mark.parametrize("attested", [False, True])
    def test_window_tier_emits_confirmed_counts(self, attested):
        from api.capability_themes import _build_window_aware_sql

        sql = str(_build_window_aware_sql(attested))
        for col in ("sufficient_confirmed_count", "partial_confirmed_count", "insufficient_confirmed_count"):
            assert f"AS {col}" in sql, col
        assert "(ewa.review_decision IS NOT NULL) AS window_confirmed" in sql
        assert "COALESCE(ws.window_confirmed, ea.review_decision IS NOT NULL) AS assessment_confirmed" in sql

    @pytest.mark.parametrize("window_enabled,attested", [(False, False), (False, True), (True, False), (True, True)])
    def test_composite_tier_emits_confirmed_counts(self, window_enabled, attested):
        from api.capability_themes import _build_composite_aware_sql

        sql = str(_build_composite_aware_sql(window_enabled, attested))
        for col in ("sufficient_confirmed_count", "partial_confirmed_count", "insufficient_confirmed_count"):
            assert f"AS {col}" in sql, col
        assert "jsonb_array_length(cac.included_window_ids) > 0" in sql
        assert "ewa3.review_decision IS NULL" in sql

    def test_confirmation_is_keyed_on_the_decision_not_the_acceptance_verb(self):
        # review_status is the attestation gate's business; the confirmation
        # weight must not double-count it.
        from api.capability_themes import _COMPOSITE_CONFIRMED, _MIXED_CONFIRMED, _WINDOW_CONFIRMED

        for frag in (_COMPOSITE_CONFIRMED, _MIXED_CONFIRMED, _WINDOW_CONFIRMED):
            assert "review_decision" in frag and "review_status" not in frag

    def test_composite_confirmed_requires_every_folded_window(self):
        from api.capability_themes import _COMPOSITE_CONFIRMED

        assert "NOT EXISTS" in _COMPOSITE_CONFIRMED
        assert "ewa3.id IS NULL" in _COMPOSITE_CONFIRMED  # a dangling id is not a confirmed window

    def test_confirmed_counts_are_subsets_of_their_status_counts(self):
        # Textually: each confirmed filter carries the same status predicate
        # plus the confirmed flag, so it can never exceed the unweighted count.
        from api.capability_themes import _build_composite_aware_sql, _build_window_aware_sql

        for sql in (str(_build_window_aware_sql(False)), str(_build_composite_aware_sql(True, False))):
            for status in ("sufficient", "partial", "insufficient"):
                assert re.search(
                    rf"assessment_status = '{status}' AND (u\.)?assessment_confirmed\) AS {status}_confirmed_count", sql
                ), (status, sql)

    def test_axis_bundle_weights_the_window_tier(self):
        from types import SimpleNamespace

        from api.capability_themes import _compute_axis_bundle
        from api.ksi_scoring import EQ_UNCONFIRMED_WEIGHT
        from schemas import CapabilityThemePosture

        posture = CapabilityThemePosture(
            implemented=4, monitored=0, ready_for_review=0, in_progress=0,
            not_started=0, at_risk=0, not_applicable=0, deferred=0,
        )
        base = dict(theme_code="BCD", controls_with_evidence=4, total_evidence_files=4, sufficient_count=4,
                    partial_count=0, insufficient_count=0, insufficient_sample_count=0, unassessable_count=0,
                    pending_count=0, unassessed_count=0, avg_relevance_score=80.0)
        unconfirmed = _compute_axis_bundle(
            posture=posture, scoped=4, maturity_score=None,
            evidence_row=SimpleNamespace(**base, sufficient_confirmed_count=0, partial_confirmed_count=0, insufficient_confirmed_count=0),
        )
        confirmed = _compute_axis_bundle(
            posture=posture, scoped=4, maturity_score=None,
            evidence_row=SimpleNamespace(**base, sufficient_confirmed_count=4, partial_confirmed_count=0, insufficient_confirmed_count=0),
        )
        assert EQ_UNCONFIRMED_WEIGHT < 1.0
        assert unconfirmed["evidence_quality"] < confirmed["evidence_quality"]


# ---------------------------------------------------------------------------
# Composite recompute on verdict confirmation
# ---------------------------------------------------------------------------

class TestCompositeListener:
    def _session(self, ewa):
        s = MagicMock(); s.info = {}; s.new = []; s.dirty = [ewa]
        return s

    def test_review_decision_change_fires(self, monkeypatch):
        from sqlalchemy.orm import attributes as orm_attributes
        from models import EvidenceWindowAssessment
        from services.composite_service import _before_flush_handler

        ewa = EvidenceWindowAssessment(organization_id=uuid4(), evidence_id="E-1", status="partial",
                                       window_start=datetime(2026, 1, 1), window_end=datetime(2026, 2, 1), frequency_used="monthly")

        class H:
            def __init__(self, deleted=(), added=()):
                self.deleted = list(deleted); self.added = list(added)

        def by_attr(obj, attr):
            if attr == "status":
                return H(deleted=["partial"])  # terminal -> terminal: the status branch stays quiet
            if attr == "review_decision":
                return H(deleted=[None], added=["confirmed"])
            return H()
        monkeypatch.setattr(orm_attributes, "get_history", by_attr)
        session = self._session(ewa)
        _before_flush_handler(session, None, None)
        assert len(session.info["_composite_pending"]) == 1
        assert session.info["_composite_pending"][0][1] == "E-1"

    def test_no_change_stays_quiet(self, monkeypatch):
        from sqlalchemy.orm import attributes as orm_attributes
        from models import EvidenceWindowAssessment
        from services.composite_service import _before_flush_handler

        ewa = EvidenceWindowAssessment(organization_id=uuid4(), evidence_id="E-1", status="partial",
                                       window_start=datetime(2026, 1, 1), window_end=datetime(2026, 2, 1), frequency_used="monthly")

        class H:
            deleted = []
            added = []
        monkeypatch.setattr(orm_attributes, "get_history", lambda obj, attr: H())
        session = self._session(ewa)
        _before_flush_handler(session, None, None)
        assert session.info.get("_composite_pending", []) == []
