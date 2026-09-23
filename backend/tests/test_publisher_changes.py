"""The publisher's own change declarations, read out of the 2026.3+ sheets.

Three sheets are new in SCF 2026.3 and none of them existed before:

* ``STRM Errata``     — one row per focal document, saying whether the release
  ADDED it, REMOVED it, or merely corrected its mappings;
* ``Change Overview`` — one row per control, with a newline-separated bullet
  list of what changed about it;
* ``READ THIS``       — the release narrative, plus the only place the workbook
  states which deprecated control was merged into which survivor.

The ``removed`` half of the first sheet is the load-bearing one. It is the
publisher declaring a retirement, which is what lets ``framework_churn`` tell a
deliberately retired document apart from one that fell out of the workbook by
accident. Everything else here is commentary, and the extractor is built so
that losing commentary can never block an upgrade.

Coverage is deliberately in two halves:

* synthetic DataFrames per sheet, which run everywhere and pin the parsing
  rules (verb matching, bullet splitting, located-not-assumed header row);
* assertions against the REAL 2026.3 and 2026.2 workbooks, which run only
  where those files are present.

The real workbooks under ``catalog-source/`` are gitignored (``.gitignore``
line 54, ``catalog-source/*.xlsx``) apart from a stale ``scf.xlsx``, so the
second half SKIPS in a fresh clone and in CI. That is a real gap, not a
silently passing test: the skip reason names the file, and the synthetic half
is written to stand on its own.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

_SCRIPTS_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "scripts",
    Path("/app/scripts"),
]
SCRIPTS_DIR = next((p for p in _SCRIPTS_CANDIDATES if p.is_dir()), None)
if SCRIPTS_DIR is None:
    raise RuntimeError(
        f"extract_scf_data.py location not found; tried {_SCRIPTS_CANDIDATES}"
    )
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import extract_scf_data as extractor  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic sheets — these run everywhere
# ---------------------------------------------------------------------------


def _errata_df(rows):
    return pd.DataFrame(
        rows,
        columns=[
            "Focal Document Identifier (FDI)",
            "Focal Document Name (FDN)",
            "Errata",
        ],
    )


def test_strm_errata_splits_the_three_publisher_verbs():
    df = _errata_df([
        ("emea-deu-c5-2020", "Germany C5 (2020)", "Removed in 2026.3"),
        ("usa-federal-cmmc-3-0", "CMMC 3.0", "Added in 2026.3"),
        ("general-iso-27002-2022", "ISO 27002:2022", "FDE #: 8.10, 8.12, 8.5"),
    ])
    out = extractor.parse_strm_errata(df)

    assert [e["fdi"] for e in out["removed"]] == ["emea-deu-c5-2020"]
    assert [e["fdi"] for e in out["added"]] == ["usa-federal-cmmc-3-0"]
    assert [e["fdi"] for e in out["mapping_errata"]] == ["general-iso-27002-2022"]
    # The retirement carries the publisher's display name, which is what an
    # operator reading "retired by the publisher" needs to recognise it.
    assert out["removed"][0]["name"] == "Germany C5 (2020)"
    # A mapping erratum keeps the note verbatim: the reference format varies
    # per document family and nothing downstream parses the individual ids.
    assert out["mapping_errata"][0]["note"] == "FDE #: 8.10, 8.12, 8.5"


def test_strm_errata_reads_the_version_from_the_workbook_not_a_constant():
    """A future release must parse with no edit to the extractor."""
    out = extractor.parse_strm_errata(_errata_df([
        ("general-sparta", "SPARTA", "Removed in 2027.1"),
        ("general-new-thing", "New Thing", "Added in 2027.1"),
    ]))
    assert [e["fdi"] for e in out["removed"]] == ["general-sparta"]
    assert [e["fdi"] for e in out["added"]] == ["general-new-thing"]


def test_strm_errata_ignores_rows_with_no_identifier_or_no_note():
    out = extractor.parse_strm_errata(_errata_df([
        ("", "Nameless", "Removed in 2026.3"),
        ("general-quiet", "Quiet", ""),
        (None, None, None),
    ]))
    assert out == {"added": [], "removed": [], "mapping_errata": []}


def test_strm_errata_without_recognised_columns_degrades_to_empty():
    """An unreadable sheet yields no declarations, never an exception.

    A declaration the parser cannot see costs a framework its explanation and
    the gate then blocks, which is the safe direction. A raise here would take
    the whole upgrade down over commentary.
    """
    df = pd.DataFrame([("x", "y")], columns=["Something", "Else"])
    assert extractor.parse_strm_errata(df) == {
        "added": [],
        "removed": [],
        "mapping_errata": [],
    }


def _overview_df(rows):
    return pd.DataFrame(rows, columns=["SCF #", "SCF Control", "Errata"])


def test_change_overview_counts_tag_occurrences_not_rows():
    """One control can carry several bullets, and each one is a change."""
    df = _overview_df([
        ("GOV-A1", "Governance Program", "- renumbered\n- wordsmithed"),
        ("GOV-A2", "Governance Metrics", "- renumbered"),
        ("GOV-A3", "New One", "- new control"),
    ])
    out = extractor.parse_change_overview(df)

    assert out["counts"]["renumbered"] == 2
    assert out["counts"]["wordsmithed"] == 1
    assert out["counts"]["new_control"] == 1
    assert out["tags"]["GOV-A1"] == ["renumbered", "wordsmithed"]


def test_change_overview_names_every_known_tag_even_at_zero():
    """"This release renamed nothing" must not read as "renames unreported"."""
    out = extractor.parse_change_overview(
        _overview_df([("GOV-A1", "Governance Program", "- renumbered")])
    )
    for tag in extractor.PUBLISHER_CONTROL_TAGS:
        assert tag in out["counts"], tag
    assert out["counts"]["renamed"] == 0
    assert out["counts"]["moved_domains"] == 0


def test_change_overview_normalises_every_merge_operand_to_one_tag():
    """'merged old X' and 'merged old X & Y' are one kind of change.

    The operands are read from the READ THIS block, where they are columns.
    Counting them as distinct tags would make the merge count depend on how
    many controls happened to be folded into one survivor.
    """
    out = extractor.parse_change_overview(_overview_df([
        ("GOV-A1", "One", "- merged old TDA-11.2"),
        ("GOV-A2", "Two", "- merged old END-03 & NET 15.3"),
    ]))
    assert out["counts"]["merged"] == 2
    assert out["tags"]["GOV-A1"] == ["merged"]
    assert out["tags"]["GOV-A2"] == ["merged"]


def test_change_overview_counts_an_unknown_tag_rather_than_dropping_it():
    out = extractor.parse_change_overview(
        _overview_df([("GOV-A1", "One", "- split into two")])
    )
    assert out["counts"]["split_into_two"] == 1
    assert out["tags"]["GOV-A1"] == ["split_into_two"]


def test_normalize_change_tag_folds_case_spacing_and_bullets():
    assert extractor.normalize_change_tag("- Moved Domains") == "moved_domains"
    assert extractor.normalize_change_tag("  new control ") == "new_control"
    assert extractor.normalize_change_tag("Merged old ABC-01") == "merged"


def _read_this_df(rows):
    width = max(len(r) for r in rows)
    padded = [list(r) + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(padded)


def test_read_this_locates_its_header_row_instead_of_assuming_one():
    """A blank row inserted above the header must not shift the columns.

    pandas cannot use this sheet's first row as a header — it is a prose
    paragraph — so the header is found by looking for 'New SCF #'. Assuming an
    index would silently mis-read every column the release after next.
    """
    df = _read_this_df([
        ("This release renumbers the catalogue.", None, None, None, None),
        (None, None, None, None, None),
        (None, None, None, None, None),
        (
            "New SCF #",
            "Legacy SCF #",
            "Deprecated SCF Control Name",
            "Legacy SCF #",
            "Merged into",
        ),
        ("GOV-A1", "OLD-1", "Old Governance Thing", "OLD-9", "GOV-A2"),
    ])
    out = extractor.parse_read_this(df)

    assert out["summary"] == "This release renumbers the catalogue."
    assert out["merged"] == [
        {
            "legacy_scf_id": "OLD-9",
            "legacy_name": "Old Governance Thing",
            "merged_into": "GOV-A2",
        }
    ]


def test_read_this_reads_the_right_legacy_column():
    """Two 'Legacy SCF #' columns share the header row.

    The left one belongs to the renumbering crosswalk, which the platform
    already gets from the controls sheet. Only the one to the RIGHT of the
    deprecated-name column describes a merge, and taking the left one would
    report the survivor's own old number as the thing that was merged away.
    """
    df = _read_this_df([
        ("Narrative.", None, None, None, None),
        (
            "New SCF #",
            "Legacy SCF #",
            "Deprecated SCF Control Name",
            "Legacy SCF #",
            "Merged into",
        ),
        ("GOV-A1", "CROSSWALK-1", "Retired Thing", "MERGED-1", "GOV-A1"),
    ])
    out = extractor.parse_read_this(df)
    assert out["merged"][0]["legacy_scf_id"] == "MERGED-1"
    assert out["merged"][0]["legacy_scf_id"] != "CROSSWALK-1"


def test_read_this_without_a_header_row_still_yields_the_narrative():
    df = _read_this_df([("Just a paragraph and nothing else.",)])
    out = extractor.parse_read_this(df)
    assert out["summary"] == "Just a paragraph and nothing else."
    assert out["merged"] == []


def test_empty_publisher_changes_leaves_counts_empty_not_zero_filled():
    """The shape for a workbook that ships no change sheets at all.

    ``counts == {}`` and ``counts == {tag: 0, ...}`` are different facts: the
    first says the publisher reported nothing, the second says it reported and
    nothing changed. A pre-2026.3 workbook is the first.
    """
    empty = extractor.empty_publisher_changes()
    assert empty["controls"]["counts"] == {}
    assert empty["summary"] is None
    assert empty["frameworks"] == {"added": [], "removed": [], "mapping_errata": []}
    assert empty["controls"]["merged"] == []


def test_resolve_optional_sheet_matches_exactly_then_loosely_then_gives_up():
    class _Xl:
        def __init__(self, names):
            self.sheet_names = names

    assert extractor.resolve_optional_sheet(
        _Xl(["Controls", " STRM Errata "]), *extractor.SHEET_STRM_ERRATA
    ) == " STRM Errata "
    assert extractor.resolve_optional_sheet(
        _Xl(["Controls", "2026.3 Errata Notes"]), *extractor.SHEET_STRM_ERRATA
    ) == "2026.3 Errata Notes"
    assert extractor.resolve_optional_sheet(
        _Xl(["Controls"]), *extractor.SHEET_STRM_ERRATA
    ) is None


# ---------------------------------------------------------------------------
# Real workbooks — skipped where the (gitignored) files are absent
# ---------------------------------------------------------------------------

CATALOG_SOURCE = Path(__file__).resolve().parents[2] / "catalog-source"
WORKBOOK_2026_3 = CATALOG_SOURCE / "scf-2026-3.xlsx"
WORKBOOK_2026_2 = CATALOG_SOURCE / "scf-2026-2.xlsx"

_MISSING = (
    "{path} is not present. The real SCF workbooks are gitignored "
    "(.gitignore: catalog-source/*.xlsx), so this assertion cannot run in a "
    "fresh clone or in CI; the synthetic-sheet tests above cover the parsing "
    "rules."
)


@pytest.mark.skipif(
    not WORKBOOK_2026_3.exists(), reason=_MISSING.format(path=WORKBOOK_2026_3)
)
def test_real_2026_3_workbook_declares_the_retirements_the_gate_needs():
    """The numbers this whole change was built against.

    Six focal documents are declared RETIRED by the publisher in 2026.3. Those
    six are the difference between "six unexplained removals" and "six the
    publisher told us about", which is the difference between a blocked upgrade
    and one an operator can reason about.
    """
    changes = extractor.extract_publisher_changes(WORKBOOK_2026_3)
    frameworks = changes["frameworks"]

    assert len(frameworks["added"]) == 25
    assert len(frameworks["removed"]) == 6
    assert len(frameworks["mapping_errata"]) == 126

    removed = {e["fdi"] for e in frameworks["removed"]}
    assert removed == {
        "apac-aus-ism-2026-march",
        "emea-deu-c5-2020",
        "general-scf-dpmp-2025",
        "general-sparta",
        "usa-federal-law-ftc-act",
        "usa-federal-sro-finra",
    }


@pytest.mark.skipif(
    not WORKBOOK_2026_3.exists(), reason=_MISSING.format(path=WORKBOOK_2026_3)
)
def test_real_2026_3_workbook_control_level_counts():
    changes = extractor.extract_publisher_changes(WORKBOOK_2026_3)
    counts = changes["controls"]["counts"]

    assert counts["new_control"] == 80
    assert counts["renumbered"] == 1457
    assert counts["wordsmithed"] == 204
    assert counts["renamed"] == 185
    assert counts["moved_domains"] == 99
    # 22, not 23: the Change Overview has 22 'merged' bullets because one of
    # them names two operands on a single line ('merged old END-03 & NET
    # 15.3'). The 23 merges are ROWS in the READ THIS deprecation block, one
    # per control that went away. Both numbers are correct about different
    # things and neither is a count of the other.
    assert counts["merged"] == 22
    assert len(changes["controls"]["merged"]) == 23

    assert len(changes["controls"]["tags"]) == 1564
    assert changes["summary"] and len(changes["summary"]) > 100


@pytest.mark.skipif(
    not WORKBOOK_2026_2.exists(), reason=_MISSING.format(path=WORKBOOK_2026_2)
)
def test_real_2026_2_workbook_ships_no_change_sheets():
    """Every release before 2026.3 must extract to the empty shape.

    The extractor runs against both eras of workbook and the absence of these
    sheets is normal, not a fault.
    """
    changes = extractor.extract_publisher_changes(WORKBOOK_2026_2)
    assert changes == extractor.empty_publisher_changes()


# ---------------------------------------------------------------------------
# The artifact extract_to_dir writes
# ---------------------------------------------------------------------------


def test_extract_to_dir_always_writes_the_artifact_and_flags_it_in_the_meta(
    tmp_path,
):
    """``publisher_changes.json`` is written for every workbook, empty or not.

    A missing file and a file saying "no change sheets" would be
    indistinguishable from a failed extraction, so the file is unconditional and
    ``catalog_meta.json`` carries a boolean saying whether it has content. A
    pre-2026.3 workbook must set that flag False, not omit the key.
    """
    import json

    from test_scf_extractor import build_workbook

    workbook = build_workbook(
        tmp_path / "scf.xlsx", version="2026.2", era="focal_documents"
    )
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    extractor.extract_to_dir(workbook, out_dir)

    artifact = out_dir / "publisher_changes.json"
    assert artifact.exists()
    assert json.loads(artifact.read_text()) == extractor.empty_publisher_changes()

    meta = json.loads((out_dir / "catalog_meta.json").read_text())
    assert meta["publisher_changes"] is False
