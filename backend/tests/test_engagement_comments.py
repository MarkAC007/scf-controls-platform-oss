# SPDX-License-Identifier: AGPL-3.0-only
"""Behaviour of the evidence-review discussion thread (api/engagement_comments.py).

Threading, edit with history, soft delete and moderation, mentions limited to
people with engagement access, and comment ids confined to their own review.
Harness (World, FakeSession, client_for) lives in test_engagement_comment_visibility.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api.engagement_comments as ec  # noqa: E402
from test_engagement_comment_visibility import (  # noqa: E402
    ENGAGEMENT_ID,
    OTHER_ENGAGEMENT_ID,
    OTHER_EVIDENCE_ID,
    World,
    client_for,
    install,
    thread_url,
)


@pytest.fixture
def world(monkeypatch):
    w = World()
    install(monkeypatch, w)
    return w


@pytest.fixture
def people(world):
    p = SimpleNamespace()
    p.admin = world.person("Org Admin", role="admin")
    p.editor = world.person("Org Editor", role="editor")
    p.other_editor = world.person("Other Editor", role="editor")
    p.external = world.person("External Auditor", grant="external")
    p.second_auditor = world.person("Second Auditor", grant="external")
    p.admin_auditor = world.person("Admin Auditor", role="admin", grant="internal")
    p.stranger = world.person("Stranger")
    p.review = world.review()
    return p


def test_reply_inherits_parent_visibility(world, people):
    client = client_for(world, people.editor)
    parent = world.comment(people.review, people.other_editor, "org note", visibility="internal")

    reply = client.post(thread_url(), json={"content": "agreed", "parent_comment_id": str(parent.id)})
    assert reply.status_code == 201
    assert reply.json()["visibility"] == "internal"
    assert reply.json()["parent_comment_id"] == str(parent.id)

    mismatch = client.post(thread_url(), json={
        "content": "x", "parent_comment_id": str(parent.id), "visibility": "shared"})
    assert mismatch.status_code == 422


def test_new_comment_defaults_to_shared_with_shape(world, people):
    resp = client_for(world, people.editor).post(thread_url(), json={"content": "hello"})
    assert resp.status_code == 201
    body = resp.json()
    assert set(body) == {"id", "parent_comment_id", "author", "content", "visibility", "mentions",
                         "is_edited", "edited_at", "is_deleted", "created_at", "can_edit", "can_delete"}
    assert body["author"] == {"user_id": str(people.editor), "name": "Org Editor"}
    assert body["visibility"] == "shared"
    assert body["can_edit"] is True and body["can_delete"] is True
    assert world.audits and world.audits[0]["action"] == "create"


def test_author_edits_own_and_history_row_is_written(world, people):
    c = world.comment(people.review, people.editor, "first draft")
    client = client_for(world, people.editor)

    resp = client.patch(f"{thread_url()}/{c.id}", json={"content": "second draft"})
    assert resp.status_code == 200
    assert resp.json()["content"] == "second draft"
    assert resp.json()["is_edited"] is True
    assert resp.json()["edited_at"] is not None
    assert [h.old_content for h in world.history] == ["first draft"]

    history = client.get(f"{thread_url()}/{c.id}/history")
    assert history.status_code == 200
    assert [h["old_content"] for h in history.json()] == ["first draft"]
    assert history.json()[0]["edited_by"]["user_id"] == str(people.editor)


def test_edit_cannot_change_visibility(world, people):
    c = world.comment(people.review, people.editor, "note")
    resp = client_for(world, people.editor).patch(
        f"{thread_url()}/{c.id}", json={"content": "note", "visibility": "internal"})
    assert resp.status_code == 422
    assert c.visibility == "shared"


def test_non_author_cannot_edit(world, people):
    c = world.comment(people.review, people.editor, "mine")
    for other in (people.admin, people.external):
        resp = client_for(world, other).patch(f"{thread_url()}/{c.id}", json={"content": "theirs"})
        assert resp.status_code == 403
    assert c.content == "mine" and world.history == []


def test_author_soft_deletes_own_and_it_keeps_its_place_blank(world, people):
    c = world.comment(people.review, people.external, "auditor remark")
    world.comment(people.review, people.editor, "reply", parent=c)
    client = client_for(world, people.external)

    assert client.delete(f"{thread_url()}/{c.id}").status_code == 204
    assert c.is_deleted is True and c.deleted_at is not None

    thread = client.get(thread_url()).json()
    assert [t["id"] for t in thread][0] == str(c.id)
    assert thread[0]["is_deleted"] is True and thread[0]["content"] == ""
    assert thread[0]["can_edit"] is False and thread[0]["can_delete"] is False
    assert client.get(thread_url() + "/counts").json() == {"shared": 1}


def test_org_admin_soft_deletes_someone_elses(world, people):
    c = world.comment(people.review, people.external, "off-topic")
    listed = client_for(world, people.admin).get(thread_url()).json()
    assert listed[0]["can_delete"] is True and listed[0]["can_edit"] is False
    assert client_for(world, people.admin).delete(f"{thread_url()}/{c.id}").status_code == 204
    assert c.is_deleted is True


def test_auditor_cannot_delete_someone_elses(world, people):
    c = world.comment(people.review, people.editor, "org reply")
    for auditor in (people.external, people.admin_auditor):  # an org admin holding a grant too
        resp = client_for(world, auditor).delete(f"{thread_url()}/{c.id}")
        assert resp.status_code == 403
    assert c.is_deleted is False


def test_mentions_limited_to_people_with_engagement_access(world, people):
    client = client_for(world, people.editor)

    ok = client.post(thread_url(), json={
        "content": "@both", "mentions": [str(people.other_editor), str(people.external)]})
    assert ok.status_code == 201
    assert {m["user_id"] for m in ok.json()["mentions"]} == {str(people.other_editor), str(people.external)}
    assert len(world.notifications) == 1
    note = world.notifications[0]
    assert set(note["mentioned_user_ids"]) == {people.other_editor, people.external}
    assert note["review_id"] == people.review.id and note["engagement_id"] == ENGAGEMENT_ID
    assert note["author_user_id"] == people.editor

    bad = client.post(thread_url(), json={"content": "@x", "mentions": [str(people.stranger)]})
    assert bad.status_code == 422
    assert bad.json()["detail"]["rejected_user_ids"] == [str(people.stranger)]
    assert len(world.notifications) == 1

    internal = client.post(thread_url(), json={
        "content": "@org", "visibility": "internal", "mentions": [str(people.other_editor)]})
    assert internal.status_code == 201
    assert world.notifications[-1]["mentioned_user_ids"] == [people.other_editor]


def test_comment_id_from_another_review_or_engagement_is_404(world, people):
    other_review = world.review(evidence_id=OTHER_EVIDENCE_ID)
    other_engagement_review = world.review(engagement_id=OTHER_ENGAGEMENT_ID)
    foreign = [
        world.comment(other_review, people.editor, "other card"),
        world.comment(other_engagement_review, people.editor, "other engagement"),
    ]
    client = client_for(world, people.editor)
    for c in foreign:
        url = f"{thread_url()}/{c.id}"
        assert client.patch(url, json={"content": "x"}).status_code == 404
        assert client.delete(url).status_code == 404
        assert client.get(url + "/history").status_code == 404
        assert client.post(thread_url(), json={"content": "r", "parent_comment_id": str(c.id)}).status_code == 404
        assert c.is_deleted is False


def test_unknown_evidence_review_is_404(world, people):
    resp = client_for(world, people.editor).get(thread_url(evidence_id="E-NOPE-99"))
    assert resp.status_code == 404


def test_lookup_queries_are_confined_to_the_review():
    """The real data-access statements filter on type, review and id."""
    captured = []

    class _Capture:
        async def execute(self, stmt):
            captured.append(str(stmt.compile(dialect=postgresql.dialect(),
                                             compile_kwargs={"literal_binds": True})))

            class _R:
                def scalar_one_or_none(self):
                    return None

                def scalars(self):
                    return SimpleNamespace(all=lambda: [])
            return _R()

    import asyncio
    review_id, comment_id = uuid4(), uuid4()
    asyncio.run(ec._load_comment(_Capture(), review_id, comment_id))
    asyncio.run(ec._load_thread(_Capture(), review_id))
    for sql in captured:
        assert "comments.commentable_type = 'engagement_evidence_review'" in sql
        assert str(review_id) in sql
    assert str(comment_id) in captured[0]
