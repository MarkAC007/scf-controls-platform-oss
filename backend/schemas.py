"""
Pydantic schemas for request/response validation.
These define the API contract and handle data validation.
"""
from enum import Enum
from pydantic import BaseModel, Field, ConfigDict, computed_field, field_validator
from typing import Optional, Any, List, Dict, Union
from datetime import date, datetime, timedelta
from uuid import UUID


# System type and status constants for validation
from services.system_catalog_validation import SYSTEM_TYPE_LIST

SYSTEM_TYPES = "|".join(SYSTEM_TYPE_LIST)
SYSTEM_STATUSES = "active|inactive|deprecated"


# =============================================================================
# Implementation Status Constants and Validation
# =============================================================================

class ImplementationStatusEnum(str, Enum):
    """
    Control implementation status values aligned with SCFConnect workflow.

    Workflow Progression:
        NOT_STARTED -> IN_PROGRESS -> IMPLEMENTED -> READY_FOR_REVIEW -> MONITORED

    Special States (can be set at any time):
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


# Regex pattern for status validation (for use in Field patterns)
IMPLEMENTATION_STATUSES = "|".join([status.value for status in ImplementationStatusEnum])
# Result: "not_started|in_progress|implemented|ready_for_review|monitored|not_applicable|at_risk|deferred"


# Organization Schemas
class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=100)


class OrganizationCreate(OrganizationBase):
    pass


class OrganizationResponse(OrganizationBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# NIST CSF 2.0 functions for validation
NIST_CSF_FUNCTIONS = "Identify|Protect|Detect|Respond|Recover|Govern"


# PPTDF Applicability Schema
class PPTDFApplicability(BaseModel):
    """People, Process, Technology, Data, Facility applicability flags."""
    people: bool = False
    process: bool = False
    technology: bool = False
    data: bool = False
    facility: bool = False


# Scoped Control Schemas
class ScopedControlBase(BaseModel):
    """Base schema for SCF controls.

    Note: Migrated from CCF to SCF in v4.0.0. The scf_id field replaces ccf_id.

    Implementation Status Workflow (SCFConnect-aligned):
        NOT_STARTED -> IN_PROGRESS -> IMPLEMENTED -> READY_FOR_REVIEW -> MONITORED

    Special states (can be set at any time):
        - NOT_APPLICABLE: Control does not apply to this organisation
        - AT_RISK: Control implementation is at risk
        - DEFERRED: Control implementation has been deferred
    """
    scf_id: str = Field(..., min_length=1, max_length=50, description="SCF control ID (e.g., AST-01)")
    selected: Optional[bool] = False
    selection_reason: Optional[str] = None
    implementation_status: Optional[str] = Field(
        None,
        pattern=f"^({IMPLEMENTATION_STATUSES})$",
        description="Implementation status. Valid values: not_started, in_progress, implemented, ready_for_review, monitored, not_applicable, at_risk, deferred"
    )
    priority: Optional[str] = None
    owner: Optional[str] = None
    assigned_to: Optional[str] = None
    maturity_level: Optional[str] = None
    target_date: Optional[date] = None
    completion_date: Optional[date] = None
    implementation_notes: Optional[str] = None
    related_documentation: Optional[Any] = None  # Can be dict or list
    custom_fields: Optional[Any] = None  # Can be dict or list

    # SCF-specific fields (added in v4.0.0)
    control_weighting: Optional[int] = Field(None, ge=1, le=10, description="Priority weighting 1-10")
    validation_cadence: Optional[str] = Field(None, description="Review frequency (e.g., Annual, Quarterly)")
    nist_csf_function: Optional[str] = Field(
        None,
        pattern=f"^({NIST_CSF_FUNCTIONS})$",
        description="NIST CSF 2.0 function"
    )
    control_question: Optional[str] = Field(None, description="Assessment question for this control")
    pptdf_applicability: Optional[PPTDFApplicability] = Field(
        None,
        description="PPTDF applicability flags"
    )


class ScopedControlCreate(ScopedControlBase):
    pass


class ScopedControlUpdate(BaseModel):
    """Partial update - all fields optional.

    Implementation Status Workflow (SCFConnect-aligned):
        NOT_STARTED -> IN_PROGRESS -> IMPLEMENTED -> READY_FOR_REVIEW -> MONITORED

    Special states (can be set at any time):
        - NOT_APPLICABLE: Control does not apply to this organisation
        - AT_RISK: Control implementation is at risk
        - DEFERRED: Control implementation has been deferred
    """
    selected: Optional[bool] = None
    selection_reason: Optional[str] = None
    implementation_status: Optional[str] = Field(
        None,
        pattern=f"^({IMPLEMENTATION_STATUSES})$",
        description="Implementation status. Valid values: not_started, in_progress, implemented, ready_for_review, monitored, not_applicable, at_risk, deferred"
    )
    priority: Optional[str] = None
    owner: Optional[str] = None
    assigned_to: Optional[str] = None
    maturity_level: Optional[str] = None
    target_date: Optional[date] = None
    completion_date: Optional[date] = None
    implementation_notes: Optional[str] = None
    related_documentation: Optional[Any] = None  # Can be dict or list
    custom_fields: Optional[Any] = None  # Can be dict or list

    # SCF-specific fields (added in v4.0.0)
    control_weighting: Optional[int] = Field(None, ge=1, le=10)
    validation_cadence: Optional[str] = None
    nist_csf_function: Optional[str] = Field(None, pattern=f"^({NIST_CSF_FUNCTIONS})$")
    control_question: Optional[str] = None
    pptdf_applicability: Optional[PPTDFApplicability] = None


class ScopedControlResponse(ScopedControlBase):
    id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime

    # Include PPTDF fields from database (flattened in DB, nested in response)
    pptdf_people: Optional[bool] = False
    pptdf_process: Optional[bool] = False
    pptdf_technology: Optional[bool] = False
    pptdf_data: Optional[bool] = False
    pptdf_facility: Optional[bool] = False

    model_config = ConfigDict(from_attributes=True)


class ScopedControlStats(BaseModel):
    """Server-side aggregated stats for the Control Scoping stats bar."""
    total_controls: int = 0
    in_scope: int = 0
    implemented: int = 0
    not_started: int = 0
    in_progress: int = 0
    not_applicable: int = 0
    at_risk: int = 0
    deferred: int = 0
    ready_for_review: int = 0
    monitored: int = 0


# Organization Settings Schemas
class OrganizationSettingsResponse(BaseModel):
    """Organization-level configurable settings."""
    owner_teams: List[str] = []
    is_trust_portal_enabled: bool = False
    trust_portal_description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class OrganizationSettingsUpdate(BaseModel):
    """Update organization settings (partial)."""
    owner_teams: Optional[List[str]] = None
    is_trust_portal_enabled: Optional[bool] = None
    trust_portal_description: Optional[str] = None


class OrganizationLogoResponse(BaseModel):
    """Metadata for an organization's uploaded logo."""
    filename: Optional[str] = None
    content_type: Optional[str] = None
    size_bytes: int = 0
    updated_at: Optional[datetime] = None


# Evidence Tracking Schemas
class EvidenceTrackingBase(BaseModel):
    evidence_id: str = Field(..., min_length=1, max_length=50)
    is_tracked: Optional[bool] = False
    method_of_collection: Optional[str] = None
    collecting_system: Optional[str] = None
    owner: Optional[str] = None
    frequency: Optional[str] = None
    comments: Optional[str] = None


class EvidenceTrackingCreate(EvidenceTrackingBase):
    system_id: Optional[UUID] = Field(None, description="Reference to the System that collects this evidence")


class EvidenceTrackingUpdate(BaseModel):
    """Partial update - all fields optional"""
    is_tracked: Optional[bool] = None
    method_of_collection: Optional[str] = None
    collecting_system: Optional[str] = None
    owner: Optional[str] = None
    frequency: Optional[str] = None
    comments: Optional[str] = None
    system_id: Optional[UUID] = Field(None, description="Reference to the System that collects this evidence")


class BatchEvidenceTrackingOperation(BaseModel):
    """Single operation in a batch evidence tracking request."""
    evidence_id: str = Field(..., min_length=1, max_length=50, description="Catalog evidence ID (e.g., 'E-IAM-01')")
    is_tracked: Optional[bool] = None
    method_of_collection: Optional[str] = None
    collecting_system: Optional[str] = None
    owner: Optional[str] = None
    frequency: Optional[str] = None
    comments: Optional[str] = None
    system_id: Optional[UUID] = Field(None, description="Reference to the System that collects this evidence")


class BatchEvidenceTrackingRequest(BaseModel):
    """Request for batch evidence tracking operations."""
    operations: List[BatchEvidenceTrackingOperation] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of operations to apply (max 500)"
    )


class BatchEvidenceTrackingResponse(BaseModel):
    """Response for batch evidence tracking operations."""
    updated: int = Field(description="Number of evidence items successfully updated")
    created: int = Field(default=0, description="Number of evidence items newly created")
    failed: int = Field(default=0, description="Number of operations that failed")
    errors: List[str] = Field(default_factory=list, description="Error messages for failed operations")
    evidence: List["EvidenceTrackingResponse"] = Field(
        default_factory=list,
        description="Full evidence objects for cache update"
    )


class EvidenceTrackingResponse(EvidenceTrackingBase):
    id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime
    system_id: Optional[UUID] = None
    system: Optional["SystemSimple"] = None  # Forward reference
    file_count: int = Field(0, description="Number of non-deleted evidence files for this evidence item")

    model_config = ConfigDict(from_attributes=True)


# User Schemas
class UserBase(BaseModel):
    email: str
    display_name: Optional[str] = None


class UserCreate(UserBase):
    google_sub: str


class UserSubscriptionInfo(BaseModel):
    """Subscription summary embedded in UserResponse.

    A lightweight subset of SubscriptionResponse for use in /api/users/me.
    Omits Stripe fields and timestamps to keep the response focused.
    """
    tier: str = Field(..., description="Subscription tier (free, professional, enterprise)")
    max_organisations: int = Field(..., ge=1, description="Maximum organisations allowed")
    max_team_members: int = Field(..., ge=1, description="Maximum team members per organisation")
    is_active: bool = Field(..., description="Whether subscription is currently active")

    model_config = ConfigDict(from_attributes=True)


class UserResponse(UserBase):
    id: UUID
    google_sub: str
    created_at: datetime
    last_login_at: Optional[datetime] = None
    email_notifications_enabled: bool
    notification_frequency: str
    is_platform_admin: bool = False
    subscription: Optional[UserSubscriptionInfo] = None

    model_config = ConfigDict(from_attributes=True)


class UserSimple(BaseModel):
    """Simplified user info for nested responses"""
    id: UUID
    email: str
    display_name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


# Organization Member Schemas
class OrganizationMemberBase(BaseModel):
    role: str = Field(..., pattern="^(admin|editor|viewer)$")


class OrganizationMemberCreate(OrganizationMemberBase):
    user_id: UUID


class OrganizationMemberResponse(OrganizationMemberBase):
    id: UUID
    organization_id: UUID
    user_id: UUID
    joined_at: datetime
    user: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


# Assignment Schemas
class AssignmentBase(BaseModel):
    assignable_type: str = Field(..., pattern="^(control|evidence|task)$")
    assignable_id: UUID
    role: str = Field(default="primary", pattern="^(primary|collaborator)$")


class AssignmentCreate(AssignmentBase):
    user_id: UUID


class AssignmentResponse(AssignmentBase):
    id: UUID
    user_id: UUID
    assigned_at: datetime
    assigned_by_user_id: Optional[UUID] = None
    user: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


# Comment Schemas
class CommentBase(BaseModel):
    content: str = Field(..., min_length=1)
    mentions: Optional[List[UUID]] = []
    parent_comment_id: Optional[UUID] = None


class CommentCreate(CommentBase):
    commentable_type: str = Field(..., pattern="^(control|evidence|task)$")
    commentable_id: UUID


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1)
    mentions: Optional[List[UUID]] = None


class CommentResponse(CommentBase):
    id: UUID
    commentable_type: str
    commentable_id: UUID
    user_id: UUID
    parent_comment_id: Optional[UUID] = None
    is_edited: bool
    edited_at: Optional[datetime] = None
    is_deleted: bool
    deleted_at: Optional[datetime] = None
    created_at: datetime
    user: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


# Evidence Collection Task Schemas
class EvidenceCollectionTaskBase(BaseModel):
    due_date: date
    status: str = Field(default="not_started", pattern="^(not_started|in_progress|completed)$")
    task_type: str = Field(default="collection", pattern="^(feasibility|setup|collection|review|documentation|issue)$")
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    completion_notes: Optional[str] = None
    dependencies: Optional[List[UUID]] = []
    attachments: Optional[List[Dict[str, str]]] = []


class EvidenceCollectionTaskCreate(EvidenceCollectionTaskBase):
    evidence_tracking_id: UUID
    assigned_user_id: Optional[UUID] = None


class EvidenceCollectionTaskUpdate(BaseModel):
    status: Optional[str] = Field(None, pattern="^(not_started|in_progress|completed)$")
    task_type: Optional[str] = Field(None, pattern="^(feasibility|setup|collection|review|documentation|issue)$")
    priority: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    title: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    completion_notes: Optional[str] = None
    completed_date: Optional[date] = None
    assigned_user_id: Optional[UUID] = None
    dependencies: Optional[List[UUID]] = None
    attachments: Optional[List[Dict[str, str]]] = None


class EvidenceCollectionTaskResponse(EvidenceCollectionTaskBase):
    id: UUID
    evidence_tracking_id: UUID
    assigned_user_id: Optional[UUID] = None
    completed_date: Optional[date] = None
    auto_generated: bool
    created_at: datetime
    assigned_user: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


# Notification Schemas
class NotificationBase(BaseModel):
    type: str = Field(..., pattern="^(assignment|mention|task_due|task_overdue|task_assigned)$")
    reference_type: str = Field(..., pattern="^(control|evidence|comment|task)$")
    reference_id: UUID
    message: str


class NotificationCreate(NotificationBase):
    user_id: UUID


class NotificationResponse(NotificationBase):
    id: UUID
    user_id: UUID
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationSettings(BaseModel):
    email_notifications_enabled: bool
    notification_frequency: str = Field(..., pattern="^(immediate|daily_digest|weekly_digest)$")


