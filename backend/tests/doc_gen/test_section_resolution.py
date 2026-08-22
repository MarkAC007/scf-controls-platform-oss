"""The resolve endpoint, the generated-section slice, and the edit-status rule.

These three defects were one defect wearing three hats: a section's merge state
could be *seen* but never *settled*. ``three_layer.resolve_section`` had been
unit-tested since the feature shipped and was called by nothing; the generated
alternative a conflict was supposed to be weighed against existed only inside a
whole-document blob with no way to slice it; and the editor's save path quietly
overwrote ``pending_retirement``, so the one state a human was meant to
adjudicate was destroyed by editing an unrelated paragraph.

The endpoints are exercised by calling the route coroutines directly with a
scripted session, rather than through ``TestClient``. ``api.documents`` builds
its auth dependency inline (``Depends(require_org_role("editor"))``), so the
callable FastAPI keys ``dependency_overrides`` on is an anonymous closure with
no importable name — reaching for it would test the wiring, not the behaviour.
The lightweight-fake register is the one ``tests/test_control_composites_api.py``
established.
"""
from __future__ import annotations

import asyncio
import os
import sys
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import catalog_models  # noqa: E402,F401 -- registers the mappers models.py relates to
import models  # noqa: E402,F401
from api import documents as documents_api  # noqa: E402
from services.doc_gen.fingerprint import sha256  # noqa: E402
from services.doc_gen.three_layer import PENDING_RETIREMENT_MARKER  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
DOC_ID = UUID("00000000-0000-0000-0000-0000000000aa")


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _Section:
    """A stand-in for a ``DocumentSection`` ORM row."""

    def __init__(self, section_id, heading_text, heading_level, ordinal, *,
                 status="unchanged", human_edited=False, edited_content=None,
                 content_hash="", last_generated_hash=""):
        self.id = uuid4()
        self.section_id = section_id
        self.heading_text = heading_text
        self.heading_level = heading_level
        self.ordinal = ordinal
        self.status = status
        self.human_edited = human_edited
        self.edited_content = edited_content
        self.content_hash = content_hash
        self.last_generated_hash = last_generated_hash
        self.control_ids: List[str] = []
        self.edited_by_user_id = None
        self.edited_at = None


class _Document:
    def __init__(self, merged_content: str, sections: List[_Section], **overrides):
        self.id = DOC_ID
        self.organization_id = ORG_ID
        self.merged_content = merged_content
        self.sections = sections
        self.generator_name = "statement-of-applicability"
        self.document_type = "soa"
        self.domain_id = ""
        self.title = "Statement of Applicability"
        self.lifecycle_status = "draft"
        self.tier = 1
        self.is_derivative = False
        self.generation_version = 2
        self.catalog_version = "2026.1"
        self.input_components: Dict[str, Any] = {}
        self.updated_at = None
        for key, value in overrides.items():
            setattr(self, key, value)


class _Result:
    def __init__(self, items: List[Any]):
        self._items = items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self):
        return self

    def all(self):
        return list(self._items)


class _Session:
    """Returns scripted results in call order; records adds, deletes, commits."""

    def __init__(self, results: List[List[Any]]):
        self._results = list(results)
        self.added: List[Any] = []
        self.deleted: List[Any] = []
        self.commits = 0

    async def execute(self, _stmt):
        return _Result(self._results.pop(0) if self._results else [])

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def commit(self):
        self.commits += 1


def _membership(role="editor"):
    return SimpleNamespace(
        user=SimpleNamespace(db_id=str(uuid4()), email="editor@example.com"),
        organization_id=ORG_ID,
        role=role,
    )


_REQUEST = SimpleNamespace(headers={}, state=SimpleNamespace(), client=None)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A three-section document with one conflict and one retiring section
# ---------------------------------------------------------------------------

MERGED = (
    "# Statement of Applicability\n"
    "\n"
    "## 1. Purpose\n"
    "\n"
    "The purpose is stated here.\n"
    "\n"
    "## 2. Scope\n"
    "\n"
    "My own words about scope.\n"
    "\n"
    "## 3. Legacy Controls\n"
    "\n"
    f"{PENDING_RETIREMENT_MARKER}\n"
    "\n"
    "Text about controls that left scope.\n"
)

