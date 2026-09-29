"""Unit tests for the shared upgrade verifier.

``verify`` is the one piece of upgrade logic that both scripts/upgrade.sh and
the Kubernetes pre-sync Job call, so its pass/fail matrix is exercised here
rather than only by running a real upgrade. The database and the image-baked
build metadata are both substituted; what is under test is the decision.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scf_upgrade  # noqa: E402


HEAD = {"scopeoverride1"}
BEHIND = {"winasver1"}
BUILD_INFO = {"version": "0.40.0", "build_stamp": "abc1234"}


@pytest.fixture
def at_head(monkeypatch):
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (HEAD, HEAD))
    monkeypatch.setattr(scf_upgrade, "read_build_info", lambda: dict(BUILD_INFO))


def test_passes_when_schema_and_image_match(at_head):
    assert scf_upgrade.verify("0.40.0", "abc1234") == 0


def test_passes_with_no_expectations(at_head):
    assert scf_upgrade.verify() == 0


def test_version_prefix_is_tolerated(at_head):
    """The manifest and the tag disagree about the leading 'v'; the check does not."""
    assert scf_upgrade.verify("v0.40.0", "abc1234") == 0


def test_fails_when_schema_behind_head(monkeypatch):
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (BEHIND, HEAD))
    monkeypatch.setattr(scf_upgrade, "read_build_info", lambda: dict(BUILD_INFO))
    assert scf_upgrade.verify() == 1


def test_fails_on_empty_schema(monkeypatch):
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (set(), HEAD))
    monkeypatch.setattr(scf_upgrade, "read_build_info", lambda: dict(BUILD_INFO))
    assert scf_upgrade.verify() == 1


def test_fails_on_stale_image_version(at_head):
    assert scf_upgrade.verify("0.41.0", None) == 1


def test_release_candidate_is_not_the_release(monkeypatch):
    """The bash this replaced matched the stripped strings exactly; so must this.

    ``upgrade_guard.compare_versions`` folds ``0.40.0-rc1`` into ``0.40.0``, so
    an RC image left running would have passed a gate whose whole purpose is to
    catch the image that is not the one just built.
    """
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (HEAD, HEAD))
    monkeypatch.setattr(
        scf_upgrade,
        "read_build_info",
        lambda: {"version": "0.40.0-rc1", "build_stamp": "abc1234"},
    )
    assert scf_upgrade.verify("0.40.0", "abc1234") == 1


@pytest.mark.parametrize(
    ("running", "expected", "same"),
    [
        ("0.40.0", "0.40.0", True),
        ("v0.40.0", "0.40.0", True),
        ("0.40.0", "v0.40.0", True),
        (" v0.40.0 ", "0.40.0", True),
        ("0.40.0-rc1", "0.40.0", False),
        ("0.40.0", "0.40.0-rc1", False),
        ("0.40.1", "0.40.0", False),
        ("", "0.40.0", False),
    ],
)
def test_same_release_is_exact_after_the_v(running, expected, same):
    assert scf_upgrade.same_release(running, expected) is same


def test_fails_on_stale_build_stamp(at_head):
    assert scf_upgrade.verify(None, "deadbee") == 1


def test_missing_build_info_is_not_fatal(monkeypatch):
    """Images older than the build stamp have none; the schema check still ran."""
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (HEAD, HEAD))
    monkeypatch.setattr(scf_upgrade, "read_build_info", lambda: None)
    assert scf_upgrade.verify("0.40.0", "abc1234") == 0


def test_unstamped_image_does_not_fail_the_stamp_check(monkeypatch):
    """An absent stamp means 'cannot tell', which is not the same as 'wrong'."""
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (HEAD, HEAD))
    monkeypatch.setattr(
        scf_upgrade, "read_build_info", lambda: {"version": "0.40.0", "build_stamp": ""}
    )
    assert scf_upgrade.verify("0.40.0", "abc1234") == 0


def test_build_info_with_neither_field_warns_not_fails(monkeypatch, capsys):
    """Matches the bash 5c step this replaced: no identity to check is a warning."""
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (HEAD, HEAD))
    monkeypatch.setattr(scf_upgrade, "read_build_info", lambda: {})
    assert scf_upgrade.verify("0.40.0", "abc1234") == 0
    assert "cannot check the running image identity" in capsys.readouterr().err


def test_reports_every_failure_not_just_the_first(monkeypatch, capsys):
    monkeypatch.setattr(scf_upgrade, "_revisions", lambda: (BEHIND, HEAD))
    monkeypatch.setattr(scf_upgrade, "read_build_info", lambda: dict(BUILD_INFO))
    assert scf_upgrade.verify("0.41.0", "deadbee") == 1
    assert capsys.readouterr().err.count("FAILED") == 3


def test_cli_requires_a_subcommand():
    with pytest.raises(SystemExit):
        scf_upgrade.main([])


def test_cli_verify_passes_flags_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        scf_upgrade, "verify", lambda v, s: seen.update(version=v, stamp=s) or 0
    )
    assert scf_upgrade.main(["verify", "--expect-version", "1.2.3"]) == 0
    assert seen == {"version": "1.2.3", "stamp": None}