# System Schemas
class SystemBase(BaseModel):
    """Base schema for System - shared fields for create and response."""
    name: str = Field(..., min_length=1, max_length=255, description="Display name for the system")
    system_type: str = Field(
        ...,
        pattern=f"^({SYSTEM_TYPES})$",
        description="Type of system (cloud_provider, identity_provider, ticketing, logging, security_tool, code_repository, document_management, endpoint_management, vulnerability_management, email_security, security_awareness, password_manager, communication, hr_system, custom)"
    )
    category: Optional[str] = Field(None, max_length=100, description="Optional grouping category")
    description: Optional[str] = Field(None, description="Detailed description of the system")
    vendor: Optional[str] = Field(None, max_length=255, description="Vendor name (e.g., 'Amazon Web Services')")
    status: str = Field(
        default="active",
        pattern=f"^({SYSTEM_STATUSES})$",
        description="System status (active, inactive, deprecated)"
    )
    connection_config: Optional[Dict[str, Any]] = Field(
        default={},
        description="Configuration for API connections (endpoints, auth hints)"
    )
    catalog_template_id: Optional[int] = Field(
        None,
        description="System catalog template this system was created from (drives recipe resolution)"
    )


class SystemCreate(SystemBase):
    """Schema for creating a new system."""
    pass


class SystemUpdate(BaseModel):
    """Schema for partial system updates - all fields optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    system_type: Optional[str] = Field(None, pattern=f"^({SYSTEM_TYPES})$")
    category: Optional[str] = Field(None, max_length=100)
    description: Optional[str] = None
    vendor: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None, pattern=f"^({SYSTEM_STATUSES})$")
    connection_config: Optional[Dict[str, Any]] = None
    catalog_template_id: Optional[int] = None


class SystemResponse(SystemBase):
    """Schema for system responses - includes server-generated fields."""
    id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None
    updated_by_user_id: Optional[UUID] = None
    created_by: Optional[UserSimple] = None
    updated_by: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


class SystemListResponse(BaseModel):
    """Schema for listing systems with optional filtering metadata."""
    systems: List[SystemResponse]
    total: int

    model_config = ConfigDict(from_attributes=True)


class SystemSimple(BaseModel):
    """Simplified system info for nested responses (e.g., in EvidenceTracking)."""
    id: UUID
    name: str
    system_type: str
    vendor: Optional[str] = None
    status: str = "active"

    model_config = ConfigDict(from_attributes=True)


# System Evidence Capability constants
CAPABILITY_STATUSES = "potential|configured|active"
CONFIDENCE_LEVELS = "high|medium|low"
COLLECTION_METHODS = "api|export|manual|webhook|scheduled|integration"


class SystemEvidenceCapabilityBase(BaseModel):
    """Base schema for SystemEvidenceCapability - shared fields."""
    evidence_id: str = Field(..., min_length=1, max_length=50, description="ERL evidence ID")
    capability_status: str = Field(
        default="potential",
        pattern=f"^({CAPABILITY_STATUSES})$",
        description="Status of capability (potential, configured, active)"
    )
    collection_method: Optional[str] = Field(
        None,
        pattern=f"^({COLLECTION_METHODS})$",
        description="How evidence is collected"
    )
    confidence_level: str = Field(
        default="medium",
        pattern=f"^({CONFIDENCE_LEVELS})$",
        description="Confidence in evidence quality"
    )
    data_format: Optional[str] = Field(None, max_length=50, description="Format of collected data")
    notes: Optional[str] = Field(None, description="Implementation notes")


class SystemEvidenceCapabilityCreate(SystemEvidenceCapabilityBase):
    """Schema for creating a new capability mapping."""
    pass


class SystemEvidenceCapabilityUpdate(BaseModel):
    """Schema for partial capability updates - all fields optional."""
    capability_status: Optional[str] = Field(None, pattern=f"^({CAPABILITY_STATUSES})$")
    collection_method: Optional[str] = Field(None, pattern=f"^({COLLECTION_METHODS})$")
    confidence_level: Optional[str] = Field(None, pattern=f"^({CONFIDENCE_LEVELS})$")
    data_format: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None


class SystemEvidenceCapabilityResponse(SystemEvidenceCapabilityBase):
    """Schema for capability responses - includes server-generated fields."""
    id: UUID
    system_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None
    updated_by_user_id: Optional[UUID] = None
    created_by: Optional[UserSimple] = None
    updated_by: Optional[UserSimple] = None
    system: Optional[SystemSimple] = None  # Include when querying by evidence_id

    model_config = ConfigDict(from_attributes=True)


# Generic response wrappers
class SuccessResponse(BaseModel):
    success: bool = True
    message: str


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
    detail: Optional[str] = None


# Database Backup/Restore Schemas
class DatabaseBackupMetadata(BaseModel):
    """Metadata included in database backup files."""
    version: str
    created_at: str
    created_by: Optional[str] = None
    table_counts: Dict[str, int]


class DatabaseBackupResponse(BaseModel):
    """Response schema for database backup endpoint."""
    metadata: DatabaseBackupMetadata
    data: Dict[str, List[Dict[str, Any]]]


class DatabaseRestoreRequest(BaseModel):
    """Request schema for database restore endpoint."""
    backup_data: Dict[str, Any]
    confirm_clear: bool = False  # Must be true to actually perform restore


class DatabaseRestoreResponse(BaseModel):
    """Response schema for database restore endpoint."""
    status: str
    message: str
    restored_by: Optional[str] = None
    restored_at: Optional[str] = None
    original_backup_created_at: Optional[str] = None
    original_backup_created_by: Optional[str] = None
    restored_counts: Optional[Dict[str, int]] = None


# User Invitation Schemas
class InviteUserRequest(BaseModel):
    """Request schema for inviting a user to the organization."""
    email: str = Field(..., min_length=5, max_length=255)
    message: Optional[str] = Field(None, max_length=500)


class InviteUserResponse(BaseModel):
    """Response schema for user invitation."""
    success: bool
    message: str
    email: str
    invited_by: Optional[str] = None


# Organisation Member Invitation Schemas
class OrgInviteCreate(BaseModel):
    """Request schema for creating an organisation member invitation."""
    email: str = Field(..., min_length=5, max_length=255, description="Invitee email address")
    role: str = Field(default="viewer", description="Role to assign: admin, editor, or viewer")
    message: Optional[str] = Field(None, max_length=1000, description="Optional personal message")


class OrgInviteResponse(BaseModel):
    """Response schema for an organisation invitation."""
    id: UUID
    organization_id: UUID
    organization_name: str
    email: str
    role: str
    status: str
    invite_token: Optional[str] = Field(None, description="Token for acceptance (only shown on creation)")
    expires_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrgInvitePreviewResponse(BaseModel):
    """Public response for viewing invite details before accepting."""
    organization_name: str = Field(..., description="Organisation name")
    inviter_name: Optional[str] = Field(None, description="Who sent the invitation")
    inviter_email: Optional[str] = Field(None, description="Inviter's email")
    role: str = Field(..., description="Role being offered")
    expires_at: datetime = Field(..., description="When the invitation expires")
    is_expired: bool = Field(default=False, description="Whether the invite has expired")
    status: str = Field(..., description="Invite status")


class AcceptOrgInviteResponse(BaseModel):
    """Response after accepting an organisation invitation."""
    success: bool = True
    message: str
    organization: OrganizationResponse


class OrgInviteListResponse(BaseModel):
    """List of organisation invitations."""
    invites: List["OrgInviteResponse"]
    total: int


# ============================================================================
# Evidence Collection Suggestions Schemas
# ============================================================================

class CapableSystemInfo(BaseModel):
    """Information about a system capable of providing evidence."""
    system_id: UUID
    name: str
    system_type: str
    vendor: Optional[str] = None
    capability_status: str  # potential, configured, active
    collection_method: Optional[str] = None
    confidence_level: str  # high, medium, low
    notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EvidenceRecommendation(BaseModel):
    """Recommendation for which system to use for evidence collection."""
    system_id: UUID
    system_name: str
    reason: str


# ============================================================================
# Collection Recipe Schemas
# ============================================================================

class RecipeStepSchema(BaseModel):
    """A single step in a collection recipe."""
    step: int
    action: str
    permissions_required: Optional[str] = None
    security_note: Optional[str] = None
    audit_note: Optional[str] = None
    vendor_docs_url: Optional[str] = None


class CollectionRecipeSchema(BaseModel):
    """A complete collection recipe for a specific maturity level."""
    title: str
    estimated_time: Optional[str] = None
    frequency: Optional[str] = None
    steps: List[RecipeStepSchema] = []
    source: str = "curated"  # curated | ai_generated


class CollectionGuidanceSchema(BaseModel):
    """Guidance for collecting evidence from a specific system."""
    system_id: UUID
    system_name: str
    system_type: str
    vendor: Optional[str] = None
    current_maturity: str
    recipe: Optional[CollectionRecipeSchema] = None
    recipe_confidence: str  # system_specific, vendor_generic, type_generic
    matched_via: Optional[str] = None  # template | alias | fallback | none
    maturity_appropriate_methods: List[Dict[str, str]] = []
    next_level_preview: Optional[CollectionRecipeSchema] = None
    alternatives_count: int = 0


# ============================================================================
# System Catalog Schemas (systems knowledge catalog — template picker)
# ============================================================================

class SystemCatalogTemplateSummary(BaseModel):
    """Catalog template summary for the add-system template picker."""
    id: int
    slug: str
    name: str
    vendor: str
    system_type: str
    category: Optional[str] = None
    description: Optional[str] = None
    website: Optional[str] = None
    logo_hint: Optional[str] = None
    is_fallback: bool = False
    recipe_levels: List[str] = []  # e.g. ["L1", "L2", "L3", "L4"]

    model_config = ConfigDict(from_attributes=True)


class SystemCatalogRecipeResponse(BaseModel):
    """A catalog recipe at a specific maturity level."""
    maturity_level: str
    title: str
    estimated_time: Optional[str] = None
    frequency: Optional[str] = None
    steps: List[RecipeStepSchema] = []
    source: str = "curated"

    model_config = ConfigDict(from_attributes=True)


class SystemCatalogTemplateDetail(SystemCatalogTemplateSummary):
    """Catalog template with aliases and full recipes."""
    aliases: List[str] = []
    recipes: List[SystemCatalogRecipeResponse] = []


class SystemRecipesResponse(BaseModel):
    """Resolved collection recipes for an organization's system."""
    system_id: UUID
    matched_via: str  # template | alias | fallback | none
    template: Optional[SystemCatalogTemplateSummary] = None
    recipes: List[SystemCatalogRecipeResponse] = []


class EvidenceSuggestionsResponse(BaseModel):
    """Response for evidence collection suggestions endpoint."""
    evidence_id: str
    currently_tracking: Optional[str] = None  # Name of system currently collecting
    current_system_id: Optional[UUID] = None  # ID of system currently collecting
    capable_systems: List[CapableSystemInfo] = []
    recommendation: Optional[EvidenceRecommendation] = None
    has_suggestions: bool = False  # Quick check if any suggestions exist
    collection_guidance: Optional[CollectionGuidanceSchema] = None  # Populated when system_id provided


# ============================================================================
# Recipe Feedback Schemas
# ============================================================================

class RecipeFeedbackCreate(BaseModel):
    """Request schema for submitting recipe feedback."""
    system_type: str
    vendor: Optional[str] = None
    feedback_type: str  # "helpful" or "not_matching"
    maturity_level: str  # L0-L5

    @field_validator("feedback_type")
    @classmethod
    def validate_feedback_type(cls, v: str) -> str:
        if v not in ("helpful", "not_matching"):
            raise ValueError("feedback_type must be 'helpful' or 'not_matching'")
        return v

    @field_validator("maturity_level")
    @classmethod
    def validate_maturity_level(cls, v: str) -> str:
        if v not in ("L0", "L1", "L2", "L3", "L4", "L5"):
            raise ValueError("maturity_level must be L0-L5")
        return v


class RecipeFeedbackResponse(BaseModel):
    """Response schema for recipe feedback submission."""
    id: UUID
    feedback_type: str
    maturity_level: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Evidence Gap Analysis Schemas
# ============================================================================

class EvidenceGapItem(BaseModel):
    """A single evidence item that has a collection gap."""
    evidence_id: str
    evidence_title: Optional[str] = None
    required_by_controls: List[str] = []
    capable_systems: List[str] = []  # System names that can provide this evidence
    capable_system_ids: List[UUID] = []  # System IDs for quick selection
    recommended_action: Optional[str] = None


class EvidenceGapsResponse(BaseModel):
    """Response for evidence gap analysis endpoint."""
    total_gaps: int
    total_tracked: int
    total_evidence: int
    coverage_percentage: float  # 0-100
    gaps: List[EvidenceGapItem] = []


# ============================================================================
# Framework Readiness Schemas
# ============================================================================

class FrameworkReadinessRequest(BaseModel):
    """Request schema for framework readiness calculation.

    The frontend sends framework-control-evidence mappings from the catalog,
    and the backend calculates readiness using database state.
    """
    frameworks: Dict[str, "FrameworkMappingInput"] = Field(
        ...,
        description="Mapping of framework name to its controls and evidence"
    )


class FrameworkMappingInput(BaseModel):
    """Input mapping for a single framework."""
    controls: List[str] = Field(
        default=[],
        description="List of SCF control IDs that belong to this framework"
    )
    evidence: List[str] = Field(
        default=[],
        description="List of evidence IDs required by this framework's controls"
    )


class FrameworkReadinessItem(BaseModel):
    """Readiness breakdown for a single framework."""
    framework_name: str
    total_controls: int
    selected_controls: int
    implemented_controls: int
    in_progress_controls: int
    at_risk_controls: int
    not_started_controls: int
    total_evidence: int
    tracked_evidence: int
    implementation_score: float = Field(
        description="Percentage of selected controls that are implemented (0-100)"
    )
    evidence_score: float = Field(
        description="Percentage of required evidence that is tracked (0-100)"
    )
    readiness_score: float = Field(
        description="Combined readiness: (40% × implementation) + (60% × evidence)"
    )
    readiness_grade: str = Field(
        description="Grade based on readiness score: 'excellent' (90+), 'good' (70-89), 'fair' (50-69), 'needs-work' (<50)"
    )


class FrameworkReadinessResponse(BaseModel):
    """Response for framework readiness calculation."""
    organization_id: UUID
    calculation_weights: Dict[str, float] = Field(
        default={"implementation": 0.4, "evidence": 0.6},
        description="Weights used in readiness calculation"
    )
    frameworks: List[FrameworkReadinessItem] = []


# ============================================================================
# Framework Bulk Scoping Schemas
# ============================================================================

class BulkScopeFrameworkRequest(BaseModel):
    """Request schema for bulk scoping controls by framework.

    When a framework like ISO 27001 is selected, all controls mapped to that
    framework are automatically added to scope. This is additive only -
    existing scoped controls are never modified.
    """
    frameworks: List[str] = Field(
        ...,
        min_length=1,
        description="List of framework IDs to scope controls from (e.g., ['iso_27001_2022', 'nist_csf_2_0'])"
    )
    selection_reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Reason for selecting these controls (e.g., 'Required by ISO 27001:2022 certification')"
    )


