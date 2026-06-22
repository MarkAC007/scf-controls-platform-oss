"""
Evidence Maturity Advisory Service.

Calculates maturity levels (L0-L5) for evidence collection based on:
- Collection method (manual, export, api, webhook, scheduled)
- Capability status (potential, configured, active)
- Collection frequency and freshness

Maturity Levels:
- L0 (Non-Existent): No defined process - evidence not tracked
- L1 (Ad Hoc): Inconsistent, reactive, manual screenshots
- L2 (Developing): Documented but manual execution
- L3 (Defined): Standardised, repeatable, semi-automated (scheduled exports)
- L4 (Managed): Measured, controlled, automated via API
- L5 (Optimising): Continuous improvement, AI/ML-driven analysis

This service intentionally keeps calculation logic separate from API concerns
for testability and reuse.
"""
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, List
from datetime import date, timedelta


class MaturityLevel(IntEnum):
    """Evidence collection maturity levels."""
    L0_NON_EXISTENT = 0
    L1_AD_HOC = 1
    L2_DEVELOPING = 2
    L3_DEFINED = 3
    L4_MANAGED = 4
    L5_OPTIMISING = 5


# Human-readable names for maturity levels
MATURITY_NAMES = {
    MaturityLevel.L0_NON_EXISTENT: "Non-Existent",
    MaturityLevel.L1_AD_HOC: "Ad Hoc",
    MaturityLevel.L2_DEVELOPING: "Developing",
    MaturityLevel.L3_DEFINED: "Defined",
    MaturityLevel.L4_MANAGED: "Managed",
    MaturityLevel.L5_OPTIMISING: "Optimising",
}

# Descriptions for each maturity level
MATURITY_DESCRIPTIONS = {
    MaturityLevel.L0_NON_EXISTENT: "No defined process for collecting this evidence",
    MaturityLevel.L1_AD_HOC: "Inconsistent, reactive collection using manual screenshots or ad-hoc methods",
    MaturityLevel.L2_DEVELOPING: "Documented collection process but still relies on manual execution",
    MaturityLevel.L3_DEFINED: "Standardised, repeatable process with semi-automated collection",
    MaturityLevel.L4_MANAGED: "Fully automated collection via API or webhook with measured results",
    MaturityLevel.L5_OPTIMISING: "Continuous improvement with AI/ML-driven analysis and validation",
}


@dataclass
class MaturityInput:
    """Input data for maturity calculation."""
    is_tracked: bool = False
    collection_method: Optional[str] = None  # manual, export, api, webhook, scheduled, integration
    capability_status: Optional[str] = None  # potential, configured, active
    frequency: Optional[str] = None  # daily, weekly, monthly, quarterly, annual, on_demand
    last_collection_date: Optional[date] = None
    has_system_linked: bool = False
    method_of_collection: Optional[str] = None  # Free-text from EvidenceTracking


@dataclass
class MaturityResult:
    """Result of maturity calculation."""
    level: MaturityLevel
    name: str
    description: str
    score: int  # 0-5
    factors: dict  # Contributing factors to the score
    upgrade_potential: Optional[MaturityLevel] = None  # Next achievable level


@dataclass
class UpgradeRecommendation:
    """Recommendation for improving maturity level."""
    current_level: MaturityLevel
    target_level: MaturityLevel
    title: str
    description: str
    effort: str  # "low", "medium", "high"
    impact: str  # "low", "medium", "high"
    steps: List[str]


# Collection method to base maturity level mapping
# These represent the ceiling without other factors
COLLECTION_METHOD_MATURITY = {
    "manual": MaturityLevel.L1_AD_HOC,
    "export": MaturityLevel.L2_DEVELOPING,
    "scheduled": MaturityLevel.L3_DEFINED,
    "api": MaturityLevel.L4_MANAGED,
    "webhook": MaturityLevel.L4_MANAGED,
    "integration": MaturityLevel.L4_MANAGED,
}

