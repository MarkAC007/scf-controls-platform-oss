"""Attestation gate on the KSI evidence-metrics SQL (#787, ISC-69..73).

The gate degrades an unattested AI verdict to ``unassessed`` rather than
dropping the row: coverage is a question about whether evidence exists,
quality is a question about what a human signed for, and conflating them
would make turning the policy on look like evidence disappeared.

Scripted SQL + pure functions, no DB — same style as
``test_ksi_composite.py``.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from itertools import product

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.capability_themes import (  # noqa: E402
    _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL_ATTESTED,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL,
    _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL_ATTESTED,
    _EVIDENCE_METRICS_SQL,
    _EVIDENCE_METRICS_SQL_ATTESTED,
    _EVIDENCE_METRICS_WINDOW_AWARE_SQL,
    _EVIDENCE_METRICS_WINDOW_AWARE_SQL_ATTESTED,
    _select_evidence_metrics_sql,
)
from services.assurance_policy import (  # noqa: E402
    DEFAULT_ASSURANCE_POLICY,
    AssurancePolicy,
)


# The SQL that shipped, hashed before the attested_only refactor split each
# constant into a builder. Parameterising a query is only safe if the
# default rendering comes out byte-for-byte identical; "it looks the same"
# is not a proof, and a stray alias or a shifted space would change the
# plan for every existing org.
SHIPPED_HASHES = {
    "per_file": "278e4d290e4700175ff374cdceebf3aaeb10ff444e2f07bf18628827067dcd12",
    "window": "bf46a4008e1528e1cfe6da3b7f29f459292872ba8f752bfc028129863b617e4c",
    "composite": "0c2ca8ff747b173d9c435535035a142b08188cb202423434bfa7bbb234537358",
    "composite_window": "067bdf22b58e6ef6eece7b47cd5a7382f4b33ba418b4c50ea4cf8c41e4007e22",
}

OPEN_VARIANTS = {
    "per_file": _EVIDENCE_METRICS_SQL,
    "window": _EVIDENCE_METRICS_WINDOW_AWARE_SQL,
    "composite": _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL,
    "composite_window": _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL,
}

ATTESTED_VARIANTS = {
    "per_file": _EVIDENCE_METRICS_SQL_ATTESTED,
    "window": _EVIDENCE_METRICS_WINDOW_AWARE_SQL_ATTESTED,
    "composite": _EVIDENCE_METRICS_COMPOSITE_AWARE_SQL_ATTESTED,
    "composite_window": _EVIDENCE_METRICS_COMPOSITE_AWARE_WINDOW_SQL_ATTESTED,
}


def _sha(sql) -> str:
    return hashlib.sha256(str(sql).encode()).hexdigest()


def _tables(sql) -> set[str]:
    """Physical table names a statement reads.

    CTE names and set-returning functions (``jsonb_array_elements_text``)
    both appear after FROM/JOIN and are neither of them tables.
    """
    text = str(sql)
    names = {
        name
        for name, call in re.findall(
            r"(?:FROM|JOIN)\s+([a-z_][a-z0-9_]*)(\s*\()?", text
        )
        if not call
    }
    ctes = set(re.findall(r"([a-z_][a-z0-9_]*)\s+AS\s*\(", text))
    return names - ctes


class TestPolicyDefault:
    def test_default_policy_is_todays_behaviour(self):
        # ISC-69/70 — an org with no policy row must score exactly as before.
        assert DEFAULT_ASSURANCE_POLICY.require_evidence_attestation is False
        assert DEFAULT_ASSURANCE_POLICY.require_reviewer_independence is False

    def test_policy_is_immutable(self):
        # A shared default that could be mutated in place would leak one
        # org's setting into every other org in the process.
        with pytest.raises(Exception):
            DEFAULT_ASSURANCE_POLICY.require_evidence_attestation = True

    def test_policy_fields_default_off_individually(self):
        assert AssurancePolicy().require_evidence_attestation is False
        assert AssurancePolicy(require_evidence_attestation=True).\
            require_reviewer_independence is False


class TestUngatedSqlIsUnchanged:
    @pytest.mark.parametrize("name", sorted(SHIPPED_HASHES))
    def test_open_variant_is_byte_identical_to_what_shipped(self, name):
        assert _sha(OPEN_VARIANTS[name]) == SHIPPED_HASHES[name]


class TestAttestationGate:
    @pytest.mark.parametrize("name", sorted(ATTESTED_VARIANTS))
    def test_attested_variant_differs_from_open(self, name):
        assert _sha(ATTESTED_VARIANTS[name]) != _sha(OPEN_VARIANTS[name])

    def test_per_file_status_is_gated(self):
        # Both columns must be gated, and asserting the whole CASE rather
        # than the predicate alone is what makes this specific: with only a
        # substring check, dropping the gate from one column still passes
        # on the other column's copy of the same predicate.
        assert (
            "CASE WHEN ef.review_status = 'approved' "
            "THEN COALESCE(ea.status, 'unassessed') ELSE 'unassessed' END"
        ) in str(ATTESTED_VARIANTS["per_file"])

    def test_per_file_score_is_gated(self):
        assert (
            "CASE WHEN ef.review_status = 'approved' THEN ea.relevance_score END"
        ) in str(ATTESTED_VARIANTS["per_file"])

    def test_window_status_is_gated(self):
        assert (
            "CASE WHEN ewa.review_status = 'approved' THEN ewa.status "
            "ELSE 'unassessed' END"
        ) in str(ATTESTED_VARIANTS["window"])

    def test_window_score_is_gated(self):
        assert (
            "CASE WHEN ewa.review_status = 'approved' THEN ewa.relevance_score END"
        ) in str(ATTESTED_VARIANTS["window"])

    def test_an_unattested_window_does_not_fall_back_to_the_file_underneath(self):
        # The window gate yields 'unassessed', not NULL. NULL would let the
        # mixed COALESCE reach past it to the per-file verdict — routing
        # around the gate instead of applying it.
        assert "ELSE 'unassessed' END AS window_status" in str(
            ATTESTED_VARIANTS["window"]
        )

    @pytest.mark.parametrize(
        "name,predicate,expected",
        [
            ("per_file", "ef.review_status = 'approved'", 2),
            ("window", "ewa.review_status = 'approved'", 2),
        ],
    )
    def test_every_quality_column_carries_its_own_gate(
        self, name, predicate, expected
    ):
        assert str(ATTESTED_VARIANTS[name]).count(predicate) >= expected

    def test_composite_requires_every_folded_window_to_be_approved(self):
        # A composite is a projection of windows; it cannot be more
        # attested than the windows it folded in.
        sql = str(ATTESTED_VARIANTS["composite_window"])
        assert "included_window_ids" in sql
        assert "ewa2.review_status <> 'approved'" in sql

    def test_composite_gate_rejects_an_empty_window_list(self):
        # NOT EXISTS over an empty array is vacuously true — without the
        # length check a composite that folded in nothing would read as
        # fully attested.
        sql = str(ATTESTED_VARIANTS["composite_window"])
        assert "jsonb_array_length(cac.included_window_ids) > 0" in sql

    @pytest.mark.parametrize("name", sorted(ATTESTED_VARIANTS))
    def test_gate_degrades_rather_than_filters(self, name):
        # ISC-70's other half: the coverage columns must be untouched, so
        # the gate may only appear inside a CASE, never as a WHERE on the
        # file set. If a gated variant ever drops rows,
        # controls_with_evidence moves and the whole KPS moves with it.
        sql = str(ATTESTED_VARIANTS[name])
        assert "COUNT(DISTINCT" in sql or "count(distinct" in sql
        for line in sql.splitlines():
            stripped = line.strip()
            if stripped.startswith(("WHERE", "AND")) and "review_status" in stripped:
                # The one legitimate predicate is inside the composite
                # attestation EXISTS sub-select, which is scoped to windows.
                assert "ewa2." in stripped, f"row-filtering gate found: {stripped}"


class TestVariantSelection:
    def test_every_combination_selects_a_distinct_statement(self):
        seen = {
            (c, w, a): _sha(_select_evidence_metrics_sql(c, w, a))
            for c, w, a in product([False, True], repeat=3)
        }
        assert len(set(seen.values())) == 8

    @pytest.mark.parametrize(
        "composite,window,expected",
        [
            (False, False, "per_file"),
            (False, True, "window"),
            (True, False, "composite"),
            (True, True, "composite_window"),
        ],
    )
    def test_policy_off_selects_exactly_the_shipped_statement(
        self, composite, window, expected
    ):
        chosen = _select_evidence_metrics_sql(composite, window, attested_only=False)
        assert _sha(chosen) == SHIPPED_HASHES[expected]

    @pytest.mark.parametrize(
        "composite,window,expected",
        [
            (False, False, "per_file"),
            (False, True, "window"),
            (True, False, "composite"),
            (True, True, "composite_window"),
        ],
    )
    def test_policy_on_selects_the_gated_twin_of_the_same_tier(
        self, composite, window, expected
    ):
        chosen = _select_evidence_metrics_sql(composite, window, attested_only=True)
        assert _sha(chosen) == _sha(ATTESTED_VARIANTS[expected])

    @pytest.mark.parametrize(
        "composite,window", [(False, False), (False, True), (True, True)]
    )
    def test_attestation_does_not_change_which_tables_are_read(
        self, composite, window
    ):
        # The policy picks a column treatment, not a data source. If it
        # also moved the tier, turning it on would silently repoint the
        # EQ axis at a different table.
        open_sql = _select_evidence_metrics_sql(composite, window, False)
        gated_sql = _select_evidence_metrics_sql(composite, window, True)
        assert _tables(gated_sql) == _tables(open_sql)

    def test_attesting_a_composite_consults_the_windows_it_folded_in(self):
        # The one deliberate exception to the rule above. With the window
        # tier off, the open composite SQL never touches
        # evidence_window_assessments — but a composite's attestation is
        # not its own to claim: it is only as attested as the windows it
        # rolled up, so the gated variant must reach for them.
        open_sql = _select_evidence_metrics_sql(True, False, False)
        gated_sql = _select_evidence_metrics_sql(True, False, True)
        assert "evidence_window_assessments" not in _tables(open_sql)
        assert "evidence_window_assessments" in _tables(gated_sql)


class TestBuildersCannotBeFedCallerInput:
    """Backs the two ``# nosemgrep`` suppressions in ``capability_themes``.

    Semgrep flags ``text(f"...")`` because an interpolated statement is
    normally an injection vector. Here every interpolated fragment is a
    module-private constant picked by a boolean, so the set of statements
    the module can produce is finite and enumerable. These tests make that
    a checked property rather than a comment: if someone later widens a
    builder to take a caller-supplied string, the suppression stops being
    true and this fails.
    """

    BUILDERS = [
        "_build_per_file_sql",
        "_build_window_aware_sql",
        "_build_composite_aware_sql",
    ]

    @pytest.mark.parametrize("name", BUILDERS)
    def test_metrics_builders_take_no_string_parameters(self, name):
        import inspect

        from api import capability_themes

        sig = inspect.signature(getattr(capability_themes, name))
        for param in sig.parameters.values():
            assert param.annotation is bool, (
                f"{name}({param.name}) is annotated {param.annotation!r}; only "
                "bool parameters keep the interpolated SQL a closed set"
            )

    @pytest.mark.parametrize("name", BUILDERS)
    def test_every_builder_carries_the_full_dotted_check_id(self, name):
        # The short id shown in the GitHub alert UI does not suppress
        # anything -- a suppression written that way silently does nothing.
        import inspect

        from api import capability_themes

        source = inspect.getsource(getattr(capability_themes, name))
        if "nosemgrep" not in source:
            pytest.skip(f"{name} has no suppression to check")
        assert (
            "# nosemgrep: python.sqlalchemy.security.audit."
            "avoid-sqlalchemy-text.avoid-sqlalchemy-text" in source
        )

    def test_the_builders_produce_a_finite_set_of_statements(self):
        # Eight combinations, eight statements, nothing caller-shaped in
        # between. This is the property the suppression rests on.
        rendered = {
            _sha(_select_evidence_metrics_sql(c, w, a))
            for c, w, a in product([False, True], repeat=3)
        }
        assert len(rendered) == 8