class BulkScopeFrameworkResponse(BaseModel):
    """Response schema for bulk scoping operation."""
    success: bool = True
    added: int = Field(
        description="Number of new controls added to scope"
    )
    updated: int = Field(
        default=0,
        description="Number of existing controls updated to selected=True"
    )
    skipped: int = Field(
        description="Number of controls already in scope (not modified)"
    )
    total: int = Field(
        description="Total controls found matching the frameworks"
    )
    frameworks_processed: List[str] = Field(
        description="Frameworks that were processed"
    )
    message: str = Field(
        description="Human-readable summary of the operation"
    )


class BulkUnscopeFrameworkRequest(BaseModel):
    """Request schema for bulk un-scoping controls by framework.

    Removes controls mapped to the specified frameworks from scope, but ONLY
    if they have no overlap with other frameworks that are currently in scope.
    Controls shared with other active frameworks are protected.
    """
    frameworks: List[str] = Field(
        ...,
        min_length=1,
        description="List of framework IDs to remove from scope (e.g., ['iso_27017_2015'])"
    )
    removal_reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Reason for removing these controls (e.g., 'No longer pursuing ISO 27017 certification')"
    )


class BulkUnscopeFrameworkResponse(BaseModel):
    """Response schema for bulk un-scoping operation with overlap protection."""
    success: bool = True
    removed: int = Field(
        description="Number of controls removed from scope (no overlap)"
    )
    protected: int = Field(
        description="Number of controls protected by overlap with other in-scope frameworks"
    )
    already_out_of_scope: int = Field(
        description="Number of controls that were already out of scope"
    )
    total: int = Field(
        description="Total controls found matching the frameworks"
    )
    protected_by: dict = Field(
        default_factory=dict,
        description="Map of framework ID → count of controls protected by that framework"
    )
    frameworks_processed: List[str] = Field(
        description="Frameworks that were processed"
    )
    message: str = Field(
        description="Human-readable summary of the operation"
    )


class ResetScopeResponse(BaseModel):
    """Response schema for resetting all controls out of scope."""
    success: bool = True
    removed: int = Field(
        description="Number of controls removed from scope"
    )
    message: str = Field(
        description="Human-readable summary of the operation"
    )


class FrameworkInfo(BaseModel):
    """Information about a compliance framework from the catalog."""
    id: str = Field(description="Framework identifier (e.g., 'iso_27001_2022')")
    name: str = Field(description="Display name (e.g., 'ISO 27001:2022')")
    control_count: int = Field(description="Number of controls mapped to this framework")


class FrameworkListResponse(BaseModel):
    """Response schema for listing available frameworks."""
    total: int = Field(description="Total number of frameworks available")
    frameworks: List[FrameworkInfo] = []


# ============================================================================
# Evidence Maturity Advisory Schemas
# ============================================================================

class MaturityFactorDetail(BaseModel):
    """Detail about a factor contributing to maturity score."""
    value: Optional[str] = None
    base_level: Optional[int] = None
    modifier: Optional[int] = None
    days_since_collection: Optional[int] = None
    reason: Optional[str] = None


class EvidenceMaturityResponse(BaseModel):
    """Response for single evidence maturity calculation."""
    evidence_id: str = Field(description="ERL evidence ID")
    level: int = Field(ge=0, le=5, description="Maturity level (0-5)")
    level_name: str = Field(description="Human-readable level name (e.g., 'Managed')")
    level_description: str = Field(description="Description of what this level means")
    score: int = Field(ge=0, le=5, description="Numeric maturity score")
    factors: Dict[str, Any] = Field(
        default={},
        description="Contributing factors to the maturity score"
    )
    upgrade_potential: Optional[int] = Field(
        None,
        ge=0,
        le=5,
        description="Next achievable maturity level, if any"
    )
    # Include tracking context
    is_tracked: bool = Field(description="Whether evidence is being tracked")
    collection_method: Optional[str] = Field(None, description="Current collection method")
    frequency: Optional[str] = Field(None, description="Collection frequency")
    system_name: Optional[str] = Field(None, description="Name of collecting system if linked")


class UpgradeRecommendationResponse(BaseModel):
    """A single recommendation for improving maturity level."""
    current_level: int = Field(ge=0, le=5)
    target_level: int = Field(ge=0, le=5)
    title: str
    description: str
    effort: str = Field(description="Effort required: 'low', 'medium', 'high'")
    impact: str = Field(description="Expected impact: 'low', 'medium', 'high'")
    steps: List[str] = Field(description="Ordered steps to implement this recommendation")


class EvidenceUpgradeRecommendationsResponse(BaseModel):
    """Response for evidence upgrade recommendations endpoint."""
    evidence_id: str
    current_level: int
    current_level_name: str
    recommendations: List[UpgradeRecommendationResponse] = []


class MaturityLevelSummary(BaseModel):
    """Summary of evidence at a specific maturity level."""
    level: int
    name: str
    count: int
    percentage: float = Field(description="Percentage of total evidence at this level")


class OrganisationMaturitySummaryResponse(BaseModel):
    """Response for organisation-wide maturity summary."""
    organisation_id: UUID
    total_evidence: int = Field(description="Total number of evidence items considered")
    tracked_evidence: int = Field(description="Number of evidence items being tracked")
    average_maturity_score: float = Field(
        ge=0,
        le=5,
        description="Weighted average maturity score across all evidence"
    )
    automation_percentage: float = Field(
        ge=0,
        le=100,
        description="Percentage of evidence at L3+ (semi-automated or better)"
    )
    distribution: List[MaturityLevelSummary] = Field(
        description="Distribution of evidence across maturity levels"
    )
    # Quick insights
    lowest_maturity_evidence: List[str] = Field(
        default=[],
        description="Evidence IDs with lowest maturity (up to 5)"
    )
    highest_maturity_evidence: List[str] = Field(
        default=[],
        description="Evidence IDs with highest maturity (up to 5)"
    )
    # Actionable summary
    improvement_opportunities: int = Field(
        description="Number of evidence items that could be upgraded with low effort"
    )


# ============================================================================
# Implementation Status Transition Schemas
# ============================================================================

class StatusTransitionInfo(BaseModel):
    """Information about a status transition."""
    from_status: Optional[str] = Field(
        None,
        description="Current status (None if new control)"
    )
    to_status: str = Field(
        ...,
        pattern=f"^({IMPLEMENTATION_STATUSES})$",
        description="Proposed new status"
    )
    is_valid: bool = Field(
        description="Whether the transition is valid"
    )
    error_message: Optional[str] = Field(
        None,
        description="Error message if transition is invalid"
    )


class StatusWorkflowInfo(BaseModel):
    """Information about the implementation status workflow."""
    workflow_states: List[str] = Field(
        default=[
            "not_started",
            "in_progress",
            "implemented",
            "ready_for_review",
            "monitored"
        ],
        description="Primary workflow states in order"
    )
    special_states: List[str] = Field(
        default=["not_applicable", "at_risk", "deferred"],
        description="Special states that can be set at any time"
    )
    all_states: List[str] = Field(
        default=[s.value for s in ImplementationStatusEnum],
        description="All valid implementation status values"
    )


class ControlStatusSummary(BaseModel):
    """Summary of control implementation statuses for an organisation."""
    total_controls: int = Field(description="Total scoped controls")
    not_started: int = Field(default=0, description="Controls not yet started")
    in_progress: int = Field(default=0, description="Controls actively being implemented")
    implemented: int = Field(default=0, description="Controls with implementation complete")
    ready_for_review: int = Field(default=0, description="Controls ready for review")
    monitored: int = Field(default=0, description="Controls in ongoing monitoring")
    not_applicable: int = Field(default=0, description="Controls marked not applicable")
    at_risk: int = Field(default=0, description="Controls at risk")
    deferred: int = Field(default=0, description="Controls deferred")
    no_status: int = Field(default=0, description="Controls with no status set")


# ============================================================================
# Consultant Portal Schemas
# ============================================================================

# Constants for consultant schemas
CONSULTANT_CLIENT_ROLES = "admin|editor|viewer"
CONSULTANT_CLIENT_STATUSES = "active|suspended|pending"
CONSULTANT_INVITE_STATUSES = "pending|accepted|expired|cancelled"


class ConsultantProfileBase(BaseModel):
    """Base schema for consultant profile."""
    company_name: Optional[str] = Field(None, max_length=255, description="Consultant's firm name")


class ConsultantProfileCreate(ConsultantProfileBase):
    """Schema for creating/updating consultant profile."""
    pass


class ConsultantProfileUpdate(BaseModel):
    """Schema for partial consultant profile updates - all fields optional."""
    company_name: Optional[str] = Field(None, max_length=255)
    is_active: Optional[bool] = None
    max_clients: Optional[int] = Field(None, ge=1, le=999)


class ConsultantProfileResponse(ConsultantProfileBase):
    """Response schema for consultant profile."""
    id: UUID
    user_id: UUID
    is_active: bool
    max_clients: int
    active_client_count: int = Field(default=0, description="Current number of active clients")
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConsultantClientRelationshipCreate(BaseModel):
    """Schema for creating a new consultant-client relationship."""
    organization_id: UUID = Field(..., description="The client organisation ID")
    role: str = Field(
        default="editor",
        pattern=f"^({CONSULTANT_CLIENT_ROLES})$",
        description="Consultant's role for this client (admin, editor, viewer)"
    )


class ConsultantClientRelationshipUpdate(BaseModel):
    """Schema for updating a consultant-client relationship."""
    role: Optional[str] = Field(None, pattern=f"^({CONSULTANT_CLIENT_ROLES})$")
    status: Optional[str] = Field(None, pattern=f"^({CONSULTANT_CLIENT_STATUSES})$")


class ConsultantClientRelationshipResponse(BaseModel):
    """Response schema for consultant-client relationship."""
    id: UUID
    consultant_id: UUID
    organization_id: UUID
    role: str = Field(..., pattern=f"^({CONSULTANT_CLIENT_ROLES})$")
    status: str = Field(..., pattern=f"^({CONSULTANT_CLIENT_STATUSES})$")
    invited_at: datetime
    accepted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    organization: Optional[OrganizationResponse] = None

    model_config = ConfigDict(from_attributes=True)


class ClientSummaryMetrics(BaseModel):
    """Summary metrics for a single client organisation."""
    total_controls: int = Field(default=0, description="Total scoped controls")
    implemented_controls: int = Field(default=0, description="Controls fully implemented")
    in_progress_controls: int = Field(default=0, description="Controls in progress")
    at_risk_controls: int = Field(default=0, description="Controls at risk")
    total_evidence: int = Field(default=0, description="Total evidence items tracked")
    tracked_evidence: int = Field(default=0, description="Evidence being actively tracked")
    framework_readiness: float = Field(default=0.0, ge=0, le=100, description="Overall framework readiness percentage")


class ClientSummaryResponse(BaseModel):
    """Response schema for client summary in consultant dashboard."""
    id: UUID = Field(description="ConsultantClientRelationship ID")
    organization_id: UUID
    organization_name: str
    organization_slug: str
    role: str = Field(description="Consultant's role for this client")
    status: str = Field(description="Relationship status (active, suspended, pending)")
    linked_at: datetime
    metrics: ClientSummaryMetrics

    model_config = ConfigDict(from_attributes=True)


class CreateClientOrgRequest(BaseModel):
    """Request schema for consultant creating a client organisation."""
    name: str = Field(..., min_length=1, max_length=255, description="Organisation name")


class CreateClientOrgResponse(BaseModel):
    """Response after consultant creates a client organisation."""
    success: bool = True
    message: str
    organization: OrganizationResponse
    awaiting_admin: bool = True


class InviteOrgAdminRequest(BaseModel):
    """Request for inviting an admin user to a pre-created org."""
    email: str = Field(..., min_length=5, max_length=255, description="Admin user's email address")
    message: Optional[str] = Field(None, max_length=1000, description="Optional personal message")


class ConsultantInviteCreate(BaseModel):
    """Request schema for creating a client invitation (legacy)."""
    email: str = Field(..., min_length=5, max_length=255, description="Invitee email address")
    organization_name: str = Field(..., min_length=1, max_length=255, description="Proposed organisation name")
    message: Optional[str] = Field(None, max_length=1000, description="Optional personal message")


class ConsultantInviteResponse(BaseModel):
    """Response schema for consultant invitation."""
    id: UUID
    email: str
    organization_name: str
    organization_id: Optional[UUID] = Field(None, description="Pre-created organisation ID")
    status: str
    invite_token: Optional[str] = Field(None, description="Token for acceptance (only shown on creation)")
    expires_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AcceptInviteRequest(BaseModel):
    """Request schema for accepting an invitation."""
    # No body needed - token is in URL, user is authenticated


class InvitePreviewResponse(BaseModel):
    """Response schema for viewing invite details before accepting.

    This is a public endpoint - does not reveal sensitive info.
    """
    organization_name: str = Field(..., description="Proposed organisation name")
    consultant_name: Optional[str] = Field(None, description="Consultant's display name")
    consultant_email: str = Field(..., description="Consultant's email")
    expires_at: datetime = Field(..., description="When the invitation expires")
    is_expired: bool = Field(default=False, description="Whether the invite has expired")
    status: str = Field(..., description="Invite status (pending, accepted, expired, cancelled)")


class AcceptInviteResponse(BaseModel):
    """Response schema after accepting an invitation."""
    success: bool = True
    message: str
    organization: OrganizationResponse


class ConsultantDashboardMetrics(BaseModel):
    """Aggregated metrics for consultant dashboard."""
    total_clients: int = Field(default=0, description="Total number of client organisations")
    active_clients: int = Field(default=0, description="Number of active client relationships")
    pending_invites: int = Field(default=0, description="Number of pending invitations")
    total_controls_across_clients: int = Field(default=0, description="Sum of controls across all clients")
    implemented_controls_across_clients: int = Field(default=0, description="Sum of implemented controls")
    average_framework_readiness: float = Field(default=0.0, ge=0, le=100, description="Average readiness across clients")

    # Status breakdown
    controls_by_status: Dict[str, int] = Field(
        default={},
        description="Aggregated control counts by status across all clients"
    )

    # Recent activity
    recent_activity: List[Dict[str, Any]] = Field(
        default=[],
        description="Recent activity across all client organisations"
    )


