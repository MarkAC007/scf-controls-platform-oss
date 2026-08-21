"""The lifecycle-options contract between the service and the API.

``available_transitions()`` already returns UI-shaped dicts. The API used to
iterate them as if they were bare status strings and re-label each one, which
raised ``unhashable type: 'dict'`` -- so *every* document detail fetch 500'd the
moment the document had a legal next state. These tests pin the shape at the
seam so the two ends cannot drift apart again.
"""
import pytest

from api.documents import DocumentDetail
from services.doc_gen.lifecycle import available_transitions, transition_label


def _detail(**overrides):
    """A minimally-valid DocumentDetail; only the transitions field matters."""
    return DocumentDetail(**{
        "id": "d1",
        "generator_name": "information-security-policy",
        "document_type": "policy",
        "domain_id": "GOV",
        "title": "Information Security Policy",
        "lifecycle_status": "draft",
        "tier": 2,
        "is_derivative": True,
        "generation_version": 1,
        "merged_content": "## Purpose\n",
        **overrides,
    })


class TestOptionShape:
    def test_options_are_flat_string_dicts(self):
        # DocumentDetail declares List[Dict[str, str]]; the webclient's
        # TransitionOption reads .to_status and .label off each entry.
        for option in available_transitions("in_review", "admin"):
            assert set(option) >= {"to_status", "label"}
            assert all(isinstance(v, str) for v in option.values())

    def test_the_service_output_validates_as_the_api_field(self):
        detail = _detail(
            available_transitions=available_transitions("draft", "admin"),
        )
        assert [o["to_status"] for o in detail.available_transitions] == ["in_review"]
        assert detail.available_transitions[0]["label"] == "Submit for Review"

    def test_re_wrapping_the_options_is_the_original_bug(self):
        options = available_transitions("draft", "admin")
        with pytest.raises(TypeError, match="unhashable"):
            [transition_label("draft", option) for option in options]

    def test_no_transitions_validates_too(self):
        detail = _detail(
            available_transitions=available_transitions("draft", "viewer"),
        )
        assert detail.available_transitions == []
