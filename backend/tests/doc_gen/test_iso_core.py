"""The ISO/IEC 27001 core pack (services/doc_gen/iso_core.py) and the Tier 3
Information Security Policy prompt.

The property that matters most is the one an auditor tests: a fact the
organisation has not recorded appears as a visible gap, never as plausible
text. Most tests here check that a register's absence produces the marker and
its presence produces the recorded values, unaltered.
"""
import copy

import pytest

from services.doc_gen import iso_core, tier3
from services.doc_gen.context import Domain, DomainWithControls, EnrichedControl, OrganisationContext
from services.doc_gen.registry import all_generators, get_generator
from services.doc_gen.section_parser import parse_markdown_sections


def _control(scf_id, domain, status="implemented", owner=None):
    return EnrichedControl(
        scf_id=scf_id, control_name=f"Control {scf_id}", control_description="",
        domain_identifier=domain, implementation_status=status, owner=owner,
    )


def _ctx(**over):
    iro = [_control("IRO-01", "IRO", owner="Sam - Incident Manager"), _control("IRO-02", "IRO")]
    gov = [_control("GOV-01", "GOV", status="not_started", owner="Jo - CISO")]
    base = dict(
        organization_id="org", name="Acme Ltd", generated_at="2026-10-28T12:00:00+00:00",
        domains=[
            DomainWithControls(domain=Domain("GOV", "Governance", principle="Govern."), controls=gov),
            DomainWithControls(domain=Domain("IRO", "Incident Response", principle="Respond."), controls=iro),
        ],
        all_controls=gov + iro, total_scoped_controls=3, total_domains=2,
    )
    base.update(over)
    return OrganisationContext(**base)


FULL_PROFILE = {
    "scope": {
        "organisation_description": "Acme builds payroll software.",
        "boundaries": "All UK operations",
        "locations": ["London HQ"], "services": ["Payroll SaaS"],
        "interfaces": "Payments via a third-party processor.",
        "exclusions": [{"id": "x1", "item": "Physical data centre", "justification": "Cloud hosted"}],
    },
    "context_issues": [
        {"id": "i1", "kind": "external", "category": "environmental", "issue": "Flood risk at HQ",
         "effect": "Office loss", "response": "Remote working", "climate_related": True},
    ],
    "interested_parties": [
        {"id": "p1", "party": "Information Commissioner's Office", "kind": "external",
         "requirements": "Breach reporting within 72 hours", "addressed_through_isms": True,
         "how_addressed": "Incident procedure"},
    ],
    "objectives": [
        {"id": "o1", "objective": "Patch critical vulnerabilities within 14 days", "measure": "% in SLA",
         "target": "95%", "owner": "IT", "resources": "", "due_date": "2027-03-31",
         "evaluation": "", "status": "in_progress"},
    ],
    "communication_plan": [
        {"id": "c1", "what": "Personal data breaches", "when": "Within 72 hours",
         "audience": "ICO", "communicator": "DPO function", "method": "Online form"},
    ],
    "audit_programme": [
        {"id": "a1", "area": "Clause 4 and Incident Response", "criteria": "", "planned_period": "2027 Q1",
         "auditor": "External", "status": "planned", "findings": ""},
    ],
}


class TestRegistry:
    def test_every_core_pack_generator_is_tier_1_and_not_derivative(self):
        core = [g for g in all_generators() if (g.renderer or "").startswith("iso_core.")]
        assert len(core) == 17
        assert all(g.tier == 1 and not g.is_derivative and not g.domain_scoped for g in core)
        for g in core:
            g.resolve_renderer()

    def test_the_information_security_policy_is_gated_as_derivative(self):
        isp = get_generator("information-security-policy")
        assert isp.tier == 3 and isp.is_derivative and isp.requires.isms


@pytest.mark.parametrize("gen", [g.name for g in all_generators() if (g.renderer or "").startswith("iso_core.")])
def test_every_renderer_handles_an_organisation_with_nothing_recorded(gen):
    content = get_generator(gen).resolve_renderer()(_ctx())
    assert content.startswith("# ")
    assert parse_markdown_sections(content)


class TestMissingFactsAreMarked:
    def test_an_empty_scope_is_marked_not_invented(self):
        out = iso_core.render_isms_scope(_ctx())
        assert "*[To be completed: describe what the organisation does]*" in out
        assert "*[To be completed: list the locations in scope]*" in out
        assert "No exclusions have been recorded." in out

    def test_a_recorded_scope_is_rendered_verbatim(self):
        out = iso_core.render_isms_scope(_ctx(isms_profile=FULL_PROFILE))
        assert "Acme builds payroll software." in out
        assert "- London HQ" in out
        assert "| Physical data centre | Cloud hosted |" in out
        assert "To be completed: describe what" not in out

    def test_climate_change_is_asked_until_answered(self):
        assert "climate change is a relevant issue" in iso_core.render_context_of_organisation(_ctx())
        out = iso_core.render_context_of_organisation(_ctx(isms_profile=FULL_PROFILE))
        assert "has determined that climate change is a relevant issue" in out
        assert "Information Commissioner's Office" in out

    def test_objective_planning_marks_each_missing_answer(self):
        out = iso_core.render_security_objectives(_ctx(isms_profile=FULL_PROFILE))
        assert "- **Resources:** *[To be completed]*" in out
        assert "- **How results are evaluated:** *[To be completed]*" in out
        assert "| Controls implemented | 2 of 3 (66.7%) |" in out


