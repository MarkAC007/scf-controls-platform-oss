"""One workflow publishes to the public repository (plan 2.2).

The legacy manual publisher, publish-oss.yml, was a second route to the public
repository that read the same token as release-oss.yml, with unpinned actions
and an injectable ref input. It was retired; release-oss.yml is the only
publisher. These tests keep a second one from appearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASE = WORKFLOWS / "release-oss.yml"

pytestmark = pytest.mark.skipif(not RELEASE.exists(), reason="release-oss.yml is private-only")


def test_the_legacy_publisher_is_gone():
    assert not (WORKFLOWS / "publish-oss.yml").exists()


def test_only_the_release_workflow_reads_the_public_repo_token():
    readers = sorted(p.name for p in WORKFLOWS.glob("*.y*ml") if "secrets.SCF_PLATFORM_OSS_SECRET" in p.read_text())
    assert readers == ["release-oss.yml"]