GENERATED_V2 = (
    "# Statement of Applicability\n"
    "\n"
    "## 1. Purpose\n"
    "\n"
    "The purpose is stated here.\n"
    "\n"
    "## 2. Scope\n"
    "\n"
    "The generator's words about scope.\n"
)


#: Section ids are hierarchical and derived from the document tree, so every
#: ``##`` heading sits under the title's ``#``. Using bare slugs here would make
#: the fixture parse to ids the routes could never find -- which is exactly the
#: identity rule these endpoints have to respect.
SID = "statement-of-applicability"


def _fixture():
    sections = [
        _Section(SID, "Statement of Applicability", 1, 0),
        _Section(f"{SID}.purpose", "1. Purpose", 2, 1),
        _Section(
            f"{SID}.scope", "2. Scope", 2, 2,
            status="conflict", human_edited=True,
            edited_content="My own words about scope.",
            last_generated_hash=sha256("The generator's words about scope."),
        ),
        _Section(f"{SID}.legacy-controls", "3. Legacy Controls", 2, 3,
                 status="pending_retirement"),
    ]
    return _Document(MERGED, sections), sections


def _version(content: Optional[str] = GENERATED_V2, *, version=2, blob_key=None):
    return SimpleNamespace(
        version=version, content=content, blob_key=blob_key,
        model_id=None, generator_version="1.0.0", input_fingerprint="f",
        created_at=None,
    )


# ---------------------------------------------------------------------------
# B1 -- editing must not decide a retirement
# ---------------------------------------------------------------------------


class TestEditPreservesPendingRetirement:
    def test_editing_a_retiring_section_keeps_it_retiring(self):
        document, sections = _fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, f"{SID}.legacy-controls",
            documents_api.SectionEditRequest(content="Rewritten while I decide."),
            _REQUEST, _membership(), db,
        ))
        assert sections[3].status == "pending_retirement"
        assert sections[3].human_edited is True
        assert sections[3].edited_content == "Rewritten while I decide."

    def test_editing_a_conflicted_section_still_keeps_mine(self):
        # Saving your own text over a conflict IS a decision; only the
        # retirement question survives an edit.
        document, sections = _fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, f"{SID}.scope",
            documents_api.SectionEditRequest(content="Still my words."),
            _REQUEST, _membership(), db,
        ))
        assert sections[2].status == "human_preserved"

    def test_editing_an_unrelated_section_leaves_the_retirement_alone(self):
        # The reproduction: a typo fix elsewhere took a policy from 15 pending
        # retirements to 14.
        document, sections = _fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, f"{SID}.purpose",
            documents_api.SectionEditRequest(content="A typo fix."),
            _REQUEST, _membership(), db,
        ))
        assert [s.status for s in sections] == [
            "unchanged", "human_preserved", "conflict", "pending_retirement",
        ]


# ---------------------------------------------------------------------------
# B2 -- the four resolution choices
# ---------------------------------------------------------------------------


def _resolve(db, section_id, choice, membership=None):
    return _run(documents_api.resolve_document_section(
        ORG_ID, DOC_ID, section_id,
        documents_api.SectionResolveRequest(choice=choice),
        _REQUEST, membership or _membership(), db,
    ))


class TestKeepMine:
    def test_conflict_becomes_human_preserved_and_the_marker_goes(self):
        document, sections = _fixture()
        db = _Session([[document]])
        out = _resolve(db, f"{SID}.scope", "keep_mine")

        assert out.status == "human_preserved"
        assert out.removed is False
        assert out.conflict_count == 0
        assert out.pending_retirement_count == 1
        assert sections[2].human_edited is True
        assert "My own words about scope." in document.merged_content
        assert db.commits == 1

    def test_the_audit_entry_names_the_section_and_both_statuses(self):
        # ``field_name`` is a bounded column and a section id derived from a
        # long heading can overflow it, so the exact identity and both statuses
        # ride in the unbounded Text columns as JSON. What the trail has to
        # answer is unchanged: which section, what it was, what it became.
        import json as _json

        document, _ = _fixture()
        db = _Session([[document]])
        _resolve(db, f"{SID}.scope", "keep_mine")
        entry = next(a for a in db.added if getattr(a, "field_name", None))
        assert entry.field_name == f"section:{SID}.scope:resolve"

        before = _json.loads(entry.old_value)
        after = _json.loads(entry.new_value)
        assert before["section_id"] == f"{SID}.scope"
        assert after["section_id"] == f"{SID}.scope"
        assert before["status"] == "conflict"
        assert after["status"] == "human_preserved"
        assert after["choice"] == "keep_mine"


