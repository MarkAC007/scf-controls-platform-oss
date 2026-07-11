"""Tests for /system-catalog API schemas and endpoint contracts."""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_system_types_extended():
    from schemas import SYSTEM_TYPES
    types = SYSTEM_TYPES.split("|")
    for t in ("endpoint_management", "vulnerability_management", "email_security",
              "security_awareness", "password_manager", "communication", "hr_system"):
        assert t in types
    # original types retained
    for t in ("cloud_provider", "identity_provider", "custom"):
        assert t in types


def test_list_endpoint_excludes_fallbacks_by_default():
    from api.system_catalog import list_templates
    param = inspect.signature(list_templates).parameters["include_fallbacks"]
    assert param.default.default is False


def test_system_create_accepts_template_id():
    from schemas import SystemCreate
    s = SystemCreate(name="GitHub", system_type="code_repository", catalog_template_id=3)
    assert s.catalog_template_id == 3


def test_system_create_accepts_new_types():
    from schemas import SystemCreate
    s = SystemCreate(name="Intune", system_type="endpoint_management")
    assert s.system_type == "endpoint_management"


def test_collection_recipe_schema_has_source():
    from schemas import CollectionRecipeSchema
    assert CollectionRecipeSchema(title="t", steps=[]).source == "curated"


def test_collection_guidance_schema_has_matched_via():
    from schemas import CollectionGuidanceSchema
    fields = CollectionGuidanceSchema.model_fields
    assert "matched_via" in fields


def test_system_recipes_response_shape():
    from schemas import SystemRecipesResponse
    fields = SystemRecipesResponse.model_fields
    assert {"system_id", "matched_via", "template", "recipes"} <= set(fields)


def test_template_summary_from_attributes():
    from schemas import SystemCatalogTemplateSummary
    assert SystemCatalogTemplateSummary.model_config.get("from_attributes") is True


def test_system_tracked_fields_include_template_id():
    from services.audit_service import SYSTEM_TRACKED_FIELDS
    assert "catalog_template_id" in SYSTEM_TRACKED_FIELDS
