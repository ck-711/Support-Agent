"""Index chronological message pagination.

Revision ID: f5a8d2c64e10
Revises: e4c7a9d51b62
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f5a8d2c64e10"
down_revision: Union[str, Sequence[str], None] = "e4c7a9d51b62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make per-conversation keyset pages efficient."""
    op.create_index(
        "ix_support_message_conversation_time_id",
        "support_message",
        ["conversation_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove the paging index."""
    op.drop_index("ix_support_message_conversation_time_id", table_name="support_message")