class TestTakeGenerated:
    def test_the_generated_body_replaces_the_human_text(self):
        document, sections = _fixture()
        db = _Session([[document], [_version()]])
        out = _resolve(db, f"{SID}.scope", "take_generated")

        assert out.status == "updated"
        assert sections[2].human_edited is False
        assert sections[2].edited_content is None
        assert sections[2].content_hash == sections[2].last_generated_hash
        assert "The generator's words about scope." in document.merged_content
        assert "My own words about scope." not in document.merged_content

    def test_a_snapshot_without_the_section_is_a_409_not_a_silent_keep(self):
        document, _ = _fixture()
        db = _Session([[document], [_version(content="# Doc\n\n## 1. Purpose\n\nx\n")]])
        with pytest.raises(HTTPException) as exc:
            _resolve(db, f"{SID}.scope", "take_generated")
        assert exc.value.status_code == 409
        assert "not in the latest version snapshot" in exc.value.detail


class TestRetire:
    def test_the_section_is_excised_and_its_row_deleted(self):
        document, sections = _fixture()
        retiring = sections[3]
        db = _Session([[document]])
        out = _resolve(db, f"{SID}.legacy-controls", "retire")

        assert out.removed is True
        assert out.status == "removed"
        assert out.pending_retirement_count == 0
        assert "Legacy Controls" not in document.merged_content
        assert "Text about controls that left scope." not in document.merged_content
        assert db.deleted == [retiring]
        assert [s.section_id for s in document.sections] == [
            SID, f"{SID}.purpose", f"{SID}.scope",
        ]

    def test_retiring_the_middle_section_renumbers_the_rest(self):
        # A gap in the ordinals is not cosmetic: the merge engine and the
        # reader both fall back to pairing rows to parsed sections by position.
        document, sections = _fixture()
        sections[2].status = "pending_retirement"
        sections[2].human_edited = False
        db = _Session([[document]])
        _resolve(db, f"{SID}.scope", "retire")
        assert [s.ordinal for s in document.sections] == [0, 1, 2]
        assert [s.section_id for s in document.sections] == [
            SID, f"{SID}.purpose", f"{SID}.legacy-controls",
        ]

    def test_the_surviving_sections_keep_their_text(self):
        document, _ = _fixture()
        db = _Session([[document]])
        _resolve(db, f"{SID}.legacy-controls", "retire")
        assert "The purpose is stated here." in document.merged_content
        assert "My own words about scope." in document.merged_content


class TestKeep:
    def test_the_retirement_is_cleared_and_the_marker_stripped(self):
        document, sections = _fixture()
        db = _Session([[document]])
        out = _resolve(db, f"{SID}.legacy-controls", "keep")

        assert out.status == "unchanged"
        assert out.removed is False
        assert out.pending_retirement_count == 0
        assert PENDING_RETIREMENT_MARKER not in document.merged_content
        assert "Text about controls that left scope." in document.merged_content

    def test_an_edited_section_comes_back_as_human_preserved(self):
        document, sections = _fixture()
        sections[3].human_edited = True
        sections[3].edited_content = "My rewrite."
        db = _Session([[document]])
        out = _resolve(db, f"{SID}.legacy-controls", "keep")
        assert out.status == "human_preserved"

    def test_other_sections_markers_are_untouched(self):
        document, sections = _fixture()
        document.merged_content = document.merged_content.replace(
            "## 1. Purpose\n", f"## 1. Purpose\n\n{PENDING_RETIREMENT_MARKER}\n"
        )
        sections[1].status = "pending_retirement"
        db = _Session([[document]])
        _resolve(db, f"{SID}.legacy-controls", "keep")
        assert document.merged_content.count(PENDING_RETIREMENT_MARKER) == 1


