"""Chaptered generation of bundled documents.

Assembly is where a bundled document can quietly go wrong: a chapter heading
at the wrong level re-parents everything after it, and a frame that writes its
own core section duplicates the section ids. Both are tested here, as is the
chapter cache that keeps one domain's change from rewriting every chapter.
"""
import pytest

from services.doc_gen import chaptered, scopes, tier2
from services.doc_gen.context import Domain, DomainWithControls, EnrichedControl, OrganisationContext
from services.doc_gen.registry import get_generator
from services.doc_gen.section_parser import flatten_sections, parse_markdown_sections

FRAME = """## Document Control

| Field | Value |
|-------|-------|
| **Version** | 1.0 |

### Change History

| Version | Date | Author | Description of Changes |
|---|---|---|---|
| 1.0 | 2026-10-28 | CISO function | Initial issue |

## Purpose

Why.

## Scope

What.

## Roles and Responsibilities

Who.

## Review and Revision

When.
"""

LAYOUT = chaptered.LAYOUTS["policy"]


def _control(scf_id, domain, desc="Do the thing."):
    return EnrichedControl(
        scf_id=scf_id, control_name=f"Control {scf_id}", control_description=desc,
        domain_identifier=domain, implementation_status="implemented",
    )


def _chapter(ident, name, *ids):
    return DomainWithControls(
        domain=Domain(identifier=ident, name=name, principle=f"{name} principle"),
        controls=[_control(i, ident) for i in ids],
    )


CTX = OrganisationContext(
    organization_id="org", name="Acme Ltd", generated_at="2026-10-28T12:00:00+00:00",
)
SCOPE = scopes.Scope(key="B-TEST", kind="bundle", title="Test Bundle", domains=("IAC", "CRY"))
CHAPTERS = [_chapter("IAC", "Identification & Authentication", "IAC-01", "IAC-02"),
            _chapter("CRY", "Cryptographic Protections", "CRY-01")]


def _ids(markdown):
    return [s.section_id for s in flatten_sections(parse_markdown_sections(markdown))]


class TestNormaliseChapter:
    def test_shallow_headings_are_demoted_to_level_four(self):
        out = chaptered.normalise_chapter("## Oops\n\ntext\n\n### Also\n\nmore", "IAC")
        assert out.splitlines()[0] == "#### Oops"
        assert "#### Also" in out

    def test_a_leading_heading_repeating_the_domain_name_is_dropped(self):
        out = chaptered.normalise_chapter("### Cryptographic Protections\n\n#### Keys\n\nx",
                                          "Cryptographic Protections")
        assert out.startswith("#### Keys")

    def test_hashes_inside_a_code_fence_are_left_alone(self):
        out = chaptered.normalise_chapter("#### A\n\n```\n# not a heading\n```", "X")
        assert "# not a heading" in out


class TestAssemble:
    def test_the_core_section_goes_before_roles(self):
        doc = chaptered.assemble(FRAME, [("Identification & Authentication", "#### Accounts\n\nx [IAC-01]")], LAYOUT)
        ids = _ids(doc)
        assert ids.index("scope") < ids.index("policy-statements") < ids.index("roles-and-responsibilities")
        assert "policy-statements.identification-authentication.accounts" in ids

    def test_a_numbered_heading_is_still_found(self):
        frame = FRAME.replace("## Roles and Responsibilities", "## 4. Roles and Responsibilities")
        doc = chaptered.assemble(frame, [("A", "#### B\n\nc")], LAYOUT)
        assert doc.index("## Policy Statements") < doc.index("## 4. Roles")

    def test_without_the_anchor_heading_chapters_follow_scope(self):
        frame = FRAME.replace("## Roles and Responsibilities\n\nWho.\n\n", "")
        doc = chaptered.assemble(frame, [("A", "#### B\n\nc")], LAYOUT)
        assert doc.index("## Scope") < doc.index("## Policy Statements") < doc.index("## Review")

    def test_a_core_section_written_by_the_frame_is_replaced_not_duplicated(self):
        frame = FRAME.replace("## Roles", "## Policy Statements\n\nThe model wrote these.\n\n## Roles")
        doc = chaptered.assemble(frame, [("A", "#### B\n\nc")], LAYOUT)
        assert doc.count("## Policy Statements") == 1
        assert "The model wrote these." not in doc


