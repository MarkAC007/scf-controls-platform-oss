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
from services.doc_gen.renderer import export_markdown, markdown_to_html, safe_filename
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
