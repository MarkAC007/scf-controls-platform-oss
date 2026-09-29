"""The Jev knobs must reach the composed services.

`docker-compose.yml` forwards environment by explicit allow-list with no
`env_file:`, so a variable read by the code and documented in `.env.example`
is inert until both `backend` and `celery-worker` forward it (same defect
class as #782/#787). The worker makes the Jev calls; the API reports the
cutoff on the engine card, so both need every knob — a cutoff the operator
tunes from the shadow statistics has to be the cutoff the worker applies.
"""
import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CONFIG_DOC = REPO_ROOT / "docs-site" / "src" / "content" / "docs" / "admin-guide" / "configuration.mdx"

# Knobs with a default the code falls back to; the compose default must be
# the same one so a blank .env behaves exactly like an unset variable.
KNOBS = {
    "JEV_CONFIDENCE_CUTOFF": "0.85",
    "JEV_STATE_CHAR_BUDGET": "90000",
}
# Optional values with no default: forwarded empty, documented blank.
OPTIONAL = ("TYPESAFE_API_KEY", "TYPESAFE_API_URL", "EVIDENCE_JEV_MODEL")
SERVICES = ("backend", "celery-worker")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("knob,default", sorted(KNOBS.items()))
def test_knob_is_forwarded_with_its_default(compose, service, knob, default):
    environment = compose["services"][service]["environment"]
    assert knob in environment, f"{knob} is not forwarded to {service}"
    assert environment[knob] == f"${{{knob}:-{default}}}"


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("name", OPTIONAL)
def test_optional_value_is_forwarded_empty(compose, service, name):
    environment = compose["services"][service]["environment"]
    assert environment.get(name) == f"${{{name}:-}}", f"{name} is not forwarded to {service}"


@pytest.mark.parametrize("knob,default", sorted(KNOBS.items()))
def test_knob_is_documented_with_its_default(knob, default):
    env_example = (REPO_ROOT / ".env.example").read_text()
    assert f"{knob}={default}" in env_example, f"{knob} missing from .env.example"


@pytest.mark.parametrize("name", OPTIONAL)
def test_optional_value_is_documented(name):
    env_example = (REPO_ROOT / ".env.example").read_text()
    assert re.search(rf"^{name}=$", env_example, re.MULTILINE), f"{name} missing from .env.example"


def test_code_defaults_match_the_documented_defaults(monkeypatch):
    from services import jev_assessment as ja

    monkeypatch.delenv(ja.CONFIDENCE_CUTOFF_ENV, raising=False)
    monkeypatch.delenv(ja.STATE_CHAR_BUDGET_ENV, raising=False)
    assert ja.confidence_cutoff() == float(KNOBS["JEV_CONFIDENCE_CUTOFF"])
    assert ja.state_char_budget() == int(KNOBS["JEV_STATE_CHAR_BUDGET"])
    # And the env names the compose file forwards are the ones the code reads.
    assert ja.CONFIDENCE_CUTOFF_ENV == "JEV_CONFIDENCE_CUTOFF"
    assert ja.STATE_CHAR_BUDGET_ENV == "JEV_STATE_CHAR_BUDGET"


# docs-site is private-only: scripts/prepare-oss.sh removes it from the OSS
# snapshot, so the assertion against the configuration page skips there.
@pytest.mark.skipif(
    not CONFIG_DOC.exists(),
    reason="docs-site is stripped from the OSS snapshot by scripts/prepare-oss.sh",
)
@pytest.mark.parametrize("knob,default", sorted(KNOBS.items()))
def test_knob_is_in_the_configuration_table_with_its_default(knob, default):
    config = CONFIG_DOC.read_text()
    assert re.search(rf"\| `{knob}` \| `{re.escape(default)}` \|", config), (
        f"{knob} is not in the configuration table with default {default}"
    )
