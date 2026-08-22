"""Tests for the Tier 1 renderers, Tier 2 prompt assembly, and export.

No database and no API key: everything here is a pure function over a context
object, which is exactly why the context is a dataclass rather than a session.
"""
import pytest

from services.doc_gen.context import (
    Domain,
    DomainWithControls,
    EnrichedControl,
    OrganisationContext,
)
from services.doc_gen.registry import get_generator
from services.doc_gen.renderer import (
    _css_string,
    build_masthead,
    export_markdown,
    _escape,
    markdown_to_html,
    markdown_to_reader_fragment,
    safe_filename,
)
from services.doc_gen.tier1 import _cell, render_soa, status_label
from services.doc_gen.tier2 import (
    build_user_prompt,
    compute_doc_version,
    extract_change_history,
    is_mock_mode,
)


def _control(scf_id, name, status="implemented", **kw):
    return EnrichedControl(
        scf_id=scf_id,
        control_name=name,
        control_description=f"{name} description.",
        domain_identifier=kw.pop("domain", "GOV"),
        implementation_status=status,
        maturity_level=kw.pop("maturity", "L3"),
        owner=kw.pop("owner", "Security Manager"),
        **kw,
    )


@pytest.fixture
def ctx():
    controls = [
        _control("AAA-01", "First Control"),
        _control("AAA-02", "Second Control", status="in_progress", maturity="L1"),
    ]
    bundle = DomainWithControls(
        domain=Domain(identifier="GOV", name="Governance", principle="Be governed.", order=1),
        controls=controls,
        maturity_breakdown={"L3": 1, "L1": 1},
        status_breakdown={"implemented": 1, "in_progress": 1},
    )
    return OrganisationContext(
        organization_id="org-1",
        name="Acme Holdings",
        generated_at="2026-08-21T10:00:00+00:00",
        catalog_version="2026.2",
        domains=[bundle],
        all_controls=controls,
        maturity_distribution={"L3": 1, "L1": 1},
        status_distribution={"implemented": 1, "in_progress": 1},
        total_scoped_controls=2,
        total_domains=1,
    )


# ---------------------------------------------------------------------------
# Table-cell safety
# ---------------------------------------------------------------------------


def test_pipes_in_content_cannot_break_a_table_row():
    """A control name containing a pipe would otherwise silently add a column."""
    assert "|" not in _cell("Access | Control")


def test_newlines_in_content_cannot_break_a_table_row():
    assert "\n" not in _cell("line one\nline two")


def test_empty_values_render_as_an_em_dash():
    assert _cell(None) == "—"
    assert _cell("") == "—"


def test_long_values_are_truncated_with_an_ellipsis():
    out = _cell("x" * 300, 50)
    assert len(out) == 50
    assert out.endswith("…")


def test_status_label_humanises_snake_case():
    assert status_label("in_progress") == "In Progress"
    assert status_label(None) == "Unspecified"


# ---------------------------------------------------------------------------
# Tier 1 rendering
# ---------------------------------------------------------------------------


def test_soa_is_deterministic(ctx):
    """Same context, same bytes — the premise the fingerprint relies on."""
    assert render_soa(ctx) == render_soa(ctx)


def test_soa_names_the_organisation(ctx):
    assert "Acme Holdings" in render_soa(ctx)


def test_soa_records_the_catalog_version(ctx):
    """Without this a reader cannot tell which SCF release the document
    describes, which is the question an auditor asks first."""
    assert "2026.2" in render_soa(ctx)


def test_soa_lists_every_scoped_control(ctx):
    out = render_soa(ctx)
    for control in ctx.all_controls:
        assert control.scf_id in out


def test_soa_reports_coverage(ctx):
    assert ctx.coverage_percent() == 50.0
    assert "50.0%" in render_soa(ctx)


def test_soa_renders_valid_table_rows(ctx):
    """Every data row must have the same column count as its header."""
    rows = [l for l in render_soa(ctx).splitlines() if l.startswith("| AAA-")]
    assert rows
    assert all(row.count("|") == 7 for row in rows)


# ---------------------------------------------------------------------------
# Tier 2 prompt assembly
# ---------------------------------------------------------------------------


def test_prompt_is_deterministic(ctx):
    spec = get_generator("policy")
    bundle = ctx.domains[0]
    assert build_user_prompt(spec, ctx, bundle) == build_user_prompt(spec, ctx, bundle)


def test_prompt_carries_the_control_data(ctx):
    prompt = build_user_prompt(get_generator("policy"), ctx, ctx.domains[0])
    assert "AAA-01" in prompt
    assert "First Control" in prompt


