"""Unit tests for webhook intake declaration-origin computation (M2 PR 1.1, #572 §3).

Exercises the pure helper that classifies where `artifact_type` / `collector_id`
came from (body JSON vs X-SCF-* headers). Feeds the `webhook.intake` cutover
signal metric — see design spec on issue #572 §3.
"""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.evidence_inbox import _compute_declaration_origins


class TestComputeDeclarationOrigins:
    def test_body_only_artifact_type(self):
        payload = {"artifact_type": "azure_backup_operation"}
        art, col = _compute_declaration_origins(payload, None, None)
        assert art == "body"
        assert col == "none"

    def test_header_only_artifact_type(self):
        art, col = _compute_declaration_origins(None, "azure_backup_operation", None)
        assert art == "header"
        assert col == "none"

    def test_both_artifact_type_body_and_header(self):
        payload = {"artifact_type": "azure_backup_operation"}
        art, col = _compute_declaration_origins(payload, "different_type", None)
        assert art == "both"

    def test_neither_artifact_type(self):
        art, col = _compute_declaration_origins({}, None, None)
        assert art == "none"
        assert col == "none"

    def test_body_only_collector_id(self):
        payload = {"collector_id": "azure_backup"}
        _, col = _compute_declaration_origins(payload, None, None)
        assert col == "body"

    def test_header_only_collector_id(self):
        _, col = _compute_declaration_origins(None, None, "azure_backup")
        assert col == "header"

    def test_both_collector_id_body_and_header(self):
        payload = {"collector_id": "azure_backup"}
        _, col = _compute_declaration_origins(payload, None, "other")
        assert col == "both"

    def test_empty_string_header_treated_as_none(self):
        # Empty string header must NOT count as present — evaluates falsy.
        art, col = _compute_declaration_origins({}, "", "")
        assert art == "none"
        assert col == "none"

    def test_payload_none_with_no_headers_returns_none_pair(self):
        art, col = _compute_declaration_origins(None, None, None)
        assert art == "none"
        assert col == "none"

    def test_non_dict_payload_ignored(self):
        # Multipart / non-JSON deliveries pass payload_json as None.
        art, col = _compute_declaration_origins(None, "some_type", "some_collector")
        assert art == "header"
        assert col == "header"


class TestIntakeLogPII:
    """PII safety: log line must NOT contain declaration values.

    The webhook.intake log is intended as a cutover metric — it should only
    emit origin enums and evidence_id, never the raw artifact_type or
    collector_id values (which could carry PII/secrets in poorly-built
    collectors).
    """

    def test_helper_does_not_return_raw_values(self):
        # The helper returns enums only — callers cannot accidentally log values
        # by using the helper's return tuple.
        payload = {"artifact_type": "SECRET_AZURE_BACKUP", "collector_id": "COLLECTOR_SECRET"}
        art, col = _compute_declaration_origins(payload, "HEADER_SECRET", "HEADER_COLLECTOR_SECRET")
        assert art in {"body", "header", "both", "none"}
        assert col in {"body", "header", "both", "none"}
        # No leaked value in the enums.
        assert "SECRET" not in art
        assert "SECRET" not in col
