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
def test_flag_defaults_to_off_in_compose(compose, flag):
    # A flag that defaults on when unset would change behaviour for every
    # existing deployment the moment this file lands.
    for service in ("backend", "celery-worker"):
        assert compose["services"][service]["environment"][flag].endswith(":-false}")


@pytest.mark.parametrize("flag", FLAGS)
def test_flag_is_documented_with_its_default(flag):
    # A name appearing somewhere in the prose is not documentation an
    # operator can copy — the commented assignment is.
    assert f"# {flag}=false" in (REPO_ROOT / ".env.example").read_text()


def test_both_services_agree_on_the_expression(compose):
    # Same default, same variable name. Two services resolving the same
    # flag differently is worse than neither forwarding it.
    backend = compose["services"]["backend"]["environment"]
    worker = compose["services"]["celery-worker"]["environment"]
    for flag in FLAGS:
        assert backend[flag] == worker[flag]
