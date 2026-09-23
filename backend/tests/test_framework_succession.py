"""Framework succession: ingestion purity, the matcher, the diff, the gate.

The behaviour under test is a heuristic sitting next to a declared crosswalk,
so the cases that matter are the ones where a matcher would be confidently
wrong: programme tiers that look like versions, jurisdictions that look alike,
and pseudo-frameworks that were never frameworks at all.
"""
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
# The extractor lives at <repo>/scripts in a checkout and at /app/scripts in
# the backend image, so resolving only one of the two makes this module fail to
# collect under `docker compose exec backend pytest`. Listed LOWEST priority
# first: insert(0) reverses the order, and the image's baked copy must never
# shadow the tree under test - that is how a bind-mounted worktree silently
# tests the image's older extractor instead of its own.
for extra in ("/app/scripts", str(BACKEND.parent / "scripts"), str(BACKEND)):
    if extra not in sys.path and Path(extra).is_dir():
        sys.path.insert(0, extra)

from extract_scf_data import (  # noqa: E402
    non_framework_id_reason,
    non_framework_reason,
    normalize_framework_id,
    partition_framework_columns,
)
from schemas_catalog_upgrade import CatalogEntityType  # noqa: E402
from services import catalog_diff as cd  # noqa: E402
from services.framework_succession import (  # noqa: E402
    CONTROL_OVERLAP_MIN_SET,
    TIER_DECLARED,
    TIER_DECLARED_STEM,
    explained_removals,
    focal_document_stem,
    match_framework_successions,
    parse_framework_id,
)


# --------------------------------------------------------------- ingestion --
@pytest.mark.parametrize(
    "column,reason",
    [
        ("Risk Threat Summary", "risk_threat_summary"),
        ("Control Threat Summary", "risk_threat_summary"),
        ("Risk R-AC-1", "risk_likelihood"),
        ("Threat MT-1", "threat_likelihood"),
        ("Threat NT-3", "threat_likelihood"),
        ("Errata 2026.2", "errata"),
    ],
)
def test_non_framework_columns_are_recognised(column, reason):
    assert non_framework_reason(column) == reason


@pytest.mark.parametrize(
    "column",
    [
        "NIST\n800-53 rev5",
        "US CA CCPA 2025",
        "ISO\n27002:2022",
        # Contains the word 'risk' but is a genuine focal document.
        "EMEA EU\nRisk Management Framework",
    ],
)
def test_real_frameworks_survive_the_partition(column):
    assert non_framework_reason(column) is None
    kept, excluded = partition_framework_columns([column])
    assert kept == [column] and excluded == {}


def test_focal_document_sheet_vetoes_the_pattern():
    """The workbook's own declaration beats our pattern, both ways."""
    column = "Risk R-AC-1"
    kept, excluded = partition_framework_columns([column])
    assert kept == [] and excluded == {column: "risk_likelihood"}

    kept, excluded = partition_framework_columns([column], {column})
    assert kept == [column] and excluded == {}


@pytest.mark.parametrize(
    "fw_id,reason",
    [
        ("risk_r_ac_1", "risk_likelihood"),
        ("threat_mt_1", "threat_likelihood"),
        ("threat_nt_3", "threat_likelihood"),
        ("control_threat_summary", "risk_threat_summary"),
        ("errata_2026_2", "errata"),
        # The requirement-tier ids, exactly as normalize_framework_id spells
        # them on every release 2025.4-2026.3. A pre-partition platform holds
        # these three in framework_mappings, so without them the first upgrade
        # after the fix reports three phantom framework retirements.
        ("identify_discretionary_security_requirements_dsr", "requirement_tier"),
        ("identify_minimum_compliance_requirements_mcr", "requirement_tier"),
        ("minimum_security_requirements_mcr_dsr", "requirement_tier"),
        ("nist_800_53_r5", None),
        ("us_ca_ccpa_2025", None),
        # Near-misses that must NOT be swallowed: a focal document may open
        # with the same word without being a requirement tier.
        ("identify_theft_red_flags_rule", None),
        ("minimum_security_standards_texas", None),
    ],
)
def test_non_framework_ids_are_recognised(fw_id, reason):
    assert non_framework_id_reason(fw_id) == reason


def test_id_patterns_mirror_the_column_patterns():
    """Every excluded COLUMN's normalised id must also be excluded.

    The two lists exist because a diff sees columns on the workbook side and
    ids on the live side. If they drift, an id a pre-fix platform holds stops
    being recognised as a correction and is reported as a framework the
    publisher retired - the exact phantom this partition removes.
    """
    columns = [
        "Risk R-AC-1", "Threat MT-1", "Threat NT-3",
        "Risk Threat Summary", "Control Threat Summary", "Errata 2026.3",
        "Identify Minimum Compliance Requirements (MCR)",
        "Identify Discretionary Security Requirements (DSR)",
        "Minimum Security Requirements MCR + DSR",
    ]
    _, excluded = partition_framework_columns(columns)
    assert set(excluded) == set(columns), "a column family stopped being excluded"
    for column, reason in excluded.items():
        fw_id = normalize_framework_id(column)
        assert non_framework_id_reason(fw_id) == reason, (
            f"column {column!r} excluded as {reason!r} but its id {fw_id!r} "
            f"is not recognised on the live side"
        )


