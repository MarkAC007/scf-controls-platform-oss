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


_JML = ("HRS-08", "HRS-11", "HRS-12", "IAC-04", "IAC-08", "IAC-09", "IAC-10", "IAC-12", "IAC-22")
_BACKUP = ("BCD-04", "BCD-18", "BCD-23", "BCD-24", "BCD-26", "BCD-30", "BCD-31", "BCD-32", "BCD-33")

#: (key, domains, control families, excluded topics) of each leaf topic.
LEAF_TOPICS = [
    ("T-ACCESS", ("HRS", "IAC"), _JML, ()),
    ("T-INCIDENT", ("IRO",), (), ()),
    ("T-BACKUP", ("BCD",), _BACKUP, ()),
    ("T-CHANGE", ("CHG",), (), ()),
    ("T-VULN", ("VPM", "THR"), (), ()),
    ("T-SUPPLIER", ("TPM",), (), ()),
    ("T-GOVERNANCE", ("GOV", "PRM"), (), ()),
    ("T-ASSURANCE", ("CPL", "IAO"), (), ()),
    ("T-RISK", ("RSK",), (), ()),
    ("T-ASSET", ("AST", "MNT"), (), ()),
    ("T-DATA", ("DCH",), (), ()),
    ("T-CRYPTO", ("CRY", "QTS"), (), ()),
    ("T-PRIVACY", ("PRI",), (), ()),
    ("T-PEOPLE", ("HRS", "SAT"), (), ("T-ACCESS",)),
    ("T-PHYSICAL", ("PES",), (), ()),
    ("T-IDENTITY", ("IAC",), (), ("T-ACCESS",)),
    ("T-HARDENING", ("OPS", "CFG", "CAP", "END", "MDM", "EMB"), (), ()),
    ("T-NETWORK", ("NET", "CLD", "WEB"), (), ()),
    ("T-MONITORING", ("MON",), (), ()),
    ("T-CONTINUITY", ("BCD",), (), ("T-BACKUP",)),
    ("T-SDLC", ("TDA", "SEA", "AAT"), (), ()),
]

COMPACT_TOPICS = [
    "T-GOV-ASSURANCE", "T-RISK", "T-ACCESS-MGMT", "T-PEOPLE-WORKPLACE",
    "T-DATA-PROTECTION", "T-IT-OPS", "T-NETWORK", "T-ASSET", "T-SUPPLIER",
    "T-CHANGE-DEV", "T-DETECT-RESPOND", "T-RESILIENCE",
]

ESSENTIALS_TOPICS = [
    "T-GOVERNANCE-RISK", "T-ACCESS-MGMT", "T-PEOPLE-WORKPLACE", "T-DATA-PROTECTION",
    "T-DEVICE-NETWORK", "T-ASSETS-SUPPLIERS", "T-INCIDENT-RESILIENCE", "T-CHANGE-DEV",
]


