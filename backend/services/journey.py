"""
Organisational journey — precondition evaluation and template import.

The platform ships the engine; a practitioner supplies the journey. This
module is the engine: it reads a stage's declarative `precondition_spec` and
answers, against data the organisation already holds, whether each check is
met.

What it deliberately does NOT do is advance a stage. Preconditions turn green.
A named person moves the stone. That separation is the whole point: the
mechanical half is worth automating, the judgement half is what a practitioner
is for, and collapsing the two would make the practitioner decoration.
"""
import json
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import and_, exists, false, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog_models import SCFCatalogControl, SCFCatalogDomain
from models import (
    AuditEngagement,
    ControlTeamAssignment,
    DocumentTransition,
    EvidenceFile,
    EvidenceTracking,
    GeneratedDocument,
    ImplementationStatus,
    JourneyStage,
    JourneyStageState,
    Organization,
    OrganizationFrameworkSelection,
    OrgJourney,
    RiskAssessment,
    ScopedControl,
    System,
    VendorActionItem,
)
from services.doc_gen.registry import GeneratorNotFound, get_generator
from services.obligation_basis import BASES, scoped_control_basis_expr
from services.materiality import material_expr, resolve_material
from services.risk_acceptance import without_current_approval
from services.capability_tests import TEST_TYPES as CAPABILITY_TEST_TYPES
from services.capability_tests import count_within_days as count_capability_tests
from services.deficiencies import count_open as count_open_deficiencies
from services.due_care import WITHIN_CADENCE, effective_cadence_expr, validation_state
from services.target_maturity import (
    at_target_clause,
    effective_target_expr,
    stored_domain_targets,
)

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "data" / "journey_templates"
DEFAULT_TEMPLATE_KEY = "compliancegenie-default"

#: A legal template key is a bare filename stem: letters, digits, dot, dash,
#: underscore, starting alphanumeric. No separator, no drive letter, no leading
#: dot — so nothing that could climb out of TEMPLATE_DIR survives the match.
#: This is early rejection, NOT the control. An allow-list is only as good as
#: the encodings it anticipated. What actually holds is that load_template()
#: never builds a path from this string at all — see its docstring.
TEMPLATE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

#: Checks this engine knows how to run. A template naming anything else gets an
#: honest "cannot evaluate" rather than a silent pass — an unknown check must
#: never read as a met one.
SUPPORTED_CHECKS = {
    "frameworks_scoped",
    "controls_with_decision",
    "controls_with_owner",
    "controls_at_status",
    "controls_at_risk_max",
    "documents_approved",
    "evidence_items_tracked",
    "evidence_files_uploaded",
    "evidence_upload_spread_days",
    "engagement_exists",
    "frameworks_with_basis",
    "controls_with_basis",
    "systems_with_criticality",
    "controls_with_target",
    "controls_at_target",
    "material_controls_at_status",
    "risks_accepted_without_approver_max",
    "open_deficiencies_max",
    "material_deficiencies_max",
    "controls_validated_within_cadence",
    "capability_tests_within_days",
}

#: Parameters each system check accepts. A system check counts systems, so a
#: control filter, a basis or anything else a template author adds by mistake
#: could only be ignored — and an ignored parameter is a gate that passes on
#: something other than what was written. Anything outside this list makes the
#: check unknown.
SYSTEM_CHECK_PARAMS = {
    "systems_with_criticality": {"type", "label", "min_fraction"},
}

#: Parameters each target-maturity check accepts (PIG R5). These count scoped
#: controls, so they take the control filters, but nothing else: a stray
#: parameter could only be ignored, and an ignored parameter is a gate that
#: passes on something other than what was written.
TARGET_CHECK_PARAMS = {
    "controls_with_target": {"type", "label", "min_fraction", "domains", "scf_ids"},
    "controls_at_target": {"type", "label", "min_fraction", "domains", "scf_ids"},
}

#: Parameters the material-controls check accepts (PIG R6). ``statuses`` is
#: required: a gate over "material controls at some status" with no status
#: named could only be read as every status, which is no gate at all.
MATERIAL_CHECK_PARAMS = {
    "material_controls_at_status": {"type", "label", "statuses", "min_fraction", "domains", "scf_ids"},
}

#: Parameters the risk-acceptance check accepts (PIG R7). It counts risks,
#: so a control filter could only be ignored.
RISK_CHECK_PARAMS = {
    "risks_accepted_without_approver_max": {"type", "label", "max_count"},
}

#: Parameters the deficiency checks accept (PIG R8). They count deficiencies,
#: which a control's domain can narrow; ``scf_ids`` and the control-count
#: parameters could only be ignored.
DEFICIENCY_CHECK_PARAMS = {
    "open_deficiencies_max": {"type", "label", "max_count", "domains"},
    "material_deficiencies_max": {"type", "label", "max_count", "domains"},
}

#: The longest review window a ``within_days`` can ask for (ten years). A
#: larger one is a typo, and past ~2,000 years the cutoff date overflows.
MAX_WITHIN_DAYS = 3660

#: Parameters the control-validation check accepts (PIG R10). It counts
#: scoped controls, so it takes the control filters and nothing else.
VALIDATION_CHECK_PARAMS = {
    "controls_validated_within_cadence": {"type", "label", "min_fraction", "domains", "scf_ids"},
}

#: Parameters the capability-test check accepts (PIG R11). It counts tests,
#: so a control filter could only be ignored. ``days`` is required: a window
#: left open would count a test from any year.
CAPABILITY_CHECK_PARAMS = {
    "capability_tests_within_days": {"type", "label", "days", "min_count", "test_types"},
}

#: The implementation statuses a check may name, read from the model's enum.
GATE_IMPLEMENTATION_STATUSES = frozenset(ImplementationStatus.values())

#: Checks that count scoped controls, and so can be narrowed to a slice of
#: them by ``domains`` or ``scf_ids``. Every other check counts something that
#: is not a control, so a control filter on it could only be ignored — and an
#: ignored filter is a gate quietly widened to the whole organisation.
CONTROL_FILTERED_CHECKS = {
    "controls_with_decision",
    "controls_with_owner",
    "controls_at_status",
    "controls_at_risk_max",
    "controls_with_basis",
    "controls_with_target",
    "controls_at_target",
    "material_controls_at_status",
    "controls_validated_within_cadence",
}

#: Checks that read the obligation basis (PIG R2). ``basis`` on any other
#: check would be ignored, so it is refused like a misplaced control filter.
BASIS_CHECKS = {"frameworks_with_basis", "controls_with_basis"}


