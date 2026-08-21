"""Tests for the declarative generator registry.

The registry is where the licence classification lives, so the tests care most
about the invariants that protect it: every Tier 2+ generator is marked
derivative, names are unique, and a domain-scoped generator resolves distinct
filenames per domain.
"""
import pytest

from services.doc_gen.registry import (
    GeneratorNotFound,
    all_generators,
    derivative_generators,
    generators_by_tier,
    get_generator,
)


def test_registry_loads():
    assert len(all_generators()) >= 8


def test_generator_names_are_unique():
    names = [g.name for g in all_generators()]
    assert len(names) == len(set(names))


def test_every_tier_2_generator_is_marked_derivative():
    """The invariant the loader also enforces at import time.

    A Tier 2 generator marked non-derivative would run without the derivative
    consent gate — the exact failure that costs a licence.
    """
    for spec in all_generators():
        if spec.tier >= 2:
            assert spec.is_derivative, f"{spec.name} is tier {spec.tier} but not derivative"


def test_no_tier_1_generator_is_marked_derivative():
    """Tier 1 renders tables from data; marking it derivative would surrender
    the free-licence position for no benefit."""
    for spec in generators_by_tier(1):
        assert not spec.is_derivative


def test_derivative_generators_are_exactly_the_tier_2_plus_ones():
    assert {g.name for g in derivative_generators()} == {
        g.name for g in all_generators() if g.tier >= 2
    }


def test_unknown_generator_names_available_ones():
    with pytest.raises(GeneratorNotFound) as exc:
        get_generator("does-not-exist")
    assert "soa" in str(exc.value)


# ---------------------------------------------------------------------------
# Filename and title resolution
# ---------------------------------------------------------------------------


def test_domain_scoped_filenames_differ_per_domain():
    spec = get_generator("policy")
    assert spec.domain_scoped
    assert spec.resolve_filename("GOV") != spec.resolve_filename("IAC")


def test_non_domain_generator_filename_is_stable():
    spec = get_generator("soa")
    assert spec.resolve_filename() == spec.resolve_filename("IAC")


def test_domain_title_interpolates_the_domain_name():
    assert get_generator("policy").resolve_title("Access Control") == "Access Control Policy"


def test_title_falls_back_to_display_name():
    assert get_generator("soa").resolve_title() == "Statement of Applicability"


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def test_every_tier_1_generator_resolves_its_renderer():
    """Catches a renderer renamed in code but not in YAML — otherwise the
    failure surfaces only when a user asks for that document."""
    for spec in generators_by_tier(1):
        assert callable(spec.resolve_renderer()), spec.name


def test_every_tier_2_generator_loads_its_prompt():
    for spec in all_generators():
        if spec.tier >= 2:
            template = spec.load_prompt_template()
            assert "{org_name}" in template, spec.name
            assert "{control_sections}" in template, spec.name


def test_tier_1_generator_has_no_prompt_template():
    with pytest.raises(ValueError):
        get_generator("soa").load_prompt_template()


def test_data_requirements_default_to_controls_only():
    spec = get_generator("soa")
    assert spec.requires.controls is True
    assert spec.requires.systems is False


def test_evidence_schedule_declares_it_needs_evidence():
    """A generator that reads a register it did not declare would get an empty
    one at runtime rather than an error."""
    assert get_generator("evidence-schedule").requires.evidence is True


def test_risk_treatment_declares_it_needs_risks():
    assert get_generator("risk-treatment").requires.risks is True