class ConsultantDashboardResponse(BaseModel):
    """Response schema for consultant dashboard endpoint."""
    profile: ConsultantProfileResponse
    metrics: ConsultantDashboardMetrics
    clients: List[ClientSummaryResponse] = Field(default=[], description="Summary of all clients")


class RemoveClientRequest(BaseModel):
    """Request schema for removing a client relationship."""
    archive: bool = Field(default=True, description="If true, archive the relationship instead of hard delete")


class RemoveClientResponse(BaseModel):
    """Response schema for removing a client."""
    success: bool = True
    message: str
    organization_id: UUID
    action: str = Field(description="'archived' or 'deleted'")


# ============================================================================
# Risk Assessment Schemas
# ============================================================================

# Treatment status values for validation
TREATMENT_STATUSES = "identified|analysed|treating|treated|accepted|monitoring"

# Risk level values for responses
RISK_LEVELS = "low|medium|high|critical"


class RiskAssessmentBase(BaseModel):
    """Base schema for risk assessment - shared fields."""
    risk_code: str = Field(
        ...,
        min_length=1,
        max_length=20,
        pattern=r"^R-[A-Z]{2,4}-\d+$",
        description="Risk code — SCF (e.g., 'R-AC-1') or custom (e.g., 'R-ORG-1')"
    )
    likelihood: Optional[int] = Field(
        None,
        ge=1,
        le=5,
        description="Inherent likelihood (1=Rare to 5=Almost Certain)"
    )
    impact: Optional[int] = Field(
        None,
        ge=1,
        le=5,
        description="Inherent impact (1=Insignificant to 5=Catastrophic)"
    )
    residual_likelihood: Optional[int] = Field(
        None,
        ge=1,
        le=5,
        description="Residual likelihood after controls"
    )
    residual_impact: Optional[int] = Field(
        None,
        ge=1,
        le=5,
        description="Residual impact after controls"
    )
    treatment_status: str = Field(
        default="identified",
        pattern=f"^({TREATMENT_STATUSES})$",
        description="Treatment workflow status"
    )
    treatment_plan: Optional[str] = Field(None, description="Description of treatment actions")
    treatment_due_date: Optional[date] = Field(None, description="Target date for treatment completion")
    owner_user_id: Optional[UUID] = Field(None, description="User responsible for this risk")
    next_review_date: Optional[date] = Field(None, description="Date of next scheduled review")
    notes: Optional[str] = Field(None, description="Additional notes or context")


class RiskAssessmentCreate(RiskAssessmentBase):
    """Schema for creating a new risk assessment."""
    pass


class RiskAssessmentUpdate(BaseModel):
    """Schema for partial risk assessment updates - all fields optional."""
    likelihood: Optional[int] = Field(None, ge=1, le=5)
    impact: Optional[int] = Field(None, ge=1, le=5)
    residual_likelihood: Optional[int] = Field(None, ge=1, le=5)
    residual_impact: Optional[int] = Field(None, ge=1, le=5)
    treatment_status: Optional[str] = Field(None, pattern=f"^({TREATMENT_STATUSES})$")
    treatment_plan: Optional[str] = None
    treatment_due_date: Optional[date] = None
    owner_user_id: Optional[UUID] = None
    next_review_date: Optional[date] = None
    notes: Optional[str] = None


class RiskAssessmentResponse(RiskAssessmentBase):
    """Response schema for risk assessment - includes server-generated fields."""
    id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None
    updated_by_user_id: Optional[UUID] = None

    # Computed fields
    inherent_risk_score: Optional[int] = Field(None, description="Likelihood × Impact (1-25)")
    residual_risk_score: Optional[int] = Field(None, description="Residual Likelihood × Residual Impact (1-25)")
    inherent_risk_level: Optional[str] = Field(None, description="Risk level: low, medium, high, critical")
    residual_risk_level: Optional[str] = Field(None, description="Residual risk level")

    # Owner relationship
    owner: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


class RiskMatrixCell(BaseModel):
    """A single cell in the 5x5 risk matrix."""
    likelihood: int = Field(..., ge=1, le=5)
    impact: int = Field(..., ge=1, le=5)
    score: int = Field(..., ge=1, le=25, description="Likelihood × Impact")
    level: str = Field(..., pattern=f"^({RISK_LEVELS})$")
    risk_codes: List[str] = Field(default=[], description="Risk codes in this cell")
    count: int = Field(default=0, description="Number of risks in this cell")


class RiskMatrixResponse(BaseModel):
    """Response for the 5x5 risk matrix endpoint."""
    organization_id: UUID
    matrix_type: str = Field(
        default="inherent",
        pattern="^(inherent|residual)$",
        description="Which risk scores to display"
    )
    cells: List[RiskMatrixCell] = Field(
        default=[],
        description="25 cells representing the 5x5 matrix"
    )
    # Summary statistics
    total_assessed: int = Field(default=0, description="Total risks with scores")
    total_unassessed: int = Field(default=0, description="Risks without scores")
    by_level: Dict[str, int] = Field(
        default={},
        description="Count of risks by level (low, medium, high, critical)"
    )


class RiskSummaryResponse(BaseModel):
    """Summary statistics for risk assessments."""
    organization_id: UUID
    total_risks: int = Field(default=0, description="Total risk codes available")
    assessed_risks: int = Field(default=0, description="Risks that have been assessed")
    unassessed_risks: int = Field(default=0, description="Risks not yet assessed")

    # By level (inherent)
    inherent_low: int = Field(default=0)
    inherent_medium: int = Field(default=0)
    inherent_high: int = Field(default=0)
    inherent_critical: int = Field(default=0)

    # By level (residual)
    residual_low: int = Field(default=0)
    residual_medium: int = Field(default=0)
    residual_high: int = Field(default=0)
    residual_critical: int = Field(default=0)

    # By treatment status
    by_treatment_status: Dict[str, int] = Field(default={})


class ControlRiskMapping(BaseModel):
    """Risk codes linked to a specific control."""
    scf_id: str = Field(..., description="SCF control ID")
    risk_codes: List[str] = Field(default=[], description="Risk codes this control mitigates")


# ============================================================================
# Custom Risk Definition Schemas
# ============================================================================

class CustomRiskDefinitionCreate(BaseModel):
    """Schema for creating an organization-defined custom risk."""
    title: str = Field(..., min_length=1, max_length=100, description="Risk title")
    description: str = Field(..., min_length=1, description="Risk description")
    category_name: str = Field(default="Custom", max_length=50, description="Category label")
    category_color: str = Field(default="#6b7280", pattern=r"^#[0-9a-fA-F]{6}$", description="Hex color")


class CustomRiskDefinitionUpdate(BaseModel):
    """Schema for updating a custom risk definition."""
    title: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, min_length=1)
    category_name: Optional[str] = Field(None, max_length=50)
    category_color: Optional[str] = Field(None, pattern=r"^#[0-9a-fA-F]{6}$")


class CustomRiskDefinitionResponse(BaseModel):
    """Response schema for a custom risk definition."""
    id: UUID
    organization_id: UUID
    risk_code: str
    title: str
    description: str
    category_name: str
    category_color: str
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class CustomRiskControlMappingCreate(BaseModel):
    """Schema for linking a control to a custom risk."""
    scf_id: str = Field(..., min_length=1, max_length=50, description="SCF control ID to link")


# ============================================================================
# Risk Profile Schemas
# ============================================================================

RISK_PROFILE_LEVELS = "low|medium|high|critical"


class RiskProfileResponse(BaseModel):
    """Response schema for organisation risk profile."""
    id: UUID
    organization_id: UUID
    low_max: int = Field(..., ge=1, le=24, description="Max score for Low risk level")
    medium_max: int = Field(..., ge=1, le=24, description="Max score for Medium risk level")
    high_max: int = Field(..., ge=1, le=24, description="Max score for High risk level")
    acceptable_risk_level: str = Field(..., description="Acceptable risk level")
    auto_escalate_above: str = Field(..., description="Auto-escalate risks above this level")
    required_vendor_certifications: str = Field(default="[]", description="JSON array of required certifications")
    preferred_vendor_certifications: str = Field(default="[]", description="JSON array of preferred certifications")
    vendor_auto_approve_max: int = Field(..., ge=1, le=25, description="Auto-approve vendors scoring at or below this")
    vendor_auto_reject_min: int = Field(..., ge=1, le=25, description="Auto-reject vendors scoring at or above this")
    created_at: datetime
    updated_at: datetime
    updated_by_user_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class RiskProfileUpdate(BaseModel):
    """Validation schema for updating risk profile."""
    low_max: Optional[int] = Field(None, ge=1, le=24)
    medium_max: Optional[int] = Field(None, ge=1, le=24)
    high_max: Optional[int] = Field(None, ge=1, le=24)
    acceptable_risk_level: Optional[str] = Field(None, pattern=f"^({RISK_PROFILE_LEVELS})$")
    auto_escalate_above: Optional[str] = Field(None, pattern=f"^({RISK_PROFILE_LEVELS})$")
    required_vendor_certifications: Optional[str] = None
    preferred_vendor_certifications: Optional[str] = None
    vendor_auto_approve_max: Optional[int] = Field(None, ge=1, le=25)
    vendor_auto_reject_min: Optional[int] = Field(None, ge=1, le=25)

    @field_validator('medium_max')
    @classmethod
    def medium_max_gt_low(cls, v, info):
        if v is not None and info.data.get('low_max') is not None:
            if v <= info.data['low_max']:
                raise ValueError('medium_max must be greater than low_max')
        return v

    @field_validator('high_max')
    @classmethod
    def high_max_gt_medium(cls, v, info):
        if v is not None and info.data.get('medium_max') is not None:
            if v <= info.data['medium_max']:
                raise ValueError('high_max must be greater than medium_max')
        return v

    @field_validator('vendor_auto_reject_min')
    @classmethod
    def reject_gt_approve(cls, v, info):
        if v is not None and info.data.get('vendor_auto_approve_max') is not None:
            if v <= info.data['vendor_auto_approve_max']:
                raise ValueError('vendor_auto_reject_min must be greater than vendor_auto_approve_max')
        return v


# ============================================================================
# Platform Admin Schemas
# ============================================================================

class PlatformUserResponse(BaseModel):
    """Response schema for user in platform admin context."""
    id: UUID
    email: str
    display_name: Optional[str] = None
    google_sub: str
    is_platform_admin: bool = False
    created_at: datetime
    last_login_at: Optional[datetime] = None
    email_notifications_enabled: bool = True
    notification_frequency: str = "immediate"
    # Count of organisation memberships
    organization_count: int = Field(default=0, description="Number of organisations the user belongs to")

    model_config = ConfigDict(from_attributes=True)


class PlatformUserListResponse(BaseModel):
    """Response schema for listing all platform users."""
    total: int = Field(description="Total number of users")
    users: List[PlatformUserResponse] = []


class PlatformOrganizationResponse(BaseModel):
    """Response schema for organisation in platform admin context."""
    id: UUID
    name: str
    slug: str
    created_at: datetime
    updated_at: datetime
    # Additional platform admin context
    member_count: int = Field(default=0, description="Number of members in this organisation")
    control_count: int = Field(default=0, description="Number of scoped controls")

    model_config = ConfigDict(from_attributes=True)


class PlatformOrganizationListResponse(BaseModel):
    """Response schema for listing all organisations."""
    total: int = Field(description="Total number of organisations")
    organizations: List[PlatformOrganizationResponse] = []


class GrantPlatformAdminRequest(BaseModel):
    """Request schema for granting platform admin to a user."""
    user_id: UUID = Field(..., description="User ID to grant platform admin to")


class RevokePlatformAdminRequest(BaseModel):
    """Request schema for revoking platform admin from a user."""
    user_id: UUID = Field(..., description="User ID to revoke platform admin from")


class PlatformAdminActionResponse(BaseModel):
    """Response schema for platform admin grant/revoke actions."""
    success: bool = True
    message: str
    user_id: UUID
    is_platform_admin: bool = Field(description="Current platform admin status after action")


class DeleteUserRequest(BaseModel):
    """Request schema for deleting a user."""
    user_id: UUID = Field(..., description="User ID to delete")
    confirm: bool = Field(default=False, description="Must be true to confirm deletion")


class DeleteOrganizationRequest(BaseModel):
    """Request schema for deleting an organisation."""
    organization_id: UUID = Field(..., description="Organisation ID to delete")
    confirm: bool = Field(default=False, description="Must be true to confirm deletion")


class GrantConsultantRequest(BaseModel):
    """Request schema for granting consultant access to a user."""
    company_name: Optional[str] = Field(None, max_length=255, description="Consultant's firm name")
    max_clients: int = Field(default=5, ge=1, le=999, description="Maximum number of clients")


class ConsultantAdminActionResponse(BaseModel):
    """Response schema for consultant grant/revoke admin actions."""
    success: bool = True
    message: str
    user_id: UUID
    is_consultant: bool = Field(description="Whether user has an active consultant profile")


class PlatformStatsResponse(BaseModel):
    """Response schema for platform-wide statistics."""
    total_users: int = Field(default=0, description="Total registered users")
    platform_admins: int = Field(default=0, description="Number of platform admins")
    total_organizations: int = Field(default=0, description="Total organisations")
    total_controls: int = Field(default=0, description="Total scoped controls across all orgs")
    total_evidence: int = Field(default=0, description="Total evidence items tracked")
    users_last_30_days: int = Field(default=0, description="Users who logged in last 30 days")
    orgs_last_30_days: int = Field(default=0, description="Orgs created in last 30 days")


# ============================================================================
# Subscription Schemas
# ============================================================================

# Valid subscription tiers
# Includes both platform-native tiers and website tier aliases
SUBSCRIPTION_TIERS = "free|professional|enterprise|pro|consultant|custom"


class SubscriptionResponse(BaseModel):
    """Response schema for user subscription details.

    Contains the user's current subscription tier, limits, and Stripe references.
    Used by GET /provisioning/subscription endpoint.
    """
    tier: str = Field(..., pattern=f"^({SUBSCRIPTION_TIERS})$", description="Subscription tier")
    max_organisations: int = Field(..., ge=1, description="Maximum organisations allowed")
    max_team_members: int = Field(..., ge=1, description="Maximum team members per organisation")
    is_active: bool = Field(..., description="Whether subscription is currently active")
    stripe_customer_id: Optional[str] = Field(None, description="Stripe customer ID if linked")
    stripe_subscription_id: Optional[str] = Field(None, description="Stripe subscription ID if linked")
    created_at: Optional[datetime] = Field(None, description="When subscription was created")
    updated_at: Optional[datetime] = Field(None, description="When subscription was last updated")

    model_config = ConfigDict(from_attributes=True)