class TestMisapplication:
    @pytest.mark.parametrize("section_id,choice,expected_state", [
        (f"{SID}.purpose", "keep_mine", "conflict"),
        (f"{SID}.purpose", "take_generated", "conflict"),
        (f"{SID}.legacy-controls", "keep_mine", "conflict"),
        (f"{SID}.scope", "retire", "pending_retirement"),
        (f"{SID}.scope", "keep", "pending_retirement"),
        (f"{SID}.purpose", "retire", "pending_retirement"),
    ])
    def test_the_wrong_state_is_409(self, section_id, choice, expected_state):
        document, _ = _fixture()
        db = _Session([[document]])
        with pytest.raises(HTTPException) as exc:
            _resolve(db, section_id, choice)
        assert exc.value.status_code == 409
        assert expected_state in exc.value.detail
        assert db.commits == 0

    def test_an_unknown_section_is_404(self):
        document, _ = _fixture()
        db = _Session([[document]])
        with pytest.raises(HTTPException) as exc:
            _resolve(db, "no-such-section", "keep_mine")
        assert exc.value.status_code == 404

    def test_an_unknown_choice_is_refused_by_the_schema(self):
        with pytest.raises(Exception):
            documents_api.SectionResolveRequest(choice="delete_everything")


class TestLifecycleSideEffect:
    def test_resolving_an_approved_document_returns_it_to_review(self):
        document, _ = _fixture()
        document.lifecycle_status = "approved"
        db = _Session([[document]])
        out = _resolve(db, f"{SID}.scope", "keep_mine")
        assert out.lifecycle_status == "in_review"
        assert any(
            getattr(t, "trigger", None) == "edit" for t in db.added
        )


# ---------------------------------------------------------------------------
# B3 -- the generated alternative
# ---------------------------------------------------------------------------


class TestGeneratedSectionRoute:
    def _get(self, db, section_id, version=None):
        return _run(documents_api.get_generated_section(
            ORG_ID, DOC_ID, section_id, version, _membership("viewer"), db,
        ))

    def test_a_present_section_returns_the_generated_body(self):
        document, _ = _fixture()
        db = _Session([[document], [_version()]])
        out = self._get(db, f"{SID}.scope")

        assert out.available is True
        assert out.version == 2
        assert out.heading_text == "2. Scope"
        assert out.content == "The generator's words about scope."
        assert out.current_content == "My own words about scope."

    def test_a_retiring_section_reports_unavailable_rather_than_empty(self):
        document, _ = _fixture()
        db = _Session([[document], [_version()]])
        out = self._get(db, f"{SID}.legacy-controls")

        assert out.available is False
        assert out.content is None
        # Absence is the answer, but the operative text is still returned so
        # the reader can see what would be lost.
        assert "Text about controls that left scope." in out.current_content
        assert PENDING_RETIREMENT_MARKER not in out.current_content

    def test_blob_backed_content_is_read_from_storage(self, monkeypatch):
        document, _ = _fixture()
        db = _Session([[document], [_version(content=None, blob_key="doc/v2.md")]])

        seen = {}

        def _stream(key):
            seen["key"] = key
            return [GENERATED_V2[:20].encode("utf-8"), GENERATED_V2[20:].encode("utf-8")]

        from services import storage_service
        monkeypatch.setattr(storage_service, "download_blob_stream", _stream)

        out = self._get(db, f"{SID}.scope")
        assert seen["key"] == "doc/v2.md"
        assert out.available is True
        assert out.content == "The generator's words about scope."

    def test_an_unreadable_blob_is_503_not_an_empty_pane(self, monkeypatch):
        document, _ = _fixture()
        db = _Session([[document], [_version(content=None, blob_key="doc/v2.md")]])

        from services import storage_service

        def _boom(_key):
            raise ValueError("Evidence storage not configured")

        monkeypatch.setattr(storage_service, "download_blob_stream", _boom)
        with pytest.raises(HTTPException) as exc:
            self._get(db, f"{SID}.scope")
        assert exc.value.status_code == 503

    def test_an_unknown_section_is_404(self):
        document, _ = _fixture()
        db = _Session([[document]])
        with pytest.raises(HTTPException) as exc:
            self._get(db, "nope")
        assert exc.value.status_code == 404

    def test_a_missing_version_is_404(self):
        document, _ = _fixture()
        db = _Session([[document], []])
        with pytest.raises(HTTPException) as exc:
            self._get(db, f"{SID}.scope", version=99)
        assert exc.value.status_code == 404
        assert "Version 99" in exc.value.detail