def _topic(raw, key):
    (entry,) = [t for t in raw["topics"] if t["key"] == key]
    return entry


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

    def test_the_leaf_topics_are_pinned(self):
        # A golden snapshot: keys are stored in generated_documents.domain_id,
        # so a changed key or membership supersedes or restales every tenant's
        # procedures. Changing this list is a decision, not a refactor.
        got = [(t.key, t.domains, t.controls, tuple(e.key for e in t.excludes))
               for t in scopes.topics()]
        assert got == LEAF_TOPICS

    def test_the_first_six_topics_predate_the_tiers(self):
        # Unchanged since the six-topic list, except that T-VULN gained THR.
        assert [row[0] for row in LEAF_TOPICS[:6]] == [
            "T-ACCESS", "T-INCIDENT", "T-BACKUP", "T-CHANGE", "T-VULN", "T-SUPPLIER",
        ]
        assert LEAF_TOPICS[4][1] == ("VPM", "THR")

    @pytest.mark.parametrize("name,keys", [
        ("consolidated", [row[0] for row in LEAF_TOPICS]),
        ("compact", COMPACT_TOPICS),
        ("essentials", ESSENTIALS_TOPICS),
    ])
    def test_each_bundled_profile_has_its_own_procedure_set(self, name, keys):
        assert [t.key for t in scopes.get_profile(name).topics] == keys

    def test_procedure_counts_fall_with_the_profile(self):
        counts = [len(scopes.get_profile(n).topics)
                  for n in ("consolidated", "compact", "essentials")]
        assert counts == [21, 12, 8]
        assert scopes.get_profile("granular").topics == ()

    @pytest.mark.parametrize("name", ["consolidated", "compact", "essentials"])
    def test_every_set_holds_every_leaf_topic_exactly_once(self, name):
        leaves = [leaf.key for t in scopes.get_profile(name).topics for leaf in t.leaves()]
        assert sorted(leaves) == sorted(row[0] for row in LEAF_TOPICS)

    def test_every_scf_domain_is_taken_whole_by_exactly_one_topic(self):
        whole = [d for t in scopes.topics() if not t.controls for d in t.domains]
        assert sorted(whole) == sorted(SCF_DOMAINS)

    def test_a_group_is_a_union_of_the_set_above_it(self):
        compact = {t.key for t in scopes.get_profile("compact").topics}
        for entry in scopes.get_profile("essentials").topics:
            # An entry is either a compact procedure carried over as it is,
            # or a new group whose members are all compact procedures.
            assert entry.key in compact or all(m.key in compact for m in entry.members)
        by_key = {t.key: t for t in scopes.get_profile("essentials").topics}
        assert [m.key for m in by_key["T-INCIDENT-RESILIENCE"].members] == [
            "T-DETECT-RESPOND", "T-RESILIENCE",
        ]
        assert [leaf.key for leaf in by_key["T-INCIDENT-RESILIENCE"].leaves()] == [
            "T-MONITORING", "T-INCIDENT", "T-BACKUP", "T-CONTINUITY",
        ]


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


    # --- procedures: leaf topics -------------------------------------------

    def test_excluding_a_topic_declared_later_refuses_to_load(self):
        # T-ACCESS is first in the file; make it the one that looks ahead.
        raw = copy.deepcopy(RAW)
        access = _topic(raw, "T-ACCESS")
        del access["controls"]
        access["domains"] = ["ZZZ"]
        access["exclude_topics"] = ["T-BACKUP"]
        with pytest.raises(ScopeConfigError, match="not a topic declared before it"):
            _parse(raw)

    def test_excluding_a_whole_domain_topic_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        _topic(raw, "T-PEOPLE")["exclude_topics"] = ["T-INCIDENT"]
        with pytest.raises(ScopeConfigError, match="must be a topic that lists control families"):
            _parse(raw)

    def test_a_family_topic_cannot_exclude_anything(self):
        raw = copy.deepcopy(RAW)
        _topic(raw, "T-BACKUP")["exclude_topics"] = ["T-ACCESS"]
        with pytest.raises(ScopeConfigError, match="cannot also exclude"):
            _parse(raw)

    def test_one_family_being_a_prefix_of_another_refuses_to_load(self):
        # IAC-04.1 is an enhancement of IAC-04, which T-ACCESS already takes:
        # the control would be in two procedures.
        raw = copy.deepcopy(RAW)
        _topic(raw, "T-BACKUP")["controls"].append("IAC-04.1")
        with pytest.raises(ScopeConfigError, match="overlap"):
            _parse(raw)

    def test_a_family_topic_its_home_domain_does_not_exclude_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        del _topic(raw, "T-IDENTITY")["exclude_topics"]
        with pytest.raises(ScopeConfigError, match="must list T-ACCESS in exclude_topics"):
            _parse(raw)

    def test_a_domain_taken_whole_twice_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        _topic(raw, "T-RISK")["domains"].append("GOV")
        with pytest.raises(ScopeConfigError, match="taken whole by both"):
            _parse(raw)

    def test_a_bundled_domain_no_topic_takes_refuses_to_load(self):
        # Checked against the domains the bundles name, not the catalogue.
        raw = copy.deepcopy(RAW)
        _topic(raw, "T-CRYPTO")["domains"] = ["CRY"]
        with pytest.raises(ScopeConfigError, match="No topic takes the whole of QTS"):
            _parse(raw)

    # --- procedures: topic sets --------------------------------------------

    def test_a_topic_profile_naming_no_set_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        del raw["profiles"]["compact"]["topics"]
        with pytest.raises(ScopeConfigError, match="names no topic set"):
            _parse(raw)

    def test_a_set_leaving_out_a_topic_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["topic_sets"]["consolidated"].remove("T-SDLC")
        with pytest.raises(ScopeConfigError, match="leaves out T-SDLC"):
            _parse(raw)

    def test_a_set_using_a_topic_twice_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["topic_sets"]["compact"].append("T-RISK")
        with pytest.raises(ScopeConfigError, match="uses T-RISK more than once"):
            _parse(raw)

    def test_a_set_reaching_past_the_set_above_it_refuses_to_load(self):
        # Essentials may cite compact entries only. T-IDENTITY is a leaf that
        # compact has already merged into T-ACCESS-MGMT.
        raw = copy.deepcopy(RAW)
        raw["topic_sets"]["essentials"].append("T-IDENTITY")
        with pytest.raises(ScopeConfigError, match="not an entry of topic set compact"):
            _parse(raw)

    def test_a_group_of_one_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        compact = raw["topic_sets"]["compact"]
        compact[compact.index("T-RISK")] = {
            "key": "T-RISK-ONLY", "title": "Risk", "topics": ["T-RISK"],
        }
        with pytest.raises(ScopeConfigError, match="at least two members"):
            _parse(raw)

    def test_a_group_reusing_a_topic_key_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["topic_sets"]["compact"][0]["key"] = "T-RISK"
        with pytest.raises(ScopeConfigError, match="Duplicate scope key"):
            _parse(raw)

    def test_a_group_key_without_the_topic_prefix_refuses_to_load(self):
        raw = copy.deepcopy(RAW)
        raw["topic_sets"]["compact"][0]["key"] = "GOV-ASSURANCE"
        with pytest.raises(ScopeConfigError, match="must start with"):
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
        assert [s.key for s in got] == ["T-GOV-ASSURANCE", "T-ACCESS-MGMT"]

    def test_the_same_controls_land_in_each_profiles_own_procedures(self):
        pairs = [("IAC-04.2", "IAC"), ("GOV-01", "GOV")]
        keys = {
            name: [s.key for s in scopes.scopes_for(
                scopes.get_profile(name), "procedure", DOMAINS, pairs)]
            for name in ("consolidated", "compact", "essentials")
        }
        assert keys == {
            "consolidated": ["T-ACCESS", "T-GOVERNANCE"],
            "compact": ["T-GOV-ASSURANCE", "T-ACCESS-MGMT"],
            "essentials": ["T-GOVERNANCE-RISK", "T-ACCESS-MGMT"],
        }

    def test_a_domain_no_topic_takes_becomes_its_own_procedure(self):
        # A domain a later SCF release adds before bundles.yaml names it.
        domains = DOMAINS + [("ZZZ", "New Domain")]
        got = scopes.scopes_for(
            scopes.get_profile("compact"), "procedure", domains,
            [("GOV-01", "GOV"), ("ZZZ-01", "ZZZ")],
        )
        orphan = got[-1]
        assert (orphan.key, orphan.kind, orphan.title, orphan.domains) == (
            "D-ZZZ", "topic", "New Domain", ("ZZZ",))
        assert orphan.includes("ZZZ-01", "ZZZ")
        found = scopes.resolve_for_generation(
            scopes.get_profile("compact"), "procedure", "d-zzz", domains)
        assert found.key == "D-ZZZ"

    def test_a_group_resolves_for_generation_under_its_own_profile_only(self):
        assert scopes.resolve_for_generation(
            scopes.get_profile("compact"), "procedure", "t-access-mgmt", DOMAINS,
        ).key == "T-ACCESS-MGMT"
        assert scopes.resolve_for_generation(
            scopes.get_profile("consolidated"), "procedure", "T-ACCESS-MGMT", DOMAINS,
        ) is None

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


