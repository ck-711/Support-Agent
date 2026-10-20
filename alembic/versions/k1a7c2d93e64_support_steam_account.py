"""Store Steam identities verified with app-specific auth tickets.

Revision ID: k1a7c2d93e64
Revises: j9e4f6c03a52
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k1a7c2d93e64"
down_revision: Union[str, Sequence[str], None] = "j9e4f6c03a52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Keep a verified Steam account separate from support conversations."""
    op.create_table(
        "support_steam_account",
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("steam_id", sa.String(length=20), nullable=False),
        sa.Column("verified_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_support_steam_account_steam_id", "support_steam_account", ["steam_id"], unique=True)


def downgrade() -> None:
    """Remove linked Steam identities."""
    op.drop_index("ix_support_steam_account_steam_id", table_name="support_steam_account")
    op.drop_table("support_steam_account")
