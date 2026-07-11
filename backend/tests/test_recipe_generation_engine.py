"""Tests for the AI recipe generation engine (mock mode)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.system_catalog_validation import validate_recipes_map


@pytest.fixture(autouse=True)
def force_mock_mode(monkeypatch):
    monkeypatch.setenv("SYSTEMS_AI_MOCK", "1")


class TestMockGeneration:
    def test_mock_returns_all_four_levels(self):
        from services.recipe_generation_engine import run_generation
        result = run_generation(
            system_name="Bespoke Tool", vendor="Acme", system_type="custom",
            description="Internal asset tracker",
        )
        assert set(result["recipes"].keys()) == {"L1", "L2", "L3", "L4"}

    def test_mock_output_validates_clean(self):
        from services.recipe_generation_engine import run_generation
        result = run_generation(
            system_name="Bespoke Tool", vendor="Acme", system_type="custom",
            description="",
        )
        assert validate_recipes_map(result["recipes"]) == []

    def test_mock_titles_clearly_marked(self):
        from services.recipe_generation_engine import run_generation
        result = run_generation(
            system_name="Bespoke Tool", vendor="Acme", system_type="custom",
            description="",
        )
        for recipe in result["recipes"].values():
            assert recipe["title"].startswith("[SAMPLE] ")

    def test_mock_mentions_system_name(self):
        from services.recipe_generation_engine import run_generation
        result = run_generation(
            system_name="Bespoke Tool", vendor="Acme", system_type="custom",
            description="",
        )
        assert "Bespoke Tool" in result["recipes"]["L1"]["steps"][0]["action"]


class TestSlugify:
    def test_slugify(self):
        from services.recipe_generation_engine import slugify
        assert slugify("Bespoke Tool v2!") == "bespoke-tool-v2"
        assert slugify("  --Weird--  ") == "weird"
        assert slugify("") == "system"


class TestInvalidOutputHandling:
    def test_invalid_output_raises_after_retry(self, monkeypatch):
        import services.recipe_generation_engine as eng
        monkeypatch.setenv("SYSTEMS_AI_MOCK", "")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
        calls = {"n": 0}

        def bad_call(user_prompt, model, sources):
            calls["n"] += 1
            raise eng.RecipeGenerationError("Model produced invalid recipes twice: junk")

        monkeypatch.setattr(eng, "_call_anthropic_for_recipes", bad_call)
        with pytest.raises(eng.RecipeGenerationError):
            eng.run_generation(system_name="X", vendor="Y", system_type="custom", description="")
        assert calls["n"] == 1
