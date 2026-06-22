"""Migration sanity test for ``control_assessment_composites`` (M3, #575, PR 1).

This suite is intentionally static — it inspects the migration module's
upgrade/downgrade function bodies to verify they declare the table, the
columns, the unique constraint, the indices, and that downgrade is the
mirror of upgrade. The full-DB upgrade/downgrade round-trip is exercised in
the integration tier.

Approach: parse the migration's AST so we don't need a Postgres instance to
verify migration shape.
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
    MIGRATIONS_DIR / "20260509_120000_add_control_assessment_composites.py"
)


@pytest.fixture(scope="module")
def migration_source() -> str:
    assert MIGRATION_FILE.exists(), f"Migration file missing: {MIGRATION_FILE}"
    return MIGRATION_FILE.read_text()


@pytest.fixture(scope="module")
def migration_module(migration_source: str):
    """Compile + exec the migration in a stub-namespace so we can introspect.

    We can't simply ``import`` the migration because it pulls in alembic.op
    which expects an active migration context. Instead, parse the AST.
    """
    return ast.parse(migration_source)


# ---------------------------------------------------------------------------
# Header / chain
# ---------------------------------------------------------------------------

class TestRevisionMetadata:
    def test_revision_id_is_short_hex_slug(self, migration_source):
        assert "revision: str = 'jj1k2l3m4n5o'" in migration_source

    def test_down_revision_chains_from_window_assessments(self, migration_source):
        assert "down_revision: Union[str, None] = 'ii9j0k1l2m3n'" in migration_source

    def test_filename_matches_pattern(self):
        # YYYYMMDD_HHMMSS_<slug>.py
        assert MIGRATION_FILE.name == "20260509_120000_add_control_assessment_composites.py"


# ---------------------------------------------------------------------------
# Upgrade body — table + indices + constraints
# ---------------------------------------------------------------------------

class TestUpgrade:
    def _upgrade_body_text(self, migration_module) -> str:
        for node in migration_module.body:
            if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
                return ast.unparse(node)
        pytest.fail("upgrade() function not found in migration")

    def test_creates_table(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "create_table" in body
        assert "'control_assessment_composites'" in body

    def test_table_has_required_columns(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        # ISC-1 column list
        for col in [
            "'id'",
            "'organization_id'",
            "'scf_id'",
            "'composite_status'",
            "'composite_score'",
            "'included_window_ids'",
            "'included_evidence_ids'",
            "'mandatory_gaps'",
            "'computation_version'",
            "'computed_at'",
            "'created_at'",
            "'updated_at'",
        ]:
            assert col in body, f"Column {col} missing from upgrade()"

    def test_unique_constraint_on_org_scf(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "uq_control_assessment_composites_org_scf" in body
        assert "UniqueConstraint" in body

    def test_status_dashboard_index_present(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "ix_control_assessment_composites_org_status" in body

    def test_org_id_lookup_index_present(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "ix_control_assessment_composites_org_id" in body

    def test_computed_at_desc_index_present(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "ix_control_assessment_composites_computed_at_desc" in body

    def test_organization_id_has_cascade_delete(self, migration_module):
        body = self._upgrade_body_text(migration_module)
        assert "ondelete='CASCADE'" in body or 'ondelete="CASCADE"' in body

    def test_no_alter_column_or_drops_on_existing_tables(self, migration_module):
        # ISC-A1: M3 must not modify EvidenceWindowAssessment shape
        body = self._upgrade_body_text(migration_module)
        assert "alter_column" not in body
        # The only drops should be in downgrade(); upgrade() must not drop anything.
        assert "drop_table" not in body
        assert "drop_column" not in body


# ---------------------------------------------------------------------------
# Downgrade body — full reversal
# ---------------------------------------------------------------------------

class TestDowngrade:
    def _downgrade_body_text(self, migration_module) -> str:
        for node in migration_module.body:
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade":
                return ast.unparse(node)
        pytest.fail("downgrade() function not found in migration")

    def test_drops_table(self, migration_module):
        body = self._downgrade_body_text(migration_module)
        assert "drop_table" in body
        assert "'control_assessment_composites'" in body

    def test_drops_each_index_added_in_upgrade(self, migration_module):
        body = self._downgrade_body_text(migration_module)
        for ix_name in [
            "ix_control_assessment_composites_org_status",
            "ix_control_assessment_composites_org_id",
            "ix_control_assessment_composites_computed_at_desc",
        ]:
            assert ix_name in body, f"Downgrade missing index drop: {ix_name}"

    def test_does_not_touch_evidence_window_assessments(self, migration_module):
        body = self._downgrade_body_text(migration_module)
        # ISC-A1 — composite migration must leave the M1a table alone.
        assert "evidence_window_assessments" not in body
        assert "scf_catalog_controls" not in body


# ---------------------------------------------------------------------------
# Cross-check against ORM model
# ---------------------------------------------------------------------------

class TestModelParity:
    def test_orm_model_matches_migration_columns(self):
        # Lazy import — pulls in async DB layer.
        from models import ControlAssessmentComposite

        column_names = {c.name for c in ControlAssessmentComposite.__table__.columns}
        expected = {
            "id",
            "organization_id",
            "scf_id",
            "composite_status",
            "composite_score",
            "included_window_ids",
            "included_evidence_ids",
            "mandatory_gaps",
            "computation_version",
            "computed_at",
            "created_at",
            "updated_at",
        }
        assert column_names == expected

    def test_orm_unique_constraint_present(self):
        from models import ControlAssessmentComposite

        constraint_names = {
            c.name for c in ControlAssessmentComposite.__table__.constraints
        }
        assert "uq_control_assessment_composites_org_scf" in constraint_names

    def test_orm_status_index_present(self):
        from models import ControlAssessmentComposite

        index_names = {ix.name for ix in ControlAssessmentComposite.__table__.indexes}
        assert "ix_control_assessment_composites_org_status" in index_names