def load_template(template_key: str = DEFAULT_TEMPLATE_KEY) -> Dict[str, Any]:
    """Read a journey template shipped with the platform.

    Templates are data files, not code. An operator adds their own by dropping
    a JSON file alongside these; nothing needs recompiling.

    `template_key` reaches here from a request body (api/journey.py, the import
    endpoint), so it is untrusted and must never be allowed to name a path.

    The load-bearing property is that NO PATH IS EVER BUILT FROM IT. The
    directory is enumerated, and the caller's string is only ever the right-hand
    operand of a string comparison against a stem we already own. Whatever the
    caller sends — `../../etc/passwd`, an absolute path, an exotic encoding —
    it cannot name a file, because it is never joined to anything. The set of
    openable files is exactly the set of files already sitting in TEMPLATE_DIR.

    That is a change of kind, not of degree, from the previous attempt. v0.40.1
    shipped a resolve()+is_relative_to() containment *check* on a path that was
    still constructed from the key. CodeQL does not model that check as a
    barrier, and it was right to be unimpressed: a check on a tainted path is
    weaker than never constructing one. The alert count went 2 -> 3 as the code
    grew. Do not "improve" this by reintroducing `TEMPLATE_DIR / f"{key}.json"`.

    Three layers, each with its own job:

      1. TEMPLATE_KEY_RE — a cheap early reject, and the only thing standing
         between a NUL byte and the filesystem layer. Not the control.
      2. Enumeration + exact stem equality — the control. This is what makes
         traversal impossible rather than merely detected.
      3. Containment of the ENUMERATED path. glob() follows symlinks, so a
         symlink planted inside TEMPLATE_DIR pointing outside it would
         otherwise be enumerated and opened; the string is blameless there and
         only the resolved path tells the truth. This layer never touches
         request data, which is exactly why it can coexist with (2).

    A rejected key raises FileNotFoundError, identically to a key that simply
    does not exist. The caller turns both into the same 404, so probing for
    files on the host cannot be distinguished from asking for a missing
    template.

    Note for anyone puzzled on a Mac: comparison is exact, so `Compliancegenie-Default`
    is refused even though a case-insensitive filesystem would once have opened it.
    Linux never accepted it; this makes dev agree with prod.
    """
    if not TEMPLATE_KEY_RE.match(template_key or ""):
        raise FileNotFoundError(f"No journey template named {template_key!r}")

    root = TEMPLATE_DIR.resolve()
    for candidate in TEMPLATE_DIR.glob("*.json"):
        # Deliberately a loop over a comparison, not a dict keyed by stem:
        # subscripting a container with an attacker-controlled key is a shape
        # taint analysis reasonably distrusts, and we gain nothing from it.
        if candidate.stem != template_key:
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            break
        with resolved.open(encoding="utf-8") as fh:
            return json.load(fh)

    raise FileNotFoundError(f"No journey template named {template_key!r}")


def unsupported_checks(template: Dict[str, Any]) -> set:
    """Check types in an uploaded artefact this engine cannot evaluate.

    The import refuses on a non-empty result. Accepting it would leave the
    practitioner with a gate that reads "cannot evaluate" for the length of an
    engagement, discovered by the client rather than by them.
    """
    found = set()
    for stage in template.get("stages", []) or []:
        for check in stage.get("precondition_spec") or []:
            ctype = check.get("type")
            if ctype not in SUPPORTED_CHECKS:
                found.add(str(ctype))
    return found


# ---------------------------------------------------------------------------
# Check parameters: scf_ids and generator
# ---------------------------------------------------------------------------
#
# A practitioner's playbook names its own slices. PIG step 1c is a handful of
# named controls, not a domain, and "the SoA is approved" is one document, not
# any document. Both filters live in the template, never in the catalogue —
# the platform stays a pure SCF workbook and the journey carries the overlay.
#
# The rule for both is the same as for an unknown check type: a parameter the
# engine cannot honour makes the check unknown, never a pass.


def _normalise_scf_id(value: str) -> str:
    """Catalogue form of a control id: trimmed and upper-cased."""
    return value.strip().upper()


def _parse_generator(value: str):
    """Split ``policy:GOV`` into ``("policy", "GOV")`` and ``soa`` into ``("soa", None)``."""
    name, sep, domain = value.strip().partition(":")
    name = name.strip().lower()
    domain = domain.strip().upper() if sep else None
    return name, domain


