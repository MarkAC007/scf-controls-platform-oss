"""Unit tests for the collector registry (M2, #572, PR 1).

Exercises the resolution chain (payload → registry → empty) and the feature
flag gate. Heuristic fallback is NOT tested here — it is exercised by the
window_assessment_service tests.
"""
import os
import sys
import textwrap

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from collectors import registry as registry_module
from collectors.registry import resolve_artifact_types


@pytest.fixture(autouse=True)
def _reset_registry_cache_and_env(monkeypatch, tmp_path):
    registry_module.reset_cache()
    monkeypatch.delenv("ENABLE_COLLECTOR_REGISTRY", raising=False)
    yield
    registry_module.reset_cache()


def _install_yaml(tmp_path, monkeypatch, body: str) -> None:
    registry_path = tmp_path / "registry.yml"
    registry_path.write_text(textwrap.dedent(body))
    monkeypatch.setattr(registry_module, "_REGISTRY_PATH", registry_path)
    registry_module.reset_cache()


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("ENABLE_COLLECTOR_REGISTRY", "true")


# ---------------------------------------------------------------------------
# Payload wins
# ---------------------------------------------------------------------------

class TestDeclaredPayloadWins:
    def test_declared_wins_over_registry_when_flag_on(self, tmp_path, monkeypatch, flag_on):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: AzureBackup
                artifact_types:
                  - type: backup_status_snapshot
        """)
        types, via = resolve_artifact_types(None, "AzureBackup", declared=["backup_policy"])
        assert types == ["backup_policy"]
        assert via == "payload"

    def test_declared_honoured_even_when_flag_off(self):
        # payload declarations are authoritative regardless of the registry flag
        types, via = resolve_artifact_types("any", "any", declared=["policy_document"])
        assert types == ["policy_document"]
        assert via == "payload"


# ---------------------------------------------------------------------------
# Registry lookup
# ---------------------------------------------------------------------------

class TestRegistryLookup:
    def test_flag_off_skips_registry(self, tmp_path, monkeypatch):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: AzureBackup
                artifact_types:
                  - type: backup_status_snapshot
        """)
        types, via = resolve_artifact_types(None, "AzureBackup")
        assert types == []
        assert via == "empty"

    def test_flag_on_resolves_single_type(self, tmp_path, monkeypatch, flag_on):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: AzureBackup
                artifact_types:
                  - type: backup_status_snapshot
        """)
        types, via = resolve_artifact_types(None, "AzureBackup")
        assert types == ["backup_status_snapshot"]
        assert via == "registry"

    def test_flag_on_resolves_multi_type(self, tmp_path, monkeypatch, flag_on):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: EntraID
                artifact_types:
                  - type: identity_inventory
                  - type: mfa_enrollment_snapshot
        """)
        types, via = resolve_artifact_types(None, "EntraID")
        assert sorted(types) == ["identity_inventory", "mfa_enrollment_snapshot"]
        assert via == "registry"

    def test_unknown_source_returns_empty(self, tmp_path, monkeypatch, flag_on):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: EntraID
                artifact_types:
                  - type: identity_inventory
        """)
        types, via = resolve_artifact_types(None, "SomeBrandNewCollector")
        assert types == []
        assert via == "empty"

    def test_collector_id_source_tuple_overrides_source_only(self, tmp_path, monkeypatch, flag_on):
        # Two rows share the same source_label; collector_id disambiguates
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: Shared
                collector_id: collector_a
                artifact_types:
                  - type: type_from_a
              - source_label: Shared
                collector_id: collector_b
                artifact_types:
                  - type: type_from_b
        """)
        # The first one wins the source-only lookup because it's inserted first;
        # a precise (collector_id, source_label) lookup should pick collector_b's row.
        types, via = resolve_artifact_types("collector_b", "Shared")
        assert types == ["type_from_b"]
        assert via == "registry"


# ---------------------------------------------------------------------------
# Registry-version safety
# ---------------------------------------------------------------------------

