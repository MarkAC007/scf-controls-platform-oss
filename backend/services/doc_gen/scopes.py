"""
Document scopes — what one domain-scoped document covers under each profile.

``templates/bundles.yaml`` declares four document profiles. Under the granular
profile a policy covers one SCF domain, as it always has. Under the others it
covers a bundle of domains, and procedures cover an operational topic instead.
This module turns that file into :class:`Scope` records the pipeline, the
staleness check and the API all resolve the same way.

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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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

    def includes(self, scf_id: str, domain_identifier: str) -> bool:
        """Whether a scoped control belongs to this scope."""
        if self.controls:
            return any(scf_id == c or scf_id.startswith(c + ".") for c in self.controls)
        return domain_identifier in self.domains

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

    def mode_for(self, generator_name: str) -> Optional[str]:
        """How this profile scopes a generator, or ``None`` if it doesn't."""
        return self.generators.get(generator_name)


@dataclass(frozen=True)
class _Config:
    profiles: Dict[str, Profile]
    default_profile: str
    topics: Tuple[Scope, ...]
    by_key: Dict[str, Scope]


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
        scope = Scope(
            key=entry["key"],
            kind=KIND_TOPIC,
            title=entry["title"],
            description=(entry.get("description") or "").strip(),
            domains=tuple(d.upper() for d in entry.get("domains") or ()),
            controls=tuple(c.upper() for c in entry.get("controls") or ()),
        )
        if not scope.domains and not scope.controls:
            raise ScopeConfigError(f"Topic {scope.key} selects no controls")
        if scope.controls and not scope.domains:
            scope = Scope(
                key=scope.key, kind=scope.kind, title=scope.title,
                description=scope.description, controls=scope.controls,
                domains=tuple(sorted({c.split("-")[0] for c in scope.controls})),
            )
        register(scope)
        topics.append(scope)

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
        profiles[name] = Profile(
            name=name,
            display_name=entry.get("display_name") or name.title(),
            description=(entry.get("description") or "").strip(),
            recommended_for=(entry.get("recommended_for") or "").strip(),
            generators=generators,
            fold_standards=bool(entry.get("fold_standards", False)),
            bundles=bundles,
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
    return list(_load().topics)


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
    vanishing from the organisation's policies.
    """
    mode = profile.mode_for(generator_name)
    if mode is None or mode == MODE_NONE:
        return []
    if mode == MODE_DOMAIN:
        return [domain_scope(ident, name) for ident, name in domains]

    present = {ident for ident, _ in domains}
    if mode == MODE_TOPIC:
        if scoped_control_ids is None:
            return list(_load().topics)
        pairs = list(scoped_control_ids)
        return [t for t in _load().topics if any(t.includes(s, d) for s, d in pairs)]

    out = [b for b in profile.bundles if present.intersection(b.domains)]
    claimed = {d for b in profile.bundles for d in b.domains}
    for ident, name in domains:
        if ident not in claimed:
            out.append(Scope(
                key=f"{_ORPHAN_PREFIX}{ident}", kind=KIND_BUNDLE, title=name,
                domains=(ident,),
            ))
    return out


def resolve_for_generation(
    profile: Profile,
    generator_name: str,
    key: str,
    domains: Sequence[Tuple[str, str]],
) -> Optional[Scope]:
    """The scope a generation request names, or ``None`` if the profile has no
    such scope for that generator.

    ``domains`` is as for :func:`scopes_for`. Orphan bundles are synthesised
    there, so they resolve here too.
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
        return any(t.key == key for t in _load().topics)
    if any(b.key == key for b in profile.bundles):
        return True
    # An orphan bundle is current while its domain is still unclaimed.
    claimed = {d for b in profile.bundles for d in b.domains}
    return key.startswith(_ORPHAN_PREFIX) and resolve_key(key) is None \
        and key[len(_ORPHAN_PREFIX):] not in claimed
