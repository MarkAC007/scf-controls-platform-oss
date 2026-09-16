"""The three assurance/KSI flags reach both containers (#787, ISC-78/79).

`docker-compose.yml` uses an explicit environment allow-list with no
`env_file:`, so a variable the code reads and `.env.example` documents still
never reaches the process unless it is named on the service. Until this PR
none of these three were, which made every one of them a control surface
wired to nothing — the same defect as #781 and #782 in a different costume.

Asserting it here means the next flag added cannot repeat it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAGS = [
    "ENABLE_PER_WINDOW_REVIEW",
    "ENABLE_WINDOW_ASSESSMENT_KSI",
    "ENABLE_COMPOSITE_KSI",
]


@pytest.fixture(scope="module")
def compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())


@pytest.mark.parametrize("flag", FLAGS)
@pytest.mark.parametrize("service", ["backend", "celery-worker"])
def test_flag_is_forwarded_to_the_service(compose, service, flag):
    environment = compose["services"][service]["environment"]
    assert flag in environment, (
        f"{flag} is not forwarded to {service}, so setting it in .env would "
        "do nothing there."
    )


@pytest.mark.parametrize("flag", FLAGS)
def test_flag_defaults_to_on_in_compose(compose, flag):
    # Window-assessment parity flipped all three on: the windowed assessment
    # is the primary surface. The compose default, the code default
    # (api/features.py FLAG_DEFAULTS) and .env.example must say the same.
    for service in ("backend", "celery-worker"):
        assert compose["services"][service]["environment"][flag].endswith(":-true}")


@pytest.mark.parametrize("flag", FLAGS)
def test_code_default_matches_compose_default(flag):
    from api.features import FLAG_DEFAULTS

    assert FLAG_DEFAULTS[flag] == "true"


def test_frontend_twin_defaults_on_everywhere_it_is_built(compose):
    # The bundle compiles VITE_ENABLE_PER_WINDOW_REVIEW; a build that leaves
    # it off against a backend that defaults on has no working review path.
    args = compose["services"]["frontend"]["build"]["args"]
    assert args["VITE_ENABLE_PER_WINDOW_REVIEW"] == "${VITE_ENABLE_PER_WINDOW_REVIEW:-true}"
    dockerfile = (REPO_ROOT / "Dockerfile.frontend").read_text()
    assert "ARG VITE_ENABLE_PER_WINDOW_REVIEW=true" in dockerfile
    assert "ENV VITE_ENABLE_PER_WINDOW_REVIEW=$VITE_ENABLE_PER_WINDOW_REVIEW" in dockerfile
    assert "# VITE_ENABLE_PER_WINDOW_REVIEW=true" in (REPO_ROOT / ".env.example").read_text()


# The GHCR publisher workflow is private-only: scripts/prepare-oss.sh drops it
# from the OSS snapshot because the registry-push credential must never ship.
# The public repo runs this same test file, so the assertion against it has to
# skip there rather than fail (v0.37.0's Release OSS run failed on exactly this).
PUBLISH_IMAGES_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"


@pytest.mark.skipif(
    not PUBLISH_IMAGES_WORKFLOW.exists(),
    reason="publish-oss-images.yml is stripped from the OSS snapshot by scripts/prepare-oss.sh",
)
def test_frontend_twin_defaults_on_in_the_image_publisher():
    # The published image is built by this workflow, not by compose, so the
    # default has to be stated there too or the GHCR image ships with it off.
    workflow = PUBLISH_IMAGES_WORKFLOW.read_text()
    assert "VITE_ENABLE_PER_WINDOW_REVIEW=true" in workflow


@pytest.mark.parametrize("flag", FLAGS)
def test_flag_is_documented_with_its_default(flag):
    # A name appearing somewhere in the prose is not documentation an
    # operator can copy — the commented assignment is.
    assert f"# {flag}=true" in (REPO_ROOT / ".env.example").read_text()


def test_both_services_agree_on_the_expression(compose):
    # Same default, same variable name. Two services resolving the same
    # flag differently is worse than neither forwarding it.
    backend = compose["services"]["backend"]["environment"]
    worker = compose["services"]["celery-worker"]["environment"]
    for flag in FLAGS:
        assert backend[flag] == worker[flag]
