"""
SQLAlchemy ORM models for CG SCF database.
Maps to PostgreSQL tables: organizations, users, scoped_controls, evidence_tracking,
assignments, comments, evidence_collection_tasks, notifications, systems.

Note: Migrated from CCF (Common Controls Framework) to SCF (Secure Controls Framework)
as of v4.0.0. The scf_id field replaces the former ccf_id field.
"""
from datetime import datetime
from enum import Enum
from typing import Optional, List, Tuple
from sqlalchemy import Column, String, Boolean, Text, Date, ForeignKey, DateTime, JSON, Integer, Numeric, UniqueConstraint, Index, BigInteger, Float, LargeBinary
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
import uuid

from database import Base


# =============================================================================
# Implementation Status Enum and Workflow
# =============================================================================

class ImplementationStatus(str, Enum):
    """
    Control implementation status values aligned with SCFConnect workflow.

    Workflow Progression:
        NOT_STARTED -> IN_PROGRESS -> IMPLEMENTED -> READY_FOR_REVIEW -> MONITORED

    Special States (can be set at any time):
        - NOT_APPLICABLE: Control does not apply to this organisation
        - AT_RISK: Control implementation is at risk (flag state)
        - DEFERRED: Control implementation has been deferred

    State Descriptions:
        - NOT_STARTED: Control has not begun implementation
        - IN_PROGRESS: Control is actively being implemented
        - IMPLEMENTED: Control implementation is complete, awaiting review
        - READY_FOR_REVIEW: Control is ready for formal review/assessment
        - MONITORED: Control is in ongoing monitoring state (steady state)
        - NOT_APPLICABLE: Control does not apply to this organisation
        - AT_RISK: Control implementation is at risk
        - DEFERRED: Control implementation has been deferred
    """
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    IMPLEMENTED = "implemented"
    READY_FOR_REVIEW = "ready_for_review"
    MONITORED = "monitored"
    NOT_APPLICABLE = "not_applicable"
    AT_RISK = "at_risk"
    DEFERRED = "deferred"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid status values as strings."""
        return [status.value for status in cls]

    @classmethod
    def workflow_states(cls) -> List["ImplementationStatus"]:
        """Return the primary workflow states in order."""
        return [
            cls.NOT_STARTED,
            cls.IN_PROGRESS,
            cls.IMPLEMENTED,
            cls.READY_FOR_REVIEW,
            cls.MONITORED,
        ]

    @classmethod
    def special_states(cls) -> List["ImplementationStatus"]:
        """Return special states that can be set at any time."""
        return [cls.NOT_APPLICABLE, cls.AT_RISK, cls.DEFERRED]

    def is_terminal(self) -> bool:
        """Check if this is a terminal/steady state."""
        return self in (
            ImplementationStatus.MONITORED,
            ImplementationStatus.NOT_APPLICABLE,
        )

    def is_active(self) -> bool:
        """Check if this status indicates active work."""
        return self in (
            ImplementationStatus.IN_PROGRESS,
            ImplementationStatus.IMPLEMENTED,
            ImplementationStatus.READY_FOR_REVIEW,
        )


# Valid status transitions for the primary workflow
# Format: {current_status: [list of valid next statuses]}
STATUS_TRANSITIONS = {
    ImplementationStatus.NOT_STARTED: [
        ImplementationStatus.IN_PROGRESS,
        ImplementationStatus.NOT_APPLICABLE,
        ImplementationStatus.DEFERRED,
    ],
    ImplementationStatus.IN_PROGRESS: [
        ImplementationStatus.IMPLEMENTED,
        ImplementationStatus.NOT_STARTED,  # Allow reverting
        ImplementationStatus.AT_RISK,
        ImplementationStatus.DEFERRED,
    ],
    ImplementationStatus.IMPLEMENTED: [
        ImplementationStatus.READY_FOR_REVIEW,
        ImplementationStatus.IN_PROGRESS,  # Allow reverting
        ImplementationStatus.AT_RISK,
    ],
    ImplementationStatus.READY_FOR_REVIEW: [
        ImplementationStatus.MONITORED,
        ImplementationStatus.IMPLEMENTED,  # Allow reverting if review fails
        ImplementationStatus.IN_PROGRESS,  # Major rework needed
        ImplementationStatus.AT_RISK,
    ],
    ImplementationStatus.MONITORED: [
        ImplementationStatus.IN_PROGRESS,  # Control needs rework
        ImplementationStatus.READY_FOR_REVIEW,  # Re-review needed
        ImplementationStatus.AT_RISK,
        ImplementationStatus.NOT_APPLICABLE,  # Scope changed
    ],
    ImplementationStatus.NOT_APPLICABLE: [
        ImplementationStatus.NOT_STARTED,  # Scope changed, now applicable
    ],
    ImplementationStatus.AT_RISK: [
        ImplementationStatus.IN_PROGRESS,  # Risk addressed, back to work
        ImplementationStatus.IMPLEMENTED,
        ImplementationStatus.DEFERRED,
        ImplementationStatus.NOT_APPLICABLE,
    ],
    ImplementationStatus.DEFERRED: [
        ImplementationStatus.NOT_STARTED,  # Un-defer, start fresh
        ImplementationStatus.IN_PROGRESS,  # Resume work
        ImplementationStatus.NOT_APPLICABLE,
    ],
}


def is_valid_status_transition(
    current_status: Optional[str],
    new_status: str,
    strict: bool = False
) -> Tuple[bool, Optional[str]]:
    """
    Validate whether a status transition is allowed.

    Args:
        current_status: The current implementation status (or None for new controls)
        new_status: The proposed new status
        strict: If True, enforce workflow rules. If False, allow any transition.

    Returns:
        Tuple of (is_valid, error_message).
        If valid, error_message is None.

    Examples:
        >>> is_valid_status_transition(None, "not_started")
        (True, None)
        >>> is_valid_status_transition("not_started", "in_progress")
        (True, None)
        >>> is_valid_status_transition("not_started", "monitored", strict=True)
        (False, "Cannot transition from 'not_started' to 'monitored'...")
    """
    # Validate new_status is a valid value
    try:
        new_enum = ImplementationStatus(new_status)
    except ValueError:
        return (False, f"Invalid status value: '{new_status}'. Valid values: {ImplementationStatus.values()}")

    # If no current status (new control), any valid status is allowed
    if current_status is None:
        return (True, None)

    # Validate current status
    try:
        current_enum = ImplementationStatus(current_status)
    except ValueError:
        # Current status is invalid/legacy - allow any transition to fix it
        return (True, None)

    # Same status - always valid (no-op)
    if current_enum == new_enum:
        return (True, None)

    # If not strict mode, allow any transition
    if not strict:
        return (True, None)

    # Check if transition is in the allowed list
    allowed = STATUS_TRANSITIONS.get(current_enum, [])
    if new_enum in allowed:
        return (True, None)

    return (
        False,
        f"Cannot transition from '{current_status}' to '{new_status}'. "
        f"Allowed transitions: {[s.value for s in allowed]}"
    )


class User(Base):
    """User model - represents authenticated users (Google OAuth).

    Platform Admin:
        The is_platform_admin flag grants cross-organisation administrative access.
        Platform admins can manage all users and organisations via the admin API/CLI.
        This is separate from organisation-level admin roles (OrganizationMember.role).
    """
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Subject identifier from the IdP (Google 'sub', or generic OIDC 'sub').
    # Uniqueness is scoped per-issuer via the composite constraint below, so the
    # same subject value from two different IdPs is two distinct users.
    google_sub = Column(String(255), nullable=False)
    # Issuer ('iss') of the IdP that owns google_sub. NULL for pending-link
    # placeholders (pre-first-login) and for pre-backfill legacy rows.
    oidc_issuer = Column(String(255), nullable=True)
    email = Column(String(255), unique=True, nullable=False)
    display_name = Column(String(255))
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    last_login_at = Column(DateTime(timezone=False))
    email_notifications_enabled = Column(Boolean, default=True)
    notification_frequency = Column(String(50), default='immediate')
    # Platform-level admin flag (cross-organisation access)
    is_platform_admin = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("oidc_issuer", "google_sub", name="uq_users_oidc_issuer_google_sub"),
    )

    # Relationships
    memberships = relationship("OrganizationMember", back_populates="user", cascade="all, delete-orphan")
    assignments = relationship("Assignment", foreign_keys="[Assignment.user_id]", back_populates="user", cascade="all, delete-orphan")
    comments = relationship("Comment", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")
    assigned_tasks = relationship("EvidenceCollectionTask", foreign_keys="[EvidenceCollectionTask.assigned_user_id]", back_populates="assigned_user")
    consultant_profile = relationship("ConsultantProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    subscription = relationship("UserSubscription", back_populates="user", uselist=False, cascade="all, delete-orphan")
    api_keys = relationship("ApiKey", back_populates="user", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class Organization(Base):
    """Organization model - represents a company/team using the system."""
    __tablename__ = "organizations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)
    awaiting_admin = Column(Boolean, default=False, nullable=False)
    created_by_consultant_id = Column(UUID(as_uuid=True), ForeignKey("consultant_profiles.id", ondelete="SET NULL"), nullable=True)
    settings = Column(JSON, nullable=False, default=dict, server_default='{}')
    logo_data = Column(LargeBinary, nullable=True)
    logo_content_type = Column(String(100), nullable=True)
    logo_filename = Column(String(255), nullable=True)
    logo_updated_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    members = relationship("OrganizationMember", back_populates="organization", cascade="all, delete-orphan")
    scoped_controls = relationship("ScopedControl", back_populates="organization", cascade="all, delete-orphan")
    evidence_tracking = relationship("EvidenceTracking", back_populates="organization", cascade="all, delete-orphan")
    systems = relationship("System", back_populates="organization", cascade="all, delete-orphan")
    consultant_relationships = relationship("ConsultantClientRelationship", back_populates="organization", cascade="all, delete-orphan")
    invites = relationship("OrganizationInvite", back_populates="organization", cascade="all, delete-orphan")
    created_by_consultant = relationship("ConsultantProfile", foreign_keys=[created_by_consultant_id])
    risk_profile = relationship("OrganizationRiskProfile", back_populates="organization", uselist=False, cascade="all, delete-orphan")
    vendors = relationship("Vendor", back_populates="organization", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="organization", cascade="all, delete-orphan")
    evidence_files = relationship("EvidenceFile", back_populates="organization", cascade="all, delete-orphan")
    evidence_validation_results = relationship("EvidenceValidationResult", back_populates="organization", cascade="all, delete-orphan")
    evidence_assessments = relationship("EvidenceAssessment", back_populates="organization", cascade="all, delete-orphan")
    webhook_endpoints = relationship("WebhookEndpoint", back_populates="organization", cascade="all, delete-orphan")
    audit_engagements = relationship("AuditEngagement", back_populates="organization", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Organization(id={self.id}, name={self.name})>"


class OrganizationRiskProfile(Base):
    """Per-organisation risk profile with configurable thresholds.

    Replaces hardcoded risk level boundaries (1-4/5-9/10-16/17-25) with
    configurable values per organisation. Each org has at most one profile
    (enforced by unique constraint on organization_id).
    """
    __tablename__ = "organization_risk_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, unique=True)

    # Risk level thresholds (score boundaries)
    low_max = Column(Integer, nullable=False, default=4)
    medium_max = Column(Integer, nullable=False, default=9)
    high_max = Column(Integer, nullable=False, default=16)

    # Risk appetite
    acceptable_risk_level = Column(String(20), nullable=False, default='medium')
    auto_escalate_above = Column(String(20), nullable=False, default='high')

    # Vendor certification preferences (JSON arrays stored as text)
    required_vendor_certifications = Column(Text, nullable=False, default='[]')
    preferred_vendor_certifications = Column(Text, nullable=False, default='[]')

    # Vendor risk auto-action thresholds
    vendor_auto_approve_max = Column(Integer, nullable=False, default=4)
    vendor_auto_reject_min = Column(Integer, nullable=False, default=20)

    # Audit
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="risk_profile")
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    def __repr__(self):
        return f"<OrganizationRiskProfile(org={self.organization_id}, low_max={self.low_max}, medium_max={self.medium_max}, high_max={self.high_max})>"


class OrganizationMember(Base):
    """Organization membership - links users to organizations with roles."""
    __tablename__ = "organization_members"
    __table_args__ = (
        # Unique constraint: each user can only have one membership per organization
        UniqueConstraint('organization_id', 'user_id', name='uq_organization_members_org_user'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(50), nullable=False, default='viewer')
    joined_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="members")
    user = relationship("User", back_populates="memberships")

    def __repr__(self):
        return f"<OrganizationMember(org={self.organization_id}, user={self.user_id}, role={self.role})>"


class ApiKey(Base):
    """Per-organisation API key for programmatic access.

    Keys are scoped to a single organisation and inherit the creating user's
    role (admin/editor/viewer) at creation time.  The plaintext key is shown
    once; only a SHA-256 hash is stored.
    """
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    key_prefix = Column(String(8), nullable=False)
    key_hash = Column(String(64), nullable=False)
    role = Column(String(50), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime(timezone=False), nullable=True)
    last_used_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="api_keys")
    organization = relationship("Organization")

    def __repr__(self):
        return f"<ApiKey(id={self.id}, name={self.name}, prefix={self.key_prefix}, active={self.is_active})>"


class ScopedControl(Base):
    """Scoped Control model - tracks control selections and implementation status.

    Note: Migrated from CCF to SCF in v4.0.0. The scf_id field replaces ccf_id.

    Implementation Status Workflow (SCFConnect-aligned):
        NOT_STARTED -> IN_PROGRESS -> IMPLEMENTED -> READY_FOR_REVIEW -> MONITORED

    Special states (can be set at any time):
        - NOT_APPLICABLE: Control does not apply to this organisation
        - AT_RISK: Control implementation is at risk
        - DEFERRED: Control implementation has been deferred
    """
    __tablename__ = "scoped_controls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    scf_id = Column(String(50), nullable=False)  # References SCF control ID (formerly ccf_id)
    selected = Column(Boolean, default=False)
    selection_reason = Column(Text)
    implementation_status = Column(String(50))  # See ImplementationStatus enum for valid values
    priority = Column(String(20))
    owner = Column(String(255))  # Legacy text field
    assigned_to = Column(String(255))  # Legacy text field
    maturity_level = Column(String(50))
    target_date = Column(Date)
    completion_date = Column(Date)
    implementation_notes = Column(Text)
    related_documentation = Column(JSON)  # JSONB in PostgreSQL
    custom_fields = Column(JSON)  # JSONB in PostgreSQL
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # SCF-specific fields (added in v4.0.0)
    control_weighting = Column(Integer)  # Priority 1-10
    validation_cadence = Column(String(50))  # Review frequency
    nist_csf_function = Column(String(20))  # CSF 2.0 function (Identify, Protect, Detect, Respond, Recover, Govern)
    control_question = Column(Text)  # Assessment question

    # PPTDF Applicability flags (People, Process, Technology, Data, Facility)
    pptdf_people = Column(Boolean, default=False)
    pptdf_process = Column(Boolean, default=False)
    pptdf_technology = Column(Boolean, default=False)
    pptdf_data = Column(Boolean, default=False)
    pptdf_facility = Column(Boolean, default=False)

    # User FK columns
    assigned_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    organization = relationship("Organization", back_populates="scoped_controls")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    owner_user = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    def __repr__(self):
        return f"<ScopedControl(id={self.id}, scf_id={self.scf_id}, status={self.implementation_status})>"

    def get_status_enum(self) -> Optional[ImplementationStatus]:
        """Get the implementation status as an enum, or None if not set/invalid."""
        if self.implementation_status is None:
            return None
        try:
            return ImplementationStatus(self.implementation_status)
        except ValueError:
            return None

    def can_transition_to(self, new_status: str, strict: bool = False) -> Tuple[bool, Optional[str]]:
        """
        Check if this control can transition to the given status.

        Args:
            new_status: The proposed new status value
            strict: If True, enforce workflow rules

        Returns:
            Tuple of (is_valid, error_message)
        """
        return is_valid_status_transition(self.implementation_status, new_status, strict)

    def is_in_review_workflow(self) -> bool:
        """Check if this control is in the review/monitoring workflow stages."""
        status = self.get_status_enum()
        return status in (
            ImplementationStatus.IMPLEMENTED,
            ImplementationStatus.READY_FOR_REVIEW,
            ImplementationStatus.MONITORED,
        )

    def needs_attention(self) -> bool:
        """Check if this control needs attention (at risk or in progress for too long)."""
        status = self.get_status_enum()
        return status == ImplementationStatus.AT_RISK


class EvidenceTracking(Base):
    """Evidence Tracking model - tracks evidence collection status."""
    __tablename__ = "evidence_tracking"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)  # References ERL evidence ID
    is_tracked = Column(Boolean, default=False)
    method_of_collection = Column(Text)
    collecting_system = Column(String(255))
    owner = Column(String(255))  # Legacy text field
    frequency = Column(String(50))
    comments = Column(Text)
    maturity_level = Column(String(2))  # Evidence collection maturity L0-L5
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # New user FK columns and collection date tracking
    assigned_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    next_collection_date = Column(Date)
    last_collection_date = Column(Date)

    # System reference - links evidence to a collecting system
    system_id = Column(UUID(as_uuid=True), ForeignKey("systems.id", ondelete="SET NULL"))

    # Relationships
    organization = relationship("Organization", back_populates="evidence_tracking")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    owner_user = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])
    collection_tasks = relationship("EvidenceCollectionTask", back_populates="evidence_tracking", cascade="all, delete-orphan")
    system = relationship("System", back_populates="evidence_tracking")

    def __repr__(self):
        return f"<EvidenceTracking(id={self.id}, evidence_id={self.evidence_id})>"


class Assignment(Base):
    """Assignment model - polymorphic assignment of users to controls or evidence."""
    __tablename__ = "assignments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assignable_type = Column(String(50), nullable=False)  # 'control' or 'evidence'
    assignable_id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(50), default='primary')  # 'primary' or 'collaborator'
    assigned_at = Column(DateTime(timezone=False), server_default=func.now())
    assigned_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="assignments")
    assigned_by = relationship("User", foreign_keys=[assigned_by_user_id])

    def __repr__(self):
        return f"<Assignment(type={self.assignable_type}, id={self.assignable_id}, user={self.user_id})>"


class Comment(Base):
    """Comment model - polymorphic comments for controls, evidence, or tasks."""
    __tablename__ = "comments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    commentable_type = Column(String(50), nullable=False)  # 'control', 'evidence', or 'task'
    commentable_id = Column(UUID(as_uuid=True), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    parent_comment_id = Column(UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"))  # For threading/replies
    content = Column(Text, nullable=False)
    mentions = Column(JSONB, default=[])  # array of user IDs
    is_edited = Column(Boolean, default=False)
    edited_at = Column(DateTime(timezone=False))
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=False))
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="comments")
    history = relationship("CommentHistory", back_populates="comment", cascade="all, delete-orphan")
    parent = relationship("Comment", remote_side=[id], backref="replies")

    def __repr__(self):
        return f"<Comment(type={self.commentable_type}, id={self.commentable_id}, user={self.user_id})>"


class CommentHistory(Base):
    """CommentHistory model - audit trail for comment edits."""
    __tablename__ = "comment_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    comment_id = Column(UUID(as_uuid=True), ForeignKey("comments.id", ondelete="CASCADE"), nullable=False)
    old_content = Column(Text, nullable=False)
    edited_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    edited_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    comment = relationship("Comment", back_populates="history")
    edited_by = relationship("User")

    def __repr__(self):
        return f"<CommentHistory(comment={self.comment_id}, edited_at={self.edited_at})>"


class EvidenceCollectionTask(Base):
    """EvidenceCollectionTask model - tracks evidence lifecycle tasks."""
    __tablename__ = "evidence_collection_tasks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_tracking_id = Column(UUID(as_uuid=True), ForeignKey("evidence_tracking.id", ondelete="CASCADE"), nullable=False)

    # Task classification
    task_type = Column(String(50), default='collection')  # 'feasibility', 'setup', 'collection', 'review', 'documentation', 'issue'
    title = Column(String(255))  # e.g., "Confirm AWS CloudTrail Access"
    description = Column(Text)  # Detailed instructions/context
    priority = Column(String(20), default='medium')  # 'low', 'medium', 'high', 'critical'

    # Scheduling and assignment
    due_date = Column(Date, nullable=False)
    status = Column(String(50), default='not_started')  # 'not_started', 'in_progress', 'completed'
    assigned_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Completion tracking
    completed_date = Column(Date)
    completion_notes = Column(Text)

    # Metadata
    dependencies = Column(JSONB, default=[])  # Array of task IDs that must complete first
    attachments = Column(JSONB, default=[])  # Array of {url, name, type}
    auto_generated = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    evidence_tracking = relationship("EvidenceTracking", back_populates="collection_tasks")
    assigned_user = relationship("User", back_populates="assigned_tasks")

    def __repr__(self):
        return f"<EvidenceCollectionTask(type={self.task_type}, title={self.title}, status={self.status})>"


class Notification(Base):
    """Notification model - in-app notifications for users."""
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type = Column(String(50), nullable=False)  # 'assignment', 'mention', 'task_due', 'task_overdue'
    reference_type = Column(String(50), nullable=False)  # 'control', 'evidence', 'comment', 'task'
    reference_id = Column(UUID(as_uuid=True), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime(timezone=False))
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="notifications")

    def __repr__(self):
        return f"<Notification(user={self.user_id}, type={self.type}, read={self.is_read})>"


class System(Base):
    """System model - represents tools and systems that can provide evidence.

    Examples: AWS (cloud_provider), Okta (identity_provider), Jira (ticketing),
    Splunk (logging), CrowdStrike (security_tool), GitHub (code_repository).
    """
    __tablename__ = "systems"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    # Core fields
    name = Column(String(255), nullable=False)  # e.g., "AWS Production", "Okta SSO"
    system_type = Column(String(50), nullable=False)  # cloud_provider, identity_provider, etc.
    category = Column(String(100))  # Optional grouping, e.g., "Infrastructure", "Security"
    description = Column(Text)
    vendor = Column(String(255))  # e.g., "Amazon Web Services", "Okta Inc."
    status = Column(String(20), default='active')  # active, inactive, deprecated

    # Connection configuration (for future integrations)
    connection_config = Column(JSONB, default={})  # API endpoints, auth method hints, etc.

    # Link to the systems knowledge catalog (template picker / recipe resolution)
    catalog_template_id = Column(
        Integer,
        ForeignKey("system_catalog_templates.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Structural link to the rich TPRM Vendor entity. Distinct from the legacy
    # free-text `vendor` column above (which is retained): this is the FK to the
    # org-scoped vendors table. SET NULL so retiring a vendor never deletes systems.
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Audit user FKs
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    organization = relationship("Organization", back_populates="systems")
    catalog_template = relationship("SystemCatalogTemplate", foreign_keys=[catalog_template_id])
    linked_vendor = relationship("Vendor", foreign_keys=[vendor_id], back_populates="systems")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])
    evidence_tracking = relationship("EvidenceTracking", back_populates="system")
    capabilities = relationship("SystemEvidenceCapability", back_populates="system", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<System(id={self.id}, name={self.name}, type={self.system_type})>"


class SystemEvidenceCapability(Base):
    """Junction model - maps systems to evidence they can provide.

    This represents CAPABILITY - what evidence a system CAN provide,
    not what IS being collected (that's EvidenceTracking.system_id).

    Examples:
    - AWS CloudTrail can provide ERL-AM-001 (Asset Inventory)
    - Okta can provide ERL-IAM-002 (User Access Reviews)
    - CrowdStrike can provide ERL-VM-003 (Vulnerability Scans)
    """
    __tablename__ = "system_evidence_capabilities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    system_id = Column(UUID(as_uuid=True), ForeignKey("systems.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)  # References ERL evidence ID

    # Capability metadata
    capability_status = Column(String(20), default='potential')  # potential, configured, active
    collection_method = Column(String(50))  # api, export, manual, webhook, scheduled
    confidence_level = Column(String(20), default='medium')  # high, medium, low
    data_format = Column(String(50))  # json, csv, pdf, logs, etc.
    notes = Column(Text)  # Implementation notes, requirements, etc.

    # Timestamps
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Audit user FKs
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    system = relationship("System", back_populates="capabilities")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    def __repr__(self):
        return f"<SystemEvidenceCapability(system={self.system_id}, evidence={self.evidence_id}, status={self.capability_status})>"


class RecipeFeedback(Base):
    """Recipe feedback model - tracks user feedback on collection recipes.

    Used to identify recipes that need improvement or don't match
    real-world system configurations.
    """
    __tablename__ = "recipe_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)
    system_type = Column(String(50), nullable=False)
    vendor = Column(String(255), nullable=True)
    feedback_type = Column(String(20), nullable=False)  # "helpful" or "not_matching"
    maturity_level = Column(String(5), nullable=False)  # L0-L5
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    organization = relationship("Organization")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return f"<RecipeFeedback(evidence={self.evidence_id}, type={self.feedback_type}, level={self.maturity_level})>"


# =============================================================================
# Consultant Portal Enums
# =============================================================================

class ConsultantClientRole(str, Enum):
    """Role a consultant has for a specific client organisation."""
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid role values as strings."""
        return [role.value for role in cls]


class ConsultantClientStatus(str, Enum):
    """Status of a consultant-client relationship."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING = "pending"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid status values as strings."""
        return [status.value for status in cls]


class InviteStatus(str, Enum):
    """Unified status enum for all invitation types (consultant and org member)."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid status values as strings."""
        return [status.value for status in cls]


# Backwards-compatible aliases
ConsultantInviteStatus = InviteStatus
OrgInviteStatus = InviteStatus


# =============================================================================
# Audit Engagement Enums
# =============================================================================

class AuditEngagementStatus(str, Enum):
    """Status workflow for audit engagements.

    Workflow Progression:
        DRAFT -> ACTIVE -> UNDER_REVIEW -> CLOSED
    """
    DRAFT = "draft"
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    CLOSED = "closed"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid status values as strings."""
        return [status.value for status in cls]


# =============================================================================
# Risk Assessment Enums
# =============================================================================

class TreatmentStatus(str, Enum):
    """Risk treatment workflow status values.

    Workflow Progression:
        IDENTIFIED -> ANALYSED -> TREATING -> TREATED -> MONITORING

    Special States:
        - ACCEPTED: Risk has been formally accepted (no further treatment)

    State Descriptions:
        - IDENTIFIED: Risk has been identified but not yet analysed
        - ANALYSED: Risk has been analysed and scored
        - TREATING: Risk treatment actions are in progress
        - TREATED: Risk treatment actions are complete
        - ACCEPTED: Risk has been formally accepted
        - MONITORING: Risk is being actively monitored
    """
    IDENTIFIED = "identified"
    ANALYSED = "analysed"
    TREATING = "treating"
    TREATED = "treated"
    ACCEPTED = "accepted"
    MONITORING = "monitoring"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid status values as strings."""
        return [status.value for status in cls]

    @classmethod
    def workflow_states(cls) -> List["TreatmentStatus"]:
        """Return the primary workflow states in order."""
        return [
            cls.IDENTIFIED,
            cls.ANALYSED,
            cls.TREATING,
            cls.TREATED,
            cls.MONITORING,
        ]


# =============================================================================
# Consultant Portal Models
# =============================================================================

class ConsultantProfile(Base):
    """ConsultantProfile model - links a user to consultant capabilities.

    A consultant can manage multiple client organisations from a single dashboard.
    This model tracks the consultant's subscription tier (max_clients) and
    their consulting firm details.

    The max_clients field enforces subscription tier limits:
        - Starter: 5 clients
        - Professional: 20 clients (default)
        - Enterprise: unlimited (999)
    """
    __tablename__ = "consultant_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    company_name = Column(String(255), nullable=True)  # Consultant's firm name
    is_active = Column(Boolean, default=True, nullable=False)
    max_clients = Column(Integer, default=20, nullable=False)  # Subscription tier limit
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="consultant_profile")
    client_relationships = relationship("ConsultantClientRelationship", back_populates="consultant", cascade="all, delete-orphan")
    invites = relationship("ConsultantInvite", back_populates="consultant", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ConsultantProfile(id={self.id}, user_id={self.user_id}, company={self.company_name})>"

    def active_client_count(self) -> int:
        """Return count of active client relationships."""
        return len([r for r in self.client_relationships if r.status == ConsultantClientStatus.ACTIVE.value])

    def can_add_client(self) -> bool:
        """Check if consultant can add another client (subscription tier check)."""
        return self.active_client_count() < self.max_clients


class ConsultantClientRelationship(Base):
    """ConsultantClientRelationship model - maps consultant to client organisations.

    This is the core multi-tenancy junction table that allows consultants to
    manage multiple organisations. The role field controls what actions the
    consultant can perform within the client organisation.

    Roles:
        - admin: Full access, can manage users and settings
        - editor: Can modify controls, evidence, and assignments
        - viewer: Read-only access to client data

    Status:
        - active: Relationship is active
        - suspended: Temporarily disabled (e.g., payment issue)
        - pending: Awaiting client acceptance
    """
    __tablename__ = "consultant_client_relationships"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultant_id = Column(UUID(as_uuid=True), ForeignKey("consultant_profiles.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(20), nullable=False, default='editor')
    status = Column(String(20), nullable=False, default='pending')
    invited_at = Column(DateTime(timezone=False), server_default=func.now())
    accepted_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    consultant = relationship("ConsultantProfile", back_populates="client_relationships")
    organization = relationship("Organization", back_populates="consultant_relationships")

    def __repr__(self):
        return f"<ConsultantClientRelationship(consultant={self.consultant_id}, org={self.organization_id}, role={self.role})>"

    def is_active(self) -> bool:
        """Check if relationship is currently active."""
        return self.status == ConsultantClientStatus.ACTIVE.value


class InviteMixin:
    """Shared behaviour for invitation models (ConsultantInvite, OrganizationInvite)."""

    def is_expired(self) -> bool:
        """Check if invite has expired."""
        from datetime import datetime, timezone as tz
        if not self.expires_at:
            return False
        # Use timezone-aware comparison; strip tzinfo for naive DB columns
        now = datetime.now(tz.utc).replace(tzinfo=None)
        return now > self.expires_at

    def is_pending(self) -> bool:
        """Check if invite is still pending and not expired."""
        return self.status == InviteStatus.PENDING.value and not self.is_expired()


class ConsultantInvite(InviteMixin, Base):
    """ConsultantInvite model - tracks pending client invitations.

    When a consultant invites a new client, an invite record is created with
    a unique token. The client can use this token to accept the invitation
    and create their organisation.

    The invite can be in one of four states:
        - pending: Awaiting client action
        - accepted: Client accepted, organisation created
        - expired: Invite link has expired
        - cancelled: Consultant cancelled the invite
    """
    __tablename__ = "consultant_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultant_id = Column(UUID(as_uuid=True), ForeignKey("consultant_profiles.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False)  # Invitee email address
    organization_name = Column(String(255), nullable=False)  # Proposed organisation name
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True)  # Pre-created org
    invite_token = Column(String(64), nullable=False, unique=True)  # Secure random token
    status = Column(String(20), nullable=False, default='pending')
    expires_at = Column(DateTime(timezone=False), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    consultant = relationship("ConsultantProfile", back_populates="invites")
    organization = relationship("Organization", foreign_keys=[organization_id])

    def __repr__(self):
        return f"<ConsultantInvite(id={self.id}, email={self.email}, status={self.status})>"


class OrganizationInvite(InviteMixin, Base):
    """OrganizationInvite model - tracks pending member invitations.

    When an org admin invites a new member, an invite record is created with
    a unique token. The invitee can use this token to accept the invitation
    and join the organisation with a specified role.

    The invite can be in one of four states:
        - pending: Awaiting invitee action
        - accepted: Invitee accepted, membership created
        - expired: Invite link has expired
        - cancelled: Admin cancelled the invite
    """
    __tablename__ = "organization_invites"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    invited_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    email = Column(String(255), nullable=False)
    role = Column(String(50), nullable=False, default='viewer')
    invite_token = Column(String(64), nullable=False, unique=True)
    status = Column(String(20), nullable=False, default='pending')
    custom_message = Column(Text, nullable=True)
    expires_at = Column(DateTime(timezone=False), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="invites")
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])

    def __repr__(self):
        return f"<OrganizationInvite(id={self.id}, email={self.email}, org={self.organization_id}, status={self.status})>"


