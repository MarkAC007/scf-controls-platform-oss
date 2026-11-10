"""API layer of the SCF Risk Catalog feature (ISC-14..16, 26..28, 41, 42, 46).

No database: each endpoint gets an AsyncMock session whose ``execute`` answers
by the table named in the compiled statement, so the tests pin behaviour, not
query order. Codes and titles are invented (the catalogue text is CC BY-ND).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from fastapi import HTTPException

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import catalog as catalog_api  # noqa: E402
from api import custom_risks as custom_api  # noqa: E402
from api import risk_assessments as ra_api  # noqa: E402
from schemas import CustomRiskControlMappingCreate  # noqa: E402
from services import compensating_controls as cc  # noqa: E402

ORG = uuid4()
MEMBERSHIP = SimpleNamespace(user=SimpleNamespace(db_id=None))


def _result(rows=(), *, scalar=None, first="__unset__"):
    r = MagicMock()
    rows = list(rows)
    r.scalars.return_value.all.return_value = rows
    r.all.return_value = rows
    r.scalar.return_value = scalar
    r.scalar_one_or_none.return_value = rows[0] if rows else None
    r.first.return_value = (rows[0] if rows else None) if first == "__unset__" else first
    return r


@pytest.fixture(autouse=True)
def _version_from_the_fake_db(monkeypatch):
    """The API asks resolve_live_catalog_version (ledger first); the fake only
    answers the aggregate fallback, so route the resolver to that query."""
    async def _resolve(db):
        return (await db.execute(select(func.max(catalog_api.SCFCatalogRisk.catalog_version)))).scalar()
    monkeypatch.setattr(catalog_api, "resolve_live_catalog_version", _resolve)


def _db(by_table: dict):
    """``by_table`` maps a table name (or 'max(' for aggregate selects) to the
    result to answer with; first matching key wins, so put 'max(' first."""
    calls = []

    async def execute(stmt, *a, **k):
        sql = str(stmt)
        calls.append(sql)
        for key, value in by_table.items():
            if key in sql:
                return value
        raise AssertionError(f"unexpected statement: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=execute)
    db.calls = calls
    return db


# ---------------------------------------------------------------------------
# /catalog/risks, /catalog/threats
# ---------------------------------------------------------------------------


def _risk_row(code, grouping, title, status="active", old_code=None):
    return SimpleNamespace(code=code, grouping=grouping, title=title, description=f"{title}.",
                           status=status, old_code=old_code, nist_csf_function="Govern",
                           catalog_version="2026.3")


def test_catalog_routes_are_registered_read_only():
    routes = {(r.path, m) for r in catalog_api.router.routes for m in getattr(r, "methods", ())}
    assert ("/catalog/risks", "GET") in routes and ("/catalog/threats", "GET") in routes
    assert not {m for p, m in routes if p in ("/catalog/risks", "/catalog/threats")} - {"GET"}


def test_payload_is_risk_codes_file_shaped_with_stable_prefix_colours():
    rows = [_risk_row("R-XX-1", "Example grouping", "One"),
            _risk_row("R-XX-2", "Example grouping", "Two", old_code="R-YY-7"),
            _risk_row("R-AA-1", "Another grouping", "Three")]
    payload = catalog_api._code_catalog_payload(rows, "2026.3", ("old_code", "nist_csf_function"))
    assert payload["catalog_version"] == "2026.3" and payload["total"] == 3
    assert payload["categories"] == {
        "AA": {"name": "Another grouping", "color": catalog_api._CATALOG_CODE_PALETTE[0]},
        "XX": {"name": "Example grouping", "color": catalog_api._CATALOG_CODE_PALETTE[1]},
    }
    assert payload["codes"]["R-XX-2"] == {
        "category": "XX", "title": "Two", "description": "Two.", "status": "active",
        "old_code": "R-YY-7", "nist_csf_function": "Govern",
    }
    # Same prefixes, different row order → same colours.
    again = catalog_api._code_catalog_payload(list(reversed(rows)), "2026.3", ())
    assert {k: v["color"] for k, v in again["categories"].items()} == \
           {k: v["color"] for k, v in payload["categories"].items()}


def test_threat_categories_are_the_code_prefix():
    assert catalog_api._code_prefix("NT-3") == "NT" and catalog_api._code_prefix("MT-12") == "MT"
    assert catalog_api._code_prefix("R-ORG-4") == "ORG"


def test_palette_wraps_past_fourteen_prefixes():
    rows = [_risk_row(f"R-{chr(65 + i)}{chr(65 + i)}-1", f"G{i}", f"T{i}") for i in range(16)]
    payload = catalog_api._code_catalog_payload(rows, None, ())
    assert len(payload["categories"]) == 16
    assert all(v["color"] in catalog_api._CATALOG_CODE_PALETTE for v in payload["categories"].values())


@pytest.mark.asyncio
async def test_list_catalog_risks_defaults_to_active_and_reports_version():
    rows = [_risk_row("R-XX-1", "G", "One")]
    db = _db({"max(": _result(scalar="2026.3"), "scf_catalog_risks": _result(rows)})
    payload = await catalog_api.list_catalog_risks(include_deprecated=False, db=db, current_user=None)
    assert payload["catalog_version"] == "2026.3"
    assert list(payload["codes"]) == ["R-XX-1"]
    listing = next(sql for sql in db.calls if "scf_catalog_risks" in sql and "max(" not in sql)
    assert "WHERE scf_catalog_risks.status" in listing


@pytest.mark.asyncio
async def test_empty_catalog_is_an_empty_payload_not_an_error():
    db = _db({"max(": _result(scalar=None), "scf_catalog_threats": _result([])})
    payload = await catalog_api.list_catalog_threats(include_deprecated=True, db=db, current_user=None)
    assert payload == {"catalog_version": None, "total": 0, "categories": {}, "codes": {}}
    listing = next(sql for sql in db.calls if "scf_catalog_threats" in sql and "max(" not in sql)
    assert "WHERE" not in listing                                   # include_deprecated: no filter


# ---------------------------------------------------------------------------
# compensating-control gate: Likely only when the catalogue carries ratings
# ---------------------------------------------------------------------------


def test_catalog_linked_codes_counts_only_likely_when_rated():
    codes = ["R-XX-1", "R-XX-2", "R-XX-3"]
    assert cc.catalog_linked_codes(codes, {"R-XX-1": "likely", "R-XX-2": "possible", "R-XX-3": "Likely"}) == {"R-XX-1", "R-XX-3"}
    assert cc.catalog_linked_codes(codes, None) == set(codes)          # pre-2026.3 catalogue
    assert cc.catalog_linked_codes(codes, {}) == set(codes)
    assert cc.catalog_linked_codes(None, {"R-XX-1": "likely"}) == set()


@pytest.mark.asyncio
async def test_linked_risk_codes_unions_likely_catalog_with_org_links():
    db = _db({
        "scf_catalog_controls": _result([], first=(["R-XX-1", "R-XX-2"], {"R-XX-1": "likely", "R-XX-2": "possible"})),
        "custom_risk_control_mappings": _result(["R-ORG-1", "R-XX-2"]),
    })
    linked = await cc.linked_risk_codes(db, ORG, "GOV-A1")
    assert linked == {"R-XX-1", "R-ORG-1", "R-XX-2"}   # R-XX-2 via the org link, not the catalogue


# ---------------------------------------------------------------------------
# risk-summary: the universe is the catalogue table ∪ custom definitions
# ---------------------------------------------------------------------------


def _assessment(code, assessed=True):
    return SimpleNamespace(
        risk_code=code,
        likelihood=3 if assessed else None, impact=3 if assessed else None,
        treatment_status="identified",
        get_inherent_risk_level=lambda *_: "medium" if assessed else None,
        get_residual_risk_level=lambda *_: None,
    )


@pytest.mark.asyncio
async def test_risk_summary_counts_from_the_catalog_table_and_clamps():
    assessments = [_assessment("R-XX-1"), _assessment("R-XX-2", assessed=False),
                   _assessment("R-ZZ-9"), _assessment("R-ZZ-8"), _assessment("R-ZZ-7")]   # three orphans
    db = _db({
        "risk_assessments": _result(assessments),
        "organization_risk_profiles": _result([]),
        "scf_catalog_risks": _result(["R-XX-1", "R-XX-2"]),
        "custom_risk_definitions": _result(["R-ORG-1"]),
    })
    summary = await ra_api.get_risk_summary(org_id=ORG, membership=MEMBERSHIP, db=db)
    assert summary.total_risks == 3
    assert summary.assessed_risks == 1              # the orphans are not counted
    assert summary.unassessed_risks == 1            # R-XX-2 (row, unscored) + R-ORG-1 (no row) → 3 - 2
    assert summary.inherent_medium == 4             # level tallies still cover every row


@pytest.mark.asyncio
async def test_risk_summary_never_goes_negative():
    """Five scored rows whose codes the catalogue retired count against a
    universe of three: assessed 0, unassessed 3, never 3 - 5."""
    db = _db({
        "risk_assessments": _result([_assessment(f"R-ZZ-{i}") for i in range(5)]),
        "organization_risk_profiles": _result([]),
        "scf_catalog_risks": _result(["R-XX-1", "R-XX-2", "R-XX-3"]),
        "custom_risk_definitions": _result([]),
    })
    summary = await ra_api.get_risk_summary(org_id=ORG, membership=MEMBERSHIP, db=db)
    assert (summary.total_risks, summary.assessed_risks, summary.unassessed_risks) == (3, 0, 3)


@pytest.mark.asyncio
async def test_risk_summary_counts_the_register_when_the_catalogue_is_not_loaded():
    """Before refresh-risk-catalog has run the table is empty; the scored rows
    are still the organisation's register, so the strip must not read 0 of 0."""
    db = _db({
        "risk_assessments": _result([_assessment(f"R-ZZ-{i}") for i in range(5)] + [_assessment("R-ORG-1", assessed=False)]),
        "organization_risk_profiles": _result([]),
        "scf_catalog_risks": _result([]),
        "custom_risk_definitions": _result(["R-ORG-1"]),
    })
    summary = await ra_api.get_risk_summary(org_id=ORG, membership=MEMBERSHIP, db=db)
    assert (summary.total_risks, summary.assessed_risks, summary.unassessed_risks) == (6, 5, 0)