class TestVersionGuard:
    def test_unknown_version_returns_empty_with_warning(self, tmp_path, monkeypatch, flag_on, caplog):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 99
            sources:
              - source_label: AzureBackup
                artifact_types:
                  - type: backup_status_snapshot
        """)
        with caplog.at_level("WARNING"):
            types, via = resolve_artifact_types(None, "AzureBackup")
        assert types == []
        assert via == "empty"
        assert any("not supported" in rec.message for rec in caplog.records)

    def test_missing_file_logs_warning_and_returns_empty(self, tmp_path, monkeypatch, flag_on, caplog):
        missing = tmp_path / "nope.yml"
        monkeypatch.setattr(registry_module, "_REGISTRY_PATH", missing)
        registry_module.reset_cache()
        with caplog.at_level("WARNING"):
            types, via = resolve_artifact_types(None, "AzureBackup")
        assert types == []
        assert via == "empty"
        assert any("not found" in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TestCaching:
    def test_registry_is_cached(self, tmp_path, monkeypatch, flag_on):
        _install_yaml(tmp_path, monkeypatch, """
            registry_version: 1
            sources:
              - source_label: AzureBackup
                artifact_types:
                  - type: backup_status_snapshot
        """)
        resolve_artifact_types(None, "AzureBackup")
        # Overwrite disk file and confirm cache serves the original result
        (tmp_path / "registry.yml").write_text("registry_version: 1\nsources: []\n")
        types, via = resolve_artifact_types(None, "AzureBackup")
        assert types == ["backup_status_snapshot"]
        assert via == "registry"


# ---------------------------------------------------------------------------
# Production registry vocabulary alignment (M2 PR 2, #572)
# ---------------------------------------------------------------------------

class TestProductionRegistryVocabularyAlignment:
    """Regression guard against vocabulary drift between
    `backend/collectors/registry.yml` and the per-control
    `required_artifact_types` populated by `extract_artifact_types.py`.

    The two vocabularies are authored independently — registry.yml is hand-written
    and the per-control vocabulary is LLM-generated with no shared constraint.
    If a registry row resolves to a type that does not appear in any expected
    vocabulary, flipping `ENABLE_COLLECTOR_REGISTRY=true` silently zeros the
    coverage signal for that source (the M1a heuristic accidentally bridged the
    two via substring-token matching; the registry path does not).

    Lock alignment for every source whose mapped controls already have
    extracted vocabulary. Add new entries here when extract_artifact_types
    runs against new domains.
    """

    # E-BCM-11 / E-BCM-12 / E-BCM-15 are the evidences fed by the AzureBackup
    # collector; all map to BCD-11 / BCD-11.x / BCD-12 controls. The vocabulary
    # below was extracted by extract_artifact_types.py and verified live against
    # CG prod windowed assessment c7bab0a8 on 2026-05-07.
    #
    # The list order matches `_build_expected_artifact_types`'s sort key
    # (mandatory desc → weight desc → alpha) — required because the M1a
    # heuristic returns the FIRST iterated type whose any-token substring-
    # matches the source label, so test outcomes depend on input order.
    BCD_BACKUP_EXPECTED_TYPES = [
        {"type": "backup_execution_logs", "weight": "high", "mandatory": True},
        {"type": "backup_location_mapping", "weight": "high", "mandatory": True},
        {"type": "backup_policy", "weight": "high", "mandatory": True},
        {"type": "backup_schedule_configuration", "weight": "high", "mandatory": True},
        {"type": "backup_storage_policy", "weight": "high", "mandatory": True},
        {"type": "critical_systems_inventory", "weight": "high", "mandatory": True},
        {"type": "integrity_verification_reports", "weight": "high", "mandatory": True},
        {"type": "storage_facility_certification", "weight": "high", "mandatory": True},
        {"type": "backup_inventory_listing", "weight": "medium", "mandatory": False},
        {"type": "storage_compliance_audit", "weight": "medium", "mandatory": False},
    ]
    BCD_BACKUP_EXPECTED_VOCAB = frozenset(e["type"] for e in BCD_BACKUP_EXPECTED_TYPES)

    def test_azure_backup_resolves_into_bcd_expected_vocabulary(self, flag_on):
        """AzureBackup must resolve to types in BCD-11 expected_artifact_types.

        Reads the real `backend/collectors/registry.yml` (no synthetic fixture).
        """
        types, via = resolve_artifact_types("azure_backup", "AzureBackup")
        assert via == "registry"
        assert types, "AzureBackup must resolve to a non-empty types list"
        unaligned = set(types) - self.BCD_BACKUP_EXPECTED_VOCAB
        assert not unaligned, (
            f"Registry types {sorted(unaligned)} are not in BCD expected vocabulary "
            f"{sorted(self.BCD_BACKUP_EXPECTED_VOCAB)}. Flipping the flag would "
            f"zero coverage signal for AzureBackup files."
        )

    def test_azure_backup_flag_on_supersets_heuristic_present_set(self, monkeypatch):
        """Flag-on coverage non-regression vs flag-off heuristic for AzureBackup.

        Originally asserted byte-identical equality between flag-on and flag-off
        present-sets — the safety contract that gated the original flag flip.
        Post-flip, M2 (#572) explicitly extends the registry beyond heuristic
        coverage so collectors can declare multiple artifact types per source.
        The invariant is now: flag-on present-set must be a SUPERSET of the
        flag-off heuristic present-set. Registry can credit MORE types than
        heuristic, never fewer.

        Skips when the backend's heavy deps (asyncpg, etc.) are not installed
        locally — CI installs them and runs the full check there.
        """
        # window_assessment_service pulls in sqlalchemy + asyncpg via models;
        # in a thin local env those imports fail. importorskip turns that into
        # a graceful skip rather than a collection error.
        wa = pytest.importorskip(
            "services.window_assessment_service",
            reason="backend deps (asyncpg, sqlalchemy models) not installed locally",
        )

        from datetime import datetime
        from uuid import uuid4
        from collectors import registry as registry_module

        _FileInWindow = wa._FileInWindow
        _compute_coverage = wa._compute_coverage

        def _file():
            return _FileInWindow(
                id=uuid4(),
                filename="webhook_AzureBackup_x.json",
                s3_key="s3://bucket/AzureBackup_x.json",
                content_type="application/json",
                uploaded_at=datetime(2026, 5, 7, 12, 0, 0),
                source_label="AzureBackup",
                extracted_text="{}",
                sha256_hash="deadbeef",
            )

        expected = self.BCD_BACKUP_EXPECTED_TYPES

        # Flag-off (heuristic path)
        monkeypatch.delenv("ENABLE_COLLECTOR_REGISTRY", raising=False)
        registry_module.reset_cache()
        _, cov_off = _compute_coverage([_file()], expected)
        off_present = {k for k, v in cov_off.items() if v["present"]}

        # Flag-on (registry path)
        monkeypatch.setenv("ENABLE_COLLECTOR_REGISTRY", "true")
        registry_module.reset_cache()
        _, cov_on = _compute_coverage([_file()], expected)
        on_present = {k for k, v in cov_on.items() if v["present"]}

        assert on_present, "Both paths produced empty coverage — no signal to compare"
        missing = off_present - on_present
        assert not missing, (
            f"Flag-on present-set {sorted(on_present)} is missing types "
            f"{sorted(missing)} that the flag-off heuristic credits — "
            f"registry path regressed coverage vs heuristic."
        )


# ---------------------------------------------------------------------------
# Source-label / emit-string alignment (M2 PR 4, #572)
# ---------------------------------------------------------------------------

class TestProductionRegistryShapeInvariants:
    """Schema-level invariants on the real `backend/collectors/registry.yml`.

    Independent of any fixture — reads disk and walks the parsed registry.
    Catches structural regressions: empty labels, missing collector_id, no
    artifact_types list, etc.
    """

    def _load_real_registry(self):
        registry_module.reset_cache()
        return registry_module._load_registry()  # noqa: SLF001 — internal but stable

    def test_every_row_has_source_label_collector_id_and_atypes(self):
        # The internal cache is the post-validation view, but the raw rows are
        # in the YAML — we walk the YAML directly so we also catch rows that
        # were filtered out for being malformed (which would otherwise silently
        # disappear from the cache).
        import yaml as _yaml
        with open(registry_module._REGISTRY_PATH, "r", encoding="utf-8") as f:
            raw = _yaml.safe_load(f)
        rows = raw.get("sources") or []
        assert rows, "registry.yml has no `sources` rows"
        for i, row in enumerate(rows):
            assert isinstance(row, dict), f"row {i} is not a mapping"
            assert row.get("source_label"), f"row {i} missing source_label"
            assert row.get("collector_id"), f"row {i} missing collector_id"
            assert row.get("artifact_types"), f"row {i} missing artifact_types"
            atypes = row["artifact_types"]
            assert isinstance(atypes, list) and atypes, (
                f"row {i} ({row['source_label']}) artifact_types must be a non-empty list"
            )
            for j, entry in enumerate(atypes):
                assert isinstance(entry, dict), (
                    f"row {i} ({row['source_label']}) artifact_types[{j}] is not a mapping"
                )
                assert entry.get("type"), (
                    f"row {i} ({row['source_label']}) artifact_types[{j}].type is empty"
                )

    def test_resolver_returns_non_empty_for_every_documented_tuple(self, flag_on):
        """For every (collector_id, source_label) declared in the registry,
        the resolver must return a non-empty types list via `registry`.

        This is the primary regression test against #572 PR 4 — the prod
        trace 2026-05-09T14:45:35Z showed `resolved_via=heuristic types=[]`
        because the (collector_id=None, source_label='Cloudflare-Security')
        lookup missed the row labelled `Cloudflare`. After the rename,
        every row in the registry must round-trip to a non-empty resolution.
        """
        reg = self._load_real_registry()
        # The cache exposes by_collector_source as the canonical (collector_id, source_label)
        # → atypes index. If a row was malformed and filtered out, it won't appear here,
        # which would itself be caught by the shape-invariants test above.
        for (collector_id, source_label), expected_types in reg["by_collector_source"].items():
            resolved, via = resolve_artifact_types(collector_id, source_label)
            assert via == "registry", (
                f"({collector_id}, {source_label}) resolved via {via!r}, expected 'registry'"
            )
            assert resolved == list(expected_types), (
                f"({collector_id}, {source_label}) resolved to {resolved}, "
                f"expected {expected_types}"
            )


class TestProductionRegistryEmitStringAlignment:
    """Lock the registry's `source_label` values against what collectors
    actually emit at the wire — `sign_and_post(..., source="X", ...)`.

    Drift here = the bug fixed in #572 PR 4: the resolver misses, falls
    through to the heuristic, and silently regresses coverage. The mapping
    below is empirical — every entry has a citation to either a source-code
    line in the `scf-evidence-collectors` repo or a prod evidence file
    whose filename pattern (`webhook_{source}_{uuid}.json`, set at
    `backend/api/evidence_inbox.py:466`) reveals the emitted source string.

    When a new collector ships, add a row here with its citation BEFORE
    adding the registry.yml row — the alignment is the contract.
    """

    # Each entry: source_label → (collector_id, citation)
    # Citations:
    #   "code:<path>:<line>" — line in scf-evidence-collectors emitting source=<label>
    #   "prod:E-XXX-NN/<file_id>" — prod evidence file with filename
    #                                webhook_<label>_<uuid>.json (15-min URL,
    #                                file_id is permanent)
    EMIT_STRING_REGISTRY = {
        "EntraID": (
            "entra_id",
            "code:azure-functions/collectors/entra_id.py:566; "
            "prod:E-IAM-01/da1c68e5-36a0-4660-ab45-2a05eafac79f",
        ),
        "AzureBackup": (
            "azure_backup",
            "code:azure-functions/collectors/azure_backup.py:449",
        ),
        "DefenderForCloud": (
            "defender",
            "code:azure-functions/collectors/defender.py:190",
        ),
        "GitHub-Extended": (
            "github_extended",
            "code:azure-functions/collectors/github_extended.py:552; "
            "prod:E-IAM-04/91d190b6-2a33-4317-b51d-52a07ee7dfc4; "
            "prod:E-CFG-01/c3b49f4a-f5d8-4795-941a-c937c9acac94",
        ),
        "Database-Baseline": (
            "database_baseline",
            "code:azure-functions/collectors/database_baseline.py:328",
        ),
        "Cloudflare-Security": (
            "cloudflare_security",
            "code:local-collectors/cloudflare_security.py:33; "
            "prod:E-NET-01/0563e700-13d6-4674-9006-993e9c4e8743",
        ),
        "macOS-Endpoint": (
            "macos_endpoint",
            "code:local-collectors/macos_endpoint.py:32; "
            "prod:E-AST-04/2af4f326-d021-4d86-8ec1-a81e9d0f97a8",
        ),
        "macOS-Extended": (
            "macos_extended",
            "code:local-collectors/macos_extended.py:33; "
            "prod:E-END-01/395f4a3f-b26e-40c7-981c-293e878dab42",
        ),
        "PAI-Local": (
            "pai_collector",
            "code:local-collectors/pai_collector.py:32",
        ),
        # SCFPlatform note: TWO collectors share collector_id=scf_platform
        # in the sibling repo:
        #   - azure-functions/collectors/scf_platform.py:174 emits "SCFPlatform"
        #   - local-collectors/scf_platform.py:39 emits "SCF-Platform-Self"
        # The registry row matches the Azure Functions emit. The local
        # collector emits a different label that does NOT round-trip through
        # this registry — track via #572 follow-up: split into two rows or
        # rename the local collector's SOURCE constant.
        "SCFPlatform": (
            "scf_platform",
            "code:azure-functions/collectors/scf_platform.py:174 "
            "(local-collectors/scf_platform.py:39 emits a different label "
            "'SCF-Platform-Self' — see #572 follow-up)",
        ),
        "SCF-Platform-Extended": (
            "scf_platform_extended",
            "code:local-collectors/scf_platform_extended.py:31",
        ),
        "Training-Log": (
            "training_log",
            "code:local-collectors/training_log.py:44",
        ),
        "Doc-Gen-Pipeline": (
            "doc_gen_pipeline",
            "code:local-collectors/doc_gen_pipeline.py:38; "
            "prod:E-AAT-01/2192e5e8-84be-4c01-be19-5dcfb8e9d163",
        ),
    }

    def test_every_registry_row_appears_in_emit_string_registry(self):
        """Every row in registry.yml must have a documented emit-string
        citation. This is the gate that prevents the M2 drift bug from
        silently re-occurring."""
        registry_module.reset_cache()
        reg = registry_module._load_registry()  # noqa: SLF001
        registry_labels = set(reg["by_source"].keys())
        documented_labels = set(self.EMIT_STRING_REGISTRY.keys())
        undocumented = registry_labels - documented_labels
        assert not undocumented, (
            f"registry.yml has source_labels with no emit-string citation: "
            f"{sorted(undocumented)}. Add them to EMIT_STRING_REGISTRY with a "
            f"code:<path>:<line> or prod:<evidence_id>/<file_id> citation."
        )

    def test_every_documented_label_resolves_via_registry(self, flag_on):
        """Every documented (collector_id, source_label) tuple must round-trip
        through the resolver and return a non-empty types list via 'registry'.

        Reads the real registry.yml — no fixture override — so this is the
        end-to-end assertion against the bug from prod trace
        2026-05-09T14:45:35Z."""
        for source_label, (collector_id, citation) in self.EMIT_STRING_REGISTRY.items():
            resolved, via = resolve_artifact_types(collector_id, source_label)
            assert via == "registry", (
                f"{source_label} (collector_id={collector_id}, citation={citation}) "
                f"resolved_via={via!r}, expected 'registry'"
            )
            assert resolved, (
                f"{source_label} (collector_id={collector_id}) returned empty types "
                f"— would fall through to heuristic. citation: {citation}"
            )