def test_prompt_carries_assessment_objectives(ctx):
    ctx.domains[0].controls[0].assessment_objectives = [
        {"ao_id": "AAA-01-A1", "objective_text": "Verify the thing."}
    ]
    prompt = build_user_prompt(get_generator("policy"), ctx, ctx.domains[0])
    assert "Verify the thing." in prompt


def test_prompt_leaves_no_unfilled_placeholders(ctx):
    """An unfilled {placeholder} would reach the model verbatim."""
    import re
    prompt = build_user_prompt(get_generator("policy"), ctx, ctx.domains[0])
    assert not re.search(r"\{[a-z_]+\}", prompt)


@pytest.mark.parametrize("generator", ["policy", "procedure", "standard"])
def test_every_tier_2_prompt_fills_cleanly(ctx, generator):
    import re
    prompt = build_user_prompt(get_generator(generator), ctx, ctx.domains[0])
    assert not re.search(r"\{[a-z_]+\}", prompt)
    assert "Acme Holdings" in prompt


def test_document_version_starts_at_one_point_zero():
    assert compute_doc_version(None) == "1.0"
    assert compute_doc_version(0) == "1.0"


def test_document_version_increments_with_generation():
    assert compute_doc_version(1) == "1.1"
    assert compute_doc_version(7) == "1.7"


# ---------------------------------------------------------------------------
# Change history preservation
# ---------------------------------------------------------------------------


EXISTING = """## Document Control

| Field | Value |
|-------|-------|
| **Version** | 1.2 |

### Change History

| Version | Date | Author | Description of Changes |
|---------|------|--------|------------------------|
| 1.0 | 2026-01-01 | CISO function | Initial issue |
| 1.1 | 2026-04-01 | CISO function | Annual review |

## Purpose

Text.
"""


def test_change_history_rows_are_recovered():
    """A regenerated ISMS document whose revision history restarts at 1.0
    looks like a record while asserting something false."""
    history = extract_change_history(EXISTING)
    assert "| 1.0 |" in history
    assert "| 1.1 |" in history


def test_change_history_excludes_the_header_and_separator():
    history = extract_change_history(EXISTING)
    assert "Description of Changes" not in history
    assert "|---" not in history


def test_change_history_stops_at_the_next_section():
    assert "Purpose" not in extract_change_history(EXISTING)


def test_change_history_of_a_document_without_one_is_empty():
    assert extract_change_history("## Purpose\n\nText.") == ""
    assert extract_change_history("") == ""


def test_first_generation_prompt_asks_for_a_single_history_row(ctx):
    prompt = build_user_prompt(get_generator("policy"), ctx, ctx.domains[0])
    assert "Initial issue" in prompt


def test_regeneration_prompt_asks_to_carry_history_forward(ctx):
    prompt = build_user_prompt(
        get_generator("policy"), ctx, ctx.domains[0],
        generation_version=2, existing_content=EXISTING,
    )
    assert "VERBATIM" in prompt
    assert "| 1.1 |" in prompt


# ---------------------------------------------------------------------------
# Mock mode
# ---------------------------------------------------------------------------


def test_mock_mode_when_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DOC_GEN_AI_MOCK", raising=False)
    assert is_mock_mode() is True


def test_mock_mode_can_be_forced_with_a_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("DOC_GEN_AI_MOCK", "1")
    assert is_mock_mode() is True


def test_live_mode_with_a_key_and_no_flag(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.delenv("DOC_GEN_AI_MOCK", raising=False)
    assert is_mock_mode() is False


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


MARKED = """## Scope

<!-- CONFLICT: this section was edited and the generator also changed it. -->

Body text.
"""


def test_markdown_export_strips_merge_markers():
    """Markers are review scaffolding. An exported document is what an auditor
    reads."""
    assert "CONFLICT" not in export_markdown(MARKED)


def test_markdown_export_adds_a_title_when_absent():
    assert export_markdown("## Scope\n\nText.", title="Access Policy").startswith(
        "# Access Policy"
    )


def test_markdown_export_does_not_double_a_title():
    out = export_markdown("# Access Policy\n\nText.", title="Access Policy")
    assert out.count("# Access Policy") == 1


def test_html_export_renders_tables():
    html = markdown_to_html("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html


def test_html_export_escapes_the_title():
    html = markdown_to_html("Body", title="<script>x</script>")
    assert "<title>&lt;script&gt;" in html


def test_html_preview_can_retain_markers():
    assert "CONFLICT" in markdown_to_html(MARKED, include_markers=True)


@pytest.mark.parametrize("title,expected", [
    ("Statement of Applicability", "statement-of-applicability.md"),
    ("Access / Control Policy", "access-control-policy.md"),
    ('Bad"Quote', "bad-quote.md"),
    ("", "document.md"),
])
def test_filenames_cannot_carry_a_path_or_a_quote(title, expected):
    """A title reaches this from user-editable data; a slash or a quote in a
    Content-Disposition header is a header-injection vector."""
    out = safe_filename(title, "md")
    assert out == expected
    assert "/" not in out and '"' not in out


def test_filename_includes_the_domain_when_given():
    assert safe_filename("Access Policy", "pdf", "IAC") == "access-policy-iac.pdf"


# ---------------------------------------------------------------------------
# Marker stripping must survive marker drift
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", [
    "<!-- CONFLICT: this section was edited and the generator also changed it. -->",
    "<!-- NEW: added from newly scoped controls. -->",
    "<!-- PENDING RETIREMENT: these controls left scope. -->",
    "<!--CONFLICT: no leading space-->",
    "<!-- conflict: lowercased by an editor -->",
])
def test_reworded_markers_are_still_stripped(marker):
    """Exact-string matching alone is not enough.

    A human editing a conflicted section reflows the comment, and marker
    wording changes between releases. Either way the stale annotation would
    otherwise reach an exported PDF.
    """
    body = f"## Scope\n\n{marker}\n\nBody text."
    assert "-->" not in export_markdown(body)
    assert "Body text." in export_markdown(body)


