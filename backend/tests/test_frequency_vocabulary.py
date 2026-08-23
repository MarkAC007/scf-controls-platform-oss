"""Frequency vocabulary parity + regression tests (#783).

These tests exist because the same concept was declared in four places that
disagreed with each other. Each test below asserts one of those declarations
still agrees with the source of truth, so the next person who adds a value to
one map and forgets the other gets a red build instead of a silent 12.3x error
in a customer's freshness dashboard.

All tests are pure unit tests — no database, no fixtures, no network.
"""
import ast
import re
from pathlib import Path

import pytest

from services import frequency_vocabulary as fv

REPO_ROOT = Path(__file__).resolve().parents[2]
TS_MODULE = REPO_ROOT / "webclient" / "src" / "data" / "frequencyVocabulary.ts"

# The backend container mounts only ./backend:/app (docker-compose.yml), so the
# webclient tree is absent under `docker compose exec backend pytest`. Skipping
# is correct there; CI runs a full checkout and these must not be skipped, which
# `test_typescript_module_present_in_full_checkout` below asserts.
_HAS_FRONTEND = (REPO_ROOT / "webclient" / "src").is_dir()
requires_frontend = pytest.mark.skipif(
    not _HAS_FRONTEND, reason="webclient tree not mounted (backend-only container)"
)


# ---------------------------------------------------------------------------
# Internal consistency of the source of truth
# ---------------------------------------------------------------------------
def test_staleness_covers_every_canonical_value():
    assert set(fv.STALENESS_DAYS) == set(fv.CANONICAL_FREQUENCIES)


def test_task_interval_covers_every_canonical_value():
    assert set(fv.TASK_INTERVAL_DAYS) == set(fv.CANONICAL_FREQUENCIES)


def test_every_staleness_value_is_a_positive_int():
    for freq, days in fv.STALENESS_DAYS.items():
        assert isinstance(days, int) and days > 0, freq


def test_every_alias_resolves_to_a_canonical_value():
    for alias, target in fv.ALIASES.items():
        assert target in fv.CANONICAL_FREQUENCIES, f"{alias} -> {target}"


def test_no_alias_shadows_a_canonical_value():
    """An alias keyed by a canonical value would make normalize() ambiguous."""
    assert not (set(fv.ALIASES) & set(fv.CANONICAL_FREQUENCIES))


def test_staleness_exceeds_task_interval_for_every_scheduled_cadence():
    """Staleness must allow grace beyond the collection interval, or a
    collection that runs exactly on schedule flickers amber on its due date."""
    for freq, interval in fv.TASK_INTERVAL_DAYS.items():
        if interval is None:
            continue
        assert fv.STALENESS_DAYS[freq] > interval, freq


# ---------------------------------------------------------------------------
# The two live defects from #783
# ---------------------------------------------------------------------------
def test_annually_normalises_to_annual():
    assert fv.normalize("Annually") == "annual"


def test_annual_staleness_is_370_not_30():
    """The headline defect: the wizard emitted 'annually', STALENESS_THRESHOLDS
    only had 'annual', and evidence_health fell through to a 30-day default —
    a 12.3x error marking every annual control overdue for 11 months a year."""
    assert fv.staleness_days("annually") == 370
    assert fv.staleness_days("Annually") == 370
    assert fv.staleness_days("annual") == 370


def test_real_time_is_recognised_and_explicitly_non_scheduling():
    """Second defect: 'real_time' had no key in the task-generation map, so the
    record was skipped via an 'Invalid frequency' warning. The outcome (no task)
    was right; calling it invalid was the lie."""
    assert fv.normalize("real_time") == "real_time"
    assert fv.task_interval_days("real_time") is None
    assert fv.is_time_based("real_time") is False


def test_on_demand_underscore_and_space_both_resolve():
    """SKIP_FREQUENCIES held 'on demand' with a space while the threshold map
    held 'on_demand' with an underscore, so one of the two always missed."""
    assert fv.normalize("on_demand") == "on_demand"
    assert fv.normalize("on demand") == "on_demand"
    assert fv.is_time_based("on_demand") is False


