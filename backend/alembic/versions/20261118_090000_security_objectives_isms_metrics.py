"""Security objectives and ISMS metrics: ``security_objectives``, ``objective_metrics``, ``isms_metric_results``.

Revision ID: ismsobj001
Revises: orgaud001
Create Date: 2026-11-18 09:00:00

Clause 6.2 objectives leave the free-form ``isms_profiles.objectives`` JSON
list and become rows of their own:

* ``security_objectives`` holds the objective: what will be done, resources,
  the responsible team, type (maintain or achieve), dates, lifecycle and the
  ISO clauses. Archived, never deleted.
* ``objective_metrics`` holds the measure and target in force, versioned: a
  change closes the active row and opens a new one. At most one row per
  objective is active.
* ``isms_metric_results`` holds every reading, computed nightly or logged by
  the team. Never updated, never deleted.

Both child tables carry ``organization_id`` and reach their parent through a
composite key on (organization_id, id), and the objective reaches its team the
same way (``fk_security_objectives_team_org``, ``ON DELETE SET NULL
(owning_team_id)``), so a row cannot point across tenants.

Backfill: every element of every organisation's ``isms_profiles.objectives``
array becomes one ``migrated`` objective, in list order (``OBJ-01`` ...), with
one ``unset`` metric row carrying the old measure and target text and one
``audit_log`` row (``field_name = 'migrated_from_isms_profile'``) holding the
original element. The objective ids are derived from the organisation and the
list position, so an upgrade after a downgrade gives the same ids and does not
write a second audit row for the same element at the same position
(audit_log is append-only; the first rows outlive a downgrade). The check
compares the element text too, so an element that moved into a position gets
its own provenance row. Profiles whose ``objectives`` is not an array, and
due dates that do not parse (kept as NULL), are logged. Elements in, objectives out and metric rows
out are counted per organisation; any mismatch stops the migration.
``isms_profiles.objectives`` itself is not modified.

Downgrade drops the three tables, but refuses while anything exists that only
lives in them: a logged result, a custom objective, a migrated or starter
objective edited since it was created, or a starter that has left
``proposed``. Computed results are not protected, and they are lost: each
nightly reading was taken from the registers as they stood that day, and
there is no as-of query to take it again. The downgrade logs how many computed
rows it drops and the dates they cover.
"""
import json
import logging
import uuid
from datetime import date, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID


revision = "ismsobj001"
down_revision = "orgaud001"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

OBJECTIVE_TYPES = ("maintain", "achieve")
LIFECYCLES = ("proposed", "active", "achieved", "missed")
KINDS = ("live", "logged", "milestone", "unset")
UNITS = ("percent", "count")
DIRECTIONS = ("at_least", "at_most")
FREQUENCIES = ("nightly", "monthly", "quarterly", "annually")
RESULT_SOURCES = ("computed", "logged")
#: Frozen copy of services.isms_metrics.status.STATUSES (a migration does not
#: import application code). tests/test_isms_objectives_migration.py pins it.
RESULT_STATUSES = (
    "on_track", "at_risk", "off_track", "in_progress", "proposed",
    "insufficient_data", "first_measurement_due", "measurement_overdue", "not_measured",
)
SOURCE_CHECK = "source IN ('custom', 'migrated') OR source LIKE 'starter:%'"

#: Namespace for the derived objective and metric ids (stable across re-runs).
_ID_NAMESPACE = uuid.UUID("5b0d6f8e-3c1a-4c8e-9a52-6f0e2d1b7a01")

_STATUS_MAP = {"planned": "active", "in_progress": "active", "achieved": "achieved", "missed": "missed"}