# =============================================================================
# Audit Engagement Models (Issue #370 — Phase D)
# =============================================================================

class AuditEngagement(Base):
    """Audit Engagement Workspace — a named, framework-scoped audit project.

    An engagement represents a discrete audit or compliance review. It captures
    the frameworks in scope, the lifecycle status, and materialises a snapshot
    of in-scope controls at creation time.
    """
    __tablename__ = "audit_engagements"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(255), nullable=False)
    frameworks = Column(ARRAY(String), nullable=False, default=[])
    status = Column(String(20), nullable=False, default=AuditEngagementStatus.DRAFT.value)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="audit_engagements")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    control_scope = relationship("EngagementControlScope", back_populates="engagement", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<AuditEngagement(id={self.id}, name={self.name}, status={self.status})>"


class EngagementControlScope(Base):
    """Materialised snapshot of in-scope controls for an audit engagement.

    Created at engagement creation time by querying selected scoped controls
    filtered by the engagement's frameworks. This is an append-only snapshot —
    controls can be added later but the initial set is fixed at creation.
    """
    __tablename__ = "engagement_control_scope"
    __table_args__ = (
        UniqueConstraint('engagement_id', 'scoped_control_id', name='uq_engagement_scoped_control'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    engagement_id = Column(UUID(as_uuid=True), ForeignKey("audit_engagements.id", ondelete="CASCADE"), nullable=False)
    scoped_control_id = Column(UUID(as_uuid=True), ForeignKey("scoped_controls.id", ondelete="CASCADE"), nullable=False)
    added_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    engagement = relationship("AuditEngagement", back_populates="control_scope")
    scoped_control = relationship("ScopedControl")

    def __repr__(self):
        return f"<EngagementControlScope(engagement={self.engagement_id}, control={self.scoped_control_id})>"


# =============================================================================
# Subscription Tier Enum
# =============================================================================

class SubscriptionTier(str, Enum):
    """User subscription tier levels.

    Tier limits:
        - FREE: 1 organisation, 5 team members
        - PROFESSIONAL: 10 organisations, 50 team members
        - ENTERPRISE: Unlimited organisations and team members
    """
    FREE = "free"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"

    @classmethod
    def values(cls) -> List[str]:
        """Return all valid tier values as strings."""
        return [tier.value for tier in cls]


# =============================================================================
# Vendor Management Enums
# =============================================================================

class VendorStatus(str, Enum):
    """Status workflow for third-party vendors.

    Workflow Progression:
        PROSPECT -> ACTIVE -> UNDER_REVIEW -> APPROVED -> SUSPENDED -> OFFBOARDED
    """
    PROSPECT = "prospect"
    ACTIVE = "active"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"

    @classmethod
    def values(cls) -> List[str]:
        return [s.value for s in cls]


class VendorCriticality(str, Enum):
    """Criticality level of a vendor to the organisation."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @classmethod
    def values(cls) -> List[str]:
        return [c.value for c in cls]


class VendorAssessmentStatus(str, Enum):
    """Status of a vendor assessment.

    AI assessment lifecycle: pending -> running -> completed | failed.
    Legacy manual lifecycle: scheduled -> in_progress -> completed | cancelled.
    """
    PENDING = "pending"
    RUNNING = "running"
    FAILED = "failed"
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    @classmethod
    def values(cls) -> List[str]:
        return [s.value for s in cls]


class VendorCertificationStatus(str, Enum):
    """Status of a vendor certification."""
    VALID = "valid"
    EXPIRED = "expired"
    REVOKED = "revoked"
    PENDING = "pending"

    @classmethod
    def values(cls) -> List[str]:
        return [s.value for s in cls]


# =============================================================================
# User Subscription Model
# =============================================================================

class UserSubscription(Base):
    """UserSubscription model - tracks user subscription tiers and limits.

    Each user has at most one subscription (enforced by unique constraint on user_id).
    If no subscription exists, the user is treated as having a free tier subscription.

    Tier Limits:
        - free: 1 organisation, 5 team members
        - professional: 10 organisations, 50 team members
        - enterprise: 999 organisations, 999 team members (effectively unlimited)

    Stripe Integration:
        The stripe_customer_id and stripe_subscription_id fields are for future
        Stripe billing integration. They are nullable to support free tier users.
    """
    __tablename__ = "user_subscriptions"

    id: int = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    tier: str = Column(String(20), nullable=False, default='free')
    max_organisations: int = Column(Integer, nullable=False, default=1)
    max_team_members: int = Column(Integer, nullable=False, default=5)
    is_active: bool = Column(Boolean, nullable=False, default=True)
    stripe_customer_id: Optional[str] = Column(String(255), nullable=True)
    stripe_subscription_id: Optional[str] = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="subscription")

    def __repr__(self):
        return f"<UserSubscription(id={self.id}, user_id={self.user_id}, tier={self.tier})>"

    def can_create_organisation(self, current_count: int) -> bool:
        """Check if user can create another organisation."""
        if not self.is_active:
            return False
        return current_count < self.max_organisations

    def can_invite_member(self, current_count: int) -> bool:
        """Check if user can invite another team member."""
        if not self.is_active:
            return False
        return current_count < self.max_team_members


# =============================================================================
# Risk Assessment Models
# =============================================================================

class RiskAssessment(Base):
    """RiskAssessment model - organisation-scoped risk assessments.

    Tracks the assessment of SCF-aligned risk codes (e.g., R-AC-1) for each
    organisation. Each risk code maps to multiple controls via the
    SCFCatalogControl.risk_codes JSONB field.

    Risk Scoring:
        Uses a 5x5 likelihood/impact matrix where:
        - Likelihood: 1 (Rare) to 5 (Almost Certain)
        - Impact: 1 (Insignificant) to 5 (Catastrophic)
        - Score = Likelihood × Impact (1-25)

        Risk Levels:
        - Low: 1-4 (Green)
        - Medium: 5-9 (Yellow)
        - High: 10-16 (Orange)
        - Critical: 17-25 (Red)

    Treatment Workflow:
        IDENTIFIED -> ANALYSED -> TREATING -> TREATED -> MONITORING
        (ACCEPTED can be set at any stage to formally accept the risk)
    """
    __tablename__ = "risk_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    risk_code = Column(String(20), nullable=False)  # e.g., "R-AC-1"

    # Inherent risk (1-5 scale) - risk without controls
    likelihood = Column(Integer, nullable=True)  # CHECK 1-5 enforced in DB
    impact = Column(Integer, nullable=True)  # CHECK 1-5 enforced in DB

    # Residual risk (after controls) - risk with current controls
    residual_likelihood = Column(Integer, nullable=True)
    residual_impact = Column(Integer, nullable=True)

    # Treatment workflow
    treatment_status = Column(String(30), nullable=False, default='identified')
    treatment_plan = Column(Text, nullable=True)
    treatment_due_date = Column(Date, nullable=True)

    # Ownership
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Review tracking
    next_review_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)

    # Audit timestamps
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization")
    owner = relationship("User", foreign_keys=[owner_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    def __repr__(self):
        return f"<RiskAssessment(org={self.organization_id}, risk_code={self.risk_code}, status={self.treatment_status})>"

    @property
    def inherent_risk_score(self) -> Optional[int]:
        """Calculate inherent risk score (likelihood × impact)."""
        if self.likelihood is not None and self.impact is not None:
            return self.likelihood * self.impact
        return None

    @property
    def residual_risk_score(self) -> Optional[int]:
        """Calculate residual risk score (residual_likelihood × residual_impact)."""
        if self.residual_likelihood is not None and self.residual_impact is not None:
            return self.residual_likelihood * self.residual_impact
        return None

    @staticmethod
    def _score_to_level(score: int, low_max: int = 4, medium_max: int = 9, high_max: int = 16) -> str:
        """Convert a risk score to a level string using configurable thresholds."""
        if score <= low_max:
            return "low"
        if score <= medium_max:
            return "medium"
        if score <= high_max:
            return "high"
        return "critical"

    def get_inherent_risk_level(self, low_max: int = 4, medium_max: int = 9, high_max: int = 16) -> Optional[str]:
        """Get the inherent risk level based on score."""
        score = self.inherent_risk_score
        if score is None:
            return None
        return self._score_to_level(score, low_max, medium_max, high_max)

    def get_residual_risk_level(self, low_max: int = 4, medium_max: int = 9, high_max: int = 16) -> Optional[str]:
        """Get the residual risk level based on score."""
        score = self.residual_risk_score
        if score is None:
            return None
        return self._score_to_level(score, low_max, medium_max, high_max)


class CustomRiskDefinition(Base):
    """Organization-defined custom risk definitions.

    Stores metadata (title, description, category) for risks created by
    organizations, complementing the static SCF risk catalog. Custom risks
    use auto-generated codes in the format R-ORG-N.
    """
    __tablename__ = "custom_risk_definitions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    risk_code = Column(String(20), nullable=False)
    title = Column(String(100), nullable=False)
    description = Column(Text, nullable=False)
    category_name = Column(String(50), nullable=False, default='Custom')
    category_color = Column(String(7), nullable=False, default='#6b7280')

    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return f"<CustomRiskDefinition(org={self.organization_id}, code={self.risk_code}, title={self.title})>"


class CustomRiskControlMapping(Base):
    """Manual mapping between custom risks and scoped controls.

    Allows organizations to link their custom risks (R-ORG-N) to
    scoped controls, since custom risks have no automatic SCF catalog mappings.
    """
    __tablename__ = "custom_risk_control_mappings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    risk_code = Column(String(20), nullable=False)
    scf_id = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Relationships
    organization = relationship("Organization")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return f"<CustomRiskControlMapping(org={self.organization_id}, risk={self.risk_code}, scf_id={self.scf_id})>"


# =============================================================================
# Vendor Management Models (TPRM)
# =============================================================================

class Vendor(Base):
    """Vendor model - represents a third-party vendor/supplier.

    Tracks vendor details, contract information, risk scoring, and
    data classification for Third-Party Risk Management (TPRM).

    Status Workflow:
        prospect -> active -> under_review -> approved -> suspended -> offboarded
    """
    __tablename__ = "vendors"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    # Core fields
    name = Column(String(255), nullable=False)
    description = Column(Text)
    website = Column(String(500))
    category = Column(String(100))
    status = Column(String(30), nullable=False, default='prospect')
    criticality = Column(String(20), nullable=False, default='low')

    # Contact information
    contact_name = Column(String(255))
    contact_email = Column(String(255))
    contact_phone = Column(String(50))

    # Contract details
    contract_start_date = Column(Date)
    contract_end_date = Column(Date)
    contract_value = Column(Numeric(12, 2))

    # Risk scoring — one authoritative score, written only from the latest
    # *completed* AI assessment. risk_score_source records which assessment
    # set it (provenance); next_review_date drives the annual-review loop.
    risk_score = Column(Integer)
    risk_level = Column(String(20))
    risk_score_source = Column(UUID(as_uuid=True), ForeignKey("vendor_assessments.id", ondelete="SET NULL", use_alter=True, name="fk_vendors_risk_score_source"), nullable=True)
    risk_scored_at = Column(DateTime(timezone=False))
    next_review_date = Column(Date)
    data_classification = Column(String(50))

    # Audit timestamps and user FKs
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    organization = relationship("Organization", back_populates="vendors")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])
    assessments = relationship(
        "VendorAssessment",
        back_populates="vendor",
        foreign_keys="VendorAssessment.vendor_id",
        cascade="all, delete-orphan",
    )
    risk_score_source_assessment = relationship("VendorAssessment", foreign_keys=[risk_score_source], viewonly=True)
    certifications = relationship("VendorCertification", back_populates="vendor", cascade="all, delete-orphan")
    reports = relationship("VendorReport", back_populates="vendor", cascade="all, delete-orphan")
    claim_verifications = relationship("VendorClaimVerification", back_populates="vendor", cascade="all, delete-orphan")
    action_items = relationship("VendorActionItem", back_populates="vendor", cascade="all, delete-orphan")
    compensating_controls = relationship("VendorCompensatingControl", back_populates="vendor", cascade="all, delete-orphan")
    # Systems that reference this vendor. passive_deletes defers to the DB-level
    # ON DELETE SET NULL on systems.vendor_id — deleting a vendor never cascades
    # into (or deletes) its systems; their vendor_id is simply nulled.
    systems = relationship(
        "System",
        back_populates="linked_vendor",
        foreign_keys="System.vendor_id",
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<Vendor(id={self.id}, name={self.name}, status={self.status})>"


class VendorAssessment(Base):
    """VendorAssessment model - THE single vendor assessment record.

    One row per assessment run (AI-driven or manual). AI assessments carry a
    job_id and progress through pending -> running -> completed | failed,
    with the full report (markdown + JSON), RAG status, recommendation and
    research sources stored directly on the row. Legacy manual assessments
    (job_id IS NULL) keep the scheduled/in_progress/completed/cancelled
    lifecycle.

    Residual (authoritative) risk lives in final_risk_score / risk_level;
    inherent risk in inherent_risk_score / inherent_risk_level.
    """
    __tablename__ = "vendor_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)

    # Assessment details
    assessment_type = Column(String(50), nullable=False, default='initial')
    assessment_date = Column(Date, nullable=False)
    status = Column(String(30), nullable=False, default='scheduled')

    # AI assessment job tracking (nullable for legacy/manual rows)
    job_id = Column(String(50), unique=True, nullable=True)
    started_at = Column(DateTime(timezone=False))
    completed_at = Column(DateTime(timezone=False))
    error_message = Column(Text)
    triggered_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Assessment inputs
    data_role = Column(String(30))
    services_used = Column(Text)
    client_name = Column(String(255))
    additional_context = Column(Text)

    # AI assessment outcome + report (content lives here, not in vendor_reports)
    rag_status = Column(String(10))            # GREEN / AMBER / RED
    recommendation = Column(String(30))        # APPROVED / CONDITIONAL / REJECTED
    executive_summary = Column(Text)
    report_markdown = Column(Text)
    report_json = Column(JSONB)
    research_sources = Column(JSONB)
    processing_time_ms = Column(Integer)

    # CIA scores (1-5 scale)
    confidentiality_score = Column(Integer)
    integrity_score = Column(Integer)
    availability_score = Column(Integer)

    # Risk scoring fields (Issue #60)
    breach_score = Column(Integer)
    certification_score = Column(Integer)
    cve_score = Column(Integer)
    regulatory_score = Column(Integer)
    data_handling_score = Column(Integer)
    likelihood = Column(Integer)  # 1-5
    impact = Column(Integer)  # 1-5
    final_risk_score = Column(Integer)  # 1-25
    risk_level = Column(String(20))  # low, medium, high, critical
    ai_analysis = Column(Text)  # Claude-generated summary

    # Inherent/residual risk model (DPSIA Enhancement - Phase 2)
    inherent_risk_score = Column(Integer)
    inherent_risk_level = Column(String(20))
    control_effectiveness_pct = Column(Integer)  # 0-100

    # Findings and outcome
    findings = Column(Text)
    risk_rating = Column(String(20))
    next_assessment_date = Column(Date)

    # Assessor
    assessor_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Audit timestamps and user FKs
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    vendor = relationship("Vendor", back_populates="assessments", foreign_keys=[vendor_id])
    assessor = relationship("User", foreign_keys=[assessor_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])
    triggered_by = relationship("User", foreign_keys=[triggered_by_user_id])

    def __repr__(self):
        return f"<VendorAssessment(vendor={self.vendor_id}, type={self.assessment_type}, status={self.status})>"


class VendorCertification(Base):
    """VendorCertification model - tracks vendor compliance certifications.

    Records certifications like ISO 27001, SOC 2, etc. held by vendors.
    """
    __tablename__ = "vendor_certifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)

    # Certification details
    certification_name = Column(String(255), nullable=False)
    certification_body = Column(String(255))
    certificate_number = Column(String(100))
    status = Column(String(30), nullable=False, default='valid')

    # Dates
    issue_date = Column(Date)
    expiry_date = Column(Date)

    # Scope and verification
    scope = Column(Text)
    verification_url = Column(String(500))

    # Audit timestamps and user FKs
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    updated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))

    # Relationships
    vendor = relationship("Vendor", back_populates="certifications")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    updated_by = relationship("User", foreign_keys=[updated_by_user_id])

    def __repr__(self):
        return f"<VendorCertification(vendor={self.vendor_id}, name={self.certification_name}, status={self.status})>"


# =============================================================================
# Vendor Research Results (AI-Powered TPRM)
# =============================================================================

class VendorResearchResult(Base):
    """VendorResearchResult model - stores AI-powered vendor research job results.

    Each record represents a single research job that queries external sources
    (HIBP, CISA KEV, CVE/NVD, regulatory) for vendor security intelligence.

    Job Status Workflow:
        pending -> running -> completed | partial | failed
    """
    __tablename__ = "vendor_research_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    job_id = Column(String(50), unique=True, nullable=False)
    status = Column(String(30), nullable=False, default='pending')

    # Per-source results (JSONB)
    hibp_results = Column(JSONB, default={})
    cisa_kev_results = Column(JSONB, default={})
    cve_nvd_results = Column(JSONB, default={})
    regulatory_results = Column(JSONB, default={})

    # Aggregated output
    summary = Column(Text)
    risk_indicators = Column(JSONB, default={})
    overall_risk_signal = Column(String(20))

    # Per-source status tracking
    source_statuses = Column(JSONB, default={})
    errors = Column(JSONB, default=[])

    # Research metadata
    researched_domain = Column(String(500))
    triggered_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Timestamps
    started_at = Column(DateTime(timezone=False))
    completed_at = Column(DateTime(timezone=False))
    created_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    vendor = relationship("Vendor")
    triggered_by = relationship("User", foreign_keys=[triggered_by_user_id])

    def __repr__(self):
        return f"<VendorResearchResult(vendor={self.vendor_id}, job={self.job_id}, status={self.status})>"


class VendorReport(Base):
    """VendorReport model - generated assessment reports for audit evidence."""
    __tablename__ = "vendor_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("vendor_assessments.id", ondelete="SET NULL"))
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)

    # Report content
    report_type = Column(String(50), nullable=False, default='comprehensive')
    title = Column(String(255), nullable=False)
    content_markdown = Column(Text, nullable=False)
    content_json = Column(JSONB)

    # Risk summary
    risk_score = Column(Integer)
    risk_level = Column(String(20))
    recommendation = Column(String(50))

    # Versioning
    version = Column(Integer, default=1)

    # Audit
    generated_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    vendor = relationship("Vendor", back_populates="reports")
    assessment = relationship("VendorAssessment")
    organization = relationship("Organization")
    generated_by = relationship("User", foreign_keys=[generated_by_user_id])

    def __repr__(self):
        return f"<VendorReport(vendor={self.vendor_id}, type={self.report_type}, version={self.version})>"


# =============================================================================
# Vendor Claim Verification (DPSIA Enhancement - Phase 1)
# =============================================================================

class VendorClaimVerification(Base):
    """Tracks independent verification of vendor claims against research data."""
    __tablename__ = "vendor_claim_verifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("vendor_assessments.id", ondelete="SET NULL"), nullable=True)

    claim_type = Column(String(50), nullable=False)  # certification, breach_disclosure, compliance, security_control
    claim_description = Column(Text, nullable=False)
    verification_status = Column(String(30), nullable=False, default='unverified')  # confirmed, unverified, discrepancy, anomaly
    verification_source = Column(String(255))
    verification_detail = Column(Text)
    evidence_url = Column(String(500))

    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    vendor = relationship("Vendor", back_populates="claim_verifications")
    assessment = relationship("VendorAssessment")

    def __repr__(self):
        return f"<VendorClaimVerification(vendor={self.vendor_id}, type={self.claim_type}, status={self.verification_status})>"


# =============================================================================
# CIA Control Breakdown (DPSIA Enhancement - Phase 2)
# =============================================================================

class VendorCIAControl(Base):
    """Per-control breakdown within CIA triad pillars for vendor assessments."""
    __tablename__ = "vendor_cia_controls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("vendor_assessments.id", ondelete="CASCADE"), nullable=False)

    pillar = Column(String(20), nullable=False)  # confidentiality, integrity, availability
    control_name = Column(String(255), nullable=False)  # e.g., "Encryption at Rest", "Access Control Model"
    control_category = Column(String(100))  # e.g., "encryption", "access_control", "audit_logging"
    score = Column(Integer)  # 1-5
    detail = Column(Text)
    evidence = Column(Text)

    created_at = Column(DateTime(timezone=False), server_default=func.now())

    # Relationships
    assessment = relationship("VendorAssessment")

    def __repr__(self):
        return f"<VendorCIAControl(assessment={self.assessment_id}, pillar={self.pillar}, control={self.control_name})>"


# =============================================================================
# Action Items and Compensating Controls (DPSIA Enhancement - Phase 3)
# =============================================================================

class VendorActionItem(Base):
    """Auto-generated and manual action items from vendor assessments."""
    __tablename__ = "vendor_action_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("vendor_assessments.id", ondelete="SET NULL"), nullable=True)
    report_id = Column(UUID(as_uuid=True), ForeignKey("vendor_reports.id", ondelete="SET NULL"), nullable=True)

    title = Column(String(255), nullable=False)
    description = Column(Text)
    priority = Column(String(20), nullable=False, default='medium')  # critical, high, medium, low
    status = Column(String(30), nullable=False, default='open')  # open, in_progress, completed, cancelled
    category = Column(String(100))

    owner_name = Column(String(255))
    owner_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    due_date = Column(Date)
    completed_date = Column(Date)
    auto_generated = Column(Boolean, default=False)

    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    vendor = relationship("Vendor", back_populates="action_items")
    assessment = relationship("VendorAssessment")
    report = relationship("VendorReport")
    owner_user = relationship("User", foreign_keys=[owner_user_id])

    def __repr__(self):
        return f"<VendorActionItem(vendor={self.vendor_id}, title={self.title}, status={self.status})>"


class VendorCompensatingControl(Base):
    """Documents compensating controls when vendors fail certification minimum bar."""
    __tablename__ = "vendor_compensating_controls"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vendor_id = Column(UUID(as_uuid=True), ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False)
    assessment_id = Column(UUID(as_uuid=True), ForeignKey("vendor_assessments.id", ondelete="SET NULL"), nullable=True)

    gap_description = Column(Text, nullable=False)
    compensating_control = Column(Text, nullable=False)
    effectiveness_rating = Column(String(20), nullable=False, default='partial')  # full, partial, minimal
    risk_reduction_notes = Column(Text)

    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    vendor = relationship("Vendor", back_populates="compensating_controls")
    assessment = relationship("VendorAssessment")

    def __repr__(self):
        return f"<VendorCompensatingControl(vendor={self.vendor_id}, effectiveness={self.effectiveness_rating})>"


# =============================================================================
# Audit Log Model
# =============================================================================

class AuditLog(Base):
    """AuditLog model - immutable record of entity-level changes.

    Captures field-level change history for any auditable entity (controls,
    evidence, vendors, risk assessments, etc.) within an organisation.

    The entity_type/entity_id pair is a generic polymorphic reference
    (no FK constraint) so the same table can track changes across all
    entity types without schema coupling.

    Fields:
        - entity_type: The type of entity changed (e.g., 'scoped_control', 'vendor')
        - entity_id: UUID of the changed entity
        - scf_id: Optional SCF control ID for control-related changes
        - action: The action performed (e.g., 'create', 'update', 'delete')
        - field_name: The specific field that changed (nullable for create/delete)
        - old_value/new_value: Serialised before/after values
        - ip_address: Client IP (supports IPv4 and IPv6, max 45 chars)
    """
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(UUID(as_uuid=True), nullable=False)
    scf_id = Column(String(20), nullable=True)
    action = Column(String(20), nullable=False)
    field_name = Column(String(100), nullable=True)
    old_value = Column(Text, nullable=True)
    new_value = Column(Text, nullable=True)
    changed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=False)
    changed_at = Column(DateTime(timezone=True), server_default=func.now())
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    action_source = Column(String(20), nullable=True)  # ui, api_key, mcp, system
    request_id = Column(UUID(as_uuid=True), nullable=True)  # Correlates middleware + field-level records

    # Relationships
    organization = relationship("Organization", back_populates="audit_logs")
    changed_by = relationship("User", foreign_keys=[changed_by_user_id])

    def __repr__(self):
        return f"<AuditLog(entity={self.entity_type}/{self.entity_id}, action={self.action}, field={self.field_name})>"


class EvidenceFile(Base):
    """Evidence File model - tracks uploaded evidence artifacts in S3.

    Stores metadata only; actual files live in S3 at the path in s3_key.
    Links to EvidenceTracking via evidence_id (string match, no FK).
    Supports soft deletion for retention/audit requirements.
    """
    __tablename__ = "evidence_files"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)  # ERL evidence ID (e.g., "ERL-001")

    # File metadata (never the actual file)
    filename = Column(String(255), nullable=False)
    s3_key = Column(String(1024), nullable=False, unique=True)
    content_type = Column(String(100), nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    sha256_hash = Column(String(64), nullable=True)  # Computed client-side before upload

    # Classification
    classification = Column(String(20), default="internal", server_default="internal", nullable=False)

    # Malware scan status (#217)
    scan_status = Column(String(20), default="pending", server_default="pending", nullable=False)
    scan_details = Column(JSONB, nullable=True)  # {"engine": "clamav", "signature": "...", "message": "..."}

    # Lifecycle
    uploaded_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    uploaded_at = Column(DateTime(timezone=False), default=datetime.utcnow, server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=False), nullable=True)
    is_deleted = Column(Boolean, default=False, server_default="false", nullable=False)
    deleted_at = Column(DateTime(timezone=False), nullable=True)
    deleted_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Review/approval workflow (#482)
    review_status = Column(String(20), default="not_reviewed", server_default="not_reviewed", nullable=False)
    reviewed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at = Column(DateTime(timezone=False), nullable=True)
    review_notes = Column(Text, nullable=True)

    # Relationships
    organization = relationship("Organization", back_populates="evidence_files")
    uploaded_by = relationship("User", foreign_keys=[uploaded_by_user_id])
    deleted_by = relationship("User", foreign_keys=[deleted_by_user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_user_id])
    validation_result = relationship("EvidenceValidationResult", back_populates="evidence_file", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<EvidenceFile(id={self.id}, evidence_id={self.evidence_id}, filename={self.filename})>"


class WebhookEndpoint(Base):
    """Webhook endpoint for evidence inbox ingestion.

    External systems POST evidence payloads to per-org webhook URLs.
    HMAC-SHA256 signature validation authenticates requests.
    The plaintext secret is stored (not just a hash) because HMAC
    verification requires the server to recompute the signature.
    """
    __tablename__ = "webhook_endpoints"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    secret = Column(String(70), nullable=False)  # plaintext "whsec_..." for HMAC verification
    secret_prefix = Column(String(12), nullable=False)  # first 12 chars for display
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)
    allowed_evidence_ids = Column(JSON, nullable=True)  # null = allow any evidence_id
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    last_delivery_at = Column(DateTime(timezone=False), nullable=True)
    delivery_count = Column(Integer, default=0, server_default="0", nullable=False)
    rate_limit_per_minute = Column(Integer, nullable=True)  # Per-endpoint rate limit (null = use org/global default)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization", back_populates="webhook_endpoints")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    deliveries = relationship("WebhookDelivery", back_populates="webhook_endpoint", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<WebhookEndpoint(id={self.id}, name={self.name}, active={self.is_active})>"


class WebhookDelivery(Base):
    """Record of a single webhook delivery attempt.

    Every inbound request to the evidence inbox creates a delivery record
    regardless of whether it succeeds or fails. This provides a complete
    audit trail of all webhook traffic.
    """
    __tablename__ = "webhook_deliveries"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_endpoint_id = Column(UUID(as_uuid=True), ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), nullable=False)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)
    event_id = Column(String(100), nullable=True)  # sender-provided idempotency key
    payload_json = Column(JSON, nullable=True)
    content_type = Column(String(100), nullable=True)
    signature_valid = Column(Boolean, nullable=False)
    status = Column(String(20), default="received", server_default="received", nullable=False)
    error_message = Column(Text, nullable=True)
    evidence_file_id = Column(UUID(as_uuid=True), ForeignKey("evidence_files.id", ondelete="SET NULL"), nullable=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    processed_at = Column(DateTime(timezone=False), nullable=True)

    # Relationships
    webhook_endpoint = relationship("WebhookEndpoint", back_populates="deliveries")
    evidence_file = relationship("EvidenceFile")

    def __repr__(self):
        return f"<WebhookDelivery(id={self.id}, endpoint={self.webhook_endpoint_id}, status={self.status})>"


class EvidenceValidationResult(Base):
    """Validation result for a single evidence file.

    Stores structured findings from the four validation rules:
    1. catalog_exists — evidence_id is a known ERL entry
    2. content_type_ok — MIME type in allowed set
    3. field_coverage — JSON payload field completeness
    4. freshness — file age vs collection frequency

    One result per EvidenceFile (unique constraint on evidence_file_id).
    Overall status = worst level across all findings.
    """
    __tablename__ = "evidence_validation_results"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_file_id = Column(UUID(as_uuid=True), ForeignKey("evidence_files.id", ondelete="CASCADE"), nullable=False, unique=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False)  # valid, warning, partial, invalid
    completeness_score = Column(Numeric(5, 4), nullable=True)  # 0.0-1.0
    findings = Column(JSONB, nullable=False, default=list)
    validation_source = Column(String(30), nullable=False)  # webhook, manual_upload
    validated_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    # Relationships
    evidence_file = relationship("EvidenceFile", back_populates="validation_result")
    organization = relationship("Organization", back_populates="evidence_validation_results")

    def __repr__(self):
        return f"<EvidenceValidationResult(file={self.evidence_file_id}, status={self.status})>"


class EvidenceHealthConfig(Base):
    """Per-org evidence staleness threshold configuration.

    Allows organisations to override default staleness thresholds
    for specific evidence items. Used by the Evidence Health Dashboard (#220).
    """
    __tablename__ = "evidence_health_config"
    __table_args__ = (
        UniqueConstraint('organization_id', 'evidence_id', name='uq_evidence_health_config_org_evidence'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)
    staleness_warning_days = Column(Integer, nullable=False, default=30)
    staleness_critical_days = Column(Integer, nullable=False, default=60)
    created_at = Column(DateTime(timezone=False), server_default=func.now())
    updated_at = Column(DateTime(timezone=False), server_default=func.now(), onupdate=func.now())

    # Relationships
    organization = relationship("Organization")

    def __repr__(self):
        return f"<EvidenceHealthConfig(org={self.organization_id}, evidence={self.evidence_id})>"


class EvidenceAssessment(Base):
    """AI-based content assessment of an evidence file.

    Evaluates whether uploaded evidence content actually satisfies
    the control requirements it's mapped to. Runs asynchronously
    after upload, stores structured findings with full audit trail.

    One assessment per EvidenceFile (unique constraint on evidence_file_id).
    Assessment is advisory only — never auto-approves or rejects.
    """
    __tablename__ = "evidence_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_file_id = Column(UUID(as_uuid=True), ForeignKey("evidence_files.id", ondelete="CASCADE"), nullable=False, unique=True)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)  # ERL evidence ID

    # Assessment result
    status = Column(String(20), default="pending", server_default="pending", nullable=False)  # pending, processing, sufficient, partial, insufficient, error
    relevance_score = Column(Numeric(5, 2), nullable=True)  # 0.00-100.00
    findings = Column(JSONB, nullable=False, default=list)  # [{category, level, message, control_id, suggestion}]
    summary = Column(Text, nullable=True)  # Human-readable assessment summary

    # Audit trail (frozen inference chain)
    model_id = Column(String(100), nullable=True)  # e.g. "claude-sonnet-4-6"
    prompt_hash = Column(String(64), nullable=True)  # SHA-256 of the full prompt
    control_context_hash = Column(String(64), nullable=True)  # SHA-256 of assembled control context
    framework_version = Column(String(50), nullable=True)  # SCF catalog version used
    input_token_count = Column(Integer, nullable=True)
    output_token_count = Column(Integer, nullable=True)
    cost_cents = Column(Numeric(8, 4), nullable=True)
    processing_time_ms = Column(Integer, nullable=True)

    # Source tracking
    assessment_source = Column(String(30), nullable=False, default="on_demand")  # on_demand, auto, bulk
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Lifecycle
    assessed_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    # Relationships
    evidence_file = relationship("EvidenceFile", backref="assessment")
    organization = relationship("Organization", back_populates="evidence_assessments")
    requested_by = relationship("User", foreign_keys=[requested_by_user_id])

    def __repr__(self):
        return f"<EvidenceAssessment(file={self.evidence_file_id}, status={self.status})>"


class EvidenceWindowAssessment(Base):
    """Windowed multi-file AI assessment of evidence.

    Scores an evidence object over a time window (derived from
    EvidenceTracking.frequency via STALENESS_THRESHOLDS) as a portfolio of
    files, rather than scoring each file in isolation. Complements per-file
    EvidenceAssessment which remains as a diagnostic layer.

    One row per (organization_id, evidence_id, window_start, window_end).
    The window_hash field fingerprints the file set inside the window so
    repeat assessments with unchanged content can cache-hit.
    """
    __tablename__ = "evidence_window_assessments"
    __table_args__ = (
        UniqueConstraint(
            'organization_id', 'evidence_id', 'window_start', 'window_end',
            name='uq_evidence_window_assessments_org_ev_window',
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    evidence_id = Column(String(50), nullable=False)

    # Window definition
    window_start = Column(DateTime(timezone=False), nullable=False)
    window_end = Column(DateTime(timezone=False), nullable=False)
    frequency_used = Column(String(20), nullable=False)

    # File + source + artifact-type portfolio snapshot
    file_ids = Column(JSONB, nullable=False, default=list, server_default="[]")
    source_coverage = Column(JSONB, nullable=False, default=dict, server_default="{}")
    artifact_type_coverage = Column(JSONB, nullable=False, default=dict, server_default="{}")
    expected_artifact_types = Column(JSONB, nullable=False, default=list, server_default="[]")

    # Assessment result
    # status values: pending, processing, sufficient, partial, insufficient,
    # insufficient_sample, error
    status = Column(String(30), default="pending", server_default="pending", nullable=False)
    relevance_score = Column(Numeric(5, 2), nullable=True)
    findings = Column(JSONB, nullable=False, default=list, server_default="[]")
    summary = Column(Text, nullable=True)

    # Audit trail (frozen inference chain)
    model_id = Column(String(100), nullable=True)
    prompt_hash = Column(String(64), nullable=True)
    control_context_hash = Column(String(64), nullable=True)
    framework_version = Column(String(50), nullable=True)
    window_hash = Column(String(64), nullable=True)
    input_token_count = Column(Integer, nullable=True)
    output_token_count = Column(Integer, nullable=True)
    cost_cents = Column(Numeric(8, 4), nullable=True)
    processing_time_ms = Column(Integer, nullable=True)

    # Source tracking
    assessment_source = Column(String(30), nullable=False, default="on_demand", server_default="on_demand")
    requested_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Lifecycle
    assessed_at = Column(DateTime(timezone=False), nullable=True)
    created_at = Column(DateTime(timezone=False), server_default=func.now(), nullable=False)

    # Per-window review workflow (M4 PR 1, #574). Mirrors the legacy
    # EvidenceFile.review_status fields. Coexists with them — no dual-write.
    # Cutover gated by ENABLE_PER_WINDOW_REVIEW (introduced in M4 PR 2).
    review_status = Column(
        String(20),
        default="not_reviewed",
        server_default="not_reviewed",
        nullable=False,
    )
    reviewed_by_user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    reviewed_at = Column(DateTime(timezone=False), nullable=True)
    review_notes = Column(Text, nullable=True)

    # Relationships
    organization = relationship("Organization")
    requested_by = relationship("User", foreign_keys=[requested_by_user_id])
    reviewed_by = relationship("User", foreign_keys=[reviewed_by_user_id])

    def __repr__(self):
        return (
            f"<EvidenceWindowAssessment(org={self.organization_id}, "
            f"evidence={self.evidence_id}, status={self.status})>"
        )


class ControlAssessmentComposite(Base):
    """Per-control rollup of EvidenceWindowAssessment rows.

    One row per (organization_id, scf_id). Computed asynchronously in Celery
    when EvidenceWindowAssessment rows transition to a terminal status.
    Materialised projection — never edited in place; always recomputed.

    See M3 design spec (#575) ISC-1..6 for the data-model contract and
    ISC-7..11 for the rollup algorithm encoded in
    ``services.composite_service``.
    """
    __tablename__ = "control_assessment_composites"
    __table_args__ = (
        UniqueConstraint(
            'organization_id', 'scf_id',
            name='uq_control_assessment_composites_org_scf',
        ),
        Index(
            'ix_control_assessment_composites_org_status',
            'organization_id', 'composite_status',
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    # scf_id matches SCFCatalogControl.scf_id (natural key, not FK because
    # catalog tables are read-only reference data).
    scf_id = Column(String(20), nullable=False)

    # composite_status enum values per ISC-4:
    #   sufficient, partial, insufficient, insufficient_sample,
    #   pending, no_evidence
    composite_status = Column(String(30), nullable=False)
    # 0.00-100.00; NULL when status in {pending, no_evidence,
    # insufficient_sample}.
    composite_score = Column(Numeric(5, 2), nullable=True)

    # Provenance — references back to the EvidenceWindowAssessment rows folded
    # into this composite. Stored as JSONB arrays rather than relationships
    # because the composite is a materialised projection.
    included_window_ids = Column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    included_evidence_ids = Column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    # Each entry is a dict with at least {evidence_id, reason}; may also carry
    # artifact_type when the gap is a missing mandatory artifact type.
    mandatory_gaps = Column(
        JSONB, nullable=False, default=list, server_default="[]",
    )

    # Bumped in code (composite_service.CURRENT_COMPUTATION_VERSION) when the
    # rollup algorithm changes. Older rows become eligible for unconditional
    # recompute on the next trigger.
    computation_version = Column(Integer, nullable=False, default=1)
    computed_at = Column(DateTime(timezone=False), nullable=False)
    created_at = Column(
        DateTime(timezone=False), server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=False),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    organization = relationship("Organization")

    def __repr__(self):
        return (
            f"<ControlAssessmentComposite(org={self.organization_id}, "
            f"scf_id={self.scf_id}, status={self.composite_status})>"
        )


class UserScopePreferences(Base):
    """Per-user, per-org audit scope preferences (persistent framework filter)."""
    __tablename__ = 'user_scope_preferences'

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    org_id = Column(UUID(as_uuid=True), ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False)
    active_frameworks = Column(ARRAY(String), nullable=False, server_default='{}')
    audit_mode_locked = Column(Boolean, nullable=False, default=False)
    audit_label = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint('user_id', 'org_id', name='uq_user_scope_preferences'),
    )


class CDMDocument(Base):
    __tablename__ = "cdm_documents"
    __table_args__ = (
        Index('ix_cdm_documents_org', 'organization_id'),
        Index('ix_cdm_documents_sha256', 'organization_id', 'sha256'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    original_filename = Column(String(512), nullable=False)
    mime_type = Column(String(100), nullable=False)
    sha256 = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    upload_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    kb_revision = Column(String(128), nullable=True)
    word_count = Column(Integer, nullable=True)
    kb_revision_at_ingest = Column(String(64), nullable=True)
    ingest_status = Column(String(20), default="pending", server_default="pending", nullable=False)
    ingest_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    organization = relationship("Organization")
    upload_user = relationship("User", foreign_keys=[upload_user_id])

    def __repr__(self):
        return f"<CDMDocument(id={self.id}, filename={self.original_filename}, status={self.ingest_status})>"


class CDMMapping(Base):
    __tablename__ = "cdm_mappings"
    __table_args__ = (
        Index('ix_cdm_mappings_org_status', 'organization_id', 'status'),
        Index('ix_cdm_mappings_control', 'organization_id', 'scoped_control_id'),
        Index('ix_cdm_mappings_document', 'cdm_document_id'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    scoped_control_id = Column(UUID(as_uuid=True), ForeignKey("scoped_controls.id", ondelete="CASCADE"), nullable=False)
    cdm_document_id = Column(UUID(as_uuid=True), ForeignKey("cdm_documents.id", ondelete="CASCADE"), nullable=False)
    section = Column(String(255), nullable=True)
    byte_offset_start = Column(Integer, nullable=False)
    byte_offset_end = Column(Integer, nullable=False)
    relevance_score = Column(Float, nullable=False)
    status = Column(String(20), default="proposed", server_default="proposed", nullable=False)
    kb_revision = Column(String(128), nullable=False)
    accepted_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    dismiss_reason = Column(Text, nullable=True)
    dismissed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    dismissed_at = Column(DateTime(timezone=True), nullable=True)
    excerpt = Column(Text, nullable=True)
    review_notes = Column(Text, nullable=True)
    last_reviewed_at = Column(DateTime(timezone=True), nullable=True)
    last_reviewed_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    organization = relationship("Organization")
    scoped_control = relationship("ScopedControl")
    document = relationship("CDMDocument")
    accepted_by_user = relationship("User", foreign_keys=[accepted_by_user_id])
    dismissed_by_user = relationship("User", foreign_keys=[dismissed_by_user_id])
    last_reviewed_by_user = relationship("User", foreign_keys=[last_reviewed_by_user_id])

    def __repr__(self):
        return f"<CDMMapping(id={self.id}, control={self.scoped_control_id}, status={self.status})>"