def check_param_problem(check: Dict[str, Any]) -> Optional[str]:
    """Why this check's ``scf_ids`` or ``generator`` cannot be honoured, or None.

    Structural only — nothing here reads the database, so the import can run
    it on every check before a single row is written. Whether a well-formed
    id or domain actually exists is answered against the catalogue by
    ``unknown_scf_ids`` and ``_domain_lookup``.
    """
    ctype = check.get("type")

    if ctype in SYSTEM_CHECK_PARAMS:
        extra = sorted(set(check) - SYSTEM_CHECK_PARAMS[ctype])
        if extra:
            return f"'{ctype}' counts systems and takes no {', '.join(extra)}"
        need = check.get("min_fraction", 1.0)
        if isinstance(need, bool) or not isinstance(need, (int, float)) or not 0 < need <= 1:
            return "min_fraction must be a number above 0 and at most 1"
        return None

    if ctype in RISK_CHECK_PARAMS:
        extra = sorted(set(check) - RISK_CHECK_PARAMS[ctype])
        if extra:
            return f"'{ctype}' counts risks and takes no {', '.join(extra)}"
        cap = check.get("max_count", 0)
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
            return "max_count must be a whole number, 0 or more"
        return None

    if ctype in CAPABILITY_CHECK_PARAMS:
        extra = sorted(set(check) - CAPABILITY_CHECK_PARAMS[ctype])
        if extra:
            return f"'{ctype}' counts capability tests and takes no {', '.join(extra)}"
        days = check.get("days")
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_WITHIN_DAYS:
            return f"days is required: a whole number of days, 1 to {MAX_WITHIN_DAYS}"
        need = check.get("min_count", 1)
        if isinstance(need, bool) or not isinstance(need, int) or need < 1:
            return "min_count must be a whole number, 1 or more"
        if "test_types" in check:
            raw = check.get("test_types")
            if not isinstance(raw, list) or not raw:
                return "test_types must be a non-empty list of capability test types"
            unknown = [v for v in raw if v not in CAPABILITY_TEST_TYPES]
            if unknown:
                return (
                    f"unknown capability test type(s) {', '.join(repr(v) for v in unknown)}; "
                    f"known: {', '.join(CAPABILITY_TEST_TYPES)}"
                )
        return None

    if ctype in DEFICIENCY_CHECK_PARAMS:
        extra = sorted(set(check) - DEFICIENCY_CHECK_PARAMS[ctype])
        if extra:
            return f"'{ctype}' counts deficiencies and takes no {', '.join(extra)}"
        cap = check.get("max_count", 0)
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
            return "max_count must be a whole number, 0 or more"
        if "domains" in check:
            raw = check.get("domains")
            if not isinstance(raw, list) or not raw:
                return "domains must be a non-empty list of SCF domain codes"
            if not all(isinstance(v, str) and v.strip() for v in raw):
                return "domains must contain only non-blank SCF domain codes"
        return None

    if ctype in MATERIAL_CHECK_PARAMS:
        # Extra keys, min_fraction and domains are checked with the target
        # checks' below.
        statuses = check.get("statuses")
        if not isinstance(statuses, list) or not statuses:
            return "statuses must be a non-empty list of implementation statuses"
        bad = [v for v in statuses if not isinstance(v, str) or v not in GATE_IMPLEMENTATION_STATUSES]
        if bad:
            return (
                "statuses must be implementation statuses ("
                + ", ".join(sorted(GATE_IMPLEMENTATION_STATUSES)) + "); not "
                + ", ".join(repr(v) for v in bad)
            )

    if ctype in TARGET_CHECK_PARAMS or ctype in MATERIAL_CHECK_PARAMS or ctype in VALIDATION_CHECK_PARAMS:
        allowed = (
            TARGET_CHECK_PARAMS.get(ctype)
            or MATERIAL_CHECK_PARAMS.get(ctype)
            or VALIDATION_CHECK_PARAMS[ctype]
        )
        extra = sorted(set(check) - allowed)
        if extra:
            return f"'{ctype}' takes no {', '.join(extra)}"
        need = check.get("min_fraction", 1.0)
        if isinstance(need, bool) or not isinstance(need, (int, float)) or not 0 < need <= 1:
            return "min_fraction must be a number above 0 and at most 1"
        if "domains" in check:
            raw = check.get("domains")
            if not isinstance(raw, list) or not raw:
                return "domains must be a non-empty list of SCF domain codes"
            if not all(isinstance(v, str) and v.strip() for v in raw):
                return "domains must contain only non-blank SCF domain codes"

    if "basis" in check or ctype == "frameworks_with_basis":
        raw = check.get("basis")
        if ctype not in BASIS_CHECKS:
            return f"'{ctype}' does not read an obligation basis, so it cannot be filtered by basis"
        if raw is None and ctype == "frameworks_with_basis":
            return "frameworks_with_basis needs a basis: 'mcr' or 'dsr'"
        if raw is not None and raw not in BASES:
            return "basis must be 'mcr' or 'dsr'"

    if "scf_ids" in check:
        raw = check.get("scf_ids")
        if ctype not in CONTROL_FILTERED_CHECKS:
            return f"'{ctype}' does not count controls, so it cannot be filtered by scf_ids"
        if not isinstance(raw, list) or not raw:
            return "scf_ids must be a non-empty list of SCF control ids"
        if not all(isinstance(v, str) and v.strip() for v in raw):
            return "scf_ids must contain only non-blank SCF control ids"

    if "within_days" in check:
        # PIG R10: "approved in the last N days" is a review cadence. Anything
        # but a positive whole number of days cannot be honoured.
        raw = check.get("within_days")
        if ctype != "documents_approved":
            return f"'{ctype}' does not count documents, so it takes no within_days"
        if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= MAX_WITHIN_DAYS:
            return f"within_days must be a whole number of days, 1 to {MAX_WITHIN_DAYS}"

    if "generator" in check:
        raw = check.get("generator")
        if ctype != "documents_approved":
            return f"'{ctype}' does not count documents, so it cannot be filtered by generator"
        if not isinstance(raw, str) or not raw.strip():
            return "generator must name a document generator, e.g. 'soa' or 'policy:GOV'"
        name, domain = _parse_generator(raw)
        try:
            spec = get_generator(name)
        except GeneratorNotFound:
            return f"no document generator named '{name}'"
        if domain == "":
            return f"'{raw.strip()}' has an empty domain after ':'"
        if domain is not None and not spec.domain_scoped:
            return f"'{name}' is not domain-scoped, so it takes no ':{domain}' suffix"

    return None


async def _named_in_domains(
    db: AsyncSession, ids: List[str], domains: Optional[List[str]]
) -> List[str]:
    """The named controls that also fall in the named domains, if any.

    The same attribution the domain filter uses — token to domain code, then
    the ``scf_id`` prefix — applied to the template's own list rather than to
    the scoped table, because the named list is the denominator. Domains that
    resolve to nothing leave nothing, never everything.
    """
    wanted = [d.strip() for d in (domains or []) if d and d.strip()]
    if not wanted:
        return ids
    lookup = await _domain_lookup(db)
    codes = {lookup[t.lower()].upper() for t in wanted if t.lower() in lookup}
    return [v for v in ids if v.split("-", 1)[0] in codes]


async def unknown_scf_ids(db: AsyncSession, scf_ids: List[str]) -> List[str]:
    """The ids in ``scf_ids`` that the SCF catalogue does not carry.

    Checked against the catalogue rather than the organisation's scope: a
    control the org has not scoped is a real control with nothing to count,
    which the gate reports honestly as a shortfall. A control the catalogue
    has never heard of is a typo, and a typo must not shrink the gate.
    """
    wanted = sorted({_normalise_scf_id(v) for v in scf_ids})
    if not wanted:
        return []
    known = set(
        (await db.execute(
            select(SCFCatalogControl.scf_id).where(SCFCatalogControl.scf_id.in_(wanted))
        )).scalars().all()
    )
    return [v for v in wanted if v not in known]


