"""Re-scope staleness and scope-aware evidence gaps.

Scope changes keep a control's implementation_status/maturity_level (Mark's
call: "keep, mark stale"). The staleness rule is a pure function on two
timestamps; the stamps are set by the scoping service (bulk scope, override,
migration) and by the direct scoped-control write paths in api/scoped_controls.

The evidence-gaps endpoint used to iterate tracking rows and report a gap only
where a system claimed a capability, so an org with many untracked
requirements could read "0 gaps". It now derives the universe from the in-scope
controls' evidence_requests.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import auth as auth_module  # noqa: E402
from auth import OrgMembership, require_auth  # noqa: E402
from database import get_db  # noqa: E402
from models import ScopedControl, assessment_is_stale  # noqa: E402
from api.scoped_controls import _stamp_rescope_and_assessment  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
AUTH = {"Authorization": "Bearer test-key"}
T0 = datetime(2026, 9, 1, 12, 0, 0)
T1 = T0 + timedelta(days=1)


class TestAssessmentIsStale:
    def _stale(self, **kw):
        base = dict(selected=True, implementation_status="implemented", maturity_level=None,
                    scope_restored_at=T1, assessment_recorded_at=T0)
        base.update(kw)
        return assessment_is_stale(**base)

    def test_assessed_before_restore_is_stale(self):
        assert self._stale() is True

    def test_never_assessed_since_restore_is_stale(self):
        assert self._stale(assessment_recorded_at=None) is True

    def test_assessed_after_restore_is_fresh(self):
        assert self._stale(assessment_recorded_at=T1 + timedelta(minutes=1)) is False

    def test_never_restored_is_fresh(self):
        assert self._stale(scope_restored_at=None) is False

    def test_out_of_scope_is_never_stale(self):
        assert self._stale(selected=False) is False

    def test_nothing_to_be_stale_about(self):
        assert self._stale(implementation_status=None, maturity_level=None) is False

    def test_maturity_alone_counts(self):
        assert self._stale(implementation_status=None, maturity_level="L2") is True

    def test_model_property_uses_the_rule(self):
        control = ScopedControl(
            organization_id=ORG_ID, scf_id="ctl", selected=True,
            implementation_status="implemented",
            scope_restored_at=T1, assessment_recorded_at=T0,
        )
        assert control.assessment_stale is True
        control.assessment_recorded_at = T1 + timedelta(seconds=1)
        assert control.assessment_stale is False


class TestDirectWriteStamps:
    def _control(self, **kw):
        base = dict(organization_id=ORG_ID, scf_id="ctl", selected=True,
                    implementation_status="in_progress", maturity_level="L1")
        base.update(kw)
        return ScopedControl(**base)

    def test_status_change_stamps_assessment(self):
        c = self._control()
        old = {"implementation_status": "in_progress", "maturity_level": "L1", "selected": True}
        _stamp_rescope_and_assessment(c, old, {"implementation_status": "implemented"})
        assert c.assessment_recorded_at is not None
        assert c.scope_restored_at is None

    def test_maturity_change_stamps_assessment(self):
        c = self._control()
        old = {"implementation_status": "in_progress", "maturity_level": "L1", "selected": True}
        _stamp_rescope_and_assessment(c, old, {"maturity_level": "L3"})
        assert c.assessment_recorded_at is not None

    def test_same_value_does_not_stamp(self):
        c = self._control()
        old = {"implementation_status": "in_progress", "maturity_level": "L1", "selected": True}
        _stamp_rescope_and_assessment(c, old, {"implementation_status": "in_progress", "notes": "x"})
        assert c.assessment_recorded_at is None

    def test_reselect_stamps_scope_restored(self):
        c = self._control(selected=True)
        old = {"implementation_status": "in_progress", "maturity_level": "L1", "selected": False}
        _stamp_rescope_and_assessment(c, old, {"selected": True})
        assert c.scope_restored_at is not None
        assert c.assessment_recorded_at is None

    def test_deselect_does_not_stamp(self):
        c = self._control(selected=False)
        old = {"implementation_status": "in_progress", "maturity_level": "L1", "selected": True}
        _stamp_rescope_and_assessment(c, old, {"selected": False})
        assert c.scope_restored_at is None

    def test_rescope_and_reassess_in_one_write_is_fresh(self):
        c = self._control(selected=True, implementation_status="implemented")
        old = {"implementation_status": "in_progress", "maturity_level": "L1", "selected": False}
        _stamp_rescope_and_assessment(c, old, {"selected": True, "implementation_status": "implemented"})
        assert c.scope_restored_at is not None and c.assessment_recorded_at is not None
        assert c.assessment_stale is False


# ---------------------------------------------------------------------------
# evidence gaps endpoint
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def fetchall(self):
        return list(self._items)

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _FakeAsyncSession:
    def __init__(self, responses: List[Any]):
        self._responses = list(responses)
        self.statements: List[Any] = []

    async def execute(self, stmt, params=None) -> _Result:
        self.statements.append(stmt)
        if not self._responses:
            raise AssertionError("FakeAsyncSession: ran out of scripted results")
        return _Result(list(self._responses.pop(0)))


@pytest.fixture
def client_factory(monkeypatch):
    app = main.app

    def _build(responses: List[Any], role: str = "viewer"):
        session = _FakeAsyncSession(responses)

        async def _override_db():
            yield session

        def _fake_user():
            user = MagicMock()
            user.db_id = str(uuid4())
            user.email = "test@example.com"
            return user

        async def _fake_require_auth(request, credentials, db):
            user = _fake_user()
            request.state.user = user
            return user

        async def _fake_verify_org_membership(org_id, user, db, min_role="viewer"):
            return OrgMembership(user=user, organization_id=org_id, role=role, is_consultant=False)

        monkeypatch.setattr(auth_module, "require_auth", _fake_require_auth)
        monkeypatch.setattr(auth_module, "verify_org_membership", _fake_verify_org_membership)
        app.dependency_overrides[require_auth] = _fake_user
        app.dependency_overrides[get_db] = _override_db
        return TestClient(app), session

    yield _build
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(require_auth, None)


GAPS = f"/api/organizations/{ORG_ID}/evidence-gaps"


def _tracking(evidence_id, is_tracked=True, system=True):
    return SimpleNamespace(evidence_id=evidence_id, is_tracked=is_tracked,
                           collecting_system="Okta" if system else None)


def _capability(evidence_id, name, status="configured", confidence="high"):
    sid = uuid4()
    return SimpleNamespace(evidence_id=evidence_id, capability_status=status,
                           confidence_level=confidence,
                           system=SimpleNamespace(id=sid, name=name))


class TestEvidenceGapsAreScopeAware:
    def test_required_but_untracked_is_a_gap_even_without_a_capable_system(self, client_factory):
        scope_rows = [("IAC-01", ["E-IAC-01", "E-IAC-02"]), ("AST-01", ["E-IAC-02"])]
        client, session = client_factory([
            scope_rows,                       # in_scope_evidence_requests
            [_tracking("E-IAC-01")],          # tracking rows
            [],                               # capabilities
        ])
        resp = client.get(GAPS, headers=AUTH)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_evidence"] == 2
        assert body["total_tracked"] == 1
        assert body["total_gaps"] == 1
        gap = body["gaps"][0]
        assert gap["evidence_id"] == "E-IAC-02"
        assert gap["required_by_controls"] == ["AST-01", "IAC-01"]
        assert gap["capable_systems"] == []
        assert "Start tracking" in gap["recommended_action"]
        assert body["coverage_percentage"] == 50.0
        assert body["tracked_not_required"] == []
        assert "scoped_controls" in str(session.statements[0])

    def test_tracked_but_unrequired_is_reported_separately_not_as_coverage(self, client_factory):
        client, _ = client_factory([
            [("IAC-01", ["E-IAC-01"])],
            [_tracking("E-IAC-01"), _tracking("E-ORPHAN")],
            [],
        ])
        body = client.get(GAPS, headers=AUTH).json()
        assert body["total_evidence"] == 1
        assert body["total_tracked"] == 1
        assert body["total_gaps"] == 0
        assert body["coverage_percentage"] == 100.0
        assert body["tracked_not_required"] == ["E-ORPHAN"]

    def test_capable_system_is_attached_and_ranked(self, client_factory):
        client, _ = client_factory([
            [("IAC-01", ["E-IAC-01"])],
            [],
            [_capability("E-IAC-01", "Okta", status="active"),
             _capability("E-IAC-01", "Jira", status="potential")],
        ])
        gap = client.get(GAPS, headers=AUTH).json()["gaps"][0]
        assert set(gap["capable_systems"]) == {"Okta", "Jira"}
        assert gap["recommended_action"].startswith("Okta is already active")

    def test_tracked_without_collecting_system_is_still_a_gap(self, client_factory):
        client, _ = client_factory([
            [("IAC-01", ["E-IAC-01"])],
            [_tracking("E-IAC-01", system=False)],
            [],
        ])
        body = client.get(GAPS, headers=AUTH).json()
        assert body["total_gaps"] == 1
        assert "collecting system" in body["gaps"][0]["recommended_action"]

    def test_empty_scope_has_no_gaps_and_full_coverage(self, client_factory):
        client, _ = client_factory([[], [_tracking("E-X")], []])
        body = client.get(GAPS, headers=AUTH).json()
        assert body["total_evidence"] == 0 and body["total_gaps"] == 0
        assert body["coverage_percentage"] == 100.0
        assert body["tracked_not_required"] == ["E-X"]
