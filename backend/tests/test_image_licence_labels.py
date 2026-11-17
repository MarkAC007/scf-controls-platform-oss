# SPDX-License-Identifier: AGPL-3.0-only
"""Published images carry the project's licence and public URLs in their OCI labels.

Left to its defaults, ``docker/metadata-action`` fills
``org.opencontainers.image.licenses`` from GitHub's licence detection, which
reports the stock AGPL text as the ambiguous ``AGPL-3.0``, and
``org.opencontainers.image.url`` from the repository running the workflow,
which is the private one. ``publish-oss-images.yml`` overrides both; this
pins the overrides so an edit to the label block cannot drop them quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "publish-oss-images.yml"

pytestmark = pytest.mark.skipif(
    not WORKFLOW.exists(),
    reason="publish-oss-images.yml is stripped from the OSS snapshot by scripts/prepare-oss.sh",
)

PUBLIC_REPO = "https://github.com/MarkAC007/scf-controls-platform-oss"


def _labels() -> dict[str, str]:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["build"]["steps"]
    matches = [s for s in steps if s.get("id") == "meta"]
    assert len(matches) == 1, "expected exactly one step with id 'meta'"
    pairs = (line.split("=", 1) for line in matches[0]["with"]["labels"].splitlines() if line.strip())
    return {key.strip(): value.strip() for key, value in pairs}


def test_licence_label_is_the_exact_spdx_identifier():
    assert _labels()["org.opencontainers.image.licenses"] == "AGPL-3.0-only"


@pytest.mark.parametrize("key", ["org.opencontainers.image.source", "org.opencontainers.image.url"])
def test_repository_labels_point_at_the_public_repo(key):
    assert _labels()[key] == PUBLIC_REPO


def test_vendor_label_names_the_copyright_holder():
    assert _labels()["org.opencontainers.image.vendor"] == (
        "Ginga Ninja Holdings Limited, trading as ComplianceGenie.io"
    )
