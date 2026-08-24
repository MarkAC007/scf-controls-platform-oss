"""Make audit_log append-only at the database level.

Revision ID: auditappendonly1
Revises: promptversion01
Create Date: 2026-08-24 01:00:00

``AuditLog``'s docstring has always called it "immutable record of
entity-level changes". Nothing enforced that. A single ``UPDATE audit_log
SET new_value = ...`` — from a mistaken migration, a support script, or
somebody covering their tracks — left no trace, in the one table whose
entire job is leaving traces. An audit trail that can be edited is not an
audit trail; it is a log.

Three statements are now refused by the database itself:

* ``UPDATE`` — except the one legitimate mutation (below).
* ``DELETE`` — except when the tenant itself is being removed (below).
* ``TRUNCATE`` — row-level triggers do not fire for TRUNCATE, so without a
  statement-level trigger the other two protections are bypassed by a
  single word.

**The two exceptions are not loopholes; they are existing, load-bearing
behaviour that this migration must not break.**

``audit_log.changed_by_user_id -> users.id`` is ``ON DELETE SET NULL`` and
the column was deliberately made nullable (revision ``auditlognull01``) so
that deleting a user does not destroy the record of what they did. That
cascade *is* an UPDATE. Refusing it outright would break the database
restore endpoint and the admin CLI, both named in that revision. So the
UPDATE trigger permits exactly one shape: every column identical except
``changed_by_user_id``, which may only end up NULL -- so a row can lose its
actor but never gain or swap one. The comparison is done over ``to_jsonb``
minus that one key, so a column added later is covered without anybody
remembering to add it here. (Deliberately absent: a check that the old value
was non-NULL. It would exclude only a NULL-to-NULL update of an otherwise
identical row -- a no-op the shape comparison already makes harmless -- and
an unfalsifiable condition in an enforcement rule is worse than no condition.)

``audit_log.organization_id -> organizations.id`` is ``ON DELETE CASCADE``:
removing an organization removes its audit rows. Three call sites rely on
it (``api/admin.py``, ``api/organizations.py``, ``api/provisioning.py``).
The DELETE trigger tells that apart from tampering by asking whether the
parent organization still exists — during a cascade Postgres has already
removed the parent row, so it does not; a direct ``DELETE FROM audit_log``
finds it very much alive. The rule this encodes is the intended one: an
audit row dies with its tenant and at no other time.

None of this stops a superuser dropping the triggers, and it is not meant
to. It stops the application, its migrations, and anyone holding the
application's credentials from quietly rewriting history.
"""
from alembic import op

revision = 'auditappendonly1'
down_revision = 'promptversion01'
branch_labels = None
depends_on = None


REFUSE_UPDATE_FN = """
CREATE OR REPLACE FUNCTION audit_log_refuse_update() RETURNS trigger AS $fn$
BEGIN
    -- The only permitted update is the ON DELETE SET NULL anonymisation
    -- from users: identical row, changed_by_user_id dropped to NULL.
    IF NEW.changed_by_user_id IS NULL
       AND (to_jsonb(NEW) - 'changed_by_user_id')
           = (to_jsonb(OLD) - 'changed_by_user_id')
    THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION
        'audit_log is append-only: UPDATE is refused (row %). The only '
        'permitted update is clearing changed_by_user_id when the acting '
        'user is deleted. To correct a record, append a new audit row.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_DELETE_FN = """
CREATE OR REPLACE FUNCTION audit_log_refuse_delete() RETURNS trigger AS $fn$
BEGIN
    -- A cascade from organizations has already removed the parent row, so
    -- its absence is what distinguishes "the tenant is going" from "someone
    -- is deleting evidence of a change".
    IF NOT EXISTS (
        SELECT 1 FROM organizations WHERE id = OLD.organization_id
    ) THEN
        RETURN OLD;
    END IF;

    RAISE EXCEPTION
        'audit_log is append-only: DELETE is refused (row %). Audit rows are '
        'removed only when their organization is deleted.',
        OLD.id;
END;
$fn$ LANGUAGE plpgsql;
"""

REFUSE_TRUNCATE_FN = """
CREATE OR REPLACE FUNCTION audit_log_refuse_truncate() RETURNS trigger AS $fn$
BEGIN
    RAISE EXCEPTION
        'audit_log is append-only: TRUNCATE is refused.';
END;
$fn$ LANGUAGE plpgsql;
"""


def upgrade():
    op.execute(REFUSE_UPDATE_FN)
    op.execute(REFUSE_DELETE_FN)
    op.execute(REFUSE_TRUNCATE_FN)

    op.execute("""
        CREATE TRIGGER audit_log_no_update
        BEFORE UPDATE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_refuse_update();
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_delete
        BEFORE DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_refuse_delete();
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_refuse_truncate();
    """)


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_delete ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_refuse_truncate();")
    op.execute("DROP FUNCTION IF EXISTS audit_log_refuse_delete();")
    op.execute("DROP FUNCTION IF EXISTS audit_log_refuse_update();")
