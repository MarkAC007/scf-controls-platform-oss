"""services.risk_catalog: upsert + control-mapping refresh (ISC-17, 18, 19, 45, 49).

Reuses the transactional FakeSession from test_catalog_apply. Every code and
title is invented: the SCF Risk / Threat Catalog text is CC BY-ND and never
lands in a fixture.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from catalog_models import SCFCatalogControl, SCFCatalogRisk, SCFCatalogThreat  # noqa: E402
from services import risk_catalog as rc  # noqa: E402
from tests.test_catalog_apply import FakeSession, _control_row, _row_attrs  # noqa: E402

T0 = datetime(2026, 1, 1)
T1 = datetime(2026, 6, 1)

RISKS = [
    {"code": "R-XX-1", "grouping": "Example", "old_code": None, "title": "Risk one",
     "description": "One.", "nist_csf_function": "Govern"},
    # Renumbered: used to be R-YY-3 in the previous catalogue.
    {"code": "R-XX-2", "grouping": "Example", "old_code": "R-YY-3", "title": "Risk two",
     "description": "Two.", "nist_csf_function": "Protect"},
]
THREATS = [
    {"code": "NT-1", "grouping": "Natural", "title": "Threat one", "description": "T1."},
]


def _risk_row(code, **over):
    attrs = {"code": code, "grouping": "Example", "old_code": None, "title": f"Live {code}",
             "description": None, "nist_csf_function": None, "status": "active",
             "retired_in_version": None, "catalog_version": "2026.2",
             "created_at": T0, "updated_at": T0}
    attrs.update(over)
    return SCFCatalogRisk(**attrs)


def _ctrl(scf_id, status="active", **over):
    row = _control_row(scf_id, f"Control {scf_id}")
    row.status = status
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _extraction_control(scf_id, risk_codes, threat_codes=(), ratings=None):
    return {
        "scf_id": scf_id,
        "risk_threat_mapping": {
            "risk_codes": list(risk_codes),
            "threat_codes": list(threat_codes),
            "risk_ratings": ratings or {c: "likely" for c in risk_codes},
            "threat_ratings": {c: "possible" for c in threat_codes},
        },
    }


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_inserts_updates_deprecates_and_never_deletes():
    stale = _risk_row("R-ZZ-9")               # not in the new catalogue
    existing = _risk_row("R-XX-1", title="Old title")
    session = FakeSession({SCFCatalogRisk: [stale, existing]})

    counts = await rc.upsert_risks(session, RISKS, "2026.3", now=T1)
    await session.flush()

    assert (counts.added, counts.updated, counts.deprecated) == (1, 1, 1)
    rows = {r.code: r for r in session.tables[SCFCatalogRisk]}
    assert set(rows) == {"R-ZZ-9", "R-XX-1", "R-XX-2"}      # nothing deleted
    assert rows["R-XX-1"].title == "Risk one" and rows["R-XX-1"].updated_at == T1
    assert rows["R-XX-1"].catalog_version == "2026.3"
    assert rows["R-XX-2"].old_code == "R-YY-3" and rows["R-XX-2"].status == "active"
    assert rows["R-ZZ-9"].status == "deprecated"
    assert rows["R-ZZ-9"].retired_in_version == "2026.3"
    assert rows["R-ZZ-9"].title == "Live R-ZZ-9"           # data kept


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_resurrects():
    session = FakeSession({SCFCatalogRisk: [_risk_row("R-XX-1", status="deprecated", retired_in_version="2026.2")]})
    first = await rc.upsert_risks(session, RISKS, "2026.3", now=T1)
    await session.flush()
    assert first.updated == 1                                # resurrected counts as updated
    row = next(r for r in session.tables[SCFCatalogRisk] if r.code == "R-XX-1")
    assert row.status == "active" and row.retired_in_version is None

    second = await rc.upsert_risks(session, RISKS, "2026.3", now=datetime(2026, 7, 1))
    assert (second.added, second.updated, second.deprecated) == (0, 0, 0)
    assert second.unchanged == 2
    assert row.updated_at == T1                              # untouched on the no-op pass


@pytest.mark.asyncio
async def test_upsert_threats_shares_the_lifecycle():
    session = FakeSession()
    counts = await rc.upsert_threats(session, THREATS, "2026.3", now=T1)
    await session.flush()
    assert counts.added == 1
    row = session.tables[SCFCatalogThreat][0]
    assert (row.code, row.grouping, row.title, row.status) == ("NT-1", "Natural", "Threat one", "active")


# ---------------------------------------------------------------------------
# control-mapping refresh
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_writes_only_the_four_mapping_columns():
    ctrl = _ctrl("GOV-A1", risk_codes=[], threat_codes=[])
    before = _row_attrs(ctrl)
    session = FakeSession({SCFCatalogControl: [ctrl]})
    extraction = [_extraction_control("GOV-A1", ["R-XX-1", "R-XX-2"], ["NT-1"])]

    counts = await rc.refresh_control_risk_mappings(session, extraction, RISKS, THREATS, now=T1)

    assert counts.controls_updated == 1
    after = _row_attrs(ctrl)
    changed = {k for k in after if after[k] != before.get(k)}
    assert changed == set(rc.CONTROL_MAPPING_FIELDS) | {"updated_at"}
    assert ctrl.risk_codes == ["R-XX-1", "R-XX-2"]
    assert ctrl.threat_codes == ["NT-1"]
    assert ctrl.risk_ratings == {"R-XX-1": "likely", "R-XX-2": "likely"}
    assert ctrl.threat_ratings == {"NT-1": "possible"}
    assert len(session.tables[SCFCatalogControl]) == 1      # no insert for unknown ids


@pytest.mark.asyncio
async def test_refresh_is_idempotent():
    ctrl = _ctrl("GOV-A1")
    session = FakeSession({SCFCatalogControl: [ctrl]})
    extraction = [_extraction_control("GOV-A1", ["R-XX-1"])]
    await rc.refresh_control_risk_mappings(session, extraction, RISKS, THREATS, now=T1)
    second = await rc.refresh_control_risk_mappings(session, extraction, RISKS, THREATS, now=datetime(2026, 7, 1))
    assert second.controls_updated == 0 and second.controls_unchanged == 1
    assert ctrl.updated_at == T1


@pytest.mark.asyncio
async def test_refresh_remaps_deprecated_rows_via_old_code_and_drops_unknown_codes():
    # Deprecated in an earlier upgrade, so absent from the 2026.3 extraction.
    # Carries the previous catalogue's codes: R-YY-3 was renumbered to R-XX-2,
    # R-QQ-7 no longer exists at all, R-XX-1 is still valid.
    dep = _ctrl("OLD-A9", status="deprecated", retired_in_version="2026.3",
                risk_codes=["R-YY-3", "R-QQ-7", "R-XX-1"],
                threat_codes=["NT-1", "NT-99"],
                risk_ratings={"R-YY-3": "likely", "R-QQ-7": "possible", "R-XX-1": "possible"},
                threat_ratings=None)
    # Retired a release earlier: its codes predate what Old Risk # describes,
    # so it is only stripped of codes the catalogue no longer defines.
    older = _ctrl("OLD-B2", status="deprecated", retired_in_version="2026.2",
                  risk_codes=["R-YY-3", "R-QQ-7", "R-XX-1"], threat_codes=[],
                  risk_ratings=None, threat_ratings=None)
    session = FakeSession({SCFCatalogControl: [dep, older]})

    counts = await rc.refresh_control_risk_mappings(session, [], RISKS, THREATS, now=T1, version="2026.3")

    assert counts.deprecated_remapped == 2
    assert dep.risk_codes == ["R-XX-2", "R-XX-1"]
    assert dep.risk_ratings == {"R-XX-2": "likely", "R-XX-1": "possible"}
    assert dep.threat_codes == ["NT-1"]
    assert dep.threat_ratings is None
    assert dep.status == "deprecated"                        # lifecycle untouched
    assert older.risk_codes == ["R-XX-1"]                    # stripped, not renumbered


@pytest.mark.asyncio
async def test_refresh_reports_extraction_rows_missing_from_live():
    session = FakeSession({SCFCatalogControl: [_ctrl("GOV-A1")]})
    extraction = [_extraction_control("GOV-A1", ["R-XX-1"]), _extraction_control("NEW-A1", ["R-XX-1"])]
    counts = await rc.refresh_control_risk_mappings(session, extraction, RISKS, THREATS, now=T1)
    assert counts.extraction_only == ["NEW-A1"]
    assert {c.scf_id for c in session.tables[SCFCatalogControl]} == {"GOV-A1"}


# ---------------------------------------------------------------------------
# refresh_risk_catalog orchestration (what the CLI calls)
# ---------------------------------------------------------------------------


def _write_extraction(tmp_path, version="2026.3"):
    (tmp_path / "catalog_meta.json").write_text(json.dumps({"catalog_version": version}))
    (tmp_path / "risks.json").write_text(json.dumps({"catalog_version": version, "risks": RISKS}))
    (tmp_path / "threats.json").write_text(json.dumps({"catalog_version": version, "threats": THREATS}))
    (tmp_path / "control_guidance.json").write_text(json.dumps({
        "catalog_version": version,
        "controls": [_extraction_control("GOV-A1", ["R-XX-1"], ["NT-1"])],
    }))
    return tmp_path


@pytest.mark.asyncio
async def test_refresh_risk_catalog_refuses_an_extraction_with_no_risks(tmp_path):
    """An empty risks.json (no Risk Catalog sheet, or a failed extraction) must
    not deprecate the live catalogue."""
    _write_extraction(tmp_path, "2026.3")
    (tmp_path / rc.RISKS_FILE).write_text(json.dumps({"catalog_version": "2026.3", "risks": []}))
    live = [_risk_row("R-XX-1", status="active"), _risk_row("R-XX-2", status="active")]
    session = FakeSession({SCFCatalogControl: [_ctrl("GOV-A1")], SCFCatalogRisk: live})
    with pytest.raises(rc.RiskCatalogEmptyExtraction):
        await rc.refresh_risk_catalog(session, tmp_path, live_version="2026.3")
    assert session.commits == 0
    assert all(r.status == "active" for r in live)


@pytest.mark.asyncio
async def test_refresh_risk_catalog_refuses_version_mismatch(tmp_path):
    _write_extraction(tmp_path, "2026.3")
    session = FakeSession({SCFCatalogControl: [_ctrl("GOV-A1")]})
    with pytest.raises(rc.RiskCatalogVersionMismatch):
        await rc.refresh_risk_catalog(session, tmp_path, live_version="2026.2")
    assert session.commits == 0
    assert session.tables[SCFCatalogRisk] == []


@pytest.mark.asyncio
async def test_refresh_risk_catalog_dry_run_rolls_back(tmp_path):
    _write_extraction(tmp_path)
    ctrl = _ctrl("GOV-A1")
    session = FakeSession({SCFCatalogControl: [ctrl]})
    report = await rc.refresh_risk_catalog(session, tmp_path, live_version="2026.3", dry_run=True, now=T1)
    assert report.dry_run and report.risks.added == 2 and report.controls.controls_updated == 1
    assert session.commits == 0 and "rollback" in session.events
    assert ctrl.risk_codes == []                             # rolled back


@pytest.mark.asyncio
async def test_refresh_risk_catalog_commits_and_touches_no_org_table(tmp_path):
    _write_extraction(tmp_path)
    ctrl = _ctrl("GOV-A1")
    session = FakeSession({SCFCatalogControl: [ctrl]})
    report = await rc.refresh_risk_catalog(session, tmp_path, live_version="2026.3", now=T1)
    assert not report.dry_run and session.commits == 1
    assert {r.code for r in session.tables[SCFCatalogRisk]} == {"R-XX-1", "R-XX-2"}
    assert {t.code for t in session.tables[SCFCatalogThreat]} == {"NT-1"}
    assert ctrl.risk_codes == ["R-XX-1"] and ctrl.threat_codes == ["NT-1"]
    # ISC-45: the service has no reference to any organisation table at all.
    import inspect
    src = inspect.getsource(rc)
    assert "risk_assessments" not in src.lower().replace("risk_assessment ", "")
    assert "accepted_at" not in src and "organization" not in src.lower().replace("organisation", "")


@pytest.mark.asyncio
async def test_refresh_risk_catalog_missing_file(tmp_path):
    (tmp_path / "catalog_meta.json").write_text("{}")
    with pytest.raises(FileNotFoundError, match="risks.json"):
        await rc.refresh_risk_catalog(FakeSession(), tmp_path, live_version=None)
