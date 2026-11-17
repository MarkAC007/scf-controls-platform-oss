# SPDX-License-Identifier: AGPL-3.0-only
"""Tests for api.engagement_evidence_reviews — the evidence review board API.

No real Postgres: loaders are monkeypatched with hand-built BoardData, and the
few direct reads go through a scripted fake session, in the style of
tests/test_engagement_auditor_access.py. The access-isolation test drives the
real require_engagement_read dependency over HTTP with a scripted session.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.audit_engagements as ae  # noqa: E402
import api.engagement_evidence_reviews as ev  # noqa: E402
from api.audit_engagements import EngagementAccess  # noqa: E402
from database import get_db  # noqa: E402
from schemas_engagement_review import (  # noqa: E402
    EvidenceReviewerAssignment,
    EvidenceReviewStatusChange,
)
from services import engagement_evidence_review as review_service  # noqa: E402
from services.engagement_evidence_owner import EvidenceOwner  # noqa: E402
from services.engagement_evidence_review import ReviewActor  # noqa: E402

ORG_ID = uuid4()
ENGAGEMENT_ID = uuid4()
FW = "iso_27001_2022"
OTHER_FW = "aicpa_tsc_2017_2022_used_for_soc_2"

AUDITOR_ID = uuid4()
OWNER_ID = uuid4()
OTHER_AUDITOR_ID = uuid4()


# =============================================================================
# Fakes
# =============================================================================

class _Result:
    def __init__(self, value=None, rows=None):
        self._value = value
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._value

    def fetchall(self):
        return list(self._rows)

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._rows))

    def one(self):
        return self._value


class _FakeSession:
    """Returns scripted results from successive execute() calls; records adds."""

    def __init__(self, scripted=None):
        self._scripted = list(scripted or [])
        self.added = []
        self.statements = []
        self.commits = 0

    async def execute(self, stmt=None, *_a, **_k):
        self.statements.append(stmt)
        if not self._scripted:
            raise AssertionError("FakeSession: ran out of scripted results")
        return self._scripted.pop(0)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _engagement(start=date(2026, 1, 1), end=date(2026, 6, 30), frameworks=(FW, OTHER_FW)):
    return SimpleNamespace(
        id=ENGAGEMENT_ID, name="ISO surveillance 2026", status="active",
        start_date=start, end_date=end, frameworks=list(frameworks),
        created_at=datetime(2025, 12, 1),
    )


def _review(eid, status="open", reviewer=None):
    return SimpleNamespace(
        id=uuid4(), engagement_id=ENGAGEMENT_ID, organization_id=ORG_ID,
        evidence_id=eid, status=status, assigned_reviewer_user_id=reviewer,
        last_decision_by_user_id=None, last_decision_at=None, decision_note=None,
        updated_at=datetime(2026, 2, 1),
    )


def _owner(eid, user_ids=()):
    return EvidenceOwner(
        evidence_id=eid, tracking_id=None, is_tracked=bool(user_ids),
        tier="explicit" if user_ids else "org_admin", user_ids=tuple(user_ids),
        assigned_user_id=user_ids[0] if user_ids else None, owner_user_id=None,
        team_id=None, team_name=None, label="Owner" if user_ids else "Unowned → org admins",
    )


def _file(eid, uploaded, start=None, end=None):
    return {
        "id": uuid4(), "evidence_id": eid, "filename": f"{eid}-{uploaded}.pdf",
        "uploaded_at": datetime.fromisoformat(uploaded), "review_status": "not_reviewed",
        "effective_period_start": start, "effective_period_end": end,
    }


def _data(**overrides):
    """A small board.

    IAC-01 -> A.5.15, A.5.16 (ISO) ; CC6.1 (SOC2)
    GOV-01 -> 5.2 (ISO)
    HRS-01 -> A.6.1 (ISO), no evidence object
    SOC-ONLY -> CC1.1 (SOC2 only)

    E-IAC-01 supports IAC-01 (reviewer AUDITOR_ID, owner OWNER_ID)
    E-GOV-01 supports GOV-01
    E-SOC-01 supports SOC-ONLY (no ISO clause -> not on the ISO tab)
    """
    in_win = _file("E-IAC-01", "2026-02-01")
    in_win_2 = _file("E-IAC-01", "2026-03-01")
    out_win = _file("E-IAC-01", "2025-06-01")
    gov_file = _file("E-GOV-01", "2026-02-10")
    files = {"E-IAC-01": [in_win, in_win_2, out_win], "E-GOV-01": [gov_file]}
    data = ev.BoardData(
        engagement=_engagement(),
        controls={
            "IAC-01": {"scf_id": "IAC-01", "title": "Identity", "mappings": {FW: ["A.5.16", "A.5.15"], OTHER_FW: ["CC6.1"]}},
            "GOV-01": {"scf_id": "GOV-01", "title": "Governance", "mappings": {FW: ["5.2"]}},
            "HRS-01": {"scf_id": "HRS-01", "title": "HR", "mappings": {FW: ["A.6.1"]}},
            "SOC-ONLY": {"scf_id": "SOC-ONLY", "title": "SOC only", "mappings": {OTHER_FW: ["CC1.1"]}},
        },
        evidence_meta={
            "E-IAC-01": {"title": "Access review"},
            "E-GOV-01": {"title": "Policy"},
            "E-SOC-01": {"title": "SOC thing"},
        },
        evidence_controls={"E-IAC-01": ["IAC-01"], "E-GOV-01": ["GOV-01"], "E-SOC-01": ["SOC-ONLY"]},
        reviews={
            "E-IAC-01": _review("E-IAC-01", "in_progress", reviewer=AUDITOR_ID),
            "E-GOV-01": _review("E-GOV-01", "open"),
            "E-SOC-01": _review("E-SOC-01", "open"),
        },
        files_by_evidence=files,
        assessment_by_file={
            in_win["id"]: "sufficient",
            in_win_2["id"]: "partial",
            out_win["id"]: "insufficient",   # out of window: must not count
            gov_file["id"]: "pending",       # does not count
        },
        open_queries_by_evidence={"E-IAC-01": 1},
        open_queries_by_scf={"HRS-01": 2},
        owners={
            "E-IAC-01": _owner("E-IAC-01", (OWNER_ID,)),
            "E-GOV-01": _owner("E-GOV-01"),
            "E-SOC-01": _owner("E-SOC-01"),
        },
        user_names={AUDITOR_ID: "Ada Auditor"},
    )
    for k, v in overrides.items():
        setattr(data, k, v)
    return data


def _actor(*, user_id=AUDITOR_ID, auditor=True, kind="external", role=None, **kw):
    return ReviewActor(user_id=user_id, is_auditor=auditor,
                       auditor_kind=kind if auditor else None, org_role=role, **kw)


def _access(user_id=AUDITOR_ID, is_auditor=True, role="auditor"):
    return EngagementAccess(
        user=SimpleNamespace(db_id=str(user_id) if user_id else None, email="x@example.com"),
        organization_id=ORG_ID, engagement_id=ENGAGEMENT_ID,
        is_auditor=is_auditor, role=role,
    )


def _ids(cards):
    return [c["evidence_id"] for c in cards]


# =============================================================================
# Cards: framework tab, verdict, window
# =============================================================================

def test_framework_tab_excludes_cards_without_a_clause_and_lists_no_evidence_controls():
    data = _data()
    cards, fw_controls = ev.build_framework_cards(data, FW, _actor())
    assert _ids(cards) == ["E-GOV-01", "E-IAC-01"]
    assert sorted(fw_controls) == ["GOV-01", "HRS-01", "IAC-01"]
    no_ev = ev._no_evidence_controls(data, cards, fw_controls)
    assert no_ev == [{"scf_id": "HRS-01", "title": "HR", "clauses": ["A.6.1"], "open_query_count": 2}]

    iac = next(c for c in cards if c["evidence_id"] == "E-IAC-01")
    assert iac["clauses"] == ["A.5.15", "A.5.16"]
    assert iac["controls"] == [{"scf_id": "IAC-01", "title": "Identity", "clauses": ["A.5.15", "A.5.16"]}]

    soc_cards, _ = ev.build_framework_cards(data, OTHER_FW, _actor())
    assert _ids(soc_cards) == ["E-IAC-01", "E-SOC-01"]


def test_ai_verdict_worst_of_in_window_and_out_of_window_ignored():
    cards, _ = ev.build_framework_cards(_data(), FW, _actor())
    iac = next(c for c in cards if c["evidence_id"] == "E-IAC-01")
    # sufficient + partial in window -> partial; the out-of-window 'insufficient' is ignored
    assert iac["ai_verdict"] == "partial"
    assert iac["file_count"] == 3
    assert iac["in_window_count"] == 2
    gov = next(c for c in cards if c["evidence_id"] == "E-GOV-01")
    assert gov["ai_verdict"] == "not_assessed"   # only a 'pending' assessment


@pytest.mark.parametrize("statuses, expected", [
    ([], "not_assessed"),
    ([None, "pending", "processing", "unassessable", "error"], "not_assessed"),
    (["sufficient"], "sufficient"),
    (["sufficient", "partial"], "partial"),
    (["partial", "insufficient", "sufficient"], "insufficient"),
    (["error", "sufficient"], "sufficient"),
])
def test_worst_ai_verdict(statuses, expected):
    assert ev.worst_ai_verdict(statuses) == expected


def test_no_engagement_dates_counts_every_file_in_window():
    data = _data(engagement=_engagement(start=None, end=None))
    cards, _ = ev.build_framework_cards(data, FW, _actor())
    iac = next(c for c in cards if c["evidence_id"] == "E-IAC-01")
    assert iac["in_window_count"] == 3
    assert iac["ai_verdict"] == "insufficient"


def test_asserted_period_overlap_puts_a_late_upload_in_window():
    late = _file("E-GOV-01", "2026-08-01", start=date(2026, 3, 1), end=date(2026, 5, 31))
    data = _data()
    data.files_by_evidence["E-GOV-01"] = [late]
    data.assessment_by_file[late["id"]] = "insufficient"
    cards, _ = ev.build_framework_cards(data, FW, _actor())
    gov = next(c for c in cards if c["evidence_id"] == "E-GOV-01")
    assert gov["in_window_count"] == 1 and gov["ai_verdict"] == "insufficient"


def test_allowed_transitions_and_note_required_follow_the_caller():
    data = _data()
    auditor_cards, _ = ev.build_framework_cards(data, FW, _actor())
    iac = next(c for c in auditor_cards if c["evidence_id"] == "E-IAC-01")
    assert iac["allowed_transitions"] == ["action_required", "accepted"]
    assert iac["note_required"] == {"action_required": True, "accepted": False}

    org_cards, _ = ev.build_framework_cards(data, FW, _actor(auditor=False, role="editor"))
    assert all(c["allowed_transitions"] == [] for c in org_cards)


def test_owner_and_reviewer_on_card():
    cards, _ = ev.build_framework_cards(_data(), FW, _actor())
    iac = next(c for c in cards if c["evidence_id"] == "E-IAC-01")
    assert iac["reviewer"] == {"user_id": AUDITOR_ID, "name": "Ada Auditor"}
    assert iac["owner"]["user_ids"] == [OWNER_ID]
    assert iac["owner"]["tier"] == "explicit"


# =============================================================================
# Filters
# =============================================================================

def _filtered(**kw):
    actor = kw.pop("actor", _actor())
    cards, _ = ev.build_framework_cards(_data(), FW, actor)
    return _ids(ev.filter_cards(cards, actor=actor, **kw))


@pytest.mark.parametrize("kw, expected", [
    ({}, ["E-GOV-01", "E-IAC-01"]),
    ({"status": "open"}, ["E-GOV-01"]),
    ({"status": "in_progress"}, ["E-IAC-01"]),
    ({"clause": "5.2"}, ["E-GOV-01"]),
    ({"clause": "A.5.15"}, ["E-IAC-01"]),
    ({"scf_id": "IAC-01"}, ["E-IAC-01"]),
    ({"ai_verdict_filter": "partial"}, ["E-IAC-01"]),
    ({"ai_verdict_filter": "not_assessed"}, ["E-GOV-01"]),
    ({"has_open_queries": True}, ["E-IAC-01"]),
    ({"has_open_queries": False}, ["E-GOV-01"]),
    ({"reviewer": "none"}, ["E-GOV-01"]),
    ({"reviewer": str(AUDITOR_ID)}, ["E-IAC-01"]),
    ({"search": "access REV"}, ["E-IAC-01"]),     # title
    ({"search": "e-gov"}, ["E-GOV-01"]),          # evidence id
    ({"search": "iac-01"}, ["E-IAC-01"]),         # control id (and evidence id)
    ({"search": "a.5.16"}, ["E-IAC-01"]),         # clause id
    ({"search": "nothing-matches"}, []),
])
def test_filters_narrow_the_list(kw, expected):
    assert _filtered(**kw) == expected


def test_mine_for_reviewer_and_for_org_owner():
    assert _filtered(mine=True, actor=_actor()) == ["E-IAC-01"]
    owner_member = _actor(user_id=OWNER_ID, auditor=False, role="editor")
    assert _filtered(mine=True, actor=owner_member) == ["E-IAC-01"]
    # Ownership only counts for an org member, not a pure external auditor.
    owner_not_member = _actor(user_id=OWNER_ID, auditor=True, role=None)
    assert _filtered(mine=True, actor=owner_not_member) == []


def test_no_evidence_controls_filters():
    data = _data()
    cards, fw_controls = ev.build_framework_cards(data, FW, _actor())
    no_ev = ev._no_evidence_controls(data, cards, fw_controls)
    assert ev.filter_no_evidence_controls(no_ev, clause="A.6.1") == no_ev
    assert ev.filter_no_evidence_controls(no_ev, clause="5.2") == []
    assert ev.filter_no_evidence_controls(no_ev, search="hr") == no_ev
    assert ev.filter_no_evidence_controls(no_ev, has_open_queries=False) == []


# =============================================================================
# Route: list / detail / dashboard (loaders monkeypatched)
# =============================================================================

@pytest.fixture
def patched_board(monkeypatch):
    data = _data()

    async def _load(_db, _org, _eng):
        return data

    async def _build_actor(_db, access):
        return _actor() if access.is_auditor else _actor(user_id=OWNER_ID, auditor=False, role=access.role)

    monkeypatch.setattr(ev, "_load_board_data", _load)
    monkeypatch.setattr(ev, "_build_actor", _build_actor)
    return data


async def _list(db=None, access=None, **kw):
    params = dict(framework=None, status=None, clause=None, scf_id=None, ai_verdict=None,
                  has_open_queries=None, reviewer=None, search=None, mine=False)
    params.update(kw)
    return await ev.list_evidence_reviews(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, access=access or _access(),
        db=db or _FakeSession(), **params,
    )


@pytest.mark.asyncio
async def test_list_route_shape_totals_and_counts(patched_board):
    body = await _list(status="open")
    assert body["framework"] == FW   # default = first framework
    assert body["totals"] == {"evidence": 2, "controls": 3}
    assert body["counts"] == {"open": 1, "in_progress": 0, "action_required": 0, "submitted": 0, "accepted": 0}
    assert _ids(body["cards"]) == ["E-GOV-01"]
    assert body["filter_options"]["clauses"] == ["5.2", "A.5.15", "A.5.16", "A.6.1"]
    assert body["filter_options"]["scf_ids"] == ["GOV-01", "HRS-01", "IAC-01"]
    assert body["filter_options"]["reviewers"] == [{"user_id": AUDITOR_ID, "name": "Ada Auditor"}]
    assert body["viewer"]["is_auditor"] is True
    assert body["viewer"]["can_assign_reviewer"] is True
    assert body["viewer"]["can_reassign_owner"] is False
    ev.EvidenceReviewBoard.model_validate(body)


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [
    {"framework": "nist_csf_2_0"},
    {"status": "done"},
    {"ai_verdict": "great"},
    {"reviewer": "not-a-uuid"},
])
async def test_list_route_rejects_bad_params_with_422(patched_board, kw):
    with pytest.raises(HTTPException) as exc:
        await _list(**kw)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_detail_route_artifacts_and_404(patched_board):
    db = _FakeSession([_Result("Quarterly user access review")])
    detail = await ev.get_evidence_review(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
        framework=None, access=_access(), db=db,
    )
    ev.EvidenceCardDetail.model_validate(detail)
    assert detail["description"] == "Quarterly user access review"
    assert [a["in_window"] for a in detail["artifacts"]] == [True, True, False]
    assert {a["window_basis"] for a in detail["artifacts"]} == {"upload_date"}
    assert [a["ai_status"] for a in detail["artifacts"]] == ["partial", "sufficient", "insufficient"]

    with pytest.raises(HTTPException) as exc:
        await ev.get_evidence_review(
            org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-NOPE",
            framework=None, access=_access(), db=_FakeSession(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_dashboard_passes_closed_control_queries_to_the_rollup(patched_board):
    # HRS-01 has no evidence object: only closed control-level queries -> accepted.
    patched_board.open_queries_by_scf = {}
    patched_board.closed_queries_by_scf = {"HRS-01": 1}
    dash = await ev.get_review_dashboard(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, framework=FW,
        access=_access(), db=_FakeSession(),
    )
    assert {r["clause"]: r["status"] for r in dash["clause_rollup"]}["A.6.1"] == "accepted"

    patched_board.answered_queries_by_scf = {"HRS-01": 1}
    dash = await ev.get_review_dashboard(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, framework=FW,
        access=_access(), db=_FakeSession(),
    )
    assert {r["clause"]: r["status"] for r in dash["clause_rollup"]}["A.6.1"] == "open"


@pytest.mark.asyncio
async def test_detail_404_for_a_stale_card_with_no_in_scope_control(patched_board):
    # Review row exists, but its only control left scope or was retired.
    patched_board.evidence_controls.pop("E-GOV-01")
    with pytest.raises(HTTPException) as exc:
        await ev.get_evidence_review(
            org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-GOV-01",
            framework=None, access=_access(), db=_FakeSession(),
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Evidence review not found"


def test_internal_auditor_viewer_cannot_reassign_owner():
    internal = _actor(user_id=OWNER_ID, kind="internal", role="editor")
    assert ev._viewer(internal)["can_reassign_owner"] is False
    editor = _actor(user_id=OWNER_ID, auditor=False, role="editor")
    assert ev._viewer(editor)["can_reassign_owner"] is False
    admin = _actor(user_id=OWNER_ID, auditor=False, role="admin")
    assert ev._viewer(admin)["can_reassign_owner"] is True


def test_card_offers_no_accept_to_internal_auditor_on_second_accountable_team():
    first, second = uuid4(), uuid4()
    data = _data()
    owner = data.owners["E-IAC-01"]
    data.owners["E-IAC-01"] = EvidenceOwner(
        **{**owner.__dict__, "team_id": first, "team_name": "Platform", "team_ids": (first, second)}
    )
    data.reviews["E-IAC-01"].status = "submitted"
    internal = _actor(user_id=uuid4(), kind="internal", role="editor", team_ids=frozenset({second}))
    cards, _ = ev.build_framework_cards(data, FW, internal)
    iac = next(c for c in cards if c["evidence_id"] == "E-IAC-01")
    assert "accepted" not in iac["allowed_transitions"]


@pytest.mark.asyncio
async def test_dashboard_numbers_match_the_board(patched_board):
    dash = await ev.get_review_dashboard(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, framework=FW,
        access=_access(), db=_FakeSession(),
    )
    ev.ReviewDashboard.model_validate(dash)
    board = await _list()
    assert dash["evidence"]["total"] == board["totals"]["evidence"]
    assert dash["controls"]["total"] == board["totals"]["controls"]
    assert [c["scf_id"] for c in dash["no_evidence_controls"]] == [
        c["scf_id"] for c in board["no_evidence_controls"]]
    assert dash["open_queries"] == {"total": 3, "evidence": 1, "control": 2}
    assert dash["engagement"]["created_at"] == datetime(2025, 12, 1)
    assert {r["clause"]: r["status"] for r in dash["clause_rollup"]} == {
        "5.2": "open", "A.5.15": "in_progress", "A.5.16": "in_progress", "A.6.1": "open",
    }


# =============================================================================
# Route: status change
# =============================================================================

@pytest.fixture
def status_env(monkeypatch):
    """Patches the reads around the move; returns a namespace to configure it."""
    env = SimpleNamespace(
        review=_review("E-IAC-01", "in_progress", reviewer=AUDITOR_ID),
        grant_kinds={AUDITOR_ID: "external"},
        flags=(False, False),
        team_ids=frozenset(),
        responded=frozenset(),
        recorded=[],
        notified=[],
        notify_raises=False,
        in_scope={"E-IAC-01"},
    )

    async def _in_scope(_db, _eng, _org):
        return set(env.in_scope)

    async def _self_accept(_db, _access, _user_id):
        return env.team_ids, env.responded

    async def _load_engagement(_db, _org, _eng):
        return _engagement()

    async def _lock(_db, _org, _eng, evidence_id):
        if evidence_id != env.review.evidence_id:
            raise HTTPException(status_code=404, detail="Evidence review not found")
        return env.review

    async def _grant(_db, _eng, user_id):
        return env.grant_kinds.get(user_id)

    async def _flags(_db, _org, _eid, _uid):
        return env.flags

    async def _record(_db, *, review, to_status, actor_user_id, actor_kind, note, request=None):
        env.recorded.append((review.status, to_status, actor_kind, note))
        review.status = to_status

    async def _record_reviewer(_db, *, review, reviewer_user_id, actor_user_id, actor_kind, request=None):
        env.recorded.append(("reviewer", reviewer_user_id, actor_kind))
        review.assigned_reviewer_user_id = reviewer_user_id

    def _notifier(name):
        async def _n(_db, **kw):
            if env.notify_raises:
                raise RuntimeError("boom")
            env.notified.append((name, kw))
            return 1
        return _n

    async def _load(_db, _org, _eng):
        data = _data()
        data.reviews[env.review.evidence_id] = env.review
        return data

    async def _detail(_db, data, evidence_id, framework, actor):
        return {"evidence_id": evidence_id, "status": data.reviews[evidence_id].status}

    monkeypatch.setattr(ev, "_load_engagement", _load_engagement)
    monkeypatch.setattr(ev, "_lock_review", _lock)
    monkeypatch.setattr(review_service, "evidence_ids_in_scope", _in_scope)
    monkeypatch.setattr(ev, "_self_accept_context", _self_accept)
    monkeypatch.setattr(ev, "_active_grant_kind", _grant)
    monkeypatch.setattr(ev, "_load_board_data", _load)
    monkeypatch.setattr(ev, "_card_detail", _detail)
    monkeypatch.setattr(ev.owner_service, "evidence_ownership_flags", _flags)
    monkeypatch.setattr(review_service, "record_transition", _record)
    monkeypatch.setattr(review_service, "record_reviewer_assignment", _record_reviewer)
    for name in ("create_engagement_evidence_action_required_notifications",
                 "create_engagement_evidence_submitted_notifications",
                 "create_engagement_evidence_reassigned_notifications"):
        monkeypatch.setattr(ev.notification_service, name, _notifier(name))
    return env


async def _move(access, db=None, **body):
    return await ev.change_evidence_review_status(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
        payload=EvidenceReviewStatusChange(**body), request=None, framework=None,
        access=access, db=db or _FakeSession(),
    )


@pytest.mark.asyncio
async def test_move_409_when_not_in_table(status_env):
    with pytest.raises(HTTPException) as exc:
        await _move(_access(), to_status="open")
    assert exc.value.status_code == 409
    assert status_env.recorded == []


@pytest.mark.asyncio
async def test_move_409_when_card_moved_since_loaded(status_env):
    """A stale view: the caller saw Submitted, the card is now In progress.
    Accept is legal from both, so only from_status can catch it."""
    status_env.review.status = "in_progress"
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await _move(_access(), db=db, to_status="accepted", from_status="submitted")
    assert exc.value.status_code == 409
    assert exc.value.detail == "This card has moved since you loaded it; it is now In progress."
    assert status_env.recorded == []
    assert db.commits == 0
    assert status_env.notified == []


@pytest.mark.asyncio
async def test_move_with_matching_from_status_succeeds(status_env):
    status_env.review.status = "submitted"
    result = await _move(_access(), to_status="accepted", from_status="submitted")
    assert result["status"] == "accepted"
    assert status_env.recorded == [("submitted", "accepted", "auditor", None)]


@pytest.mark.asyncio
async def test_move_without_from_status_keeps_todays_behaviour(status_env):
    status_env.review.status = "submitted"
    result = await _move(_access(), to_status="accepted")
    assert result["status"] == "accepted"


def test_lock_review_takes_a_row_lock():
    import asyncio

    captured = []

    class _Capture:
        async def execute(self, stmt, *_a, **_k):
            captured.append(stmt)
            return _Result(SimpleNamespace(id=uuid4()))

    asyncio.run(ev._lock_review(_Capture(), ORG_ID, ENGAGEMENT_ID, "E-IAC-01"))
    [stmt] = captured
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert sql.rstrip().endswith("FOR UPDATE")
    assert "engagement_evidence_reviews.organization_id" in sql
    assert "engagement_evidence_reviews.evidence_id" in sql


@pytest.mark.asyncio
async def test_move_404_when_card_is_on_no_board(status_env):
    """The review row still exists, but no in-scope, non-retired control is
    supported by it: same 404 as an unknown id, nothing recorded."""
    status_env.in_scope = set()
    with pytest.raises(HTTPException) as exc:
        await _move(_access(), to_status="accepted")
    assert exc.value.status_code == 404
    assert exc.value.detail == "Evidence review not found"
    assert status_env.recorded == []


@pytest.mark.asyncio
async def test_reviewer_404_when_card_is_on_no_board(status_env):
    status_env.in_scope = set()
    status_env.grant_kinds[OTHER_AUDITOR_ID] = "external"
    with pytest.raises(HTTPException) as exc:
        await _assign(_access(), OTHER_AUDITOR_ID)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Evidence review not found"
    assert status_env.recorded == []


@pytest.mark.asyncio
async def test_move_422_when_note_missing(status_env):
    with pytest.raises(HTTPException) as exc:
        await _move(_access(), to_status="action_required")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_move_403_wrong_side(status_env):
    status_env.review.status = "action_required"
    # An external auditor cannot make the org's submit move.
    with pytest.raises(HTTPException) as exc:
        await _move(_access(), to_status="submitted", note="done", no_artifact_reason="n/a")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_org_admin_without_grant_cannot_make_an_auditor_move(status_env):
    admin = _access(user_id=OWNER_ID, is_auditor=False, role="admin")
    with pytest.raises(HTTPException) as exc:
        await _move(admin, to_status="accepted")
    assert exc.value.status_code == 403
    assert status_env.recorded == []


@pytest.mark.asyncio
async def test_internal_auditor_cannot_accept_evidence_they_own(status_env):
    status_env.grant_kinds[OWNER_ID] = "internal"
    status_env.flags = (True, False)
    internal = _access(user_id=OWNER_ID, is_auditor=False, role="editor")
    with pytest.raises(HTTPException) as exc:
        await _move(internal, to_status="accepted")
    assert exc.value.status_code == 403
    assert "Segregation of duties" in exc.value.detail

    status_env.flags = (False, True)   # on the accountable team
    with pytest.raises(HTTPException) as exc:
        await _move(internal, to_status="accepted")
    assert exc.value.status_code == 403

    status_env.flags = (False, False)  # independent internal auditor may accept
    result = await _move(internal, to_status="accepted")
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_internal_auditor_cannot_accept_their_own_organisation_response(status_env):
    """One person must not raise the action, answer it and accept the answer.
    An internal auditor who is an org editor may respond for the organisation,
    but the card they responded on has to be accepted by another auditor."""
    status_env.grant_kinds[OWNER_ID] = "internal"
    status_env.review = _review("E-IAC-01", "submitted", reviewer=AUDITOR_ID)
    status_env.responded = frozenset({status_env.review.id})
    internal = _access(user_id=OWNER_ID, is_auditor=False, role="editor")

    with pytest.raises(HTTPException) as exc:
        await _move(internal, to_status="accepted")
    assert exc.value.status_code == 403
    assert "Segregation of duties" in exc.value.detail
    assert "you submitted the organisation's response" in exc.value.detail
    assert status_env.recorded == []

    # Someone else made the latest response: the same auditor may accept.
    status_env.responded = frozenset()
    result = await _move(internal, to_status="accepted")
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_external_auditor_is_never_blocked_by_the_self_accept_rule(status_env):
    status_env.review = _review("E-IAC-01", "submitted", reviewer=AUDITOR_ID)
    status_env.flags = (True, True)
    status_env.responded = frozenset({status_env.review.id})
    result = await _move(_access(), to_status="accepted")
    assert result["status"] == "accepted"


@pytest.mark.asyncio
async def test_action_required_records_commits_and_notifies(status_env):
    db = _FakeSession()
    result = await _move(_access(), db=db, to_status="action_required", note="Need Q2 export")
    assert result["status"] == "action_required"
    assert status_env.recorded == [("in_progress", "action_required", "auditor", "Need Q2 export")]
    assert db.commits == 1
    [(name, kw)] = status_env.notified
    assert name == "create_engagement_evidence_action_required_notifications"
    assert kw["evidence_id"] == "E-IAC-01" and kw["note"] == "Need Q2 export"
    assert db.added == []   # no comment for a non-submit move


@pytest.mark.asyncio
async def test_notification_failure_does_not_fail_the_move(status_env):
    status_env.notify_raises = True
    result = await _move(_access(), to_status="action_required", note="x")
    assert result["status"] == "action_required"


def _org_editor():
    return _access(user_id=OWNER_ID, is_auditor=False, role="editor")


@pytest.mark.asyncio
async def test_submit_rejects_file_of_another_evidence_object_or_org(status_env):
    status_env.review.status = "action_required"
    foreign = uuid4()
    # The file query filters by org + evidence id, so a foreign file is simply not returned.
    db = _FakeSession([_Result(rows=[])])
    with pytest.raises(HTTPException) as exc:
        await _move(_org_editor(), db=db, to_status="submitted", note="see file",
                    artifact_file_ids=[foreign])
    assert exc.value.status_code == 422
    assert status_env.recorded == []
    assert db.commits == 0
    # The fake returns rows whatever the SQL says, so check the SQL itself.
    [stmt] = db.statements
    compiled = stmt.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "evidence_files.organization_id = %(organization_id_1)s" in sql
    assert "evidence_files.evidence_id = %(evidence_id_1)s" in sql
    assert compiled.params["organization_id_1"] == ORG_ID
    assert compiled.params["evidence_id_1"] == "E-IAC-01"

    mine, other = uuid4(), uuid4()
    db = _FakeSession([_Result(rows=[(mine, "mine.pdf")])])
    with pytest.raises(HTTPException) as exc:
        await _move(_org_editor(), db=db, to_status="submitted", note="see files",
                    artifact_file_ids=[mine, other])
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_submit_with_files_writes_one_shared_comment_and_notifies(status_env):
    status_env.review.status = "action_required"
    f1 = uuid4()
    db = _FakeSession([_Result(rows=[(f1, "q2-export.csv")])])
    result = await _move(_org_editor(), db=db, to_status="submitted",
                         note="Attached the Q2 export", artifact_file_ids=[f1])
    assert result["status"] == "submitted"
    [comment] = db.added
    assert comment.commentable_type == "engagement_evidence_review"
    assert comment.commentable_id == status_env.review.id
    assert comment.visibility == "shared"
    assert "Attached the Q2 export" in comment.content and "q2-export.csv" in comment.content
    [(name, kw)] = status_env.notified
    assert name == "create_engagement_evidence_submitted_notifications"
    assert kw["assigned_reviewer_user_id"] == AUDITOR_ID


@pytest.mark.asyncio
async def test_submit_without_files_needs_a_reason_and_records_it(status_env):
    status_env.review.status = "action_required"
    with pytest.raises(HTTPException) as exc:
        await _move(_org_editor(), to_status="submitted", note="nothing to add")
    assert exc.value.status_code == 422

    db = _FakeSession()
    await _move(_org_editor(), db=db, to_status="submitted", note="Explained",
                no_artifact_reason="Control is manual; see query reply")
    [comment] = db.added
    assert "Control is manual" in comment.content


# =============================================================================
# Route: reviewer assignment
# =============================================================================

async def _assign(access, user_id):
    return await ev.assign_evidence_reviewer(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
        payload=EvidenceReviewerAssignment(user_id=user_id), request=None,
        framework=None, access=access, db=_FakeSession(),
    )


@pytest.mark.asyncio
async def test_reviewer_must_hold_an_active_grant(status_env):
    with pytest.raises(HTTPException) as exc:
        await _assign(_access(), uuid4())   # no grant for that user
    assert exc.value.status_code == 422
    assert status_env.recorded == []


@pytest.mark.asyncio
async def test_reviewer_assignment_by_auditor_notifies_new_reviewer(status_env):
    status_env.grant_kinds[OTHER_AUDITOR_ID] = "external"
    await _assign(_access(), OTHER_AUDITOR_ID)
    assert status_env.recorded == [("reviewer", OTHER_AUDITOR_ID, "auditor")]
    [(name, kw)] = status_env.notified
    assert name == "create_engagement_evidence_reassigned_notifications"
    assert kw["new_reviewer_user_id"] == OTHER_AUDITOR_ID


@pytest.mark.asyncio
async def test_reviewer_assignment_permissions(status_env):
    editor = _access(user_id=OWNER_ID, is_auditor=False, role="editor")
    with pytest.raises(HTTPException) as exc:
        await _assign(editor, AUDITOR_ID)
    assert exc.value.status_code == 403

    admin = _access(user_id=OWNER_ID, is_auditor=False, role="admin")
    await _assign(admin, None)   # clearing is allowed, no notification
    assert status_env.recorded == [("reviewer", None, "org")]
    assert status_env.notified == []


# =============================================================================
# Route: file link
# =============================================================================

@pytest.fixture
def file_scope(monkeypatch):
    ids = {"E-IAC-01"}

    async def _in_scope(_db, _eng, _org):
        return set(ids)

    monkeypatch.setattr(review_service, "evidence_ids_in_scope", _in_scope)
    return ids


@pytest.mark.asyncio
async def test_file_route_404_when_card_is_on_no_board(file_scope):
    file_scope.clear()
    db = _FakeSession([_Result(uuid4())])   # review row exists
    with pytest.raises(HTTPException) as exc:
        await ev.get_evidence_review_file(
            org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
            file_id=uuid4(), access=_access(), db=db,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Evidence file not found"


@pytest.mark.asyncio
async def test_file_route_404_for_file_of_another_evidence_object(file_scope):
    # review exists, but the file query (org + evidence id + file id) finds nothing
    db = _FakeSession([_Result(uuid4()), _Result(None)])
    with pytest.raises(HTTPException) as exc:
        await ev.get_evidence_review_file(
            org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
            file_id=uuid4(), access=_access(), db=db,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_file_route_404_when_evidence_has_no_review():
    db = _FakeSession([_Result(None)])
    with pytest.raises(HTTPException) as exc:
        await ev.get_evidence_review_file(
            org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-OTHER",
            file_id=uuid4(), access=_access(), db=db,
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_file_route_returns_signed_link(monkeypatch, file_scope):
    file_id = uuid4()
    calls = []

    def _link(org_id, evidence_id, fid, membership):
        calls.append(membership.user.db_id)
        return f"/api/x/{fid}?token=t"

    monkeypatch.setattr(ev, "_proxy_download_url", _link)
    ef = SimpleNamespace(id=file_id, filename="export.csv")
    db = _FakeSession([_Result(uuid4()), _Result(ef)])
    body = await ev.get_evidence_review_file(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
        file_id=file_id, access=_access(), db=db,
    )
    assert body == {"download_url": f"/api/x/{file_id}?token=t", "filename": "export.csv"}
    assert calls == [str(AUDITOR_ID)]


# =============================================================================
# Events and people
# =============================================================================

@pytest.mark.asyncio
async def test_events_route_404_when_card_is_on_no_board(file_scope):
    file_scope.clear()
    db = _FakeSession([_Result(uuid4())])   # review row exists
    with pytest.raises(HTTPException) as exc:
        await ev.list_evidence_review_events(
            org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
            access=_access(), db=db,
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == "Evidence review not found"
    assert db.statements == []   # refused before the review or its history is read


@pytest.mark.asyncio
async def test_events_oldest_first_with_actor_names(file_scope):
    review_id = uuid4()
    events = [
        SimpleNamespace(id=uuid4(), event_type="transition", from_status="open", to_status="in_progress",
                        actor_user_id=AUDITOR_ID, actor_kind="auditor", note=None,
                        created_at=datetime(2026, 2, 1)),
        SimpleNamespace(id=uuid4(), event_type="reviewer_assigned", from_status="in_progress",
                        to_status="in_progress", actor_user_id=None, actor_kind="org", note=None,
                        created_at=datetime(2026, 2, 2)),
    ]
    db = _FakeSession([
        _Result(review_id),
        _Result(rows=events),
        _Result(rows=[(AUDITOR_ID, "Ada Auditor")]),
    ])
    body = await ev.list_evidence_review_events(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
        access=_access(), db=db,
    )
    assert [e["event_type"] for e in body] == ["transition", "reviewer_assigned"]
    assert body[0]["actor"] == {"user_id": AUDITOR_ID, "name": "Ada Auditor"}
    assert body[1]["actor"] is None


@pytest.mark.asyncio
async def test_review_people_has_no_email_and_falls_back_to_neutral_name():
    db = _FakeSession([
        _Result(rows=[(AUDITOR_ID, "external", "Ada Auditor"), (OTHER_AUDITOR_ID, "internal", None)]),
        _Result(rows=[(OWNER_ID, "editor", "Olu Owner")]),
    ])
    body = await ev.list_review_people(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, access=_access(), db=db,
    )
    ev.ReviewPeople.model_validate(body)
    assert {a["kind"] for a in body["auditors"]} == {"external", "internal"}
    assert body["members"] == [{"user_id": OWNER_ID, "name": "Olu Owner", "role": "editor"}]
    assert "@" not in repr(body)
    assert all("email" not in p for p in body["auditors"] + body["members"])


# =============================================================================
# No comment-derived data anywhere
# =============================================================================

def _keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _keys(v)


def test_response_models_have_no_comment_fields():
    for model in (ev.EvidenceReviewBoard, ev.EvidenceCardDetail, ev.ReviewDashboard,
                  ev.EvidenceReviewEventItem, ev.ReviewPeople, ev.ArtifactDownload):
        schema = model.model_json_schema()
        assert not [k for k in _keys(schema) if str(k).lower().startswith("comment")], model


@pytest.mark.asyncio
async def test_no_route_returns_a_comment_key(patched_board):
    board = await _list()
    detail = await ev.get_evidence_review(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, evidence_id="E-IAC-01",
        framework=None, access=_access(), db=_FakeSession([_Result("desc")]),
    )
    dash = await ev.get_review_dashboard(
        org_id=ORG_ID, engagement_id=ENGAGEMENT_ID, framework=None,
        access=_access(), db=_FakeSession(),
    )
    for body in (board, detail, dash):
        assert not [k for k in _keys(body) if str(k).lower().startswith("comment")]


# =============================================================================
# Isolation: a non-member with no grant gets the identical 404 on every route
# =============================================================================

EID = "E-IAC-01"
ROUTES = [
    ("GET", "/evidence-reviews", None),
    ("GET", f"/evidence-reviews/{EID}", None),
    ("GET", f"/evidence-reviews/{EID}/files/{uuid4()}", None),
    ("POST", f"/evidence-reviews/{EID}/status", {"to_status": "in_progress"}),
    ("PUT", f"/evidence-reviews/{EID}/reviewer", {"user_id": None}),
    ("GET", f"/evidence-reviews/{EID}/events", None),
    ("GET", "/review-people", None),
    ("GET", "/review-dashboard", None),
]


def _client(monkeypatch, scripted_factory):
    async def _auth(*_a, **_k):
        return SimpleNamespace(db_id=str(uuid4()), email="stranger@example.com")

    async def _deny(*_a, **_k):
        raise HTTPException(status_code=403, detail="not a member")

    monkeypatch.setattr(ae, "require_auth", _auth)
    monkeypatch.setattr(ae, "verify_org_membership", _deny)

    app = FastAPI()
    app.include_router(ev.router, prefix="/api")

    async def _db():
        yield _FakeSession(scripted_factory())

    app.dependency_overrides[get_db] = _db
    return TestClient(app)


@pytest.mark.parametrize("method, path, body", ROUTES)
def test_non_member_without_grant_gets_same_404_as_missing_engagement(monkeypatch, method, path, body):
    url = f"/api/organizations/{ORG_ID}/engagements/{ENGAGEMENT_ID}{path}"
    headers = {"Authorization": "Bearer test"}

    # Engagement exists, caller is not a member and holds no grant.
    client = _client(monkeypatch, lambda: [_Result(ENGAGEMENT_ID), _Result(None)])
    no_grant = client.request(method, url, json=body, headers=headers)

    # Engagement does not exist under this org.
    client = _client(monkeypatch, lambda: [_Result(None)])
    missing = client.request(method, url, json=body, headers=headers)

    assert no_grant.status_code == 404
    assert missing.status_code == 404
    assert no_grant.json() == missing.json()
