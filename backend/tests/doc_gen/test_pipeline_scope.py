"""The post-generation scope check.

``build_context`` restricts every generator's input to controls with
``selected = True``, and the Tier 2 prompt templates forbid going beyond that
set. Neither is a guarantee -- a prompt instructs a model, it does not
constrain one. These tests cover the check that makes drift visible instead of
silent.
"""

from services.doc_gen.pipeline import GenerationResult, find_out_of_scope_citations


IN_SCOPE = ["GOV-01", "GOV-02", "AST-01"]


class TestFindOutOfScopeCitations:
    def test_a_document_citing_only_scoped_controls_is_clean(self):
        content = "Leadership approves the programme [GOV-01] annually [GOV-02]."
        assert find_out_of_scope_citations(content, IN_SCOPE) == []

    def test_a_control_outside_the_scoped_set_is_reported(self):
        content = "Assets are inventoried [AST-01] and encrypted [CRY-03]."
        assert find_out_of_scope_citations(content, IN_SCOPE) == ["CRY-03"]

    def test_every_out_of_scope_citation_is_reported_not_just_the_first(self):
        content = "[CRY-03] and [IAC-07] and [GOV-01]."
        assert find_out_of_scope_citations(content, IN_SCOPE) == ["CRY-03", "IAC-07"]

    def test_the_report_is_deduplicated(self):
        content = "[CRY-03] here, [CRY-03] there, [CRY-03] everywhere."
        assert find_out_of_scope_citations(content, IN_SCOPE) == ["CRY-03"]

    def test_unbracketed_prose_is_not_a_citation(self):
        # Naming a neighbouring topic in prose is exactly what the prompt now
        # tells the model to do instead of citing it, so it must not trip.
        content = "Cryptographic controls are covered by CRY-03 elsewhere."
        assert find_out_of_scope_citations(content, IN_SCOPE) == []

    def test_an_empty_document_is_clean(self):
        assert find_out_of_scope_citations("", IN_SCOPE) == []

    def test_an_empty_scope_makes_every_citation_out_of_scope(self):
        assert find_out_of_scope_citations("[GOV-01]", []) == ["GOV-01"]

    def test_the_scoped_set_may_be_any_iterable(self):
        # ``run_generation`` passes a generator expression over ctx.all_controls.
        gen = (c for c in IN_SCOPE)
        assert find_out_of_scope_citations("[GOV-01] [CRY-03]", gen) == ["CRY-03"]


class TestGenerationResultCarriesTheFinding:
    def test_a_clean_run_reports_an_empty_list_not_a_missing_key(self):
        result = GenerationResult(
            document_id="d", generator_name="g", domain_id=None,
            action="created", title="T",
        )
        assert result.to_dict()["out_of_scope_citations"] == []

    def test_the_finding_reaches_the_dict_the_api_returns(self):
        result = GenerationResult(
            document_id="d", generator_name="g", domain_id=None,
            action="created", title="T",
            out_of_scope_citations=["CRY-03"],
        )
        assert result.to_dict()["out_of_scope_citations"] == ["CRY-03"]

    def test_the_dict_holds_a_copy_so_callers_cannot_mutate_the_result(self):
        result = GenerationResult(
            document_id="d", generator_name="g", domain_id=None,
            action="created", title="T",
            out_of_scope_citations=["CRY-03"],
        )
        result.to_dict()["out_of_scope_citations"].append("IAC-07")
        assert result.out_of_scope_citations == ["CRY-03"]


class TestTopicChapters:
    """A grouped procedure is written one chapter per member topic."""

    NAMES = {"HRS": "Human Resources Security", "SAT": "Security Awareness & Training",
             "PES": "Physical & Environmental Security", "IAC": "Identification & Authentication"}

    @staticmethod
    def _control(scf_id):
        from services.doc_gen.context import EnrichedControl
        return EnrichedControl(
            scf_id=scf_id, control_name=scf_id, control_description="Do the thing.",
            domain_identifier=scf_id.split("-")[0], implementation_status="implemented",
        )

    def _chapters(self, key, *ids):
        from services.doc_gen import scopes
        from services.doc_gen.pipeline import topic_chapters
        return topic_chapters(scopes.resolve_key(key), [self._control(i) for i in ids], self.NAMES)

    def test_two_members_in_scope_and_one_not_give_two_chapters_and_a_line(self):
        # Nothing is in scope for the third member, physical access.
        from services.doc_gen import scopes
        from services.doc_gen.pipeline import topic_chapters
        group = scopes.Scope(
            key="T-TEST", kind="topic", title="Test group", domains=("HRS", "SAT", "PES", "IAC"),
            members=(scopes.resolve_key("T-ACCESS"), scopes.resolve_key("T-PEOPLE"),
                     scopes.resolve_key("T-PHYSICAL")),
        )
        controls = [self._control(i) for i in ("IAC-04.1", "HRS-08", "HRS-05", "SAT-02")]
        chapters, notes = topic_chapters(group, controls, self.NAMES)
        assert [c.domain.identifier for c in chapters] == ["T-ACCESS", "T-PEOPLE"]
        assert [[x.scf_id for x in c.controls] for c in chapters] == [
            ["IAC-04.1", "HRS-08"], ["HRS-05", "SAT-02"]]
        assert notes == ["Physical Access & Visitor Control: Not applicable in this "
                         "organisation's scope — see the Statement of Applicability."]

    def test_a_chapter_presents_the_topic_as_a_domain(self):
        chapters, _ = self._chapters("T-ACCESS-MGMT", "IAC-04", "IAC-02")
        first = chapters[0].domain
        assert first.name == "Joiners, Movers & Leavers"
        assert first.principle.startswith("Granting, changing, reviewing and removing access")
        assert first.principle_intent == (
            "Operational procedure drawing on: Human Resources Security, "
            "Identification & Authentication")

    def test_a_control_is_in_one_chapter_only(self):
        chapters, notes = self._chapters("T-ACCESS-MGMT", "IAC-04", "IAC-04.1", "IAC-02", "HRS-12")
        assert notes == []
        assert [[x.scf_id for x in c.controls] for c in chapters] == [
            ["IAC-04", "IAC-04.1", "HRS-12"], ["IAC-02"]]

    def test_a_group_with_one_member_in_scope_still_names_the_other(self):
        chapters, notes = self._chapters("T-PEOPLE-WORKPLACE", "SAT-02")
        assert [c.domain.identifier for c in chapters] == ["T-PEOPLE"]
        assert len(notes) == 1 and notes[0].startswith("Physical Access & Visitor Control:")


