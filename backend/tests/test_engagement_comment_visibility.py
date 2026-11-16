"""The proof that an `internal` comment never reaches an auditor.

For an external auditor and for an internal auditor (an org member holding an
active grant), with an internal comment on the review: it is absent from every
list, count and history; editing, deleting or replying to it is the same 404
as a comment that never existed; writing `internal` is 403; mentioning an
auditor in an internal comment is 422; and the org-level /api/comments routes
refuse engagement comment types outright.

No real database: the real resolve_engagement_access and can_see_internal run
against a fake session that answers only their grant / engagement lookups from
an in-memory World; the thread's data-access helpers are swapped for
World-backed versions. The visibility decisions themselves are the real code.

This module also hosts the harness reused by test_engagement_comments.py.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Dict, List, Optional
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.audit_engagements as ae  # noqa: E402
import api.comments as org_comments  # noqa: E402
import api.engagement_comments as ec  # noqa: E402
from auth import require_auth  # noqa: E402
from database import get_db  # noqa: E402
from models import (  # noqa: E402
    AuditEngagement,
    Comment,
    CommentHistory,
    EngagementAuditor,
    EngagementEvidenceReview,
)


# =============================================================================
# Harness
# =============================================================================

ORG_ID = uuid4()
ENGAGEMENT_ID = uuid4()
OTHER_ENGAGEMENT_ID = uuid4()
EVIDENCE_ID = "E-IAC-07"
OTHER_EVIDENCE_ID = "E-GOV-01"


class _Scalar:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


@dataclass
class World:
    members: Dict[UUID, str] = field(default_factory=dict)              # user -> org role
    grants: Dict[UUID, tuple] = field(default_factory=dict)             # user -> (kind, status)
    names: Dict[UUID, str] = field(default_factory=dict)
    reviews: List[SimpleNamespace] = field(default_factory=list)
    comments: List[Comment] = field(default_factory=list)
    history: List[CommentHistory] = field(default_factory=list)
    notifications: List[dict] = field(default_factory=list)
    audits: List[dict] = field(default_factory=list)
    _clock: datetime = field(default_factory=lambda: datetime(2026, 11, 1, 9, 0))

    def person(self, name, *, role=None, grant=None) -> UUID:
        uid = uuid4()
        self.names[uid] = name
        if role:
            self.members[uid] = role
        if grant:
            self.grants[uid] = (grant, "active")
        return uid

    def review(self, engagement_id=ENGAGEMENT_ID, evidence_id=EVIDENCE_ID) -> SimpleNamespace:
        r = SimpleNamespace(id=uuid4(), engagement_id=engagement_id, organization_id=ORG_ID,
                            evidence_id=evidence_id, status="open")
        self.reviews.append(r)
        return r

    def tick(self) -> datetime:
        self._clock += timedelta(minutes=1)
        return self._clock

    def comment(self, review, author, content, visibility="shared", parent=None, **extra) -> Comment:
        c = Comment(
            id=uuid4(), commentable_type=EngagementEvidenceReview.COMMENTABLE_TYPE,
            commentable_id=review.id, user_id=author, content=content, visibility=visibility,
            parent_comment_id=parent.id if parent else None, mentions=[],
            is_edited=False, is_deleted=False, created_at=self.tick(), **extra,
        )
        self.comments.append(c)
        return c


class FakeSession:
    """Answers only the access-resolution lookups; records writes into the World."""

    def __init__(self, world: World, caller_id: Optional[UUID]):
        self.world = world
        self.caller_id = caller_id
        self.commits = 0

    async def execute(self, stmt, *_a, **_k):
        desc = stmt.column_descriptions[0]
        entity, name = desc.get("entity"), desc.get("name")
        if entity is AuditEngagement and name == "id":
            return _Scalar(ENGAGEMENT_ID)
        if entity is EngagementAuditor and name in ("id", "kind"):
            grant = self.world.grants.get(self.caller_id)
            if grant and grant[1] == "active":
                return _Scalar(uuid4() if name == "id" else grant[0])
            return _Scalar(None)
        raise AssertionError(f"FakeSession: unexpected statement {stmt}")

    def add(self, obj):
        if isinstance(obj, Comment):
            obj.id = obj.id or uuid4()
            obj.created_at = obj.created_at or self.world.tick()
            self.world.comments.append(obj)
        elif isinstance(obj, CommentHistory):
            obj.id = obj.id or uuid4()
            obj.edited_at = obj.edited_at or self.world.tick()
            self.world.history.append(obj)
        else:
            raise AssertionError(f"FakeSession: unexpected add {obj!r}")

    async def flush(self):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, _obj):
        pass

    async def rollback(self):
        pass


def install(monkeypatch, world: World) -> None:
    """Point the thread module's data access, auth, audit and notifications at the World."""

    async def _require_auth(request, *_a, **_k):
        return request.state.test_user

    async def _verify_org_membership(org_id, user, db, min_role="viewer"):
        role = world.members.get(UUID(user.db_id)) if user.db_id else None
        if role is None:
            raise HTTPException(status_code=403, detail="not a member")
        return SimpleNamespace(role=role)

    monkeypatch.setattr(ae, "require_auth", _require_auth)
    monkeypatch.setattr(ae, "verify_org_membership", _verify_org_membership)

    async def _load_review(db, org_id, engagement_id, evidence_id):
        return next((r for r in world.reviews
                     if r.organization_id == org_id and r.engagement_id == engagement_id
                     and r.evidence_id == evidence_id), None)

    async def _load_thread(db, review_id):
        return sorted((c for c in world.comments
                       if c.commentable_type == ec.COMMENTABLE_TYPE and c.commentable_id == review_id),
                      key=lambda c: c.created_at)

    async def _load_comment(db, review_id, comment_id):
        return next((c for c in world.comments
                     if c.id == comment_id and c.commentable_type == ec.COMMENTABLE_TYPE
                     and c.commentable_id == review_id), None)

    async def _load_history(db, comment_id):
        return sorted((h for h in world.history if h.comment_id == comment_id),
                      key=lambda h: h.edited_at, reverse=True)

    async def _user_names(db, user_ids):
        return {uid: world.names[uid] for uid in user_ids if uid in world.names}

    async def _engagement_people(db, org_id, engagement_id):
        active = {uid for uid, (_k, status) in world.grants.items() if status == "active"}
        return set(world.members), active

    async def _notify(db, **kwargs):
        world.notifications.append(kwargs)
        return len(kwargs.get("mentioned_user_ids") or [])

    async def _audit(**kwargs):
        world.audits.append(kwargs)
        return []

    for name, fn in {
        "_load_review": _load_review, "_load_thread": _load_thread, "_load_comment": _load_comment,
        "_load_history": _load_history, "_user_names": _user_names,
        "_engagement_people": _engagement_people,
        "create_engagement_mention_notifications": _notify, "log_entity_changes": _audit,
    }.items():
        monkeypatch.setattr(ec, name, fn)


