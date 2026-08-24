"""Record what the server measured about evidence bytes, not only what was claimed (#57).

`evidence_files.sha256_hash` has existed since evidence upload did, carrying the
column comment "Computed client-side before upload". That comment is accurate and
it is the problem: the value was supplied by the same party that supplied the
bytes, over bytes the server never read, and nothing ever recomputed it. An
auditor reading that column was reading an uploader's assertion dressed as a
measurement. `file_size_bytes` was worse — the confirm endpoint hard-coded `0`
with a comment promising a HEAD request "in future".

This migration adds the four columns that let the server keep its own record:

* `computed_sha256`            — the digest the backend measured over the bytes
                                 it fetched from object storage.
* `hash_verification_status`   — how that digest relates to the client's claim:
                                 pending / verified / mismatch / unasserted /
                                 unavailable.
* `hash_verified_at`           — when the measurement was taken. Null until it is.
* `hash_verification_details`  — both digests, kept together so a discrepancy
                                 survives any later edit of either column.

**No back-fill, deliberately.** Verifying an existing row means fetching its
object out of S3 or Azure Blob, and a tenant with tens of thousands of files
would turn a schema migration into a multi-hour, multi-gigabyte egress job
holding a deploy hostage — with no way to report progress and no way to resume.
Every existing row therefore lands on the `pending` server default and is drained
afterwards by the rate-limited beat sweep in `tasks_evidence_integrity.py`, which
can be watched, paused and resumed like the background work it is.

**`pending` is not a penalty.** Per the product decision recorded with this
change, an unverified file stays downloadable and keeps contributing to posture,
badged as not-yet-scanned. Nobody's score moves because of this deploy; the
backlog is the platform's own debt, not the customer's.

The index is what makes the sweep affordable — it is a partial index over the
pending rows only, so it shrinks to nothing as the backlog drains and costs a
fully-verified deployment essentially no write amplification.

Revision ID: evintegrity001
Revises: evassign001
Create Date: 2026-08-23 21:00:00
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "evintegrity001"
down_revision = "evassign001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_INDEX_NAME = "ix_evidence_files_hash_pending"


def upgrade() -> None:
    op.add_column(
        "evidence_files",
        sa.Column("computed_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "evidence_files",
        sa.Column(
            "hash_verification_status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column(
        "evidence_files",
        sa.Column("hash_verified_at", sa.DateTime(timezone=False), nullable=True),
    )
    op.add_column(
        "evidence_files",
        sa.Column("hash_verification_details", postgresql.JSONB(), nullable=True),
    )

    # Oldest-first is the sweep's ordering, so the index carries `uploaded_at`
    # as its second key and the sweep never sorts.
    op.create_index(
        _INDEX_NAME,
        "evidence_files",
        ["uploaded_at"],
        unique=False,
        postgresql_where=sa.text("hash_verification_status = 'pending'"),
    )

    backlog = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM evidence_files "
            "WHERE is_deleted = false AND hash_verification_status = 'pending'"
        )
    ).scalar()
    logger.info(
        "Evidence integrity columns added. %s existing file(s) await verification; "
        "the background sweep will drain them. No score changes from this migration.",
        backlog,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="evidence_files")
    op.drop_column("evidence_files", "hash_verification_details")
    op.drop_column("evidence_files", "hash_verified_at")
    op.drop_column("evidence_files", "hash_verification_status")
    op.drop_column("evidence_files", "computed_sha256")
