"""
Document scopes — what one domain-scoped document covers under each profile.

``templates/bundles.yaml`` declares four document profiles. Under the granular
profile a policy covers one SCF domain, as it always has. Under the others it
covers a bundle of domains, and procedures cover an operational topic instead.
This module turns that file into :class:`Scope` records the pipeline, the
staleness check and the API all resolve the same way.

Procedures compress with the profile as policies do. The file declares leaf
topics once, then one topic set per bundled profile; each smaller set is made
of unions of the entries of the set before it. A union is a *group* scope: it
holds its member scopes and asks them, rather than copying their selectors
into one list, because members select in different ways (a whole domain, or
named control families within one).

A scope's ``key`` is what ``generated_documents.domain_id`` stores: an SCF
domain identifier ("IAC") for a domain document, or a prefixed bundle or topic
key ("B-ACCESS", "T-INCIDENT") otherwise. The prefixes keep the two disjoint,
so a stored key always resolves to exactly one kind of scope.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import yaml

logger = logging.getLogger(__name__)

_BUNDLES_FILE = Path(__file__).parent / "templates" / "bundles.yaml"

#: The generators whose scope a profile decides. Everything else in the
#: registry is organisation-level and ignores the profile.
PROFILED_GENERATORS = ("policy", "standard", "procedure")

MODE_DOMAIN = "domain"
MODE_BUNDLE = "bundle"
MODE_TOPIC = "topic"
MODE_NONE = "none"
_MODES = (MODE_DOMAIN, MODE_BUNDLE, MODE_TOPIC, MODE_NONE)

KIND_DOMAIN = "domain"
KIND_BUNDLE = "bundle"
KIND_TOPIC = "topic"

#: Prefix of the bundle created for a domain no bundle claims; see
#: :func:`scopes_for`.
_ORPHAN_PREFIX = "D-"


class ScopeConfigError(ValueError):
    """``bundles.yaml`` is inconsistent. Raised at load, never at generation."""


@dataclass(frozen=True)
class Scope:
    """One thing a document can cover."""

    key: str
    kind: str
    title: str
    domains: Tuple[str, ...] = ()
    #: Topic scopes only: control families to take. Empty means "every control
    #: in ``domains``".
    controls: Tuple[str, ...] = ()
    description: str = ""
    #: Overrides "<title> Policy" for a document that is not named after its
    #: scope, such as the essentials manual.
    document_title: Optional[str] = None
    #: Group topics only: the scopes this one is a union of, in document order.
    #: ``domains`` is then the union of theirs, for callers that walk domains,
    #: but membership is always decided by the members themselves.
    members: Tuple["Scope", ...] = ()
    #: Whole-domain topics only: earlier family topics whose controls this one
    #: leaves to them (``exclude_topics``).
    excludes: Tuple["Scope", ...] = ()

    def includes(self, scf_id: str, domain_identifier: str) -> bool:
        """Whether a scoped control belongs to this scope."""
        if self.members:
            return any(m.includes(scf_id, domain_identifier) for m in self.members)
        if self.controls:
            return any(_in_family(scf_id, c) for c in self.controls)
        if domain_identifier not in self.domains:
            return False
        return not any(e.includes(scf_id, domain_identifier) for e in self.excludes)

    def leaves(self) -> Tuple["Scope", ...]:
        """The leaf topics this scope is made of; itself when it is one."""
        if not self.members:
            return (self,)
        return tuple(leaf for m in self.members for leaf in m.leaves())

    def select(self, controls: Iterable[Any]) -> List[Any]:
        """Filter objects with ``scf_id`` and ``domain_identifier`` attributes."""
        return [c for c in controls if self.includes(c.scf_id, c.domain_identifier)]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "title": self.title,
            "domains": list(self.domains),
            "controls": list(self.controls),
            "description": self.description,
            "document_title": self.document_title,
            "topics": [m.key for m in self.members],
        }


@dataclass(frozen=True)
class Profile:
    name: str
    display_name: str
    description: str
    recommended_for: str
    generators: Dict[str, str]
    fold_standards: bool
    bundles: Tuple[Scope, ...] = field(default_factory=tuple)
    #: The procedures this profile produces in topic mode, in order.
    topics: Tuple[Scope, ...] = field(default_factory=tuple)

    def mode_for(self, generator_name: str) -> Optional[str]:
        """How this profile scopes a generator, or ``None`` if it doesn't."""
        return self.generators.get(generator_name)


