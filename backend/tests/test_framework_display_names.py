"""Framework display names fall back to the live catalogue registry (UIP-022).

``frameworks.json`` is seeded once; after the 2026.3 upgrade renamed column
headers (``americas_bahamas`` -> ``americas_bahamas_dpa_2003``) the old file no
longer named them, and the listing fell back to the title-cased id
("Americas Bahamas Dpa 2003") in both the engagement drawer and scoping.
"""
import pytest

from api import catalog
from services import framework_registry
from services.framework_registry import LiveRegistryStatus


def test_live_name_replaces_title_case_but_not_curated_json(monkeypatch):
    monkeypatch.setattr(catalog, "FRAMEWORK_DISPLAY_NAMES", {"iso_27001_2022": "ISO 27001 (2022)"})
    live = {
        "americas_bahamas_dpa_2003": "Bahamas - Data Protection Act (DPA) (2003)",
        "iso_27001_2022": "ISO/IEC 27001:2022 - Information security ... - Requirements",
    }

    # Not in the curated file: the registry name, not "Americas Bahamas Dpa 2003".
    assert catalog.format_framework_name("americas_bahamas_dpa_2003", live) == (
        "Bahamas - Data Protection Act (DPA) (2003)"
    )
    # Curated short names keep precedence over the publisher's full title.
    assert catalog.format_framework_name("iso_27001_2022", live) == "ISO 27001 (2022)"


def test_falls_back_without_live_names(monkeypatch):
    monkeypatch.setattr(catalog, "FRAMEWORK_DISPLAY_NAMES", {"iso_27001_2022": "ISO 27001 (json)"})

    assert catalog.format_framework_name("iso_27001_2022") == "ISO 27001 (json)"
    assert catalog.format_framework_name("brand_new_standard_2_1", {}) == "Brand New Standard 2.1"


@pytest.mark.parametrize(
    "framework_id, expected",
    [
        ("emea_uk_cyber_essentials_3_3", "UK Cyber Essentials 3.3"),
        ("usa_federal_sox_2002", "SOX 2002"),
        ("usa_federal_fedramp_r5_moderate", "FedRAMP R5 Moderate"),
        ("usa_federal_nrc_10_cfr_73_54_2015", "NRC 10 CFR 73.54 2015"),
        ("usa_colorado_colorado_privacy_act_2021", "Colorado Privacy Act 2021"),
        ("apac_hong_kong_pdo_2022", "Hong Kong PDO 2022"),
        ("americas_canada_pipeda_2000", "Canada PIPEDA 2000"),
        ("usa", "USA"),
    ],
)
def test_unnamed_ids_are_humanised(monkeypatch, framework_id, expected):
    monkeypatch.setattr(catalog, "FRAMEWORK_DISPLAY_NAMES", {})
    assert catalog.format_framework_name(framework_id, {}) == expected


@pytest.mark.asyncio
async def test_live_framework_names_maps_registry_entries(monkeypatch):
    async def fake_read(_session):
        return LiveRegistryStatus(
            catalog_version="2026.3",
            registry={
                "a_fw": {"name": "A Framework", "focal_document_id": "a"},
                "no_name": {"focal_document_id": "b"},
                "bad": "not-a-dict",
            },
        )

    monkeypatch.setattr(framework_registry, "read_live_framework_registry", fake_read)

    assert await catalog.live_framework_names(object()) == {"a_fw": "A Framework"}


@pytest.mark.asyncio
async def test_live_framework_names_never_raises(monkeypatch):
    async def boom(_session):
        raise RuntimeError("db down")

    monkeypatch.setattr(framework_registry, "read_live_framework_registry", boom)

    assert await catalog.live_framework_names(object()) == {}
