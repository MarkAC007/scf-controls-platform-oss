"""Tests for DB-backed recipe resolution helpers in the capabilities API."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.capabilities import clamp_recipe_level, matched_via_to_confidence


class TestClampRecipeLevel:
    def test_l0_clamps_to_l1(self):
        assert clamp_recipe_level("L0") == "L1"

    def test_l5_clamps_to_l4(self):
        assert clamp_recipe_level("L5") == "L4"

    def test_valid_levels_pass_through(self):
        for level in ("L1", "L2", "L3", "L4"):
            assert clamp_recipe_level(level) == level

    def test_garbage_defaults_to_l1(self):
        assert clamp_recipe_level("nonsense") == "L1"
        assert clamp_recipe_level(None) == "L1"


class TestMatchedViaToConfidence:
    def test_template_is_system_specific(self):
        assert matched_via_to_confidence("template") == "system_specific"

    def test_alias_is_vendor_generic(self):
        # A heuristic alias match must not render the same high-confidence
        # badge as an explicit template link
        assert matched_via_to_confidence("alias") == "vendor_generic"

    def test_fallback_is_type_generic(self):
        assert matched_via_to_confidence("fallback") == "type_generic"

    def test_none_is_type_generic(self):
        assert matched_via_to_confidence("none") == "type_generic"


def test_per_system_recipes_endpoint_exists():
    from api.capabilities import get_system_recipes  # noqa: F401


def test_legacy_json_resolution_removed():
    import api.capabilities as caps
    assert not hasattr(caps, "_resolve_recipe")
    assert not hasattr(caps, "_load_recipes")
