"""Tests for `python -m cli.admin backfill-framework-registry`.

No live database and no workbook: the extractor and the session factory are
both injected, so what is under test is the command's own decisions — the
version guard, the source it stamps, and that it never touches catalogue rows.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import CatalogFrameworkRegistry  # noqa: E402
from cli import admin  # noqa: E402

REGISTRY = {
    "nist_800_53_r5": {
        "name": "NIST 800-53 rev5",
        "focal_document_id": "usa-federal-nist-800-53-r5",
        "geography": "USA",
    },
    "iso_27002_2022": {
        "name": "ISO 27002:2022",
        "focal_document_id": None,
        "geography": "International",
    },
}


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Answers the registry SELECT; records every statement it is handed."""

    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.added = []
        self.commits = 0
        self.statements = []

    async def execute(self, statement, params=None):
        self.statements.append(statement)
        return _FakeResult(self.rows)

    def add(self, obj):
        self.added.append(obj)
        self.rows.append(obj)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _patch(monkeypatch, session, *, live_version, workbook_version, registry=REGISTRY):
    monkeypatch.setattr(admin, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        admin,
        "_load_extractor",
        lambda: argparse.Namespace(
            extract_framework_registry_only=lambda path: (workbook_version, registry)
        ),
    )

    async def _resolve(_session):
        return live_version

    import services.catalog_diff as cd

    monkeypatch.setattr(cd, "resolve_live_catalog_version", _resolve)


def _args(**over):
    attrs = {"workbook": "/tmp/scf.xlsx", "allow_version_mismatch": False}
    attrs.update(over)
    return argparse.Namespace(**attrs)


def test_backfill_refuses_version_mismatch(monkeypatch, capsys):
    """Naming both versions, because the wrong workbook writes wrong identifiers."""
    session = _FakeSession()
    _patch(monkeypatch, session, live_version="2026.1", workbook_version="2026.3")

    rc = asyncio.run(admin.cmd_backfill_framework_registry(_args()))

    assert rc == 1
    assert session.added == [] and session.commits == 0
    out = capsys.readouterr().out
    assert "2026.3" in out and "2026.1" in out
    assert "--allow-version-mismatch" in out


def test_backfill_accepts_a_mismatch_when_explicitly_allowed(monkeypatch, capsys):
    session = _FakeSession()
    _patch(monkeypatch, session, live_version="2026.1", workbook_version="2026.3")

    rc = asyncio.run(
        admin.cmd_backfill_framework_registry(_args(allow_version_mismatch=True))
    )

    assert rc == 0
    # Stamped with the LIVE version — the row describes the live rows, not the
    # workbook it happened to be read from.
    assert session.added[0].catalog_version == "2026.1"
    assert "--allow-version-mismatch" in capsys.readouterr().out


def test_backfill_upserts_registry(monkeypatch, capsys):
    session = _FakeSession()
    _patch(monkeypatch, session, live_version="2026.1", workbook_version="2026.1")

    rc = asyncio.run(admin.cmd_backfill_framework_registry(_args()))

    assert rc == 0
    assert len(session.added) == 1
    row = session.added[0]
    assert isinstance(row, CatalogFrameworkRegistry)
    assert row.catalog_version == "2026.1"
    assert row.source == "backfill"
    assert row.registry == REGISTRY
    assert session.commits == 1

    out = capsys.readouterr().out
    assert "2" in out  # 2 entries
    assert "1" in out  # 1 carrying a focal-document id

    # Catalogue control rows are never selected, let alone written.
    assert not any(
        "scf_catalog_controls" in str(stmt) for stmt in session.statements
    )


def test_backfill_updates_an_existing_row_in_place(monkeypatch):
    existing = CatalogFrameworkRegistry(
        catalog_version="2026.1", registry={}, source="seed"
    )
    session = _FakeSession(rows=[existing])
    _patch(monkeypatch, session, live_version="2026.1", workbook_version="2026.1")

    rc = asyncio.run(admin.cmd_backfill_framework_registry(_args()))

    assert rc == 0
    assert session.added == []
    assert existing.registry == REGISTRY
    assert existing.source == "backfill"


def test_backfill_refuses_without_a_live_catalog_version(monkeypatch, capsys):
    session = _FakeSession()
    _patch(monkeypatch, session, live_version=None, workbook_version="2026.1")

    rc = asyncio.run(admin.cmd_backfill_framework_registry(_args()))

    assert rc == 1
    assert session.added == []
    assert "seed the catalog" in capsys.readouterr().out.lower()


def test_backfill_refuses_an_empty_registry(monkeypatch, capsys):
    """A pre-2026.1 workbook: writing nothing beats writing a row that lies."""
    session = _FakeSession()
    _patch(monkeypatch, session, live_version="2026.1", workbook_version="2026.1", registry={})

    rc = asyncio.run(admin.cmd_backfill_framework_registry(_args()))

    assert rc == 1
    assert session.added == []
    assert "No framework registry" in capsys.readouterr().out


def test_command_is_registered_and_requires_a_workbook():
    parser = admin.create_parser()
    args = parser.parse_args(
        ["backfill-framework-registry", "--workbook", "/tmp/scf.xlsx"]
    )
    assert args.command == "backfill-framework-registry"
    assert args.allow_version_mismatch is False
    with pytest.raises(SystemExit):
        parser.parse_args(["backfill-framework-registry"])
