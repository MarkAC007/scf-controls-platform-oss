"""CDM v2 Tier-2 phrase verification tests (epic #709).

The whole point of these tests is the failure mode that is *not* an exception:
a citation whose offsets land in the right neighbourhood with the wrong start.
Whitespace normalisation is length-changing, so every flexible match here is
checked by slicing the original text and comparing, never by trusting an index.
"""
from __future__ import annotations

import pytest

from services.cdm_verification import (
    MatchType,
    VerifiedMatch,
    build_normalisation_map,
    locate_phrase,
    locate_phrase_in_document,
    normalise_whitespace,
)


# ─────────────────────── the offset rule, enforced ──────────────────────


def test_verified_match_rejects_inverted_offsets():
    with pytest.raises(ValueError):
        VerifiedMatch(char_start=10, char_end=5, match_type=MatchType.EXACT, matched_text="x")


def test_verified_match_rejects_equal_offsets():
    with pytest.raises(ValueError):
        VerifiedMatch(char_start=5, char_end=5, match_type=MatchType.EXACT, matched_text="x")


def test_verified_match_rejects_negative_start():
    with pytest.raises(ValueError):
        VerifiedMatch(char_start=-1, char_end=4, match_type=MatchType.EXACT, matched_text="x")


def test_verified_match_rejects_null_offsets():
    """A structural Protocol would accept None here; construction must not."""
    with pytest.raises(ValueError):
        VerifiedMatch(
            char_start=None,  # type: ignore[arg-type]
            char_end=None,  # type: ignore[arg-type]
            match_type=MatchType.EXACT,
            matched_text="x",
        )


def test_verified_match_rejects_empty_matched_text():
    with pytest.raises(ValueError):
        VerifiedMatch(char_start=0, char_end=3, match_type=MatchType.EXACT, matched_text="")


def test_verified_match_accepts_a_sound_span():
    match = VerifiedMatch(
        char_start=0, char_end=3, match_type=MatchType.EXACT, matched_text="abc"
    )
    assert match.char_end > match.char_start


# ──────────────────────────── normalisation map ─────────────────────────


def test_normalisation_map_length_matches_normalised_text():
    text = "Control   is\n\n  maintained  annually."
    normalised, index_map = build_normalisation_map(text)
    assert len(index_map) == len(normalised)


def test_normalisation_map_indices_point_at_source_characters():
    text = "Control   is\n\n  maintained."
    normalised, index_map = build_normalisation_map(text)
    for position, char in enumerate(normalised):
        if char == " ":
            assert text[index_map[position]].isspace()
        else:
            assert text[index_map[position]] == char


def test_normalisation_matches_the_plain_normaliser():
    text = "  Control   is\n\n  maintained  \t"
    normalised, _ = build_normalisation_map(text)
    assert normalised == normalise_whitespace(text)


# ────────────────────────────── pass one ────────────────────────────────


def test_exact_match_is_labelled_exact():
    body = "The organisation maintains a supplier register annually."
    match = locate_phrase(body, "maintains a supplier register")
    assert match is not None
    assert match.match_type is MatchType.EXACT
    assert body[match.char_start:match.char_end] == "maintains a supplier register"


def test_no_match_returns_none():
    body = "The organisation maintains a supplier register."
    assert locate_phrase(body, "quantum entanglement policy") is None


def test_empty_inputs_return_none():
    assert locate_phrase("", "anything") is None
    assert locate_phrase("something", "") is None
    assert locate_phrase("something", "   ") is None


# ────────────────────────────── pass two ────────────────────────────────


def test_whitespace_variant_is_located_and_labelled():
    body = "The control   is\n\n  maintained annually by the owner."
    match = locate_phrase(body, "control is maintained annually")
    assert match is not None
    assert match.match_type is MatchType.WHITESPACE_FLEXIBLE


def test_whitespace_variant_offsets_resolve_to_correct_original_text():
    """The finding this test exists for: an index into normalised text is not
    an index into the original. Slicing the original must give the phrase back."""
    body = "Preamble text. The control   is\n\n  maintained annually. Trailing."
    match = locate_phrase(body, "control is maintained annually")
    assert match is not None
    sliced = body[match.char_start:match.char_end]
    assert normalise_whitespace(sliced) == "control is maintained annually"


