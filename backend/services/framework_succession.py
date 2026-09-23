"""Derived succession matching for catalogue frameworks.

Controls declare their own predecessors: the workbook carries a ``Legacy SCF #``
column and ``catalog_diff`` reads it, so a control renumbering is a *fact*.
Frameworks declare nothing. The framework id is a slug of a spreadsheet column
header, so an edition bump or a namespace change presents as an unrelated
removal plus an unrelated addition, and an organisation scoped to the old id
watches its framework disappear.

This module derives the missing link. Everything here is a **heuristic**, and
the module is written so that fact stays visible downstream:

* nothing in it mutates an organisation's framework selection;
* every proposal carries a score, the signals that produced it, and an
  ``ambiguous`` flag when more than one candidate was close;
* the confidence tiers are named, so a reviewer can tell a namespace rename
  (near-certain) from a fuzzy name match (needs a human).

Two independent signals
-----------------------
**id_stem** — canonicalise the jurisdiction prefix, then strip *edition*
tokens. Deliberately conservative about what counts as an edition token: a
trailing bare number is NOT stripped by default, because ``far_52_204_21``,
``far_52_204_25`` and ``far_52_204_27`` are three different regulations that
collapse onto one stem the moment you do. See ``_strip_edition_tokens``.

**display_name** — Dice similarity over content tokens of the workbook's own
focal-document name, after removing the jurisdiction words and the edition
parenthetical that the id signal already accounts for.

They are scored separately and reported separately. A caller measuring recall
can therefore attribute it, rather than crediting one signal for the other's
work.

Empirical note (SCF 2026.2 -> 2026.3): 69 of 75 genuine retirements are one
mechanical transformation, ``us_* -> usa_<jurisdiction>_*`` with US state
abbreviations expanded to full names. That is why jurisdiction canonicalisation
carries most of the weight here and suffix-stripping carries little — the
change was at the *front* of the id, not the back.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Jurisdiction canonicalisation
# ---------------------------------------------------------------------------

# SCF 2026.3 re-namespaced the US framework set: ``us_<abbrev>_`` became
# ``usa_<full state name>_`` and bare ``us_`` became ``usa_federal_``. Mapping
# both spellings onto one canonical token is what makes the majority of that
# release's churn legible.
#
# This table is a durable property of US jurisdiction naming, not a 2026.3
# constant: it is the standard postal-abbreviation set. If a future release
# invents a third spelling, the table gains a row; it does not need rewriting.
_US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas",
    "ca": "california", "co": "colorado", "ct": "connecticut", "de": "delaware",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho",
    "il": "illinois", "in": "indiana", "ia": "iowa", "ks": "kansas",
    "ky": "kentucky", "la": "louisiana", "me": "maine", "md": "maryland",
    "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska",
    "nv": "nevada", "nh": "new_hampshire", "nj": "new_jersey",
    "nm": "new_mexico", "ny": "new_york", "nc": "north_carolina",
    "nd": "north_dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon",
    "pa": "pennsylvania", "ri": "rhode_island", "sc": "south_carolina",
    "sd": "south_dakota", "tn": "tennessee", "tx": "texas", "ut": "utah",
    "vt": "vermont", "va": "virginia", "wa": "washington",
    "wv": "west_virginia", "wi": "wisconsin", "wy": "wyoming",
    "dc": "district_of_columbia", "pr": "puerto_rico",
}
_US_STATE_NAMES = {v: k for k, v in _US_STATES.items()}

# Region prefixes SCF uses. Candidates are only compared inside the same
# region, which prunes a large class of cross-jurisdiction false positives
# (an Australian standard is never the successor to a German one).
_REGION_PREFIXES = ("apac", "emea", "americas", "usa", "us")


@dataclass(frozen=True)
class FrameworkIdentity:
    """A framework id decomposed into the parts worth comparing."""

    raw: str
    region: str          # 'us', 'apac', 'emea', 'americas', or '' when absent
    jurisdiction: str    # canonical: 'federal', 'california', 'australia', ''
    body: str            # everything after the jurisdiction, edition intact
    stem: str            # body with edition tokens stripped

    @property
    def canonical(self) -> str:
        return f"{self.region}:{self.jurisdiction}:{self.stem}"

    @property
    def canonical_with_edition(self) -> str:
        return f"{self.region}:{self.jurisdiction}:{self.body}"


_YEAR_RE = re.compile(r"^(19|20)\d{2}$")
_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}
_REVISION_RE = re.compile(r"^r\d+$")
_VERSION_RE = re.compile(r"^v\d+$")


def _strip_edition_tokens(tokens: List[str]) -> List[str]:
    """Remove trailing tokens that denote an edition rather than an identity.

    What is stripped:
      * a trailing 4-digit year                      (``_2020``, ``_2026``)
      * a trailing month name preceding that year    (``_march_2026``)
      * a trailing ``v<n>``                          (``_v1_1`` -> ``_v1``)
      * a bare number that immediately follows an ``r<n>`` revision token,
        which is how SCF spells a revision's sub-release
        (``nist_800_53_r5_2`` -> ``nist_800_53_r5``)

    What is deliberately NOT stripped: a bare trailing number in any other
    position. ``far_52_204_21`` / ``far_52_204_25`` / ``far_52_204_27`` are
    three distinct regulations; ``cmmc_2_0_level_1`` / ``level_2`` / ``level_3``
    and ``tx_ramp_level_1`` / ``level_2`` are distinct programme tiers;
    ``fedramp_r5_low`` / ``moderate`` / ``high`` are distinct baselines. Every
    one of those collapses to a single stem under naive numeric stripping, and
    a collapsed stem is exactly how a matcher produces a confident wrong
    answer. Version-shaped suffixes that genuinely need stripping
    (``sparta`` -> ``sparta_4_0``) are handled by the LOOSE tier instead, which
    requires display-name corroboration before it will propose anything.
    """
    out = list(tokens)
    changed = True
    while changed and out:
        changed = False
        if _YEAR_RE.match(out[-1]):
            out.pop()
            changed = True
            if out and out[-1] in _MONTHS:
                out.pop()
            continue
        if _VERSION_RE.match(out[-1]):
            out.pop()
            changed = True
            continue
        if (
            len(out) >= 2
            and out[-1].isdigit()
            and _REVISION_RE.match(out[-2])
        ):
            out.pop()
            changed = True
            continue
    return out


# Tokens whose following number is a DISCRIMINATOR, not a version. Stripping a
# digit after one of these is what collapses cmmc_2_0_level_1 / _2 / _3 and
# tx_ramp_level_1 / _2 onto a single stem, which is precisely how a matcher
# produces a confident wrong answer.
_ORDINAL_QUALIFIERS = {
    "level", "tier", "part", "phase", "class", "category", "group",
    "baseline", "stage", "annex", "appendix", "section", "chapter", "volume",
}


def _strip_version_tail(tokens: List[str]) -> List[str]:
    """The LOOSE tier's extra step: drop a trailing run of bare numbers.

    Refuses to strip a digit that follows an ``_ORDINAL_QUALIFIERS`` word, and
    only ever used by a tier that additionally requires display-name
    corroboration — see ``_strip_edition_tokens`` for why this is unsafe alone.
    """
    out = list(tokens)
    while len(out) > 1 and out[-1].isdigit():
        if out[-2] in _ORDINAL_QUALIFIERS:
            break
        out.pop()
    return out


def parse_framework_id(fw_id: str) -> FrameworkIdentity:
    """Decompose a framework id into region / jurisdiction / body / stem."""
    tokens = [t for t in fw_id.lower().split("_") if t]
    if not tokens:
        return FrameworkIdentity(fw_id, "", "", "", "")

    region, jurisdiction = "", ""
    if tokens[0] in _REGION_PREFIXES:
        head = tokens.pop(0)
        # 'us' and 'usa' are the same region under two spellings.
        region = "us" if head in ("us", "usa") else head
        if region == "us" and tokens:
            nxt = tokens[0]
            if nxt in _US_STATES:                 # us_ca_...
                jurisdiction = _US_STATES[tokens.pop(0)]
            elif nxt in _US_STATE_NAMES:          # usa_california_...
                jurisdiction = tokens.pop(0)
            elif nxt == "federal":                # usa_federal_...
                jurisdiction = tokens.pop(0)
            elif nxt in ("new", "north", "south", "west", "rhode", "district", "puerto"):
                # multi-word state names survive slugification as separate
                # tokens: usa_new_york_..., usa_west_virginia_...
                guess = "_".join(tokens[:2])
                if guess in _US_STATE_NAMES:
                    jurisdiction = guess
                    del tokens[:2]
            if not jurisdiction:
                # Bare `us_<body>` is federal by construction; 2026.3 spells the
                # same thing `usa_federal_<body>`.
                jurisdiction = "federal"
        elif tokens:
            jurisdiction = tokens.pop(0)

    body_tokens = tokens
    stem_tokens = _strip_edition_tokens(body_tokens)
    return FrameworkIdentity(
        raw=fw_id,
        region=region,
        jurisdiction=jurisdiction,
        body="_".join(body_tokens),
        stem="_".join(stem_tokens),
    )


# ---------------------------------------------------------------------------
# Display-name signal
# ---------------------------------------------------------------------------

_PARENTHETICAL_RE = re.compile(r"\(([^)]*)\)")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")

# Words that carry no discriminating power once the id signal has already
# accounted for jurisdiction and edition.
_NAME_STOPWORDS = {
    "the", "of", "and", "for", "a", "an", "in", "on", "to",
    "act", "law", "rule", "rules", "regulation", "regulations",
    "standard", "standards", "framework", "guidance", "guideline",
    "guidelines", "requirements", "program", "version", "rev", "revision",
    "united", "states", "usa", "us", "federal", "state",
}
_NAME_STOPWORDS |= _MONTHS
_NAME_STOPWORDS |= set(_US_STATE_NAMES)


def name_tokens(display_name: Optional[str]) -> frozenset:
    """Content tokens of a focal-document name, edition parentheticals removed."""
    if not display_name:
        return frozenset()
    text = display_name.lower()
    # "(March 2026)" / "(2020)" / "(Compilation No. 9, 4 June 2026)" are edition
    # markers; the id signal already covers editions, so they are removed here
    # to keep the two signals from measuring the same thing twice.
    text = _PARENTHETICAL_RE.sub(
        lambda m: "" if re.search(r"(19|20)\d{2}", m.group(1)) else m.group(1),
        text,
    )
    raw = [t for t in _NON_WORD_RE.split(text) if t]
    out = set()
    for t in raw:
        if t in _NAME_STOPWORDS or _YEAR_RE.match(t):
            continue
        # Digits are kept at any length: "Level 1" vs "Level 2" differ ONLY in
        # that character, and dropping it made the two token-identical.
        if not t.isdigit() and len(t) < 2:
            continue
        # Light plural folding, so "Countermeasures" matches "Countermeasure".
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.add(t)
    return frozenset(out)


def name_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Dice coefficient over content tokens. 0.0 when either side is empty."""
    ta, tb = name_tokens(a), name_tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb)
    return (2.0 * overlap) / (len(ta) + len(tb))


