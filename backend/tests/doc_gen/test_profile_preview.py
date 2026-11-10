"""What switching document structure does to the documents already stored.

The settings card shows this before the switch is confirmed. It is computed
from the same scope rules the library uses to flag a document superseded, so
the preview and the result cannot disagree.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import catalog_models  # noqa: E402,F401 -- registers the mappers models.py relates to
import models  # noqa: E402,F401
from api import documents as documents_api  # noqa: E402
from services.doc_gen import scopes  # noqa: E402

DOMAINS = [("IRO", "Incident Response"), ("MON", "Continuous Monitoring"),
           ("BCD", "Business Continuity & Disaster Recovery"), ("TPM", "Third-Party Management"),
           ("IAC", "Identification & Authentication")]
PAIRS = [("IRO-01", "IRO"), ("MON-01", "MON"), ("BCD-01", "BCD"), ("BCD-04", "BCD"),
         ("TPM-01", "TPM"), ("IAC-04", "IAC"), ("IAC-02", "IAC")]


def _doc(key, generator="procedure", status="draft", title=None):
    return SimpleNamespace(id=f"id-{key}", generator_name=generator, domain_id=key,
                           title=title or f"{key} document", lifecycle_status=status)


def _preview(current, target, documents):
    return documents_api.build_profile_preview(
        scopes.get_profile(current), scopes.get_profile(target), documents, DOMAINS, PAIRS)


class TestProfilePreview:
    def test_compact_to_essentials(self):
        out = _preview("compact", "essentials", [
            _doc("T-SUPPLIER", title="Supplier Procedure"),
            _doc("T-DETECT-RESPOND", title="Log Review Procedure", status="approved"),
            _doc("T-ACCESS-MGMT", title="Access Control Procedure"),
            _doc("T-INCIDENT", title="Old Incident Procedure"),
        ])
        assert (out.current.key, out.target.key, out.target.title) == (
            "compact", "essentials", scopes.get_profile("essentials").display_name)

        superseded = {e.key: [r.key for r in e.replaced_by] for e in out.procedures.superseded}
        assert superseded == {"T-SUPPLIER": ["T-ASSETS-SUPPLIERS"],
                              "T-DETECT-RESPOND": ["T-INCIDENT-RESILIENCE"]}
        title = [e.replaced_by[0].title for e in out.procedures.superseded
                 if e.key == "T-DETECT-RESPOND"][0]
        assert title == "Incident Response & Recovery"

        assert [(e.key, e.restored) for e in out.procedures.kept] == [("T-ACCESS-MGMT", False)]

        created = {e.key: [s.key for s in e.seeded_from] for e in out.procedures.created}
        # Only what is in scope is created; each names what its draft starts from.
        assert created == {"T-ASSETS-SUPPLIERS": ["T-SUPPLIER"],
                           "T-INCIDENT-RESILIENCE": ["T-DETECT-RESPOND", "T-INCIDENT"]}

        assert out.counts["procedure"].superseded == 2
        assert out.counts["procedure"].signed_off == 1
        assert (out.superseded_total, out.signed_off_total) == (2, 1)

    def test_a_document_already_superseded_is_not_counted_again(self):
        out = _preview("compact", "essentials", [_doc("T-INCIDENT")])
        assert out.procedures.superseded == [] and out.superseded_total == 0

    def test_switching_back_restores_what_was_superseded(self):
        out = _preview("essentials", "compact", [
            _doc("T-SUPPLIER"), _doc("T-INCIDENT-RESILIENCE"), _doc("T-ACCESS-MGMT")])
        assert [(e.key, e.restored) for e in out.procedures.kept] == [
            ("T-ACCESS-MGMT", False), ("T-SUPPLIER", True)]
        assert {e.key: [r.key for r in e.replaced_by] for e in out.procedures.superseded} == {
            "T-INCIDENT-RESILIENCE": ["T-DETECT-RESPOND", "T-RESILIENCE"]}

    def test_policies_and_standards_are_counted_with_their_sign_off(self):
        out = _preview("compact", "essentials", [
            _doc("G-GRC", "policy", "published"), _doc("G-GRC", "standard", "draft"),
            _doc("IAC", "policy", "approved"),
        ])
        compact, essentials = scopes.get_profile("compact"), scopes.get_profile("essentials")
        expected = int(scopes.is_current(compact, "policy", "G-GRC")
                       and not scopes.is_current(essentials, "policy", "G-GRC"))
        assert out.counts["policy"].superseded == expected
        assert out.counts["policy"].signed_off == expected
        assert out.procedures.superseded == [] and out.procedures.kept == []

    def test_the_same_structure_changes_nothing(self):
        out = _preview("compact", "compact", [_doc("T-SUPPLIER"), _doc("T-INCIDENT")])
        assert out.superseded_total == 0 and out.procedures.superseded == []
        assert [e.key for e in out.procedures.kept] == ["T-SUPPLIER"]


class TestSupersededSummary:
    def _summary(self, key, profile):
        document = SimpleNamespace(
            id="x", generator_name="procedure", document_type="procedure", domain_id=key,
            title="T", lifecycle_status="draft", tier=2, is_derivative=True,
            generation_version=1, catalog_version=None, input_components={}, updated_at=None)
        return documents_api._summary(document, {}, None, scopes.get_profile(profile))

    def test_a_superseded_procedure_names_the_shape_and_what_covers_it_now(self):
        out = self._summary("T-INCIDENT", "compact")
        assert out.superseded
        assert out.superseded_under == scopes.get_profile("compact").display_name
        assert out.superseded_by == [
            {"key": "T-DETECT-RESPOND", "title": "Log Review & Incident Response"}]

    def test_a_current_procedure_names_neither(self):
        out = self._summary("T-SUPPLIER", "compact")
        assert not out.superseded and out.superseded_under is None and out.superseded_by == []
