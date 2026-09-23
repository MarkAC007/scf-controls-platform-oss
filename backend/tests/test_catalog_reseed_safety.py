"""Tests for the destructive-reseed guard and the import staging directory.

Two defects are covered here, both in the path that a self-hosted operator
takes when they upload their licensed SCF workbook through the UI:

1. ``catalog_seeder.reseed_catalog(force=True)`` deleted seven catalogue tables
   and COMMITTED before asking whether the JSON it was about to reseed from
   existed. Every seeder returns ``{"status": "error"}`` on a missing file
   rather than raising, and ``main.py`` only logs that, so a single absent
   input meant a table was destroyed and never restored. The guard must refuse
   BEFORE the delete, leaving row counts untouched.

2. ``tasks_catalog.import_catalog`` extracted the workbook straight into
   ``DATA_DIR``, which resolves to ``/app/data/json`` whenever that directory
   exists — and the backend image creates it. On a deployment where that path
   is not a writable mount the import failed after the upload had already
   succeeded. The extraction now lands in a temp staging directory and the
   seeders are pointed at it; publishing back to ``DATA_DIR`` is best-effort,
   so Docker Compose (a writable bind mount) is unchanged and a read-only
   filesystem still completes.

No live database: the session is faked at the ``AsyncSessionLocal`` boundary,
which is this repo's unit-test pattern for the catalogue modules.
"""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import catalog_seeder  # noqa: E402
from catalog_models import (  # noqa: E402
    CapabilityTheme,
    CapabilityThemeMapping,
    CatalogFrameworkRegistry,
    SCFCatalogAssessmentObjective,
    SCFCatalogControl,
    SCFCatalogDomain,
    SCFCatalogEvidence,
)

# Every table reseed_catalog clears, by its real SQL name, so a rename cannot
# make the "nothing was deleted" assertion silently vacuous.
CLEARED_TABLES = {
    model.__table__.name
    for model in (
        CatalogFrameworkRegistry,
        CapabilityThemeMapping,
        CapabilityTheme,
        SCFCatalogAssessmentObjective,
        SCFCatalogEvidence,
        SCFCatalogControl,
        SCFCatalogDomain,
    )
}

# What a complete DATA_DIR looks like to the seeders: the six inputs plus the
# metadata file the version resolver reads.
COMPLETE_INPUTS = {
    "control_guidance.json": {"controls": []},
    "domains.json": [],
    "erl.json": {},
    "assessment_objectives.json": {"objectives": []},
    "capability_themes.json": {"themes": [], "nist_family_mappings": {}},
    "framework_registry.json": {},
    "catalog_meta.json": {"catalog_version": "2026.2"},
}


def _write_inputs(directory: Path, omit: tuple = ()) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for name, payload in COMPLETE_INPUTS.items():
        if name in omit:
            continue
        (directory / name).write_text(json.dumps(payload))
    return directory


# ---------------------------------------------------------------------------
# Fake database
# ---------------------------------------------------------------------------


class _FakeCatalogDB:
    """Row counts per catalogue table; a DELETE against one zeroes it.

    Counting rows rather than recording statements is deliberate. The
    regression is not "a DELETE was issued", it is "rows are gone" — an
    assertion on statements would still pass if the guard ran after the delete.
    """

    def __init__(self, rows: int = 10):
        self.rows = {name: rows for name in CLEARED_TABLES}
        self.statements: list = []
        self.commits = 0

    def apply(self, sql: str) -> None:
        self.statements.append(sql)
        if not sql.upper().startswith("DELETE FROM"):
            return
        for name in CLEARED_TABLES:
            # Match the table token exactly; a prefix match would let
            # scf_catalog_control* collide.
            if sql.split()[2].strip('"') == name:
                self.rows[name] = 0


class _FakeSession:
    def __init__(self, db: _FakeCatalogDB):
        self._db = db

    async def execute(self, statement):
        self._db.apply(str(statement))
        return None

    async def commit(self):
        self._db.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def fake_db(monkeypatch) -> _FakeCatalogDB:
    db = _FakeCatalogDB()
    monkeypatch.setattr(catalog_seeder, "AsyncSessionLocal", lambda: _FakeSession(db))
    return db


@pytest.fixture
def seeded_calls(monkeypatch) -> list:
    """Replace seed_catalog_if_empty so reseed's own behaviour is isolated."""
    calls: list = []

    async def _fake_seed():
        calls.append(Path(catalog_seeder.DATA_DIR))
        return {"controls": {"status": "seeded", "count": 3}}

    monkeypatch.setattr(catalog_seeder, "seed_catalog_if_empty", _fake_seed)
    return calls


