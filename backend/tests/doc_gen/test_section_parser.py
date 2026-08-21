"""Section parsing, ID normalisation, and control-ID extraction."""
import pytest

from services.doc_gen.section_parser import (
    extract_control_ids,
    flatten_sections,
    normalise_section_id,
    parse_markdown_sections,
    split_preamble,
    to_section_rows,
)


class TestNormaliseSectionId:
    @pytest.mark.parametrize(
        "heading,expected",
        [
            ("1. Document Control", "document-control"),
            ("4.1 Access Management", "access-management"),
            ("4.1.2 Privileged Access", "privileged-access"),
            ("**Evidence Produced:**", "evidence-produced"),
            ("Roles and Responsibilities", "roles-and-responsibilities"),
            ("Purpose:", "purpose"),
            ("  Spaced   Out  ", "spaced-out"),
        ],
    )
    def test_normalisation(self, heading, expected):
        assert normalise_section_id(heading) == expected

    def test_renumbering_does_not_change_the_id(self):
        # The whole point: inserting a section must not orphan human edits
        # attached to the sections after it.
        assert normalise_section_id("3. Scope") == normalise_section_id("4. Scope")

    def test_bracketed_control_ids_are_stripped_from_ids(self):
        assert normalise_section_id("Scope [AAA-01]") == "scope"


class TestExtractControlIds:
    def test_finds_bracketed_ids_sorted_and_deduplicated(self):
        text = "refs [BBB-02] and [AAA-01] and [AAA-01] again"
        assert extract_control_ids(text) == ["AAA-01", "BBB-02"]

    def test_accepts_dotted_sub_numbers(self):
        assert extract_control_ids("[AAA-01.1]") == ["AAA-01.1"]

    def test_ignores_unbracketed_mentions(self):
        # Prose naming a control is not a citation.
        assert extract_control_ids("we follow AAA-01 closely") == []

    def test_empty_content_is_safe(self):
        assert extract_control_ids("") == []
        assert extract_control_ids(None) == []


class TestParseMarkdownSections:
    def test_builds_hierarchical_ids(self):
        md = "## Policy Statements\nbody\n### Account Lifecycle\nmore\n"
        flat = flatten_sections(parse_markdown_sections(md))
        assert [s.section_id for s in flat] == [
            "policy-statements",
            "policy-statements.account-lifecycle",
        ]

    def test_siblings_return_to_the_parent_level(self):
        md = "## A\n1\n### A1\n2\n## B\n3\n"
        flat = flatten_sections(parse_markdown_sections(md))
        assert [s.section_id for s in flat] == ["a", "a.a1", "b"]

    def test_headings_inside_code_fences_are_ignored(self):
        md = "## Real\ntext\n```\n# not a heading\n## also not\n```\n## Second\n"
        flat = flatten_sections(parse_markdown_sections(md))
        assert [s.section_id for s in flat] == ["real", "second"]

    def test_tilde_fences_are_respected(self):
        md = "## Real\n~~~\n# hidden\n~~~\n"
        assert len(flatten_sections(parse_markdown_sections(md))) == 1

    def test_no_headings_yields_no_sections(self):
        assert parse_markdown_sections("just prose, no headings") == []

    def test_content_excludes_the_heading_line(self):
        md = "## Scope\nthis is the body\n"
        section = parse_markdown_sections(md)[0]
        assert section.content == "this is the body"
        assert "## Scope" not in section.content

    def test_control_ids_collected_from_heading_and_body(self):
        md = "## Scope [AAA-01]\nalso [BBB-02] here\n"
        section = parse_markdown_sections(md)[0]
        assert section.control_ids == ["AAA-01", "BBB-02"]

    def test_identical_bodies_hash_identically(self):
        a = parse_markdown_sections("## One\nsame body\n")[0]
        b = parse_markdown_sections("## Two\nsame body\n")[0]
        assert a.content_hash == b.content_hash


class TestSplitPreamble:
    def test_returns_content_before_first_heading(self):
        assert split_preamble("---\ntitle: x\n---\n\n## First\nbody") == "---\ntitle: x\n---\n"

    def test_returns_everything_when_there_are_no_headings(self):
        assert split_preamble("no headings here") == "no headings here"


class TestToSectionRows:
    def test_rows_carry_ordinal_and_seeded_generated_hash(self):
        rows = to_section_rows(parse_markdown_sections("## A\n1\n### A1\n2\n## B\n3\n"))
        assert [r["ordinal"] for r in rows] == [0, 1, 2]
        for row in rows:
            assert row["last_generated_hash"] == row["content_hash"]
            assert row["human_edited"] is False
            assert row["status"] == "new"

    def test_heading_level_is_preserved(self):
        rows = to_section_rows(parse_markdown_sections("## A\n1\n### A1\n2\n"))
        assert [r["heading_level"] for r in rows] == [2, 3]
