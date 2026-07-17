"""Unit tests for the backend-side migration guard (upgrade design Part E).

These exercise the pure decision logic (``evaluate_guard``) and the
``apply_guard_decision`` fail-closed behaviour without touching a database — the
DB-facing wrappers gather these same inputs from the live schema at runtime.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upgrade_guard  # noqa: E402
from upgrade_guard import (  # noqa: E402
    GuardDecision,
    apply_guard_decision,
    compare_versions,
    evaluate_guard,
    parse_version,
    read_build_info,
)


# ---------------------------------------------------------------------------
# semver helpers
# ---------------------------------------------------------------------------
def test_parse_version_strips_leading_v_and_pads():
    assert parse_version("v0.8.0") == (0, 8, 0)
    assert parse_version("0.8") == (0, 8, 0)
    assert parse_version("1") == (1, 0, 0)
    assert parse_version("") == (0, 0, 0)


def test_parse_version_tolerates_prerelease_suffix():
    assert parse_version("0.9.0-rc1") == (0, 9, 0)
    assert parse_version("v1.2.3-beta") == (1, 2, 3)


def test_compare_versions_orders_correctly():
    assert compare_versions("0.6.0", "0.9.0") == -1
    assert compare_versions("0.9.0", "0.8.0") == 1
    assert compare_versions("0.8.0", "0.8.0") == 0
    assert compare_versions("0.10.0", "0.9.0") == 1  # numeric, not lexical


# ---------------------------------------------------------------------------
# Floor check
# ---------------------------------------------------------------------------
def _base(**overrides):
    """Default guard inputs (non-fresh DB at head, no ack needed) + overrides."""
    kwargs = dict(
        floor="0.0.0",
        target="0.8.0",
        last_applied="0.8.0",
        alembic_version_present=True,
        upgrade_state_present=True,
        pending=False,
        environment="production",
        migrate_ack=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_floor_refusal_names_required_stop():
    decision = evaluate_guard(**_base(last_applied="0.6.0", floor="0.9.0", pending=True))
    assert decision.permit is False
    assert decision.code == "floor"
    assert "0.9.0" in decision.message
    assert "0.6.0" in decision.message
    assert "scripts/upgrade.sh" in decision.message
    with pytest.raises(SystemExit):
        apply_guard_decision(decision)


def test_floor_pass_when_at_or_above_floor():
    decision = evaluate_guard(**_base(last_applied="0.9.0", floor="0.9.0"))
    assert decision.permit is True


def test_legacy_install_refused_when_floor_above_zero():
    # DB has migration history but no recorded platform version (pre-guard install)
    # and migrations would actually run.
    decision = evaluate_guard(
        **_base(last_applied=None, upgrade_state_present=False, floor="0.9.0", pending=True)
    )
    assert decision.permit is False
    assert decision.code == "legacy_floor"
    assert "0.9.0" in decision.message


def test_at_head_permits_regardless_of_floor():
    # No pending migrations => nothing to protect; the floor must not strand startup.
    decision = evaluate_guard(**_base(last_applied="0.6.0", floor="0.9.0", pending=False))
    assert decision.permit is True
    assert decision.code == "at_head"


def test_at_head_permits_legacy_state_after_one_shot_upgrade():
    # The scripts/upgrade.sh one-shot migrates to head but records no version
    # (bare alembic CLI bypasses the guard). The next compose up sees the state
    # table present-but-empty with a real floor baked — it must permit, not
    # crash-loop a successful upgrade.
    decision = evaluate_guard(
        **_base(last_applied=None, upgrade_state_present=True, floor="0.8.0", pending=False)
    )
    assert decision.permit is True
    assert decision.code == "at_head"


def test_legacy_install_permitted_when_floor_is_zero():
    decision = evaluate_guard(
        **_base(last_applied=None, upgrade_state_present=False, floor="0.0.0", pending=False)
    )
    assert decision.permit is True


# ---------------------------------------------------------------------------
# Ack sentinel
# ---------------------------------------------------------------------------
def test_no_ack_stop_mentions_upgrade_script_and_ack_var():
    decision = evaluate_guard(
        **_base(pending=True, environment="production", migrate_ack=None)
    )
    assert decision.permit is False
    assert decision.code == "ack"
    assert "upgrade.sh" in decision.message
    assert "SCF_MIGRATE_ACK" in decision.message
    with pytest.raises(SystemExit):
        apply_guard_decision(decision)


def test_ack_accepted_when_matches_target():
    decision = evaluate_guard(
        **_base(pending=True, environment="production", migrate_ack="0.8.0", target="0.8.0")
    )
    assert decision.permit is True


def test_ack_any_accepted():
    decision = evaluate_guard(
        **_base(pending=True, environment="production", migrate_ack="any")
    )
    assert decision.permit is True


def test_ack_mismatch_refused():
    decision = evaluate_guard(
        **_base(pending=True, environment="production", migrate_ack="0.7.0", target="0.8.0")
    )
    assert decision.permit is False
    assert decision.code == "ack_mismatch"


def test_no_ack_needed_when_no_pending_migrations():
    decision = evaluate_guard(**_base(pending=False, migrate_ack=None))
    assert decision.permit is True


# ---------------------------------------------------------------------------
# Environment + fresh-install permissiveness
# ---------------------------------------------------------------------------
def test_development_is_permissive_without_ack():
    decision = evaluate_guard(
        **_base(pending=True, environment="development", migrate_ack=None)
    )
    assert decision.permit is True


def test_fresh_install_permitted():
    decision = evaluate_guard(
        floor="0.9.0",
        target="0.9.0",
        last_applied=None,
        alembic_version_present=False,
        upgrade_state_present=False,
        pending=True,
        environment="production",
        migrate_ack=None,
    )
    assert decision.permit is True
    assert decision.code == "initial_install"


def test_apply_guard_decision_permits_silently():
    # Should not raise.
    apply_guard_decision(GuardDecision(True, "ok", "fine"))


# ---------------------------------------------------------------------------
# build_info path resolution (canonical /build_info.json, /app fallback)
# ---------------------------------------------------------------------------
def test_read_build_info_explicit_path(tmp_path):
    p = tmp_path / "bi.json"
    p.write_text('{"version": "0.8.0", "build_stamp": "abc", "min_upgradable_version": "0.6.0"}')
    data = read_build_info(str(p))
    assert data["version"] == "0.8.0"
    assert data["min_upgradable_version"] == "0.6.0"


def test_read_build_info_missing_returns_none(tmp_path):
    assert read_build_info(str(tmp_path / "does-not-exist.json")) is None


def test_read_build_info_prefers_canonical_over_fallback(tmp_path, monkeypatch):
    canonical = tmp_path / "root_build_info.json"
    fallback = tmp_path / "app_build_info.json"
    monkeypatch.setattr(upgrade_guard, "BUILD_INFO_PATH", str(canonical))
    monkeypatch.setattr(upgrade_guard, "BUILD_INFO_FALLBACK_PATH", str(fallback))

    # Only the /app fallback exists → it is used.
    fallback.write_text('{"version": "0.7.0"}')
    assert read_build_info()["version"] == "0.7.0"

    # Canonical root path exists → it wins over the fallback.
    canonical.write_text('{"version": "0.9.0"}')
    assert read_build_info()["version"] == "0.9.0"