# ---------------------------------------------------------------- stemming --
@pytest.mark.parametrize(
    "a,b",
    [
        ("apac-aus-ism-2026-march", "apac-aus-ism-2026-june"),
        ("emea-deu-c5-2020", "emea-deu-c5-2026"),
        ("general-scf-dpmp-2025", "general-scf-dpmp-2026"),
        ("general-sparta", "general-sparta-4-0"),
        ("usa-federal-sro-finra", "usa-federal-sro-finra-2007"),
        ("usa-federal-law-ftc-act", "usa-federal-law-ftc-act-1938"),
    ],
)
def test_focal_document_stem_collapses_editions(a, b):
    assert focal_document_stem(a) == focal_document_stem(b)


@pytest.mark.parametrize(
    "a,b",
    [
        # Programme tiers, not versions. Collapsing these is how a matcher
        # migrates a tenant from CMMC level 1 to level 2.
        ("usa-federal-dow-cmmc-2-level-1", "usa-federal-dow-cmmc-2-level-2"),
        ("usa-state-tx-ramp-level-1", "usa-state-tx-ramp-level-2"),
    ],
)
def test_focal_document_stem_keeps_ordinal_discriminators(a, b):
    assert focal_document_stem(a) != focal_document_stem(b)


def test_focal_document_stem_tolerates_nothing():
    assert focal_document_stem(None) is None
    assert focal_document_stem("") is None


@pytest.mark.parametrize(
    "fw_id,jurisdiction,stem_tail",
    [
        ("us_ca_ccpa_2025", "california", "ccpa"),
        ("usa_california_ccpa_2025", "california", "ccpa"),
        ("us_ny_shield_act", "new_york", "shield_act"),
        ("us_c2m2_2_1", "federal", "c2m2_2_1"),
    ],
)
def test_jurisdiction_canonicalisation(fw_id, jurisdiction, stem_tail):
    identity = parse_framework_id(fw_id)
    assert identity.jurisdiction == jurisdiction
    assert identity.stem.endswith(stem_tail)


# ----------------------------------------------------------------- matcher --
def test_declared_focal_document_id_outranks_every_derived_signal():
    removed = {"us_ca_ccpa_2025": "US - California CCPA (2025)"}
    added = {
        "usa_california_ccpa_2025": "USA California CCPA 2025",
        # A decoy whose display name is a far better string match.
        "usa_california_ccpa_2025_amended": "US - California CCPA (2025)",
    }
    fdi = {
        "us_ca_ccpa_2025": "usa-state-ca-ccpa-cpra-2026",
        "usa_california_ccpa_2025": "usa-state-ca-ccpa-cpra-2026",
        "usa_california_ccpa_2025_amended": "usa-state-ca-ccpa-2027",
    }
    p = match_framework_successions(removed, added, focal_document_ids=fdi)[
        "us_ca_ccpa_2025"
    ]
    assert p.bound_successor == "usa_california_ccpa_2025"
    assert p.best.tier == TIER_DECLARED
    assert "focal_document_id" in p.best.signals


def test_declared_stem_covers_an_edition_bump():
    removed = {"apac_australia_ism_march_2026": "Australia ISM (March 2026)"}
    added = {"apac_australia_ism_june_2026": "Australia ISM (June 2026)"}
    fdi = {
        "apac_australia_ism_march_2026": "apac-aus-ism-2026-march",
        "apac_australia_ism_june_2026": "apac-aus-ism-2026-june",
    }
    p = match_framework_successions(removed, added, focal_document_ids=fdi)[
        "apac_australia_ism_march_2026"
    ]
    assert p.bound_successor == "apac_australia_ism_june_2026"
    assert p.best.tier == TIER_DECLARED_STEM


def test_two_ids_claiming_one_declared_identity_is_ambiguous_not_a_winner():
    removed = {"americas_canada_csag": "OSFI Self-Assessment Guidance"}
    added = {
        "americas_canada_osfi_a": "OSFI Self-Assessment Guidance",
        "americas_canada_osfi_b": "OSFI Self-Assessment Guidance",
    }
    fdi = dict.fromkeys(
        ["americas_canada_csag", "americas_canada_osfi_a", "americas_canada_osfi_b"],
        "americas-can-osfi-self-assessment",
    )
    p = match_framework_successions(removed, added, focal_document_ids=fdi)[
        "americas_canada_csag"
    ]
    assert p.bound_successor is None, "an unresolved merge must not auto-bind"
    assert all(c.ambiguous for c in p.candidates)


def test_programme_tiers_do_not_cross_match():
    removed = {
        "us_cmmc_2_0_level_1": "CMMC 2.0 Level 1",
        "us_cmmc_2_0_level_2": "CMMC 2.0 Level 2",
    }
    added = {
        "usa_federal_cmmc_2_0_level_1": "CMMC 2.0 Level 1",
        "usa_federal_cmmc_2_0_level_2": "CMMC 2.0 Level 2",
    }
    proposals = match_framework_successions(removed, added)
    assert proposals["us_cmmc_2_0_level_1"].bound_successor == (
        "usa_federal_cmmc_2_0_level_1"
    )
    assert proposals["us_cmmc_2_0_level_2"].bound_successor == (
        "usa_federal_cmmc_2_0_level_2"
    )


