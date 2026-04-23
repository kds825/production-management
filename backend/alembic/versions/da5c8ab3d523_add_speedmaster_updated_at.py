"""add speedmaster updated_at

Revision ID: da5c8ab3d523
Revises: cffe753b1054
Create Date: 2026-04-18 12:05:47.440673

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "da5c8ab3d523"
down_revision: Union[str, Sequence[str], None] = "cffe753b1054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "speed_master",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("speed_master", "updated_at")