@dataclass(frozen=True)
class _Config:
    profiles: Dict[str, Profile]
    default_profile: str
    topics: Tuple[Scope, ...]
    by_key: Dict[str, Scope]


def _in_family(scf_id: str, family: str) -> bool:
    """Whether a control is the named control or one of its enhancements.

    The dot is the boundary: "IAC-04" takes "IAC-04.2" and not "IAC-040".
    """
    return scf_id == family or scf_id.startswith(family + ".")


def _check_key(key: str) -> None:
    if not key or len(key) > 64 or key[:2] not in ("B-", "G-", "M-", "T-"):
        raise ScopeConfigError(
            f"Scope key '{key}' must start with B-, G-, M- or T- and fit 64 characters"
        )


def _parse(raw: Dict[str, Any]) -> _Config:
    bundle_sets: Dict[str, List[Scope]] = {}
    by_key: Dict[str, Scope] = {}

    def register(scope: Scope) -> None:
        _check_key(scope.key)
        if scope.key in by_key:
            raise ScopeConfigError(f"Duplicate scope key: {scope.key}")
        by_key[scope.key] = scope

    # Consolidated bundles first: groups and the manual are declared as unions
    # of them, so they must exist before anything refers to them.
    sets = raw.get("bundle_sets") or {}
    for set_name in sets:
        scopes: List[Scope] = []
        for entry in sets[set_name] or []:
            if "domains" in entry:
                domains = tuple(d.upper() for d in entry["domains"])
            else:
                domains = ()
                for ref in entry.get("bundles") or []:
                    if ref not in by_key:
                        raise ScopeConfigError(
                            f"{entry['key']} refers to {ref}, which is not declared before it"
                        )
                    domains += by_key[ref].domains
            scope = Scope(
                key=entry["key"],
                kind=KIND_BUNDLE,
                title=entry["title"],
                domains=domains,
                document_title=entry.get("document_title"),
            )
            register(scope)
            scopes.append(scope)
        bundle_sets[set_name] = scopes

    topics: List[Scope] = []
    for entry in raw.get("topics") or []:
        key = entry["key"]
        domains = tuple(d.upper() for d in entry.get("domains") or ())
        controls = tuple(c.upper() for c in entry.get("controls") or ())
        if not domains and not controls:
            raise ScopeConfigError(f"Topic {key} selects no controls")
        excludes: List[Scope] = []
        for ref in entry.get("exclude_topics") or []:
            # An exclusion carves a family topic's controls out of a
            # whole-domain topic. Anything looser -- excluding a later topic,
            # a whole domain, or a topic with exclusions of its own -- makes
            # "which procedure is this control in?" depend on reading order.
            if controls:
                raise ScopeConfigError(
                    f"Topic {key} lists controls, so it cannot also exclude {ref}"
                )
            found = by_key.get(ref)
            if found is None or found.kind != KIND_TOPIC:
                raise ScopeConfigError(
                    f"Topic {key} excludes {ref}, which is not a topic declared before it"
                )
            if not found.controls or found.excludes:
                raise ScopeConfigError(
                    f"Topic {key} excludes {ref}, which must be a topic that "
                    "lists control families and excludes nothing itself"
                )
            excludes.append(found)
        if controls and not domains:
            domains = tuple(sorted({c.split("-")[0] for c in controls}))
        scope = Scope(
            key=key,
            kind=KIND_TOPIC,
            title=entry["title"],
            description=(entry.get("description") or "").strip(),
            domains=domains,
            controls=controls,
            excludes=tuple(excludes),
        )
        register(scope)
        topics.append(scope)
    _check_leaf_partition(topics, [b for scopes in bundle_sets.values() for b in scopes])

    topic_sets = _parse_topic_sets(raw.get("topic_sets") or {}, topics, register)

    profiles: Dict[str, Profile] = {}
    reference: Optional[Tuple[str, frozenset]] = None
    for name, entry in (raw.get("profiles") or {}).items():
        generators = dict(entry.get("generators") or {})
        for gen, mode in generators.items():
            if gen not in PROFILED_GENERATORS or mode not in _MODES:
                raise ScopeConfigError(f"Profile {name}: invalid generator mode {gen}={mode}")
        set_name = entry.get("bundles")
        bundles = tuple(bundle_sets.get(set_name, ())) if set_name else ()
        if MODE_BUNDLE in generators.values() and not bundles:
            raise ScopeConfigError(f"Profile {name} uses bundles but names no bundle set")
        if bundles:
            # Every domain exactly once, and the same domains as every other
            # bundled profile. A domain dropped here would silently disappear
            # from that organisation's policies.
            seen: List[str] = [d for b in bundles for d in b.domains]
            dupes = sorted({d for d in seen if seen.count(d) > 1})
            if dupes:
                raise ScopeConfigError(f"Profile {name} covers {', '.join(dupes)} more than once")
            covered = frozenset(seen)
            if reference is None:
                reference = (name, covered)
            elif covered != reference[1]:
                diff = sorted(covered.symmetric_difference(reference[1]))
                raise ScopeConfigError(
                    f"Profile {name} and {reference[0]} cover different domains: {', '.join(diff)}"
                )
        topic_set = entry.get("topics")
        profile_topics = tuple(topic_sets.get(topic_set, ())) if topic_set else ()
        if MODE_TOPIC in generators.values() and not profile_topics:
            # Without a set the profile would list no procedures at all, and
            # every in-scope control would silently have none.
            raise ScopeConfigError(f"Profile {name} uses topics but names no topic set")
        profiles[name] = Profile(
            name=name,
            display_name=entry.get("display_name") or name.title(),
            description=(entry.get("description") or "").strip(),
            recommended_for=(entry.get("recommended_for") or "").strip(),
            generators=generators,
            fold_standards=bool(entry.get("fold_standards", False)),
            bundles=bundles,
            topics=profile_topics,
        )

    default_profile = raw.get("default_profile") or "granular"
    if default_profile not in profiles:
        raise ScopeConfigError(f"default_profile {default_profile} is not a profile")
    return _Config(
        profiles=profiles,
        default_profile=default_profile,
        topics=tuple(topics),
        by_key=by_key,
    )


