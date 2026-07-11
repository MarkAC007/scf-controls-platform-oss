"""Tests for the system catalog seeder's file-loading layer."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalog_seeder import load_system_catalog_files, SYSTEM_CATALOG_DIR


def test_seed_dir_exists():
    assert SYSTEM_CATALOG_DIR.is_dir()


def test_loads_converted_vendors_without_errors():
    vendors, fallbacks, fallbacks_version, errors = load_system_catalog_files()
    assert errors == []
    slugs = {v["slug"] for v in vendors}
    assert {"okta", "aws", "microsoft-entra-id", "github", "jira",
            "servicenow", "splunk", "crowdstrike"} <= slugs
    assert "cloud_provider" in fallbacks
    assert fallbacks_version  # propagated from _fallbacks.json so edits reseed


def test_invalid_file_reported_not_fatal(tmp_path, monkeypatch):
    import catalog_seeder
    bad_dir = tmp_path / "system_catalog"
    bad_dir.mkdir()
    (bad_dir / "bad.json").write_text("{not json")
    (bad_dir / "worse.json").write_text('{"slug": "worse"}')
    monkeypatch.setattr(catalog_seeder, "SYSTEM_CATALOG_DIR", bad_dir)
    vendors, fallbacks, _version, errors = load_system_catalog_files()
    assert vendors == [] and fallbacks == {}
    assert len(errors) == 2


def test_missing_dir_returns_empty(tmp_path, monkeypatch):
    import catalog_seeder
    monkeypatch.setattr(catalog_seeder, "SYSTEM_CATALOG_DIR", tmp_path / "nope")
    vendors, fallbacks, _version, errors = load_system_catalog_files()
    assert vendors == [] and fallbacks == {} and errors == []