def client_for(world: World, caller_id: Optional[UUID]) -> TestClient:
    app = FastAPI()
    app.include_router(ec.router, prefix="/api")

    @app.middleware("http")
    async def _who(request, call_next):
        request.state.test_user = SimpleNamespace(
            db_id=str(caller_id) if caller_id else None, email="caller@example.com")
        return await call_next(request)

    async def _db():
        yield FakeSession(world, caller_id)

    app.dependency_overrides[get_db] = _db
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer test"
    return client


def thread_url(engagement_id=ENGAGEMENT_ID, evidence_id=EVIDENCE_ID) -> str:
    return (f"/api/organizations/{ORG_ID}/engagements/{engagement_id}"
            f"/evidence-reviews/{evidence_id}/comments")


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def world(monkeypatch):
    w = World()
    install(monkeypatch, w)
    return w


@pytest.fixture
def scene(world):
    """A review with one shared and one internal comment, written by an org editor."""
    s = SimpleNamespace()
    s.editor = world.person("Org Editor", role="editor")
    s.external = world.person("External Auditor", grant="external")
    s.internal_auditor = world.person("Internal Auditor", role="editor", grant="internal")
    s.review = world.review()
    s.shared = world.comment(s.review, s.editor, "Shared note")
    s.internal = world.comment(s.review, s.editor, "Org-only note", visibility="internal")
    return s


AUDITORS = ["external", "internal_auditor"]


