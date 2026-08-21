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
