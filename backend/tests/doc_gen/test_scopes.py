"""Document profiles and the scopes they resolve to (templates/bundles.yaml).

The load-time checks matter most: a profile that drops a domain silently
removes that domain's controls from an organisation's policies, so the file
must refuse to load rather than generate an incomplete policy set.
"""
import copy

import pytest
import yaml

from services.doc_gen import scopes
from services.doc_gen.scopes import ScopeConfigError, _BUNDLES_FILE, _parse

RAW = yaml.safe_load(_BUNDLES_FILE.read_text(encoding="utf-8"))

#: The 34 SCF domains as of the 2026.x catalogue.
SCF_DOMAINS = {
    "GOV", "AAT", "AST", "BCD", "CAP", "CHG", "CLD", "CPL", "CFG", "MON", "CRY",
    "DCH", "EMB", "END", "HRS", "IAC", "IRO", "IAO", "MNT", "MDM", "NET", "PES",
    "PRI", "PRM", "QTS", "RSK", "SEA", "OPS", "SAT", "TDA", "TPM", "THR", "VPM", "WEB",
}


class TestShippedFile:
    def test_the_four_profiles_are_declared_with_granular_as_default(self):
        assert scopes.profile_names() == ["granular", "consolidated", "compact", "essentials"]
        assert scopes.default_profile() == "granular"

    @pytest.mark.parametrize("name,count", [("consolidated", 13), ("compact", 6), ("essentials", 1)])
    def test_every_bundled_profile_covers_every_scf_domain_once(self, name, count):
        profile = scopes.get_profile(name)
        assert len(profile.bundles) == count
        domains = [d for b in profile.bundles for d in b.domains]
        assert sorted(domains) == sorted(SCF_DOMAINS)

    def test_compact_groups_are_unions_of_consolidated_bundles(self):
        consolidated = {b.key: set(b.domains) for b in scopes.get_profile("consolidated").bundles}
        for group in scopes.get_profile("compact").bundles:
            covered = [k for k, ds in consolidated.items() if ds <= set(group.domains)]
            assert set().union(*(consolidated[k] for k in covered)) == set(group.domains)

    def test_the_essentials_manual_has_its_own_document_title(self):
        (manual,) = scopes.get_profile("essentials").bundles
        assert manual.document_title == "Information Security Policy Manual"

    def test_smaller_profiles_fold_standards_and_use_topic_procedures(self):
        for name in ("compact", "essentials"):
            p = scopes.get_profile(name)
            assert p.fold_standards
            assert p.mode_for("standard") == scopes.MODE_NONE
            assert p.mode_for("procedure") == scopes.MODE_TOPIC
        granular = scopes.get_profile("granular")
        assert not granular.fold_standards
        assert set(granular.generators.values()) == {scopes.MODE_DOMAIN}

    def test_an_unknown_profile_name_falls_back_to_the_default(self):
        assert scopes.get_profile("retired-profile").name == "granular"
        assert scopes.get_profile(None).name == "granular"

    def test_six_topics_each_select_something(self):
        topics = scopes.topics()
        assert [t.key for t in topics] == [
            "T-ACCESS", "T-INCIDENT", "T-BACKUP", "T-CHANGE", "T-VULN", "T-SUPPLIER",
        ]
        assert all(t.domains for t in topics)


class TestLoadTimeValidation:
    def test_a_domain_in_two_bundles_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["bundle_sets"]["consolidated"][1]["domains"].append("GOV")
        with pytest.raises(ScopeConfigError, match="more than once"):
            _parse(raw)

    def test_profiles_covering_different_domains_refuse_to_load(self):
        # Groups normally inherit their domains from bundles; one declared
        # with its own, incomplete list drops RSK, CPL, PRM and IAO.
        raw = copy.deepcopy(RAW)
        raw["bundle_sets"]["compact"][0] = {"key": "G-GRC", "title": "GRC", "domains": ["GOV"]}
        with pytest.raises(ScopeConfigError, match="cover different domains"):
            _parse(raw)

    def test_a_group_naming_an_undeclared_bundle_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["bundle_sets"]["compact"][0]["bundles"] = ["B-NOPE"]
        with pytest.raises(ScopeConfigError, match="not declared"):
            _parse(raw)

    def test_a_key_without_a_scope_prefix_refuses_to_load(self):
        # Unprefixed keys could collide with an SCF domain identifier in
        # generated_documents.domain_id.
        raw = copy.deepcopy(RAW)
        raw["bundle_sets"]["consolidated"][0]["key"] = "GRC"
        with pytest.raises(ScopeConfigError, match="must start with"):
            _parse(raw)

    def test_an_unknown_generator_mode_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["profiles"]["compact"]["generators"]["policy"] = "chapter"
        with pytest.raises(ScopeConfigError, match="invalid generator mode"):
            _parse(raw)


DOMAINS = [("GOV", "Governance"), ("IAC", "Identification & Authentication"),
           ("IRO", "Incident Response")]