class TestGroupMembership:
    """A group asks its members; it never merges their selectors."""

    def _group(self, key):
        return scopes.resolve_key(key)

    def test_a_group_takes_what_any_member_takes(self):
        access = self._group("T-ACCESS-MGMT")
        assert [m.key for m in access.members] == ["T-ACCESS", "T-IDENTITY"]
        # IAC-04.1 through the joiners-movers-leavers families, IAC-02 through
        # the rest of the domain. Flattening the family list onto the domain
        # would lose the second.
        assert access.includes("IAC-04.1", "IAC")
        assert access.includes("IAC-02", "IAC")
        assert access.includes("HRS-08", "HRS")

    def test_a_group_does_not_take_the_rest_of_a_family_members_domain(self):
        # T-ACCESS reaches into HRS for three families only; the rest of HRS
        # belongs to T-PEOPLE.
        access = self._group("T-ACCESS-MGMT")
        assert not access.includes("HRS-05", "HRS")
        assert "HRS" in access.domains

    def test_a_whole_domain_topic_leaves_excluded_families_alone(self):
        (identity,) = [t for t in scopes.topics() if t.key == "T-IDENTITY"]
        assert identity.includes("IAC-02", "IAC")
        assert not identity.includes("IAC-04", "IAC")
        assert not identity.includes("IAC-04.1", "IAC")
        assert identity.includes("IAC-040", "IAC")

    def test_a_group_of_groups_reaches_its_leaves(self):
        recovery = self._group("T-INCIDENT-RESILIENCE")
        assert recovery.includes("IRO-02", "IRO")
        assert recovery.includes("MON-01", "MON")
        assert recovery.includes("BCD-04.1", "BCD")
        assert recovery.includes("BCD-01", "BCD")
        assert not recovery.includes("CHG-01", "CHG")

    def test_a_groups_domains_are_the_union_of_its_members(self):
        assert self._group("T-DATA-PROTECTION").domains == ("DCH", "CRY", "QTS", "PRI")

    def test_to_dict_names_the_members(self):
        assert self._group("T-ACCESS-MGMT").to_dict()["topics"] == ["T-ACCESS", "T-IDENTITY"]
        assert self._group("T-RISK").to_dict()["topics"] == []