async def template_param_problems(db: AsyncSession, template: Dict[str, Any]) -> List[str]:
    """Every parameter in an uploaded artefact the engine cannot honour.

    The import refuses on a non-empty result, for the same reason it refuses
    an unknown check type: the practitioner should find a typo when they
    upload, not when a client asks why a gate never turns green.
    """
    problems: List[str] = []
    lookup: Optional[Dict[str, str]] = None
    for stage in template.get("stages", []) or []:
        key = stage.get("key") or "unnamed stage"
        for check in stage.get("precondition_spec") or []:
            where = f"{key}: {check.get('label') or check.get('type')}"
            problem = check_param_problem(check)
            if problem:
                problems.append(f"{where} — {problem}")
                continue
            if "scf_ids" in check:
                missing = await unknown_scf_ids(db, check["scf_ids"])
                if missing:
                    problems.append(
                        f"{where} — not in the SCF catalogue: {', '.join(missing)}"
                    )
            if "generator" in check:
                _, domain = _parse_generator(check["generator"])
                if domain is not None:
                    if lookup is None:
                        lookup = await _domain_lookup(db)
                    if domain.lower() not in lookup:
                        problems.append(f"{where} — no SCF domain '{domain}'")
            if (
                check.get("type") in TARGET_CHECK_PARAMS
                or check.get("type") in MATERIAL_CHECK_PARAMS
                or check.get("type") in DEFICIENCY_CHECK_PARAMS
                or check.get("type") in VALIDATION_CHECK_PARAMS
            ) and "domains" in check:
                # New checks refuse an unknown domain at upload. The older
                # control checks keep failing closed at evaluation instead, so
                # templates already imported are not suddenly refused.
                if lookup is None:
                    lookup = await _domain_lookup(db)
                bad = [d for d in check["domains"] if d.strip().lower() not in lookup]
                if bad:
                    problems.append(f"{where} — no SCF domain {', '.join(repr(d) for d in bad)}")
    return problems


def available_templates() -> List[Dict[str, str]]:
    """Every template on disk, for a picker."""
    out: List[Dict[str, str]] = []
    if not TEMPLATE_DIR.is_dir():
        return out
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as fh:
                doc = json.load(fh)
        except (json.JSONDecodeError, OSError):
            logger.warning("Skipping unreadable journey template %s", path.name)
            continue
        out.append({
            "template_key": doc.get("template_key", path.stem),
            "name": doc.get("name", path.stem),
            "description": doc.get("description", ""),
            "template_version": doc.get("template_version", ""),
            "stage_count": len(doc.get("stages", [])),
        })
    return out


# ---------------------------------------------------------------------------
# Precondition evaluation
# ---------------------------------------------------------------------------


async def _in_scope_counts(db: AsyncSession, org_id: UUID) -> Dict[str, int]:
    """Totals the status checks divide by, computed once per evaluation."""
    total = await db.scalar(
        select(func.count(ScopedControl.id)).where(
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
        )
    )
    return {"in_scope": int(total or 0)}


async def _domain_lookup(db: AsyncSession) -> Dict[str, str]:
    """Every spelling of a domain the catalogue will answer to, lower-cased.

    Three spellings reach the same domain, because a template author who wrote
    out a name instead of a code has not made a mistake worth failing a gate
    over:

    * the code itself, ``GOV``;
    * the domains sheet's name, ``Cybersecurity & Data Protection Governance``;
    * the *controls* sheet's name, ``Security, Compliance & Resilience
      Governance`` — which is what a generated report prints, so it is what
      gets copied.

    The domains sheet is loaded first and the controls sheet only fills gaps,
    so a name the domains sheet already claims can never be reassigned by a
    stray control row. Ordering by prefix keeps the result of a genuinely
    ambiguous controls-sheet name deterministic rather than dependent on scan
    order; there is no such name in the shipped catalogue, and if one appears
    a deterministic answer is far easier to diagnose than a shifting one.
    """
    lookup: Dict[str, str] = {}
    rows = await db.execute(
        select(SCFCatalogDomain.identifier, SCFCatalogDomain.name)
    )
    for identifier, name in rows:
        if identifier:
            lookup[identifier.strip().lower()] = identifier
        if name and name.strip():
            lookup[name.strip().lower()] = identifier

    prefix = func.split_part(SCFCatalogControl.scf_id, "-", 1)
    rows = await db.execute(
        select(SCFCatalogControl.scf_domain, prefix).distinct().order_by(prefix)
    )
    for scf_domain, code in rows:
        if scf_domain and scf_domain.strip() and code:
            lookup.setdefault(scf_domain.strip().lower(), code)
    return lookup


def _prefix_predicate(column, codes: List[str]):
    """Controls belonging to any of ``codes``, keyed on the ``scf_id`` prefix.

    The prefix is the SCF's own construction rule and the only total, sound
    attribution in this schema: every catalogue control is ``XXX-…`` and every
    prefix names a live domain. The alternative — matching the controls
    sheet's ``scf_domain`` against the domains sheet's ``name`` — is the bug
    this replaces. That join carries both of the catalogue's disagreements:
    the whole of GOV, whose 38 controls are named differently in the two
    sheets, and ``CHG-08``, one control whose ``scf_domain`` cell says
    Embedded Technology while every one of its siblings, and its own subject
    matter, say Change Management. The prefix carries neither.

    **Named consequence, decided rather than stumbled into:** this moves
    ``CHG-08`` out of Embedded Technology and into Change Management. That is
    a deliberate behaviour change beyond the GOV fix. The prefix is treated as
    authoritative because it is provably total — every one of the catalogue's
    controls is ``XXX-…`` and every prefix names a live domain, asserted in
    the test suite so it fails loudly if that ever stops being true — while
    ``scf_domain`` is a display string from a different workbook sheet that
    already carries both known divergences. A domain renamed upstream cannot
    break a prefix; it broke the name join outright.

    Shared with the tests on purpose, so what they assert about the catalogue
    is the predicate the gate actually runs rather than a restatement of it.

    ``istartswith`` with ``autoescape`` rather than string interpolation: an
    identifier containing ``_`` or ``%`` would otherwise be a wildcard, and a
    prefix LIKE stays index-usable where ``split_part`` would not.
    """
    return or_(*[
        column.istartswith(f"{code}-", autoescape=True) for code in sorted(codes)
    ])


