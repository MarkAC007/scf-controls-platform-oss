"""The reader fragment.

The in-app reader injects this into a React tree, so it must be a fragment and
must not smuggle merge markers through as HTML comments — a comment renders as
nothing, which is exactly backwards for the one view whose job is to show the
merge.
"""

from dataclasses import dataclass
from typing import List

from services.doc_gen.renderer import markdown_to_reader_fragment


@dataclass
class Row:
    section_id: str
    status: str
    ordinal: int
    control_ids: List[str]


DOC = (
    "## Purpose\nWhy this exists.\n\n"
    "## Roles\n"
    "<!-- NEW: section added from newly scoped controls. -->\n\n"
    "Who does what [GOV-01].\n\n"
    "### Scope\n"
    "<!-- PENDING RETIREMENT: the controls behind this section left scope. "
    "Nothing has been deleted -- review and retire deliberately. -->\n\n"
    "Old scope text.\n"
)

ROWS = [
    Row("purpose", "unchanged", 0, []),
    Row("roles", "new", 1, ["GOV-01"]),
    # Stored under its true parent, which is *not* what a re-parse of the
    # rendered document would derive. The fragment must honour the stored id.
    Row("policy.scope", "pending_retirement", 2, []),
]


def fragment() -> str:
    return markdown_to_reader_fragment(DOC, ROWS)


class TestFragmentShape:
    def test_it_is_a_fragment_not_a_page(self):
        html = fragment()
        assert not html.lstrip().lower().startswith("<!doctype")
        assert "<html" not in html and "<head>" not in html

    def test_every_section_is_wrapped(self):
        assert fragment().count('<section class="docr-sec') == 3

    def test_headings_keep_their_level(self):
        html = fragment()
        assert "<h2>Purpose</h2>" in html
        assert "<h3>Scope</h3>" in html


class TestMergeStateIsVisible:
    def test_status_reaches_the_wrapper(self):
        html = fragment()
        for status in ("unchanged", "new", "pending_retirement"):
            assert f"status-{status}" in html

    def test_markers_do_not_survive_as_comments(self):
        # The whole defect: an HTML comment renders as nothing at all.
        assert "<!--" not in fragment()

    def test_each_marked_section_gets_a_visible_banner(self):
        html = fragment()
        assert 'docr-flag-new' in html
        assert 'docr-flag-pending_retirement' in html

    def test_an_unchanged_section_gets_no_banner(self):
        html = fragment()
        head = html[: html.index("status-new")]
        assert "docr-flag" not in head


class TestIdentity:
    def test_the_stored_id_is_used_not_the_re_derived_one(self):
        # A re-parse would call this "roles.scope"; it is stored as
        # "policy.scope" and the reader must anchor on what is stored, or the
        # contents rail cannot scroll to it and Edit cannot open it.
        html = fragment()
        assert 'data-section-id="policy.scope"' in html
        assert 'data-section-id="roles.scope"' not in html

    def test_a_row_count_mismatch_falls_back_to_parsed_ids(self):
        # Degrades rather than pairing rows against the wrong sections.
        html = markdown_to_reader_fragment(DOC, ROWS[:1])
        assert 'data-section-id="purpose"' in html
        assert "status-pending_retirement" not in html


class TestControlProvenance:
    def test_control_ids_are_listed_on_the_section(self):
        html = fragment()
        assert '<span class="docr-control">GOV-01</span>' in html

    def test_a_section_with_no_controls_lists_none(self):
        html = markdown_to_reader_fragment("## Purpose\nWhy.\n", [Row("purpose", "unchanged", 0, [])])
        assert "docr-controls" not in html
