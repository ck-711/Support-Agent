"""Add document provenance and reviewable chunk keywords.

Revision ID: h7c2f4a81d30
Revises: g6b9e3d75f21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h7c2f4a81d30"
down_revision: Union[str, Sequence[str], None] = "g6b9e3d75f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add version, locale, source kind, and keyword metadata."""
    op.add_column(
        "support_document", sa.Column("source_type", sa.String(length=24), nullable=False, server_default="unknown")
    )
    op.add_column("support_document", sa.Column("game_version", sa.String(length=64), nullable=True))
    op.add_column("support_document", sa.Column("language", sa.String(length=8), nullable=False, server_default="en"))
    op.add_column("support_document", sa.Column("keywords_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("support_chunk", sa.Column("keywords_json", sa.Text(), nullable=False, server_default="[]"))


def downgrade() -> None:
    """Remove added document metadata."""
    op.drop_column("support_chunk", "keywords_json")
    op.drop_column("support_document", "keywords_json")
    op.drop_column("support_document", "language")
    op.drop_column("support_document", "game_version")
    op.drop_column("support_document", "source_type")