def test_different_states_never_pair_on_name_alone():
    removed = {"us_ak_pipa": "Personal Information Protection Act"}
    added = {"usa_illinois_pipa_2006": "Personal Information Protection Act"}
    p = match_framework_successions(removed, added)["us_ak_pipa"]
    assert p.bound_successor is None


def test_a_removal_with_no_plausible_successor_stays_unexplained():
    removed = {"emea_russia": "Russia - Federal Law 149-FZ"}
    added = {"nist_800_53_r5_2": "NIST 800-53 rev5.2"}
    proposals = match_framework_successions(removed, added)
    explained, unexplained = explained_removals(proposals)
    assert explained == [] and unexplained == ["emea_russia"]


def test_a_surviving_id_is_an_eligible_successor():
    """2026.2 carried one document under two ids; 2026.3 dropped the duplicate."""
    removed = {"americas_canada_csag": "OSFI Self-Assessment Guidance"}
    retained = {
        "americas_canada_osfi_self_assessment_guidance": (
            "OSFI Self-Assessment Guidance"
        )
    }
    p = match_framework_successions(removed, {}, retained)["americas_canada_csag"]
    assert p.bound_successor == "americas_canada_osfi_self_assessment_guidance"
    assert "successor_retained" in p.best.signals


# -------------------------------------------------------------------- diff --
def _live(key, name, fdi=None, status="active"):
    return cd.LiveEntityRow(
        key=key,
        status=status,
        fields={"focal_document_id": fdi},
        name=name,
    )


def _extracted(names, registry=None, retired_fdis=()):
    """A minimal workbook. ``retired_fdis`` writes the publisher's own
    ``STRM Errata`` "removed in <version>" declarations into it."""
    return cd.ExtractedCatalog(
        catalog_version="2026.3",
        controls=[{"scf_id": "GOV-01", "control_name": "Governance"}],
        domains=[{"identifier": "GOV"}],
        evidence={"E-GOV-01": {}},
        assessment_objectives=[{"ao_id": "GOV-01-A"}],
        framework_names=names,
        framework_registry=registry or {},
        publisher_changes=(
            {
                "summary": None,
                "frameworks": {
                    "added": [],
                    "removed": [{"fdi": fdi, "name": None} for fdi in retired_fdis],
                    "mapping_errata": [],
                },
                "controls": {"counts": {}, "merged": [], "tags": {}},
            }
            if retired_fdis
            else {}
        ),
    )


def test_frameworks_diff_classifies_every_change_class():
    live = cd.LiveCatalog()
    live.frameworks = {
        "kept_same": _live("kept_same", "Kept"),
        "kept_renamed": _live("kept_renamed", "Old Display Name"),
        "gone": _live("gone", "Retired Thing"),
        "was_deprecated": _live("was_deprecated", "Back Again", status="deprecated"),
    }
    extracted = _extracted(
        {
            "kept_same": "Kept",
            "kept_renamed": "New Display Name",
            "brand_new": "Brand New",
            "was_deprecated": "Back Again",
        }
    )
    diff = cd.compute_frameworks_diff(extracted, live)

    assert [a.key for a in diff.added] == ["brand_new"]
    assert [c.key for c in diff.changed] == ["kept_renamed"]
    assert diff.changed[0].fields["display_name"].old == "Old Display Name"
    assert [d.key for d in diff.deprecated] == ["gone"]
    assert [r.key for r in diff.resurrected] == ["was_deprecated"]
    assert diff.unchanged == ["kept_same"]


def test_frameworks_diff_attributes_a_declared_successor():
    live = cd.LiveCatalog()
    live.frameworks = {
        "us_ca_sb327": _live("us_ca_sb327", "CA SB-327", "usa-state-ca-sb327-2018")
    }
    extracted = _extracted(
        {"usa_california_sb327_2018": "USA California SB327 2018"},
        {
            "usa_california_sb327_2018": {
                "focal_document_id": "usa-state-ca-sb327-2018"
            }
        },
    )
    dep = cd.compute_frameworks_diff(extracted, live).deprecated[0]
    assert dep.superseded_by == "usa_california_sb327_2018"
    assert dep.superseded_source == cd.SUCCESSION_SOURCE_FOCAL_DOCUMENT
    assert dep.suggestions[0].score == pytest.approx(1.0)


def test_frameworks_diff_leaves_an_unmatched_removal_unbound():
    live = cd.LiveCatalog()
    live.frameworks = {"emea_russia": _live("emea_russia", "Russia 149-FZ")}
    extracted = _extracted({"nist_800_53_r5_2": "NIST 800-53 rev5.2"})
    dep = cd.compute_frameworks_diff(extracted, live).deprecated[0]
    assert dep.superseded_by is None
    assert dep.superseded_source is None