# Capability status modifiers
# Active capabilities can boost; potential ones cap
CAPABILITY_STATUS_MODIFIERS = {
    "potential": -1,  # Reduce by 1 level - not actually collecting
    "configured": 0,   # No change - ready but need proof of execution
    "active": 1,       # Boost by 1 level - proven active collection
}

# Frequency impact on maturity
# More frequent = higher confidence in process maturity
FREQUENCY_SCORES = {
    "real_time": 2,    # Continuous streaming
    "daily": 2,        # High frequency
    "weekly": 1,       # Good frequency
    "monthly": 0,      # Acceptable
    "quarterly": -1,   # Low frequency, may miss issues
    "annual": -2,      # Concerning for most evidence
    "on_demand": -1,   # Reactive, not proactive
}


def calculate_maturity(input_data: MaturityInput) -> MaturityResult:
    """
    Calculate evidence collection maturity level.

    The algorithm:
    1. Start with base level from collection_method
    2. Apply capability_status modifier
    3. Consider frequency/freshness
    4. Cap at realistic ceiling based on automation

    Args:
        input_data: MaturityInput with evidence collection details

    Returns:
        MaturityResult with level, description, and contributing factors
    """
    factors = {}

    # Step 0: Not tracked at all = L0
    if not input_data.is_tracked:
        return MaturityResult(
            level=MaturityLevel.L0_NON_EXISTENT,
            name=MATURITY_NAMES[MaturityLevel.L0_NON_EXISTENT],
            description=MATURITY_DESCRIPTIONS[MaturityLevel.L0_NON_EXISTENT],
            score=0,
            factors={"reason": "Evidence is not being tracked"},
            upgrade_potential=MaturityLevel.L2_DEVELOPING,
        )

    # Step 1: Base level from collection method
    collection_method = (input_data.collection_method or "").lower().strip()

    # Also check free-text method_of_collection for hints
    method_text = (input_data.method_of_collection or "").lower()

    # Infer collection method from free text if not explicitly set
    if not collection_method:
        if any(kw in method_text for kw in ["api", "rest", "graphql", "endpoint"]):
            collection_method = "api"
        elif any(kw in method_text for kw in ["webhook", "callback", "push"]):
            collection_method = "webhook"
        elif any(kw in method_text for kw in ["scheduled", "cron", "automated", "script"]):
            collection_method = "scheduled"
        elif any(kw in method_text for kw in ["export", "download", "csv", "report"]):
            collection_method = "export"
        elif any(kw in method_text for kw in ["screenshot", "manual", "copy", "paste"]):
            collection_method = "manual"

    base_level = COLLECTION_METHOD_MATURITY.get(collection_method, MaturityLevel.L1_AD_HOC)
    factors["collection_method"] = {
        "value": collection_method or "unknown",
        "base_level": base_level,
    }

    # Step 2: Apply capability status modifier
    capability_status = (input_data.capability_status or "").lower()
    status_modifier = CAPABILITY_STATUS_MODIFIERS.get(capability_status, 0)

    if capability_status:
        factors["capability_status"] = {
            "value": capability_status,
            "modifier": status_modifier,
        }

    # Step 3: Consider frequency
    frequency = (input_data.frequency or "").lower()
    frequency_modifier = FREQUENCY_SCORES.get(frequency, 0)

    if frequency:
        factors["frequency"] = {
            "value": frequency,
            "modifier": frequency_modifier,
        }

    # Step 4: Check freshness (if we have last_collection_date)
    freshness_modifier = 0
    if input_data.last_collection_date:
        days_since = (date.today() - input_data.last_collection_date).days
        if days_since <= 7:
            freshness_modifier = 1  # Very fresh
        elif days_since <= 30:
            freshness_modifier = 0  # Acceptable
        elif days_since <= 90:
            freshness_modifier = -1  # Stale
        else:
            freshness_modifier = -2  # Very stale

        factors["freshness"] = {
            "days_since_collection": days_since,
            "modifier": freshness_modifier,
        }

    # Step 5: System linkage bonus
    system_modifier = 0
    if input_data.has_system_linked:
        system_modifier = 1  # Having a defined system shows process maturity
        factors["system_linked"] = {
            "value": True,
            "modifier": system_modifier,
        }

    # Calculate final score
    raw_score = base_level + status_modifier + (frequency_modifier // 2) + (freshness_modifier // 2) + system_modifier

    # Clamp to valid range (L0-L5)
    # Note: L5 requires explicit criteria beyond automation
    clamped_score = max(1, min(4, raw_score))  # Cap at L4 for now - L5 requires special criteria

    # L5 criteria: Must be L4 AND have active status AND high frequency AND fresh data
    if (clamped_score == 4 and
        capability_status == "active" and
        frequency in ["real_time", "daily"] and
        input_data.last_collection_date and
        (date.today() - input_data.last_collection_date).days <= 7):
        clamped_score = 5
        factors["l5_achieved"] = {
            "reason": "Meets all L5 criteria: automated, active, high frequency, fresh data"
        }

    final_level = MaturityLevel(clamped_score)

    # Determine upgrade potential
    upgrade_potential = None
    if final_level < MaturityLevel.L5_OPTIMISING:
        upgrade_potential = MaturityLevel(final_level + 1)

    return MaturityResult(
        level=final_level,
        name=MATURITY_NAMES[final_level],
        description=MATURITY_DESCRIPTIONS[final_level],
        score=clamped_score,
        factors=factors,
        upgrade_potential=upgrade_potential,
    )


def get_upgrade_recommendations(
    current_level: MaturityLevel,
    collection_method: Optional[str] = None,
    capability_status: Optional[str] = None,
) -> List[UpgradeRecommendation]:
    """
    Generate recommendations for improving evidence maturity level.

    Args:
        current_level: Current maturity level
        collection_method: Current collection method
        capability_status: Current capability status

    Returns:
        List of UpgradeRecommendation objects, prioritised by impact
    """
    recommendations = []

    # L0 -> L1/L2: Start tracking
    if current_level == MaturityLevel.L0_NON_EXISTENT:
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel.L2_DEVELOPING,
            title="Begin Evidence Tracking",
            description="Establish a documented process for collecting this evidence",
            effort="medium",
            impact="high",
            steps=[
                "Identify the authoritative source for this evidence",
                "Document the collection procedure (who, what, when, how)",
                "Set up a collection schedule (at minimum quarterly)",
                "Create a storage location for collected evidence",
                "Assign an owner responsible for collection",
            ],
        ))

    # L1 -> L2: Document the process
    if current_level == MaturityLevel.L1_AD_HOC:
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel.L2_DEVELOPING,
            title="Document Collection Process",
            description="Move from ad-hoc to documented, repeatable collection",
            effort="low",
            impact="medium",
            steps=[
                "Write down the exact steps used to collect this evidence",
                "Define what format the evidence should be stored in",
                "Establish a regular collection schedule",
                "Create a checklist for quality verification",
            ],
        ))

    # L2 -> L3: Automate with scheduled exports
    if current_level <= MaturityLevel.L2_DEVELOPING:
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel.L3_DEFINED,
            title="Implement Scheduled Collection",
            description="Automate evidence collection with scheduled exports or scripts",
            effort="medium",
            impact="high",
            steps=[
                "Identify if the source system supports scheduled reports/exports",
                "Set up automated report generation (daily/weekly)",
                "Configure email delivery or file storage for exports",
                "Create alerts for failed collection jobs",
                "Link the collecting system in the platform",
            ],
        ))

    # L3 -> L4: Full API integration
    if current_level <= MaturityLevel.L3_DEFINED:
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel.L4_MANAGED,
            title="Implement API-Based Collection",
            description="Connect directly to source systems via API for real-time evidence",
            effort="high",
            impact="high",
            steps=[
                "Check if the source system has an API (REST, GraphQL)",
                "Obtain API credentials and document access requirements",
                "Build or configure an integration to pull evidence automatically",
                "Implement error handling and retry logic",
                "Set up monitoring for API health and data freshness",
                "Configure capability status as 'active' once operational",
            ],
        ))

    # L4 -> L5: Continuous improvement
    if current_level == MaturityLevel.L4_MANAGED:
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel.L5_OPTIMISING,
            title="Enable Continuous Optimisation",
            description="Add AI/ML-driven analysis for proactive compliance monitoring",
            effort="high",
            impact="medium",
            steps=[
                "Implement anomaly detection on collected evidence",
                "Set up trend analysis to predict compliance drift",
                "Create automated alerts for potential issues",
                "Build dashboards for evidence quality metrics",
                "Establish feedback loops to improve collection accuracy",
            ],
        ))

    # Specific recommendations based on current state
    if collection_method == "manual" and current_level >= MaturityLevel.L1_AD_HOC:
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel.L3_DEFINED,
            title="Replace Manual Screenshots",
            description="Manual screenshots are error-prone and time-consuming",
            effort="medium",
            impact="high",
            steps=[
                "Identify native export capabilities in the source system",
                "Look for scheduled report features",
                "Consider third-party tools for automated capture if needed",
            ],
        ))

    if capability_status == "potential":
        recommendations.append(UpgradeRecommendation(
            current_level=current_level,
            target_level=MaturityLevel(min(current_level + 1, 5)),
            title="Activate System Capability",
            description="A capable system is identified but not yet configured",
            effort="low",
            impact="medium",
            steps=[
                "Review the system's evidence capability configuration",
                "Set up the connection between the system and evidence tracking",
                "Update capability status to 'configured' once ready",
                "Update to 'active' once evidence is being collected",
            ],
        ))

    # Sort by impact (high first) then effort (low first)
    impact_order = {"high": 0, "medium": 1, "low": 2}
    effort_order = {"low": 0, "medium": 1, "high": 2}
    recommendations.sort(key=lambda r: (impact_order.get(r.impact, 2), effort_order.get(r.effort, 2)))

    return recommendations