# ---------------------------------------------------------------------------
def focal_document_stem(fdi: Optional[str]) -> Optional[str]:
    """A focal-document identifier with its edition suffix removed.

    ``apac-aus-ism-2026-march`` and ``apac-aus-ism-2026-june`` both reduce to
    ``apac-aus-ism``; ``general-sparta`` and ``general-sparta-4-0`` both reduce
    to ``general-sparta``. Those six pairs are the whole of the 2026.2 ->
    2026.3 churn that exact FDI equality does not already cover.

    Stripping is deliberately conservative in the one place it matters: a bare
    digit that follows an ordinal qualifier (``-level-1``, ``-tier-2``) is a
    discriminator, not a version, so ``usa-federal-dow-cmmc-2-level-1`` keeps
    its tail rather than colliding with ``-level-2``.
    """
    if not fdi:
        return None
    tokens = [t for t in re.split(r"[-_\s]+", str(fdi).strip().lower()) if t]
    if not tokens:
        return None
    changed = True
    while changed and len(tokens) > 1:
        changed = False
        tail = tokens[-1]
        if len(tokens) >= 2 and tokens[-2] in _ORDINAL_QUALIFIERS:
            break
        if _YEAR_RE.match(tail):
            tokens.pop()
            changed = True
            if tokens and tokens[-1] in _MONTHS:
                tokens.pop()
            continue
        if tail in _MONTHS:
            tokens.pop()
            changed = True
            continue
        if tail.isdigit() or _VERSION_RE.match(tail) or _REVISION_RE.match(tail):
            tokens.pop()
            changed = True
            continue
    return "-".join(tokens)