class TestPrompts:
    def test_the_frame_is_told_its_scope_and_related_documents(self):
        prompt = chaptered.build_frame_prompt(
            get_generator("policy"), CTX, SCOPE, CHAPTERS, ["Access Control Policy"],
        )
        assert "POL-B-TEST-2026" in prompt
        assert "- IAC — Identification & Authentication" in prompt
        assert "- Access Control Policy" in prompt
        assert "Do NOT write a Policy Statements section" in prompt

    def test_folded_profiles_ask_policy_chapters_for_requirements(self):
        spec = get_generator("policy")
        folded = chaptered.build_chapter_prompt(spec, CTX, SCOPE, CHAPTERS[0], scopes.get_profile("compact"))
        plain = chaptered.build_chapter_prompt(spec, CTX, SCOPE, CHAPTERS[0], scopes.get_profile("consolidated"))
        assert "#### Requirements" in folded
        assert "#### Requirements" not in plain

    def test_the_fingerprint_prompt_changes_when_one_chapter_changes(self):
        spec, profile = get_generator("policy"), scopes.get_profile("consolidated")
        before = chaptered.fingerprint_prompt(spec, CTX, SCOPE, CHAPTERS, profile, [])
        changed = [CHAPTERS[0], _chapter("CRY", "Cryptographic Protections", "CRY-01", "CRY-02")]
        assert chaptered.fingerprint_prompt(spec, CTX, SCOPE, changed, profile, []) != before


class TestGenerateBundle:
    @pytest.fixture
    def live(self, monkeypatch):
        """Pretend a key is configured and record every model call."""
        calls = []
        monkeypatch.setattr(tier2, "is_mock_mode", lambda: False)
        monkeypatch.setattr(tier2, "resolve_model", lambda: "test-model")

        def fake(prompt, *, label):
            calls.append(label)
            return FRAME if label.endswith(":frame") else "#### Statements\n\nShall. [X-01]"

        monkeypatch.setattr(tier2, "invoke_model", fake)
        return calls

    def test_every_chapter_and_the_frame_are_written_once(self, live):
        out = chaptered.generate_bundle(
            get_generator("policy"), CTX, SCOPE, CHAPTERS, scopes.get_profile("consolidated"), [],
        )
        assert live == ["policy:B-TEST:IAC", "policy:B-TEST:CRY", "policy:B-TEST:frame"]
        assert set(out.chapter_cache) == {"IAC", "CRY"}
        assert "### Cryptographic Protections" in out.content

    def test_unchanged_chapters_are_reused_and_changed_ones_rewritten(self, live):
        spec, profile = get_generator("policy"), scopes.get_profile("consolidated")
        first = chaptered.generate_bundle(spec, CTX, SCOPE, CHAPTERS, profile, [])
        live.clear()
        changed = [CHAPTERS[0], _chapter("CRY", "Cryptographic Protections", "CRY-01", "CRY-02")]
        second = chaptered.generate_bundle(
            spec, CTX, SCOPE, changed, profile, [], chapter_cache=first.chapter_cache,
        )
        assert live == ["policy:B-TEST:CRY", "policy:B-TEST:frame"]
        assert second.reused_chapters == ["IAC"]

    def test_a_mock_chapter_is_never_reused_once_a_key_is_configured(self, live, monkeypatch):
        spec, profile = get_generator("policy"), scopes.get_profile("consolidated")
        monkeypatch.setattr(tier2, "is_mock_mode", lambda: True)
        mocked = chaptered.generate_bundle(spec, CTX, SCOPE, CHAPTERS, profile, [])
        assert mocked.mocked and live == []
        monkeypatch.setattr(tier2, "is_mock_mode", lambda: False)
        real = chaptered.generate_bundle(
            spec, CTX, SCOPE, CHAPTERS, profile, [], chapter_cache=mocked.chapter_cache,
        )
        assert real.reused_chapters == []
        assert len(live) == 3

    def test_mock_output_parses_into_per_chapter_sections(self):
        out = chaptered.generate_bundle(
            get_generator("policy"), CTX, SCOPE, CHAPTERS, scopes.get_profile("compact"), [],
        ) if tier2.is_mock_mode() else None
        if out is None:
            pytest.skip("an API key is configured in this environment")
        ids = _ids(out.content)
        assert "policy-statements.identification-authentication" in ids
        assert "policy-statements.cryptographic-protections.requirements" in ids
        assert out.content.startswith("> **MOCK OUTPUT**")


PROCEDURE_FRAME = """## Document Control

| Field | Value |
|-------|-------|
| **Version** | 1.0 |

## Purpose

Why.

## Scope and Applicability

What.

## Prerequisites

Before.

## Exception Handling

Unless.

## Records and Retention

Kept.
"""

GROUP = scopes.resolve_key("T-ACCESS-MGMT")
ACTIVITIES = [_chapter("T-ACCESS", "Joiners, Movers & Leavers", "IAC-04", "HRS-08"),
              _chapter("T-IDENTITY", "Authentication, Privileged Access & Access Review", "IAC-02")]
NOTE = chaptered.not_applicable_note("Physical Access & Visitor Control")


