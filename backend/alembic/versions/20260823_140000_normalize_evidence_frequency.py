"""Normalise evidence_tracking.frequency to the canonical vocabulary (#783).

`evidence_tracking.frequency` is a bare String(50) with no constraint that has
had two writers: a wizard dropdown emitting `annually` and a free-text box whose
placeholder suggested `Annual`. Downstream, the freshness engine only understood
`annual`, so every row written by the dropdown was judged against a 30-day
staleness threshold instead of 370 — eleven months of false-overdue per year on
every annual control.

The write path is now closed by a Pydantic validator. This migration cleans what
is already in the table so the read path agrees with it.

**Conservative by design.** Only spellings the shared alias table already
recognises are rewritten. Anything unrecognised is LEFT ALONE and reported in
the migration log — guessing at a value an operator typed is how you turn a
visible data-quality problem into an invisible one.

Revision ID: freqvocab001
Revises: docgen003
Create Date: 2026-08-23 14:00:00
"""
import logging

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "freqvocab001"
down_revision = "docgen003"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


def _load_vocabulary():
    """Import the shared vocabulary lazily, as env.py puts backend/ on sys.path.

    Copying the alias table into this file would create exactly the second
    declaration #783 exists to remove — and this migration would then be the
    one place guaranteed never to be updated when the vocabulary changes.
    """
    from services.frequency_vocabulary import ALIASES, STALENESS_DAYS, normalize

    return ALIASES, STALENESS_DAYS, normalize


def upgrade() -> None:
    _, canonical_map, normalize = _load_vocabulary()
    canonical = set(canonical_map)

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT DISTINCT frequency FROM evidence_tracking "
            "WHERE frequency IS NOT NULL AND frequency <> ''"
        )
    ).fetchall()

    rewritten = 0
    unmapped = []
    for (raw,) in rows:
        if raw in canonical:
            continue
        resolved = normalize(raw)
        if resolved is None:
            unmapped.append(raw)
            continue
        result = conn.execute(
            sa.text(
                "UPDATE evidence_tracking SET frequency = :new WHERE frequency = :old"
            ),
            {"new": resolved, "old": raw},
        )
        rewritten += result.rowcount or 0
        logger.info("frequency normalised: %r -> %r (%s rows)", raw, resolved, result.rowcount)

    logger.info("#783 frequency normalisation: %s rows rewritten", rewritten)
    if unmapped:
        # Deliberately a warning, not a failure. An operator's unrecognised
        # value is a data-quality signal to act on, not a reason to block a
        # deploy — and silently guessing at it would be worse than either.
        logger.warning(
            "#783 frequency normalisation: %s value(s) left unchanged because no "
            "canonical mapping exists: %s",
            len(unmapped),
            ", ".join(repr(v) for v in sorted(unmapped)),
        )


def downgrade() -> None:
    """Restore the two spellings the pre-#783 code could actually read.

    The upgrade collapses several spellings onto one canonical value
    (`annually`, `yearly` and `Annual` all become `annual`), so the exact
    original is not recoverable — restoring an arbitrary one would be a
    fabrication. This downgrade therefore restores only what a rollback needs to
    keep working, and says plainly what it cannot fix.

    Two canonical values are NEW as of #783 and would be unreadable to rolled-
    back code:

      * `semi_annual` — the old ``task_generator.FREQUENCY_DAYS`` held
        `semi-annual` / `semi-annually` (hyphens), not the underscore form, so
        without this rewrite the old generator would log "Invalid frequency" and
        create ZERO tasks for those rows.
      * `biweekly`    — same, for `bi-weekly`.

    Both are rewritten back to the hyphenated forms below.

    **What this cannot restore, stated rather than hidden:** the old
    ``validation_service.STALENESS_THRESHOLDS`` had no key for either cadence in
    any spelling, so after a rollback those rows fall back to the 30-day default
    for freshness. That is a pre-existing gap this PR closes, not one the
    migration creates — but a rollback reopens it, and an operator rolling back
    should know that rather than discover it as false-red.

    Every other canonical value (`daily`, `weekly`, `monthly`, `quarterly`,
    `annual`, `real_time`, `on_demand`) was already a key in the old maps, so no
    rewrite is needed for them.
    """
    conn = op.get_bind()
    for canonical, legacy in (("semi_annual", "semi-annual"), ("biweekly", "bi-weekly")):
        result = conn.execute(
            sa.text(
                "UPDATE evidence_tracking SET frequency = :legacy WHERE frequency = :canonical"
            ),
            {"legacy": legacy, "canonical": canonical},
        )
        if result.rowcount:
            logger.info(
                "frequency rolled back: %r -> %r (%s rows)", canonical, legacy, result.rowcount
            )