# ---------------------------------------------------------------------------
# Locating a section in the operative document
# ---------------------------------------------------------------------------


#: A retiree at the end of the document plus a human-introduced heading, so
#: neither the row sequence nor the heading sequence lines up with the other.
DRIFTED_MD = (
    "# Statement of Applicability\n\n"
    "## Roles\n\n"
    "### Responsibilities\n\n"
    "Typed by a human.\n\n"
    "## Review\n\n"
    "Review body.\n\n"
    "### Scope\n\n"
    "Old scope text.\n"
)


def _drifted_document() -> _Document:
    return _Document(
        DRIFTED_MD,
        [
            _Section(SID, "Statement of Applicability", 1, 0),
            _Section(f"{SID}.roles", "Roles", 2, 1),
            _Section(f"{SID}.review", "Review", 2, 2),
            _Section(
                f"{SID}.roles.scope",
                "Scope",
                3,
                3,
                status="pending_retirement",
                human_edited=True,
                edited_content="Old scope text, edited by a human.",
            ),
        ],
    )


class TestSectionPosition:
    """``_section_position`` is what every text-editing path addresses through.

    It used to trust ``ordinal`` when the heading count matched the row count
    and fall back to a re-derived id otherwise. Both halves fail on the same
    document: the counts disagree the moment a human edit contains a heading
    line, and a re-derived id is wrong for precisely the retiree these routes
    exist to settle.
    """

    def test_the_retiree_resolves_to_its_own_heading(self):
        document = _drifted_document()
        retiring = document.sections[3]
        # Index 4 is "### Scope", the last heading in the document.
        assert documents_api._section_position(document, retiring) == 4

    def test_a_live_section_is_not_displaced_by_the_extra_heading(self):
        document = _drifted_document()
        assert documents_api._section_position(document, document.sections[2]) == 3

    def test_a_section_with_no_heading_reports_minus_one(self):
        document = _drifted_document()
        orphan = _Section(f"{SID}.gone", "Gone", 2, 9)
        assert documents_api._section_position(document, orphan) == -1


class TestRebuildMergedKeyTranslation:
    """A human edit on a retired section has to survive the rebuild.

    ``build_merged_document`` applies edits under the ids it re-derives from
    the markdown it is handed. That is right where the generation pipeline
    calls it — a fresh generation parses to exactly the ids it defines — and
    wrong here, where the markdown is the operative document and a retiree
    reads back under a different parent. Passing stored ids straight through
    dropped the edit: the save returned 200 and the text was unchanged.
    """

    def test_an_edit_on_a_retiree_reaches_the_document(self):
        document = _drifted_document()
        rebuilt = documents_api._rebuild_merged(document)
        assert "Old scope text, edited by a human." in rebuilt

    def test_the_edit_lands_on_the_retiree_and_nowhere_else(self):
        document = _drifted_document()
        rebuilt = documents_api._rebuild_merged(document)
        assert rebuilt.count("Old scope text, edited by a human.") == 1
        assert "Review body." in rebuilt
        assert "Typed by a human." in rebuilt

    def test_an_edit_with_no_heading_to_land_on_is_dropped_not_misapplied(self):
        # Better an unapplied edit than one written into a section the user
        # was not editing.
        document = _drifted_document()
        document.sections.append(
            _Section(
                f"{SID}.gone",
                "Gone",
                2,
                9,
                human_edited=True,
                edited_content="Text belonging to a section that is not here.",
            )
        )
        rebuilt = documents_api._rebuild_merged(document)
        assert "Text belonging to a section that is not here." not in rebuilt
        assert "Review body." in rebuilt

    def test_overrides_still_reach_their_section(self):
        # ``take_generated`` depends on this path, and its key is a stored id
        # like any other.
        document = _drifted_document()
        rebuilt = documents_api._rebuild_merged(
            document, overrides={f"{SID}.review": "Generated review body."}
        )
        assert "Generated review body." in rebuilt
        assert "Review body." not in rebuilt


# ---------------------------------------------------------------------------
# An audit write must never break the action it is auditing
# ---------------------------------------------------------------------------


from models import AuditLog  # noqa: E402 -- for the real column width

