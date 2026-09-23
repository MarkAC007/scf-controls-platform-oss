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


# ----------------------------------------------- framework registry seeding --
class _FakeCountResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value


class _FakeSession:
    """Counts one query and collects added rows; enough for the seeder's shape."""

    def __init__(self, existing: int = 0):
        self._existing = existing
        self.added = []
        self.commits = 0

    async def execute(self, statement):
        return _FakeCountResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


def _seed_registry(session):
    import asyncio

    from catalog_seeder import seed_framework_registry_if_empty

    return asyncio.run(seed_framework_registry_if_empty(session))


def test_seed_framework_registry_reads_registry_json_then_frameworks_json(
    tmp_path, monkeypatch
):
    """framework_registry.json preferred; frameworks.json is the pre-2026.1 fallback."""
    import catalog_seeder

    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)
    monkeypatch.setattr(catalog_seeder, "_resolve_catalog_version", lambda: "2026.2")

    # Neither file present: an error, not a silent empty row.
    session = _FakeSession()
    assert _seed_registry(session)["status"] == "error"
    assert session.added == []

    # frameworks.json alone: names, no focal-document identifiers.
    (tmp_path / "frameworks.json").write_text('{"nist_800_53_r5": "NIST 800-53 rev5"}')
    session = _FakeSession()
    result = _seed_registry(session)
    assert result == {"status": "seeded", "count": 1, "with_focal_document_id": 0}
    row = session.added[0]
    assert row.catalog_version == "2026.2"
    assert row.source == "seed"
    assert row.registry == {
        "nist_800_53_r5": {
            "name": "NIST 800-53 rev5",
            "focal_document_id": None,
            "geography": None,
        }
    }

    # framework_registry.json wins over it and carries the identifiers.
    (tmp_path / "framework_registry.json").write_text(
        '{"nist_800_53_r5": {"name": "NIST 800-53 rev5", '
        '"focal_document_id": "usa-federal-nist-800-53-r5", "geography": "USA"}}'
    )
    session = _FakeSession()
    result = _seed_registry(session)
    assert result == {"status": "seeded", "count": 1, "with_focal_document_id": 1}
    assert session.added[0].registry["nist_800_53_r5"]["focal_document_id"] == (
        "usa-federal-nist-800-53-r5"
    )
    assert session.commits == 1


def test_seed_framework_registry_skips_when_version_already_has_a_row(
    tmp_path, monkeypatch
):
    import catalog_seeder

    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)
    (tmp_path / "framework_registry.json").write_text('{"fw": {"name": "FW"}}')
    session = _FakeSession(existing=1)
    assert _seed_registry(session) == {"status": "skipped", "existing": 1}
    assert session.added == [] and session.commits == 0