def _in(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _user_fk(name: str) -> sa.Column:
    return sa.Column(name, UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


def upgrade() -> None:
    op.create_table(
        "security_objectives",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "organization_id", UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("ref", sa.String(16), nullable=False),
        sa.Column("legacy_row_id", sa.String(64), nullable=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("planned_actions", sa.Text(), nullable=False, server_default=""),
        sa.Column("resources", sa.Text(), nullable=False, server_default=""),
        sa.Column("evaluation_method", sa.Text(), nullable=False, server_default=""),
        sa.Column("objective_type", sa.String(16), nullable=False, server_default="maintain"),
        # Constrained by fk_security_objectives_team_org below, not a single-column key.
        sa.Column("owning_team_id", UUID(as_uuid=True), nullable=True),
        _user_fk("owner_user_id"),
        sa.Column("owner_text", sa.Text(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("review_date", sa.Date(), nullable=True),
        sa.Column("lifecycle_status", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("iso_clauses", ARRAY(sa.String()), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(80), nullable=False, server_default="custom"),
        sa.Column("last_evaluated_at", sa.DateTime(), nullable=True),
        _user_fk("last_evaluated_by_user_id"),
        sa.Column("last_evaluation_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        _user_fk("created_by_user_id"),
        _user_fk("updated_by_user_id"),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("organization_id", "ref", name="uq_security_objectives_org_ref"),
        sa.UniqueConstraint("organization_id", "id", name="uq_security_objectives_org_id"),
        sa.CheckConstraint(f"objective_type IN ({_in(OBJECTIVE_TYPES)})", name="ck_security_objectives_type"),
        sa.CheckConstraint(f"lifecycle_status IN ({_in(LIFECYCLES)})", name="ck_security_objectives_lifecycle"),
        # A new source needs a migration, and that migration meets this file's downgrade guard.
        sa.CheckConstraint(SOURCE_CHECK, name="ck_security_objectives_source"),
    )
    # The team belongs to the objective's organisation. SET NULL on
    # owning_team_id alone (PostgreSQL 15+): deleting a team clears the team
    # and leaves organization_id, which is NOT NULL, untouched. Raw SQL because
    # SQLAlchemy's DDL compiler rejects the column list ("Unexpected SQL
    # phrase"), as in evtaskteam1 (20260824_160000).
    op.execute(
        """
        ALTER TABLE security_objectives
          ADD CONSTRAINT fk_security_objectives_team_org
          FOREIGN KEY (organization_id, owning_team_id)
          REFERENCES teams (organization_id, id)
          ON DELETE SET NULL (owning_team_id)
        """
    )
    op.create_index("ix_security_objectives_organization_id", "security_objectives", ["organization_id"])
    # The composite key is organization_id-leftmost; this serves the team-delete action.
    op.create_index("ix_security_objectives_owning_team_id", "security_objectives", ["owning_team_id"])
    op.create_index(
        "uq_security_objectives_org_starter", "security_objectives", ["organization_id", "source"],
        unique=True, postgresql_where=sa.text("source LIKE 'starter:%'"),
    )

    op.create_table(
        "objective_metrics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("objective_id", UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("measure_key", sa.String(64), nullable=True),
        sa.Column("params", JSONB(), nullable=False, server_default="{}"),
        sa.Column("name", sa.Text(), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("unit", sa.String(16), nullable=True),
        sa.Column("direction", sa.String(16), nullable=True),
        sa.Column("target_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("warn_value", sa.Numeric(12, 2), nullable=True),
        sa.Column("target_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("min_population", sa.Integer(), nullable=True),
        sa.Column("frequency", sa.String(16), nullable=True),
        _user_fk("target_set_by_user_id"),
        sa.Column("target_set_at", sa.DateTime(), nullable=True),
        sa.Column("active_from", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("active_to", sa.DateTime(), nullable=True),
        _user_fk("created_by_user_id"),
        sa.ForeignKeyConstraint(
            ["organization_id", "objective_id"],
            ["security_objectives.organization_id", "security_objectives.id"],
            name="fk_objective_metrics_objective_org", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_objective_metrics_org_id"),
        sa.CheckConstraint(f"kind IN ({_in(KINDS)})", name="ck_objective_metrics_kind"),
        sa.CheckConstraint("kind <> 'live' OR measure_key IS NOT NULL", name="ck_objective_metrics_live_measure"),
        sa.CheckConstraint(f"unit IS NULL OR unit IN ({_in(UNITS)})", name="ck_objective_metrics_unit"),
        sa.CheckConstraint(
            f"direction IS NULL OR direction IN ({_in(DIRECTIONS)})", name="ck_objective_metrics_direction"
        ),
        sa.CheckConstraint(
            f"frequency IS NULL OR frequency IN ({_in(FREQUENCIES)})", name="ck_objective_metrics_frequency"
        ),
    )
    op.create_index(
        "uq_objective_metrics_one_active", "objective_metrics", ["objective_id"],
        unique=True, postgresql_where=sa.text("active_to IS NULL"),
    )
    # Every version, closed rows too: the version join and the metric detail.
    op.create_index("ix_objective_metrics_objective_id", "objective_metrics", ["objective_id"])

    op.create_table(
        "isms_metric_results",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
        sa.Column("objective_metric_id", UUID(as_uuid=True), nullable=False),
        sa.Column("measured_on", sa.Date(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(14, 4), nullable=True),
        sa.Column("numerator", sa.Integer(), nullable=True),
        sa.Column("denominator", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("detail", JSONB(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(16), nullable=False),
        _user_fk("entered_by_user_id"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "objective_metric_id"],
            ["objective_metrics.organization_id", "objective_metrics.id"],
            name="fk_isms_metric_results_metric_org", ondelete="CASCADE",
        ),
        sa.CheckConstraint(f"source IN ({_in(RESULT_SOURCES)})", name="ck_isms_metric_results_source"),
        sa.CheckConstraint(f"status IN ({_in(RESULT_STATUSES)})", name="ck_isms_metric_results_status"),
    )
    op.create_index(
        "uq_isms_metric_results_computed_day", "isms_metric_results", ["objective_metric_id", "measured_on"],
        unique=True, postgresql_where=sa.text("source = 'computed'"),
    )
    op.create_index(
        "ix_isms_metric_results_metric_period", "isms_metric_results", ["objective_metric_id", "period_start"]
    )

    _migrate_profile_objectives()


# ---------------------------------------------------------------------------
# Backfill from isms_profiles.objectives
# ---------------------------------------------------------------------------

# jsonb_array_elements and jsonb_array_length raise on a non-array, and SQL does
# not promise to evaluate a WHERE before the expressions it guards, so each call
# sits behind its own CASE: a malformed profile must not take the whole
# migration down with it (tests/test_isms_objectives_migration.py caught
# jsonb_array_length running first).
_ELEMENTS = sa.text(
    """
    SELECT p.organization_id, e.ord, e.elem::text AS elem_text
    FROM isms_profiles p
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(p.objectives) = 'array' THEN p.objectives ELSE '[]'::jsonb END
    ) WITH ORDINALITY AS e(elem, ord)
    ORDER BY p.organization_id, e.ord
    """
)

_COUNT_IN = sa.text(
    """
    SELECT organization_id, n FROM (
        SELECT organization_id,
               CASE WHEN jsonb_typeof(objectives) = 'array' THEN jsonb_array_length(objectives) ELSE 0 END AS n
        FROM isms_profiles
    ) counted
    WHERE n > 0
    """
)

_NOT_ARRAY = sa.text(
    """
    SELECT organization_id, jsonb_typeof(objectives) FROM isms_profiles
    WHERE objectives IS NOT NULL AND jsonb_typeof(objectives) <> 'array'
    ORDER BY organization_id
    """
)

_COUNT_OUT = sa.text(
    """
    SELECT o.organization_id, count(DISTINCT o.id), count(m.id)
    FROM security_objectives o
    LEFT JOIN objective_metrics m ON m.objective_id = o.id AND m.organization_id = o.organization_id
    WHERE o.source = 'migrated'
    GROUP BY o.organization_id
    """
)

_INSERT_OBJECTIVE = sa.text(
    """
    INSERT INTO security_objectives
        (id, organization_id, ref, legacy_row_id, name, planned_actions, resources, evaluation_method,
         objective_type, owner_text, due_date, lifecycle_status, iso_clauses, source, created_at, updated_at)
    VALUES
        (:id, :org, :ref, :legacy, :name, '', :resources, :evaluation,
         :otype, :owner, :due, :lifecycle, '{}', 'migrated', :ts, :ts)
    """
)

_INSERT_METRIC = sa.text(
    """
    INSERT INTO objective_metrics
        (id, organization_id, objective_id, kind, params, name, description, target_text, active_from)
    VALUES (:id, :org, :objective, 'unset', '{}', '', :description, :target, :ts)
    """
)

_AUDIT_EXISTS = sa.text(
    """
    SELECT 1 FROM audit_log
    WHERE entity_type = 'security_objective' AND entity_id = :id
      AND field_name = 'migrated_from_isms_profile' AND new_value = :value
    LIMIT 1
    """
)

_INSERT_AUDIT = sa.text(
    """
    INSERT INTO audit_log
        (id, organization_id, entity_type, entity_id, action, field_name, old_value, new_value,
         changed_by_user_id, action_source)
    VALUES (gen_random_uuid(), :org, 'security_objective', :id, 'create', 'migrated_from_isms_profile',
            NULL, :value, NULL, 'system')
    """
)


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value)


def _due(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _row(org_id, position: int, element) -> dict:
    if isinstance(element, dict):
        name = _text(element.get("objective")) or "Untitled objective"
        due = _due(element.get("due_date"))
        status = element.get("status")
        legacy = element.get("id")
        legacy = str(legacy)[:64] if legacy not in (None, "") else None
        return {
            "name": name,
            "legacy": legacy,
            "resources": _text(element.get("resources")),
            "evaluation": _text(element.get("evaluation")),
            "owner": _text(element.get("owner")) or None,
            "due": due,
            "otype": "achieve" if due is not None else "maintain",
            # A list or object status is unhashable; anything not a known string is active.
            "lifecycle": _STATUS_MAP.get(status, "active") if isinstance(status, str) else "active",
            "measure": _text(element.get("measure")),
            "target": _text(element.get("target")),
        }
    return {
        "name": (_text(element) or "Untitled objective")[:300],
        "legacy": None, "resources": "", "evaluation": "", "owner": None, "due": None,
        "otype": "maintain", "lifecycle": "active", "measure": "", "target": "",
    }


def _migrate_profile_objectives() -> None:
    bind = op.get_bind()
    now = datetime.utcnow()
    for org_id, kind in bind.execute(_NOT_ARRAY).fetchall():
        logger.warning(
            "ismsobj001: organisation %s has isms_profiles.objectives of type %s, not an array; "
            "nothing migrated for it", org_id, kind,
        )
    elements = bind.execute(_ELEMENTS).fetchall()
    audited = 0
    for org_id, position, elem_text in elements:
        element = json.loads(elem_text)
        row = _row(org_id, int(position), element)
        objective_id = uuid.uuid5(_ID_NAMESPACE, f"{org_id}:objective:{int(position)}")
        metric_id = uuid.uuid5(_ID_NAMESPACE, f"{org_id}:metric:{int(position)}")
        ref = f"OBJ-{int(position):02d}"
        raw_due = element.get("due_date") if isinstance(element, dict) else None
        if raw_due not in (None, "") and row["due"] is None:
            logger.warning(
                "ismsobj001: organisation %s %s has due_date %r, which is not a date; kept as NULL "
                "(the original is in the audit row)", org_id, ref, raw_due,
            )
        bind.execute(_INSERT_OBJECTIVE, {
            "id": objective_id, "org": org_id, "ref": ref, "legacy": row["legacy"], "name": row["name"],
            "resources": row["resources"], "evaluation": row["evaluation"], "otype": row["otype"],
            "owner": row["owner"], "due": row["due"], "lifecycle": row["lifecycle"], "ts": now,
        })
        bind.execute(_INSERT_METRIC, {
            "id": metric_id, "org": org_id, "objective": objective_id,
            "description": row["measure"], "target": row["target"], "ts": now,
        })
        if bind.execute(_AUDIT_EXISTS, {"id": objective_id, "value": elem_text}).first() is None:
            bind.execute(_INSERT_AUDIT, {"org": org_id, "id": objective_id, "value": elem_text})
            audited += 1

    expected = {org: int(n) for org, n in bind.execute(_COUNT_IN).fetchall()}
    measured = {org: (int(o), int(m)) for org, o, m in bind.execute(_COUNT_OUT).fetchall()}
    for org in set(expected) | set(measured):
        want = expected.get(org, 0)
        objectives, metrics = measured.get(org, (0, 0))
        if not (want == objectives == metrics):
            raise RuntimeError(
                f"ismsobj001: organisation {org} has {want} objective(s) in isms_profiles.objectives "
                f"but {objectives} objective(s) and {metrics} metric row(s) were written"
            )
    logger.info(
        "ismsobj001: %s objective(s) migrated from isms_profiles.objectives across %s organisation(s); "
        "%s audit row(s) written",
        len(elements), len(expected), audited,
    )


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------

_COMPUTED = sa.text(
    "SELECT count(*), min(measured_on), max(measured_on) FROM isms_metric_results WHERE source = 'computed'"
)

_ONLY_HERE = sa.text(
    """
    SELECT
      (SELECT count(*) FROM isms_metric_results WHERE source = 'logged'),
      (SELECT count(*) FROM security_objectives WHERE source = 'custom'),
      (SELECT count(*) FROM security_objectives
        WHERE (source = 'migrated' OR source LIKE 'starter:%') AND updated_at > created_at),
      (SELECT count(*) FROM security_objectives
        WHERE source LIKE 'starter:%' AND lifecycle_status <> 'proposed')
    """
)


def downgrade() -> None:
    bind = op.get_bind()
    logged, custom, edited, confirmed = bind.execute(_ONLY_HERE).one()
    if logged or custom or edited or confirmed:
        raise RuntimeError(
            f"Cannot downgrade ismsobj001: {logged} logged result(s), {custom} custom objective(s), "
            f"{edited} migrated or starter objective(s) edited since creation and {confirmed} confirmed "
            "starter(s) exist only in security_objectives / isms_metric_results and would be dropped. "
            "Export them first, then remove them with SQL: the application has no delete route (objectives "
            "are archived), and GET /api/organizations/{org_id}/isms-metrics/{id} returns at most 100 results "
            "per objective, so export isms_metric_results with SQL as well. This migration will not delete them."
        )
    computed, first, last = bind.execute(_COMPUTED).one()
    logger.warning(
        "ismsobj001 downgrade: dropping %s computed reading(s) measured %s to %s; they cannot be taken again",
        computed, first or "-", last or "-",
    )
    op.drop_index("ix_isms_metric_results_metric_period", table_name="isms_metric_results")
    op.drop_index("uq_isms_metric_results_computed_day", table_name="isms_metric_results")
    op.drop_table("isms_metric_results")
    op.drop_index("ix_objective_metrics_objective_id", table_name="objective_metrics")
    op.drop_index("uq_objective_metrics_one_active", table_name="objective_metrics")
    op.drop_table("objective_metrics")
    op.drop_index("uq_security_objectives_org_starter", table_name="security_objectives")
    op.drop_index("ix_security_objectives_owning_team_id", table_name="security_objectives")
    op.drop_constraint("fk_security_objectives_team_org", "security_objectives", type_="foreignkey")
    op.drop_index("ix_security_objectives_organization_id", table_name="security_objectives")
    op.drop_table("security_objectives")