#: The bound the audit row has to respect, read from the column rather than
#: restated. A magic ``100`` here would keep passing after someone widened or
#: narrowed the column, which is precisely when this test needs to speak up.
FIELD_NAME_LIMIT = AuditLog.field_name.type.length

#: Two headings that normalise to ids differing only in their final word. The
#: pair is the point: an audit trail that cannot tell them apart has not
#: recorded which section a human retired.
LONG_A = "Security, Compliance and Resilience Governance Assurance Oversight Alpha"
LONG_B = "Security, Compliance and Resilience Governance Assurance Oversight Bravo"

LONG_MERGED = (
    "# Statement of Applicability\n\n"
    f"## {LONG_A}\n\n"
    "My own words about the first domain.\n\n"
    f"## {LONG_B}\n\n"
    f"{PENDING_RETIREMENT_MARKER}\n\n"
    "Text about the second domain, which left scope.\n"
)

LONG_GENERATED = (
    "# Statement of Applicability\n\n"
    f"## {LONG_A}\n\n"
    "The generator's words about the first domain.\n"
)


def _long_id(heading: str) -> str:
    from services.doc_gen.section_parser import normalise_section_id

    return f"{SID}.{normalise_section_id(heading)}"


LONG_ID_A = _long_id(LONG_A)
LONG_ID_B = _long_id(LONG_B)


def _long_fixture():
    sections = [
        _Section(SID, "Statement of Applicability", 1, 0),
        _Section(
            LONG_ID_A, LONG_A, 2, 1,
            status="conflict", human_edited=True,
            edited_content="My own words about the first domain.",
            last_generated_hash=sha256("The generator's words about the first domain."),
        ),
        _Section(LONG_ID_B, LONG_B, 2, 2, status="pending_retirement"),
    ]
    return _Document(LONG_MERGED, sections), sections


def _audit_rows(db) -> List[Any]:
    return [a for a in db.added if getattr(a, "field_name", None)]


class TestTheFixtureActuallyOverflows:
    """If these ids fit, every test below is asserting nothing."""

    def test_the_composed_resolve_name_exceeds_the_column(self):
        assert len(f"section:{LONG_ID_B}:resolve") > FIELD_NAME_LIMIT

    def test_the_composed_edit_name_exceeds_the_column(self):
        assert len(f"section:{LONG_ID_A}") > FIELD_NAME_LIMIT

    def test_the_two_ids_differ_only_in_their_tail(self):
        assert LONG_ID_A != LONG_ID_B
        assert LONG_ID_A[:-5] == LONG_ID_B[:-5]


class TestEditSectionSurvivesAnOverflowingId:
    def test_the_edit_succeeds_and_writes_an_audit_row(self):
        document, sections = _long_fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, LONG_ID_A,
            documents_api.SectionEditRequest(content="Rewritten."),
            _REQUEST, _membership(), db,
        ))
        assert db.commits == 1
        assert sections[1].edited_content == "Rewritten."
        assert len(_audit_rows(db)) == 1

    def test_the_audit_field_name_fits_the_column(self):
        document, _ = _long_fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, LONG_ID_A,
            documents_api.SectionEditRequest(content="Rewritten."),
            _REQUEST, _membership(), db,
        ))
        assert len(_audit_rows(db)[0].field_name) <= FIELD_NAME_LIMIT

    def test_the_exact_section_id_is_recoverable(self):
        # A digest in ``field_name`` distinguishes rows but cannot be read
        # back. ISC-38 requires the entry to *name* the section, so the
        # complete id has to survive somewhere unbounded.
        import json as _json

        document, _ = _long_fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, LONG_ID_A,
            documents_api.SectionEditRequest(content="Rewritten."),
            _REQUEST, _membership(), db,
        ))
        entry = _audit_rows(db)[0]
        assert _json.loads(entry.new_value)["section_id"] == LONG_ID_A

    def test_two_ids_differing_only_in_the_tail_stay_distinguishable(self):
        import json as _json

        rows = []
        for section_id in (LONG_ID_A, LONG_ID_B):
            document, _ = _long_fixture()
            db = _Session([[document]])
            _run(documents_api.edit_section(
                ORG_ID, DOC_ID, section_id,
                documents_api.SectionEditRequest(content="Rewritten."),
                _REQUEST, _membership(), db,
            ))
            rows.append(_audit_rows(db)[0])

        assert rows[0].field_name != rows[1].field_name
        assert _json.loads(rows[0].new_value)["section_id"] == LONG_ID_A
        assert _json.loads(rows[1].new_value)["section_id"] == LONG_ID_B

    def test_the_edit_semantics_are_unchanged(self):
        # The overflow fix must not alter what this route does; only that it
        # no longer takes the transaction down with it.
        document, sections = _long_fixture()
        db = _Session([[document]])
        _run(documents_api.edit_section(
            ORG_ID, DOC_ID, LONG_ID_B,
            documents_api.SectionEditRequest(content="Rewritten while I decide."),
            _REQUEST, _membership(), db,
        ))
        assert sections[2].status == "pending_retirement"
        assert sections[2].human_edited is True
        entry = _audit_rows(db)[0]
        assert entry.entity_type == "generated_document"
        assert entry.action == "update"
        assert entry.field_name.startswith("section:")


