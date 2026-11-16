"""Engagement evidence review board: review state, events, and their supports.

Revision ID: engrev001
Revises: riskcat001
Create Date: 2026-11-16 11:00:00

An engagement is an auditor reviewing evidence the organisation already holds.
The unit the auditor tests is the evidence object (an SCF ERL id such as
E-IAC-07), so this revision gives every (engagement, evidence object) pair a
review row that moves through Open -> In progress -> Action required ->
Submitted -> Accepted, and an append-only event row for every move.

What it adds:

* ``engagement_evidence_reviews`` — one row per (engagement, evidence_id).
* ``engagement_evidence_review_events`` — append-only (trigger-enforced).
* ``engagement_queries.evidence_id`` — a query can target an evidence object.
  ``scf_id`` becomes nullable and a check requires exactly one target.
* ``comments.visibility`` — ``shared`` (default) or ``internal`` (org-only).
* ``engagement_auditors.kind`` — ``external`` (default) or ``internal``.
* ``notifications.engagement_id`` — lets an auditor who is not an org member
  read the notifications raised for an engagement they hold a grant on.

Backfill: one ``open`` review row for every evidence object that maps, from
either side of the catalog (``scf_catalog_evidence.control_mappings`` or
``scf_catalog_controls.evidence_requests``), to an in-scope, non-retired
control of an existing engagement. This is the same union the API reads in
``_fetch_evidence_by_scf``.

Downgrade drops everything added here. Queries that target an evidence object
have no control to fall back to, so they are deleted before ``scf_id`` is made
NOT NULL again.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "engrev001"
down_revision = "riskcat001"
branch_labels = None
depends_on = None


REVIEW_STATUSES = ("open", "in_progress", "action_required", "submitted", "accepted")
_STATUS_LIST = ", ".join(f"'{s}'" for s in REVIEW_STATUSES)


BACKFILL_SQL = """
INSERT INTO engagement_evidence_reviews
    (id, engagement_id, organization_id, evidence_id, status)