async def _domain_clause(
    db: AsyncSession, domains: Optional[List[str]], label: str = "unnamed check", column=None,
):
    """Restrict a scoped-control query to a set of SCF domains, or not at all.

    ``column`` is the ``scf_id`` column to attribute by: the scoped control's
    unless another is given (a deficiency keeps its control's own).

    A practitioner's wave gate reads "GOV, RSK and CPL are done", so the
    filter speaks in domain codes. Resolution is one move — token to domain
    identifier, in any of its three spellings — and attribution is then by
    ``scf_id`` prefix.

    Returns None when no domains were named — an unfiltered check keeps its
    org-wide meaning, so templates written before domain scoping evaluate
    exactly as they did.

    **A named domain that resolves to nothing returns ``false()``, never
    None.** None would make this function a no-op and silently widen the check
    to the entire organisation, so a typo in a template would read as a green
    gate over data nobody asked about — a false pass, which is worse than any
    wrong number. Failing closed makes the gate unmeetable instead, and the
    warning says why; the ``in_scope == 0`` guard in each branch then reports
    "No controls scoped in …", which is the truth about what was asked for.

    A named domain with zero scoped controls needs no special case: it
    resolves, contributes its predicate, matches nothing, and invents nothing.
    """
    if not domains:
        return None
    wanted = [d.strip() for d in domains if d and d.strip()]
    if not wanted:
        return None

    lookup = await _domain_lookup(db)
    codes: List[str] = []
    unresolved: List[str] = []
    for token in wanted:
        code = lookup.get(token.lower())
        if code is None:
            unresolved.append(token)
        elif code not in codes:
            codes.append(code)

    if unresolved:
        # Loud on purpose. A domain quietly dropped from a gate is how an
        # entire slice of the catalogue went missing from both halves of a
        # fraction without anybody noticing.
        logger.warning(
            "Journey check %r names domain(s) this catalogue cannot resolve: "
            "%s. The check is failed closed rather than widened.",
            label,
            ", ".join(sorted(unresolved)),
        )
    if not codes:
        return false()
    return _prefix_predicate(ScopedControl.scf_id if column is None else column, codes)


async def _denominator(db: AsyncSession, org_id: UUID, clause, totals: Dict[str, int]) -> int:
    """How many in-scope controls this check divides by.

    An unfiltered check reuses the total computed once per evaluation; a
    domain-filtered one has to count its own, because the denominator moves
    with the filter. Counting the numerator against an org-wide denominator
    would make every wave gate read as a few percent complete, forever.
    """
    if clause is None:
        return totals["in_scope"]
    got = await db.scalar(
        select(func.count(ScopedControl.id)).where(
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            clause,
        )
    )
    return int(got or 0)


def _accountable_team_clause():
    """A control is owned when a team is accountable for it (#1052).

    EXISTS, not a join. One control can carry several team rows — one
    accountable, the rest consulted — and a join would count that control once
    per row, letting the numerator exceed the denominator. The semi-join stops
    at the first match, so one control is one control.

    ``is_accountable`` is the whole test. A consulted team is informed, not
    responsible: ``services/owner_resolution.py`` keeps consulted teams off its
    notification tier for that reason, and an ownership gate has to agree with
    the chain that decides who actually gets paged — otherwise a stage reads
    green while nobody is answerable for it. ``uq_control_accountable_team``
    makes at most one row per control satisfy this, so the count is of
    controls, not of relationships.

    A team's internal staffing — primary, delegate, member — is deliberately
    not consulted. That is a property of how a team is manned, not of who owns
    the control, and folding it in would make ownership blink out whenever
    somebody went on leave.

    The organisation predicate is repeated inside the subquery for the reason
    ``services.team_assignments`` gives: defence in depth, and it lets the
    planner use the assignment table's organisation index.
    """
    return (
        select(literal(1))
        .select_from(ControlTeamAssignment)
        .where(
            ControlTeamAssignment.scoped_control_id == ScopedControl.id,
            ControlTeamAssignment.organization_id == ScopedControl.organization_id,
            ControlTeamAssignment.is_accountable.is_(True),
        )
        .exists()
    )


def _required_count(total: int, need: float) -> int:
    """The smallest numerator that satisfies this check's own gate.

    Defined in terms of the gate expression ``k / total >= need`` rather than
    ``ceil(total * need)``, because those two disagree under float rounding —
    25 controls at 0.28 gives 8 by ceil, but 7/25 already passes. A label that
    demanded a control the gate does not want would be the same defect this
    number exists to remove, moved one line across.

    The two corrections run at most once each and make the printed
    requirement true by construction: whatever ``frac >= need`` decides, this
    is the count it decided it on.
    """
    if total <= 0:
        return 0
    k = max(0, min(total, math.ceil(total * need)))
    while k > 0 and (k - 1) / total >= need:
        k -= 1
    while k < total and k / total < need:
        k += 1
    return k


