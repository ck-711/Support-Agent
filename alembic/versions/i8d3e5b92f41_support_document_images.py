"""Add staff-reviewed knowledge images stored in object storage.

Revision ID: i8d3e5b92f41
Revises: h7c2f4a81d30
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision: str = "i8d3e5b92f41"
down_revision: Union[str, Sequence[str], None] = "h7c2f4a81d30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create image metadata and caption vector storage."""
    op.create_table(
        "support_document_image",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("support_document.id"), nullable=False),
        sa.Column("object_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("embedding", VECTOR(1024), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_support_document_image_document_id", "support_document_image", ["document_id"])


def downgrade() -> None:
    """Drop image metadata."""
    op.drop_index("ix_support_document_image_document_id", table_name="support_document_image")
    op.drop_table("support_document_image")
