"""Section parsing, ID normalisation, and control-ID extraction."""
import pytest

from services.doc_gen.section_parser import (
    excise_section_block,
    extract_control_ids,
    flatten_sections,
    hashable_body,
    normalise_section_id,
    pair_sections_to_headings,
    parse_markdown_sections,
    recompute_section_ids,
    section_body_from_markdown,
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


class TestCountParentheticalsAreNotIdentity:
    """A tally in a heading is a property of today's scope, not of the section.

    The Statement of Applicability writes ``### 3. GOV — Governance (12
    controls)``. While the count was part of the slug, scoping a single control
    renamed every domain section at once: 40 sections became 71, 33 of them
    retired, and every human edit on a domain section was stranded.
    """

    @pytest.mark.parametrize(
        "heading,expected",
        [
            ("GOV — Governance & Risk Management (12 controls)",
             "gov-governance-risk-management"),
            ("3. GOV — Governance (1 control)", "gov-governance"),
            ("Controls Without an Owner (0)", "controls-without-an-owner"),
            ("Untracked Evidence (7)", "untracked-evidence"),
            ("Backlog (3 items)", "backlog"),
        ],
    )
    def test_a_trailing_count_is_dropped(self, heading, expected):
        assert normalise_section_id(heading) == expected

    def test_the_count_no_longer_moves_the_id(self):
        assert (
            normalise_section_id("3. GOV — Governance (12 controls)")
            == normalise_section_id("3. GOV — Governance (13 controls)")
        )

    @pytest.mark.parametrize(
        "heading,expected",
        [
            ("Acceptable Use (Policy)", "acceptable-use-policy"),
            ("Access Control (Draft)", "access-control-draft"),
            ("Mapping (Annex A)", "mapping-annex-a"),
            # Not trailing: the parenthetical qualifies what follows it.
            ("Scope (2 sites) and Boundaries", "scope-2-sites-and-boundaries"),
        ],
    )
    def test_a_non_numeric_qualifier_still_counts(self, heading, expected):
        # Two sibling headings differing only by such a qualifier are two
        # different sections; collapsing them would collide their ids.
        assert normalise_section_id(heading) == expected

    def test_the_display_heading_keeps_its_count(self):
        # Only the slug loses the tally -- the document still reports it.
        sections = parse_markdown_sections("## GOV — Governance (12 controls)\nbody\n")
        assert sections[0].heading_text == "GOV — Governance (12 controls)"
        assert sections[0].section_id == "gov-governance"


class TestSectionBodyFromMarkdown:
    MARKDOWN = (
        "# Policy\n\nPreamble.\n\n"
        "## Purpose\n\nWhy this exists.\n\n"
        "## Scope\n\nWhat it covers.\n\n"
        "### Exclusions\n\nWhat it does not.\n"
    )

    def test_a_present_section_is_returned_with_its_heading(self):
        found = section_body_from_markdown(self.MARKDOWN, "policy.scope")
        assert found is not None
        assert found.heading_text == "Scope"
        assert found.content == "What it covers."

    def test_a_nested_section_is_reachable_by_its_full_path(self):
        found = section_body_from_markdown(self.MARKDOWN, "policy.scope.exclusions")
        assert found is not None
        assert found.content == "What it does not."

    def test_an_absent_section_is_none_not_an_error(self):
        # This is the pending_retirement case: absence from the newest
        # generation is what retired the section, so "not here" is the answer.
        assert section_body_from_markdown(self.MARKDOWN, "policy.retired") is None

    def test_empty_markdown_yields_none(self):
        assert section_body_from_markdown("", "anything") is None


class TestExciseSectionBlock:
    MARKDOWN = "## A\n\nalpha\n\n## B\n\nbeta\n\n## C\n\ngamma\n"

    def test_the_named_block_and_only_it_is_removed(self):
        out = excise_section_block(self.MARKDOWN, 1)
        assert "## B" not in out and "beta" not in out
        assert "alpha" in out and "gamma" in out

    def test_the_last_block_can_be_removed(self):
        out = excise_section_block(self.MARKDOWN, 2)
        assert "gamma" not in out
        assert out.rstrip().endswith("beta")

    def test_a_nested_subsection_survives_its_parent(self):
        # A subsection has its own row and its own retirement decision;
        # sweeping it away with its parent would be a silent deletion.
        markdown = "## A\n\nalpha\n\n### A1\n\nchild\n\n## B\n\nbeta\n"
        out = excise_section_block(markdown, 0)
        assert "alpha" not in out
        assert "### A1" in out and "child" in out

    def test_an_out_of_range_position_changes_nothing(self):
        assert excise_section_block(self.MARKDOWN, 9) == self.MARKDOWN
        assert excise_section_block(self.MARKDOWN, -1) == self.MARKDOWN


class TestRecomputeSectionIds:
    """The migration's mapping function."""

    def _row(self, section_id, heading_text, level, ordinal, status="unchanged"):
        return {
            "section_id": section_id,
            "heading_text": heading_text,
            "heading_level": level,
            "ordinal": ordinal,
            "status": status,
        }

    def _retiree(self, section_id, heading_text, level, ordinal):
        return self._row(section_id, heading_text, level, ordinal,
                         status="pending_retirement")

    def test_counted_headings_are_remapped_and_the_tree_is_rebuilt(self):
        root = "statement-of-applicability"
        rows = [
            self._row(root, "Statement of Applicability", 1, 0),
            self._row(f"{root}.gov-governance-12-controls",
                      "1. GOV — Governance (12 controls)", 2, 1),
            self._row(f"{root}.gov-governance-12-controls.notes", "Notes", 3, 2),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {
            f"{root}.gov-governance-12-controls": f"{root}.gov-governance",
            f"{root}.gov-governance-12-controls.notes": f"{root}.gov-governance.notes",
        }
        assert remap.collisions == []

    def test_rows_that_do_not_move_are_absent_from_the_mapping(self):
        rows = [self._row("purpose", "1. Purpose", 2, 0)]
        assert recompute_section_ids(rows).changes == {}

    def test_a_collision_with_a_later_row_leaves_the_row_alone(self):
        # Two headings that differed only by their counts collapse onto one
        # slug. uq_document_sections_doc_section would abort the whole
        # migration, so the colliding row keeps its id and is reported.
        rows = [
            self._row("gov-governance-12-controls",
                      "GOV — Governance (12 controls)", 2, 0),
            self._row("gov-governance", "GOV — Governance", 2, 1),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {}
        assert remap.collisions == ["gov-governance-12-controls"]

    def test_a_collision_with_an_already_assigned_row_is_caught_too(self):
        rows = [
            self._row("a-1-control", "A (1 control)", 2, 0),
            self._row("a-2-controls", "A (2 controls)", 2, 1),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {"a-1-control": "a"}
        assert remap.collisions == ["a-2-controls"]

    def test_descendants_of_a_kept_row_build_on_the_id_it_kept(self):
        rows = [
            self._row("a-1-control", "A (1 control)", 2, 0),
            self._row("a-2-controls", "A (2 controls)", 2, 1),
            self._row("a-2-controls.notes", "Notes", 3, 2),
        ]
        remap = recompute_section_ids(rows)
        # The parent kept "a-2-controls", so the child must not be reparented
        # onto the slug the parent failed to take.
        assert "a-2-controls.notes" not in remap.changes

    def test_rows_are_walked_in_ordinal_order_not_input_order(self):
        rows = [
            self._row("doc.child", "Child", 3, 2),
            self._row("doc", "Doc", 1, 0),
            self._row("doc.parent-4-controls", "Parent (4 controls)", 2, 1),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes["doc.parent-4-controls"] == "doc.parent"
        assert remap.changes["doc.child"] == "doc.parent.child"

    def test_a_heading_that_normalises_to_nothing_is_left_alone(self):
        rows = [self._row("legacy", "###", 2, 0), self._row("x", "12.", 2, 1)]
        remap = recompute_section_ids(rows)
        assert remap.changes == {}

    def test_a_retiree_is_not_reparented_onto_the_last_live_heading(self):
        # three_way_merge appends retired sections after every live one, so a
        # retiree's ordinal sits past the tree. Walking it with the level stack
        # would parent it under whichever live heading sorts last -- here
        # "Review" -- handing it somebody else's identity while reporting
        # success. Its stored path is the truth.
        rows = [
            self._row("roles", "Roles", 2, 0),
            self._row("roles.scope", "Scope", 3, 1),
            self._row("review", "Review", 2, 2),
            self._retiree("roles.audit", "Audit (2 controls)", 3, 3),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {}
        assert "review.audit" not in remap.changes.values()
        assert remap.collisions == []

    def test_a_retiree_still_loses_its_count_under_its_stored_parent(self):
        # Freezing the parent must not exempt the leaf: the migration exists to
        # strip counts, and a retiree carrying one would still be stranded.
        rows = [
            self._row("gov", "GOV", 2, 0),
            self._retiree("gov.audit-2-controls", "Audit (2 controls)", 3, 1),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {"gov.audit-2-controls": "gov.audit"}

    def test_a_retiree_follows_a_rename_of_its_stored_parent(self):
        rows = [
            self._row("gov-12-controls", "GOV (12 controls)", 2, 0),
            self._retiree("gov-12-controls.audit-2-controls",
                          "Audit (2 controls)", 3, 1),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {
            "gov-12-controls": "gov",
            "gov-12-controls.audit-2-controls": "gov.audit",
        }

    def test_a_retiree_never_parents_a_row_that_follows_it(self):
        rows = [
            self._row("gov", "GOV", 2, 0),
            self._retiree("gov.audit", "Audit", 3, 1),
            self._row("notes-3-controls", "Notes (3 controls)", 2, 2),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {"notes-3-controls": "notes"}

    def test_the_collision_guard_still_covers_a_retiree(self):
        # The live sibling already holds the slug the retiree would take.
        rows = [
            self._row("gov", "GOV", 2, 0),
            self._row("gov.audit", "Audit", 3, 1),
            self._retiree("gov.audit-2-controls", "Audit (2 controls)", 3, 2),
        ]
        remap = recompute_section_ids(rows)
        assert remap.changes == {}
        assert remap.collisions == ["gov.audit-2-controls"]

    def test_a_row_without_a_status_is_treated_as_live(self):
        # The key is optional; callers predating it must keep the walk.
        rows = [
            {"section_id": "gov", "heading_text": "GOV",
             "heading_level": 2, "ordinal": 0},
            {"section_id": "child-1-control", "heading_text": "Child (1 control)",
             "heading_level": 3, "ordinal": 1},
        ]
        assert recompute_section_ids(rows).changes == {
            "child-1-control": "gov.child",
        }


class TestPairSectionsToHeadings:
    """Stored rows to document headings, by identity.

    The mapping this produces decides which heading a per-section action acts
    on, so "close enough" is indistinguishable from acting on the wrong
    section. Rows are given as plain dicts here; the API passes ORM objects and
    both shapes are supported deliberately.
    """

    #: "Scope" was retired out of "Roles" and is re-rendered at the end at its
    #: original depth, and a human edit introduced a heading of its own. Five
    #: headings, four rows, and no positional correspondence at all.
    DOC = (
        "# Policy\n\n"
        "## Roles\n\n"
        "### Responsibilities\n\n"
        "Typed by a human.\n\n"
        "## Review\n\n"
        "Review body.\n\n"
        "### Scope\n\n"
        "Old scope text.\n"
    )

    ROWS = [
        {"section_id": "policy", "heading_text": "Policy", "heading_level": 1, "ordinal": 0},
        {"section_id": "policy.roles", "heading_text": "Roles", "heading_level": 2, "ordinal": 1},
        {"section_id": "policy.review", "heading_text": "Review", "heading_level": 2, "ordinal": 2},
        {
            "section_id": "policy.roles.scope",
            "heading_text": "Scope",
            "heading_level": 3,
            "ordinal": 3,
        },
    ]

    def test_the_retiree_is_found_by_heading_not_by_id_or_position(self):
        pairing = pair_sections_to_headings(self.DOC, self.ROWS)
        # Index 4 is the last heading, "### Scope". Its parsed id is
        # policy.review.scope; its stored id is policy.roles.scope.
        assert pairing.heading_index["policy.roles.scope"] == 4
        assert pairing.unmatched == []

    def test_unclaimed_headings_are_reported(self):
        pairing = pair_sections_to_headings(self.DOC, self.ROWS)
        # "### Responsibilities" -- the human-introduced heading, index 2.
        assert pairing.unclaimed == [2]

    def test_exact_ids_are_matched_before_any_looser_pass(self):
        # Two sections share the heading text "Scope": one live under Roles,
        # one retired to the end. If the ghost were allowed to grab a heading
        # by text before the live section claimed its own by id, the two would
        # swap and every action on either would hit the other.
        doc = (
            "# Policy\n\n"
            "## Roles\n\n"
            "### Scope\n\nLive scope.\n\n"
            "## Review\n\nReview body.\n\n"
            "### Scope\n\nGhost scope.\n"
        )
        rows = [
            {"section_id": "policy", "heading_text": "Policy", "heading_level": 1, "ordinal": 0},
            {
                "section_id": "policy.roles",
                "heading_text": "Roles",
                "heading_level": 2,
                "ordinal": 1,
            },
            {
                "section_id": "policy.roles.scope",
                "heading_text": "Scope",
                "heading_level": 3,
                "ordinal": 2,
            },
            {
                "section_id": "policy.review",
                "heading_text": "Review",
                "heading_level": 2,
                "ordinal": 3,
            },
            # The retiree, appended last. Its stored parent no longer precedes it.
            {
                "section_id": "policy.governance.scope",
                "heading_text": "Scope",
                "heading_level": 3,
                "ordinal": 4,
            },
        ]
        pairing = pair_sections_to_headings(doc, rows)
        assert pairing.heading_index["policy.roles.scope"] == 2
        assert pairing.heading_index["policy.governance.scope"] == 4

    def test_a_row_with_no_heading_is_reported_not_guessed(self):
        rows = self.ROWS + [
            {
                "section_id": "policy.gone",
                "heading_text": "Gone",
                "heading_level": 2,
                "ordinal": 9,
            }
        ]
        pairing = pair_sections_to_headings(self.DOC, rows)
        assert pairing.unmatched == ["policy.gone"]
        assert "policy.gone" not in pairing.heading_index

    def test_no_two_rows_share_a_heading(self):
        pairing = pair_sections_to_headings(self.DOC, self.ROWS)
        indices = list(pairing.heading_index.values())
        assert len(indices) == len(set(indices))

    def test_a_renamed_ancestor_is_matched_by_text_and_level(self):
        # The section itself is untouched; only its parent heading changed, so
        # its stored id carries a parent path that no longer exists.
        doc = "# Policy\n\n## Governance\n\n### Scope\n\nBody.\n"
        rows = [
            {"section_id": "policy", "heading_text": "Policy", "heading_level": 1, "ordinal": 0},
            {
                "section_id": "policy.governance",
                "heading_text": "Governance",
                "heading_level": 2,
                "ordinal": 1,
            },
            {
                "section_id": "policy.roles.scope",
                "heading_text": "Scope",
                "heading_level": 3,
                "ordinal": 2,
            },
        ]
        pairing = pair_sections_to_headings(doc, rows)
        assert pairing.heading_index["policy.roles.scope"] == 2

    def test_a_level_change_falls_through_to_text_alone(self):
        doc = "# Policy\n\n## Scope\n\nBody.\n"
        rows = [
            {"section_id": "policy", "heading_text": "Policy", "heading_level": 1, "ordinal": 0},
            {
                "section_id": "policy.scope",
                "heading_text": "Scope",
                "heading_level": 3,
                "ordinal": 1,
            },
        ]
        pairing = pair_sections_to_headings(doc, rows)
        assert pairing.heading_index["policy.scope"] == 1

    def test_object_rows_work_as_well_as_mappings(self):
        from types import SimpleNamespace

        rows = [SimpleNamespace(**row) for row in self.ROWS]
        assert pair_sections_to_headings(self.DOC, rows).heading_index[
            "policy.roles.scope"
        ] == 4

    def test_an_empty_document_matches_nothing_and_says_so(self):
        pairing = pair_sections_to_headings("", self.ROWS)
        assert pairing.heading_index == {}
        assert len(pairing.unmatched) == len(self.ROWS)


class TestHashableBody:
    """A regeneration restamps ``**Generated:**`` even when nothing changed.

    Hashing that stamp made the first section of every Tier 1 document report
    ``updated`` on every run. These pin the normalisation that stops it without
    hiding a genuine change on the same line's neighbours.
    """

    ONE = (
        "**Generated:** 2026-08-22T09:02:35.031061+00:00\n"
        "**Controls in Scope:** 346\n"
        "**Domains:** 31"
    )
    TWO = (
        "**Generated:** 2026-08-22T09:03:14.921964+00:00\n"
        "**Controls in Scope:** 346\n"
        "**Domains:** 31"
    )

    def test_two_runs_differing_only_in_the_stamp_compare_equal(self):
        assert hashable_body(self.ONE) == hashable_body(self.TWO)

    def test_a_real_change_beside_the_stamp_still_shows(self):
        moved = self.TWO.replace("346", "347")
        assert hashable_body(self.TWO) != hashable_body(moved)

    def test_the_stamp_is_replaced_not_deleted_so_offsets_hold(self):
        assert hashable_body(self.ONE).count("\n") == self.ONE.count("\n")
        assert "**Generated:**" in hashable_body(self.ONE)

    def test_a_body_with_no_stamp_is_returned_untouched(self):
        body = "Some prose.\n\n- a bullet\n"
        assert hashable_body(body) == body

    def test_every_tier1_document_is_covered_not_just_the_soa(self):
        """``tier1._header`` emits the stamp for all five generators."""
        for title in (
            "Statement of Applicability",
            "Control Status Report",
            "Risk Treatment Plan",
            "Evidence Schedule",
            "Maturity Assessment Report",
        ):
            a = f"# {title}\n\n**Generated:** 2026-01-01T00:00:00+00:00"
            b = f"# {title}\n\n**Generated:** 2026-06-30T23:59:59+00:00"
            assert hashable_body(a) == hashable_body(b)

    def test_the_hash_of_a_parsed_section_ignores_the_stamp(self):
        """End to end through the parser, not just the helper."""
        def parse(ts):
            doc = (
                "# Statement of Applicability\n"
                "## Acme Ltd\n\n"
                f"**Generated:** {ts}\n"
                "**Controls in Scope:** 346\n"
            )
            return flatten_sections(parse_markdown_sections(doc))

        first = parse("2026-08-22T09:02:35.031061+00:00")
        second = parse("2026-08-22T09:03:14.921964+00:00")
        hashes = {s.section_id: s.content_hash for s in first}
        assert hashes == {s.section_id: s.content_hash for s in second}
        assert hashes  # guard against both sides being empty