def test_whitespace_variant_start_is_not_shifted_by_earlier_collapsing():
    """Collapsed runs *before* the match must not displace its start."""
    body = "A    B     C    the   quick   brown   fox jumps."
    match = locate_phrase(body, "quick brown fox")
    assert match is not None
    assert body[match.char_start:match.char_end].startswith("quick")
    assert normalise_whitespace(body[match.char_start:match.char_end]) == "quick brown fox"


def test_matched_text_field_equals_the_original_slice():
    body = "Policy:  access   is reviewed  quarterly."
    match = locate_phrase(body, "access is reviewed quarterly")
    assert match is not None
    assert match.matched_text == body[match.char_start:match.char_end]


@pytest.mark.parametrize(
    "phrase",
    [
        "control is maintained",
        "is maintained annually",
        "The control is maintained annually by the owner",
    ],
)
def test_multiple_whitespace_variants_all_resolve_correctly(phrase):
    body = "The control   is\n\n  maintained annually by the owner."
    match = locate_phrase(body, phrase)
    assert match is not None
    assert normalise_whitespace(body[match.char_start:match.char_end]) == normalise_whitespace(phrase)


# ───────────────────────── non-ASCII behaviour ──────────────────────────


def test_non_ascii_phrase_resolves_to_correct_slice():
    body = "The “threshold” is  £50,000 per annum — reviewed by Renée."
    match = locate_phrase(body, "threshold” is £50,000 per annum")
    assert match is not None
    assert normalise_whitespace(body[match.char_start:match.char_end]) == (
        "threshold” is £50,000 per annum"
    )


# ──────────────────────────── fuzzy pass ────────────────────────────────


def test_fuzzy_pass_is_off_by_default():
    body = "The organisation maintains a register of approved suppliers."
    assert locate_phrase(body, "organisation maintains a register of vendors") is None


def test_fuzzy_pass_when_enabled_returns_a_resolvable_span():
    body = "The organisation maintains a register of approved suppliers."
    match = locate_phrase(
        body, "organisation maintains a register of vendors", allow_fuzzy=True
    )
    assert match is not None
    assert match.match_type is MatchType.FUZZY
    assert body[match.char_start:match.char_end] == match.matched_text


def test_fuzzy_pass_refuses_a_weak_overlap():
    body = "Completely unrelated content about physical security badges."
    assert locate_phrase(
        body, "supplier offboarding termination procedure", allow_fuzzy=True
    ) is None


# ─────────────────── document-coordinate translation ────────────────────


def test_document_coordinates_are_chunk_start_plus_local_offset():
    document = "HEADER PADDING. The control is maintained annually. FOOTER."
    chunk_start = document.index("The control")
    body = document[chunk_start:chunk_start + len("The control is maintained annually.")]
    match = locate_phrase_in_document(document, chunk_start, body, "control is maintained")
    assert match is not None
    assert document[match.char_start:match.char_end] == "control is maintained"


def test_document_coordinates_reject_a_diverged_chunk():
    """If the stored chunk no longer agrees with the extracted text, we must
    write nothing rather than a citation pointing at the wrong place."""
    document = "COMPLETELY DIFFERENT TEXT THAT HAS SINCE BEEN RE-EXTRACTED."
    stale_body = "The control is maintained annually."
    match = locate_phrase_in_document(document, 0, stale_body, "control is maintained")
    assert match is None


def test_document_coordinates_survive_a_whitespace_flexible_match():
    document = "PADDING. The control   is  maintained annually. END."
    chunk_start = document.index("The control")
    body = document[chunk_start:document.index(" END.")]
    match = locate_phrase_in_document(document, chunk_start, body, "control is maintained")
    assert match is not None
    assert match.match_type is MatchType.WHITESPACE_FLEXIBLE
    assert normalise_whitespace(
        document[match.char_start:match.char_end]
    ) == "control is maintained"