@dataclass
class MaturityDistribution:
    """Distribution of evidence across maturity levels."""
    l0_count: int = 0
    l1_count: int = 0
    l2_count: int = 0
    l3_count: int = 0
    l4_count: int = 0
    l5_count: int = 0
    total: int = 0

    @property
    def average_score(self) -> float:
        """Calculate weighted average maturity score."""
        if self.total == 0:
            return 0.0
        total_score = (
            self.l0_count * 0 +
            self.l1_count * 1 +
            self.l2_count * 2 +
            self.l3_count * 3 +
            self.l4_count * 4 +
            self.l5_count * 5
        )
        return round(total_score / self.total, 2)

    @property
    def automation_percentage(self) -> float:
        """Percentage of evidence at L3+ (semi-automated or better)."""
        if self.total == 0:
            return 0.0
        automated = self.l3_count + self.l4_count + self.l5_count
        return round(automated / self.total * 100, 1)

    def increment(self, level: MaturityLevel) -> None:
        """Increment count for a maturity level."""
        self.total += 1
        if level == MaturityLevel.L0_NON_EXISTENT:
            self.l0_count += 1
        elif level == MaturityLevel.L1_AD_HOC:
            self.l1_count += 1
        elif level == MaturityLevel.L2_DEVELOPING:
            self.l2_count += 1
        elif level == MaturityLevel.L3_DEFINED:
            self.l3_count += 1
        elif level == MaturityLevel.L4_MANAGED:
            self.l4_count += 1
        elif level == MaturityLevel.L5_OPTIMISING:
            self.l5_count += 1