def test_a_multiline_marker_is_stripped():
    body = "## Scope\n\n<!-- CONFLICT: reflowed\nacross two lines -->\n\nBody."
    assert "-->" not in export_markdown(body)


def test_an_unrelated_html_comment_is_preserved():
    """Only merge annotations are stripped. An author's own comment is theirs."""
    body = "## Scope\n\n<!-- author note: check this -->\n\nBody."
    assert "author note" in export_markdown(body)


# ---------------------------------------------------------------------------
# PDF masthead
#
# The masthead is the first thing an auditor sees, and every part of it is
# optional -- an organisation with no logo, a report with no domain. It has to
# degrade to something that still reads as a controlled document rather than
# to a broken image box or an empty badge.
# ---------------------------------------------------------------------------


def test_masthead_carries_every_element_when_all_are_present():
    html = build_masthead(
        title="Asset Management Policy",
        organisation="Ginga Ninja Holdings Ltd.",
        subtitle="Domain Policy · Version 2 · Draft",
        domain_id="AST",
        logo_data_uri="data:image/png;base64,AAAA",
    )
    assert "Asset Management Policy" in html
    assert "Ginga Ninja Holdings Ltd." in html
    assert "Domain Policy · Version 2 · Draft" in html
    assert ">AST<" in html
    assert 'src="data:image/png;base64,AAAA"' in html


def test_masthead_without_a_logo_emits_no_image_tag():
    html = build_masthead(title="Control Status Report", domain_id="GOV")
    assert "<img" not in html
    assert ">GOV<" in html


def test_masthead_without_a_domain_emits_no_badge():
    """Organisation-wide reports have no domain. An empty navy pill is worse
    than no pill."""
    html = build_masthead(title="Statement of Applicability", organisation="Acme")
    assert "doc-masthead-badge" not in html


def test_masthead_with_neither_logo_nor_domain_drops_the_right_column():
    html = build_masthead(title="Evidence Schedule")
    assert "doc-masthead-right" not in html
    assert "Evidence Schedule" in html


def test_masthead_escapes_the_title():
    html = build_masthead(title='Policy <script>alert("x")</script>')
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_masthead_escapes_the_organisation_name():
    html = build_masthead(title="Policy", organisation="A & B <Ltd>")
    assert "<Ltd>" not in html
    assert "&amp;" in html


def test_markdown_to_html_places_the_prefix_before_the_body():
    out = markdown_to_html("## Purpose\n\nText.", title="P", body_prefix="<div id=mh></div>")
    assert out.index("id=mh") < out.index("Purpose")


def test_html_export_has_no_masthead_by_default():
    """Only the PDF path passes one; the HTML export is the raw document."""
    assert "doc-masthead" not in markdown_to_html("# T\n\nBody.", title="T")


# ---------------------------------------------------------------------------
# CSS string escaping
#
# The running header and footer are injected into `content: "..."` literals. A
# double quote in an organisation name would close the string early and take
# the rest of the stylesheet with it.
# ---------------------------------------------------------------------------


def test_a_quote_in_the_value_cannot_close_the_css_string():
    assert '"' not in _css_string('Bob "The Builder" Ltd')


def test_a_newline_in_the_value_cannot_break_the_declaration():
    assert "\n" not in _css_string("Line one\nLine two")


def test_a_backslash_cannot_start_a_css_escape():
    assert "\\" not in _css_string("Acme \\ Co")


def test_an_ordinary_name_survives_unchanged():
    assert _css_string("Ginga Ninja Holdings Ltd.") == "Ginga Ninja Holdings Ltd."