# =============================================================================
# The auditor side
# =============================================================================

@pytest.mark.parametrize("who", AUDITORS)
def test_shared_list_excludes_internal_and_internal_list_is_404(world, scene, who):
    client = client_for(world, getattr(scene, who))
    shared = client.get(thread_url())
    assert shared.status_code == 200
    ids = [c["id"] for c in shared.json()]
    assert ids == [str(scene.shared.id)]
    assert "Org-only note" not in shared.text

    internal = client.get(thread_url(), params={"visibility": "internal"})
    assert internal.status_code == 404


@pytest.mark.parametrize("who", AUDITORS)
def test_counts_has_no_internal_key(world, scene, who):
    resp = client_for(world, getattr(scene, who)).get(thread_url() + "/counts")
    assert resp.status_code == 200
    assert resp.json() == {"shared": 1}
    assert "internal" not in resp.json()


@pytest.mark.parametrize("who", AUDITORS)
def test_history_patch_delete_of_internal_comment_are_404(world, scene, who):
    client = client_for(world, getattr(scene, who))
    url = f"{thread_url()}/{scene.internal.id}"
    never_issued = f"{thread_url()}/{uuid4()}"

    for path in (url, never_issued):
        assert client.get(path + "/history").status_code == 404
        assert client.patch(path, json={"content": "x"}).status_code == 404
        assert client.delete(path).status_code == 404

    # Identical to a comment id that was never issued.
    assert client.get(url + "/history").json() == client.get(never_issued + "/history").json()
    assert scene.internal.is_deleted is False
    assert scene.internal.content == "Org-only note"


@pytest.mark.parametrize("who", AUDITORS)
def test_reply_to_internal_is_404_and_writing_internal_is_403(world, scene, who):
    client = client_for(world, getattr(scene, who))
    before = len(world.comments)

    reply = client.post(thread_url(), json={"content": "re", "parent_comment_id": str(scene.internal.id)})
    assert reply.status_code == 404

    write = client.post(thread_url(), json={"content": "psst", "visibility": "internal"})
    assert write.status_code == 403

    assert len(world.comments) == before


@pytest.mark.parametrize("who", AUDITORS)
def test_auditor_may_still_post_shared(world, scene, who):
    resp = client_for(world, getattr(scene, who)).post(thread_url(), json={"content": "Please send v2"})
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "shared"


def test_mentioning_an_auditor_in_an_internal_comment_is_422_and_no_notification(world, scene):
    client = client_for(world, scene.editor)
    for auditor in (scene.external, scene.internal_auditor):
        resp = client.post(thread_url(), json={
            "content": "@them", "visibility": "internal", "mentions": [str(auditor)],
        })
        assert resp.status_code == 422
        assert resp.json()["detail"]["rejected_user_ids"] == [str(auditor)]
    assert world.notifications == []


@pytest.mark.parametrize("who", AUDITORS)
def test_revoked_grant_on_org_member_restores_internal(world, scene, who):
    if who == "external":
        # A revoked external auditor loses the engagement entirely: the same 404.
        world.grants[scene.external] = ("external", "revoked")
        client = client_for(world, scene.external)
        resp = client.get(thread_url())
        assert resp.status_code == 404
        assert resp.json()["detail"] == "Audit engagement not found"
        assert client.get(thread_url() + "/counts").status_code == 404
    else:
        world.grants[scene.internal_auditor] = ("internal", "revoked")
        client = client_for(world, scene.internal_auditor)
        assert client.get(thread_url() + "/counts").json() == {"shared": 1, "internal": 1}
        internal = client.get(thread_url(), params={"visibility": "internal"})
        assert [c["id"] for c in internal.json()] == [str(scene.internal.id)]


# =============================================================================
# The organisation side
# =============================================================================

def test_org_member_without_grant_sees_both(world, scene):
    client = client_for(world, scene.editor)
    assert [c["id"] for c in client.get(thread_url()).json()] == [str(scene.shared.id)]
    internal = client.get(thread_url(), params={"visibility": "internal"})
    assert internal.status_code == 200
    assert [c["content"] for c in internal.json()] == ["Org-only note"]
    assert client.get(thread_url() + "/counts").json() == {"shared": 1, "internal": 1}