def _check_leaf_partition(topics: Sequence[Scope], bundles: Sequence[Scope]) -> None:
    """Refuse a topic list under which a control could have two procedures, or none.

    Checked against the domains this file's own bundles name, never the live
    catalogue: a catalogue import that adds a domain must not stop the backend
    from starting. That domain gets a procedure of its own instead; see
    :func:`scopes_for`.
    """
    family_topics = [t for t in topics if t.controls]
    whole_topics = [t for t in topics if not t.controls]

    # No two family topics may claim the same control: equal families, or one
    # family being an enhancement of another ("IAC-04" and "IAC-04.1").
    claimed: List[Tuple[str, str]] = []
    for topic in family_topics:
        for family in topic.controls:
            for other_family, other_key in claimed:
                if _in_family(family, other_family) or _in_family(other_family, family):
                    raise ScopeConfigError(
                        f"Topics {other_key} and {topic.key} both take {family}: "
                        f"{other_family} and {family} overlap"
                    )
            claimed.append((family, topic.key))

    home: Dict[str, Scope] = {}
    for topic in whole_topics:
        for domain in topic.domains:
            if domain in home:
                raise ScopeConfigError(
                    f"Domain {domain} is taken whole by both {home[domain].key} and {topic.key}"
                )
            home[domain] = topic

    # A family topic takes controls out of a domain another topic takes whole,
    # so that topic must leave them to it.
    for topic in family_topics:
        for domain in topic.domains:
            owner = home.get(domain)
            if owner is not None and topic not in owner.excludes:
                raise ScopeConfigError(
                    f"Topic {owner.key} takes all of {domain} and {topic.key} takes "
                    f"families in it; {owner.key} must list {topic.key} in exclude_topics"
                )

    bundle_domains = {d for b in bundles for d in b.domains}
    if topics and bundle_domains:
        missing = sorted(bundle_domains - set(home))
        if missing:
            raise ScopeConfigError(
                f"No topic takes the whole of {', '.join(missing)}; controls there "
                "would have no procedure"
            )


