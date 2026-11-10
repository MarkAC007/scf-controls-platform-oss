"""`python -m cli.admin refresh-risk-catalog`: argument wiring + exit codes.

The service is exercised in test_risk_catalog_service.py; here the session
factory and the live-version resolver are injected so what is under test is the
command's own decisions: which directory it reads, the version guard's exit
code, dry-run pass-through, and the missing-file message.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import SCFCatalogControl, SCFCatalogRisk  # noqa: E402
from cli import admin  # noqa: E402
from services import catalog_diff  # noqa: E402
from tests.test_catalog_apply import FakeSession, _control_row  # noqa: E402

RISKS = [{"code": "R-XX-1", "grouping": "Example", "old_code": None, "title": "Risk one",
          "description": "One.", "nist_csf_function": "Govern"}]
THREATS = [{"code": "NT-1", "grouping": "Example", "title": "Threat one", "description": "Invented."}]


class _CtxSession(FakeSession):
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _extraction(tmp_path, version="2026.3"):
    (tmp_path / "catalog_meta.json").write_text(json.dumps({"catalog_version": version}))
    (tmp_path / "risks.json").write_text(json.dumps({"catalog_version": version, "risks": RISKS}))
    (tmp_path / "threats.json").write_text(json.dumps({"catalog_version": version, "threats": THREATS}))
    (tmp_path / "control_guidance.json").write_text(json.dumps({"controls": [
        {"scf_id": "GOV-A1", "risk_threat_mapping": {"risk_codes": ["R-XX-1"], "threat_codes": [],
                                                      "risk_ratings": {"R-XX-1": "likely"}, "threat_ratings": {}}},
    ]}))
    return tmp_path


def _args(**over):
    base = {"data_dir": None, "allow_version_mismatch": False, "dry_run": False}
    base.update(over)
    return argparse.Namespace(**base)


def _patch(monkeypatch, session, live_version="2026.3"):
    monkeypatch.setattr(admin, "AsyncSessionLocal", lambda: session)

    async def _resolve(_session):
        return live_version

    monkeypatch.setattr(catalog_diff, "resolve_live_catalog_version", _resolve)


def test_parser_registers_the_command():
    parser = admin.create_parser()
    ns = parser.parse_args(["refresh-risk-catalog", "--data-dir", "/x", "--dry-run", "--allow-version-mismatch"])
    assert ns.command == "refresh-risk-catalog"
    assert (ns.data_dir, ns.dry_run, ns.allow_version_mismatch) == ("/x", True, True)


def test_refresh_writes_and_returns_zero(tmp_path, monkeypatch, capsys):
    ctrl = _control_row("GOV-A1", "Control")
    session = _CtxSession({SCFCatalogControl: [ctrl]})
    _patch(monkeypatch, session)
    rc = asyncio.run(admin.cmd_refresh_risk_catalog(_args(data_dir=str(_extraction(tmp_path)))))
    assert rc == 0
    assert session.commits == 1
    assert [r.code for r in session.tables[SCFCatalogRisk]] == ["R-XX-1"]
    assert ctrl.risk_codes == ["R-XX-1"]
    out = capsys.readouterr().out
    assert "written" in out and "+1 added" in out


def test_default_data_dir_is_the_seeder_directory(tmp_path, monkeypatch):
    import catalog_seeder
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", _extraction(tmp_path))
    session = _CtxSession({SCFCatalogControl: [_control_row("GOV-A1", "Control")]})
    _patch(monkeypatch, session)
    assert asyncio.run(admin.cmd_refresh_risk_catalog(_args())) == 0
    assert session.commits == 1


def test_version_mismatch_exits_one_without_writing(tmp_path, monkeypatch, capsys):
    session = _CtxSession({SCFCatalogControl: [_control_row("GOV-A1", "Control")]})
    _patch(monkeypatch, session, live_version="2026.2")
    rc = asyncio.run(admin.cmd_refresh_risk_catalog(_args(data_dir=str(_extraction(tmp_path)))))
    assert rc == 1 and session.commits == 0
    assert "--allow-version-mismatch" in capsys.readouterr().out


def test_version_mismatch_flag_lets_it_through(tmp_path, monkeypatch):
    session = _CtxSession({SCFCatalogControl: [_control_row("GOV-A1", "Control")]})
    _patch(monkeypatch, session, live_version="2026.2")
    rc = asyncio.run(admin.cmd_refresh_risk_catalog(
        _args(data_dir=str(_extraction(tmp_path)), allow_version_mismatch=True)))
    assert rc == 0 and session.commits == 1
    # Stamped with the LIVE version, so the rows agree with the controls.
    assert session.tables[SCFCatalogRisk][0].catalog_version == "2026.2"


def test_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    session = _CtxSession({SCFCatalogControl: [_control_row("GOV-A1", "Control")]})
    _patch(monkeypatch, session)
    rc = asyncio.run(admin.cmd_refresh_risk_catalog(_args(data_dir=str(_extraction(tmp_path)), dry_run=True)))
    assert rc == 0 and session.commits == 0
    assert "DRY RUN" in capsys.readouterr().out


def test_missing_files_exit_one(tmp_path, monkeypatch, capsys):
    session = _CtxSession()
    _patch(monkeypatch, session)
    rc = asyncio.run(admin.cmd_refresh_risk_catalog(_args(data_dir=str(tmp_path))))
    assert rc == 1
    assert "not found" in capsys.readouterr().out