# ---------------------------------------------------------------------------
# controls-for-risk: union, Likely-first order, sources
# ---------------------------------------------------------------------------


def _ctl(scf_id, status="active", codes=("R-XX-1",), rating=None, superseded_by=None):
    return SimpleNamespace(scf_id=scf_id, control_name=f"Control {scf_id}", status=status,
                           retired_in_version="2026.2" if status == "deprecated" else None,
                           superseded_by=superseded_by, risk_codes=list(codes),
                           risk_ratings={"R-XX-1": rating} if rating else None)


def _scoped(scf_id):
    return SimpleNamespace(scf_id=scf_id, implementation_status="implemented", priority="high", target_date=None)


@pytest.mark.asyncio
async def test_controls_for_risk_unions_catalog_and_org_links_likely_first():
    catalog = [
        _ctl("AAA-1", rating="possible"),                                 # catalog, possible
        _ctl("BBB-1", rating="likely"),                                   # catalog, likely
        _ctl("CCC-1", status="deprecated", rating="likely"),              # catalog, deprecated, unscoped → hidden
        _ctl("DDD-1", status="deprecated", codes=(), superseded_by="DDD-2"),  # org-only, deprecated → visible
        _ctl("EEE-1", rating="possible"),                                 # both (pre-guard link)
    ]
    db = _db({
        "custom_risk_control_mappings": _result([("DDD-1",), ("EEE-1",)]),
        "scf_catalog_controls": _result(catalog),
        "scoped_controls": _result([_scoped("AAA-1"), _scoped("DDD-1")]),
    })
    resp = await ra_api.get_controls_for_risk(org_id=ORG, risk_code="r-xx-1", membership=MEMBERSHIP, db=db)

    assert resp["risk_code"] == "R-XX-1"
    assert resp["catalog_control_ids"] == ["BBB-1", "AAA-1", "EEE-1", "DDD-1"]
    assert resp["total_catalog_controls"] == 4
    assert resp["sources"] == {"BBB-1": "catalog", "AAA-1": "catalog", "EEE-1": "both", "DDD-1": "org"}
    assert resp["ratings"] == {"BBB-1": "likely", "AAA-1": "possible", "EEE-1": "possible"}
    assert [(s["scf_id"], s["source"], s["rating"]) for s in resp["scoped_controls"]] == [
        ("AAA-1", "catalog", "possible"), ("DDD-1", "org", None)]
    assert resp["scoped_controls"][1]["superseded_by"] == "DDD-2"
    # The catalogue query resolves the org-linked ids too (one query, both sources).
    catalog_sql = next(sql for sql in db.calls if "scf_catalog_controls" in sql)
    assert "IN (" in catalog_sql and "risk_ratings" in catalog_sql