# ---------------------------------------------------------------------------
# normalize() behaviour
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Monthly  ", "monthly"),
        ("QUARTERLY", "quarterly"),
        ("Weekly.", "weekly"),
        ("Yearly", "annual"),
        ("semi-annually", "semi_annual"),
        ("Semi Annual", "semi_annual"),
        ("bi-weekly", "biweekly"),
        ("Fortnightly", "biweekly"),
        ("as needed", "on_demand"),
        ("Continuous", "real_time"),
    ],
)
def test_normalize_absorbs_historical_spellings(raw, expected):
    assert fv.normalize(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "whenever", "every so often", "42"])
def test_normalize_returns_none_for_unrecognised(raw):
    assert fv.normalize(raw) is None


def test_staleness_days_returns_none_rather_than_a_default():
    """`.get(...) or DEFAULT` was the shape that made #783 invisible. An
    unrecognised value must surface as None, not as a plausible 30 days."""
    assert fv.staleness_days("whenever") is None


# ---------------------------------------------------------------------------
# The module is importable inside a Celery worker without S3 deps
# ---------------------------------------------------------------------------
def test_module_imports_only_stdlib():
    """composite_service inlined its own copy of the thresholds specifically to
    avoid validation_service's transitive boto3/storage imports. That reason is
    only satisfied while this module stays stdlib-only."""
    source = Path(fv.__file__).read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    forbidden = {"boto3", "sqlalchemy", "models", "database", "services", "pydantic", "fastapi"}
    assert not (imported & forbidden), f"non-stdlib imports: {imported & forbidden}"


# ---------------------------------------------------------------------------
# Cross-subsystem parity — the class the epic named
# ---------------------------------------------------------------------------
def test_validation_service_thresholds_match_source_of_truth():
    from services.validation_service import STALENESS_THRESHOLDS

    assert STALENESS_THRESHOLDS == fv.STALENESS_DAYS


def test_composite_service_thresholds_match_validation_service():
    """These two were a hand-maintained copy of each other before #783."""
    from services.composite_service import STALENESS_THRESHOLDS as composite
    from services.validation_service import STALENESS_THRESHOLDS as validation

    assert composite == validation


def test_task_generator_map_matches_source_of_truth():
    from services.task_generator import FREQUENCY_DAYS, NON_TASK_FREQUENCIES

    expected = {f: d for f, d in fv.TASK_INTERVAL_DAYS.items() if d is not None}
    assert FREQUENCY_DAYS == expected
    assert set(NON_TASK_FREQUENCIES) == {
        f for f, d in fv.TASK_INTERVAL_DAYS.items() if d is None
    }


# ---------------------------------------------------------------------------
# The UI dropdown is a subset of BOTH backend maps — issue #783's ask
# ---------------------------------------------------------------------------
def test_every_ui_option_has_a_staleness_threshold():
    for option in fv.UI_OPTIONS:
        assert option["value"] in fv.STALENESS_DAYS, option["value"]


def test_every_ui_option_has_a_task_interval_entry():
    for option in fv.UI_OPTIONS:
        assert option["value"] in fv.TASK_INTERVAL_DAYS, option["value"]


def test_ui_options_are_canonical_values():
    for option in fv.UI_OPTIONS:
        assert fv.normalize(option["value"]) == option["value"], option["value"]


def test_ui_options_have_unique_values_and_labels():
    values = [o["value"] for o in fv.UI_OPTIONS]
    labels = [o["label"] for o in fv.UI_OPTIONS]
    assert len(values) == len(set(values))
    assert len(labels) == len(set(labels))


# ---------------------------------------------------------------------------
# TypeScript parity — a shared module in two languages is still two
# declarations, so this is the test that keeps them one
# ---------------------------------------------------------------------------
def _parse_ts_string_array(source: str, const_name: str) -> list:
    """Extract a flat `const NAME = [ 'a', 'b' ]` array from TS source."""
    match = re.search(
        rf"export const {const_name}\s*=\s*\[(.*?)\]", source, re.S
    )
    assert match, f"{const_name} not found in {TS_MODULE}"
    return re.findall(r"'([^']+)'", match.group(1))