#: A slice of the catalogue as an organisation might scope it.
CATALOGUE = [
    ("IAC-01", "IAC"), ("IAC-02", "IAC"), ("IAC-04", "IAC"), ("IAC-04.1", "IAC"),
    ("IAC-10.1", "IAC"), ("IAC-15", "IAC"), ("HRS-01", "HRS"), ("HRS-05", "HRS"),
    ("HRS-08", "HRS"), ("SAT-02", "SAT"), ("BCD-01", "BCD"), ("BCD-04", "BCD"),
    ("BCD-11.2", "BCD"), ("BCD-23", "BCD"), ("IRO-02", "IRO"), ("MON-01", "MON"),
    ("THR-01", "THR"), ("VPM-06", "VPM"), ("GOV-01", "GOV"), ("PRI-05", "PRI"),
]


#: One control for every family a topic names, as the SCF catalogue has them.
FAMILY_CONTROLS = [(family, family.split("-")[0]) for family in _JML + _BACKUP]


class TestCatalogueCoverage:
    @pytest.mark.parametrize("name", ["consolidated", "compact", "essentials"])
    def test_every_control_lands_in_exactly_one_procedure(self, name):
        domains = []
        for _, ident in CATALOGUE:
            if (ident, ident) not in domains:
                domains.append((ident, ident))
        procedures = scopes.scopes_for(scopes.get_profile(name), "procedure", domains, CATALOGUE)
        for scf_id, ident in CATALOGUE:
            homes = [p.key for p in procedures if p.includes(scf_id, ident)]
            assert len(homes) == 1, f"{scf_id} is in {homes or 'no procedure'} under {name}"

    @staticmethod
    def _families_matching_nothing(config):
        """Control families a topic names that no catalogue control belongs to."""
        ids = [scf_id for scf_id, _ in CATALOGUE + FAMILY_CONTROLS]
        return [
            f"{topic.key}: {family}"
            for topic in config.topics for family in topic.controls
            if not any(scopes._in_family(scf_id, family) for scf_id in ids)
        ]

    def test_every_family_a_topic_names_exists_in_the_catalogue(self):
        assert self._families_matching_nothing(_parse(copy.deepcopy(RAW))) == []

    def test_a_mistyped_family_fails_loudly(self):
        # "IAC-4" loads, because the loader never sees the catalogue, and then
        # matches nothing: T-ACCESS silently loses IAC-04 to T-IDENTITY. The
        # check above is what catches it.
        raw = copy.deepcopy(RAW)
        families = _topic(raw, "T-ACCESS")["controls"]
        families[families.index("IAC-04")] = "IAC-4"
        config = _parse(raw)
        assert self._families_matching_nothing(config) == ["T-ACCESS: IAC-4"]
        assert not config.by_key["T-ACCESS"].includes("IAC-04", "IAC")
        assert config.by_key["T-IDENTITY"].includes("IAC-04", "IAC")


