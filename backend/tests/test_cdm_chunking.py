"""CDM v2 chunker tests (epic #709).

Determinism and offset round-tripping are load-bearing: if either breaks,
every stored citation silently moves (issue #709 risk R4). These tests assert
those invariants directly rather than sampling them.
"""
from __future__ import annotations

import pytest

from services.cdm_chunking import (
    FLUSH_MIN_CHARS,
    HARD_CAP_CHARS,
    DocumentChunk,
    chunk_document_text,
    normalise_whitespace,
)

SAMPLE = """# Information Security Policy

Intro paragraph, deliberately short.

## 4.2 Access Control

{long}

Second paragraph following a blank line.

Section 7 Supplier Management

The organisation maintains a register of suppliers.
""".format(long="Access to systems is granted on least privilege. " * 12)


def _assert_offsets_roundtrip(text: str, chunks: list[DocumentChunk]) -> None:
    for chunk in chunks:
        assert text[chunk.char_start:chunk.char_end] == chunk.body, (
            f"chunk {chunk.ordinal} offsets do not round-trip"
        )


# ─────────────────────────── core invariants ────────────────────────────


def test_offsets_roundtrip_against_source_text():
    """extracted_text[char_start:char_end] must equal body, always."""
    chunks = chunk_document_text(SAMPLE)
    assert chunks
    _assert_offsets_roundtrip(SAMPLE, chunks)


def test_chunking_is_deterministic_across_runs():
    """Identical input must yield identical boundaries, or offsets move."""
    first = chunk_document_text(SAMPLE)
    second = chunk_document_text(SAMPLE)
    assert [(c.char_start, c.char_end, c.heading, c.body) for c in first] == [
        (c.char_start, c.char_end, c.heading, c.body) for c in second
    ]


def test_ordinals_are_contiguous_from_zero():
    chunks = chunk_document_text(SAMPLE)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_empty_and_whitespace_only_input_yields_no_chunks():
    assert chunk_document_text("") == []
    assert chunk_document_text("   \n\n  \t \n") == []


# ─────────────────────────────── headings ───────────────────────────────


def test_markdown_heading_flushes_current_chunk():
    text = "Body before the heading.\n# New Section\nBody after.\n"
    chunks = chunk_document_text(text)
    assert len(chunks) == 2
    assert chunks[0].body == "Body before the heading."
    assert chunks[1].body == "Body after."


def test_heading_becomes_following_chunk_heading():
    text = "Body before.\n# Access Control\nBody after.\n"
    chunks = chunk_document_text(text)
    assert chunks[0].heading is None
    assert chunks[1].heading == "Access Control"


def test_heading_text_excluded_from_body():
    """Headings are locators, never content."""
    chunks = chunk_document_text(SAMPLE)
    for chunk in chunks:
        assert "# Information Security Policy" not in chunk.body
        assert "## 4.2 Access Control" not in chunk.body


@pytest.mark.parametrize(
    "line,expected",
    [
        ("# Top Level", "Top Level"),
        ("### Third Level", "Third Level"),
        ("4.2.1 Access Control Requirements", "4.2.1 Access Control Requirements"),
        ("Section 7: Supplier Management", "Supplier Management"),
        ("Appendix B - Definitions", "Definitions"),
    ],
)
def test_non_markdown_heading_shapes_are_detected(line, expected):
    """PyMuPDF output has no markdown, so numbered/Section forms must work.

    Without this the default extraction path would leave every heading NULL
    and the provenance locator would not exist where it matters most.
    """
    text = f"Preceding body text.\n{line}\nFollowing body text.\n"
    chunks = chunk_document_text(text)
    assert chunks[-1].heading == expected


def test_back_to_back_headings_keep_the_nearest_one():
    """A title line immediately followed by a subsection heading must not lose
    the subsection — the following body belongs to the *nearest* heading."""
    text = "# Supplier Management Policy\n\n## 4.2 Onboarding\n\nBody content here.\n"
    chunks = chunk_document_text(text)
    assert len(chunks) == 1
    assert chunks[0].heading == "4.2 Onboarding"
    assert chunks[0].body == "Body content here."


def test_three_consecutive_headings_attribute_to_the_last():
    text = "# A\n## B\n### C\nBody.\n"
    chunks = chunk_document_text(text)
    assert len(chunks) == 1
    assert chunks[0].heading == "C"


def test_hash_inside_fenced_code_is_not_a_heading():
    text = "Intro line.\n```\n# not a heading\n```\nOutro line.\n"
    chunks = chunk_document_text(text)
    assert all(c.heading is None for c in chunks)


# ──────────────────────────── flush thresholds ──────────────────────────


def test_blank_line_below_threshold_does_not_flush():
    short = "Short paragraph."
    text = f"{short}\n\n{short}\n"
    chunks = chunk_document_text(text)
    assert len(chunks) == 1


def test_blank_line_above_threshold_flushes():
    long_para = "This sentence pads the paragraph out. " * 15
    assert len(long_para) > FLUSH_MIN_CHARS
    text = f"{long_para}\n\nFollowing paragraph.\n"
    chunks = chunk_document_text(text)
    assert len(chunks) == 2
    assert chunks[1].body == "Following paragraph."


def test_whitespace_only_line_counts_as_blank():
    """PDF extraction emits '   ' lines; they must flush like empty ones."""
    long_para = "This sentence pads the paragraph out. " * 15
    text = f"{long_para}\n   \t \nFollowing paragraph.\n"
    chunks = chunk_document_text(text)
    assert len(chunks) == 2
    assert chunks[1].body == "Following paragraph."


def test_hard_cap_flushes_mid_paragraph():
    giant = "word " * 1000  # 5000 chars, no blank lines
    chunks = chunk_document_text(giant)
    assert len(chunks) >= 2
    assert all(len(c.body) <= HARD_CAP_CHARS for c in chunks)
    _assert_offsets_roundtrip(giant, chunks)


def test_hard_cap_cuts_at_word_boundary():
    """The cap must not split a token, or phrase verification degrades."""
    giant = "supplier " * 400
    chunks = chunk_document_text(giant)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert all(token == "supplier" for token in chunk.body.split())


def test_hard_cap_preserves_full_text_coverage():
    giant = "alpha beta gamma delta " * 300
    chunks = chunk_document_text(giant)
    recovered = " ".join(c.body_norm for c in chunks)
    assert recovered.split() == giant.split()


# ──────────────────────────────── body_norm ─────────────────────────────


def test_body_norm_collapses_whitespace_runs():
    text = "Control   is\n  maintained\tannually.\n"
    chunks = chunk_document_text(text)
    assert chunks[0].body_norm == "Control is maintained annually."


def test_normalise_whitespace_is_idempotent():
    once = normalise_whitespace("a   b\n\nc")
    assert normalise_whitespace(once) == once


# ────────────────────────────── encoding ────────────────────────────────


def test_non_ascii_offsets_roundtrip():
    """Smart quotes, currency signs and em-dashes are the norm in policy PDFs."""
    text = (
        "# Policy — Budget\n\n"
        "The “threshold” is £50,000 per annum — reviewed by Renée.\n"
    )
    chunks = chunk_document_text(text)
    _assert_offsets_roundtrip(text, chunks)
    assert "£50,000" in chunks[0].body