def _parse_ts_option_objects(source: str, const_name: str) -> list:
    match = re.search(
        rf"export const {const_name}:\s*FrequencyOption\[\]\s*=\s*\[(.*?)\n\]", source, re.S
    )
    assert match, f"{const_name} not found in {TS_MODULE}"
    return re.findall(
        r"\{\s*value:\s*'([^']+)',\s*label:\s*'([^']+)'\s*\}", match.group(1)
    )


@requires_frontend
def test_typescript_module_present_in_full_checkout():
    """Guards the guard: in a checkout that HAS the frontend, the module must
    exist. Without this, a deleted TS module would make the parity tests skip
    rather than fail."""
    assert TS_MODULE.is_file(), f"missing {TS_MODULE}"


@requires_frontend
def test_typescript_canonical_values_match_python():
    ts_values = _parse_ts_string_array(TS_MODULE.read_text(), "CANONICAL_FREQUENCIES")
    assert ts_values == list(fv.CANONICAL_FREQUENCIES)


@requires_frontend
def test_typescript_ui_options_match_python():
    """If this fails, the dropdown and the backend have diverged again — which
    is exactly how 'annually' came to be offered but never understood."""
    ts_options = _parse_ts_option_objects(TS_MODULE.read_text(), "FREQUENCY_OPTIONS")
    py_options = [(o["value"], o["label"]) for o in fv.UI_OPTIONS]
    assert ts_options == py_options


