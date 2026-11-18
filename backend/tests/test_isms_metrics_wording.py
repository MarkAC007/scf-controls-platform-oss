# SPDX-License-Identifier: AGPL-3.0-only
"""Plain-English wording for ISMS metrics (pure, no database)."""
import pytest

from services.isms_metrics import wording
from services.isms_metrics.library import REGISTRY


def test_value_text():
    assert wording.value_text("percent", 13.636, "live") == "14%"
    assert wording.value_text("percent", 12.5, "live") == "13%"
    assert wording.value_text("count", 16, "live") == "16"
    assert wording.value_text("count", 0, "live") == "0"
    assert wording.value_text(None, 1, "milestone") == "Done"
    assert wording.value_text(None, 0, "milestone") == "Not done"
    assert wording.value_text("percent", None, "live") == "—"


def test_target_text_and_label():
    assert wording.target_text("percent", "at_least", 90, 75) == "at least 90%"
    assert wording.target_label("percent", "at_least", 90, 75) == "at least 90% · warning below 75%"
    assert wording.target_label("count", "at_most", 0, 3) == "at most 0 · warning above 3"
    assert wording.target_label("percent", "at_least", 100, None) == "at least 100%"
    assert wording.target_text("percent", "at_least", None) == ""
    assert wording.target_label("percent", "at_least", None, None) == ""
    assert wording.target_text("percent", "at_least", 92.5) == "at least 92.5%"


def test_live_evaluation_text():
    measure = REGISTRY["evidence_collected_on_time"]
    text = wording.evaluation_text(measure, {}, "live", "percent", "at_least", 90, 75, "nightly")
    assert text.startswith("Evidence tasks due in the last 90 days")
    assert "Target: at least 90%; warning below 75%." in text
    assert text.endswith("Measured nightly by the platform from Evidence tasks.")


def test_live_evaluation_text_uses_params():
    measure = REGISTRY["deficiencies_corrected_in_time"]
    text = wording.evaluation_text(measure, {"days": 30}, "live", "percent", "at_least", 90, None, "nightly")
    assert "within 30 days" in text and "Target: at least 90%." in text


def test_logged_and_milestone_evaluation_text():
    assert wording.evaluation_text(None, None, "logged", "percent", "at_least", 95, 85, "quarterly",
                                   "Training completion (%)") == (
        "Training completion (%), entered quarterly by the responsible team. "
        "Target: at least 95%; warning below 85%."
    )
    assert wording.evaluation_text(None, None, "milestone", None, None, None, None, None) == (
        "Done or not done by the due date."
    )


def test_metric_description():
    assert wording.metric_description(REGISTRY["risk_within_appetite"], "live", "nightly") == (
        "Live from the Risk Register, measured nightly."
    )
    assert wording.metric_description(None, "logged", "quarterly") == "Logged quarterly by the responsible team."
    assert wording.metric_description(None, "milestone", None) == "Done or not done by the due date."


def test_metric_name_carries_unit_and_params():
    assert wording.metric_name(REGISTRY["risk_within_appetite"]) == "Risks within appetite (%)"
    assert wording.metric_name(REGISTRY["open_material_deficiencies"]) == "Open material deficiencies (count)"
    assert wording.metric_name(REGISTRY["deficiencies_corrected_in_time"], {"days": 60}) == (
        "Deficiencies corrected within 60 days (%)"
    )


def test_wording_shape():
    out = wording.wording(REGISTRY["controls_implemented"], "live", target=80, warn=60)
    assert set(out) == {"evaluation_method", "metric_name", "metric_description", "target_text", "target_label"}
    assert out["metric_name"] == "Controls implemented (%)"
    assert out["target_label"] == "at least 80% · warning below 60%"
    milestone = wording.wording(None, "milestone", name="Certificate", target=5)
    assert milestone["target_text"] == "" and milestone["metric_name"] == "Certificate"


# ---------------------------------------------------------------------------
# A printed value never reads better or worse than the status beside it
# ---------------------------------------------------------------------------


def test_a_value_just_short_of_its_target_keeps_a_decimal_place():
    """99.5% against "at least 100%" is At risk; "100%" beside that would contradict it."""
    assert wording.value_text("percent", 99.5, "live", 100, 90) == "99.5%"
    assert wording.value_text("percent", 99.96, "live", 100, 90) == "99.96%"
    # Nothing shorter is true: round away from the target instead of onto it.
    assert wording.value_text("percent", 99.996, "live", 100, 90) == "99.99%"
    assert wording.value_text("percent", 0.004, "live", 0, None) == "0.01%"


def test_a_value_just_past_an_at_most_target_keeps_a_decimal_place():
    assert wording.value_text("percent", 5.4, "live", 5, 10) == "5.4%"
    assert wording.value_text("percent", 10.3, "live", 5, 10) == "10.3%"


def test_the_warning_level_is_respected_as_well_as_the_target():
    assert wording.value_text("percent", 74.6, "live", 90, 75) == "74.6%"
    assert wording.value_text("percent", 89.5, "live", 90, 75) == "89.5%"


def test_a_value_clear_of_its_levels_still_prints_whole():
    assert wording.value_text("percent", 13.636, "live", 90, 75) == "14%"
    assert wording.value_text("percent", 99.5, "live", 90, 75) == "100%"
    assert wording.value_text("percent", 90, "live", 90, 75) == "90%"
    assert wording.value_text("percent", 90.4, "live", 90, 75) == "90.4%"
    assert wording.value_text("percent", 100, "live", 100, 90) == "100%"
    # No level to compare with: as before.
    assert wording.value_text("percent", 99.5, "live") == "100%"
    assert wording.value_text("percent", 99.5, "live", None, None) == "100%"


def test_counts_keep_two_places_unless_that_would_cross_a_level():
    assert wording.value_text("count", 2.5, "logged", 3, None) == "2.5"
    assert wording.value_text("count", 16, "live", 10, 20) == "16"
    assert wording.value_text("count", 2.996, "logged", 3, None) == "2.996"
    assert wording.value_text("count", 2.994, "logged", 3, None) == "2.99"


@pytest.mark.parametrize("unit", ["percent", "count"])
@pytest.mark.parametrize("target,warn", [(100, 90), (90, 75), (5, 10), (0, 3), (99.99, 99.5), (33.3, None)])
def test_the_printed_value_sits_where_the_real_one_does(unit, target, warn):
    from decimal import Decimal

    def side(number, level):
        return (number > level) - (number < level)

    for thousandths in range(0, 101_000, 7):
        value = thousandths / 1000
        printed = Decimal(wording.value_text(unit, value, "live", target, warn).rstrip("%"))
        for level in (target, warn):
            if level is not None:
                assert side(printed, Decimal(str(level))) == side(Decimal(str(value)), Decimal(str(level))), (
                    value, printed, level)
