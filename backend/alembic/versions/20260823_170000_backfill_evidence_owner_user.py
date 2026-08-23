"""Backfill evidence assignment onto the columns the schedulers read (#781).

`evidence_tracking.owner` was a free-text box and the only assignment control the
UI offered. The task generator, the due-date notifier and the work queue all read
`assigned_user_id` / `owner_user_id` instead — two columns no endpoint wrote.
Every auto-generated collection task was therefore created unassigned,
unnotified and invisible.

The API now writes those columns. This migration repairs what is already there,
in two steps:

1. Resolve `evidence_tracking.owner` text to `owner_user_id` where it matches a
   member unambiguously.
2. Stamp the resolved assignee onto **open, currently-unassigned** tasks that
   already exist. Without this, the fix only helps evidence collected in some
   future period: `task_generator` assigns a task once, at creation, and its
   duplicate-window check means it will not revisit the ones already there. An
   org with eighty tracked items would deploy this PR and still see an empty
   work queue.

**Deliberately conservative on both steps.**

* A row is only resolved when the text matches exactly one member of that row's
  OWN organisation, case-insensitively, on email or display_name. Ambiguous and
  unmatched values are left alone. Guessing an owner would put a real person's
  name against evidence they never agreed to own — and, because
  `notifications.py:451` routes evidence notices to `owner_user_id`, would start
  emailing them about it.
* `owner` text is never cleared. It stays as the label for teams and external
  parties, and is the only record of what the operator typed.
* Tasks are only stamped where `assigned_user_id IS NULL`. A per-task assignee
  set deliberately by a human is never overwritten, and completed tasks are left
  as the historical record they are.

**Operator note — this migration causes notifications to start flowing.** That
is the point of it, but it is a real-world effect: every person resolved in step
1 becomes a recipient for evidence they were previously only a text label
against, and every task stamped in step 2 becomes eligible for a due-date
reminder on the next notifier run. Row ids are logged (values are not — the
labels are user-typed and routinely contain personal names and email addresses,
which do not belong in deploy-log retention) so a scoped manual rollback is
possible.

Revision ID: evassign001
Revises: docgen003
Create Date: 2026-08-23 17:00:00
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "evassign001"
down_revision = "freqvocab001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


# One statement, not one SELECT per row: a multi-tenant deployment can hold tens
# of thousands of owner-bearing rows, and a per-row round-trip would hold the
# migration transaction open for the duration of the deploy.
#
# The HAVING clause is what makes this conservative — a label that matches two
# members of the same org resolves to no rows at all rather than to whichever
# one the planner returned first.
_RESOLVE_OWNERS = sa.text(
    """
    WITH candidate AS (
        SELECT et.id            AS tracking_id,
               min(u.id::text)::uuid AS user_id
        FROM evidence_tracking et
        JOIN organization_members m ON m.organization_id = et.organization_id
        JOIN users u                ON u.id = m.user_id
        WHERE et.owner IS NOT NULL
          AND btrim(et.owner) <> ''
          AND et.owner_user_id IS NULL
          AND (
                lower(btrim(u.email)) = lower(btrim(et.owner))
             OR lower(btrim(coalesce(u.display_name, ''))) = lower(btrim(et.owner))
          )
        GROUP BY et.id
        HAVING count(DISTINCT u.id) = 1
    )
    UPDATE evidence_tracking et
    SET owner_user_id = candidate.user_id
    FROM candidate
    WHERE et.id = candidate.tracking_id
    RETURNING et.id
    """
)

# Ambiguity is not an error, but an operator should be able to find it. Counted
# and reported by row id, never by label value.
_AMBIGUOUS = sa.text(
    """
    SELECT et.id
    FROM evidence_tracking et
    JOIN organization_members m ON m.organization_id = et.organization_id
    JOIN users u                ON u.id = m.user_id
    WHERE et.owner IS NOT NULL
      AND btrim(et.owner) <> ''
      AND et.owner_user_id IS NULL
      AND (
            lower(btrim(u.email)) = lower(btrim(et.owner))
         OR lower(btrim(coalesce(u.display_name, ''))) = lower(btrim(et.owner))
      )
    GROUP BY et.id
    HAVING count(DISTINCT u.id) > 1
    """
)

_UNMATCHED_COUNT = sa.text(
    """
    SELECT count(*)
    FROM evidence_tracking et
    WHERE et.owner IS NOT NULL
      AND btrim(et.owner) <> ''
      AND et.owner_user_id IS NULL
    """
)

# Step 2. Only open tasks, only where no assignee was ever set.
_STAMP_TASKS = sa.text(
    """
    UPDATE evidence_collection_tasks t
    SET assigned_user_id = COALESCE(et.assigned_user_id, et.owner_user_id)
    FROM evidence_tracking et
    WHERE t.evidence_tracking_id = et.id
      AND t.assigned_user_id IS NULL
      AND t.status <> 'completed'
      AND COALESCE(et.assigned_user_id, et.owner_user_id) IS NOT NULL
    RETURNING t.id
    """
)


def upgrade() -> None:
    conn = op.get_bind()

    ambiguous = [row[0] for row in conn.execute(_AMBIGUOUS).fetchall()]

    resolved = [row[0] for row in conn.execute(_RESOLVE_OWNERS).fetchall()]
    logger.info(
        "#781 owner backfill: %s evidence_tracking row(s) resolved to a user: %s",
        len(resolved),
        ", ".join(str(r) for r in resolved) or "-",
    )

    if ambiguous:
        logger.warning(
            "#781 owner backfill: %s row(s) left unresolved because the owner label "
            "matched more than one member of the same organisation. Tracking ids: %s",
            len(ambiguous),
            ", ".join(str(r) for r in ambiguous),
        )

    unmatched = conn.execute(_UNMATCHED_COUNT).scalar() or 0
    if unmatched:
        # Not a failure. Most of these are teams ("Security Operations", "GRC" —
        # both are literal options in the evidence panel's dropdown) or people
        # who were never platform users. That is what free-text `owner` is for
        # now; they are counted so an operator can decide, not so a deploy can be
        # blocked.
        logger.warning(
            "#781 owner backfill: %s row(s) still carry an owner label that matches "
            "no member of their organisation and were left unchanged",
            unmatched,
        )

    stamped = [row[0] for row in conn.execute(_STAMP_TASKS).fetchall()]
    logger.info(
        "#781 task backfill: %s open task(s) given an assignee they never had. "
        "These become eligible for due-date notifications on the next notifier "
        "run. Task ids: %s",
        len(stamped),
        ", ".join(str(r) for r in stamped) or "-",
    )


def downgrade() -> None:
    """Cannot be undone safely, and says so rather than guessing.

    Both steps write columns that other paths also write: `owner_user_id` is set
    by the bulk-import reconciliation path and, as of #781, by the API;
    `evidence_collection_tasks.assigned_user_id` is set by the generator and by
    the tasks API. By the time anyone rolls back there is no way to tell which
    values this migration wrote apart from which a person set deliberately, and
    clearing all of them would silently unassign live work and stop its
    notifications.

    Nothing is undone. The upgrade is additive and safe to leave in place:
    rolled-back code reads both columns exactly as this migration writes them —
    `task_generator` has always fallen back to `owner_user_id`, and
    `notifications`/`dashboard` have always read
    `evidence_collection_tasks.assigned_user_id` — so a populated column is what
    the old code already expected to find.

    A scoped manual rollback is possible: the upgrade logs the exact row ids it
    wrote, in both steps.
    """
    logger.info(
        "#781 backfill: downgrade is a no-op by design - see the docstring. Values "
        "are left in place because this migration cannot distinguish the rows it "
        "wrote from rows a person assigned. The upgrade logged the row ids if a "
        "scoped rollback is needed."
    )