async def _evaluate_one(
    db: AsyncSession,
    org_id: UUID,
    check: Dict[str, Any],
    totals: Dict[str, int],
) -> Dict[str, Any]:
    """Run one check and describe the outcome in the same shape every time."""
    ctype = check.get("type")
    label = check.get("label") or ctype or "unnamed check"

    def result(met: Optional[bool], detail: str) -> Dict[str, Any]:
        return {"type": ctype, "label": label, "met": met, "detail": detail}

    if ctype not in SUPPORTED_CHECKS:
        # Unknown means unknown. Never a pass.
        return result(None, f"This deployment cannot evaluate a '{ctype}' check")

    problem = check_param_problem(check)
    if problem:
        # A filter the engine cannot honour is the same as a check it cannot
        # run: unknown, never a pass over a wider slice than was asked for.
        return result(None, f"Cannot evaluate: {problem}")

    # A wave gate names its domains; an org-wide check names none. Everything
    # below divides by `in_scope`, which is now the count for whatever slice
    # this check is about.
    domain_clause = await _domain_clause(db, check.get("domains"), label)
    scope_label = ""
    if domain_clause is not None:
        named = ", ".join(str(d) for d in check.get("domains") or [])
        scope_label = f" in {named}"

    # A step gate names its controls. Intersected with any domain filter, so
    # a template naming both gets the controls that satisfy both.
    if "scf_ids" in check:
        missing = await unknown_scf_ids(db, check["scf_ids"])
        if missing:
            return result(
                None,
                "Cannot evaluate: not in the SCF catalogue: " + ", ".join(missing),
            )
        named = await _named_in_domains(
            db, sorted({_normalise_scf_id(v) for v in check["scf_ids"]}), check.get("domains")
        )
        # The denominator is the named controls, not the scoped ones. A named
        # control nobody has scoped is a shortfall, not an absence: dividing
        # by what happens to be scoped would let a gate naming three controls
        # pass on the one that is done. The only way out of the count is a
        # recorded exclusion, which carries a reason and its own audit trail.
        excluded = set((await db.execute(
            select(ScopedControl.scf_id).where(
                ScopedControl.organization_id == org_id,
                ScopedControl.scf_id.in_(named),
                ScopedControl.scope_override == "exclude",
            )
        )).scalars().all()) if named else set()
        counted = [v for v in named if v not in excluded]
        ids_clause = ScopedControl.scf_id.in_(counted)
        domain_clause = ids_clause if domain_clause is None else and_(domain_clause, ids_clause)
        scope_label += f" across {len(named)} named control{'s' if len(named) != 1 else ''}"
        if excluded:
            scope_label += f" ({len(excluded)} excluded)"
        in_scope = len(counted)
    else:
        in_scope = await _denominator(db, org_id, domain_clause, totals)

    if ctype == "frameworks_scoped":
        need = int(check.get("min_count", 1))
        got = await db.scalar(
            select(func.count(OrganizationFrameworkSelection.id)).where(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active.is_(True),
            )
        )
        got = int(got or 0)
        return result(got >= need, f"{got} framework(s) scoped (need {need})")

    if ctype == "frameworks_with_basis":
        basis = check["basis"]
        need = int(check.get("min_count", 1))
        got = await db.scalar(
            select(func.count(OrganizationFrameworkSelection.id)).where(
                OrganizationFrameworkSelection.organization_id == org_id,
                OrganizationFrameworkSelection.active.is_(True),
                OrganizationFrameworkSelection.basis == basis,
            )
        )
        got = int(got or 0)
        return result(got >= need, f"{got} {basis.upper()} framework(s) scoped (need {need})")

    if ctype == "controls_with_basis":
        if in_scope == 0:
            return result(False, f"No controls scoped{scope_label or ' yet'}")
        basis = check.get("basis")
        expr = scoped_control_basis_expr()
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            expr.isnot(None) if basis is None else expr == basis,
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        classified = int(await db.scalar(select(func.count(ScopedControl.id)).where(*conds)) or 0)
        frac = classified / in_scope
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(in_scope, need)
        short = "" if classified >= required else f", {required - classified} more needed"
        what = f"{basis.upper()} basis" if basis else "an obligation basis"
        return result(
            frac >= need,
            f"{classified} of {in_scope}{scope_label} with {what} ({frac:.1%}) — {required} required{short}",
        )

    if ctype in ("controls_with_target", "controls_at_target"):
        if in_scope == 0:
            return result(False, f"No controls scoped{scope_label or ' yet'}")
        settings = await db.scalar(select(Organization.settings).where(Organization.id == org_id))
        targets = stored_domain_targets(settings)
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            effective_target_expr(targets).isnot(None)
            if ctype == "controls_with_target" else at_target_clause(targets),
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        got = int(await db.scalar(select(func.count(ScopedControl.id)).where(*conds)) or 0)
        frac = got / in_scope
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(in_scope, need)
        short = "" if got >= required else f", {required - got} more needed"
        # A control with no target is never at target: the at-target count
        # divides by every in-scope control, so missing targets hold it down.
        what = "with a target maturity" if ctype == "controls_with_target" else "at target maturity"
        return result(
            frac >= need,
            f"{got} of {in_scope}{scope_label} {what} ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "controls_validated_within_cadence":
        if in_scope == 0:
            return result(False, f"No controls scoped{scope_label or ' yet'}")
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        rows = (await db.execute(
            select(effective_cadence_expr().label("cadence"), ScopedControl.last_validated_at)
            .where(*conds)
        )).all()
        # Never validated, no known cadence and overdue all count against: a
        # control is within its cadence only when a validation is on record
        # and the next one is not yet past due. A named control nobody has
        # scoped has no row, so it counts against too.
        today = date.today()
        got = sum(
            1 for r in rows
            if validation_state(r.last_validated_at, r.cadence, today) in WITHIN_CADENCE
        )
        frac = got / in_scope
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(in_scope, need)
        short = "" if got >= required else f", {required - got} more needed"
        return result(
            frac >= need,
            f"{got} of {in_scope}{scope_label} validated within their cadence ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "material_controls_at_status":
        statuses = list(check["statuses"])
        if "scf_ids" in check:
            # The named, unexcluded controls that are material: a named
            # material control nobody has scoped still counts against the gate,
            # as every named control does (see the scf_ids denominator above).
            named = sorted(set(counted))
            overrides = dict((await db.execute(
                select(ScopedControl.scf_id, ScopedControl.material_override).where(
                    ScopedControl.organization_id == org_id,
                    ScopedControl.scf_id.in_(named),
                )
            )).all()) if named else {}
            weights = dict((await db.execute(
                select(SCFCatalogControl.scf_id, SCFCatalogControl.control_weighting).where(
                    SCFCatalogControl.scf_id.in_(named)
                )
            )).all()) if named else {}
            material_ids = [
                v for v in named if resolve_material(overrides.get(v), weights.get(v))[0]
            ]
            total = len(material_ids)
            base = [ScopedControl.scf_id.in_(material_ids)]
        else:
            base = [ScopedControl.selected.is_(True), material_expr()]
            if domain_clause is not None:
                base.append(domain_clause)
            total = int(await db.scalar(
                select(func.count(ScopedControl.id)).where(
                    ScopedControl.organization_id == org_id, *base
                )
            ) or 0)
        if total == 0:
            return result(False, f"No material controls in scope{scope_label}")
        got = int(await db.scalar(
            select(func.count(ScopedControl.id)).where(
                ScopedControl.organization_id == org_id,
                ScopedControl.selected.is_(True),
                ScopedControl.implementation_status.in_(statuses),
                *base,
            )
        ) or 0)
        frac = got / total
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(total, need)
        short = "" if got >= required else f", {required - got} more needed"
        return result(
            frac >= need,
            f"{got} of {total} material control{'s' if total != 1 else ''}{scope_label} "
            f"at {' or '.join(statuses)} ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "controls_with_decision":
        if in_scope == 0:
            return result(False, f"No controls scoped{scope_label or ' yet'}")
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            ScopedControl.implementation_status.isnot(None),
            ScopedControl.implementation_status != "",
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        decided = int(await db.scalar(select(func.count(ScopedControl.id)).where(*conds)) or 0)
        frac = decided / in_scope
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(in_scope, need)
        short = "" if decided >= required else f", {required - decided} more needed"
        return result(
            frac >= need,
            f"{decided} of {in_scope}{scope_label} ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "controls_with_owner":
        if in_scope == 0:
            return result(False, f"No controls scoped{scope_label or ' yet'}")
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            _accountable_team_clause(),
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        owned = int(await db.scalar(select(func.count(ScopedControl.id)).where(*conds)) or 0)
        frac = owned / in_scope
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(in_scope, need)
        short = "" if owned >= required else f", {required - owned} more needed"
        return result(
            frac >= need,
            f"{owned} of {in_scope}{scope_label} ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "controls_at_status":
        if in_scope == 0:
            return result(False, f"No controls scoped{scope_label or ' yet'}")
        statuses = check.get("statuses") or []
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            ScopedControl.implementation_status.in_(statuses),
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        at = int(await db.scalar(select(func.count(ScopedControl.id)).where(*conds)) or 0)
        frac = at / in_scope
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(in_scope, need)
        short = "" if at >= required else f", {required - at} more needed"
        return result(
            frac >= need,
            f"{at} of {in_scope}{scope_label} ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "controls_at_risk_max":
        conds = [
            ScopedControl.organization_id == org_id,
            ScopedControl.selected.is_(True),
            ScopedControl.implementation_status == "at_risk",
        ]
        if domain_clause is not None:
            conds.append(domain_clause)
        at_risk = int(await db.scalar(select(func.count(ScopedControl.id)).where(*conds)) or 0)
        cap = int(check.get("max_count", 0))
        return result(at_risk <= cap, f"{at_risk} flagged at risk{scope_label} (limit {cap})")

    if ctype == "documents_approved":
        conds = [
            GeneratedDocument.organization_id == org_id,
            GeneratedDocument.lifecycle_status.in_(["approved", "published"]),
        ]
        which = ""
        if "generator" in check:
            # "The SoA is approved" is one document, not any document — an
            # approved risk report must not satisfy a gate about the SoA.
            name, domain = _parse_generator(check["generator"])
            conds.append(GeneratedDocument.generator_name == name)
            which = f" {name}"
            if domain is not None:
                code = (await _domain_lookup(db)).get(domain.lower())
                if code is None:
                    return result(None, f"Cannot evaluate: no SCF domain '{domain}'")
                conds.append(GeneratedDocument.domain_id == code.upper())
                which = f" {name} ({code})"
        within = ""
        if "within_days" in check:
            # Approved recently, not merely approved once: the document must
            # still be approved and its approval recorded inside the window.
            # An edit after approval sends it back to review, so a stale
            # sign-off cannot carry a changed document through.
            days = int(check["within_days"])
            cutoff = datetime.utcnow() - timedelta(days=days)
            conds.append(exists().where(
                DocumentTransition.document_id == GeneratedDocument.id,
                DocumentTransition.to_status == "approved",
                DocumentTransition.created_at >= cutoff,
            ))
            within = f" in the last {days} day{'s' if days != 1 else ''}"
        approved = await db.scalar(select(func.count(GeneratedDocument.id)).where(*conds))
        approved = int(approved or 0)
        need = int(check.get("min_count", 1))
        return result(approved >= need, f"{approved}{which} approved{within} (need {need})")

    if ctype == "evidence_items_tracked":
        tracked = await db.scalar(
            select(func.count(EvidenceTracking.id)).where(
                EvidenceTracking.organization_id == org_id,
                EvidenceTracking.is_tracked.is_(True),
            )
        )
        tracked = int(tracked or 0)
        need = int(check.get("min_count", 1))
        return result(tracked >= need, f"{tracked} tracked (need {need})")

    if ctype == "evidence_files_uploaded":
        files = await db.scalar(
            select(func.count(EvidenceFile.id)).where(
                EvidenceFile.organization_id == org_id,
                EvidenceFile.is_deleted.is_(False),
            )
        )
        files = int(files or 0)
        need = int(check.get("min_count", 1))
        return result(files >= need, f"{files} uploaded (need {need})")

    if ctype == "evidence_upload_spread_days":
        # An auditor's first inflation signal is evidence that all arrived in
        # the same week. This check makes that visible before they see it.
        row = (
            await db.execute(
                select(
                    func.min(EvidenceFile.uploaded_at),
                    func.max(EvidenceFile.uploaded_at),
                    func.count(EvidenceFile.id),
                ).where(
                    EvidenceFile.organization_id == org_id,
                    EvidenceFile.is_deleted.is_(False),
                )
            )
        ).one()
        earliest, latest, count = row
        need = int(check.get("min_days", 30))
        if not count or earliest is None or latest is None:
            return result(False, "No evidence uploaded yet")
        spread = (latest - earliest).days
        return result(spread >= need, f"{spread} days between first and last upload (need {need})")

    if ctype == "risks_accepted_without_approver_max":
        # An accepted risk nobody answers for: accepted through an ordinary
        # edit rather than the acceptance route, its approver since deleted,
        # or its approval expired. An expired approval is no approval.
        unapproved = int(await db.scalar(
            select(func.count(RiskAssessment.id)).where(
                RiskAssessment.organization_id == org_id,
                without_current_approval(date.today()),
            )
        ) or 0)
        cap = int(check.get("max_count", 0))
        return result(
            unapproved <= cap,
            f"{unapproved} accepted risks without a current approval (limit {cap})",
        )

    if ctype in ("open_deficiencies_max", "material_deficiencies_max"):
        # A cap passes on zero, so a domain that resolves to nothing would
        # pass by matching nothing. Unknown, never a pass.
        if "domains" in check:
            lookup = await _domain_lookup(db)
            unresolved = [d for d in check["domains"] if d.strip().lower() not in lookup]
            if unresolved:
                return result(
                    None,
                    "Cannot evaluate: no SCF domain " + ", ".join(repr(d) for d in unresolved),
                )
        material = ctype == "material_deficiencies_max"
        # By the scf_id the deficiency keeps, so deleting its control does
        # not take it out of its domain's count.
        scf_clause = await _domain_clause(
            db, check.get("domains"), label, column=VendorActionItem.scf_id,
        )
        found = await count_open_deficiencies(db, org_id, scf_clause, material_only=material)
        cap = int(check.get("max_count", 0))
        what = "material deficiencies" if material else "deficiencies"
        return result(found <= cap, f"{found} open {what}{scope_label} (limit {cap})")

    if ctype == "capability_tests_within_days":
        # Whatever the result: a failed test still shows the capability was
        # tested, and what it found is tracked as deficiencies.
        days = int(check["days"])
        need = int(check.get("min_count", 1))
        types = check.get("test_types")
        found = await count_capability_tests(db, org_id, days, types)
        what = f" ({', '.join(types)})" if types else ""
        return result(
            found >= need,
            f"{found} capability test(s){what} in the last {days} days — {need} required",
        )

    if ctype == "systems_with_criticality":
        # Two rules, the same ones the named-control gate uses. The boundary
        # must have been drawn: with no system marked in scope there is nothing
        # to rate, and an empty set must not pass. And the only way out of the
        # count is a recorded decision: a system nobody has placed in or out of
        # scope may well be inside, so it is a shortfall until someone rates
        # it or marks it out — otherwise one rated system among forty
        # undecided ones would read as a complete boundary.
        org_systems = System.organization_id == org_id
        marked_in = int(await db.scalar(
            select(func.count(System.id)).where(org_systems, System.in_scope.is_(True))
        ) or 0)
        if marked_in == 0:
            return result(False, "No systems marked in scope yet")
        counted = [org_systems, or_(System.in_scope.is_(True), System.in_scope.is_(None))]
        total = int(await db.scalar(select(func.count(System.id)).where(*counted)) or 0)
        rated = int(await db.scalar(
            select(func.count(System.id)).where(*counted, System.criticality.isnot(None))
        ) or 0)
        undecided = total - marked_in
        frac = rated / total
        need = float(check.get("min_fraction", 1.0))
        required = _required_count(total, need)
        short = "" if rated >= required else f", {required - rated} more needed"
        what = "in-scope systems" if not undecided else f"systems in scope or undecided ({undecided} undecided)"
        return result(
            frac >= need,
            f"{rated} of {total} {what} with a criticality ({frac:.1%}) — {required} required{short}",
        )

    if ctype == "engagement_exists":
        count = await db.scalar(
            select(func.count(AuditEngagement.id)).where(
                AuditEngagement.organization_id == org_id,
            )
        )
        count = int(count or 0)
        return result(count > 0, f"{count} engagement(s)")

    # Unreachable while SUPPORTED_CHECKS and the branches above agree. If they
    # ever drift, fail closed rather than silently pass.
    return result(None, f"No evaluator wired for '{ctype}'")


async def evaluate_stage_preconditions(
    db: AsyncSession,
    org_id: UUID,
    stage: JourneyStage,
    totals: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Evaluate every precondition on one stage.

    Returns the individual results plus a roll-up. `all_met` is True only when
    every check ran AND passed — an unevaluable check keeps it False, because
    "we could not tell" is not "yes".
    """
    spec = stage.precondition_spec or []
    if totals is None:
        totals = await _in_scope_counts(db, org_id)

    checks = [await _evaluate_one(db, org_id, c, totals) for c in spec]
    met = sum(1 for c in checks if c["met"] is True)
    unknown = sum(1 for c in checks if c["met"] is None)
    return {
        "checks": checks,
        "met_count": met,
        "total_count": len(checks),
        "unknown_count": unknown,
        "all_met": len(checks) > 0 and met == len(checks),
    }


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


async def import_template(
    db: AsyncSession,
    org_id: UUID,
    template: Dict[str, Any],
    practitioner_name: Optional[str] = None,
    practitioner_organization_id: Optional[UUID] = None,
    activate: bool = False,
) -> OrgJourney:
    """Create this organisation's journey from a template, or re-issue it.

    Re-issuing MERGES by stage ``key``. A practitioner revises their playbook
    mid-engagement — that is the whole point of the artefact being uploadable —
    and a revision must never cost the client their signatures. An attestation
    is the audit trail: a named person, a date, a note, sometimes a conditional
    pass with a deadline. Replacing the row wholesale would delete exactly the
    evidence the database check constraint exists to protect.

    So: a stage whose key survives the revision keeps its state, its signature
    and its timestamps, and takes the new copy and the new preconditions. A key
    that disappears from the template is removed. A new key arrives locked.
    Re-ordering follows the template.

    The one thing a revision cannot do is un-sign something. If a stage was
    passed under v1 and v1.1 rewrites what that stage asks for, the signature
    stands and the new wording sits beneath it — because the person signed a
    moment in time, not a paragraph.
    """
    existing = (
        await db.execute(
            select(OrgJourney)
            .options(selectinload(OrgJourney.stages))
            .where(OrgJourney.organization_id == org_id)
        )
    ).scalar_one_or_none()

    raw_stages = list(template.get("stages", []))
    keys = [raw.get("key") or f"stage-{i}" for i, raw in enumerate(raw_stages)]

    if existing is None:
        journey = OrgJourney(
            organization_id=org_id,
            name=template.get("name", "Compliance journey"),
            description=template.get("description"),
            template_key=template.get("template_key"),
            template_version=template.get("template_version"),
            practitioner_name=practitioner_name,
            practitioner_organization_id=practitioner_organization_id,
            activated_at=datetime.now(timezone.utc) if activate else None,
        )
        db.add(journey)
        await db.flush()
        prior: Dict[str, JourneyStage] = {}
    else:
        journey = existing
        journey.name = template.get("name", journey.name)
        journey.description = template.get("description")
        journey.template_key = template.get("template_key")
        journey.template_version = template.get("template_version")
        if practitioner_name is not None:
            journey.practitioner_name = practitioner_name
        if practitioner_organization_id is not None:
            journey.practitioner_organization_id = practitioner_organization_id
        if activate and journey.activated_at is None:
            journey.activated_at = datetime.now(timezone.utc)
        prior = {st.key: st for st in journey.stages}

        # Stages the revision dropped. A signed stage is never silently
        # discarded — removing it would rewrite history — so this refuses
        # rather than guessing.
        dropped = [st for key, st in prior.items() if key not in keys]
        signed_and_dropped = [
            st.key for st in dropped
            if st.state in (JourneyStageState.PASSED.value,
                            JourneyStageState.PASSED_CONDITIONAL.value)
        ]
        if signed_and_dropped:
            raise ValueError(
                "This revision drops stages that have already been attested: "
                + ", ".join(sorted(signed_and_dropped))
                + ". Keep their keys in the template, or start a new journey."
            )
        for st in dropped:
            await db.delete(st)
        # Ordinals are unique per journey, so clear the old ones before the
        # new ordering is written or the merge collides with itself.
        for st in prior.values():
            st.ordinal = -(st.ordinal + 1)
        await db.flush()

    now = datetime.now(timezone.utc)
    for index, raw in enumerate(raw_stages):
        key = keys[index]
        stage = prior.get(key)
        if stage is None:
            stage = JourneyStage(journey_id=journey.id, key=key,
                                 state=JourneyStageState.LOCKED.value)
            db.add(stage)

        stage.ordinal = index
        stage.title = raw.get("title") or f"Stage {index + 1}"
        stage.summary = raw.get("summary")
        stage.expect_next = raw.get("expect_next")
        stage.precondition_spec = raw.get("precondition_spec") or []

        # The first stage of a newly activated journey is where the org stands
        # today. On an unactivated journey every stone stays dark: the
        # organisation sees the road, nobody is walking it yet. An already
        # running stage is left exactly as it was.
        if (
            activate
            and index == 0
            and stage.state == JourneyStageState.LOCKED.value
            and stage.attested_at is None
        ):
            stage.state = JourneyStageState.ACTIVE.value
            stage.started_at = now

    await db.flush()
    await db.refresh(journey)
    return journey