class TestGroupedProcedures:
    """A procedure for a group of topics is written a chapter per topic."""

    def test_procedures_have_a_chapter_layout(self):
        layout = chaptered.layout_for(get_generator("procedure"))
        assert (layout.core_heading, layout.insert_before, layout.identifier_prefix) == (
            "Procedure Steps", "Exception Handling", "PRO")

    def test_the_steps_go_between_prerequisites_and_exceptions(self):
        doc = chaptered.assemble(
            PROCEDURE_FRAME, [("Joiners, Movers & Leavers", "#### Grant access\n\nx [IAC-04]")],
            chaptered.LAYOUTS["procedure"],
        )
        ids = _ids(doc)
        assert ids.index("prerequisites") < ids.index("procedure-steps") < ids.index("exception-handling")
        assert "procedure-steps.joiners-movers-leavers.grant-access" in ids

    def test_a_topic_with_nothing_in_scope_is_named_in_the_scope_section(self):
        doc = chaptered.assemble(
            PROCEDURE_FRAME, [("A", "#### B\n\nc")], chaptered.LAYOUTS["procedure"], [NOTE],
        )
        assert NOTE == ("Physical Access & Visitor Control: Not applicable in this "
                        "organisation's scope — see the Statement of Applicability.")
        scope_section = doc[doc.index("## Scope and Applicability"):doc.index("## Prerequisites")]
        assert f"- {NOTE}" in scope_section
        assert doc.count(NOTE) == 1

    def test_the_note_is_not_repeated_when_the_frame_already_wrote_it(self):
        frame = PROCEDURE_FRAME.replace("What.", f"What.\n\n- {NOTE}")
        doc = chaptered.assemble(frame, [("A", "#### B\n\nc")], chaptered.LAYOUTS["procedure"], [NOTE])
        assert doc.count(NOTE) == 1

    def test_without_a_scope_section_the_note_opens_the_steps(self):
        frame = PROCEDURE_FRAME.replace("## Scope and Applicability\n\nWhat.\n\n", "")
        doc = chaptered.assemble(frame, [("A", "#### B\n\nc")], chaptered.LAYOUTS["procedure"], [NOTE])
        assert doc.index("## Procedure Steps") < doc.index(NOTE) < doc.index("### A")

    def test_no_notes_leaves_the_frame_as_written(self):
        layout = chaptered.LAYOUTS["procedure"]
        assert chaptered.assemble(PROCEDURE_FRAME, [("A", "#### B\n\nc")], layout) == \
            chaptered.assemble(PROCEDURE_FRAME, [("A", "#### B\n\nc")], layout, [])

    def test_the_frame_names_its_activities_and_the_ones_out_of_scope(self):
        prompt = chaptered.build_frame_prompt(
            get_generator("procedure"), CTX, GROUP, ACTIVITIES, ["Access Control Policy"],
            scope_notes=[NOTE],
        )
        assert "PRO-T-ACCESS-MGMT-2026" in prompt
        assert "- Joiners, Movers & Leavers: " in prompt
        # Topic keys are internal: outside the document identifier they must
        # not reach the model, or they turn up in the prose.
        assert "T-ACCESS —" not in prompt and "T-IDENTITY" not in prompt
        assert f"- {NOTE}" in prompt
        assert "Do NOT write a Procedure Steps section" in prompt
        assert "- Access Control Policy" in prompt

    def test_the_chapter_prompt_is_about_one_activity(self):
        prompt = chaptered.build_chapter_prompt(
            get_generator("procedure"), CTX, GROUP, ACTIVITIES[0], scopes.get_profile("compact"),
        )
        assert "Joiners, Movers & Leavers" in prompt
        assert "IAC-04" in prompt and "IAC-02" not in prompt

    def test_a_topic_leaving_scope_changes_the_fingerprint(self):
        # Otherwise the document would not read as stale when a whole member
        # topic is descoped, and its not-applicable line would never appear.
        spec, profile = get_generator("procedure"), scopes.get_profile("compact")
        before = chaptered.fingerprint_prompt(spec, CTX, GROUP, ACTIVITIES[:1], profile, [])
        after = chaptered.fingerprint_prompt(
            spec, CTX, GROUP, ACTIVITIES[:1], profile, [], scope_notes=[NOTE])
        assert before != after

    def test_each_activity_is_written_once_and_cached_by_topic_key(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tier2, "is_mock_mode", lambda: False)
        monkeypatch.setattr(tier2, "resolve_model", lambda: "test-model")

        def fake(prompt, *, label):
            calls.append(label)
            return PROCEDURE_FRAME if label.endswith(":frame") else "#### Grant access\n\nDo. [IAC-04]"

        monkeypatch.setattr(tier2, "invoke_model", fake)
        out = chaptered.generate_bundle(
            get_generator("procedure"), CTX, GROUP, ACTIVITIES, scopes.get_profile("compact"), [],
            scope_notes=[NOTE],
        )
        assert calls == ["procedure:T-ACCESS-MGMT:T-ACCESS", "procedure:T-ACCESS-MGMT:T-IDENTITY",
                         "procedure:T-ACCESS-MGMT:frame"]
        assert set(out.chapter_cache) == {"T-ACCESS", "T-IDENTITY"}
        assert "### Joiners, Movers & Leavers" in out.content
        assert "### Authentication, Privileged Access & Access Review" in out.content
        assert f"- {NOTE}" in out.content

    def test_mock_output_has_the_procedure_sections(self):
        if not tier2.is_mock_mode():
            pytest.skip("an API key is configured in this environment")
        out = chaptered.generate_bundle(
            get_generator("procedure"), CTX, GROUP, ACTIVITIES, scopes.get_profile("compact"), [],
            scope_notes=[NOTE],
        )
        ids = _ids(out.content)
        assert ids.index("procedure-steps") < ids.index("exception-handling")
        assert "procedure-steps.joiners-movers-leavers" in ids
        assert NOTE in out.content