SELECT gen_random_uuid(), x.engagement_id, x.organization_id, x.evidence_id, 'open'
FROM (
    SELECT DISTINCT e.id AS engagement_id, e.organization_id, ev.evidence_id
    FROM audit_engagements e
    JOIN engagement_control_scope s
      ON s.engagement_id = e.id AND s.scope_status = 'in_scope'
    LEFT JOIN scf_catalog_controls c ON c.scf_id = s.scf_id
    JOIN scf_catalog_evidence ev
      ON ev.control_mappings @> to_jsonb(s.scf_id::text)
      OR (c.evidence_requests IS NOT NULL
          AND c.evidence_requests @> to_jsonb(ev.evidence_id::text))
    WHERE c.status IS NULL OR c.status <> 'deprecated'
) x
ON CONFLICT (engagement_id, evidence_id) DO NOTHING
"""


REFUSE_UPDATE_FN = """
CREATE OR REPLACE FUNCTION engagement_evidence_review_events_refuse_update()
RETURNS trigger AS $fn$
BEGIN
    -- The one permitted mutation is the ON DELETE SET NULL anonymisation from
    -- users: deleting a user must not be blocked by this table and must not
    -- destroy the record of what they did. A row may lose its actor, never
    -- gain one or swap one.
    IF (to_jsonb(NEW) - 'actor_user_id') = (to_jsonb(OLD) - 'actor_user_id')
       AND NEW.actor_user_id IS NULL
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'engagement_evidence_review_events is append-only: UPDATE is refused (row %).',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_DELETE_FN = """
CREATE OR REPLACE FUNCTION engagement_evidence_review_events_refuse_delete()
RETURNS trigger AS $fn$
BEGIN
    -- A cascade has already removed the parent row by the time referencing
    -- rows are collected, so a missing parent is what tells "the engagement
    -- this history belongs to is going" from "someone is deleting history".
    IF NOT EXISTS (SELECT 1 FROM engagement_evidence_reviews WHERE id = OLD.review_id)
       OR NOT EXISTS (SELECT 1 FROM audit_engagements WHERE id = OLD.engagement_id)
       OR NOT EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id)
    THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'engagement_evidence_review_events is append-only: DELETE is refused (row %). '
        'Review history is removed only with the review, engagement or organization '
        'it belongs to.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # --- review state -------------------------------------------------------
    op.create_table(
        "engagement_evidence_reviews",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("engagement_id", UUID(as_uuid=True),
                  sa.ForeignKey("audit_engagements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="open"),
        sa.Column("assigned_reviewer_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_decision_by_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("last_decision_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("engagement_id", "evidence_id",
                            name="uq_engagement_evidence_review"),
        sa.CheckConstraint(f"status IN ({_STATUS_LIST})",
                           name="ck_engagement_evidence_reviews_status"),
    )
    op.create_index("ix_engagement_evidence_reviews_engagement_status",
                    "engagement_evidence_reviews", ["engagement_id", "status"])
    op.create_index("ix_engagement_evidence_reviews_reviewer",
                    "engagement_evidence_reviews", ["assigned_reviewer_user_id"])

    # --- append-only event history -------------------------------------------
    op.create_table(
        "engagement_evidence_review_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("review_id", UUID(as_uuid=True),
                  sa.ForeignKey("engagement_evidence_reviews.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("engagement_id", UUID(as_uuid=True),
                  sa.ForeignKey("audit_engagements.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evidence_id", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False, server_default="transition"),
        sa.Column("from_status", sa.String(20), nullable=False),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_kind", sa.String(10), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("actor_kind IN ('auditor', 'org')",
                           name="ck_engagement_evidence_review_events_actor_kind"),
        sa.CheckConstraint("event_type IN ('transition', 'reviewer_assigned')",
                           name="ck_engagement_evidence_review_events_event_type"),
        sa.CheckConstraint(
            f"from_status IN ({_STATUS_LIST}) AND to_status IN ({_STATUS_LIST})",
            name="ck_engagement_evidence_review_events_statuses"),
    )
    op.create_index("ix_engagement_evidence_review_events_review_created",
                    "engagement_evidence_review_events", ["review_id", "created_at"])

    op.execute(REFUSE_UPDATE_FN)
    op.execute(REFUSE_DELETE_FN)
    op.execute("""
        CREATE TRIGGER trg_engagement_evidence_review_events_no_update
        BEFORE UPDATE ON engagement_evidence_review_events
        FOR EACH ROW EXECUTE FUNCTION engagement_evidence_review_events_refuse_update()
    """)
    op.execute("""
        CREATE TRIGGER trg_engagement_evidence_review_events_no_delete
        BEFORE DELETE ON engagement_evidence_review_events
        FOR EACH ROW EXECUTE FUNCTION engagement_evidence_review_events_refuse_delete()
    """)

    # --- queries can target an evidence object -------------------------------
    op.add_column("engagement_queries", sa.Column("evidence_id", sa.String(50), nullable=True))
    op.alter_column("engagement_queries", "scf_id", existing_type=sa.String(50), nullable=True)
    op.create_check_constraint(
        "ck_engagement_queries_one_target",
        "engagement_queries",
        "(scf_id IS NOT NULL) <> (evidence_id IS NOT NULL)",
    )
    op.create_index("ix_engagement_queries_engagement_evidence",
                    "engagement_queries", ["engagement_id", "evidence_id"])

    # --- comment visibility ---------------------------------------------------
    op.add_column("comments", sa.Column("visibility", sa.String(20), nullable=False,
                                        server_default="shared"))
    op.create_check_constraint("ck_comments_visibility", "comments",
                               "visibility IN ('shared', 'internal')")

    # --- internal auditors ----------------------------------------------------
    op.add_column("engagement_auditors", sa.Column("kind", sa.String(20), nullable=False,
                                                   server_default="external"))
    op.create_check_constraint("ck_engagement_auditors_kind", "engagement_auditors",
                               "kind IN ('external', 'internal')")

    # --- engagement-confined notifications ------------------------------------
    op.add_column("notifications", sa.Column(
        "engagement_id", UUID(as_uuid=True),
        sa.ForeignKey("audit_engagements.id", ondelete="CASCADE",
                      name="fk_notifications_engagement_id"),
        nullable=True,
    ))
    op.create_index("ix_notifications_engagement_id", "notifications", ["engagement_id"],
                    postgresql_where=sa.text("engagement_id IS NOT NULL"))

    # --- backfill existing engagements ----------------------------------------
    op.execute(BACKFILL_SQL)


def downgrade() -> None:
    op.drop_index("ix_notifications_engagement_id", table_name="notifications")
    op.drop_constraint("fk_notifications_engagement_id", "notifications", type_="foreignkey")
    op.drop_column("notifications", "engagement_id")

    op.drop_constraint("ck_engagement_auditors_kind", "engagement_auditors", type_="check")
    op.drop_column("engagement_auditors", "kind")

    # Before visibility goes: comments on review cards include internal notes,
    # and would lose their only org-only marker. Responses to evidence-targeted
    # queries (deleted below) would be orphaned. Card notifications point at
    # rows that are about to be dropped.
    op.execute("DELETE FROM comments WHERE commentable_type = 'engagement_evidence_review'")
    op.execute(
        "DELETE FROM comments WHERE commentable_type = 'engagement_query' "
        "AND commentable_id IN (SELECT id FROM engagement_queries WHERE scf_id IS NULL)"
    )
    op.execute("DELETE FROM notifications WHERE reference_type = 'engagement_evidence_review'")

    op.drop_constraint("ck_comments_visibility", "comments", type_="check")
    op.drop_column("comments", "visibility")

    op.drop_index("ix_engagement_queries_engagement_evidence", table_name="engagement_queries")
    op.drop_constraint("ck_engagement_queries_one_target", "engagement_queries", type_="check")
    # An evidence-targeted query has no control to fall back to.
    op.execute("DELETE FROM engagement_queries WHERE scf_id IS NULL")
    op.alter_column("engagement_queries", "scf_id", existing_type=sa.String(50), nullable=False)
    op.drop_column("engagement_queries", "evidence_id")

    op.execute("DROP TRIGGER IF EXISTS trg_engagement_evidence_review_events_no_delete "
               "ON engagement_evidence_review_events")
    op.execute("DROP TRIGGER IF EXISTS trg_engagement_evidence_review_events_no_update "
               "ON engagement_evidence_review_events")
    op.drop_table("engagement_evidence_review_events")
    op.execute("DROP FUNCTION IF EXISTS engagement_evidence_review_events_refuse_delete()")
    op.execute("DROP FUNCTION IF EXISTS engagement_evidence_review_events_refuse_update()")
    op.drop_table("engagement_evidence_reviews")