@pytest.mark.asyncio
async def test_controls_for_risk_catalog_only_needs_no_in_clause_and_hides_deprecated():
    db = _db({
        "custom_risk_control_mappings": _result([]),
        "scf_catalog_controls": _result([_ctl("AAA-1", rating="likely"), _ctl("CCC-1", status="deprecated")]),
        "scoped_controls": _result([]),
    })
    resp = await ra_api.get_controls_for_risk(org_id=ORG, risk_code="R-XX-1", membership=MEMBERSHIP, db=db)
    assert resp["catalog_control_ids"] == ["AAA-1"] and resp["sources"] == {"AAA-1": "catalog"}
    assert "IN (" not in next(sql for sql in db.calls if "scf_catalog_controls" in sql)


@pytest.mark.asyncio
async def test_controls_for_risk_empty_shape():
    db = _db({"custom_risk_control_mappings": _result([]), "scf_catalog_controls": _result([])})
    resp = await ra_api.get_controls_for_risk(org_id=ORG, risk_code="R-ORG-1", membership=MEMBERSHIP, db=db)
    assert resp == {"risk_code": "R-ORG-1", "total_catalog_controls": 0, "catalog_control_ids": [],
                    "sources": {}, "ratings": {}, "scoped_controls": []}
    assert not any("scoped_controls" in sql for sql in db.calls)


