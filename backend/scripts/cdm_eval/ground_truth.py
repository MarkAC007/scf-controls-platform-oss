"""Ground-truth expectations for CDM evaluation runs.

The evaluation harness needs a small, committed answer key so retrieval changes
can be measured against stable expectations instead of anecdotes from a local
run. The values here are deliberately generic policy-type names, not client
document identifiers. The real corpus filenames live only in gitignored
``backend/fixtures-local/`` data; this file names the kind of document expected
for a domain without checking in any client-specific filename.

An abstain entry is as important as a covered entry. The first metric asks
whether the top mapped document is right when a policy type is present; the
second asks whether the system stays silent when this corpus has no suitable
document. Unknown domains fail loudly because silently accepting fixture drift
would make every harness number untrustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Covered:
    expected_substring: str


@dataclass(frozen=True)
class Abstain:
    """Domain intentionally expected to produce no document mapping."""


Expectation = Covered | Abstain

OUTCOME_CORRECT = "correct"
OUTCOME_WRONG = "wrong"
OUTCOME_UNEXPECTED_ABSTAIN = "unexpected_abstain"
OUTCOME_CORRECT_ABSTAIN = "correct_abstain"
OUTCOME_MISSED_ABSTAIN = "missed_abstain"
OUTCOMES: frozenset[str] = frozenset(
    {
        OUTCOME_CORRECT,
        OUTCOME_WRONG,
        OUTCOME_UNEXPECTED_ABSTAIN,
        OUTCOME_CORRECT_ABSTAIN,
        OUTCOME_MISSED_ABSTAIN,
    }
)

GROUND_TRUTH: dict[str, Expectation] = {
    "AAT": Abstain(),
    "AST": Covered("Asset Management"),
    "BCD": Covered("Business Continuity"),
    "CAP": Abstain(),
    "CFG": Abstain(),
    "CHG": Covered("Change Management"),
    "CLD": Abstain(),
    "CPL": Abstain(),
    "CRY": Covered("Encryption"),
    "DCH": Abstain(),
    "EMB": Abstain(),
    "END": Abstain(),
    "GOV": Abstain(),
    "HRS": Abstain(),
    "IAC": Covered("Identity and Access"),
    "IAO": Abstain(),
    "IRO": Covered("Incident Response"),
    "MDM": Covered("Mobile Device"),
    "MNT": Abstain(),
    "MON": Covered("Logging and Monitoring"),
    "NET": Abstain(),
    "OPS": Abstain(),
    "PES": Covered("Physical and Environmental"),
    "PRI": Abstain(),
    "PRM": Abstain(),
    "RSK": Abstain(),
    "SAT": Abstain(),
    "SEA": Abstain(),
    "TDA": Covered("Secure Development"),
    "THR": Abstain(),
    "TPM": Covered("Vendor Information Security"),
    "VPM": Covered("Vulnerability Management"),
    "WEB": Abstain(),
}


def covered_domains() -> frozenset[str]:
    return frozenset(domain for domain, expectation in GROUND_TRUTH.items() if isinstance(expectation, Covered))


def abstain_domains() -> frozenset[str]:
    return frozenset(domain for domain, expectation in GROUND_TRUTH.items() if isinstance(expectation, Abstain))


def expected_substring(domain: str) -> str | None:
    try:
        expectation = GROUND_TRUTH[domain]
    except KeyError as exc:
        raise KeyError(f"Unknown SCF domain {domain!r}; known domains: {', '.join(sorted(GROUND_TRUTH))}") from exc

    if isinstance(expectation, Covered):
        return expectation.expected_substring
    if isinstance(expectation, Abstain):
        return None
    raise TypeError(f"Unsupported expectation for SCF domain {domain!r}: {expectation!r}")


def judge(domain: str, top_filename: str | None) -> str:
    expected = expected_substring(domain)

    if expected is None:
        if top_filename is None:
            return OUTCOME_CORRECT_ABSTAIN
        return OUTCOME_MISSED_ABSTAIN

    if top_filename is None:
        return OUTCOME_UNEXPECTED_ABSTAIN

    if expected.casefold() in top_filename.casefold():
        return OUTCOME_CORRECT
    return OUTCOME_WRONG


_covered = covered_domains()
_abstain = abstain_domains()
assert len(GROUND_TRUTH) == 33, f"Expected 33 SCF domains, found {len(GROUND_TRUTH)}"
assert len(_covered) == 12, f"Expected 12 covered domains, found {len(_covered)}"
assert len(_abstain) == 21, f"Expected 21 abstain domains, found {len(_abstain)}"
assert _covered.isdisjoint(_abstain), "Covered and abstain domains must be disjoint"
assert _covered | _abstain == frozenset(GROUND_TRUTH), "Covered plus abstain domains must equal the fixture keys"
assert all(
    expectation.expected_substring
    for expectation in GROUND_TRUTH.values()
    if isinstance(expectation, Covered)
), "Every covered domain must have a non-empty expected substring"
