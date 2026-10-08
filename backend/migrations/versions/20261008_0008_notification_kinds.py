"""notification kinds, receipt link, and what each user wants by email

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-08 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.add_column(
            sa.Column("kind", sa.String(30), nullable=False, server_default="other")
        )
        batch_op.add_column(sa.Column("receipt_id", sa.Integer(), nullable=True))
    # Everything is wanted until a user switches a kind off
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("email_disabled_kinds", sa.JSON(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("email_disabled_kinds")
    with op.batch_alter_table("notifications") as batch_op:
        batch_op.drop_column("receipt_id")
        batch_op.drop_column("kind")