# Matching
# ---------------------------------------------------------------------------

# Confidence tiers, highest first. The tier is part of the output because
# "renamed namespace" and "the names look a bit alike" are different claims and
# a reviewer needs to be able to tell them apart.
# DECLARED tiers. These are not heuristics: the workbook's Focal Documents
# sheet carries a Focal Document Identifier per framework, the publisher's own
# stable id, and it survives the mapping-column rename that the framework id is
# derived from. It is the framework equivalent of the control sheet's
# 'Legacy SCF #'. 69 of the 75 genuine 2026.2 -> 2026.3 removals are resolved
# by FDI equality alone, with no inference of any kind.
TIER_DECLARED = "focal_document"          # identical FDI on both sides
TIER_DECLARED_STEM = "focal_document_stem"  # FDI differs only by its edition

TIER_NAMESPACE = "namespace"      # identical once jurisdiction is canonicalised
TIER_EDITION = "edition"          # identical once the edition token is dropped
TIER_LOOSE = "loose"              # version-tail stripped, needs name support
TIER_NAME_ONLY = "name_only"      # id signal silent, names agree strongly

_TIER_BASE_SCORE = {
    TIER_DECLARED: 1.00,
    TIER_DECLARED_STEM: 0.90,
    TIER_NAMESPACE: 0.95,
    TIER_EDITION: 0.85,
    TIER_LOOSE: 0.60,
    TIER_NAME_ONLY: 0.55,
}