def test_frameworks_entity_reaches_the_summary():
    live = cd.LiveCatalog()
    live.frameworks = {"gone": _live("gone", "Gone")}
    detail = cd.compute_catalog_diff(
        _extracted({"brand_new": "Brand New"}), live, from_version="2026.2"
    )
    summary = cd.summarize_diff(detail).model_dump(mode="json")
    assert summary["entities"]["frameworks"]["added"] == 1
    assert summary["entities"]["frameworks"]["deprecated"] == 1
    # FRAMEWORK_MAPPINGS keeps its own, distinct meaning.
    assert CatalogEntityType.FRAMEWORK_MAPPINGS in detail.entities
    assert CatalogEntityType.FRAMEWORKS in detail.entities


def test_framework_diff_round_trips_through_json():
    live = cd.LiveCatalog()
    live.frameworks = {
        "us_ca_sb327": _live("us_ca_sb327", "CA SB-327", "usa-state-ca-sb327-2018")
    }
    extracted = _extracted(
        {"usa_california_sb327_2018": "USA California SB327 2018"},
        {
            "usa_california_sb327_2018": {
                "focal_document_id": "usa-state-ca-sb327-2018"
            }
        },
    )
    detail = cd.compute_catalog_diff(extracted, live, from_version="2026.2")
    blob = detail.model_dump(mode="json")
    revived = type(detail).model_validate(blob)
    dep = revived.entities[CatalogEntityType.FRAMEWORKS].deprecated[0]
    assert dep.superseded_by == "usa_california_sb327_2018"
    assert dep.suggestions[0].signals == ["focal_document_id"]


# -------------------------------------------------------------------- gate --
def _report(
    live_frameworks,
    workbook_names,
    registry=None,
    retired_fdis=(),
    live_registry=None,
):
    live = cd.LiveCatalog()
    live.controls["GOV-01"] = cd.LiveEntityRow(
        key="GOV-01", status="active", fields={}, name="Governance"
    )
    live.frameworks = live_frameworks
    return cd.run_sanity_checks(
        _extracted(workbook_names, registry, retired_fdis),
        live,
        live_registry=live_registry,
    )


def _sanity(live_frameworks, workbook_names, registry=None, retired_fdis=()):
    report = _report(live_frameworks, workbook_names, registry, retired_fdis)
    return next(c for c in report.checks if c.check == "framework_churn")


def test_framework_churn_blocks_on_unexplained_removals():
    live = {f"gone_{i}": _live(f"gone_{i}", f"Gone {i}") for i in range(40)}
    live["kept"] = _live("kept", "Kept")
    check = _sanity(live, {"kept": "Kept"})
    assert check.passed is False
    assert "unexplained" in check.detail
    # The threshold and the floor are stated, not implied.
    assert "5%" in check.detail and "10-row floor" in check.detail


def test_framework_churn_passes_when_the_workbook_explains_the_removals():
    live = {
        "us_ca_sb327": _live("us_ca_sb327", "CA SB-327", "usa-state-ca-sb327-2018"),
        "us_ca_sb1386": _live("us_ca_sb1386", "CA SB-1386", "usa-state-ca-sb1386-2002"),
    }
    names = {
        "usa_california_sb327_2018": "USA California SB327 2018",
        "usa_california_sb1386_2002": "USA California SB1386 2002",
    }
    registry = {
        "usa_california_sb327_2018": {"focal_document_id": "usa-state-ca-sb327-2018"},
        "usa_california_sb1386_2002": {
            "focal_document_id": "usa-state-ca-sb1386-2002"
        },
    }
    check = _sanity(live, names, registry)
    assert check.passed is True
    assert "0 unexplained" in check.detail


def test_framework_churn_tolerates_a_handful_under_the_floor():
    live = {f"gone_{i}": _live(f"gone_{i}", f"Gone {i}") for i in range(3)}
    live.update({f"kept_{i}": _live(f"kept_{i}", f"Kept {i}") for i in range(20)})
    check = _sanity(live, {f"kept_{i}": f"Kept {i}" for i in range(20)})
    assert check.passed is True


def test_count_gate_is_retained_alongside_the_churn_gate():
    live = cd.LiveCatalog()
    report = cd.run_sanity_checks(_extracted({}), live)
    names = {c.check for c in report.checks}
    assert {"framework_names", "framework_churn"} <= names
    assert next(c for c in report.checks if c.check == "framework_names").passed is False


# ------------------------------------------------------- control overlap ----
def _controls(n, offset=0):
    return {f"GOV-{i + offset:03d}" for i in range(n)}


def test_control_overlap_vetoes_a_derived_match_on_a_different_document():
    """CMMC Level 1 and its Assessment Objectives sheet are not successors.

    The ids differ by one token and the display names by two words, so both
    string signals endorse the pairing. The control sets do not: measured on
    2026.2 they overlap 0.28.
    """
    removed = {"us_cmmc_2_0_level_1": "CMMC 2.0 Level 1"}
    added = {
        "usa_federal_cmmc_2_0_level_1_aos": "CMMC 2.0 Level 1 Assessment Objectives"
    }
    without = match_framework_successions(removed, added)["us_cmmc_2_0_level_1"]
    assert without.bound_successor == "usa_federal_cmmc_2_0_level_1_aos", (
        "precondition: the string signals alone do propose this pairing"
    )

    sets = {
        "us_cmmc_2_0_level_1": _controls(52),
        "usa_federal_cmmc_2_0_level_1_aos": _controls(16, offset=100),
    }
    p = match_framework_successions(removed, added, control_sets=sets)[
        "us_cmmc_2_0_level_1"
    ]
    assert p.bound_successor is None
    assert p.candidates[0].vetoed is True
    assert "vetoed_by_control_overlap" in p.candidates[0].signals
    # Withheld, not hidden: the reviewer still sees the candidate and why.
    assert p.candidates[0].control_overlap == pytest.approx(0.0)


