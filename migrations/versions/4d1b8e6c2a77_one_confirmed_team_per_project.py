"""one confirmed team per project, enforced by the database

A team confirmation is a check-then-write: two clicks at the same moment can both
read "no confirmed team" before either commits. This partial unique index makes
the database refuse the loser, matching the rule already used for mentors.

Revision ID: 4d1b8e6c2a77
Revises: 3c9a7f2d61b8
Create Date: 2026-10-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4d1b8e6c2a77"
down_revision: Union[str, None] = "3c9a7f2d61b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_teams_confirmed_project",
        "teams",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("state = 'confirmed'"),
        postgresql_where=sa.text("state = 'confirmed'"),
    )


def downgrade() -> None:
    op.drop_index("uq_teams_confirmed_project", table_name="teams")