class TestSeededChapters:
    """A new group's chapters start from the procedures the group supersedes."""

    SEED = "## Procedure Steps\n\n1. The service desk raises a ticket in Halo."

    def _prompt(self, seed=""):
        return chaptered.build_chapter_prompt(
            get_generator("procedure"), CTX, GROUP, ACTIVITIES[0],
            scopes.get_profile("compact"), seed=seed,
        )

    def test_no_seed_leaves_the_prompt_exactly_as_it_was(self):
        # Chapter caches are keyed on the prompt: a changed byte here would
        # rewrite every cached chapter of every grouped procedure.
        assert self._prompt("") == chaptered.build_chapter_prompt(
            get_generator("procedure"), CTX, GROUP, ACTIVITIES[0], scopes.get_profile("compact"))
        assert "EXISTING PROCEDURE" not in self._prompt("")

    def test_the_seed_reaches_the_chapter_prompt(self):
        prompt = self._prompt(self.SEED)
        assert "EXISTING PROCEDURE FOR THIS ACTIVITY" in prompt
        assert "The service desk raises a ticket in Halo." in prompt
        assert prompt.index("EXISTING PROCEDURE") > prompt.index("IAC-04")

    def test_a_long_seed_is_cut_not_sent_whole(self):
        prompt = self._prompt("x" * (chaptered.SEED_LIMIT + 5000))
        assert prompt.count("x" * 1000) <= chaptered.SEED_LIMIT // 1000

    def test_a_seed_changes_the_fingerprint(self):
        spec, profile = get_generator("procedure"), scopes.get_profile("compact")
        plain = chaptered.fingerprint_prompt(spec, CTX, GROUP, ACTIVITIES[:1], profile, [])
        same = chaptered.fingerprint_prompt(spec, CTX, GROUP, ACTIVITIES[:1], profile, [], seeds={})
        seeded = chaptered.fingerprint_prompt(
            spec, CTX, GROUP, ACTIVITIES[:1], profile, [],
            seeds={ACTIVITIES[0].domain.identifier: self.SEED})
        assert plain == same != seeded

    def test_the_seed_is_kept_with_the_chapter_so_a_later_run_reuses_it(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tier2, "is_mock_mode", lambda: False)
        monkeypatch.setattr(tier2, "resolve_model", lambda: "test-model")

        def fake(prompt, *, label):
            calls.append(label)
            return PROCEDURE_FRAME if label.endswith(":frame") else "#### Grant access\n\nx [IAC-04]"

        monkeypatch.setattr(tier2, "invoke_model", fake)
        spec, profile = get_generator("procedure"), scopes.get_profile("compact")
        ident = ACTIVITIES[0].domain.identifier
        first = chaptered.generate_bundle(
            spec, CTX, GROUP, ACTIVITIES, profile, [], seeds={ident: self.SEED})
        assert first.chapter_cache[ident]["seed"] == self.SEED
        assert all("seed" not in v for k, v in first.chapter_cache.items() if k != ident)

        # What the pipeline does on every later run: hand the stored seeds back.
        stored = {k: v["seed"] for k, v in first.chapter_cache.items() if v.get("seed")}
        calls.clear()
        second = chaptered.generate_bundle(
            spec, CTX, GROUP, ACTIVITIES, profile, [],
            chapter_cache=first.chapter_cache, seeds=stored)
        assert sorted(second.reused_chapters) == sorted(a.domain.identifier for a in ACTIVITIES)
        assert second.chapter_cache[ident]["seed"] == self.SEED
        # Without them the chapter's prompt changes and it is written again.
        calls.clear()
        chaptered.generate_bundle(
            spec, CTX, GROUP, ACTIVITIES, profile, [], chapter_cache=first.chapter_cache)
        assert any(label.endswith(f":{ident}") for label in calls)