def test_control_overlap_never_overrules_the_publisher():
    """A declared pairing is reported with its low overlap, not vetoed."""
    removed = {"old_fw": "Old"}
    added = {"new_fw": "New"}
    fdi = {"old_fw": "same-document", "new_fw": "same-document"}
    sets = {"old_fw": _controls(40), "new_fw": _controls(40, offset=500)}
    p = match_framework_successions(
        removed, added, focal_document_ids=fdi, control_sets=sets
    )["old_fw"]
    assert p.bound_successor == "new_fw"
    assert p.best.vetoed is False
    assert "control_overlap_low" in p.best.signals


def test_control_overlap_corroborates_without_promoting():
    """Overlap decorates a candidate; it never creates one.

    FAR 52.204-21 and CMMC Level 1 cover the same seventeen controls in the
    real catalogue. If overlap could promote, they would merge.
    """
    removed = {"us_far_52_204_21": "FAR 52.204-21"}
    added = {"usa_federal_cmmc_2_0_level_1": "CMMC 2.0 Level 1"}
    shared = _controls(17)
    p = match_framework_successions(
        removed, added, control_sets={
            "us_far_52_204_21": shared,
            "usa_federal_cmmc_2_0_level_1": shared,
        }
    )["us_far_52_204_21"]
    assert p.bound_successor is None, "identical control sets must not pair them"


def test_control_overlap_ignores_sets_too_small_to_mean_anything():
    removed = {"us_cmmc_2_0_level_1": "CMMC 2.0 Level 1"}
    added = {"usa_federal_cmmc_2_0_level_1_aos": "CMMC 2.0 Level 1 AOs"}
    tiny = {
        "us_cmmc_2_0_level_1": _controls(CONTROL_OVERLAP_MIN_SET - 1),
        "usa_federal_cmmc_2_0_level_1_aos": _controls(
            CONTROL_OVERLAP_MIN_SET - 1, offset=900
        ),
    }
    p = match_framework_successions(removed, added, control_sets=tiny)[
        "us_cmmc_2_0_level_1"
    ]
    assert p.candidates[0].control_overlap is None
    assert p.candidates[0].vetoed is False


def test_framework_churn_counts_only_declared_explanations():
    """A derived match must not be able to unblock a release.

    Otherwise the cheapest way past the gate is to loosen the matcher, and the
    error it gets loosened into is the one that silently rebinds tenant scope.
    """
    live = {
        f"old_fw_{i}": _live(f"old_fw_{i}", f"Framework {i}") for i in range(20)
    }
    names = {f"old_fw_{i}_2026": f"Framework {i} 2026" for i in range(20)}
    check = _sanity(live, names)
    proposals = match_framework_successions(
        {k: v.name for k, v in live.items()}, names
    )
    bound = [k for k, p in proposals.items() if p.bound_successor]
    assert bound, "precondition: the derived matcher does pair these"
    assert check.passed is False
    # The detail must attribute every removal, and a derived pairing attributes
    # to nothing: no rename, no new edition, no publisher retirement.
    assert "20 live frameworks absent from the workbook" in check.detail
    assert "0 renamed (same focal document)" in check.detail
    assert "0 superseded by a new edition" in check.detail
    assert "0 retired by the publisher" in check.detail
    assert "20 unexplained" in check.detail


# ------------------------------------------------- review gate on derive ----
def test_derived_framework_pairing_defaults_to_retain_not_migrate():
    """The brief's hard constraint: a heuristic never rebinds by default.

    A declared pairing is SCF's own assertion and migrates like a control does.
    A derived one is our guess, so applying without editing must leave the
    tenant's scope where it is.
    """
    from schemas_catalog_upgrade import PlannedActionType
    from services.catalog_diff import (
        SUCCESSION_SOURCE_DERIVED,
        SUCCESSION_SOURCE_FOCAL_DOCUMENT,
    )
    from services.reconciliation_service import _default_framework_action

    assert _default_framework_action(
        "usa_federal_ism_june_2026", SUCCESSION_SOURCE_FOCAL_DOCUMENT
    ) is PlannedActionType.MIGRATE
    assert _default_framework_action(
        "usa_federal_ism_june_2026", SUCCESSION_SOURCE_DERIVED
    ) is PlannedActionType.RETAIN
    # No successor at all, and an unattributed one, both stay put.
    assert _default_framework_action(None, SUCCESSION_SOURCE_FOCAL_DOCUMENT) \
        is PlannedActionType.RETAIN
    assert _default_framework_action("something", None) \
        is PlannedActionType.RETAIN