class TestCurrency:
    def test_documents_from_another_profile_are_superseded(self):
        compact = scopes.get_profile("compact")
        assert scopes.is_current(compact, "policy", "G-GRC")
        assert not scopes.is_current(compact, "policy", "IAC")
        assert not scopes.is_current(compact, "policy", "B-GRC")
        assert not scopes.is_current(compact, "standard", "B-GRC")

    def test_procedures_from_another_profile_are_superseded(self):
        compact = scopes.get_profile("compact")
        # A leaf compact keeps as it is stays current; one it merged does not.
        assert scopes.is_current(compact, "procedure", "T-SUPPLIER")
        assert scopes.is_current(compact, "procedure", "T-DETECT-RESPOND")
        assert not scopes.is_current(compact, "procedure", "T-INCIDENT")
        assert not scopes.is_current(compact, "procedure", "T-INCIDENT-RESILIENCE")
        consolidated = scopes.get_profile("consolidated")
        assert scopes.is_current(consolidated, "procedure", "T-INCIDENT")
        assert not scopes.is_current(consolidated, "procedure", "T-DETECT-RESPOND")
        essentials = scopes.get_profile("essentials")
        assert scopes.is_current(essentials, "procedure", "T-ACCESS-MGMT")
        assert not scopes.is_current(essentials, "procedure", "T-SUPPLIER")

    def test_an_orphan_procedure_is_current_while_its_domain_is_unclaimed(self):
        compact = scopes.get_profile("compact")
        assert scopes.is_current(compact, "procedure", "D-ZZZ")
        assert not scopes.is_current(compact, "procedure", "D-IAC")

    @pytest.mark.parametrize("key", COMPACT_TOPICS + ESSENTIALS_TOPICS + ["T-INCIDENT"])
    def test_every_procedure_key_resolves_whatever_the_profile(self, key):
        assert scopes.scope_for_stored_key(key).key == key

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

    def test_a_group_key_takes_each_members_controls_once(self):
        ids = [c["scf_id"] for c in self._inputs().controls_for("T-ACCESS-MGMT")]
        assert sorted(ids) == ["HRS-12", "IAC-04", "IAC-04.2", "IAC-15"]

    def test_a_whole_domain_topic_key_skips_the_excluded_families(self):
        ids = [c["scf_id"] for c in self._inputs().controls_for("T-IDENTITY")]
        assert ids == ["IAC-15"]

    def test_a_domain_key_is_unchanged(self):
        assert [c["scf_id"] for c in self._inputs().controls_for("CRY")] == ["CRY-01"]


class TestSuccessors:
    """What now covers a superseded document, for the "now covered by" line."""

    def _keys(self, profile, generator, key, names=None):
        return [s.key for s in scopes.covering(scopes.get_profile(profile), generator, key, names)]

    def test_a_current_document_has_no_successor(self):
        assert self._keys("compact", "procedure", "T-SUPPLIER") == []
        assert self._keys("compact", "procedure", "T-ACCESS-MGMT") == []

    def test_a_merged_leaf_is_covered_by_the_group_that_holds_it(self):
        assert self._keys("compact", "procedure", "T-INCIDENT") == ["T-DETECT-RESPOND"]
        assert self._keys("essentials", "procedure", "T-INCIDENT") == ["T-INCIDENT-RESILIENCE"]
        assert self._keys("essentials", "procedure", "T-DETECT-RESPOND") == ["T-INCIDENT-RESILIENCE"]

    def test_a_family_topic_is_matched_by_membership_not_by_domain(self):
        # T-ACCESS draws on HRS and IAC. By domain it would also name People &
        # Workplace, which holds the rest of HRS and none of T-ACCESS.
        assert self._keys("compact", "procedure", "T-ACCESS") == ["T-ACCESS-MGMT"]

    def test_a_split_group_is_covered_by_each_part_in_order(self):
        assert self._keys("compact", "procedure", "T-INCIDENT-RESILIENCE") == [
            "T-DETECT-RESPOND", "T-RESILIENCE"]
        assert self._keys("consolidated", "procedure", "T-ACCESS-MGMT") == ["T-ACCESS", "T-IDENTITY"]

    def test_a_topic_under_granular_is_covered_by_its_domains(self):
        got = scopes.covering(scopes.get_profile("granular"), "procedure", "T-VULN",
                              {"VPM": "Vulnerability & Patch Management"})
        assert [(s.key, s.title) for s in got] == [
            ("VPM", "Vulnerability & Patch Management"), ("THR", "THR")]

    def test_a_domain_document_is_covered_by_the_topic_that_owns_the_domain(self):
        assert self._keys("compact", "procedure", "IRO") == ["T-DETECT-RESPOND"]
        assert self._keys("compact", "policy", "IRO") != []

    def test_descendants_are_every_scope_between_a_group_and_its_leaves(self):
        got = [s.key for s in scopes.descendants(scopes.resolve_key("T-INCIDENT-RESILIENCE"))]
        assert got == ["T-DETECT-RESPOND", "T-MONITORING", "T-INCIDENT",
                       "T-RESILIENCE", "T-BACKUP", "T-CONTINUITY"]
        assert scopes.descendants(scopes.resolve_key("T-SUPPLIER")) == []
