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
