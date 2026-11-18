# SPDX-License-Identifier: AGPL-3.0-only
"""Which framework key feeds the capability-theme mapping.

SCF 2026.3 renamed the NIST 800-53 Rev 5 column, so the key moved from
``nist_800_53_r5`` to ``nist_800_53_r5_2``. The derivation read the old
spelling only and every capability theme went to zero controls. These tests
pin the key rule; the apply and repair behaviour is in test_catalog_apply.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.theme_mapping import (  # noqa: E402
    nist_800_53_key_rank,
    nist_800_53_refs,
    nist_families,
)


def test_the_key_up_to_2026_2_is_read():
    assert nist_800_53_refs({"nist_800_53_r5": ["AC-1", "IA-2.1"]}) == ["AC-1", "IA-2.1"]


def test_the_2026_3_key_is_read():
    """The defect: this spelling returned nothing."""
    assert nist_800_53_refs({"nist_800_53_r5_2": ["CM-08(01)", "PM-05"]}) == [
        "CM-08(01)",
        "PM-05",
    ]


def test_the_newest_edition_wins_and_the_two_are_not_merged():
    refs = nist_800_53_refs(
        {"nist_800_53_r5": ["AC-1"], "nist_800_53_r5_2": ["SC-07"]}
    )
    assert refs == ["SC-07"]


def test_a_later_revision_outranks_a_sub_release():
    refs = nist_800_53_refs(
        {"nist_800_53_r5_2": ["AC-02"], "nist_800_53_r6": ["IR-04"]}
    )
    assert refs == ["IR-04"]


def test_an_empty_newer_key_does_not_hide_an_older_one_with_references():
    assert nist_800_53_refs({"nist_800_53_r5_2": [], "nist_800_53_r5": ["AC-1"]}) == ["AC-1"]


@pytest.mark.parametrize(
    "key",
    [
        "nist_800_53_r4",              # earlier revision
        "nist_800_53_r5_noc",          # the "no controls" variant
        "nist_800_53b_r5_high",        # a baseline, not the catalogue
        "nist_800_53b_r5_privacy",
        "nist_800_82_r3",              # a different publication
        "nist_800_171_r3",
        "nist_csf_2_0",
        "xnist_800_53_r5",
        "nist_800_53_r5_2_extra",
    ],
)
def test_other_keys_are_not_mistaken_for_it(key):
    assert nist_800_53_key_rank(key) is None
    assert nist_800_53_refs({key: ["AC-1"]}) == []


@pytest.mark.parametrize("value", [None, {}, {"nist_800_53_r5_2": None}])
def test_a_control_without_references_yields_none(value):
    assert nist_800_53_refs(value) == []


def test_the_returned_list_is_a_copy():
    source = {"nist_800_53_r5_2": ["AC-02"]}
    nist_800_53_refs(source).append("XX-1")
    assert source["nist_800_53_r5_2"] == ["AC-02"]


def test_families_from_both_reference_spellings():
    assert nist_families(["AC-1", "AC-2", "IA-2.1"]) == {"AC", "IA"}
    assert nist_families(["CM-08(01)", "PM-05", "CM-08"]) == {"CM", "PM"}