# ---------------------------------------------------------------------------
# org links on SCF risks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_risk_exists_accepts_custom_or_active_catalog_risk_only():
    custom_found = _db({"custom_risk_definitions": _result([SimpleNamespace()])})
    assert await custom_api._risk_exists(custom_found, ORG, "R-ORG-1") is True
    assert not any("scf_catalog_risks" in sql for sql in custom_found.calls)

    org_missing = _db({"custom_risk_definitions": _result([])})
    assert await custom_api._risk_exists(org_missing, ORG, "R-ORG-9") is False   # never consults the catalogue

    scf = _db({"custom_risk_definitions": _result([]), "scf_catalog_risks": _result(["R-XX-1"])})
    assert await custom_api._risk_exists(scf, ORG, "r-xx-1") is True
    catalog_sql = next(sql for sql in scf.calls if "scf_catalog_risks" in sql)
    assert "status" in catalog_sql                                              # active only

    retired = _db({"custom_risk_definitions": _result([]), "scf_catalog_risks": _result([])})
    assert await custom_api._risk_exists(retired, ORG, "R-XX-9") is False


@pytest.mark.asyncio
async def test_linking_an_already_catalog_mapped_control_is_409():
    db = _db({
        "custom_risk_definitions": _result([]),
        "scf_catalog_risks": _result(["R-XX-1"]),
        "custom_risk_control_mappings": _result([]),
        "scf_catalog_controls": _result([], first=("active", None, ["R-XX-1", "R-XX-2"], {"R-XX-1": "likely", "R-XX-2": "possible"})),
    })
    with pytest.raises(HTTPException) as exc:
        await custom_api.add_custom_risk_control(
            org_id=ORG, risk_code="r-xx-1", data=CustomRiskControlMappingCreate(scf_id="GOV-A1"),
            request=MagicMock(), membership=MEMBERSHIP, db=db)
    assert exc.value.status_code == 409 and "already maps" in exc.value.detail
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_a_possible_rated_catalog_mapping_still_takes_an_org_link():
    """The 409 guard uses the governance gate's definition of a link (Likely
    only), so a Possible-rated pair can be made a real link by the organisation.
    A catalogue without ratings treats every mapping as a link."""
    unrated = _db({
        "custom_risk_definitions": _result([]),
        "scf_catalog_risks": _result(["R-XX-2"]),
        "custom_risk_control_mappings": _result([]),
        "scf_catalog_controls": _result([], first=("active", None, ["R-XX-1", "R-XX-2"], None)),
    })
    with pytest.raises(HTTPException) as exc:
        await custom_api.add_custom_risk_control(
            org_id=ORG, risk_code="R-XX-2", data=CustomRiskControlMappingCreate(scf_id="GOV-A1"),
            request=MagicMock(), membership=MEMBERSHIP, db=unrated)
    assert exc.value.status_code == 409

    possible = _db({
        "custom_risk_definitions": _result([]),
        "scf_catalog_risks": _result(["R-XX-2"]),
        "custom_risk_control_mappings": _result([]),
        "scf_catalog_controls": _result([], first=("active", None, ["R-XX-1", "R-XX-2"], {"R-XX-1": "likely", "R-XX-2": "possible"})),
    })
    try:
        await custom_api.add_custom_risk_control(
            org_id=ORG, risk_code="r-xx-2", data=CustomRiskControlMappingCreate(scf_id="GOV-A1"),
            request=MagicMock(), membership=MEMBERSHIP, db=possible)
    except HTTPException as exc:
        raise AssertionError(f"Possible-rated link refused: {exc.detail}")
    except Exception:
        # Past the guards the handler audits and withdraws approval against
        # tables this fake does not serve; the link itself was accepted.
        pass
    links = [c.args[0] for c in possible.add.call_args_list if c.args[0].__class__.__name__ == "CustomRiskControlMapping"]
    assert len(links) == 1
    assert links[0].risk_code == "R-XX-2"                       # normalised, not the raw path value


@pytest.mark.asyncio
async def test_unknown_risk_is_404():
    db = _db({"custom_risk_definitions": _result([]), "scf_catalog_risks": _result([])})
    with pytest.raises(HTTPException) as exc:
        await custom_api.add_custom_risk_control(
            org_id=ORG, risk_code="R-XX-404", data=CustomRiskControlMappingCreate(scf_id="GOV-A1"),
            request=MagicMock(), membership=MEMBERSHIP, db=db)
    assert exc.value.status_code == 404
