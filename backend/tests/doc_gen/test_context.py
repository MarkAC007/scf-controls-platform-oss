"""Context assembly -- the three shapes that broke every generation.

``build_context`` sits between the ORM and every generator, so a wrong
assumption here fails the whole feature rather than one document:

* controls store a domain's DISPLAY NAME, everything else speaks its
  IDENTIFIER, so both the filter and the grouping have to translate;
* risk levels are derived from the organisation's bands, not stored columns;
* the risk owner is a relationship, not a name.

The session is faked rather than mocked at the model layer: real model
instances go in, so a column rename shows up here as a failure instead of
being papered over by a stub.
"""
import pytest

from catalog_models import SCFCatalogControl, SCFCatalogDomain
from models import (
    Organization,
    OrganizationRiskProfile,
    RiskAssessment,
    ScopedControl,
    User,
)
from services.doc_gen.context import _resolve_domain_name, build_context

ORG_ID = "11111111-1111-1111-1111-111111111111"
GOV_NAME = "Governance & Risk Management"


class _FakeQuery:
    """Chainable stand-in that hands back canned rows and records filters."""

    def __init__(self, rows, recorder):
        self._rows = rows
        self._recorder = recorder

    def join(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def filter(self, *criteria):
        self._recorder.extend(criteria)
        return self

    def all(self):
        return list(self._rows)

    def one_or_none(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    """Dispatches ``query()`` by entity, so each model gets its own rows."""

    def __init__(self, rows_by_entity):
        self._rows_by_entity = rows_by_entity
        self.criteria = []

    def query(self, *entities):
        key = tuple(e.__name__ for e in entities)
        rows = self._rows_by_entity.get(key, self._rows_by_entity.get(key[0], []))
        return _FakeQuery(rows, self.criteria)

    def filter_values(self):
        """Literal right-hand values of every equality filter seen so far."""
        out = []
        for clause in self.criteria:
            right = getattr(clause, "right", None)
            if right is not None and hasattr(right, "value"):
                out.append(right.value)
        return out


def _domain():
    return SCFCatalogDomain(
        identifier="GOV",
        name=GOV_NAME,
        principle="Govern",
        principle_intent="Organisations specify the development...",
        order=1,
        status="active",
    )


def _control_row():
    scoped = ScopedControl(
        organization_id=ORG_ID,
        scf_id="GOV-01",
        selected=True,
        implementation_status="implemented",
        maturity_level=3,
        owner="CISO",
    )
    catalog = SCFCatalogControl(
        scf_id="GOV-01",
        control_name="Cybersecurity & Data Protection Governance Program",
        control_description="Mechanisms exist to facilitate...",
        scf_domain=GOV_NAME,
        status="active",
    )
    return (scoped, catalog)


def _session(*, risks=(), profile=None):
    return _FakeSession({
        "Organization": [Organization(id=ORG_ID, name="ComplianceGenie")],
        "OrganizationCatalogState": [],
        "SCFCatalogDomain": [_domain()],
        ("ScopedControl", "SCFCatalogControl"): [_control_row()],
        "SCFCatalogAssessmentObjective": [],
        "SCFCatalogEvidence": [],
        "EvidenceTracking": [],
        "RiskAssessment": list(risks),
        "OrganizationRiskProfile": [profile] if profile else [],
        "System": [],
        "Vendor": [],
    })


def _build(session, **kwargs):
    return build_context(
        session,
        ORG_ID,
        include_evidence=False,
        include_systems=False,
        **kwargs,
    )


class TestDomainResolution:
    """A domain has two spellings; the filter must accept either."""

    def test_identifier_resolves_to_the_display_name(self):
        assert _resolve_domain_name(
            "GOV", {"GOV": GOV_NAME}, {GOV_NAME: "GOV"}
        ) == GOV_NAME

    def test_display_name_passes_through_case_insensitively(self):
        assert _resolve_domain_name(
            GOV_NAME.lower(), {"GOV": GOV_NAME}, {GOV_NAME: "GOV"}
        ) == GOV_NAME

    def test_unknown_domain_is_returned_unchanged(self):
        # Falling through unchanged matches nothing, which the pipeline
        # reports as "no controls in that domain" -- the safe failure. The
        # unsafe one would be dropping the filter and generating the estate.
        assert _resolve_domain_name("NOPE", {"GOV": GOV_NAME}, {GOV_NAME: "GOV"}) == "NOPE"

    def test_filter_by_identifier_queries_the_display_name(self):
        session = _session()
        _build(session, domain_filter="GOV", include_risks=False)
        # The bug: "GOV" went to the query verbatim, matched no control row,
        # and every domain-scoped generation failed as "no controls in scope".
        assert GOV_NAME in session.filter_values()
        assert "GOV" not in session.filter_values()


class TestDomainGrouping:
    def test_controls_group_under_the_identifier(self):
        ctx = _build(_session(), include_risks=False)
        # ctx.domain() is looked up by identifier by the pipeline, so grouping
        # on the display name made every domain bundle unreachable.
        assert ctx.domain("GOV") is not None
        assert ctx.domain("GOV").domain.name == GOV_NAME
        assert [c.domain_identifier for c in ctx.all_controls] == ["GOV"]


class TestRiskAssessments:
    def _risk(self, owner=None):
        return RiskAssessment(
            organization_id=ORG_ID,
            risk_code="R-GOV-1",
            likelihood=5,
            impact=5,
            residual_likelihood=2,
            residual_impact=2,
            treatment_status="treating",
            owner=owner,
        )

    def test_levels_are_derived_from_the_organisation_bands(self):
        profile = OrganizationRiskProfile(
            organization_id=ORG_ID, low_max=4, medium_max=9, high_max=16
        )
        ctx = _build(_session(risks=[self._risk()], profile=profile))
        risk = ctx.risk_assessments[0]
        assert risk["inherent_risk_score"] == 25
        assert risk["inherent_risk_level"] == "critical"
        assert risk["residual_risk_level"] == "low"

    def test_levels_fall_back_to_defaults_without_a_profile(self):
        ctx = _build(_session(risks=[self._risk()]))
        assert ctx.risk_assessments[0]["inherent_risk_level"] == "critical"

    def test_custom_bands_move_the_boundary(self):
        profile = OrganizationRiskProfile(
            organization_id=ORG_ID, low_max=4, medium_max=25, high_max=30
        )
        ctx = _build(_session(risks=[self._risk()], profile=profile))
        assert ctx.risk_assessments[0]["inherent_risk_level"] == "medium"

    def test_risk_level_is_not_a_stored_attribute(self):
        # The original code read ra.inherent_risk_level; guard the rename.
        with pytest.raises(AttributeError):
            self._risk().inherent_risk_level

    def test_owner_renders_as_a_name_not_a_relationship(self):
        owner = User(
            email="ciso@example.com",
            display_name="Casey Iso",
            google_sub="sub-1",
        )
        ctx = _build(_session(risks=[self._risk(owner=owner)]))
        assert ctx.risk_assessments[0]["owner"] == "Casey Iso"

    def test_owner_falls_back_to_email(self):
        owner = User(email="ciso@example.com", google_sub="sub-1")
        ctx = _build(_session(risks=[self._risk(owner=owner)]))
        assert ctx.risk_assessments[0]["owner"] == "ciso@example.com"

    def test_ownerless_risk_is_none(self):
        ctx = _build(_session(risks=[self._risk()]))
        assert ctx.risk_assessments[0]["owner"] is None