class TestEveryResolveChoiceSurvivesAnOverflowingId:
    """The live failure: retiring a real Statement of Applicability section."""

    def _run_choice(self, choice, section_id, results=None):
        document, sections = _long_fixture()
        db = _Session(results if results is not None else [[document]])
        out = _run(documents_api.resolve_document_section(
            ORG_ID, DOC_ID, section_id,
            documents_api.SectionResolveRequest(choice=choice),
            _REQUEST, _membership(), db,
        ))
        return out, db, sections, document

    def test_retire_succeeds_where_it_previously_500ed(self):
        out, db, _, document = self._run_choice("retire", LONG_ID_B)
        assert out.removed is True
        assert db.commits == 1
        assert LONG_B not in document.merged_content

    @pytest.mark.parametrize("choice,section_id", [
        ("keep_mine", LONG_ID_A),
        ("retire", LONG_ID_B),
        ("keep", LONG_ID_B),
    ])
    def test_the_audit_field_name_fits_the_column(self, choice, section_id):
        _, db, _, _ = self._run_choice(choice, section_id)
        rows = _audit_rows(db)
        assert rows, f"{choice} wrote no audit row"
        for row in rows:
            assert len(row.field_name) <= FIELD_NAME_LIMIT, choice

    def test_take_generated_fits_the_column_too(self):
        # The one choice that needs a version snapshot as well as the document.
        document, _ = _long_fixture()
        db = _Session([[document], [_version(LONG_GENERATED)]])
        _run(documents_api.resolve_document_section(
            ORG_ID, DOC_ID, LONG_ID_A,
            documents_api.SectionResolveRequest(choice="take_generated"),
            _REQUEST, _membership(), db,
        ))
        rows = _audit_rows(db)
        assert rows
        assert len(rows[0].field_name) <= FIELD_NAME_LIMIT

    @pytest.mark.parametrize("choice,section_id,prior,final", [
        ("keep_mine", LONG_ID_A, "conflict", "human_preserved"),
        ("retire", LONG_ID_B, "pending_retirement", "removed"),
        ("keep", LONG_ID_B, "pending_retirement", "unchanged"),
    ])
    def test_both_statuses_survive_the_clamp(self, choice, section_id, prior, final):
        import json as _json

        _, db, _, _ = self._run_choice(choice, section_id)
        entry = _audit_rows(db)[0]
        assert _json.loads(entry.old_value)["status"] == prior
        assert _json.loads(entry.new_value)["status"] == final

    @pytest.mark.parametrize("choice,section_id", [
        ("keep_mine", LONG_ID_A),
        ("retire", LONG_ID_B),
        ("keep", LONG_ID_B),
    ])
    def test_the_exact_section_id_survives_the_clamp(self, choice, section_id):
        import json as _json

        _, db, _, _ = self._run_choice(choice, section_id)
        entry = _audit_rows(db)[0]
        assert _json.loads(entry.old_value)["section_id"] == section_id
        assert _json.loads(entry.new_value)["section_id"] == section_id

    def test_two_ids_differing_only_in_the_tail_stay_distinguishable(self):
        _, db_a, _, _ = self._run_choice("keep_mine", LONG_ID_A)
        _, db_b, _, _ = self._run_choice("keep", LONG_ID_B)
        assert _audit_rows(db_a)[0].field_name != _audit_rows(db_b)[0].field_name
