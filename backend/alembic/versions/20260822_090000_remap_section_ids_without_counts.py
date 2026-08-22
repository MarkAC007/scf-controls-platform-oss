"""Remap document_sections.section_id after dropping count parentheticals from slugs.

``normalise_section_id`` now strips a trailing parenthetical whose content
starts with a digit, so ``### 3. GOV — Governance & Risk Management (12
controls)`` slugs to ``gov-governance-risk-management`` rather than
``gov-governance-risk-management-12-controls``. Every stored id derived from a
counted heading is therefore now wrong: the next regeneration would parse the
same heading to a *new* id, find no prior row under it, mark the new section
``new``, and mark the old row ``pending_retirement`` — stranding the human edit
attached to it on a ghost section. That is the very failure the code change
exists to stop, so the existing rows have to move with it.

**How the new ids are derived.** From the rows, not from the markdown. Each row
already stores ``heading_text``, ``heading_level`` and ``ordinal``, which is
everything ``parse_markdown_sections`` uses; walking them in ordinal order with
a level stack reproduces its ``parent.child`` paths exactly. Reparsing
``merged_content`` instead would be wrong for precisely the rows that matter
most: a retired section is re-rendered at the end of the document at its
original depth, so it reparses under whichever heading now precedes it, and its
row would be remapped onto somebody else's identity. The rows carry that same
hazard in their ordinals -- retirees are numbered after every live section --
which is why ``status`` is selected here and why ``recompute_section_ids``
re-slugs a retiree against its own stored parent path instead of the walk.

**Collisions.** ``uq_document_sections_doc_section`` makes
``(document_id, section_id)`` unique. Two sibling headings that differed only
by their counts collapse to the same slug once the counts are gone, and a
document containing such a pair would abort this migration for the whole
estate. A colliding row keeps its current id and is logged: one section whose
edit is stranded is a bounded, visible problem that a human can fix from the
UI; a migration that will not apply is not.

**Why the mapping function lives in application code.** This migration's whole
definition is "apply today's slug rule to yesterday's rows", so freezing a copy
of that rule here would only guarantee the two drift. It imports
``services.doc_gen.section_parser.recompute_section_ids``, which is unit-tested
in ``tests/doc_gen/test_section_parser.py``.

Revision ID: docgen002
Revises: docgen001
Create Date: 2026-08-22 09:00:00.000000
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "docgen002"
down_revision = "docgen001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    from services.doc_gen.section_parser import recompute_section_ids

    bind = op.get_bind()
    rows = bind.execute(sa.text(
        "SELECT id, document_id, section_id, heading_text, heading_level, "
        "ordinal, status "
        "FROM document_sections ORDER BY document_id, ordinal"
    )).mappings().all()

    by_document = {}
    for row in rows:
        by_document.setdefault(row["document_id"], []).append(dict(row))

    renamed = 0
    collided = 0
    for document_id, document_rows in by_document.items():
        remap = recompute_section_ids(document_rows)
        if remap.collisions:
            collided += len(remap.collisions)
            logger.warning(
                "docgen002: document %s kept %d colliding section id(s): %s",
                document_id, len(remap.collisions), ", ".join(remap.collisions),
            )
        if not remap.changes:
            continue

        # Two passes through a temporary namespace. A single-pass UPDATE can
        # transiently collide with a row this pass has not reached yet -- the
        # unique constraint is checked per statement, not deferred to commit.
        # The temporary value is the row's own primary key behind a "~~"
        # prefix: unique by construction, short enough for the column, and not
        # a shape ``normalise_section_id`` can ever produce.
        by_old = {r["section_id"]: r["id"] for r in document_rows}
        for old_id in remap.changes:
            row_id = by_old[old_id]
            bind.execute(
                sa.text(
                    "UPDATE document_sections SET section_id = :tmp WHERE id = :row_id"
                ),
                {"tmp": f"~~{row_id}", "row_id": row_id},
            )
        for old_id, new_id in remap.changes.items():
            bind.execute(
                sa.text(
                    "UPDATE document_sections SET section_id = :new WHERE id = :row_id"
                ),
                {"new": new_id, "row_id": by_old[old_id]},
            )
        renamed += len(remap.changes)

    logger.info(
        "docgen002: remapped %d section id(s) across %d document(s); "
        "%d left unchanged by the collision guard",
        renamed, len(by_document), collided,
    )


def downgrade() -> None:
    """Deliberately a no-op, and it has to be.

    The mapping is not invertible. Stripping a count parenthetical is lossy:
    ``gov-governance`` does not carry the ``(12 controls)`` it came from, and
    the count that *would* be reconstructed from today's scope is not
    necessarily the count that was in the heading when the id was first
    written. Guessing would fabricate ids that match neither the old rows nor
    the new ones.

    Nothing is lost by leaving the new ids in place. On a downgrade the old
    ``normalise_section_id`` returns, and the first regeneration afterwards
    would retire these sections and create counted ones — the same one-off
    churn this migration was written to avoid, but no data loss: retirement
    preserves content, and ``document_versions`` snapshots are untouched
    either way.
    """
