"""Staleness: has the organisation moved since this document was generated?

The pipeline has always computed an input fingerprint and skipped a
regeneration when nothing changed. Nothing surfaced the answer, so a Statement
of Applicability generated before a scoping exercise looked identical to one
generated after it.

These tests pin two things. First, that the recomputed ``controls_hash`` is
byte-identical to the one the pipeline stored -- a projection that differs by
so much as a default would make every document permanently stale, which is
worse than no badge at all. Second, that the *composite* is deliberately not
what is compared, because two of its four components are properties of the
prompt rather than of the organisation.
"""
from __future__ import annotations

from services.doc_gen.fingerprint import compute_controls_hash, compute_fingerprint
from services.doc_gen.staleness import CurrentInputs, assess


def _control(key, *, status="implemented", owner="Alice", objectives=None):
    return {
        "scf_id": key,
        "control_name": f"Name {key}",
        "control_description": f"Description {key}",
        "implementation_status": status,
        "maturity_level": "L3",
        "owner": owner,
        "implementation_notes": None,
        "assessment_objectives": objectives or [],
    }


def _inputs(controls, *, catalog_version="2026.1", domain="GOV"):
    return CurrentInputs(
        catalog_version=catalog_version,
        all_controls=controls,
        by_domain={domain: controls},
    )


def _stored(controls, *, catalog_version="2026.1"):
    """The ``input_components`` blob the pipeline would have written."""
    result = compute_fingerprint(
        controls, "system", "user", catalog_version=catalog_version
    )
    return result.input_components.to_dict()


class TestHashParity:
    def test_the_standalone_hash_matches_the_composites_component(self):
        # If these ever diverge, every document reads as stale for ever.
        controls = [_control("CTRL1"), _control("CTRL2")]
        assert (
            compute_controls_hash(controls)
            == compute_fingerprint(controls, "s", "u").input_components.controls_hash
        )

    def test_control_order_does_not_affect_the_hash(self):
        a = [_control("CTRL1"), _control("CTRL2")]
        assert compute_controls_hash(a) == compute_controls_hash(list(reversed(a)))


class TestAssess:
    def test_unchanged_inputs_are_not_stale(self):
        controls = [_control("CTRL1"), _control("CTRL2")]
        result = assess(_stored(controls), "GOV", _inputs(controls))
        assert result.is_stale is False
        assert result.reason is None

    def test_a_scoped_control_added(self):
        before = [_control("CTRL1")]
        after = before + [_control("CTRL2")]
        result = assess(_stored(before), "GOV", _inputs(after))
        assert result.is_stale is True
        assert result.reason == "Scope has changed (1 added) since this was generated"

    def test_a_scoped_control_removed(self):
        before = [_control("CTRL1"), _control("CTRL2")]
        after = [_control("CTRL1")]
        result = assess(_stored(before), "GOV", _inputs(after))
        assert result.reason == "Scope has changed (1 removed) since this was generated"

    def test_both_directions_are_reported(self):
        before = [_control("CTRL1"), _control("CTRL2")]
        after = [_control("CTRL1"), _control("CTRL3")]
        result = assess(_stored(before), "GOV", _inputs(after))
        assert "1 added" in result.reason and "1 removed" in result.reason

    def test_an_edited_control_is_stale_without_a_scope_change(self):
        before = [_control("CTRL1", status="not_started")]
        after = [_control("CTRL1", status="implemented")]
        result = assess(_stored(before), "GOV", _inputs(after))
        assert result.is_stale is True
        assert result.reason == "Control details have changed since this was generated"

    def test_an_added_assessment_objective_counts(self):
        before = [_control("CTRL1")]
        after = [_control(
            "CTRL1", objectives=[{"ao_id": "CTRL1_A", "objective_text": "Do it"}]
        )]
        assert assess(_stored(before), "GOV", _inputs(after)).is_stale is True

    def test_a_catalog_upgrade_alone_is_stale(self):
        controls = [_control("CTRL1")]
        result = assess(
            _stored(controls, catalog_version="2026.1"),
            "GOV",
            _inputs(controls, catalog_version="2026.2"),
        )
        assert result.is_stale is True
        assert "SCF catalog moved from 2026.1 to 2026.2" in result.reason

    def test_a_document_with_no_stored_components_is_not_stale(self):
        # It predates fingerprint tracking. Nothing is known, so asserting
        # staleness would badge documents that may well be current.
        assert assess({}, "GOV", _inputs([_control("CTRL1")])).is_stale is False
        assert assess(None, "GOV", _inputs([_control("CTRL1")])).is_stale is False

    def test_a_domain_document_reads_only_its_own_domain(self):
        gov = [_control("CTRL1")]
        ast = [_control("CTRL9")]
        inputs = CurrentInputs(
            catalog_version="2026.1",
            all_controls=gov + ast,
            by_domain={"GOV": gov, "AST": ast},
        )
        # A change in another domain must not make the GOV policy stale.
        assert assess(_stored(gov), "GOV", inputs).is_stale is False

    def test_a_non_domain_document_reads_the_whole_estate(self):
        gov = [_control("CTRL1")]
        ast = [_control("CTRL9")]
        inputs = CurrentInputs(
            catalog_version="2026.1",
            all_controls=gov + ast,
            by_domain={"GOV": gov, "AST": ast},
        )
        assert assess(_stored(gov + ast), "", inputs).is_stale is False
        assert assess(_stored(gov), "", inputs).is_stale is True

    def test_a_domain_that_lost_every_control_is_stale(self):
        before = [_control("CTRL1")]
        inputs = CurrentInputs(
            catalog_version="2026.1", all_controls=[], by_domain={}
        )
        result = assess(_stored(before), "GOV", inputs)
        assert result.is_stale is True
        assert "1 removed" in result.reason


class TestWhyNotTheComposite:
    def test_the_prompt_components_are_not_consulted(self):
        # Tier 2 prompts embed the document's own previous content, so a
        # composite comparison would mark every edited Tier 2 document stale
        # for ever. Only controls and catalog version are inputs.
        controls = [_control("CTRL1")]
        stored = _stored(controls)
        stored["prompt_hash"] = "a-completely-different-prompt"
        stored["template_hash"] = "a-completely-different-template"
        assert assess(stored, "GOV", _inputs(controls)).is_stale is False