def test_row_floor_is_inert_on_a_full_catalogue():
    """The floor must never be the clause that passes a full-catalogue run.

    control_churn's 50-row floor against ~1,200 controls can forgive 4.2%.
    This floor is 10 against ~250 frameworks: at most 9 removals = 3.6%, which
    the 5% ratio passes on its own. So every full-catalogue verdict here is the
    ratio's, and no operator can walk a large unexplained churn past the check
    by leaning on the floor. Raising MIN_ROWS would break that property.
    """
    from services.catalog_diff import (
        FRAMEWORK_CHURN_MIN_ROWS,
        FRAMEWORK_CHURN_UNEXPLAINED_THRESHOLD,
    )

    population = 250
    live = {f"fw_{i}": _live(f"fw_{i}", f"Framework {i}")
            for i in range(population)}
    # One short of the floor: the largest churn the floor could ever forgive.
    names = {k: v.name
             for k, v in list(live.items())[FRAMEWORK_CHURN_MIN_ROWS - 1:]}
    check = _sanity(live, names)
    ratio = (FRAMEWORK_CHURN_MIN_ROWS - 1) / population
    assert ratio <= FRAMEWORK_CHURN_UNEXPLAINED_THRESHOLD, (
        "the floor can only ever forgive a churn the ratio already passes"
    )
    assert check.passed is True

    # And one row more - now past the floor - still turns on the ratio, not
    # the floor: 10 of 250 is 4.0%, under threshold, so it passes.
    names2 = {k: v.name
              for k, v in list(live.items())[FRAMEWORK_CHURN_MIN_ROWS:]}
    assert _sanity(live, names2).passed is True
    # 20 of 250 is 8.0% - over the ratio, over the floor - and blocks.
    names3 = {k: v.name for k, v in list(live.items())[20:]}
    assert _sanity(live, names3).passed is False


# ------------------------------------------------- live registry provenance --
def _live_controls(framework_ids):
    """One control row mapping every supplied framework id — the live membership."""
    return {
        "GOV-01": cd.LiveEntityRow(
            key="GOV-01",
            status="active",
            fields={"framework_mappings": {fw: ["GOV-01"] for fw in framework_ids}},
            name="Governance",
        )
    }


def test_derive_live_frameworks_prefers_db_registry_over_file(tmp_path, monkeypatch):
    """The stored row wins; the file is only consulted when there is no row.

    The file is a frontend cache on a mounted volume with no transactional
    relationship to the session, so a stale or foreign DATA_DIR must never be
    able to overrule the record.
    """
    import catalog_seeder

    (tmp_path / "framework_registry.json").write_text(
        '{"nist_800_53_r5": {"name": "STALE FILE NAME", '
        '"focal_document_id": "stale-file-identifier"}}'
    )
    monkeypatch.setattr(catalog_seeder, "DATA_DIR", tmp_path)

    controls = _live_controls(["nist_800_53_r5"])

    from_file = cd.derive_live_frameworks(controls)
    assert from_file["nist_800_53_r5"].fields["focal_document_id"] == (
        "stale-file-identifier"
    )
    assert from_file["nist_800_53_r5"].name == "STALE FILE NAME"

    from_db = cd.derive_live_frameworks(
        controls,
        {
            "nist_800_53_r5": {
                "name": "NIST 800-53 rev5",
                "focal_document_id": "usa-federal-nist-800-53-r5",
                "geography": "USA",
            }
        },
    )
    assert from_db["nist_800_53_r5"].fields["focal_document_id"] == (
        "usa-federal-nist-800-53-r5"
    )
    assert from_db["nist_800_53_r5"].name == "NIST 800-53 rev5"

    # An empty registry is not an answer — it falls back to the file rather than
    # reporting every framework as having no focal-document identifier.
    assert cd.derive_live_frameworks(controls, {}) == from_file


def test_framework_churn_passes_when_live_fdi_comes_from_db_registry():
    """The defect this table exists to fix, end to end at the gate.

    20 live frameworks renamed by the publisher, every pairing declared by a
    shared focal-document identifier. With the live identifiers coming from the
    stored registry the churn gate passes; strip them (the state of any install
    with no registry row) and the same transition is blocked.
    """
    live_ids = [f"old_fw_{i}" for i in range(20)]
    fdis = {fw: f"publisher-doc-{i}" for i, fw in enumerate(live_ids)}
    registry_rows = {fw: {"name": f"Framework {i}", "focal_document_id": fdis[fw]}
                     for i, fw in enumerate(live_ids)}

    live_frameworks = cd.derive_live_frameworks(
        _live_controls(live_ids), registry_rows
    )
    assert all(
        row.fields["focal_document_id"] for row in live_frameworks.values()
    ), "the registry path must supply every live focal-document identifier"

    workbook_names = {f"new_fw_{i}": f"Framework {i}" for i in range(20)}
    workbook_registry = {
        f"new_fw_{i}": {"focal_document_id": f"publisher-doc-{i}"} for i in range(20)
    }

    check = _sanity(live_frameworks, workbook_names, workbook_registry)
    assert check.passed is True
    assert "20 renamed (same focal document)" in check.detail
    assert "0 unexplained" in check.detail

    # Same transition with no stored registry: 20 unexplained removals, blocked.
    without = cd.derive_live_frameworks(_live_controls(live_ids), None)
    for row in without.values():
        row.fields["focal_document_id"] = None
    blocked = _sanity(without, workbook_names, workbook_registry)
    assert blocked.passed is False
    assert "unexplained" in blocked.detail