# ---------------------------------------------------------------------------
# FIX 1 — the destructive reseed refuses before it deletes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "missing",
    [
        "control_guidance.json",
        "domains.json",
        "erl.json",
        "assessment_objectives.json",
        "capability_themes.json",
    ],
)
@pytest.mark.asyncio
async def test_missing_input_refuses_and_destroys_nothing(
    tmp_path, monkeypatch, fake_db, seeded_calls, missing
):
    _write_inputs(tmp_path, omit=(missing,))
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    result = await catalog_seeder.reseed_catalog(force=True)

    assert result["status"] == "error"
    assert missing in result["message"]
    # The regression that matters: every table still holds its rows.
    assert all(count == 10 for count in fake_db.rows.values()), fake_db.rows
    assert fake_db.commits == 0
    assert seeded_calls == []


@pytest.mark.asyncio
async def test_framework_registry_satisfied_by_either_file(
    tmp_path, monkeypatch, fake_db, seeded_calls
):
    """frameworks.json is the pre-2026.1 fallback and must count as present."""
    _write_inputs(tmp_path, omit=("framework_registry.json",))
    (tmp_path / "frameworks.json").write_text(json.dumps({}))
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    result = await catalog_seeder.reseed_catalog(force=True)

    assert result == {"controls": {"status": "seeded", "count": 3}}
    assert all(count == 0 for count in fake_db.rows.values())


@pytest.mark.asyncio
async def test_both_registry_files_missing_refuses(
    tmp_path, monkeypatch, fake_db, seeded_calls
):
    _write_inputs(tmp_path, omit=("framework_registry.json",))
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    result = await catalog_seeder.reseed_catalog(force=True)

    assert result["status"] == "error"
    assert "framework_registry.json" in result["message"]
    assert "frameworks.json" in result["message"]
    assert all(count == 10 for count in fake_db.rows.values())


@pytest.mark.asyncio
async def test_unreadable_input_refuses(tmp_path, monkeypatch, fake_db, seeded_calls):
    """Present but unreadable is as fatal as absent, and must be caught too."""
    if os.geteuid() == 0:
        pytest.skip("root ignores the mode bits this test relies on")
    _write_inputs(tmp_path)
    (tmp_path / "erl.json").chmod(0o000)
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)
    try:
        result = await catalog_seeder.reseed_catalog(force=True)
    finally:
        (tmp_path / "erl.json").chmod(0o644)

    assert result["status"] == "error"
    assert "erl.json" in result["message"]
    assert all(count == 10 for count in fake_db.rows.values())


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param("", id="empty-file"),
        pytest.param('{"controls": [', id="truncated"),
        pytest.param("<!DOCTYPE html>\n<html>404</html>", id="html-error-page"),
        pytest.param("\x00\x01\x02", id="binary-garbage"),
    ],
)
@pytest.mark.asyncio
async def test_corrupt_input_refuses(
    tmp_path, monkeypatch, fake_db, seeded_calls, corrupt
):
    """Unparseable is as fatal as absent: json.load would raise AFTER the delete.

    A half-written file is the realistic failure — an interrupted `kubectl cp`,
    a truncated extraction, a proxy error page saved over the real thing. The
    readability check alone lets every one of these through.
    """
    _write_inputs(tmp_path)
    (tmp_path / "control_guidance.json").write_text(corrupt)
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    result = await catalog_seeder.reseed_catalog(force=True)

    assert result["status"] == "error"
    assert "control_guidance.json" in result["message"]
    assert all(count == 10 for count in fake_db.rows.values()), fake_db.rows
    assert fake_db.commits == 0
    assert seeded_calls == []


@pytest.mark.asyncio
async def test_missing_data_dir_refuses(tmp_path, monkeypatch, fake_db, seeded_calls):
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path / "does-not-exist")

    result = await catalog_seeder.reseed_catalog(force=True)

    assert result["status"] == "error"
    assert all(count == 10 for count in fake_db.rows.values())


@pytest.mark.asyncio
async def test_complete_inputs_still_delete_and_reseed(
    tmp_path, monkeypatch, fake_db, seeded_calls
):
    """The unchanged happy path: all seven tables cleared, then reseeded."""
    _write_inputs(tmp_path)
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    result = await catalog_seeder.reseed_catalog(force=True)

    assert result == {"controls": {"status": "seeded", "count": 3}}
    assert all(count == 0 for count in fake_db.rows.values()), fake_db.rows
    assert fake_db.commits == 1
    deleted = {
        sql.split()[2].strip('"')
        for sql in fake_db.statements
        if sql.upper().startswith("DELETE FROM")
    }
    assert deleted == CLEARED_TABLES
    assert seeded_calls == [tmp_path]