# A LOOSE or NAME_ONLY proposal is not emitted at all below these name floors.
_TIER_NAME_FLOOR = {
    TIER_DECLARED: 0.0,
    TIER_DECLARED_STEM: 0.0,
    TIER_NAMESPACE: 0.0,
    TIER_EDITION: 0.0,
    TIER_LOOSE: 0.34,
    TIER_NAME_ONLY: 0.60,
}

# Above this, the top candidate is bound as `superseded_by`; below it the
# proposal is offered as a suggestion only. A near-tie also blocks binding.
AUTO_PROPOSE_THRESHOLD = 0.80
AMBIGUITY_MARGIN = 0.10

# Control-set overlap: what it is for, and what it is NOT for.
#
# Every control carries ``framework_mappings`` keyed by framework id, so each
# framework has an observable control set. Measured across 2026.2 -> 2026.3,
# once the control renumbering is undone through the workbook's Legacy SCF #
# crosswalk, the 69 declared true pairs have a median overlap of 1.000 and a
# 10th percentile of 0.833. Without that remapping the same pairs median 0.075,
# because 2026.3 renumbered nearly every control - so the raw sets are not
# comparable and an overlap computed on them is worse than no signal at all.
#
# It is NOT a scorer. On the same measurement a WRONG candidate ties or beats
# the true one twice in 69: ``nist_800_53_r5`` against ``nist_800_82_r3`` at
# 0.982 each, and ``us_far_52_204_21`` against ``usa_federal_cmmc_2_0_level_1``
# at 0.881 - FAR 52.204-21 and CMMC Level 1 genuinely cover the same seventeen
# controls while being different instruments. Ranking on overlap would merge
# them at high confidence. Three true pairs also sit at 0.000.
#
# So it is used in exactly two ways: as reviewable evidence attached to every
# candidate, and as a VETO on a derived binding whose control sets say the two
# documents cover different ground. A veto only ever withholds a proposal; it
# never creates one. A DECLARED pairing is never vetoed - the publisher's own
# identifier outranks our arithmetic - but a low overlap is still recorded so a
# reviewer sees it.
CONTROL_OVERLAP_VETO = 0.50
# Below this many controls on either side the ratio is noise, not evidence.
CONTROL_OVERLAP_MIN_SET = 5


@dataclass
class SuccessionCandidate:
    successor_id: str
    successor_name: Optional[str]
    score: float
    tier: str
    signals: List[str] = field(default_factory=list)
    ambiguous: bool = False
    # Share of the removed framework's controls that also map to this
    # candidate, once control renumbering is undone. None when either side has
    # no control set to compare. See ``CONTROL_OVERLAP_VETO``.
    control_overlap: Optional[float] = None
    vetoed: bool = False


@dataclass
class SuccessionProposal:
    removed_id: str
    removed_name: Optional[str]
    candidates: List[SuccessionCandidate] = field(default_factory=list)

    @property
    def best(self) -> Optional[SuccessionCandidate]:
        return self.candidates[0] if self.candidates else None

    @property
    def bound_successor(self) -> Optional[str]:
        """The successor confident enough to propose, or None.

        None is a first-class answer: an unexplained removal is a real signal
        that something retired, and inventing a successor to make a churn check
        pass would defeat the point of the check.
        """
        top = self.best
        if top is None or top.score < AUTO_PROPOSE_THRESHOLD or top.ambiguous:
            return None
        if top.vetoed:
            return None
        return top.successor_id


