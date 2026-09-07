"""Drop the five retired Control Documents Mapper (CDM) tables (#907).

Revision ID: cdmdrop001
Revises: evassessver1
Create Date: 2026-09-10 12:00:00

**Breaking, one-way.** Drops ``cdm_mappings``, ``cdm_control_proposals``,
``cdm_document_intents``, ``cdm_document_chunks`` and ``cdm_documents``, then
removes the per-tenant ``cdm_enabled`` key from ``organizations.settings``.
The CDM routes left in the previous release; nothing reads these tables any
more. Uploaded CDM files are NOT removed here — object storage has no place in
a schema migration — run ``scripts/cdm_retirement_purge.py`` after the upgrade.

**If any of the five tables still holds rows, this migration refuses to run
unless ``SCF_CDM_DROP_ACK=1`` is set.** ``scripts/upgrade.sh`` takes a
``pg_dump`` and an object-store backup before it migrates, but the bare
``docker compose up --build`` path (with ``SCF_MIGRATE_ACK=any`` in ``.env``)
has taken no backup at all, and a drop is the one migration that cannot be
walked back by the next release. The refusal names the row counts, the backup
to keep and the variable to set. Add ``SCF_CDM_DROP_ACK=1`` to ``.env`` for the
upgrade run and remove it afterwards.

Before dropping, the row counts and the storage-key manifest
(``cdm/{organization_id}/{document_id}/`` per document) are written to the
migration log so the purge report can be checked against them.

Downgrade restores an empty schema only. Operator rollback is
``upgrade.sh --rollback <ts>``, which restores rows and files from the
pre-upgrade backup — copy that backup set out of ``./backups/`` before
``backup.sh`` prunes it. The seven historical CDM migrations stay in the chain
(``cdm4consol001`` is a parent of ``auditlognull01`` and of the v0.12.1 merge
revision); this migration's ``downgrade()`` recreates the schema those seven
built, so a dev-database ``downgrade``/``upgrade`` round-trip works.
``scripts/upgrade.sh`` refuses Alembic downgrades on a real install.
"""
from __future__ import annotations

import logging
import os
from typing import Dict, List

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "cdmdrop001"
down_revision = "evassessver1"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

ACK_ENV = "SCF_CDM_DROP_ACK"
PURGE_SCRIPT = "scripts/cdm_retirement_purge.py"

#: Children before parents. ``cdm_mappings`` references chunks and proposals
#: (both SET NULL) as well as documents (CASCADE); proposals, intents and
#: chunks reference documents. ``DROP TABLE IF EXISTS`` without CASCADE, so an
#: unexpected dependent object fails loudly instead of being taken silently.
TABLES_IN_DROP_ORDER = (
    "cdm_mappings",
    "cdm_control_proposals",
    "cdm_document_intents",
    "cdm_document_chunks",
    "cdm_documents",
)


def _row_counts(conn) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table in TABLES_IN_DROP_ORDER:
        exists = conn.execute(sa.text("SELECT to_regclass(:t) IS NOT NULL"), {"t": table}).scalar()
        if not exists:
            counts[table] = 0
            continue
        # {table} is an identifier from TABLES_IN_DROP_ORDER, not user input; semgrep only honours
        # the suppression on the line directly above the call.
        # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
        counts[table] = int(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar() or 0)
    return counts


def _manifest(conn) -> List[str]:
    """One ``cdm/{org}/{doc}/`` prefix per document — the keys the purge removes."""
    exists = conn.execute(sa.text("SELECT to_regclass('cdm_documents') IS NOT NULL")).scalar()
    if not exists:
        return []
    rows = conn.execute(
        sa.text("SELECT organization_id, id FROM cdm_documents ORDER BY organization_id, created_at")
    ).fetchall()
    return [f"cdm/{org_id}/{doc_id}/" for org_id, doc_id in rows]