# ── Untrusted document content ───────────────────────────────────────────────
#
# `merged_content` carries model output and text typed into the section editor,
# and it reaches three markup consumers: the in-app reader, the HTML export and
# WeasyPrint. Python-Markdown passes raw HTML through by design, so these assert
# the boundary that stops it. They are the regression net for a real finding,
# not hypotheticals -- both payloads below survived into the reader before it.

XSS_MD = (
    "## Purpose\n\n"
    '<img src=x onerror="alert(1)">\n\n'
    "<script>alert(2)</script>\n\n"
    "Ordinary text.\n"
)


def test_raw_html_cannot_reach_the_reader_fragment():
    out = markdown_to_reader_fragment(XSS_MD, [])
    assert "<script" not in out
    assert "<img" not in out
    # Defanged, not deleted -- the author still sees the text they typed.
    assert "&lt;script&gt;alert(2)&lt;/script&gt;" in out


def test_raw_html_cannot_reach_the_html_export():
    out = markdown_to_html(XSS_MD, title="P")
    assert "<script>" not in out
    assert "<img" not in out
    assert "&lt;script&gt;" in out


def test_an_escaped_entity_cannot_decode_back_into_a_tag():
    """`&` must be escaped before `<`, or `&lt;script&gt;` round-trips to a tag."""
    out = markdown_to_html("&lt;script&gt;alert(1)&lt;/script&gt;", title="P")
    assert "<script>" not in out
    assert "&amp;lt;" in out


def test_attr_list_syntax_cannot_inject_an_event_handler():
    """`## H {: onclick="..." }` rendered as a real onclick until attr_list went.

    The syntax now degrades to visible text, which is the honest outcome: the
    handler is inert and the author can see why their line looks odd.
    """
    src = '## Heading {: onclick="alert(1)" }'
    for out in (markdown_to_html(src, title="P"), markdown_to_reader_fragment(src, [])):
        assert "<h2 onclick" not in out
        assert '{: onclick="alert(1)" }' in out


def test_blockquotes_still_render_because_gt_is_left_alone():
    assert "<blockquote>" in markdown_to_html("> Quoted.", title="P")


def test_ordinary_markdown_survives_neutralisation():
    src = (
        "## Purpose\n\n"
        "**Bold** and *italic*.\n\n"
        "- one\n- two\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```python\nx = 1\n```\n"
    )
    out = markdown_to_html(src, title="P")
    for tag in ("<h2", "<strong>", "<em>", "<ul>", "<li>", "<table>", "<code"):
        assert tag in out, tag


def test_escape_closes_a_quoted_attribute_value():
    """`_escape` feeds data-section-id="..." -- a quote there breaks out."""
    assert _escape('a" onload="alert(1)') == "a&quot; onload=&quot;alert(1)"
    assert _escape("a' b") == "a&#39; b"


def test_a_quoted_section_id_cannot_break_out_of_its_attribute():
    class Row:
        section_id = 'x" onload="alert(1)'
        status = "unchanged"
        ordinal = 0
        control_ids: list = []
        # The heading fields are what pair this row to the heading in the
        # document, and without them the id never reaches an attribute at all
        # -- which would leave this test asserting nothing.
        heading_text = "Purpose"
        heading_level = 2

    out = markdown_to_reader_fragment("## Purpose\n\nText.\n", [Row()])
    assert 'onload="' not in out
    assert "&quot;" in out


# ---------------------------------------------------------------------------
# Counted-noun agreement in generated headings
# ---------------------------------------------------------------------------


def test_a_single_control_domain_says_control_not_controls(ctx):
    """"(1 controls)" in a Statement of Applicability is a credibility problem.

    Small, and the sort of thing a reader notices in a document they are being
    asked to trust. The tally still appears in the heading -- only the section
    *id* drops it (see ``normalise_section_id``).
    """
    ctx.domains[0].controls = ctx.domains[0].controls[:1]
    ctx.all_controls = ctx.domains[0].controls
    ctx.total_scoped_controls = 1
    output = render_soa(ctx)
    assert "(1 control)" in output
    assert "(1 controls)" not in output


def test_a_multi_control_domain_still_says_controls(ctx):
    assert "(2 controls)" in render_soa(ctx)


def test_the_domain_heading_id_ignores_the_tally(ctx):
    # The whole reason the count had to leave the slug: scoping one more
    # control must not rename the section and strand its edits.
    from services.doc_gen.section_parser import flatten_sections, parse_markdown_sections

    before = {s.section_id for s in flatten_sections(parse_markdown_sections(render_soa(ctx)))}
    ctx.domains[0].controls = ctx.domains[0].controls[:1]
    ctx.all_controls = ctx.domains[0].controls
    ctx.total_scoped_controls = 1
    after = {s.section_id for s in flatten_sections(parse_markdown_sections(render_soa(ctx)))}
    assert before == after
