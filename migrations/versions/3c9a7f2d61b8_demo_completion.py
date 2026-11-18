"""notifications, directed feedback and frozen team recommendations

Adds the storage the completed prototype needs: an in-app notification ledger,
feedback that names the person it is about, a confidentiality flag on
evaluations and feedback, and the frozen scoring output behind a suggested team.

Revision ID: 3c9a7f2d61b8
Revises: 2b7c1d9a4e10
Create Date: 2026-10-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "3c9a7f2d61b8"
down_revision: Union[str, None] = "2b7c1d9a4e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("href", sa.String(length=300), nullable=False),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])
    op.create_index(
        "ix_notifications_user_read", "notifications", ["user_id", "read_at"]
    )

    with op.batch_alter_table("feedback") as batch:
        batch.add_column(sa.Column("subject_user_id", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "confidential", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )
        batch.add_column(
            sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_foreign_key(
            "fk_feedback_subject_user",
            "users",
            ["subject_user_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index("ix_feedback_subject_user_id", ["subject_user_id"])

    with op.batch_alter_table("evaluations") as batch:
        batch.add_column(sa.Column("summary", sa.Text(), nullable=False, server_default=""))
        batch.add_column(
            sa.Column(
                "confidential", sa.Boolean(), nullable=False, server_default=sa.true()
            )
        )
        batch.add_column(
            sa.Column("is_synthetic", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("student_profiles") as batch:
        batch.add_column(
            sa.Column(
                "availability_timezone",
                sa.String(length=64),
                nullable=False,
                server_default="Asia/Kolkata",
            )
        )

    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column(
                "timezone",
                sa.String(length=64),
                nullable=False,
                server_default="Asia/Kolkata",
            )
        )

    with op.batch_alter_table("teams") as batch:
        batch.add_column(sa.Column("rationale", sa.Text(), nullable=False, server_default=""))
        batch.add_column(
            sa.Column("score_snapshot", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("teams") as batch:
        batch.drop_column("score_snapshot")
        batch.drop_column("rationale")

    with op.batch_alter_table("projects") as batch:
        batch.drop_column("timezone")

    with op.batch_alter_table("student_profiles") as batch:
        batch.drop_column("availability_timezone")

    with op.batch_alter_table("evaluations") as batch:
        batch.drop_column("is_synthetic")
        batch.drop_column("confidential")
        batch.drop_column("summary")

    with op.batch_alter_table("feedback") as batch:
        batch.drop_index("ix_feedback_subject_user_id")
        batch.drop_constraint("fk_feedback_subject_user", type_="foreignkey")
        batch.drop_column("is_synthetic")
        batch.drop_column("confidential")
        batch.drop_column("subject_user_id")

    op.drop_index("ix_notifications_user_read", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")
    op.drop_table("notifications")