class TestRiskMethodology:
    def test_the_matrix_uses_the_organisations_thresholds(self):
        ctx = _ctx(risk_profile={"low_max": 2, "medium_max": 6, "high_max": 12,
                                 "acceptable_risk_level": "low", "auto_escalate_above": "medium"})
        out = iso_core.render_risk_methodology(ctx)
        assert "| Low | 1–2 |" in out and "| Critical | 13–25 |" in out
        assert "| **5** | 5 Medium | 10 High | 15 Critical | 20 Critical | 25 Critical |" in out
        assert "at or below **Low**" in out

    def test_without_a_risk_profile_the_defaults_apply_and_acceptance_is_marked(self):
        out = iso_core.render_risk_methodology(_ctx())
        assert "| Low | 1–4 |" in out
        assert "configure the acceptable risk level" in out


class TestRegisters:
    def test_the_nonconformity_register_counts_open_items_without_a_root_cause(self):
        ctx = _ctx(deficiencies=[
            {"title": "MFA gap", "status": "open", "root_cause": ""},
            {"title": "Old finding", "status": "completed", "root_cause": "Process"},
        ])
        out = iso_core.render_nonconformity_register(ctx)
        assert "| Open | 1 |" in out and "| Open without a root cause | 1 |" in out
        assert "| Closed | 1 |" in out

    def test_the_legal_register_names_the_basis(self):
        ctx = _ctx(obligations=[{"name": "UK GDPR", "basis": "mcr", "obligation_type": "statutory",
                                 "obligation_source": "Data Protection Act 2018"}],
                   isms_profile=FULL_PROFILE)
        out = iso_core.render_legal_register(ctx)
        assert "| UK GDPR | Minimum compliance requirement | Statutory | Data Protection Act 2018 |" in out
        assert "Information Commissioner's Office" in out

    def test_the_document_register_leaves_itself_out(self):
        ctx = _ctx(documents=[
            {"title": "Document and Record Control Procedure", "generator": "document-control",
             "document_type": "isms", "lifecycle_status": "draft", "generation_version": 1},
            {"title": "Statement of Applicability", "generator": "soa", "document_type": "soa",
             "lifecycle_status": "approved", "generation_version": 3},
        ])
        out = iso_core.render_document_control(ctx)
        register = out.split("## 6. Document Register")[1]
        assert "Statement of Applicability" in register
        assert "Document and Record Control Procedure" not in register


class TestPlans:
    def test_the_irp_cites_only_scoped_incident_controls(self):
        out = iso_core.render_irp(_ctx(isms_profile=FULL_PROFILE))
        assert "[IRO-01], [IRO-02]" in out
        assert "IRO-14" not in out
        assert "| Personal data breaches |" in out

    def test_the_irp_without_incident_controls_says_so(self):
        ctx = _ctx(domains=[], all_controls=[])
        assert "none are in scope" in iso_core.render_irp(ctx)

    def test_the_bcp_leaves_recovery_objectives_to_the_organisation(self):
        ctx = _ctx(systems=[{"name": "Payroll DB", "system_type": "database", "status": "active"}])
        assert "| Payroll DB | Database | *[To be completed]* | *[To be completed]* |" in iso_core.render_bcp(ctx)


class TestFunctions:
    @pytest.mark.parametrize("owner,expected", [
        ("Jo - CISO", "Chief Information Security Officer (CISO) Function"),
        ("David Kim - DevOps Lead", "Operations Function"),
        ("Rachel Singh - Data Privacy Officer", "Data Protection Officer (DPO) Function"),
        ("Head of HR", "Human Resources Function"),
        ("Christopher Jones", "Information Security Function"),
    ])
    def test_inference(self, owner, expected):
        assert iso_core.infer_function(owner) == expected

    def test_unowned_controls_are_called_out(self):
        assert "**1 control has no owner**" in iso_core.render_roles_responsibilities(_ctx())


class TestManual:
    def test_the_manual_appends_the_registers(self):
        out = iso_core.render_isms_manual(_ctx(isms_profile=FULL_PROFILE))
        for appendix in ("Appendix A. Issues", "Appendix B. Interested Parties", "Appendix C. Objectives",
                         "Appendix D. Communication", "Appendix E. Audit Programme"):
            assert f"## {appendix}" in out
        assert "Flood risk at HQ" in out


class TestInputsDigest:
    def test_editing_a_register_changes_the_digest(self):
        before = iso_core.inputs_digest(_ctx(isms_profile=FULL_PROFILE))
        changed = copy.deepcopy(FULL_PROFILE)
        changed["objectives"][0]["target"] = "99%"
        assert iso_core.inputs_digest(_ctx(isms_profile=changed)) != before

    def test_a_document_register_does_not_hash_its_own_version(self):
        doc = {"title": "Doc control", "generator": "document-control", "lifecycle_status": "draft",
               "generation_version": 1}
        a = iso_core.inputs_digest(_ctx(documents=[doc]), exclude_generator="document-control")
        b = iso_core.inputs_digest(_ctx(documents=[{**doc, "generation_version": 2}]),
                                   exclude_generator="document-control")
        assert a == b


class TestInformationSecurityPolicyPrompt:
    def test_missing_scope_facts_reach_the_model_as_gaps(self):
        prompt = tier3.build_prompt(get_generator("information-security-policy"), _ctx(), [])
        assert "- Boundaries: [To be completed]" in prompt
        assert "- None recorded" in prompt
        assert "Never invent a fact about the organisation" in prompt

    def test_recorded_facts_and_the_policy_set_reach_the_model(self):
        prompt = tier3.build_prompt(
            get_generator("information-security-policy"), _ctx(isms_profile=FULL_PROFILE),
            ["Access Control Policy", "Incident Response Procedure"],
        )
        assert "- Boundaries: All UK operations" in prompt
        assert "Patch critical vulnerabilities within 14 days" in prompt
        assert "- Access Control Policy" in prompt
        assert "ISP-2026" in prompt
