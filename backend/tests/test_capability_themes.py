"""
Tests for the Capability Themes API.

See issue #548 — the theme detail view was returning out-of-scope controls
rendered as `Unset`. The endpoint default was changed from `None` (no filter)
to `"in_scope"` so consumers omitting the parameter get only the scoped subset.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.capability_themes import list_capability_theme_controls


class TestCapabilityThemeControlsDefaults:
    """Regression tests for the scope_status default (issue #548)."""

    def test_scope_status_default_is_in_scope(self):
        sig = inspect.signature(list_capability_theme_controls)
        param = sig.parameters["scope_status"]
        assert param.default.default == "in_scope", (
            "list_capability_theme_controls must default scope_status to "
            "'in_scope' so theme detail views only show scoped controls "
            "(issue #548)."
        )