# ------------------------------------ publisher-declared retirement (2026.3) --
def test_a_publisher_declared_retirement_explains_a_successorless_removal():
    """The third kind of declaration, new in SCF 2026.3.

    A renamed framework has a successor and a new edition has a successor. A
    RETIRED one has none — the publisher deleted the document. Before the STRM
    Errata sheet existed there was no way to say so, and every deliberate
    retirement counted against the gate as if the workbook had lost it.

    This is still a declaration: it is read off the publisher's own sheet and
    matched on the live framework's focal-document identifier. It is not the
    matcher being allowed to vote.
    """
    live = {
        f"gone_{i}": _live(f"gone_{i}", f"Gone {i}", f"publisher-doc-{i}")
        for i in range(20)
    }
    live["kept"] = _live("kept", "Kept", "publisher-doc-kept")

    blocked = _sanity(live, {"kept": "Kept"})
    assert blocked.passed is False
    assert "20 unexplained" in blocked.detail
    assert "0 retired by the publisher" in blocked.detail

    explained = _sanity(
        live,
        {"kept": "Kept"},
        retired_fdis=[f"publisher-doc-{i}" for i in range(20)],
    )
    assert explained.passed is True
    assert "20 retired by the publisher" in explained.detail
    assert "0 unexplained" in explained.detail


def test_a_publisher_retirement_is_matched_on_the_identifier_not_the_key():
    """The declaration names a focal document, not our column slug.

    Matching on the framework key would silently stop working the moment the
    publisher renamed the column, which is the exact event this whole mechanism
    exists to survive.
    """
    live = {
        f"gone_{i}": _live(f"gone_{i}", f"Gone {i}", f"publisher-doc-{i}")
        for i in range(20)
    }
    # The publisher retires documents whose identifiers nothing live carries.
    check = _sanity(
        live,
        {"kept": "Kept"},
        retired_fdis=["some-other-doc-1", "some-other-doc-2"],
    )
    assert check.passed is False
    assert "0 retired by the publisher" in check.detail
    assert "20 unexplained" in check.detail


def test_a_live_framework_with_no_identifier_cannot_be_publisher_retired():
    """No identifier, no declaration — the pre-registry install's state.

    This is the failure production hit, in miniature: with no live identifiers
    there is nothing for the publisher's declaration to match against, so the
    removals stay unexplained and the gate blocks. It must NOT fall back to
    matching by name.
    """
    live = {f"gone_{i}": _live(f"gone_{i}", f"Gone {i}") for i in range(20)}
    check = _sanity(
        live, {"kept": "Kept"}, retired_fdis=[f"publisher-doc-{i}" for i in range(20)]
    )
    assert check.passed is False
    assert "0 retired by the publisher" in check.detail


def test_a_derived_match_still_explains_nothing_beside_a_publisher_retirement():
    """The anti-test for the new tier.

    Adding a third way to explain a removal must not have widened the first
    two. A derived pairing was never an explanation and still is not, even in a
    release that ships the errata sheet.
    """
    live = {
        f"old_fw_{i}": _live(f"old_fw_{i}", f"Framework {i}", f"publisher-doc-{i}")
        for i in range(20)
    }
    # Names the derived matcher will happily pair, identifiers it cannot see.
    names = {f"old_fw_{i}_2026": f"Framework {i} 2026" for i in range(20)}
    proposals = match_framework_successions(
        {k: v.name for k, v in live.items()}, names
    )
    assert [k for k, p in proposals.items() if p.bound_successor], (
        "precondition: the derived matcher does pair these"
    )

    # The workbook ships an errata sheet, but retires something else entirely.
    check = _sanity(live, names, retired_fdis=["general-unrelated-doc"])
    assert check.passed is False
    assert "0 renamed (same focal document)" in check.detail
    assert "0 retired by the publisher" in check.detail
    assert "20 unexplained" in check.detail


def test_each_removal_is_attributed_once_with_rename_taking_precedence():
    """The counts must sum to the total, so they cannot double-attribute.

    A framework can be both renamed (its identifier survives on a new column)
    and listed in the errata. The rename is the more specific fact — there IS a
    successor — so it wins, and the operator reading the line sees each removal
    in exactly one bucket.
    """
    live = {
        "renamed_one": _live("renamed_one", "Renamed One", "publisher-doc-a"),
        "retired_one": _live("retired_one", "Retired One", "publisher-doc-b"),
    }
    names = {"renamed_one_2026": "Renamed One 2026"}
    registry = {"renamed_one_2026": {"focal_document_id": "publisher-doc-a"}}
    check = _sanity(
        live, names, registry, retired_fdis=["publisher-doc-a", "publisher-doc-b"]
    )

    assert "2 live frameworks absent from the workbook" in check.detail
    assert "1 renamed (same focal document)" in check.detail
    assert "1 retired by the publisher" in check.detail
    assert "0 unexplained" in check.detail


