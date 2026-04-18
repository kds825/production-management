"""wip_upload_log table

Revision ID: cffe753b1054
Revises: 708591555e9b
Create Date: 2026-04-18 11:32:47.640442

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "cffe753b1054"
down_revision: Union[str, Sequence[str], None] = "708591555e9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # wip_upload_log: Excel 재업로드 멱등성 G4 기반 테이블
    op.create_table(
        "wip_upload_log",
        sa.Column("upload_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("canonical_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_file_hash", sa.String(length=64), nullable=True),
        sa.Column("run_label", sa.String(length=50), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=False),
        sa.Column("rows_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.PrimaryKeyConstraint("upload_id"),
        sa.UniqueConstraint("canonical_hash"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("wip_upload_log")
