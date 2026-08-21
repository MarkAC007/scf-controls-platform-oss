"""Tests for the licence gate.

The gate is the reason this feature can ship without a licence renegotiation,
so its decision table is tested exhaustively rather than by example. Each case
asserts on the *reason* as well as the verdict — a refusal a user cannot act on
is a defect even when the verdict is right.
"""
import pytest

from services.doc_gen import licence
from services.doc_gen.licence import (
    ACKNOWLEDGEMENT_TEXT,
    LicenceError,
    assert_generation_allowed,
    check_generation_allowed,
    is_derivative_tier,
    platform_kill_switch_engaged,
)


class FakeSettings:
    """Stand-in for a DocGenSettings row."""

    def __init__(self, enabled=True, derivative=False, acknowledged=True):
        self.enabled = enabled
        self.derivative_generators_enabled = derivative
        self.licence_acknowledged_at = "2026-08-21" if acknowledged else None


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tier,expected", [(1, False), (2, True), (3, True)])
def test_tier_classification_treats_generated_prose_as_derivative(tier, expected):
    assert is_derivative_tier(tier) is expected


# ---------------------------------------------------------------------------
# The decision table
# ---------------------------------------------------------------------------


def test_no_settings_row_refuses():
    result = check_generation_allowed(None, tier=1, is_derivative=False)
    assert result.allowed is False
    assert "not enabled" in result.reason


def test_disabled_refuses_even_for_tier_1():
    result = check_generation_allowed(
        FakeSettings(enabled=False), tier=1, is_derivative=False
    )
    assert result.allowed is False


def test_tier_1_allowed_when_enabled_without_derivative_consent():
    """The whole point of the two-switch design.

    An organisation that only wants a Statement of Applicability must not be
    made to accept a derivative-work notice that does not describe what it is
    doing.
    """
    result = check_generation_allowed(
        FakeSettings(enabled=True, derivative=False), tier=1, is_derivative=False
    )
    assert result.allowed is True
    assert result.reason is None


def test_tier_2_refused_without_derivative_consent():
    result = check_generation_allowed(
        FakeSettings(enabled=True, derivative=False), tier=2, is_derivative=True
    )
    assert result.allowed is False
    assert "derivative" in result.reason
    # The refusal must name the remedy, not just the rule.
    assert "administrator" in result.reason.lower()


def test_tier_2_allowed_with_derivative_consent():
    result = check_generation_allowed(
        FakeSettings(enabled=True, derivative=True), tier=2, is_derivative=True
    )
    assert result.allowed is True


def test_enabled_without_acknowledgement_refuses():
    """Defence in depth behind the database check constraint.

    The constraint makes this row unstorable, so reaching this branch means
    something has gone wrong upstream. Refusing is the only safe response.
    """
    result = check_generation_allowed(
        FakeSettings(enabled=True, acknowledged=False), tier=1, is_derivative=False
    )
    assert result.allowed is False
    assert "acknowledgement" in result.reason


# ---------------------------------------------------------------------------
# Platform kill switch
# ---------------------------------------------------------------------------


def test_platform_kill_switch_overrides_every_organisation(monkeypatch):
    monkeypatch.setenv("DOC_GEN_DISABLED", "1")
    assert platform_kill_switch_engaged() is True
    result = check_generation_allowed(
        FakeSettings(enabled=True, derivative=True), tier=1, is_derivative=False
    )
    assert result.allowed is False
    assert "platform-wide" in result.reason


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_kill_switch_off_for_falsey_values(monkeypatch, value):
    monkeypatch.setenv("DOC_GEN_DISABLED", value)
    assert platform_kill_switch_engaged() is False


# ---------------------------------------------------------------------------
# assert_generation_allowed
# ---------------------------------------------------------------------------


def test_assert_raises_licence_error_on_refusal():
    with pytest.raises(LicenceError):
        assert_generation_allowed(None, tier=1, is_derivative=False)


def test_licence_error_is_a_permission_error():
    """So the API layer can map it to 403 without importing this module."""
    assert issubclass(LicenceError, PermissionError)


def test_assert_is_silent_when_allowed():
    assert_generation_allowed(
        FakeSettings(enabled=True), tier=1, is_derivative=False
    )


# ---------------------------------------------------------------------------
# Acknowledgement
#
# The licence position is stated once, to the administrator who switches the
# module on. It is deliberately absent from generated documents: a customer's
# policy is their artifact and says nothing about how this platform is
# licensed. The two tests below are the pair -- one asserts the acknowledgement
# is still there, the other asserts nothing else in this module hands
# document-facing licence text to the generator.
# ---------------------------------------------------------------------------


def test_acknowledgement_text_names_the_licence_and_the_consequence():
    assert "CC BY-ND 4.0" in ACKNOWLEDGEMENT_TEXT
    assert "derivative" in ACKNOWLEDGEMENT_TEXT


def test_the_acknowledgement_is_the_only_licence_text_this_module_publishes():
    """No attribution block may be reintroduced for appending to documents."""
    published = {
        name: value
        for name, value in vars(licence).items()
        if not name.startswith("_")
        and isinstance(value, str)
        and "Secure Controls Framework" in value
    }
    assert set(published) == {"ACKNOWLEDGEMENT_TEXT"}
    assert not hasattr(licence, "attribution_footer")