def _parse_topic_sets(
    raw_sets: Dict[str, Any],
    leaves: Sequence[Scope],
    register: Callable[[Scope], None],
) -> Dict[str, List[Scope]]:
    """Read ``topic_sets``: one list of procedures per bundled profile.

    The sets are read in file order and each is built only from the entries of
    the one before it (the first from the leaf topics), every one of them
    exactly once. So every set holds every leaf exactly once, and moving
    between sets only ever merges or splits whole procedures.
    """
    out: Dict[str, List[Scope]] = {}
    previous: Dict[str, Scope] = {t.key: t for t in leaves}
    previous_name = "topics"
    for set_name, entries in raw_sets.items():
        scopes_in_set: List[Scope] = []
        cited: List[str] = []

        def take(ref: str, by: str) -> Scope:
            if ref not in previous:
                raise ScopeConfigError(
                    f"Topic set {set_name}: {by} refers to {ref}, "
                    f"which is not an entry of {previous_name}"
                )
            if ref in cited:
                raise ScopeConfigError(f"Topic set {set_name} uses {ref} more than once")
            cited.append(ref)
            return previous[ref]

        for entry in entries or []:
            if isinstance(entry, str):
                scopes_in_set.append(take(entry, entry))
                continue
            key = entry["key"]
            refs = list(entry.get("topics") or [])
            if len(refs) < 2:
                raise ScopeConfigError(
                    f"Topic group {key} needs at least two members; "
                    "write the member's own key instead of a group of one"
                )
            members = tuple(take(ref, key) for ref in refs)
            domains: List[str] = []
            for member in members:
                domains += [d for d in member.domains if d not in domains]
            group = Scope(
                key=key,
                kind=KIND_TOPIC,
                title=entry["title"],
                description=(entry.get("description") or "").strip(),
                domains=tuple(domains),
                members=members,
            )
            register(group)
            scopes_in_set.append(group)

        unused = [k for k in previous if k not in cited]
        if unused:
            raise ScopeConfigError(
                f"Topic set {set_name} leaves out {', '.join(unused)}; "
                "controls there would have no procedure"
            )
        out[set_name] = scopes_in_set
        previous = {s.key: s for s in scopes_in_set}
        previous_name = f"topic set {set_name}"
    return out


@functools.lru_cache(maxsize=1)
def _load() -> _Config:
    config = _parse(yaml.safe_load(_BUNDLES_FILE.read_text(encoding="utf-8")))
    logger.info(
        "doc_gen scopes loaded: %d profiles, %d scopes",
        len(config.profiles), len(config.by_key),
    )
    return config


def profile_names() -> List[str]:
    return list(_load().profiles)


def default_profile() -> str:
    return _load().default_profile


def get_profile(name: Optional[str]) -> Profile:
    """The named profile, falling back to the default for an unknown name.

    Falling back rather than raising keeps generation working if a profile is
    ever renamed in the YAML while an organisation still stores the old name.
    """
    config = _load()
    return config.profiles.get(name or "", config.profiles[config.default_profile])


def topics() -> List[Scope]:
    """The leaf topics, in file order. A profile's own procedures are
    ``get_profile(name).topics``."""
    return list(_load().topics)


def _topic_claimed_domains() -> set:
    """Domains some leaf topic takes whole. Every profile's set is built from
    the same leaves, so this is the same for all of them."""
    return {d for t in _load().topics if not t.controls for d in t.domains}


def _orphan(ident: str, name: str, kind: str) -> Scope:
    return Scope(key=f"{_ORPHAN_PREFIX}{ident}", kind=kind, title=name, domains=(ident,))


def resolve_key(key: str) -> Optional[Scope]:
    """The bundle or topic a stored key names, or ``None`` for a domain key."""
    return _load().by_key.get((key or "").upper())


def domain_scope(identifier: str, name: str) -> Scope:
    return Scope(key=identifier, kind=KIND_DOMAIN, title=name, domains=(identifier,))


def scopes_for(
    profile: Profile,
    generator_name: str,
    domains: Sequence[Tuple[str, str]],
    scoped_control_ids: Optional[Iterable[Tuple[str, str]]] = None,
) -> List[Scope]:
    """Every scope a generator can produce a document for, under a profile.

    Args:
        profile: The organisation's document profile.
        generator_name: A registry generator name.
        domains: ``(identifier, name)`` of each domain with scoped controls,
            in catalogue order.
        scoped_control_ids: ``(scf_id, domain_identifier)`` of each scoped
            control. Used to drop topics with nothing in scope; when omitted,
            every topic is returned.

    A bundle is listed when any of its domains has scoped controls. A scoped
    domain that no bundle claims -- a domain added by a later SCF release
    before ``bundles.yaml`` names it -- becomes a bundle of its own rather than
    vanishing from the organisation's policies. Topics do the same: a scoped
    domain no topic takes whole gets a procedure of its own.
    """
    mode = profile.mode_for(generator_name)
    if mode is None or mode == MODE_NONE:
        return []
    if mode == MODE_DOMAIN:
        return [domain_scope(ident, name) for ident, name in domains]

    present = {ident for ident, _ in domains}
    if mode == MODE_TOPIC:
        if scoped_control_ids is None:
            out = list(profile.topics)
        else:
            pairs = list(scoped_control_ids)
            out = [t for t in profile.topics if any(t.includes(s, d) for s, d in pairs)]
        claimed = _topic_claimed_domains()
        out += [_orphan(ident, name, KIND_TOPIC) for ident, name in domains
                if ident not in claimed]
        return out

    out = [b for b in profile.bundles if present.intersection(b.domains)]
    claimed = {d for b in profile.bundles for d in b.domains}
    out += [_orphan(ident, name, KIND_BUNDLE) for ident, name in domains
            if ident not in claimed]
    return out


