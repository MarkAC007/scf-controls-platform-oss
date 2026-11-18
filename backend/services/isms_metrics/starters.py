# SPDX-License-Identifier: AGPL-3.0-only
"""The twelve starter objectives, seeded ``proposed`` in this order.

Eight are measured live from the registers, three are logged by the
responsible team and one is a milestone. A starter carries a suggested target
but nobody has set it: ``target_set_by_user_id`` stays empty and nothing is
judged until an admin confirms the team and the target.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Starter:
    key: str
    name: str
    objective_type: str
    planned_actions: str
    clauses: Tuple[str, ...]
    kind: str  # live | logged | milestone
    metric_name: str
    measure_key: Optional[str] = None
    unit: Optional[str] = None
    direction: Optional[str] = None
    target: Optional[float] = None
    warn: Optional[float] = None
    frequency: Optional[str] = None
    description: str = ""

    @property
    def source(self) -> str:
        return f"starter:{self.key}"


STARTERS: Tuple[Starter, ...] = (
    Starter(
        "risk_appetite", "Keep risk within appetite", "maintain",
        "Assess every risk, treat those above the acceptable level, and renew or retire risk acceptances "
        "before they expire.",
        ("6.1.2", "6.1.3", "8.2", "8.3"), "live", "Risks within appetite (%)", measure_key="risk_within_appetite",
    ),
    Starter(
        "material_deficiencies", "Leave no material deficiency open", "maintain",
        "Give every material deficiency an owner and a due date, and retest before closing.",
        ("10.2", "9.1"), "live", "Open material deficiencies (count)", measure_key="open_material_deficiencies",
    ),
    Starter(
        "deficiency_correction", "Correct deficiencies within 90 days", "maintain",
        "Review open deficiencies every month and correct each one within 90 days of it being raised.",
        ("10.2",), "live", "Deficiencies corrected within 90 days (%)", measure_key="deficiencies_corrected_in_time",
    ),
    Starter(
        "evidence_on_time", "Collect evidence on time", "maintain",
        "Complete each evidence collection task by its due date; automate collection where a collector exists.",
        ("9.1", "7.5"), "live", "Evidence collected on time (%)", measure_key="evidence_collected_on_time",
    ),
    Starter(
        "supplier_assurance", "Keep supplier assurance current", "maintain",
        "Assess every high and critical supplier at least once a year and follow up the actions raised.",
        ("8.1", "A.5.19", "A.5.20", "A.5.21", "A.5.22"), "live", "Suppliers with current assurance (%)",
        measure_key="supplier_assurance_current",
    ),
    Starter(
        "controls_implemented", "Implement the controls in scope", "achieve",
        "Implement each control in scope, in priority order, and record its status as work completes.",
        ("6.1.3", "8.1"), "live", "Controls implemented (%)", measure_key="controls_implemented",
    ),
    Starter(
        "response_capability", "Test our response capability", "maintain",
        "Run at least two incident response or continuity tests a year and record the outcome of each.",
        ("8.1", "A.5.24", "A.5.26", "A.5.29", "A.5.30"), "live", "Response tests passed (count)",
        measure_key="response_tests_passed",
    ),
    Starter(
        "audit_programme", "Complete the internal audit programme", "achieve",
        "Carry out each audit in the programme and record its findings.",
        ("9.2",), "live", "Internal audits completed (%)", measure_key="audit_programme_completed",
    ),
    Starter(
        "security_awareness", "Train everyone in security awareness", "maintain",
        "Run annual awareness training for all staff and contractors; induct new starters in their first month.",
        ("7.2", "7.3"), "logged", "Training completion (%)", unit="percent", direction="at_least",
        target=95, warn=85, frequency="quarterly",
        description="Staff who completed security training in the last 12 months.",
    ),
    Starter(
        "client_information", "Protect client information", "maintain",
        "Record and investigate every incident involving client information and act on the lessons.",
        ("A.5.24", "A.5.25", "A.5.26", "A.5.27"), "logged", "Client information incidents (count)",
        unit="count", direction="at_most", target=0, warn=None, frequency="quarterly",
        description="Confirmed incidents involving client information this year.",
    ),
    Starter(
        "access_reviews", "Review access on schedule", "maintain",
        "Review user access to each system on its planned schedule and remove what is no longer needed.",
        ("A.5.15", "A.5.18"), "logged", "Access reviews completed (%)", unit="percent", direction="at_least",
        target=100, warn=90, frequency="quarterly", description="Planned access reviews completed.",
    ),
    Starter(
        "iso_certification", "Achieve ISO/IEC 27001 certification", "achieve",
        "Complete the internal audit and management review, then the Stage 1 and Stage 2 audits with an "
        "accredited certification body.",
        ("9.2", "10.1"), "milestone", "Certification milestone",
        description="Certificate issued with no major nonconformity.",
    ),
)

STARTER_SOURCES = tuple(s.source for s in STARTERS)
