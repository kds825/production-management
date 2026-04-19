"""add constraint_config_history and timestamps

Revision ID: dc5d1c469f8e
Revises: d1e2f3a4b5c6
Create Date: 2026-04-18 11:27:12.044206

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "dc5d1c469f8e"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "constraint_config",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "constraint_config_history",
        sa.Column("history_id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("constraint_id", sa.String(length=10), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("changed_by", sa.String(length=100), nullable=True),
        sa.Column("old_params_json", postgresql.JSONB(), nullable=True),
        sa.Column("new_params_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["constraint_id"], ["constraint_config.constraint_id"]),
    )
    op.create_index(
        "ix_constraint_config_history_constraint_id",
        "constraint_config_history",
        ["constraint_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_constraint_config_history_constraint_id", "constraint_config_history"
    )
    op.drop_table("constraint_config_history")
    op.drop_column("constraint_config", "updated_at")