@pytest.mark.asyncio
async def test_force_false_early_return_unchanged(tmp_path, monkeypatch, fake_db):
    """force=False must keep its existing shape and never touch the filesystem."""
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path / "does-not-exist")

    result = await catalog_seeder.reseed_catalog()

    assert result == {"status": "skipped", "message": "Use force=True to reseed"}
    assert all(count == 10 for count in fake_db.rows.values())


# ---------------------------------------------------------------------------
# data_dir_override
# ---------------------------------------------------------------------------


def test_data_dir_override_restores_on_exception(tmp_path):
    original = catalog_seeder.DATA_DIR
    with pytest.raises(RuntimeError):
        with catalog_seeder.data_dir_override(tmp_path):
            assert catalog_seeder.DATA_DIR == tmp_path
            raise RuntimeError("boom")
    assert catalog_seeder.DATA_DIR == original


# ---------------------------------------------------------------------------
# FIX 2 — the import stages its extraction instead of writing DATA_DIR
# ---------------------------------------------------------------------------

# Exactly what extract_to_dir writes (scripts/extract_scf_data.py). Notably it
# does NOT write capability_themes.json — that file only ever comes from the
# image's curated copy, which is why the staging directory has to be primed.
EXTRACTOR_OUTPUTS = {
    "control_guidance.json": {"controls": []},
    "erl.json": {},
    "controls_mapping.json": {},
    "frameworks.json": {},
    "framework_registry.json": {},
    "publisher_changes.json": {},
    "domains.json": [],
    "assessment_objectives.json": {"objectives": []},
    "catalog_meta.json": {"catalog_version": "2026.3", "controls": 1},
}


@pytest.fixture
def fake_extractor(monkeypatch):
    """Stand in for scripts/extract_scf_data.py (avoids pandas and a workbook)."""
    module = types.ModuleType("extract_scf_data")
    seen: dict = {}

    def extract_to_dir(excel_path, output_dir):
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        seen["output_dir"] = output_dir
        for name, payload in EXTRACTOR_OUTPUTS.items():
            (output_dir / name).write_text(json.dumps(payload))
        return dict(EXTRACTOR_OUTPUTS["catalog_meta.json"])

    module.extract_to_dir = extract_to_dir
    monkeypatch.setitem(sys.modules, "extract_scf_data", module)
    return seen


@pytest.fixture
def import_task(monkeypatch, fake_extractor):
    """tasks_catalog.import_catalog with storage, gating and reseed stubbed."""
    from celery.backends.base import DisabledBackend

    import tasks_catalog
    from celery_app import celery_app

    # self.update_state() writes to the configured result backend, which is
    # redis. EagerResult carries its own state, so a disabled backend lets
    # apply() run the real task body with nothing to connect to.
    monkeypatch.setattr(
        tasks_catalog.import_catalog, "backend", DisabledBackend(app=celery_app)
    )
    monkeypatch.setattr(tasks_catalog, "single_tenant_flag_set", lambda: True)
    monkeypatch.setattr(
        tasks_catalog.storage_service,
        "download_blob_stream",
        lambda key: [b"not-really-a-workbook"],
    )

    observed: dict = {}

    async def _fake_reseed(force=False):
        # Capture the directory the seeders would read, while the override is
        # still in effect, and ask the real guard whether it is complete.
        staged = Path(catalog_seeder.DATA_DIR)
        observed["seeded_from"] = staged
        observed["missing"] = catalog_seeder.missing_reseed_inputs(staged)
        observed["files"] = sorted(p.name for p in staged.iterdir() if p.is_file())
        return {"controls": {"status": "seeded", "count": 1}}

    monkeypatch.setattr(catalog_seeder, "reseed_catalog", _fake_reseed)
    observed["extractor"] = fake_extractor
    return tasks_catalog, observed