def _jurisdiction_compatible(
    a: "FrameworkIdentity", b: "FrameworkIdentity"
) -> bool:
    """True when two identities could name the same jurisdiction."""
    return not a.jurisdiction or not b.jurisdiction or a.jurisdiction == b.jurisdiction


def _score_pair(
    removed: FrameworkIdentity,
    added: FrameworkIdentity,
    removed_name: Optional[str],
    added_name: Optional[str],
) -> Optional[SuccessionCandidate]:
    """Score one (removed, added) pair, or None when nothing links them."""
    if removed.region != added.region:
        return None

    sim = name_similarity(removed_name, added_name)
    tier: Optional[str] = None
    signals: List[str] = []

    if (
        removed.jurisdiction == added.jurisdiction
        and removed.body
        and removed.body == added.body
    ):
        tier = TIER_NAMESPACE
        signals.append("id_namespace")
    elif (
        removed.jurisdiction == added.jurisdiction
        and removed.stem
        and removed.stem == added.stem
    ):
        tier = TIER_EDITION
        signals.append("id_stem")
    elif (
        removed.jurisdiction == added.jurisdiction
        and removed.stem
        and _strip_version_tail(removed.stem.split("_"))
        == _strip_version_tail(added.stem.split("_"))
    ):
        tier = TIER_LOOSE
        signals.append("id_stem_loose")
    elif sim >= _TIER_NAME_FLOOR[TIER_NAME_ONLY] and _jurisdiction_compatible(
        removed, added
    ):
        # Jurisdiction is stripped out of the name signal (it is the id
        # signal's job), so when the id signal is silent the name alone cannot
        # tell Alaska's PIPA from Illinois's PIPA — their names are otherwise
        # identical. Require the jurisdictions to agree before trusting it.
        tier = TIER_NAME_ONLY

    if tier is None:
        return None
    if sim < _TIER_NAME_FLOOR[tier]:
        return None
    if sim > 0.0:
        signals.append("display_name")

    base = _TIER_BASE_SCORE[tier]
    # The name signal adjusts within the tier; it never promotes across tiers,
    # so a strong name match cannot manufacture namespace-grade confidence.
    score = base + (0.05 * sim) if tier in (TIER_NAMESPACE, TIER_EDITION) else base + (0.30 * sim)
    return SuccessionCandidate(
        successor_id=added.raw,
        successor_name=added_name,
        score=round(min(score, 1.0), 4),
        tier=tier,
        signals=signals,
    )


