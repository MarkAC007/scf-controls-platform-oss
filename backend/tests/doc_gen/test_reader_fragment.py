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
    """A stand-in for ``DocumentSection``.

    ``heading_text`` and ``heading_level`` are carried because the real column
    pair is ``NOT NULL`` and the renderer needs them: they are how a section
    whose stored id no longer matches the document -- a retiree -- is found
    again. A fixture without them would be testing a row shape that cannot
    exist.
    """

    section_id: str
    status: str
    ordinal: int
    control_ids: List[str]
    heading_text: str = ""
    heading_level: int = 2


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
    Row("purpose", "unchanged", 0, [], "Purpose", 2),
    Row("roles", "new", 1, ["GOV-01"], "Roles", 2),
    # Stored under its true parent, which is *not* what a re-parse of the
    # rendered document would derive. The fragment must honour the stored id.
    Row("policy.scope", "pending_retirement", 2, [], "Scope", 3),
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


# ---------------------------------------------------------------------------
# Pairing rows to headings when the two sequences disagree
# ---------------------------------------------------------------------------

#: A document carrying both divergences at once.
#:
#: "Scope" was retired, so it is re-rendered at the end at its original depth
#: and now parses as a child of "Review". And someone typing into the "Roles"
#: body added a "### Responsibilities" heading, which is a heading with no row
#: behind it. Five headings, four rows, and the fourth row belongs to the
#: *last* heading -- so pairing by position is wrong in both directions.
DRIFTED = (
    "# Policy\n\n"
    "## Roles\n\n"
    "### Responsibilities\n\n"
    "Who does what.\n\n"
    "## Review\n\n"
    "Review body.\n\n"
    "### Scope\n"
    "<!-- PENDING RETIREMENT: the controls behind this section left scope. -->\n\n"
    "Old scope text.\n"
)

DRIFTED_ROWS = [
    Row("policy", "unchanged", 0, [], "Policy", 1),
    Row("policy.roles", "unchanged", 1, [], "Roles", 2),
    Row("policy.review", "conflict", 2, [], "Review", 2),
    Row("policy.roles.scope", "pending_retirement", 3, [], "Scope", 3),
]


class TestPairingWhenCountsDisagree:
    """The reader's ids are what per-section decision controls act on.

    A wrong id here is not a mislabel; it points "Take generated" or "Retire"
    at a section the user was not looking at. So these assert identity, not
    merely that something plausible was rendered.
    """

    def drifted(self) -> str:
        return markdown_to_reader_fragment(DRIFTED, DRIFTED_ROWS)

    def test_the_retiree_keeps_its_stored_id(self):
        html = self.drifted()
        assert 'data-section-id="policy.roles.scope"' in html
        # The id a re-parse derives for it, which is the wrong section to act on.
        assert 'data-section-id="policy.review.scope"' not in html

    def test_the_retiree_gets_its_own_body_not_a_neighbours(self):
        html = self.drifted()
        retiring = html.split('data-section-id="policy.roles.scope"')[1]
        assert "Old scope text." in retiring.split("</section>")[0]
        assert "Review body." not in retiring.split("</section>")[0]

    def test_status_is_not_shifted_onto_the_wrong_heading(self):
        html = self.drifted()
        # Positional pairing would put the conflict on "Responsibilities",
        # which is the third heading but has no row at all.
        conflicting = html.split('data-section-id="policy.review"')[1]
        assert "status-conflict" in html.split('data-section-id="policy.review"')[0].rsplit(
            "<section", 1
        )[-1]
        assert "<h2>Review</h2>" in conflicting.split("</section>")[0]
        assert html.count("status-conflict") == 1

    def test_a_heading_with_no_row_is_still_rendered(self):
        # Human-introduced headings are part of the document. Dropping one
        # would silently delete the user's own text from the reader.
        html = self.drifted()
        assert 'data-section-id="policy.roles.responsibilities"' in html
        assert "<h3>Responsibilities</h3>" in html

    def test_every_heading_appears_exactly_once(self):
        html = self.drifted()
        assert html.count('<section class="docr-sec') == 5
        assert html.count("status-pending_retirement") == 1