class UsageLimit(BaseModel):
    """Current vs maximum usage for a resource."""
    current: int = Field(..., ge=0, description="Current usage count")
    max: int = Field(..., ge=1, description="Maximum allowed by subscription")


class UsageResponse(BaseModel):
    """Response schema for current usage vs subscription limits.

    Used by GET /provisioning/usage endpoint.
    """
    organisations: UsageLimit = Field(..., description="Organisation count vs limit")
    team_members: UsageLimit = Field(..., description="Team member count vs limit")
    tier: str = Field(..., pattern=f"^({SUBSCRIPTION_TIERS})$", description="Current subscription tier")


class SyncRequest(BaseModel):
    """Request schema for subscription sync from marketing site webhook.

    Used by POST /provisioning/sync endpoint (API key auth only).
    Supports user creation if the user doesn't exist yet (new signups from marketing site).

    Accepts both snake_case field names (native) and camelCase aliases
    (sent by the marketing site webhook).
    """
    user_email: str = Field(..., alias="email", min_length=5, max_length=255, description="Email of user to update or create")
    name: Optional[str] = Field(None, max_length=255, description="User display name (used when creating new users)")
    tier: str = Field(..., alias="planTier", pattern=f"^({SUBSCRIPTION_TIERS})$", description="New subscription tier")
    stripe_customer_id: Optional[str] = Field(None, alias="stripeCustomerId", description="Stripe customer ID")
    stripe_subscription_id: Optional[str] = Field(None, alias="stripeSubscriptionId", description="Stripe subscription ID")
    max_clients: Optional[int] = Field(None, alias="maxClients", ge=1, le=999, description="Max consultant clients (only for consultant tier)")
    status: Optional[str] = Field(None, description="Subscription status (e.g., 'canceled')")
    cancel_at_period_end: Optional[bool] = Field(None, alias="cancelAtPeriodEnd", description="Whether subscription cancels at period end")
    current_period_end: Optional[str] = Field(None, alias="currentPeriodEnd", description="ISO timestamp when current period ends")

    model_config = ConfigDict(populate_by_name=True)  # Accept both alias and field name

    @field_validator('user_email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        """Basic email format validation."""
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email format')
        return v.lower().strip()


class SyncResponse(BaseModel):
    """Response schema for subscription sync operation."""
    status: str = Field(..., description="Sync status (e.g., 'synced')")
    user_id: str = Field(..., description="UUID of updated user")
    tier: str = Field(..., pattern=f"^({SUBSCRIPTION_TIERS})$", description="New tier applied")
    message: Optional[str] = Field(None, description="Additional status message")


class AccountDeletionRequest(BaseModel):
    """Request schema for account self-deletion via marketing site."""
    email: str = Field(..., min_length=5, max_length=255, description="Email of user to delete")

    @field_validator('email')
    @classmethod
    def validate_email(cls, v: str) -> str:
        if '@' not in v or '.' not in v.split('@')[-1]:
            raise ValueError('Invalid email format')
        return v.lower().strip()


class DeletionPreviewResponse(BaseModel):
    """Response schema for account deletion preview."""
    email: str = Field(..., description="User email")
    sole_admin_orgs: list[dict] = Field(default_factory=list, description="Orgs where user is sole admin (will be deleted)")
    shared_orgs: list[dict] = Field(default_factory=list, description="Orgs where user is member but not sole admin (membership removed)")
    total_scoped_controls: int = Field(0, description="Total controls across sole-admin orgs")
    total_evidence: int = Field(0, description="Total evidence items across sole-admin orgs")
    total_systems: int = Field(0, description="Total systems across sole-admin orgs")
    total_vendors: int = Field(0, description="Total vendors across sole-admin orgs")


class DeleteUserResponse(BaseModel):
    """Response schema for account deletion."""
    status: str = Field(..., description="Deletion status")
    orgs_deleted: int = Field(0, description="Number of organisations deleted")
    memberships_removed: int = Field(0, description="Number of org memberships removed")
    message: str = Field(..., description="Status message")


# ============================================================================
# Stripe Webhook Schemas
# ============================================================================

class StripeWebhookResponse(BaseModel):
    """Response schema for Stripe webhook processing."""
    received: bool = Field(default=True, description="Whether webhook was received")
    event_type: str = Field(..., description="Stripe event type processed")
    processed: bool = Field(..., description="Whether event was successfully processed")
    message: Optional[str] = Field(None, description="Additional processing message")


# ============================================================================
# Vendor Management Schemas (TPRM)
# ============================================================================

# Vendor status and criticality constants for validation
VENDOR_STATUSES = "prospect|active|under_review|approved|suspended|offboarded"
VENDOR_CRITICALITIES = "low|medium|high|critical"
VENDOR_ASSESSMENT_TYPES = "initial|annual|adhoc|periodic|triggered|follow_up"
VENDOR_ASSESSMENT_STATUSES = "pending|running|failed|scheduled|in_progress|completed|cancelled"
# Unified AI assessment trigger types (Phase 3 API)
VENDOR_AI_ASSESSMENT_TYPES = "initial|annual|adhoc"
VENDOR_CERTIFICATION_STATUSES = "valid|expired|revoked|pending"
VENDOR_RISK_RATINGS = "low|medium|high|critical"
VENDOR_DATA_CLASSIFICATIONS = "public|internal|confidential|restricted"


class VendorBase(BaseModel):
    """Base schema for vendor - shared fields for create and response."""
    name: str = Field(..., min_length=1, max_length=255, description="Vendor name")
    description: Optional[str] = Field(None, description="Description of the vendor")
    website: Optional[str] = Field(None, max_length=500, description="Vendor website URL")
    category: Optional[str] = Field(None, max_length=100, description="Vendor category")
    status: str = Field(
        default="prospect",
        pattern=f"^({VENDOR_STATUSES})$",
        description="Vendor status"
    )
    criticality: str = Field(
        default="low",
        pattern=f"^({VENDOR_CRITICALITIES})$",
        description="Vendor criticality level"
    )
    contact_name: Optional[str] = Field(None, max_length=255, description="Primary contact name")
    contact_email: Optional[str] = Field(None, max_length=255, description="Primary contact email")
    contact_phone: Optional[str] = Field(None, max_length=50, description="Primary contact phone")
    contract_start_date: Optional[date] = Field(None, description="Contract start date")
    contract_end_date: Optional[date] = Field(None, description="Contract end date")
    contract_value: Optional[float] = Field(None, ge=0, description="Contract value")
    risk_score: Optional[int] = Field(None, ge=1, le=25, description="Risk score (1-25)")
    risk_level: Optional[str] = Field(None, pattern=f"^({VENDOR_CRITICALITIES})$", description="Risk level")
    data_classification: Optional[str] = Field(
        None,
        pattern=f"^({VENDOR_DATA_CLASSIFICATIONS})$",
        description="Data classification"
    )


class VendorCreate(VendorBase):
    """Schema for creating a new vendor."""
    pass


class VendorUpdate(BaseModel):
    """Schema for partial vendor updates - all fields optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    website: Optional[str] = Field(None, max_length=500)
    category: Optional[str] = Field(None, max_length=100)
    status: Optional[str] = Field(None, pattern=f"^({VENDOR_STATUSES})$")
    criticality: Optional[str] = Field(None, pattern=f"^({VENDOR_CRITICALITIES})$")
    contact_name: Optional[str] = Field(None, max_length=255)
    contact_email: Optional[str] = Field(None, max_length=255)
    contact_phone: Optional[str] = Field(None, max_length=50)
    contract_start_date: Optional[date] = None
    contract_end_date: Optional[date] = None
    contract_value: Optional[float] = Field(None, ge=0)
    risk_score: Optional[int] = Field(None, ge=1, le=25)
    risk_level: Optional[str] = Field(None, pattern=f"^({VENDOR_CRITICALITIES})$")
    data_classification: Optional[str] = Field(None, pattern=f"^({VENDOR_DATA_CLASSIFICATIONS})$")


class VendorResponse(VendorBase):
    """Response schema for vendor - includes server-generated fields."""
    id: UUID
    organization_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None
    updated_by_user_id: Optional[UUID] = None
    created_by: Optional[UserSimple] = None
    updated_by: Optional[UserSimple] = None
    # Risk provenance + annual review loop
    risk_score_source: Optional[UUID] = Field(None, description="Assessment that set the current risk score")
    risk_scored_at: Optional[datetime] = Field(None, description="When the current risk score was set")
    next_review_date: Optional[date] = Field(None, description="Next annual review due date")

    model_config = ConfigDict(from_attributes=True)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def review_status(self) -> Optional[str]:
        """ok | due_soon (<=30 days) | overdue; None if never assessed."""
        if not self.next_review_date:
            return None
        today = date.today()
        if self.next_review_date < today:
            return "overdue"
        if self.next_review_date <= today + timedelta(days=30):
            return "due_soon"
        return "ok"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def risk_provenance(self) -> Optional[Dict[str, Any]]:
        """{assessment_id, scored_at} for the assessment that set the risk score."""
        if not self.risk_score_source:
            return None
        return {
            "assessment_id": str(self.risk_score_source),
            "scored_at": self.risk_scored_at.isoformat() if self.risk_scored_at else None,
        }


class VendorAssessmentBase(BaseModel):
    """Base schema for vendor assessment."""
    assessment_type: str = Field(
        default="initial",
        pattern=f"^({VENDOR_ASSESSMENT_TYPES})$",
        description="Type of assessment"
    )
    assessment_date: date = Field(..., description="Date of assessment")
    status: str = Field(
        default="scheduled",
        pattern=f"^({VENDOR_ASSESSMENT_STATUSES})$",
        description="Assessment status"
    )
    confidentiality_score: Optional[int] = Field(None, ge=1, le=5, description="Confidentiality score (1-5)")
    integrity_score: Optional[int] = Field(None, ge=1, le=5, description="Integrity score (1-5)")
    availability_score: Optional[int] = Field(None, ge=1, le=5, description="Availability score (1-5)")
    # Risk scoring fields (Issue #60)
    breach_score: Optional[int] = Field(None, ge=0, le=25, description="Breach history score (0-25)")
    certification_score: Optional[int] = Field(None, ge=0, le=25, description="Certification status score (0-25)")
    cve_score: Optional[int] = Field(None, ge=0, le=25, description="CVE severity score (0-25)")
    regulatory_score: Optional[int] = Field(None, ge=0, le=25, description="Regulatory actions score (0-25)")
    data_handling_score: Optional[int] = Field(None, ge=0, le=25, description="Data handling risk score (0-25)")
    likelihood: Optional[int] = Field(None, ge=1, le=5, description="Risk likelihood (1-5)")
    impact: Optional[int] = Field(None, ge=1, le=5, description="Risk impact (1-5)")
    final_risk_score: Optional[int] = Field(None, ge=1, le=25, description="Final risk score (1-25)")
    risk_level: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$", description="Risk level")
    ai_analysis: Optional[str] = Field(None, description="AI-generated analysis summary")
    findings: Optional[str] = Field(None, description="Assessment findings")
    risk_rating: Optional[str] = Field(None, pattern=f"^({VENDOR_RISK_RATINGS})$", description="Risk rating")
    next_assessment_date: Optional[date] = Field(None, description="Next assessment date")
    assessor_user_id: Optional[UUID] = Field(None, description="Assessor user ID")
    # DPSIA Enhancement fields (Phase 2)
    inherent_risk_score: Optional[int] = Field(None, ge=1, le=25, description="Inherent risk score before controls")
    inherent_risk_level: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$", description="Inherent risk level")
    control_effectiveness_pct: Optional[int] = Field(None, ge=0, le=100, description="Control effectiveness percentage")


class VendorAssessmentCreate(VendorAssessmentBase):
    """Schema for creating a new vendor assessment."""
    pass


class VendorAssessmentUpdate(BaseModel):
    """Schema for partial vendor assessment updates."""
    assessment_type: Optional[str] = Field(None, pattern=f"^({VENDOR_ASSESSMENT_TYPES})$")
    assessment_date: Optional[date] = None
    status: Optional[str] = Field(None, pattern=f"^({VENDOR_ASSESSMENT_STATUSES})$")
    confidentiality_score: Optional[int] = Field(None, ge=1, le=5)
    integrity_score: Optional[int] = Field(None, ge=1, le=5)
    availability_score: Optional[int] = Field(None, ge=1, le=5)
    # Risk scoring fields (Issue #60)
    breach_score: Optional[int] = Field(None, ge=0, le=25)
    certification_score: Optional[int] = Field(None, ge=0, le=25)
    cve_score: Optional[int] = Field(None, ge=0, le=25)
    regulatory_score: Optional[int] = Field(None, ge=0, le=25)
    data_handling_score: Optional[int] = Field(None, ge=0, le=25)
    likelihood: Optional[int] = Field(None, ge=1, le=5)
    impact: Optional[int] = Field(None, ge=1, le=5)
    final_risk_score: Optional[int] = Field(None, ge=1, le=25)
    risk_level: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    findings: Optional[str] = None
    risk_rating: Optional[str] = Field(None, pattern=f"^({VENDOR_RISK_RATINGS})$")
    next_assessment_date: Optional[date] = None
    assessor_user_id: Optional[UUID] = None
    inherent_risk_score: Optional[int] = Field(None, ge=1, le=25)
    inherent_risk_level: Optional[str] = Field(None, pattern="^(low|medium|high|critical)$")
    control_effectiveness_pct: Optional[int] = Field(None, ge=0, le=100)


class VendorAssessmentResponse(VendorAssessmentBase):
    """Response schema for vendor assessment (unified record)."""
    id: UUID
    vendor_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None
    updated_by_user_id: Optional[UUID] = None
    created_by: Optional[UserSimple] = None
    updated_by: Optional[UserSimple] = None
    assessor: Optional[UserSimple] = None
    # Risk scoring fields are inherited from VendorAssessmentBase
    # DPSIA Enhancement fields
    inherent_risk_score: Optional[int] = None
    inherent_risk_level: Optional[str] = None
    control_effectiveness_pct: Optional[int] = None
    # AI assessment job tracking (null for legacy/manual rows)
    job_id: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    triggered_by_user_id: Optional[UUID] = None
    # Assessment inputs
    data_role: Optional[str] = None
    services_used: Optional[str] = None
    client_name: Optional[str] = None
    additional_context: Optional[str] = None
    # AI assessment outcome + report
    rag_status: Optional[str] = None
    recommendation: Optional[str] = None
    executive_summary: Optional[str] = None
    report_markdown: Optional[str] = None
    report_json: Optional[dict] = None
    research_sources: Optional[list] = None
    processing_time_ms: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class VendorAIAssessmentTriggerRequest(BaseModel):
    """Request to trigger a unified vendor AI assessment."""
    assessment_type: str = Field(
        default="initial",
        pattern=f"^({VENDOR_AI_ASSESSMENT_TYPES})$",
        description="Type of assessment: initial, annual or adhoc.",
    )
    services_used: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Description of services the vendor provides.",
    )
    data_role: str = Field(
        default="Processor",
        pattern="^(Processor|Controller|Joint Controller)$",
        description="Vendor's data role.",
    )
    additional_context: Optional[str] = Field(
        None,
        max_length=5000,
        description="Additional context for the assessment.",
    )


class VendorAIAssessmentTriggerResponse(BaseModel):
    """Response after triggering a unified vendor AI assessment."""
    assessment_id: str
    job_id: str
    vendor_id: str
    status: str


class VendorAssessmentStatusResponse(BaseModel):
    """Polling response for a unified vendor assessment's progress."""
    assessment_id: str
    job_id: Optional[str] = None
    vendor_id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    error_message: Optional[str] = None


class VendorCertificationBase(BaseModel):
    """Base schema for vendor certification."""
    certification_name: str = Field(..., min_length=1, max_length=255, description="Certification name")
    certification_body: Optional[str] = Field(None, max_length=255, description="Issuing body")
    certificate_number: Optional[str] = Field(None, max_length=100, description="Certificate number")
    status: str = Field(
        default="valid",
        pattern=f"^({VENDOR_CERTIFICATION_STATUSES})$",
        description="Certification status"
    )
    issue_date: Optional[date] = Field(None, description="Issue date")
    expiry_date: Optional[date] = Field(None, description="Expiry date")
    scope: Optional[str] = Field(None, description="Certification scope")
    verification_url: Optional[str] = Field(None, max_length=500, description="Verification URL")


class VendorCertificationCreate(VendorCertificationBase):
    """Schema for creating a new vendor certification."""
    pass


class VendorCertificationUpdate(BaseModel):
    """Schema for partial vendor certification updates."""
    certification_name: Optional[str] = Field(None, min_length=1, max_length=255)
    certification_body: Optional[str] = Field(None, max_length=255)
    certificate_number: Optional[str] = Field(None, max_length=100)
    status: Optional[str] = Field(None, pattern=f"^({VENDOR_CERTIFICATION_STATUSES})$")
    issue_date: Optional[date] = None
    expiry_date: Optional[date] = None
    scope: Optional[str] = None
    verification_url: Optional[str] = Field(None, max_length=500)


class VendorCertificationResponse(VendorCertificationBase):
    """Response schema for vendor certification."""
    id: UUID
    vendor_id: UUID
    created_at: datetime
    updated_at: datetime
    created_by_user_id: Optional[UUID] = None
    updated_by_user_id: Optional[UUID] = None
    created_by: Optional[UserSimple] = None
    updated_by: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Vendor Research schemas (Issue #59)
# ---------------------------------------------------------------------------

class VendorResearchTriggerRequest(BaseModel):
    """Request to trigger AI-powered vendor research."""
    domain_override: Optional[str] = Field(
        None,
        max_length=500,
        description="Override the vendor's website domain for research lookup.",
    )


class VendorResearchTriggerResponse(BaseModel):
    """Response after triggering vendor research."""
    job_id: str
    vendor_id: str
    status: str
    domain: str


class VendorResearchStatusResponse(BaseModel):
    """Polling response for research job progress."""
    job_id: str
    vendor_id: str
    status: str
    source_statuses: dict = Field(default_factory=dict)
    errors: list = Field(default_factory=list)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None


class VendorResearchResultResponse(BaseModel):
    """Full results for a completed research job."""
    job_id: str
    vendor_id: str
    status: str
    hibp_results: dict = Field(default_factory=dict)
    cisa_kev_results: dict = Field(default_factory=dict)
    cve_nvd_results: dict = Field(default_factory=dict)
    regulatory_results: dict = Field(default_factory=dict)
    summary: Optional[str] = None
    risk_indicators: dict = Field(default_factory=dict)
    overall_risk_signal: Optional[str] = None
    source_statuses: dict = Field(default_factory=dict)
    errors: list = Field(default_factory=list)
    researched_domain: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None


# ============================================================================
# Vendor Action Item Schemas (DPSIA Enhancement)
# ============================================================================

ACTION_ITEM_PRIORITIES = "critical|high|medium|low"
ACTION_ITEM_STATUSES = "open|in_progress|completed|cancelled"


class VendorActionItemBase(BaseModel):
    """Base schema for vendor action item."""
    title: str = Field(..., min_length=1, max_length=255, description="Action item title")
    description: Optional[str] = Field(None, description="Detailed description")
    priority: str = Field(default="medium", pattern=f"^({ACTION_ITEM_PRIORITIES})$", description="Priority level")
    status: str = Field(default="open", pattern=f"^({ACTION_ITEM_STATUSES})$", description="Current status")
    category: Optional[str] = Field(None, max_length=100, description="Category")
    owner_name: Optional[str] = Field(None, max_length=255, description="Owner name")
    owner_user_id: Optional[UUID] = Field(None, description="Owner user ID")
    due_date: Optional[date] = Field(None, description="Due date")
    completed_date: Optional[date] = Field(None, description="Completion date")


class VendorActionItemCreate(VendorActionItemBase):
    """Schema for creating an action item."""
    pass


class VendorActionItemUpdate(BaseModel):
    """Schema for updating an action item."""
    title: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    priority: Optional[str] = Field(None, pattern=f"^({ACTION_ITEM_PRIORITIES})$")
    status: Optional[str] = Field(None, pattern=f"^({ACTION_ITEM_STATUSES})$")
    category: Optional[str] = Field(None, max_length=100)
    owner_name: Optional[str] = Field(None, max_length=255)
    owner_user_id: Optional[UUID] = None
    due_date: Optional[date] = None
    completed_date: Optional[date] = None


class VendorActionItemResponse(VendorActionItemBase):
    """Response schema for action item."""
    id: UUID
    vendor_id: UUID
    assessment_id: Optional[UUID] = None
    report_id: Optional[UUID] = None
    auto_generated: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Vendor Compensating Control Schemas (DPSIA Enhancement)
# ============================================================================

EFFECTIVENESS_RATINGS = "full|partial|minimal"


class VendorCompensatingControlBase(BaseModel):
    """Base schema for compensating control."""
    gap_description: str = Field(..., min_length=1, description="Description of the gap")
    compensating_control: str = Field(..., min_length=1, description="The compensating control")
    effectiveness_rating: str = Field(
        default="partial",
        pattern=f"^({EFFECTIVENESS_RATINGS})$",
        description="Effectiveness rating"
    )
    risk_reduction_notes: Optional[str] = Field(None, description="Notes on risk reduction")


class VendorCompensatingControlCreate(VendorCompensatingControlBase):
    """Schema for creating a compensating control."""
    pass


class VendorCompensatingControlUpdate(BaseModel):
    """Schema for updating a compensating control."""
    gap_description: Optional[str] = Field(None, min_length=1)
    compensating_control: Optional[str] = Field(None, min_length=1)
    effectiveness_rating: Optional[str] = Field(None, pattern=f"^({EFFECTIVENESS_RATINGS})$")
    risk_reduction_notes: Optional[str] = None


class VendorCompensatingControlResponse(VendorCompensatingControlBase):
    """Response schema for compensating control."""
    id: UUID
    vendor_id: UUID
    assessment_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# DPSIA Assessment schemas (deprecated /dpsia/* alias routes — kept one release)
# ---------------------------------------------------------------------------

class DPSIATriggerRequest(BaseModel):
    """Request to trigger a vendor AI assessment via the deprecated /dpsia alias."""
    assessment_type: str = Field(
        default="new",
        pattern="^(new|annual-review|adhoc)$",
        description="Type of assessment.",
    )
    services_used: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Description of services the vendor provides.",
    )
    data_role: str = Field(
        default="Processor",
        pattern="^(Processor|Controller|Joint Controller)$",
        description="Vendor's data role.",
    )
    client_name: Optional[str] = Field(
        None,
        max_length=255,
        description="Client/organisation name for the assessment.",
    )
    additional_context: Optional[str] = Field(
        None,
        max_length=5000,
        description="Additional context for the assessment.",
    )


class DPSIATriggerResponse(BaseModel):
    """Response after triggering a DPSIA assessment."""
    job_id: str
    vendor_id: str
    status: str


class DPSIAStatusResponse(BaseModel):
    """Polling response for DPSIA assessment progress."""
    job_id: str
    vendor_id: str
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None
    error_message: Optional[str] = None


class DPSIAResultResponse(BaseModel):
    """Full results for a completed DPSIA assessment."""
    job_id: str
    vendor_id: str
    status: str
    assessment_type: Optional[str] = None
    data_role: Optional[str] = None
    rag_status: Optional[str] = None
    recommendation: Optional[str] = None
    risk_score: Optional[int] = None
    risk_level: Optional[str] = None
    executive_summary: Optional[str] = None
    report_markdown: Optional[str] = None
    report_json: Optional[dict] = None
    report_filename: Optional[str] = None
    research_sources: Optional[list] = None
    linked_assessment_id: Optional[str] = None
    linked_report_id: Optional[str] = None
    processing_time_ms: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: Optional[str] = None


# ============================================================================
# API Key Schemas
# ============================================================================

API_KEY_ROLES = "admin|editor|viewer"


class ApiKeyCreate(BaseModel):
    """Schema for creating a new API key."""
    name: str = Field(..., min_length=1, max_length=255, description="Friendly name for the API key")
    expires_at: Optional[datetime] = Field(None, description="Optional expiry datetime (UTC)")

    @field_validator('expires_at', mode='before')
    @classmethod
    def empty_string_to_none(cls, v: object) -> object:
        """Coerce empty strings to None so HTML date inputs don't cause 422."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


class ApiKeyResponse(BaseModel):
    """Response schema for API key (never includes the plaintext key)."""
    id: UUID
    name: str
    key_prefix: str = Field(..., description="First 8 characters of the key for identification")
    role: str = Field(..., pattern=f"^({API_KEY_ROLES})$")
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime
    user_id: UUID
    user_email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Response returned once at creation time — includes the plaintext key."""
    plaintext_key: str = Field(..., description="Full API key (shown once, never stored)")
    warning: str = Field(
        default="Store this key securely. It will not be shown again.",
        description="One-time warning"
    )


# Batch Scoped Controls Schemas
class BatchScopedControlOperation(BaseModel):
    """A single operation within a batch update.

    Supports the same updatable fields as the single-record PATCH endpoint.
    Only provided (non-None) fields will be applied.
    """
    scf_id: str = Field(..., min_length=1, max_length=50)
    selected: Optional[bool] = None
    implementation_status: Optional[str] = Field(
        None,
        pattern=f"^({IMPLEMENTATION_STATUSES})$",
        description="Valid values: not_started, in_progress, implemented, ready_for_review, monitored, not_applicable, at_risk, deferred"
    )
    selection_reason: Optional[str] = None
    priority: Optional[str] = None
    owner: Optional[str] = None
    assigned_to: Optional[str] = None
    maturity_level: Optional[str] = None
    target_date: Optional[date] = None
    completion_date: Optional[date] = None
    implementation_notes: Optional[str] = None


class BatchScopedControlRequest(BaseModel):
    """Request for batch scoped control operations."""
    operations: List[BatchScopedControlOperation] = Field(
        ...,
        min_length=1,
        max_length=500,
        description="List of operations to apply (max 500)"
    )


class BatchScopedControlResponse(BaseModel):
    """Response for batch scoped control operations."""
    updated: int = Field(description="Number of controls successfully updated")
    created: int = Field(default=0, description="Number of controls newly created")
    failed: int = Field(default=0, description="Number of operations that failed")
    errors: List[str] = Field(default_factory=list, description="Error messages for failed operations")
    controls: List[ScopedControlResponse] = Field(
        default_factory=list,
        description="Full control objects for cache update"
    )


# Audit Log Schemas
class AuditLogResponse(BaseModel):
    """Response for a single audit log entry."""
    id: UUID
    organization_id: UUID
    entity_type: str
    entity_id: UUID
    scf_id: Optional[str] = None
    action: str
    field_name: Optional[str] = None
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    changed_by_user_id: UUID
    changed_by_email: Optional[str] = None
    changed_at: datetime
    ip_address: Optional[str] = None
    action_source: Optional[str] = None
    request_id: Optional[UUID] = None

    model_config = ConfigDict(from_attributes=True)


class AuditLogListResponse(BaseModel):
    """Paginated response for audit log entries."""
    entries: List[AuditLogResponse]
    total: int
    offset: int
    limit: int


# Resolve forward references
# ========================
# Capability Themes Schemas
# ========================

class CapabilityThemePosture(BaseModel):
    """Posture breakdown by implementation status for a capability theme."""
    monitored: int = 0
    implemented: int = 0
    ready_for_review: int = 0
    in_progress: int = 0
    not_started: int = 0
    at_risk: int = 0
    not_applicable: int = 0
    deferred: int = 0


class CapabilityThemeResponse(BaseModel):
    """A single capability theme with posture scoring.

    Phase 1 (#549) added nine nullable axis fields below `posture_percentage`.
    The legacy `posture_percentage` is retained for backward compatibility and
    will be removed on 2026-05-14 (30-day deprecation window per #549 Phase 3).
    Consumers should migrate to `composite_score` + the four axis fields.
    """
    theme_code: str
    name: str
    description: str
    ksi_reference: Optional[str] = None
    icon: Optional[str] = None
    display_order: int = 0
    total_controls: int = Field(description="Total controls mapped to this theme")
    scoped_controls: int = Field(description="Controls in scope for this org")
    posture: CapabilityThemePosture
    posture_percentage: float = Field(
        description=(
            "DEPRECATED (#549 Phase 3) — scheduled for removal on 2026-05-14. "
            "Formula: (monitored + implemented) / (scoped - not_applicable) * 100. "
            "Migrate to `composite_score` + the four axis fields (implementation_coverage, "
            "maturity_score, evidence_coverage, evidence_quality)."
        ),
        json_schema_extra={"deprecated": True},
    )
    maturity_score: Optional[float] = Field(None, description="Weighted avg maturity (L0=0..L5=5)")
    # Multi-axis scoring (issue #549, Phase 1) — nullable for backward compat.
    implementation_coverage: Optional[float] = Field(
        None,
        description="IC axis: (monitored + implemented + 0.5·ready_for_review + 0.25·in_progress) / (scoped - not_applicable). 0.0–1.0.",
    )
    implementation_band: Optional[str] = Field(None, description="IC band: Strong | Moderate | Developing.")
    maturity_band: Optional[str] = Field(None, description="M band based on maturity_score: Strong (≥3.0) | Moderate (2.0–2.9) | Developing.")
    evidence_coverage: Optional[float] = Field(
        None,
        description="EC axis: controls_with_evidence / (scoped - not_applicable). 0.0–1.0.",
    )
    evidence_coverage_band: Optional[str] = Field(None, description="EC band: Strong | Moderate | Developing.")
    evidence_quality: Optional[float] = Field(
        None,
        description="EQ axis: weighted (sufficient/partial/insufficient) × (avg relevance / 100). 0.0–1.0.",
    )
    evidence_quality_band: Optional[str] = Field(None, description="EQ band: Strong | Moderate | Developing.")
    evidence_quality_warning: Optional[str] = Field(
        None,
        description="'low_ai_coverage' when more than 30% of evidence files are unassessed; null otherwise.",
    )
    composite_score: Optional[float] = Field(
        None,
        description="KPS composite: weighted (0.35·IC + 0.20·M/5 + 0.20·EC + 0.25·EQ) with null-axis redistribution. 0.0–1.0.",
    )
    composite_band: Optional[str] = Field(None, description="KPS band: Strong | Moderate | Developing.")


class CapabilityThemeListResponse(BaseModel):
    """Response for listing all capability themes with posture."""
    themes: List[CapabilityThemeResponse]


class CapabilityThemeControlItem(BaseModel):
    """A scoped control within a capability theme."""
    scf_id: str
    control_name: Optional[str] = None
    scf_domain: Optional[str] = None
    selected: bool = False
    implementation_status: Optional[str] = None
    maturity_level: Optional[str] = None
    relevance: str = "primary"


class CapabilityThemeControlsResponse(BaseModel):
    """Paginated list of controls for a capability theme."""
    theme_code: str
    theme_name: str
    controls: List[CapabilityThemeControlItem]
    total: int
    offset: int
    limit: int


class CapabilityThemeEvidencePosture(BaseModel):
    """Evidence assessment metrics for a single capability theme."""
    theme_code: str
    controls_with_evidence: int = Field(description="Controls in this theme with at least one evidence file")
    total_evidence_files: int = Field(description="Total evidence files linked to this theme's controls")
    sufficient_count: int = Field(description="Files assessed as sufficient")
    partial_count: int = Field(description="Files assessed as partial")
    insufficient_count: int = Field(description="Files assessed as insufficient")
    insufficient_sample_count: int = Field(
        default=0,
        description="Window assessments flagged insufficient_sample — coverage gap, not content quality (M1a)",
    )
    pending_count: int = Field(description="Files with pending/processing assessment")
    unassessed_count: int = Field(description="Files with no assessment")
    average_relevance_score: Optional[float] = Field(None, description="Avg relevance score across assessed files (0-100)")
    evidence_confidence: str = Field(description="Derived confidence level: strong, moderate, weak, none")


class CapabilityThemeEvidencePostureResponse(BaseModel):
    """Evidence posture across all capability themes."""
    themes: List[CapabilityThemeEvidencePosture]


class CapabilityThemeScorecardItem(BaseModel):
    """Flat per-theme scorecard combining identity + all four axes + composite (issue #549, Phase 1)."""
    theme_code: str
    name: str
    icon: Optional[str] = None
    display_order: int = 0
    scoped_controls: int
    implementation_coverage: Optional[float] = None
    implementation_band: Optional[str] = None
    maturity_score: Optional[float] = None
    maturity_band: Optional[str] = None
    evidence_coverage: Optional[float] = None
    evidence_coverage_band: Optional[str] = None
    evidence_quality: Optional[float] = None
    evidence_quality_band: Optional[str] = None
    evidence_quality_warning: Optional[str] = None
    composite_score: Optional[float] = None
    composite_band: Optional[str] = None


class CapabilityThemeScorecardResponse(BaseModel):
    """Unified scorecard endpoint response — issue #549, Phase 1.

    Replaces the dual-call pattern of fetching `capability-themes` plus
    `evidence-posture` separately. Same data, one request.
    """
    themes: List[CapabilityThemeScorecardItem]


# =============================================================================
# Trust Portal Public Schemas
# =============================================================================

class TrustPortalThemeSummary(BaseModel):
    """Public projection of a capability theme — aggregated bands only, no raw data.

    The four `*_band` fields mirror the KSI multi-axis scoring already used in the
    authenticated scorecard endpoint. They are optional so that legacy consumers still
    parse successfully and so that orgs with no data for a given axis surface None
    rather than a misleading band.
    """
    name: str = Field(description="Theme display name, e.g. 'Identity & Access Management'")
    icon: Optional[str] = Field(None, description="UI icon identifier")
    display_order: int = Field(description="Sort order for display")
    posture_band: str = Field(description="Aggregated posture: Strong, Moderate, or Developing")
    evidence_confidence: str = Field(description="Evidence confidence: strong, moderate, weak, or none")
    implementation_band: Optional[str] = Field(
        None,
        description="IC axis band (Strong/Moderate/Developing) — implementation coverage of scoped controls.",
    )
    maturity_band: Optional[str] = Field(
        None,
        description="M axis band — weighted average maturity level across scoped controls.",
    )
    evidence_coverage_band: Optional[str] = Field(
        None,
        description="EC axis band — share of scoped controls that have evidence attached.",
    )
    evidence_quality_band: Optional[str] = Field(
        None,
        description="EQ axis band — AI-assessed quality of the attached evidence.",
    )
    composite_band: Optional[str] = Field(
        None,
        description="KPS composite band blending IC/M/EC/EQ. Band only — the raw "
        "composite_score float stays internal. Preferred driver for public headline visuals.",
    )


class TrustPortalFramework(BaseModel):
    """Public projection of a scoped framework."""
    name: str = Field(description="Framework display name, e.g. 'ISO 27001'")
    control_count: int = Field(description="Number of controls in scope for this framework")


class TrustPortalResponse(BaseModel):
    """Public trust portal response — safe for unauthenticated access."""
    organization_name: str
    organization_slug: str
    description: Optional[str] = Field(None, description="Org trust portal description")
    themes: List[TrustPortalThemeSummary]
    frameworks: List[TrustPortalFramework]
    last_updated: datetime = Field(description="Most recent scoped control update")
    generated_at: datetime = Field(description="Server timestamp when response was generated")
    show_axes: bool = Field(
        False,
        description="When true, consumers should render the four-axis (IC/M/EC/EQ) breakdown per theme.",
    )


# =============================================================================
# Evidence File Upload/Download Schemas (Issue #324)
# =============================================================================

class EvidenceWebhookPayload(BaseModel):
    """Advisory model for evidence-inbox webhook bodies (M2, #572).

    Parsing is advisory: the inbox falls through to the raw body when validation
    fails so that pre-M2 collectors remain compatible.
    """
    model_config = ConfigDict(extra="allow")

    collected_at: Optional[datetime] = None
    source: Optional[str] = None
    artifact_type: Optional[Union[str, List[str]]] = None
    collector_id: Optional[str] = None
    data: Optional[dict] = None


class EvidenceUploadUrlRequest(BaseModel):
    """Request for a pre-signed upload URL."""
    filename: str = Field(..., min_length=1, max_length=255, description="Original filename")
    content_type: str = Field(..., min_length=1, max_length=128, description="MIME content type")


class EvidenceUploadUrlResponse(BaseModel):
    """Response with pre-signed POST fields for browser upload."""
    url: str = Field(description="S3 endpoint URL to POST to")
    fields: Dict[str, str] = Field(description="Form fields to include in the POST")
    object_key: str = Field(description="S3 object key for the uploaded file")


class EvidenceDownloadUrlRequest(BaseModel):
    """Request for a pre-signed download URL."""
    file_key: str = Field(..., min_length=1, description="S3 object key")
    filename: Optional[str] = Field(None, max_length=255, description="Friendly filename for download")


class EvidenceDownloadUrlResponse(BaseModel):
    """Response with pre-signed GET URL for downloading."""
    url: str = Field(description="Pre-signed download URL")
    expires_in: int = Field(description="URL expiry in seconds")


class EvidenceTagRequest(BaseModel):
    """Request to tag an uploaded evidence object."""
    file_key: str = Field(..., min_length=1, description="S3 object key")
    evidence_id: Optional[str] = Field(None, description="Evidence tracking record ID")


class EvidenceTagResponse(BaseModel):
    """Response from tagging an evidence object."""
    tagged: bool = Field(description="Whether tagging succeeded")
    key: str = Field(description="S3 object key")
    tag_count: int = Field(description="Number of tags applied")


# =============================================================================
# Evidence File Record Schemas (Issue #325)
# =============================================================================

class EvidenceFileUploadUrlRequest(BaseModel):
    """Request to generate a pre-signed upload URL and create a pending file record."""
    filename: str = Field(..., min_length=1, max_length=255, description="Original filename")
    content_type: str = Field(..., min_length=1, max_length=100, description="MIME content type")
    file_size_bytes: int = Field(..., ge=1, le=50 * 1024 * 1024, description="File size in bytes (max 50MB)")


class EvidenceFileUploadUrlResponse(BaseModel):
    """Response with pre-signed POST fields and the S3 key for confirmation."""
    url: str = Field(description="S3 endpoint URL to POST to")
    fields: Dict[str, str] = Field(description="Form fields to include in the POST")
    s3_key: str = Field(description="S3 object key — pass this to confirm endpoint")
    expires_in: int = Field(description="URL expiry in seconds")


class EvidenceFileConfirmRequest(BaseModel):
    """Request to confirm a successful upload and create the file record."""
    s3_key: str = Field(..., min_length=1, description="S3 object key returned by upload-url")
    sha256_hash: Optional[str] = Field(None, min_length=64, max_length=64, description="SHA-256 hash of uploaded file")


class EvidenceFileResponse(BaseModel):
    """Complete evidence file record with download URL."""
    id: UUID
    organization_id: UUID
    evidence_id: str
    filename: str
    s3_key: str
    content_type: str
    file_size_bytes: int
    sha256_hash: Optional[str] = None
    classification: str
    scan_status: str = "pending"
    scan_details: Optional[dict] = None
    uploaded_by_user_id: Optional[UUID] = None
    uploaded_at: datetime
    expires_at: Optional[datetime] = None
    is_deleted: bool
    download_url: Optional[str] = None
    uploaded_by: Optional[UserSimple] = None
    review_status: str = "not_reviewed"
    reviewed_by_user_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None
    reviewed_by: Optional[UserSimple] = None

    model_config = ConfigDict(from_attributes=True)


class EvidenceFileReviewRequest(BaseModel):
    """Request to review (approve/reject) an evidence file."""
    review_status: str = Field(..., description="One of: approved, rejected, needs_revision")
    review_notes: Optional[str] = Field(None, max_length=2000, description="Optional reviewer notes")


class EvidenceFileListResponse(BaseModel):
    """List of evidence files for an evidence item."""
    files: List[EvidenceFileResponse]
    total: int


# =============================================================================
# Webhook Endpoint Schemas (Issue #214 — Evidence Inbox)
# =============================================================================

class WebhookEndpointCreate(BaseModel):
    """Request to create a new webhook endpoint."""
    name: str = Field(..., min_length=1, max_length=200, description="Human label, e.g. 'Splunk SIEM'")
    description: Optional[str] = Field(None, max_length=2000, description="Optional description")
    allowed_evidence_ids: Optional[List[str]] = Field(None, description="Restrict to specific evidence IDs (null = allow any)")
    rate_limit_per_minute: Optional[int] = Field(None, ge=1, le=10000, description="Per-endpoint rate limit (null = use default)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Jira Evidence Webhook",
                "description": "Receives evidence from Jira Service Management",
                "allowed_evidence_ids": ["IRO-04", "IRO-06"],
                "rate_limit_per_minute": 120
            }
        }
    )