def match_framework_successions(
    removed: Dict[str, Optional[str]],
    added: Dict[str, Optional[str]],
    retained: Optional[Dict[str, Optional[str]]] = None,
    max_candidates: int = 3,
    focal_document_ids: Optional[Dict[str, Optional[str]]] = None,
    control_sets: Optional[Dict[str, set]] = None,
) -> Dict[str, SuccessionProposal]:
    """Propose successors for removed framework ids.

    ``removed``, ``added`` and ``retained`` map framework id -> display name
    (name may be None). Returns one proposal per removed id, including removals
    with no candidate at all — the caller needs the unexplained ones.

    ``retained`` (ids present in BOTH versions) belongs in the candidate pool
    because a removal is not always a rename into something new. SCF 2026.2
    carried one document under two ids — ``americas_canada_csag`` and
    ``americas_canada_osfi_self_assessment_guidance``, byte-identical display
    names — and 2026.3 dropped the duplicate. That is a de-duplication, and its
    successor is a *surviving* id, not an added one. A matcher that only ever
    looks at additions cannot express it, and reports a retirement instead.
    Candidates drawn from this pool carry the ``successor_retained`` signal so a
    reviewer can see they are a merge rather than a rename.

    ``focal_document_ids`` maps framework id -> the publisher's Focal Document
    Identifier, for every id on either side that has one. Where it is supplied
    the match is DECLARED rather than derived: the workbook itself says the two
    ids are the same document. Pre-2026.1 workbooks carry no such column, so
    the argument is optional and its absence silently falls back to the derived
    tiers.
    """
    retained = retained or {}
    # ``control_sets`` maps framework id -> the set of control ids mapping to
    # it, in ONE id space on both sides (the caller undoes control renumbering
    # first). It corroborates and vetoes; it never promotes. See
    # ``CONTROL_OVERLAP_VETO``.
    control_sets = control_sets or {}
    fdi_map = {k: v for k, v in (focal_document_ids or {}).items() if v}
    pool: Dict[str, Optional[str]] = dict(added)
    pool.update(retained)
    pool_ids = {k: parse_framework_id(k) for k in pool}
    proposals: Dict[str, SuccessionProposal] = {}

    for rid, rname in removed.items():
        r = parse_framework_id(rid)
        scored: List[SuccessionCandidate] = []
        for aid, a in pool_ids.items():
            if aid == rid:
                continue
            cand = _score_pair(r, a, rname, pool.get(aid))
            if cand is not None:
                if aid in retained and aid not in added:
                    cand.signals.append("successor_retained")
                scored.append(cand)

        # Declared pass. A pool id carrying the same FDI is the same document
        # by the publisher's own statement, so it outranks every derived tier
        # and replaces any derived candidate for that same successor rather
        # than competing with it. Failing that, an FDI that differs only in its
        # edition is a declared identity plus one inference, which is still a
        # stronger claim than anything the id stem can make.
        r_fdi = fdi_map.get(rid)
        if r_fdi:
            exact = [
                aid for aid in pool_ids
                if aid != rid and fdi_map.get(aid) == r_fdi
            ]
            stem = []
            if not exact:
                r_stem = focal_document_stem(r_fdi)
                stem = [
                    aid for aid in pool_ids
                    if aid != rid
                    and fdi_map.get(aid)
                    and focal_document_stem(fdi_map[aid]) == r_stem
                ]
            tier = TIER_DECLARED if exact else TIER_DECLARED_STEM
            hits = exact or stem
            for aid in hits:
                signals = ["focal_document_id" if exact else "focal_document_stem"]
                if aid in retained and aid not in added:
                    signals.append("successor_retained")
                scored = [c for c in scored if c.successor_id != aid]
                scored.append(
                    SuccessionCandidate(
                        successor_id=aid,
                        successor_name=pool.get(aid),
                        score=_TIER_BASE_SCORE[tier],
                        tier=tier,
                        signals=signals,
                        # More than one pool id claiming the same declared
                        # identity is a merge the publisher has not
                        # disambiguated; a reviewer decides, not this function.
                        ambiguous=len(hits) > 1,
                    )
                )

        # Corroborate every candidate with the control-set evidence, and
        # withhold a DERIVED binding the control sets contradict.
        r_controls = control_sets.get(rid)
        for cand in scored:
            a_controls = control_sets.get(cand.successor_id)
            if (
                r_controls is None
                or a_controls is None
                or len(r_controls) < CONTROL_OVERLAP_MIN_SET
                or len(a_controls) < CONTROL_OVERLAP_MIN_SET
            ):
                continue
            union = r_controls | a_controls
            cand.control_overlap = len(r_controls & a_controls) / len(union)
            if cand.control_overlap < CONTROL_OVERLAP_VETO:
                if cand.tier in (TIER_DECLARED, TIER_DECLARED_STEM):
                    # The publisher says these are one document. Report the
                    # disagreement, do not overrule it.
                    cand.signals.append("control_overlap_low")
                else:
                    cand.vetoed = True
                    cand.signals.append("vetoed_by_control_overlap")

        scored.sort(key=lambda c: (-c.score, c.successor_id))

        # A near-tie at the top is an ambiguity, not a winner — but only
        # between candidates of the SAME tier. A 1.00 namespace match is not
        # made doubtful by a 0.90 loose one: those are different claims, and
        # treating them as rivals blocked every correct cmmc/tx_ramp pairing on
        # the first run. The epsilon keeps 1.000 - 0.900 from reading as a tie
        # through float representation.
        if len(scored) >= 2:
            top = scored[0]
            rivals = [c for c in scored if c.tier == top.tier]
            if len(rivals) >= 2 and (top.score - rivals[1].score) < AMBIGUITY_MARGIN - 1e-9:
                for c in rivals:
                    if top.score - c.score < AMBIGUITY_MARGIN - 1e-9:
                        c.ambiguous = True

        proposals[rid] = SuccessionProposal(
            removed_id=rid,
            removed_name=rname,
            candidates=scored[:max_candidates],
        )
    return proposals


def explained_removals(
    proposals: Dict[str, SuccessionProposal],
) -> Tuple[List[str], List[str]]:
    """Split removals into (explained, unexplained) by bound successor."""
    explained = [k for k, p in proposals.items() if p.bound_successor]
    unexplained = [k for k, p in proposals.items() if not p.bound_successor]
    return sorted(explained), sorted(unexplained)