@pytest.mark.asyncio
async def test_can_see_internal_is_the_one_rule(world, scene):
    def access(uid, is_auditor, role):
        return ae.EngagementAccess(user=SimpleNamespace(db_id=str(uid)), organization_id=ORG_ID,
                                   engagement_id=ENGAGEMENT_ID, is_auditor=is_auditor, role=role)

    assert await ae.can_see_internal(FakeSession(world, scene.editor), access(scene.editor, False, "editor"))
    assert not await ae.can_see_internal(FakeSession(world, scene.external), access(scene.external, True, "auditor"))
    # resolve_engagement_access reports an internal auditor as a member
    # (is_auditor=False, their org role); the grant is what hides internal.
    assert not await ae.can_see_internal(
        FakeSession(world, scene.internal_auditor), access(scene.internal_auditor, False, "editor"))


@pytest.mark.asyncio
async def test_resolve_access_reports_internal_auditor_as_member(world, scene):
    user = SimpleNamespace(db_id=str(scene.internal_auditor))
    acc = await ae.resolve_engagement_access(user, ORG_ID, ENGAGEMENT_ID,
                                             FakeSession(world, scene.internal_auditor))
    assert acc.is_auditor is False
    assert acc.role == "editor"


# =============================================================================
# Org-level /api/comments refuses engagement comment types
# =============================================================================

class _OrgCommentsSession:
    def __init__(self, comment):
        self.comment = comment

    async def execute(self, stmt, *_a, **_k):
        desc = stmt.column_descriptions[0]
        if desc.get("entity") is Comment and desc.get("name") == "Comment":
            return _Scalar(self.comment)
        raise AssertionError(f"guard should refuse before {stmt}")


@pytest.fixture
def org_comments_client(monkeypatch):
    def _build(comment):
        app = FastAPI()
        app.include_router(org_comments.router)

        async def _user():
            return SimpleNamespace(db_id=str(uuid4()), email="a@example.com")

        async def _db():
            yield _OrgCommentsSession(comment)

        async def _orgs(*_a, **_k):
            return [ORG_ID]

        monkeypatch.setattr(org_comments, "get_accessible_org_ids", _orgs)
        app.dependency_overrides[require_auth] = _user
        app.dependency_overrides[get_db] = _db
        return TestClient(app)
    return _build


ENGAGEMENT_TYPES = ["engagement_query", "engagement_evidence_review"]


@pytest.mark.parametrize("ctype", ENGAGEMENT_TYPES)
def test_org_comment_routes_404_for_engagement_types(org_comments_client, ctype):
    comment = Comment(id=uuid4(), commentable_type=ctype, commentable_id=uuid4(),
                      user_id=uuid4(), content="secret", visibility="internal")
    client = org_comments_client(comment)

    listed = client.get("/api/comments", params={"commentable_type": ctype, "commentable_id": str(uuid4())})
    assert listed.status_code == 404
    assert client.patch(f"/api/comments/{comment.id}", json={"content": "x"}).status_code == 404
    assert client.delete(f"/api/comments/{comment.id}").status_code == 404
    assert client.get(f"/api/comments/{comment.id}/history").status_code == 404
    assert comment.content == "secret" and not comment.is_deleted


@pytest.mark.parametrize("ctype", ENGAGEMENT_TYPES)
def test_org_comment_create_refuses_engagement_types(org_comments_client, ctype):
    client = org_comments_client(None)
    resp = client.post("/api/comments", json={
        "commentable_type": ctype, "commentable_id": str(uuid4()), "content": "x",
    })
    # CommentCreate's pattern (schemas.py, outside this change) rejects the type
    # at validation, before the handler runs.
    assert resp.status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize("ctype", ENGAGEMENT_TYPES)
async def test_org_comment_create_handler_guard_is_404(ctype):
    from schemas import CommentCreate
    data = CommentCreate.model_construct(commentable_type=ctype, commentable_id=uuid4(),
                                         content="x", mentions=[], parent_comment_id=None)
    with pytest.raises(HTTPException) as exc:
        await org_comments.create_comment(
            request=None, comment_data=data,
            current_user=SimpleNamespace(db_id=str(uuid4())), db=None,
        )
    assert exc.value.status_code == 404