class WebhookEndpointResponse(BaseModel):
    """Webhook endpoint details (secret never included)."""
    id: UUID
    organization_id: UUID
    name: str
    description: Optional[str] = None
    secret_prefix: str = Field(..., description="First 12 chars of secret for identification")
    is_active: bool
    allowed_evidence_ids: Optional[List[str]] = None
    created_by_user_id: Optional[UUID] = None
    last_delivery_at: Optional[datetime] = None
    delivery_count: int
    rate_limit_per_minute: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WebhookEndpointCreatedResponse(WebhookEndpointResponse):
    """Returned once at creation — includes the plaintext secret for HMAC signing."""
    plaintext_secret: str = Field(..., description="Webhook signing secret (shown once, never retrievable)")
    warning: str = Field(
        default="Store this secret securely. It will not be shown again.",
        description="One-time warning"
    )


class WebhookDeliveryResponse(BaseModel):
    """Single webhook delivery log entry."""
    id: UUID
    webhook_endpoint_id: UUID
    organization_id: UUID
    evidence_id: str
    event_id: Optional[str] = None
    content_type: Optional[str] = None
    signature_valid: bool
    status: str
    error_message: Optional[str] = None
    evidence_file_id: Optional[UUID] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    created_at: datetime
    processed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class WebhookDeliveryListResponse(BaseModel):
    """Paginated list of webhook deliveries."""
    deliveries: List[WebhookDeliveryResponse]
    total: int