def resolve_for_generation(
    profile: Profile,
    generator_name: str,
    key: str,
    domains: Sequence[Tuple[str, str]],
) -> Optional[Scope]:
    """The scope a generation request names, or ``None`` if the profile has no
    such scope for that generator.

    ``domains`` is as for :func:`scopes_for`. Orphan bundles and topics are
    synthesised there, so they resolve here too.
    """
    wanted = (key or "").upper()
    for scope in scopes_for(profile, generator_name, domains):
        if scope.key == wanted:
            return scope
    return None


def scope_for_stored_key(key: str) -> Optional[Scope]:
    """Resolve a ``domain_id`` read back from the database.

    Unlike :func:`resolve_for_generation` this ignores the current profile: a
    document generated under another profile is still a document about its own
    scope, and staleness must be measured against that scope.
    """
    key = (key or "").upper()
    if not key:
        return None
    found = resolve_key(key)
    if found is not None:
        return found
    if key.startswith(_ORPHAN_PREFIX):
        ident = key[len(_ORPHAN_PREFIX):]
        return Scope(key=key, kind=KIND_BUNDLE, title=ident, domains=(ident,))
    return None


def is_current(profile: Profile, generator_name: str, stored_key: str) -> bool:
    """Whether a document's scope is one the profile still produces.

    A document from another profile is *superseded*: kept, readable and
    exportable, but no longer the organisation's current document for its
    controls.
    """
    mode = profile.mode_for(generator_name)
    if mode is None:
        return True
    key = (stored_key or "").upper()
    if mode == MODE_NONE:
        return False
    if mode == MODE_DOMAIN:
        return scope_for_stored_key(key) is None
    if mode == MODE_TOPIC:
        # Only the profile's own set: a procedure another profile merges or
        # splits is superseded here, exactly as its policies are.
        if any(t.key == key for t in profile.topics):
            return True
        claimed = _topic_claimed_domains()
    elif any(b.key == key for b in profile.bundles):
        return True
    else:
        claimed = {d for b in profile.bundles for d in b.domains}
    # An orphan is current while its domain is still unclaimed.
    return key.startswith(_ORPHAN_PREFIX) and resolve_key(key) is None \
        and key[len(_ORPHAN_PREFIX):] not in claimed


def covering(
    profile: Profile,
    generator_name: str,
    stored_key: str,
    domain_names: Optional[Dict[str, str]] = None,
) -> List[Scope]:
    """The profile's documents that cover what a superseded document covered.

    Empty for a document the profile still produces, and for a generator the
    profile does not produce at all. Topic sets nest, so a procedure's
    successors are exact: the entries of the profile's set built from the same
    leaf topics. One entry when the profile merged it into a group, several
    when the profile splits a group it was. Anything else is matched on the
    domains the document drew on.
    """
    mode = profile.mode_for(generator_name)
    if mode is None or mode == MODE_NONE or is_current(profile, generator_name, stored_key):
        return []
    key = (stored_key or "").upper()
    stored = scope_for_stored_key(key)
    domains = set(stored.domains) if stored is not None else {key}
    if mode == MODE_DOMAIN:
        names = domain_names or {}
        ordered = stored.domains if stored is not None else (key,)
        return [domain_scope(d, names.get(d, d)) for d in ordered]
    if mode == MODE_TOPIC:
        if stored is not None and stored.kind == KIND_TOPIC and resolve_key(key) is not None:
            wanted = {leaf.key for leaf in stored.leaves()}
            return [t for t in profile.topics
                    if wanted.intersection(leaf.key for leaf in t.leaves())]
        return [t for t in profile.topics if domains.intersection(t.domains)]
    return [b for b in profile.bundles if domains.intersection(b.domains)]


def descendants(scope: Scope) -> List[Scope]:
    """Every scope a group is built from, groups and leaf topics alike.

    A document stored under any of these keys is a predecessor of the group's
    document: it covered part of what the group now covers.
    """
    out: List[Scope] = []
    for member in scope.members:
        out.append(member)
        out.extend(descendants(member))
    return out
