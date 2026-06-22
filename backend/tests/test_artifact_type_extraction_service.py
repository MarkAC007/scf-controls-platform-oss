"""Unit tests for artifact type extraction service (M1a).

Covers the pure parsing behaviour used by the extraction CLI. DB + LLM paths
are exercised through the CLI against real infrastructure per the plan's
Verification section.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.artifact_type_extraction_service import _parse_artifact_types


class TestParseArtifactTypes:
    def test_valid_array_parses(self):
        raw = (
            '[{"type": "status_snapshot", "weight": "high", "mandatory": true, '
            '"description": "op status"}]'
        )
        parsed = _parse_artifact_types(raw)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "status_snapshot"
        assert parsed[0]["weight"] == "high"
        assert parsed[0]["mandatory"] is True
        assert parsed[0]["description"] == "op status"

    def test_code_fence_stripped(self):
        raw = (
            '```json\n'
            '[{"type": "restore_test", "weight": "medium", '
            '"mandatory": false, "description": ""}]\n'
            '```'
        )
        parsed = _parse_artifact_types(raw)
        assert parsed[0]["type"] == "restore_test"

    def test_invalid_json_returns_empty(self):
        assert _parse_artifact_types("not json") == []

    def test_non_array_returns_empty(self):
        assert _parse_artifact_types('{"type": "status_snapshot"}') == []

    def test_entries_without_type_are_dropped(self):
        raw = '[{"weight": "high", "description": "no type"}, {"type": "ok"}]'
        parsed = _parse_artifact_types(raw)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "ok"

    def test_unknown_weight_coerced_to_medium(self):
        raw = '[{"type": "x", "weight": "banana", "mandatory": false}]'
        parsed = _parse_artifact_types(raw)
        assert parsed[0]["weight"] == "medium"

    def test_mandatory_defaults_to_false(self):
        raw = '[{"type": "x"}]'
        parsed = _parse_artifact_types(raw)
        assert parsed[0]["mandatory"] is False

    def test_whitespace_type_rejected(self):
        raw = '[{"type": "   "}, {"type": "good"}]'
        parsed = _parse_artifact_types(raw)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "good"

    def test_non_dict_entries_dropped(self):
        raw = '["string", {"type": "ok"}, 42, null]'
        parsed = _parse_artifact_types(raw)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "ok"