class TestScopesFor:
    def test_granular_lists_one_scope_per_scoped_domain(self):
        got = scopes.scopes_for(scopes.get_profile("granular"), "policy", DOMAINS)
        assert [(s.key, s.kind) for s in got] == [
            ("GOV", "domain"), ("IAC", "domain"), ("IRO", "domain"),
        ]

    def test_bundles_without_a_scoped_domain_are_not_listed(self):
        got = scopes.scopes_for(scopes.get_profile("consolidated"), "policy", DOMAINS)
        assert [s.key for s in got] == ["B-GRC", "B-ACCESS", "B-RESILIENCE"]

    def test_a_domain_no_bundle_claims_becomes_its_own_bundle(self):
        got = scopes.scopes_for(
            scopes.get_profile("compact"), "policy", DOMAINS + [("ZZZ", "New Domain")],
        )
        orphan = got[-1]
        assert (orphan.key, orphan.title, orphan.domains) == ("D-ZZZ", "New Domain", ("ZZZ",))

    def test_topics_with_nothing_in_scope_are_dropped(self):
        got = scopes.scopes_for(
            scopes.get_profile("compact"), "procedure", DOMAINS,
            [("IAC-04.2", "IAC"), ("GOV-01", "GOV")],
        )
        assert [s.key for s in got] == ["T-ACCESS"]

    def test_a_folded_generator_has_no_scopes(self):
        assert scopes.scopes_for(scopes.get_profile("compact"), "standard", DOMAINS) == []

    def test_resolution_canonicalises_case(self):
        found = scopes.resolve_for_generation(
            scopes.get_profile("consolidated"), "policy", "b-access", DOMAINS,
        )
        assert found.key == "B-ACCESS"


class TestTopicMembership:
    def test_a_control_family_includes_its_enhancements(self):
        (access,) = [t for t in scopes.topics() if t.key == "T-ACCESS"]
        assert access.includes("IAC-04", "IAC")
        assert access.includes("IAC-04.2", "IAC")

    def test_a_family_prefix_does_not_match_a_different_control(self):
        (access,) = [t for t in scopes.topics() if t.key == "T-ACCESS"]
        assert not access.includes("IAC-040", "IAC")
        assert not access.includes("IAC-05", "IAC")

    def test_a_domain_topic_takes_the_whole_domain(self):
        (incident,) = [t for t in scopes.topics() if t.key == "T-INCIDENT"]
        assert incident.includes("IRO-16.3", "IRO")
        assert not incident.includes("BCD-01", "BCD")


class TestCurrency:
    def test_documents_from_another_profile_are_superseded(self):
        compact = scopes.get_profile("compact")
        assert scopes.is_current(compact, "policy", "G-GRC")
        assert not scopes.is_current(compact, "policy", "IAC")
        assert not scopes.is_current(compact, "policy", "B-GRC")
        assert not scopes.is_current(compact, "standard", "B-GRC")
        assert scopes.is_current(compact, "procedure", "T-INCIDENT")

    def test_granular_treats_bundles_as_superseded(self):
        granular = scopes.get_profile("granular")
        assert scopes.is_current(granular, "policy", "IAC")
        assert not scopes.is_current(granular, "policy", "G-GRC")

    def test_organisation_level_documents_are_always_current(self):
        assert scopes.is_current(scopes.get_profile("compact"), "soa", "")

    def test_a_stored_orphan_key_still_resolves(self):
        found = scopes.scope_for_stored_key("D-ZZZ")
        assert found.domains == ("ZZZ",)
        assert scopes.scope_for_stored_key("IAC") is None


class TestStalenessResolvesScopes:
    """A bundled or topic document's staleness is measured on its own scope."""

    def _inputs(self):
        from services.doc_gen.staleness import CurrentInputs

        by_domain = {
            "IAC": [{"scf_id": "IAC-04"}, {"scf_id": "IAC-04.2"}, {"scf_id": "IAC-15"}],
            "HRS": [{"scf_id": "HRS-12"}],
            "CRY": [{"scf_id": "CRY-01"}],
        }
        return CurrentInputs(
            catalog_version="2026.3",
            all_controls=[c for cs in by_domain.values() for c in cs],
            by_domain=by_domain,
        )

    def test_a_bundle_key_takes_every_control_in_its_domains(self):
        ids = [c["scf_id"] for c in self._inputs().controls_for("B-ACCESS")]
        assert ids == ["IAC-04", "IAC-04.2", "IAC-15"]

    def test_a_topic_key_takes_only_its_control_families(self):
        ids = [c["scf_id"] for c in self._inputs().controls_for("T-ACCESS")]
        assert sorted(ids) == ["HRS-12", "IAC-04", "IAC-04.2"]

    def test_a_domain_key_is_unchanged(self):
        assert [c["scf_id"] for c in self._inputs().controls_for("CRY")] == ["CRY-01"]
