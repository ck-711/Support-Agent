"""Bind new support conversations to registered player accounts.

Revision ID: g6b9e3d75f21
Revises: f5a8d2c64e10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g6b9e3d75f21"
down_revision: Union[str, Sequence[str], None] = "f5a8d2c64e10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add nullable ownership so legacy anonymous records survive retention."""
    op.add_column("support_conversation", sa.Column("user_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_support_conversation_user_id", "support_conversation", "user", ["user_id"], ["id"]
    )
    op.create_index("ix_support_conversation_user_id", "support_conversation", ["user_id"])


def downgrade() -> None:
    """Remove account ownership."""
    op.drop_index("ix_support_conversation_user_id", table_name="support_conversation")
    op.drop_constraint("fk_support_conversation_user_id", "support_conversation", type_="foreignkey")
    op.drop_column("support_conversation", "user_id")