def _refusal(counts: Dict[str, int]) -> str:
    lines = [
        "REFUSING to drop the CDM tables: they still hold rows and "
        f"{ACK_ENV} is not set to 1.",
        "",
        "Row counts:",
    ]
    lines += [f"  {table}: {n}" for table, n in counts.items()]
    lines += [
        "",
        "This drop is one-way. Make sure you have the pre-upgrade backup that "
        "scripts/upgrade.sh took (pg_dump + object store under ./backups/<ts>_*) "
        "and copy that backup set somewhere backup.sh will not prune it.",
        f"Then add {ACK_ENV}=1 to .env, re-run the upgrade, and remove the "
        "variable afterwards. Never leave SCF_MIGRATE_ACK=any in .env.",
        "Note: under scripts/upgrade.sh this refusal counts as a failed migration "
        "and the script rolls the whole upgrade back automatically (database and "
        "object store restored from the backup it just took). Run "
        "scripts/cdm_retirement_probe.py first and set the ack BEFORE upgrading.",
        f"After the upgrade, run {PURGE_SCRIPT} to remove the uploaded files.",
    ]
    return "\n".join(lines)


def upgrade() -> None:
    conn = op.get_bind()
    counts = _row_counts(conn)
    total = sum(counts.values())

    if total and os.getenv(ACK_ENV, "").strip() != "1":
        raise RuntimeError(_refusal(counts))

    logger.info("cdmdrop001: dropping the five CDM tables. Row counts: "
                + ", ".join(f"{t}={n}" for t, n in counts.items()))
    manifest = _manifest(conn)
    logger.info(f"cdmdrop001: {len(manifest)} document key prefix(es) in object storage:")
    for prefix in manifest:
        logger.info(f"  {prefix}")
    logger.info(f"cdmdrop001: after the upgrade completes, run `python {PURGE_SCRIPT}` (dry run) "
                "then `--apply` to remove those files.")

    for table in TABLES_IN_DROP_ORDER:
        # {table} is an identifier from TABLES_IN_DROP_ORDER, not user input.
        # nosemgrep: python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))

    # organizations.settings is a plain json column (models.Organization), so
    # the jsonb key operators need the cast; the result is cast back so the
    # column type is untouched. The jsonb_typeof guard keeps a pathological
    # non-object value (a bare string "cdm_enabled" also satisfies `?`) from
    # aborting a one-way migration with "cannot delete from scalar".
    op.execute(sa.text(
        "UPDATE organizations "
        "SET settings = (settings::jsonb - 'cdm_enabled')::json "
        "WHERE jsonb_typeof(settings::jsonb) = 'object' "
        "AND settings::jsonb ? 'cdm_enabled'"
    ))
    logger.info("cdmdrop001: done; removed the per-tenant cdm_enabled setting.")


