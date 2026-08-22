"""What a document summary claims about itself.

A ``pending_retirement`` section is a ghost: content the generator has stopped
producing, held only so a human can dispose of it deliberately. Counting ghosts
in ``section_count`` made one Statement of Applicability report 71 sections when
33 of them were awaiting deletion -- a number that is not a count of anything
the document says. They now have a field of their own, and the headline count
is operative sections only.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import catalog_models  # noqa: E402,F401 -- registers the mappers models.py relates to
import models  # noqa: E402,F401
from api import documents as documents_api  # noqa: E402
from services.doc_gen.staleness import Staleness  # noqa: E402

DOC_ID = UUID("00000000-0000-0000-0000-0000000000aa")


class _CapturingSession:
    """Records the statement it was handed and replays a scripted result."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return SimpleNamespace(all=lambda: list(self.rows))


def _document(**overrides):
    document = SimpleNamespace(
        id=DOC_ID,
        generator_name="statement-of-applicability",
        document_type="soa",
        domain_id="",
        title="Statement of Applicability",
        lifecycle_status="draft",
        tier=1,
        is_derivative=False,
        generation_version=3,
        catalog_version="2026.1",
        input_components={},
        updated_at=None,
    )
    for key, value in overrides.items():
        setattr(document, key, value)
    return document


class TestSectionStats:
    def test_the_tallies_are_mapped_onto_the_document(self):
        row = SimpleNamespace(
            document_id=DOC_ID, total=38, conflicts=2, edited=7,
            pending_retirement=33,
        )
        db = _CapturingSession([row])
        stats = asyncio.run(documents_api._section_stats(db, [DOC_ID]))
        assert stats[DOC_ID] == {
            "total": 38, "conflicts": 2, "edited": 7, "pending_retirement": 33,
        }

    def test_retiring_sections_are_excluded_from_the_total_in_sql(self):
        # The exclusion is the fix, and it lives in the query, so this asserts
        # on the compiled SQL rather than on a hand-fed row.
        db = _CapturingSession([])
        asyncio.run(documents_api._section_stats(db, [DOC_ID]))
        sql = " ".join(str(db.statements[0]).split())
        assert "FILTER (WHERE document_sections.status != " in sql
        assert "FILTER (WHERE document_sections.status = " in sql

    def test_no_document_ids_means_no_query(self):
        db = _CapturingSession([])
        assert asyncio.run(documents_api._section_stats(db, [])) == {}
        assert db.statements == []


class TestSummary:
    def test_the_headline_count_excludes_ghosts(self):
        summary = documents_api._summary(
            _document(),
            {"total": 38, "conflicts": 2, "edited": 7, "pending_retirement": 33},
        )
        assert summary.section_count == 38
        assert summary.pending_retirement_count == 33
        assert summary.conflict_count == 2
        assert summary.edited_count == 7

    def test_missing_stats_default_to_zero(self):
        summary = documents_api._summary(_document(), {})
        assert summary.section_count == 0
        assert summary.pending_retirement_count == 0

    def test_staleness_defaults_to_not_stale_when_it_was_not_assessed(self):
        summary = documents_api._summary(_document(), {})
        assert summary.is_stale is False
        assert summary.stale_reason is None

    def test_a_staleness_verdict_is_carried_through(self):
        summary = documents_api._summary(
            _document(), {},
            Staleness(is_stale=True, reason="Scope has changed (4 added)"),
        )
        assert summary.is_stale is True
        assert summary.stale_reason == "Scope has changed (4 added)"


class TestHistoryMarksTheCurrentVersion:
    def test_is_current_matches_the_documents_generation_version(self):
        document = _document(generation_version=3)

        class _Session:
            def __init__(self):
                self._calls = 0

            async def execute(self, _stmt):
                self._calls += 1
                if self._calls == 1:
                    return SimpleNamespace(
                        scalar_one_or_none=lambda: document,
                        scalars=lambda: SimpleNamespace(all=lambda: []),
                    )
                if self._calls == 2:  # transitions
                    return SimpleNamespace(
                        scalars=lambda: SimpleNamespace(all=lambda: [])
                    )
                versions = [
                    SimpleNamespace(version=v, model_id=None, generator_version="1.0.0",
                                    input_fingerprint="f", created_at=None,
                                    # v1 predates the summary column. NULL there
                                    # means "not recorded", and the endpoint must
                                    # render it as nothing rather than as a claim
                                    # that nothing changed.
                                    change_summary=None if v == 1 else {
                                        "counts": {"updated": 2},
                                        "control_count": 41,
                                        "initial": False,
                                    })
                    for v in (1, 2, 3)
                ]
                return SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: versions)
                )

        membership = SimpleNamespace(
            user=SimpleNamespace(db_id=str(uuid4()), email="v@example.com"),
            organization_id=UUID("00000000-0000-0000-0000-000000000001"),
            role="viewer",
        )
        out = asyncio.run(documents_api.document_history(
            membership.organization_id, DOC_ID, membership, _Session(),
        ))
        assert [v["is_current"] for v in out["versions"]] == [False, False, True]
        # An unrecorded version says nothing; a recorded one says what it did.
        assert out["versions"][0]["change_description"] == ""
        assert "2 sections updated" in out["versions"][1]["change_description"]
        assert "41 controls" in out["versions"][1]["change_description"]