def test_a_workbook_with_no_errata_sheet_retires_nothing():
    """Every release up to 2026.2. Absence of the sheet is not a declaration."""
    live = {f"gone_{i}": _live(f"gone_{i}", f"Gone {i}", f"doc-{i}") for i in range(20)}
    check = _sanity(live, {"kept": "Kept"})
    assert "0 retired by the publisher" in check.detail
    assert check.passed is False


# ------------------------------------------- live_framework_registry gate --
def _registry_check(report):
    return next(
        (c for c in report.checks if c.check == "live_framework_registry"), None
    )


def _live_registry(**over):
    from services.framework_registry import LiveRegistryStatus

    fields = dict(
        catalog_version="2026.2",
        registry={"a": {"focal_document_id": "doc-a"}},
        source="apply",
        entries=1,
        with_focal_document_id=1,
    )
    fields.update(over)
    return LiveRegistryStatus(**fields)


def test_no_registry_check_is_emitted_when_nobody_looked():
    """A verdict about a registry that was never read would be fabricated.

    ``run_sanity_checks`` is called directly by tests and by any non-staging
    consumer. Those callers pass no registry status, and the honest response is
    to omit the check rather than invent a pass or a fail for it.
    """
    report = _report({"kept": _live("kept", "Kept")}, {"kept": "Kept"})
    assert _registry_check(report) is None


def test_the_registry_check_sits_immediately_before_framework_churn():
    """Ordering is part of the deliverable.

    A framework_churn failure is unreadable on its own: "73 unexplained" looks
    identical whether the release dropped 73 documents or the platform had no
    identifiers to compare against. Production hit the second. The line that
    disambiguates it has to be the line above it.
    """
    report = _report(
        {"kept": _live("kept", "Kept")}, {"kept": "Kept"},
        live_registry=_live_registry(),
    )
    names = [c.check for c in report.checks]
    assert names.index("live_framework_registry") == names.index("framework_churn") - 1


def test_the_registry_check_passes_and_states_its_provenance():
    report = _report(
        {"kept": _live("kept", "Kept")}, {"kept": "Kept"},
        live_registry=_live_registry(entries=248, with_focal_document_id=248),
    )
    check = _registry_check(report)
    assert check.passed is True
    assert "registry for 2026.2" in check.detail
    assert "248 frameworks" in check.detail
    assert "248 carrying a focal-document identifier" in check.detail
    assert "source: apply" in check.detail


def test_a_recovered_registry_says_so_rather_than_naming_the_source_column():
    """"source: recovered" tells an operator nothing they can act on.

    Saying it came from the applied release's own stored workbook tells them
    where the identifiers are from and why they are trustworthy.
    """
    report = _report(
        {"kept": _live("kept", "Kept")}, {"kept": "Kept"},
        live_registry=_live_registry(
            source="recovered", recovered_from_run_id="run-123"
        ),
    )
    check = _registry_check(report)
    assert check.passed is True
    assert "recovered from the 2026.2 upgrade workbook" in check.detail


def test_a_missing_registry_fails_with_the_reason_and_the_remedy():
    """The line production needed and did not have.

    It must carry three things: that there is no registry, WHY recovery could
    not supply one, and what the operator should do next. Anything less and the
    next person hits the same wall with the same "73 unexplained".
    """
    report = _report(
        {"kept": _live("kept", "Kept")}, {"kept": "Kept"},
        live_registry=_live_registry(
            registry=None,
            source=None,
            entries=0,
            with_focal_document_id=0,
            reason="no applied upgrade run for 2026.2 still holds its workbook",
        ),
    )
    check = _registry_check(report)
    assert check.passed is False
    assert "no framework registry with focal-document identifiers" in check.detail
    assert "2026.2" in check.detail
    assert "still holds its workbook" in check.detail, "the reason must survive"
    assert "Register your current catalog workbook" in check.detail, "the remedy"


def test_a_registry_with_no_identifiers_fails_like_a_missing_one():
    """The 2026.1|seed row. A registry that cannot decide anything is not one."""
    report = _report(
        {"kept": _live("kept", "Kept")}, {"kept": "Kept"},
        live_registry=_live_registry(
            registry={"a": {"focal_document_id": None}},
            source="seed",
            entries=1,
            with_focal_document_id=0,
            reason="the stored registry for 2026.2 carries no focal-document "
                   "identifiers (source: seed)",
        ),
    )
    check = _registry_check(report)
    assert check.passed is False
    assert "source: seed" in check.detail


def test_a_failed_registry_check_fails_the_whole_report():
    """It is a gate, not a warning. Staging must stop."""
    report = _report(
        {"kept": _live("kept", "Kept")}, {"kept": "Kept"},
        live_registry=_live_registry(
            registry=None, entries=0, with_focal_document_id=0, reason="none stored"
        ),
    )
    assert report.passed is False
    assert [c.check for c in report.checks if not c.passed] == [
        "live_framework_registry"
    ]