def downgrade() -> None:
    """Recreate the five tables, empty, at the schema the seven CDM migrations
    left them (lm5n6o7p8q9r … cdm5ingstart01, plus cdm2c709chunk / cdm3intent001 /
    cdm4consol001 additions). Dev-only; no rows come back."""
    op.create_table(
        "cdm_documents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("upload_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kb_revision", sa.String(128), nullable=True),
        sa.Column("ingest_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("ingest_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # nm6o7p8q9r0s
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("kb_revision_at_ingest", sa.String(64), nullable=True),
        # cdm2c709chunk
        sa.Column("extracted_text_sha256", sa.String(64), nullable=True),
        sa.Column("extraction_backend", sa.String(32), nullable=True),
        # cdm3intent001
        sa.Column("intent_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("intent_error", sa.Text(), nullable=True),
        sa.Column("intent_classified_at", sa.DateTime(timezone=True), nullable=True),
        # cdm5ingstart01
        sa.Column("ingest_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_cdm_documents_org", "cdm_documents", ["organization_id"])
    op.create_index("ix_cdm_documents_sha256", "cdm_documents", ["organization_id", "sha256"])

    op.create_table(
        "cdm_document_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cdm_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("heading", sa.String(255), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=False),
        sa.Column("char_end", sa.Integer(), nullable=False),
        sa.Column("body_norm", sa.Text(), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(),
                  sa.Computed("to_tsvector('english', body)", persisted=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cdm_document_id"], ["cdm_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("cdm_document_id", "ordinal", name="uq_cdm_chunks_document_ordinal"),
        sa.CheckConstraint("char_end > char_start", name="ck_cdm_chunks_offsets_ordered"),
        sa.CheckConstraint("char_start >= 0", name="ck_cdm_chunks_offset_non_negative"),
    )
    op.create_index("ix_cdm_chunks_org_document", "cdm_document_chunks", ["organization_id", "cdm_document_id"])
    op.create_index("ix_cdm_chunks_search_vector", "cdm_document_chunks", ["search_vector"], postgresql_using="gin")

    op.create_table(
        "cdm_document_intents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cdm_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("classification_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("prompt_version", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cdm_document_id"], ["cdm_documents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("cdm_document_id", "domain", name="uq_cdm_document_intents"),
        sa.CheckConstraint("rank BETWEEN 1 AND 3", name="ck_cdm_document_intents_rank"),
    )
    op.create_index("ix_cdm_document_intents_org_domain", "cdm_document_intents", ["organization_id", "domain"])

    op.create_table(
        "cdm_control_proposals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scoped_control_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cdm_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("consolidated_score", sa.Float(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("citation_count", sa.SmallInteger(), nullable=False),
        sa.Column("citations_fingerprint", sa.String(64), nullable=False),
        sa.Column("recompute_provider", sa.String(32), nullable=True),
        sa.Column("recompute_model_id", sa.String(128), nullable=True),
        sa.Column("kb_revision", sa.String(128), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scoped_control_id"], ["scoped_controls.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cdm_document_id"], ["cdm_documents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["accepted_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["dismissed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("organization_id", "scoped_control_id", "cdm_document_id",
                            name="uq_cdm_control_proposals"),
        sa.CheckConstraint("status IN ('proposed', 'accepted', 'dismissed', 'stale')",
                           name="ck_cdm_control_proposals_status"),
    )
    op.create_index("ix_cdm_control_proposals_org_status", "cdm_control_proposals", ["organization_id", "status"])
    op.create_index("ix_cdm_control_proposals_org_document", "cdm_control_proposals",
                    ["organization_id", "cdm_document_id"])

    op.create_table(
        "cdm_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scoped_control_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("scoped_controls.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cdm_document_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("cdm_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("section", sa.String(255), nullable=True),
        sa.Column("byte_offset_start", sa.Integer(), nullable=False),
        sa.Column("byte_offset_end", sa.Integer(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="proposed"),
        sa.Column("kb_revision", sa.String(128), nullable=False),
        sa.Column("accepted_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismiss_reason", sa.Text(), nullable=True),
        sa.Column("dismissed_by_user_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # op7q8r9s0t1u
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        # cdm2c709chunk
        sa.Column("ts_rank_component", sa.Float(), nullable=True),
        sa.Column("objective_coverage_component", sa.Float(), nullable=True),
        sa.Column("term_overlap_component", sa.Float(), nullable=True),
        sa.Column("score_weights", postgresql.JSONB(), nullable=True),
        sa.Column("match_type", sa.String(24), nullable=True),
        sa.Column("matched_objective_text", sa.Text(), nullable=True),
        sa.Column("retrieval_tier", sa.String(24), nullable=True),
        sa.Column("cdm_document_chunk_id", postgresql.UUID(as_uuid=True), nullable=True),
        # cdm4consol001
        sa.Column("control_proposal_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key("fk_cdm_mappings_last_reviewed_by_user_id_users", "cdm_mappings", "users",
                          ["last_reviewed_by_user_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_cdm_mappings_chunk", "cdm_mappings", "cdm_document_chunks",
                          ["cdm_document_chunk_id"], ["id"], ondelete="SET NULL")
    op.create_foreign_key("fk_cdm_mappings_control_proposal", "cdm_mappings", "cdm_control_proposals",
                          ["control_proposal_id"], ["id"], ondelete="SET NULL")
    op.create_index("ix_cdm_mappings_org_status", "cdm_mappings", ["organization_id", "status"])
    op.create_index("ix_cdm_mappings_control", "cdm_mappings", ["organization_id", "scoped_control_id"])
    op.create_index("ix_cdm_mappings_document", "cdm_mappings", ["cdm_document_id"])
    op.create_index("ix_cdm_mappings_control_proposal", "cdm_mappings", ["control_proposal_id"])
