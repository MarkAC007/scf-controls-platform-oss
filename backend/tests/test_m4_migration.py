"""Migration sanity test for the M4 PR 1 per-window review columns (#574).

Mirrors ``test_composite_migration.py`` from M3 — AST inspection of the
migration's upgrade/downgrade bodies, plus an ORM parity check. Avoids
needing a live Postgres instance.

Validates:
  * Revision id chains (kk2l3m4n5o6p ← jj1k2l3m4n5o)
  * Upgrade adds the four review columns to evidence_window_assessments
  * Upgrade creates the composite (organization_id, review_status) index
  * Upgrade is purely additive (no ALTER COLUMN, no drops, no new tables)
  * Downgrade drops the index and the four columns reverse-pure
  * ORM model `EvidenceWindowAssessment` exposes the four new attributes
"""
from __future__ import annotations

import ast
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


MIGRATIONS_DIR = (
    pathlib.Path(__file__).resolve().parents[1] / "alembic" / "versions"
)
MIGRATION_FILE = (
    MIGRATIONS_DIR / "20260509_140000_add_per_window_review_columns.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    assert MIGRATION_FILE.exists(), f"Migration file missing: {MIGRATION_FILE}"
    return MIGRATION_FILE.read_text()


@pytest.fixture(scope="module")
def migration_module(migration_source: str):
    return ast.parse(migration_source)


# ---------------------------------------------------------------------------
# Header / chain
# ---------------------------------------------------------------------------


class TestRevisionMetadata:
    def test_revision_id_locked(self, migration_source):
        assert "revision: str = 'kk2l3m4n5o6p'" in migration_source

    def test_down_revision_chains_from_m3(self, migration_source):
        assert "down_revision: Union[str, None] = 'jj1k2l3m4n5o'" in migration_source

    def test_filename_matches_pattern(self):
        # YYYYMMDD_HHMMSS_<slug>.py
        assert MIGRATION_FILE.name == "20260509_140000_add_per_window_review_columns.py"


# ---------------------------------------------------------------------------
# Upgrade body — additive columns + index
# ---------------------------------------------------------------------------


class TestUpgrade:
    def _upgrade_body_text(self, migration_module) -> str:
        for node in migration_module.body:
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
                return ast.unparse(node)
        pytest.fail("upgrade() function not found in migration")

    def test_targets_evidence_window_assessments(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "'evidence_window_assessments'" in body

    def test_adds_review_status_column(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "'review_status'" in body
        # NOT NULL with server_default 'not_reviewed'
        assert "nullable=False" in body
        assert "'not_reviewed'" in body
        assert "String(20)" in body

    def test_adds_reviewed_by_user_id_column(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "'reviewed_by_user_id'" in body
        # FK to users.id with ON DELETE SET NULL
        assert "'users.id'" in body
        assert "ondelete='SET NULL'" in body or 'ondelete="SET NULL"' in body

    def test_adds_reviewed_at_column(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "'reviewed_at'" in body
        assert "DateTime" in body

    def test_adds_review_notes_column(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "'review_notes'" in body
        assert "sa.Text" in body

    def test_creates_composite_review_index(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "ix_evidence_window_assessments_org_review" in body
        # (organization_id, review_status)
        assert "'organization_id'" in body
        assert "'review_status'" in body

    def test_no_alter_column_or_drops(self, migration_module):
        # Pure additive — no shape changes on existing columns.
        body = self._upgrade_body_text(migration_module)
        assert "alter_column" not in body
        assert "drop_table" not in body
        assert "drop_column" not in body
        assert "drop_index" not in body

    def test_does_not_create_new_tables(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "create_table" not in body


# ---------------------------------------------------------------------------
# Downgrade body — full reversal
# ---------------------------------------------------------------------------


class TestDowngrade:
    def _downgrade_body_text(self, migration_module) -> str:
        for node in migration_module.body:
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                return ast.unparse(node)
        pytest.fail("downgrade() function not found in migration")

    def test_drops_index(self, migration_module):
        body = self._downgrade_body_text(migration_module)
        assert "ix_evidence_window_assessments_org_review" in body
        assert "drop_index" in body

    def test_drops_each_review_column(self, migration_module):
        body = self._downgrade_body_text(migration_module)
        for col in [
            "'review_status'",
            "'reviewed_by_user_id'",
            "'reviewed_at'",
            "'review_notes'",
        ]:
            assert col in body, f"Downgrade missing column drop: {col}"
            assert "drop_column" in body

    def test_does_not_drop_table(self, migration_module):
        body = self._downgrade_body_text(migration_module)
        # Down migration must NOT drop the windowed-assessment table itself.
        assert "drop_table" not in body


# ---------------------------------------------------------------------------
# ORM parity — model exposes new fields
# ---------------------------------------------------------------------------


class TestModelParity:
    def test_orm_has_review_columns(self):
        from models import EvidenceWindowAssessment

        column_names = {c.name for c in EvidenceWindowAssessment.__table__.columns}
        for col in [
            "review_status",
            "reviewed_by_user_id",
            "reviewed_at",
            "review_notes",
        ]:
            assert col in column_names, f"ORM missing review column: {col}"

    def test_orm_review_status_is_not_nullable(self):
        from models import EvidenceWindowAssessment

        col = EvidenceWindowAssessment.__table__.columns["review_status"]
        assert col.nullable is False

    def test_orm_reviewed_at_is_nullable(self):
        from models import EvidenceWindowAssessment

        col = EvidenceWindowAssessment.__table__.columns["reviewed_at"]
        assert col.nullable is True

    def test_orm_review_notes_is_nullable(self):
        from models import EvidenceWindowAssessment

        col = EvidenceWindowAssessment.__table__.columns["review_notes"]
        assert col.nullable is True

    def test_orm_reviewed_by_user_id_is_nullable(self):
        from models import EvidenceWindowAssessment

        col = EvidenceWindowAssessment.__table__.columns["reviewed_by_user_id"]
        assert col.nullable is True

    def test_orm_has_reviewed_by_relationship(self):
        from models import EvidenceWindowAssessment

        # Inspect SQLAlchemy mapper to confirm the relationship exists.
        rels = {r.key for r in EvidenceWindowAssessment.__mapper__.relationships}
        assert "reviewed_by" in rels


# ---------------------------------------------------------------------------
# Schema parity — Pydantic response contract preserves backward compat
# ---------------------------------------------------------------------------


class TestSchemaParity:
    def test_response_schema_has_optional_review_fields(self):
        from schemas import EvidenceWindowAssessmentResponse

        fields = EvidenceWindowAssessmentResponse.model_fields
        for name in [
            "review_status",
            "reviewed_by_user_id",
            "reviewed_at",
            "review_notes",
        ]:
            assert name in fields, f"Response schema missing field {name}"
            # Field default must be None (Optional, backward compatible).
            assert fields[name].default is None or fields[name].is_required() is False
