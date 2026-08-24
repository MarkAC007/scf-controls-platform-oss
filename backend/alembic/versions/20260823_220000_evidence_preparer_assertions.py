"""Record what the preparer asserts about evidence, not only what the file is (#786, #802).

An `EvidenceFile` has always recorded when it arrived, how big it is, what it
hashes to and who dragged it in. It records nothing about *what it is evidence
of*, and two of the three most-tested areas in an audit are therefore not
expressible in the schema at all.

**Effective period (#786).** Freshness is computed from `uploaded_at`. A 2023
access review uploaded this morning paints green, and the platform reports the
control as freshly evidenced on the strength of an artefact describing a window
three years old. What an auditor tests is period coverage — does the uploaded
set cover 1 Jan to 31 Dec, or the fortnight before they called — and that
question needs the window the artefact actually covers, asserted by the person
who prepared it.

**Population and sampling (#802).** "E-HRS-16 has 2 files" is not a conclusion.
Two of two joiners and two of four hundred are different findings. Without a
declared population there is no sampling adequacy, and without sampling
adequacy nothing in the evidence set is supportable.

**IPE (#802).** Completeness and accuracy of Information Produced by the Entity
is the most-failed area in Big 4 testing. For any system-generated export the
auditor asks who ran it, against which system, on what filter, on what date,
and how anyone knows the extract is complete. None of it was captured.

All twelve columns are nullable and **nothing is back-filled**. The temptation
is `effective_period_start := uploaded_at`, and it is exactly wrong: these
columns exist to record that a human took responsibility for a claim, so
inventing the claim on their behalf destroys the only property that makes them
worth having. Every existing row lands as "not asserted", which is a true
statement about it, and the UI renders that state distinctly rather than
showing an empty field that reads like an oversight.

**Nothing consumes these columns yet.** Anchoring coverage to the asserted
period is the next change in this epic; this migration and the capture form it
serves are deliberately inert with respect to scoring, so no organisation's
freshness, maturity or KSI numbers move when it deploys.

Revision ID: evassertions001
Revises: evintegrity001
Create Date: 2026-08-24 09:00:00
"""
import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "evassertions001"
down_revision = "evintegrity001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

_FK_IPE_EXTRACTED_BY = "fk_evidence_files_ipe_extracted_by_user_id"

#: Dropped in reverse order on downgrade. Kept as one list so the two halves of
#: this migration cannot drift apart.
_COLUMNS = [
    ("effective_period_start", sa.Date()),
    ("effective_period_end", sa.Date()),
    ("population_size", sa.Integer()),
    ("population_source", sa.Text()),
    ("sample_size", sa.Integer()),
    ("sample_method", sa.String(length=100)),
    ("sample_basis", sa.Text()),
    ("ipe_source_system", sa.String(length=255)),
    ("ipe_query_or_filter", sa.Text()),
    ("ipe_extracted_by_user_id", postgresql.UUID(as_uuid=True)),
    ("ipe_extracted_at", sa.DateTime(timezone=False)),
    ("ipe_completeness_check", sa.Text()),
]


def upgrade() -> None:
    for name, type_ in _COLUMNS:
        op.add_column("evidence_files", sa.Column(name, type_, nullable=True))

    # ON DELETE SET NULL, matching every other user reference on this table: a
    # deleted account must not take the evidence record with it, and the
    # assertion that *someone* ran the export survives losing their name.
    op.create_foreign_key(
        _FK_IPE_EXTRACTED_BY,
        "evidence_files",
        "users",
        ["ipe_extracted_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )

    total = op.get_bind().execute(
        sa.text("SELECT count(*) FROM evidence_files WHERE is_deleted = false")
    ).scalar()
    logger.info(
        "Preparer assertion columns added. %s existing evidence file(s) remain "
        "'not asserted' — deliberately not back-filled, since inventing an "
        "assertion is the one thing these columns exist to prevent. Nothing "
        "consumes them yet, so no score changes from this migration.",
        total,
    )


def downgrade() -> None:
    op.drop_constraint(_FK_IPE_EXTRACTED_BY, "evidence_files", type_="foreignkey")
    for name, _ in reversed(_COLUMNS):
        op.drop_column("evidence_files", name)