class WebhookIngestResponse(BaseModel):
    """Response from the evidence inbox ingest endpoint."""
    delivery_id: UUID
    status: str = Field(..., description="received, processed, rejected, or failed")
    message: str


# =============================================================================
# Evidence Validation Schemas (Issue #218 — Evidence Validation Engine)
# =============================================================================

class ValidationFindingSchema(BaseModel):
    """A single finding from a validation rule."""
    rule: str = Field(..., description="Rule name (e.g. catalog_exists, content_type_ok)")
    level: str = Field(..., description="Severity: valid, warning, partial, invalid")
    message: str
    detail: Optional[str] = None


class EvidenceValidationResultResponse(BaseModel):
    """Full validation result for one evidence file."""
    id: UUID
    evidence_file_id: UUID
    organization_id: UUID
    evidence_id: str
    status: str = Field(..., description="Overall: valid, warning, partial, invalid")
    completeness_score: Optional[float] = None
    findings: List[ValidationFindingSchema]
    validation_source: str
    validated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceValidationSummary(BaseModel):
    """Aggregate validation metrics for dashboard display."""
    total_files: int
    valid_count: int
    warning_count: int
    partial_count: int
    invalid_count: int
    pass_rate: float = Field(..., description="Fraction of files with status=valid (0.0–1.0)")


class EvidenceValidationListResponse(BaseModel):
    """Paginated list of validation results."""
    results: List[EvidenceValidationResultResponse]
    total: int


