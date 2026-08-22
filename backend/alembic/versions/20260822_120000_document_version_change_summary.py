"""Record what each generated version actually changed.

``document_versions`` stored a version number, a model id and a fingerprint —
enough to identify a snapshot, not enough to say anything about it. The History
panel could therefore only ever render a column of indistinguishable numbers,
and the Change History table inside a regenerated policy fell back to the same
generic sentence every time ("Revised — updated control data"), which is a
record in shape only.

The information exists. ``three_way_merge`` already computes per-status tallies
for the review banner, and the control count is in scope at the same point.
This adds one nullable JSONB column to carry them forward, so the answer to
"what changed in v7?" survives past the request that generated it.

**Nullable on purpose.** Every version row that predates this column keeps a
NULL, and NULL means "not recorded" rather than "nothing changed" — those are
different claims and the UI must not conflate them. Backfilling would mean
inventing tallies for merges that were never measured, which is exactly the
kind of plausible-looking fiction an ISMS document must not carry.

Revision ID: docgen003
Revises: docgen002
Create Date: 2026-08-22 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "docgen003"
down_revision = "docgen002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("change_summary", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("document_versions", "change_summary")
