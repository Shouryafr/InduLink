"""Enforce append-only history and refuse hard deletes, in the database itself.

The application also refuses these operations at the ORM layer, but a trigger
means the rule holds for raw SQL, a migration script or a future service that
forgets. Brief section 5: never hard-delete users, evidence, assignments or
versions; audit events are append-only.

Revision ID: 2b7c1d9a4e10
Revises: 1e554f5e84df
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision: str = "2b7c1d9a4e10"
down_revision: str | None = "1e554f5e84df"
branch_labels = None
depends_on = None

#: Tables that must never lose a row, and the message the trigger raises.
PROTECTED_TABLES = (
    ("audit_events", "Audit events are append-only: history cannot be rewritten."),
    ("users", "Accounts are never hard-deleted; suspend or anonymise instead."),
    ("evidence", "Evidence is never hard-deleted; it is rejected or superseded."),
    ("project_versions", "Requirement versions are immutable once written."),
    ("mentor_assignments", "Mentor assignments are cancelled, never deleted."),
    ("recommendation_snapshots", "Recommendation history is immutable."),
)


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        for table, message in PROTECTED_TABLES:
            op.execute(
                f"""
                CREATE TRIGGER IF NOT EXISTS trg_{table}_no_delete
                BEFORE DELETE ON {table}
                BEGIN
                    SELECT RAISE(ABORT, '{message}');
                END
                """
            )
        op.execute(
            """
            CREATE TRIGGER IF NOT EXISTS trg_audit_events_no_update
            BEFORE UPDATE ON audit_events
            BEGIN
                SELECT RAISE(ABORT, 'Audit events are append-only: history cannot be rewritten.');
            END
            """
        )
    elif dialect == "postgresql":
        op.execute(
            """
            CREATE OR REPLACE FUNCTION refuse_row_change() RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '%', TG_ARGV[0];
            END;
            $$ LANGUAGE plpgsql;
            """
        )
        for table, message in PROTECTED_TABLES:
            escaped = message.replace("'", "''")
            op.execute(
                f"CREATE TRIGGER trg_{table}_no_delete BEFORE DELETE ON {table} "
                f"FOR EACH ROW EXECUTE FUNCTION refuse_row_change('{escaped}');"
            )
        op.execute(
            "CREATE TRIGGER trg_audit_events_no_update BEFORE UPDATE ON audit_events "
            "FOR EACH ROW EXECUTE FUNCTION refuse_row_change("
            "'Audit events are append-only: history cannot be rewritten.');"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "sqlite":
        for table, _message in PROTECTED_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete")
        op.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_update")
    elif dialect == "postgresql":
        for table, _message in PROTECTED_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_delete ON {table}")
        op.execute("DROP TRIGGER IF EXISTS trg_audit_events_no_update ON audit_events")
        op.execute("DROP FUNCTION IF EXISTS refuse_row_change()")
