"""Every action a workflow uses is pinned to a full commit, with its version noted.

A tag such as ``@v5`` is a pointer its publisher can move, so a workflow that
references one runs whatever the tag points at on the day. A full commit hash
cannot be moved. The trailing ``# vX.Y.Z`` comment is what lets a person, and
the update automation, see which release the hash is.

This test is the guard until the repository setting that requires pinned
actions is switched on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((REPO_ROOT / ".github" / "workflows").glob("*.y*ml"))

USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(?P<ref>\S+)(?P<rest>.*)$")
PINNED = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_./-]+)?@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"^\s+# v\d+\.\d+\.\d+$")

pytestmark = pytest.mark.skipif(not WORKFLOWS, reason="no workflows in this tree")


def _uses_lines(path: Path) -> list[tuple[int, str, str]]:
    found = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        match = USES_LINE.match(line)
        if match:
            found.append((number, match.group("ref").strip("'\""), match.group("rest")))
    return found


def _count_uses_keys(node) -> int:
    if isinstance(node, dict):
        return sum((1 if key == "uses" else 0) + _count_uses_keys(value) for key, value in node.items())
    if isinstance(node, list):
        return sum(_count_uses_keys(item) for item in node)
    return 0


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_commit_with_its_version(workflow: Path):
    problems = []
    for number, ref, rest in _uses_lines(workflow):
        if ref.startswith("./"):
            continue  # a workflow or action in this repository
        if not PINNED.match(ref):
            problems.append(f"{workflow.name}:{number}: {ref} is not pinned to a full commit hash")
        elif not VERSION_COMMENT.match(rest):
            problems.append(f"{workflow.name}:{number}: {ref} has no '# vX.Y.Z' comment")
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_the_line_scan_sees_every_uses_key(workflow: Path):
    # If a `uses:` is ever written in a form the line scan misses, the test
    # above would pass without looking at it. Compare against the parsed file.
    parsed = _count_uses_keys(yaml.safe_load(workflow.read_text()))
    assert len(_uses_lines(workflow)) == parsed
