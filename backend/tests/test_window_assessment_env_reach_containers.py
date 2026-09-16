"""The window-assessment knobs must reach the composed services.

`docker-compose.yml` forwards environment by explicit allow-list with no
`env_file:`, so a variable read by the code and documented in `.env.example`
is inert until both `backend` and `celery-worker` forward it (same defect
class as #782/#787). Window assessments run on the worker; the API also
imports the service, so both need every knob.
"""
import os
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
CONFIG_DOC = REPO_ROOT / "docs-site" / "src" / "content" / "docs" / "admin-guide" / "configuration.mdx"

KNOBS = {
    "ARTIFACT_TYPE_LAZY_EXTRACTION": "true",
    "WINDOW_ASSESSMENT_TEXT_BUDGET": "150000",
    "WINDOW_ASSESSMENT_ON_INGEST": "true",
    "WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS": "120",
}
SERVICES = ("backend", "celery-worker")


@pytest.fixture(scope="module")
def compose() -> dict:
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


@pytest.mark.parametrize("service", SERVICES)
@pytest.mark.parametrize("knob,default", sorted(KNOBS.items()))
def test_knob_is_forwarded_with_its_default(compose, service, knob, default):
    environment = compose["services"][service]["environment"]
    assert knob in environment, f"{knob} is not forwarded to {service}"
    # Forward the real default, not an empty string: int("") would crash the
    # worker at import and "" would silently disable lazy extraction.
    assert environment[knob] == f"${{{knob}:-{default}}}"


@pytest.mark.parametrize("knob,default", sorted(KNOBS.items()))
def test_knob_is_documented_with_its_default(knob, default):
    env_example = (REPO_ROOT / ".env.example").read_text()
    assert f"{knob}={default}" in env_example, f"{knob} missing from .env.example"


# docs-site is private-only: scripts/prepare-oss.sh removes it from the OSS
# snapshot (the published site is docs.scfcontrolsplatform.app). The public repo
# runs this same test file, so the assertion against the configuration page has
# to skip there rather than fail (v0.37.0's Release OSS run failed on exactly this).
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


def test_code_defaults_match_the_documented_defaults(monkeypatch):
    src = (REPO_ROOT / "backend" / "services" / "window_assessment_service.py").read_text()
    assert 'os.getenv("WINDOW_ASSESSMENT_TEXT_BUDGET") or "150000"' in src
    assert 'os.getenv("ARTIFACT_TYPE_LAZY_EXTRACTION") or "true"' in src

    from services import window_assessment_trigger as trig

    for knob in ("WINDOW_ASSESSMENT_ON_INGEST", "WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS"):
        monkeypatch.delenv(knob, raising=False)
    assert trig.ingest_trigger_enabled() is (KNOBS["WINDOW_ASSESSMENT_ON_INGEST"] == "true")
    assert trig.debounce_seconds() == int(KNOBS["WINDOW_ASSESSMENT_INGEST_DEBOUNCE_SECONDS"])
