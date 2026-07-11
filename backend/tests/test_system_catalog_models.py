"""Structural tests for system catalog models."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog_models import SystemCatalogTemplate, SystemCatalogRecipe
from models import System


def test_template_table_and_columns():
    cols = {c.name for c in SystemCatalogTemplate.__table__.columns}
    assert SystemCatalogTemplate.__tablename__ == "system_catalog_templates"
    assert {"id", "slug", "name", "vendor", "system_type", "aliases",
            "is_fallback", "organization_id", "version"} <= cols


def test_template_slug_unique():
    slug = SystemCatalogTemplate.__table__.columns["slug"]
    assert slug.unique


def test_recipe_table_unique_per_level():
    assert SystemCatalogRecipe.__tablename__ == "system_catalog_recipes"
    uniques = [c for c in SystemCatalogRecipe.__table__.constraints
               if c.__class__.__name__ == "UniqueConstraint"]
    assert any({"template_id", "maturity_level"} == {col.name for col in u.columns}
               for u in uniques)


def test_system_has_catalog_template_fk():
    col = System.__table__.columns.get("catalog_template_id")
    assert col is not None
    fks = list(col.foreign_keys)
    assert fks and fks[0].target_fullname == "system_catalog_templates.id"
    assert fks[0].ondelete == "SET NULL"