def _curated(directory: Path) -> Path:
    """The three files Dockerfile.backend bakes into /app/data/json."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("capability_themes.json", "collection_interfaces.json",
                 "system_collection_recipes.json"):
        (directory / name).write_text(json.dumps({"from": "image"}))
    return directory


def test_import_succeeds_when_data_dir_is_not_writable(
    tmp_path, monkeypatch, import_task
):
    """The read-only-filesystem case: the import must still complete."""
    if os.geteuid() == 0:
        pytest.skip("root can write a read-only directory")
    tasks_catalog, observed = import_task
    data_dir = _curated(tmp_path / "json")
    before = sorted(p.name for p in data_dir.iterdir())
    data_dir.chmod(0o555)
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", data_dir)

    try:
        outcome = tasks_catalog.import_catalog.apply(
            args=("uploads/scf.xlsx", "scf.xlsx")
        )
    finally:
        data_dir.chmod(0o755)

    assert outcome.successful(), outcome.result
    assert outcome.result["status"] == "complete"
    # Seeded from the staging directory, not DATA_DIR, and it was complete.
    assert observed["seeded_from"] != data_dir
    assert observed["missing"] == []
    assert "capability_themes.json" in observed["files"]
    # DATA_DIR was left exactly as it was found.
    assert sorted(p.name for p in data_dir.iterdir()) == before
    # And the override was unwound.
    assert catalog_seeder.DATA_DIR == data_dir


def test_import_publishes_into_writable_data_dir(
    tmp_path, monkeypatch, import_task
):
    """Docker Compose parity: a writable DATA_DIR ends up with the extraction."""
    tasks_catalog, observed = import_task
    data_dir = _curated(tmp_path / "json")
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", data_dir)

    outcome = tasks_catalog.import_catalog.apply(
        args=("uploads/scf.xlsx", "scf.xlsx")
    )

    assert outcome.successful(), outcome.result
    assert outcome.result["status"] == "complete"
    on_disk = sorted(p.name for p in data_dir.iterdir() if p.is_file())
    # Every file the extractor writes is in DATA_DIR, exactly as before...
    assert set(EXTRACTOR_OUTPUTS) <= set(on_disk)
    # ...the curated files the image ships are untouched...
    assert "collection_interfaces.json" in on_disk
    assert json.loads((data_dir / "capability_themes.json").read_text()) == {
        "from": "image"
    }
    # ...and the content is the extraction's, not a stale copy.
    assert json.loads((data_dir / "catalog_meta.json").read_text()) == (
        EXTRACTOR_OUTPUTS["catalog_meta.json"]
    )
    assert observed["missing"] == []


def test_import_never_writes_into_data_dir_before_extracting(
    tmp_path, monkeypatch, import_task
):
    """The extraction target is the staging dir, never the install's DATA_DIR."""
    tasks_catalog, observed = import_task
    data_dir = _curated(tmp_path / "json")
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", data_dir)

    outcome = tasks_catalog.import_catalog.apply(args=("uploads/scf.xlsx", "scf.xlsx"))
    assert outcome.successful(), outcome.result

    # The extractor was handed the staging directory, not the install's.
    assert observed["extractor"]["output_dir"] != data_dir
    assert observed["extractor"]["output_dir"] == observed["seeded_from"]


def test_import_fails_loudly_when_the_reseed_refuses(
    tmp_path, monkeypatch, import_task
):
    """A refused reseed must not be reported to the operator as a completed import."""
    tasks_catalog, _ = import_task
    data_dir = _curated(tmp_path / "json")
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", data_dir)
    monkeypatch.setattr(
        catalog_seeder,
        "reseed_catalog",
        _refusing_reseed,
    )

    outcome = tasks_catalog.import_catalog.apply(args=("uploads/scf.xlsx", "scf.xlsx"))

    assert not outcome.successful()
    assert "catalogue seeding refused" in str(outcome.result)
    # The message names what was wrong, so the operator can act on it.
    assert "capability_themes.json" in str(outcome.result)


async def _refusing_reseed(force=False):
    return {
        "status": "error",
        "message": "Refusing to reseed: ... capability_themes.json ...",
        "missing": ["capability_themes.json"],
    }


def test_import_still_refuses_outside_single_tenant(monkeypatch, import_task):
    tasks_catalog, _observed = import_task
    monkeypatch.setattr(tasks_catalog, "single_tenant_flag_set", lambda: False)

    outcome = tasks_catalog.import_catalog.apply(args=("uploads/scf.xlsx", "scf.xlsx"))

    assert isinstance(outcome.result, RuntimeError)
    assert "OSS_SINGLE_TENANT" in str(outcome.result)


def test_import_reports_an_invalid_workbook(tmp_path, monkeypatch, import_task):
    tasks_catalog, _observed = import_task
    data_dir = _curated(tmp_path / "json")
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", data_dir)

    def _bad(excel_path, output_dir):
        raise ValueError("no SCF sheet")

    sys.modules["extract_scf_data"].extract_to_dir = _bad

    outcome = tasks_catalog.import_catalog.apply(args=("uploads/scf.xlsx", "scf.xlsx"))

    assert isinstance(outcome.result, RuntimeError)
    assert "not a valid SCF catalogue workbook" in str(outcome.result)
    assert catalog_seeder.DATA_DIR == data_dir