@requires_frontend
def test_no_frontend_component_declares_its_own_frequency_options():
    """The wizard used to hold its own FREQUENCY_OPTIONS literal. Anything that
    re-declares one is a third source of truth."""
    src_dir = REPO_ROOT / "webclient" / "src"
    offenders = []
    for path in src_dir.rglob("*.tsx"):
        text = path.read_text(errors="ignore")
        if re.search(r"(const|let)\s+FREQUENCY_OPTIONS\s*[:=]", text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, f"components declaring their own option list: {offenders}"


# ---------------------------------------------------------------------------
# Write-path validation — the column had no constraint and three writers
# ---------------------------------------------------------------------------
WRITER_SCHEMAS = ["EvidenceTrackingBase", "EvidenceTrackingUpdate", "BatchEvidenceTrackingOperation"]


def _writer(name):
    import schemas

    return getattr(schemas, name)


@pytest.mark.parametrize("schema_name", WRITER_SCHEMAS)
def test_writer_schema_normalises_annually(schema_name):
    kwargs = {"frequency": "Annually"}
    if schema_name != "EvidenceTrackingUpdate":
        kwargs["evidence_id"] = "E-HRS-16"
    assert _writer(schema_name)(**kwargs).frequency == "annual"


@pytest.mark.parametrize("schema_name", WRITER_SCHEMAS)
def test_writer_schema_passes_unrecognised_frequency_through(schema_name):
    """Deliberately lenient. `updateEvidenceTracking` re-sends the whole tracking
    object on EVERY field edit, so raising here would make any row holding
    pre-#783 free text permanently un-editable — including un-correctable, since
    saving the correction resends the offending value. Loudness lives on the
    read path, where it cannot brick a write."""
    kwargs = {"frequency": "Monthly on the 1st"}
    if schema_name != "EvidenceTrackingUpdate":
        kwargs["evidence_id"] = "E-HRS-16"
    assert _writer(schema_name)(**kwargs).frequency == "Monthly on the 1st"


@pytest.mark.parametrize("schema_name", WRITER_SCHEMAS)
def test_writer_schema_trims_unrecognised_frequency(schema_name):
    kwargs = {"frequency": "  Every 6 weeks  "}
    if schema_name != "EvidenceTrackingUpdate":
        kwargs["evidence_id"] = "E-HRS-16"
    assert _writer(schema_name)(**kwargs).frequency == "Every 6 weeks"


def test_unrecognised_frequency_surfaces_as_none_on_the_read_path():
    """The other half of leniency: a value the write path let through must NOT
    resolve to a plausible default downstream. That was the #783 defect."""
    assert fv.staleness_days("Monthly on the 1st") is None
    assert fv.task_interval_days("Monthly on the 1st") is None
    assert fv.is_time_based("Monthly on the 1st") is False


@pytest.mark.parametrize("schema_name", WRITER_SCHEMAS)
@pytest.mark.parametrize("blank", [None, "", "   "])
def test_writer_schema_treats_blank_as_cleared(schema_name, blank):
    kwargs = {"frequency": blank}
    if schema_name != "EvidenceTrackingUpdate":
        kwargs["evidence_id"] = "E-HRS-16"
    assert _writer(schema_name)(**kwargs).frequency is None


@pytest.mark.parametrize("schema_name", WRITER_SCHEMAS)
def test_writer_schema_accepts_the_old_free_text_placeholder_values(schema_name):
    """The removed free-text box suggested "Monthly, Quarterly, Annual".
    Every value it hinted at must still be accepted, or existing integrations
    start 422-ing on data that used to work."""
    for raw, expected in [("Monthly", "monthly"), ("Quarterly", "quarterly"), ("Annual", "annual")]:
        kwargs = {"frequency": raw}
        if schema_name != "EvidenceTrackingUpdate":
            kwargs["evidence_id"] = "E-HRS-16"
        assert _writer(schema_name)(**kwargs).frequency == expected


@pytest.mark.parametrize("schema_name", WRITER_SCHEMAS)
def test_writer_schema_accepts_every_ui_option(schema_name):
    for option in fv.UI_OPTIONS:
        kwargs = {"frequency": option["value"]}
        if schema_name != "EvidenceTrackingUpdate":
            kwargs["evidence_id"] = "E-HRS-16"
        assert _writer(schema_name)(**kwargs).frequency == option["value"]



# ---------------------------------------------------------------------------
# The fifth declaration — maturity scoring keyed off the raw string (found by
# adversarial review of this PR, not by the original issue)
# ---------------------------------------------------------------------------
def test_maturity_scores_cover_every_canonical_frequency():
    from services.maturity import FREQUENCY_SCORES

    assert set(FREQUENCY_SCORES) == set(fv.CANONICAL_FREQUENCIES)


def test_maturity_l5_frequencies_are_canonical():
    from services.maturity import L5_FREQUENCIES

    for freq in L5_FREQUENCIES:
        assert freq in fv.CANONICAL_FREQUENCIES, freq


def test_maturity_scores_annually_the_same_as_annual():
    """Before #783 this map keyed off `(frequency or '').lower()`, so a row the
    wizard stored as 'annually' missed the map and scored 0 — the same modifier
    as monthly — instead of -2."""
    from datetime import date

    from services.maturity import MaturityInput, calculate_maturity

    def score(freq):
        result = calculate_maturity(
            MaturityInput(
                is_tracked=True,
                method_of_collection="automated",
                frequency=freq,
                last_collection_date=date.today(),
            )
        )
        return result.factors.get("frequency", {}).get("modifier")

    assert score("annually") == score("annual") == -2
    assert score("Yearly") == -2


def test_task_generator_no_longer_exports_skip_frequencies():
    """SKIP_FREQUENCIES was removed rather than redefined: it would have kept
    its name while matching none of its old values, so a consumer doing
    `if raw in SKIP_FREQUENCIES` would silently start generating tasks for
    on-demand evidence. An ImportError is the honest failure."""
    import services.task_generator as tg

    assert not hasattr(tg, "SKIP_FREQUENCIES")


def test_every_alias_is_reachable_through_normalize():
    """`weekly.` and `quarterly.` were dead entries — normalize() strips the
    trailing full stop before any lookup, so they could never match."""
    for alias, target in fv.ALIASES.items():
        assert fv.normalize(alias) == target, f"unreachable alias: {alias!r}"
