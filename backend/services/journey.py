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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import false, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from catalog_models import SCFCatalogControl, SCFCatalogDomain
from models import (
    AuditEngagement,
    ControlTeamAssignment,
    EvidenceFile,
    EvidenceTracking,
    GeneratedDocument,
    JourneyStage,
    JourneyStageState,
    OrganizationFrameworkSelection,
    OrgJourney,
    ScopedControl,
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
}


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
    db: AsyncSession, domains: Optional[List[str]], label: str = "unnamed check"
):
    """Restrict a scoped-control query to a set of SCF domains, or not at all.

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
    return _prefix_predicate(ScopedControl.scf_id, codes)


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

    # A wave gate names its domains; an org-wide check names none. Everything
    # below divides by `in_scope`, which is now the count for whatever slice
    # this check is about.
    domain_clause = await _domain_clause(db, check.get("domains"), label)
    scope_label = ""
    if domain_clause is not None:
        named = ", ".join(str(d) for d in check.get("domains") or [])
        scope_label = f" in {named}"
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
        approved = await db.scalar(
            select(func.count(GeneratedDocument.id)).where(
                GeneratedDocument.organization_id == org_id,
                GeneratedDocument.lifecycle_status.in_(["approved", "published"]),
            )
        )
        approved = int(approved or 0)
        need = int(check.get("min_count", 1))
        return result(approved >= need, f"{approved} approved (need {need})")

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
