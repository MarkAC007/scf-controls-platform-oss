"""Tests for system catalog vendor-file validation."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.system_catalog_validation import (
    validate_vendor_file,
    validate_recipe,
    VALID_SYSTEM_TYPES,
    RECIPE_LEVELS,
)


def _recipe(title):
    return {
        "title": title,
        "estimated_time": "10 minutes",
        "frequency": "weekly",
        "steps": [{"step": 1, "action": "Export the log"}],
    }


def _valid_file():
    return {
        "slug": "okta",
        "name": "Okta",
        "vendor": "Okta, Inc.",
        "system_type": "identity_provider",
        "category": "Identity & Access Management",
        "description": "Workforce identity platform.",
        "website": "https://www.okta.com",
        "aliases": ["okta", "okta sso"],
        "logo_hint": "okta",
        "version": "1.0",
        "recipes": {
            "L1": _recipe("Manual export"),
            "L2": _recipe("Scheduled export"),
            "L3": _recipe("API collection"),
            "L4": _recipe("Managed pipeline"),
        },
    }


class TestValidateVendorFile:
    def test_valid_file_passes(self):
        assert validate_vendor_file(_valid_file()) == []

    def test_missing_required_field(self):
        data = _valid_file()
        del data["slug"]
        errors = validate_vendor_file(data)
        assert any("slug" in e for e in errors)

    def test_bad_system_type(self):
        data = _valid_file()
        data["system_type"] = "spaceship"
        assert any("system_type" in e for e in validate_vendor_file(data))

    def test_bad_slug_format(self):
        data = _valid_file()
        data["slug"] = "Okta SSO!"
        assert any("slug" in e for e in validate_vendor_file(data))

    def test_unknown_recipe_level_rejected(self):
        data = _valid_file()
        data["recipes"]["L9"] = data["recipes"]["L1"]
        assert any("L9" in e for e in validate_vendor_file(data))

    def test_empty_recipes_rejected(self):
        data = _valid_file()
        data["recipes"] = {}
        assert any("recipes" in e for e in validate_vendor_file(data))

    def test_incomplete_recipe_ladder_rejected(self):
        # Seed files must ship the full L1-L4 ladder — resolution never
        # falls through to another source mid-ladder
        data = _valid_file()
        del data["recipes"]["L3"]
        assert any("missing maturity levels" in e for e in validate_vendor_file(data))

    def test_non_dict_root_rejected(self):
        assert validate_vendor_file(["not", "a", "dict"]) == ["file root must be a JSON object"]

    def test_new_system_types_accepted(self):
        for st in ("endpoint_management", "vulnerability_management", "email_security",
                   "security_awareness", "password_manager", "communication", "hr_system"):
            assert st in VALID_SYSTEM_TYPES
            data = _valid_file()
            data["system_type"] = st
            assert validate_vendor_file(data) == []


class TestValidateRecipe:
    def test_recipe_missing_title(self):
        errors = validate_recipe({"steps": [{"step": 1, "action": "x"}]}, "L1")
        assert any("title" in e for e in errors)

    def test_recipe_missing_steps(self):
        errors = validate_recipe({"title": "t", "steps": []}, "L1")
        assert any("steps" in e for e in errors)

    def test_step_missing_action(self):
        errors = validate_recipe({"title": "t", "steps": [{"step": 1}]}, "L1")
        assert any("action" in e for e in errors)

    def test_step_optional_fields_type_checked(self):
        errors = validate_recipe(
            {"title": "t", "steps": [{"step": 1, "action": "x", "security_note": 42}]}, "L1"
        )
        assert any("security_note" in e for e in errors)

    def test_levels_constant(self):
        assert RECIPE_LEVELS == ("L1", "L2", "L3", "L4")