# =============================================================================
# Evidence Health Dashboard Schemas (Issue #220)
# =============================================================================

class EvidenceHealthItem(BaseModel):
    """Health status for a single evidence item."""
    evidence_id: str
    evidence_name: Optional[str] = None
    collecting_system: Optional[str] = None
    frequency: Optional[str] = None
    last_file_uploaded_at: Optional[datetime] = None
    days_since_upload: Optional[int] = None
    staleness_threshold_days: Optional[int] = None
    status: str = Field(..., description="green, amber, red, or unknown")
    file_count: int = 0
    latest_validation_status: Optional[str] = None
    latest_assessment_status: Optional[str] = None
    latest_assessment_score: Optional[float] = None
    control_mappings: List[str] = Field(
        default_factory=list,
        description="SCF control IDs this evidence is mapped to (from catalog)",
    )


class EvidenceHealthSummaryStats(BaseModel):
    """Org-level summary statistics for evidence health."""
    total_tracked: int
    green_count: int
    amber_count: int
    red_count: int
    unknown_count: int
    green_pct: float = 0.0
    amber_pct: float = 0.0
    red_pct: float = 0.0


class EvidenceHealthResponse(BaseModel):
    """Complete evidence health dashboard response."""
    summary: EvidenceHealthSummaryStats
    items: List[EvidenceHealthItem]


class EvidenceHealthConfigSchema(BaseModel):
    """Per-org evidence staleness threshold configuration."""
    evidence_id: str
    staleness_warning_days: int = Field(30, ge=1)
    staleness_critical_days: int = Field(60, ge=1)

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Evidence AI Assessment Schemas
# =============================================================================

class AssessmentFindingSchema(BaseModel):
    """Single finding from AI assessment."""
    category: str = Field(..., description="Finding category: relevance, completeness, quality, error")
    level: str = Field(..., description="Severity: sufficient, partial, insufficient, info")
    message: str = Field(..., description="Human-readable finding description")
    control_id: Optional[str] = Field(None, description="SCF control ID this finding relates to")
    suggestion: Optional[str] = Field(None, description="Suggested remediation action")


class EvidenceAssessmentResponse(BaseModel):
    """Full AI assessment result for one evidence file."""
    id: UUID
    evidence_file_id: UUID
    organization_id: UUID
    evidence_id: str
    status: str = Field(..., description="Assessment status: pending, processing, sufficient, partial, insufficient, error")
    relevance_score: Optional[float] = Field(None, description="0.00-100.00 relevance to mapped controls")
    findings: List[AssessmentFindingSchema]
    summary: Optional[str] = Field(None, description="Human-readable assessment summary")

    # Audit metadata
    model_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    control_context_hash: Optional[str] = None
    framework_version: Optional[str] = None
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None
    cost_cents: Optional[float] = None
    processing_time_ms: Optional[int] = None

    assessment_source: str
    requested_by_user_id: Optional[UUID] = None
    assessed_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class EvidenceAssessmentRequest(BaseModel):
    """Request to trigger AI assessment of an evidence file."""
    assessment_source: str = Field("on_demand", description="Trigger source: on_demand, auto, bulk")


class EvidenceAssessmentBulkRequest(BaseModel):
    """Request to trigger bulk AI assessment."""
    evidence_id: Optional[str] = Field(None, description="Assess all files for this evidence ID")
    file_ids: Optional[List[UUID]] = Field(None, description="Specific file IDs to assess")
    assess_unassessed: bool = Field(False, description="Assess all files that have no existing assessment")


class EvidenceAssessmentSummary(BaseModel):
    """Aggregate AI assessment metrics for dashboard."""
    total_assessed: int
    sufficient_count: int
    partial_count: int
    insufficient_count: int
    pending_count: int
    error_count: int
    unassessed_count: int = 0
    average_relevance_score: Optional[float] = None
    total_cost_cents: Optional[float] = None


# ---------------------------------------------------------------------------
# Windowed Evidence Assessment (M1a)
# ---------------------------------------------------------------------------


class EvidenceWindowAssessmentResponse(BaseModel):
    """Full windowed assessment result for an evidence object over a time window."""
    id: UUID
    organization_id: UUID
    evidence_id: str

    window_start: datetime
    window_end: datetime
    frequency_used: str

    file_ids: List[str] = Field(default_factory=list)
    source_coverage: Dict[str, int] = Field(default_factory=dict)
    artifact_type_coverage: Dict[str, Any] = Field(default_factory=dict)
    expected_artifact_types: List[Dict[str, Any]] = Field(default_factory=list)

    status: str = Field(..., description="pending, processing, sufficient, partial, insufficient, insufficient_sample, error")
    relevance_score: Optional[float] = None
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    summary: Optional[str] = None

    model_id: Optional[str] = None
    prompt_hash: Optional[str] = None
    control_context_hash: Optional[str] = None
    framework_version: Optional[str] = None
    window_hash: Optional[str] = None
    input_token_count: Optional[int] = None
    output_token_count: Optional[int] = None
    cost_cents: Optional[float] = None
    processing_time_ms: Optional[int] = None

    assessment_source: str
    requested_by_user_id: Optional[UUID] = None
    assessed_at: Optional[datetime] = None
    created_at: datetime

    # Per-window review workflow (M4 PR 1, #574). All fields Optional with
    # default None to preserve backward compatibility with M1a serialization
    # — pre-M4 code paths constructing this response without supplying review
    # fields continue to work unchanged.
    review_status: Optional[str] = None
    reviewed_by_user_id: Optional[UUID] = None
    reviewed_at: Optional[datetime] = None
    review_notes: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class EvidenceWindowAssessmentRequest(BaseModel):
    """Request to trigger a windowed evidence assessment."""
    assessment_source: str = Field("on_demand", description="Trigger source: on_demand, auto, bulk")


class EvidenceWindowAssessmentBulkRequest(BaseModel):
    """Request to trigger bulk windowed assessments."""
    evidence_ids: List[str] = Field(..., min_length=1, max_length=25)


class EvidenceWindowAssessmentSummary(BaseModel):
    """Aggregate windowed-assessment metrics for dashboard."""
    total_windows_assessed: int
    sufficient_count: int
    partial_count: int
    insufficient_count: int
    insufficient_sample_count: int
    pending_count: int
    error_count: int
    average_relevance_score: Optional[float] = None
    total_cost_cents: Optional[float] = None


# ---------------------------------------------------------------------------
# Per-window review request (M4 PR 2, #574 — ISC-11)
# ---------------------------------------------------------------------------


class WindowAssessmentReviewRequest(BaseModel):
    """Request body for ``PUT .../window-assessments/{id}/review``.

    Valid ``review_status`` values: ``approved``, ``rejected``,
    ``needs_revision``, ``not_reviewed`` (revoke). Endpoint validates the
    value at request time and returns 422 on invalid input.
    """
    review_status: str = Field(
        ...,
        description="approved, rejected, needs_revision, or not_reviewed (revoke)",
    )
    review_notes: Optional[str] = Field(None, max_length=2000)


# ---------------------------------------------------------------------------
# Frequency Health response (M4 PR 2, #574 — ISC-18)
# ---------------------------------------------------------------------------


class FrequencyHealthItem(BaseModel):
    """Per-evidence_id cadence observation. Mirrors
    :class:`services.frequency_health_service.FrequencyObservation`
    field-for-field."""
    evidence_id: str
    declared_frequency: Optional[str] = None
    suggested_frequency: Optional[str] = None
    observed_cadence_days: Optional[float] = None
    confidence: str
    file_count: int
    misaligned: bool
    reason: str

    model_config = ConfigDict(from_attributes=True)


class FrequencyHealthResponse(BaseModel):
    """Aggregate frequency-health report for an organization (ISC-18).

    ``items`` contains only misaligned rows per ISC-19; low-confidence
    non-misaligned entries are summed in ``low_confidence_count`` for
    awareness.
    """
    organization_id: UUID
    computed_at: datetime
    evaluation_window_days: int
    total_evidence_ids_evaluated: int
    misaligned_count: int
    low_confidence_count: int
    items: List[FrequencyHealthItem] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Audit Engagement Schemas (Issue #370 — Phase D)
# =============================================================================

class AuditEngagementCreate(BaseModel):
    name: str
    frameworks: list[str]
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class AuditEngagementUpdate(BaseModel):
    name: Optional[str] = None
    frameworks: Optional[list[str]] = None
    status: Optional[str] = None
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class AuditEngagementResponse(BaseModel):
    id: UUID
    organization_id: UUID
    name: str
    frameworks: list[str]
    status: str
    start_date: Optional[date]
    end_date: Optional[date]
    created_by_user_id: Optional[UUID]
    created_at: datetime
    updated_at: datetime
    scope_count: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class EngagementScopeItem(BaseModel):
    id: UUID
    scoped_control_id: UUID
    scf_id: Optional[str] = None
    control_name: Optional[str] = None
    added_at: datetime

    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# Control Assessment Composite (M3, #575)
#
# Schemas defined in PR 1 but NOT yet wired to API endpoints. PR 2 will add
# the GET endpoints that serialise these. See M3 design spec ISC-15..17.
# =============================================================================


class CompositeStatusEnum(str, Enum):
    """Composite rollup status — per ISC-4."""
    SUFFICIENT = "sufficient"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    PENDING = "pending"
    NO_EVIDENCE = "no_evidence"


class CompositeMandatoryGap(BaseModel):
    """Single entry in ``ControlAssessmentComposite.mandatory_gaps``.

    Records why the composite was flagged. Possible reasons:
      - ``missing_window``: mapped evidence has no window assessment row.
      - ``missing``: mandatory artifact_type is absent from the window.
      - ``stale``: window's most recent file is older than 2x cadence.
      - ``window_insufficient``: the window itself is insufficient.
      - ``window_error``: the window completed with an error status.
    """
    evidence_id: str
    reason: str
    artifact_type: Optional[str] = None


class CompositeWindowSummary(BaseModel):
    """Per-window summary returned by the GET endpoint (PR 2)."""
    evidence_id: str
    window_id: UUID
    status: str
    relevance_score: Optional[float] = None


class ControlAssessmentCompositeResponse(BaseModel):
    """Response shape for the per-control composite endpoint (PR 2).

    See ISC-15 for the field contract. ``windows`` is computed at read time
    via JOIN; not persisted on the composite row.
    """
    scf_id: str
    composite_status: CompositeStatusEnum
    composite_score: Optional[float] = None
    included_evidence_ids: List[str] = Field(default_factory=list)
    missing_evidence_ids: List[str] = Field(default_factory=list)
    mandatory_gaps: List[CompositeMandatoryGap] = Field(default_factory=list)
    computation_version: int
    computed_at: datetime
    windows: List[CompositeWindowSummary] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class ControlAssessmentCompositeListResponse(BaseModel):
    """Cursor-paginated list of composites — see ISC-16."""
    items: List[ControlAssessmentCompositeResponse]
    next_cursor: Optional[str] = None


EvidenceTrackingResponse.model_rebuild()
BatchEvidenceTrackingResponse.model_rebuild()
FrameworkReadinessRequest.model_rebuild()
ConsultantClientRelationshipResponse.model_rebuild()
ConsultantDashboardResponse.model_rebuild()
AcceptInviteResponse.model_rebuild()
AcceptOrgInviteResponse.model_rebuild()
OrgInviteListResponse.model_rebuild()
CreateClientOrgResponse.model_rebuild()


class CDMDocumentBase(BaseModel):
    original_filename: str = Field(..., min_length=1, max_length=512)
    mime_type: str = Field(..., min_length=1, max_length=100)
    sha256: str = Field(..., min_length=64, max_length=64)
    size_bytes: int = Field(..., ge=0)
    upload_user_id: Optional[UUID] = None
    kb_revision: Optional[str] = Field(None, max_length=128)
    ingest_status: str = Field(..., min_length=1, max_length=20)
    ingest_error: Optional[str] = None


class CDMDocumentResponse(CDMDocumentBase):
    id: UUID
    organization_id: UUID
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CDMDocumentListResponse(BaseModel):
    documents: List[CDMDocumentResponse]
    total: int


class CDMMappingResponse(BaseModel):
    id: UUID
    organization_id: UUID
    scoped_control_id: UUID
    cdm_document_id: UUID
    section: Optional[str] = None
    byte_offset_start: int
    byte_offset_end: int
    relevance_score: float
    status: str
    kb_revision: str
    accepted_by_user_id: Optional[UUID] = None
    accepted_at: Optional[datetime] = None
    dismiss_reason: Optional[str] = None
    dismissed_by_user_id: Optional[UUID] = None
    dismissed_at: Optional[datetime] = None
    excerpt: Optional[str] = None
    review_notes: Optional[str] = None
    last_reviewed_at: Optional[datetime] = None
    last_reviewed_by_user_id: Optional[UUID] = None
    created_at: datetime
    scf_id: Optional[str] = None
    original_filename: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class CDMMappingReviewRequest(BaseModel):
    notes: Optional[str] = None
    mark_reviewed: Optional[bool] = None


class CDMMappingListResponse(BaseModel):
    mappings: List[CDMMappingResponse]
    total: int
    offset: int
    limit: int


class CDMMappingDismissRequest(BaseModel):
    reason: str = Field(..., min_length=1)


class CDMMappingBulkRequest(BaseModel):
    mapping_ids: List[UUID] = Field(..., min_length=1, max_length=200)
    reason: Optional[str] = None  # only used by bulk-dismiss


class CDMMappingBulkResponse(BaseModel):
    accepted: List[UUID] = Field(default_factory=list)
    dismissed: List[UUID] = Field(default_factory=list)
    skipped: List[UUID] = Field(default_factory=list)  # not in 'proposed' or cross-tenant
    not_found: List[UUID] = Field(default_factory=list)


class CDMUploadResponse(BaseModel):
    document_id: UUID
    ingest_status: str


class CDMJobStatusResponse(BaseModel):
    document_id: UUID
    ingest_status: str
    ingest_error: Optional[str] = None
    word_count: Optional[int] = None


class CDMQueryRequest(BaseModel):
    control_id: UUID
    query_text: Optional[str] = None
    limit: int = Field(10, ge=1, le=200)


class CDMQueryResponse(BaseModel):
    hits: List[Dict[str, Any]]
    kb_revision: Optional[str] = None


class CDMComputeMappingsResponse(BaseModel):
    """202 body for POST /cdm/compute-mappings."""

    task_id: str
    idempotent_existing: bool = False


class CDMComputeMappingsStatusResponse(BaseModel):
    """GET /cdm/compute-mappings/{task_id} body."""

    task_id: str
    state: str
    ready: bool
    successful: Optional[bool] = None
    result: Optional[Dict[str, Any]] = None