class TestChapterSeeds:
    """Which superseded procedure each chapter of a new group starts from."""

    LEAF = ("# Incident Response Procedure\n\n## Document Control\n\n| Version | 3 |\n\n"
            "### Change History\n\nold\n\n## Purpose\n\nWhy.\n\n## Procedure Steps\n\n"
            "1. Phone the duty manager.\n")
    GROUP = ("# Log Review & Incident Response Procedure\n\n## Procedure Steps\n\n"
             "### Log Review & Security Monitoring\n\n#### Weekly review\n\nRead the SIEM queue.\n\n"
             "### Incident Response\n\n#### Triage\n\nPage the on-call lead.\n\n"
             "## Exception Handling\n\nNone.\n")

    @staticmethod
    def _seeds(key, *predecessors):
        from datetime import datetime, timezone
        from services.doc_gen import scopes
        from services.doc_gen.pipeline import Predecessor, chapter_seeds
        return chapter_seeds(scopes.resolve_key(key), [
            Predecessor(key=k, content=c, updated_at=datetime(2026, 1, day, tzinfo=timezone.utc))
            for k, c, day in predecessors
        ])

    def test_a_leaf_procedure_seeds_its_own_chapter_without_document_control(self):
        seeds, used = self._seeds("T-DETECT-RESPOND", ("T-INCIDENT", self.LEAF, 1))
        assert used == ["T-INCIDENT"] and list(seeds) == ["T-INCIDENT"]
        assert "Phone the duty manager." in seeds["T-INCIDENT"]
        assert "Document Control" not in seeds["T-INCIDENT"]
        assert "Change History" not in seeds["T-INCIDENT"]
        assert "# Incident Response Procedure" not in seeds["T-INCIDENT"]

    def test_a_group_procedure_seeds_each_leaf_from_its_own_chapter(self):
        seeds, used = self._seeds("T-INCIDENT-RESILIENCE", ("T-DETECT-RESPOND", self.GROUP, 1))
        assert used == ["T-DETECT-RESPOND"]
        assert seeds["T-MONITORING"] == "#### Weekly review\n\nRead the SIEM queue."
        assert seeds["T-INCIDENT"] == "#### Triage\n\nPage the on-call lead."
        assert "T-BACKUP" not in seeds and "T-CONTINUITY" not in seeds

    def test_the_more_recently_updated_predecessor_wins(self):
        seeds, used = self._seeds(
            "T-INCIDENT-RESILIENCE",
            ("T-INCIDENT", self.LEAF, 1), ("T-DETECT-RESPOND", self.GROUP, 2))
        assert "Page the on-call lead." in seeds["T-INCIDENT"]
        assert used == ["T-DETECT-RESPOND"]
        seeds, used = self._seeds(
            "T-INCIDENT-RESILIENCE",
            ("T-INCIDENT", self.LEAF, 3), ("T-DETECT-RESPOND", self.GROUP, 2))
        assert "Phone the duty manager." in seeds["T-INCIDENT"]
        # Named in the group's own order, not the order they were found.
        assert used == ["T-DETECT-RESPOND", "T-INCIDENT"]

    def test_markers_are_stripped_and_unrelated_keys_ignored(self):
        marked = self.LEAF.replace("## Purpose", "<!-- section:purpose -->\n## Purpose")
        seeds, used = self._seeds(
            "T-DETECT-RESPOND", ("T-INCIDENT", marked, 1), ("T-SUPPLIER", self.LEAF, 2))
        assert used == ["T-INCIDENT"]
        assert "<!--" not in seeds["T-INCIDENT"]

    def test_nothing_stored_seeds_nothing(self):
        assert self._seeds("T-DETECT-RESPOND") == ({}, [])
        from services.doc_gen import scopes
        from services.doc_gen.pipeline import chapter_seeds
        assert chapter_seeds(scopes.resolve_key("T-SUPPLIER"), []) == ({}, [])

    def test_the_run_and_the_history_say_what_it_was_seeded_from(self):
        from services.doc_gen.pipeline import _version_change_summary
        from services.doc_gen.three_layer import describe_version_summary
        summary = _version_change_summary({"new": 9}, 37, True, ["T-INCIDENT", "T-BACKUP"])
        assert summary["seeded_from"] == ["T-INCIDENT", "T-BACKUP"]
        assert describe_version_summary(summary) == (
            "Initial generation from 37 controls, seeded from T-INCIDENT, T-BACKUP.")
        assert "seeded_from" not in _version_change_summary({"new": 9}, 37, True)
        result = GenerationResult(
            document_id="d", generator_name="procedure", domain_id="T-X",
            action="created", title="T", seeded_from=["T-INCIDENT"])
        assert result.to_dict()["seeded_from"] == ["T-INCIDENT"]
