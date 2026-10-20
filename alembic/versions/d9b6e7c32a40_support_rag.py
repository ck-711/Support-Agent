"""Add document vectors and human handoff context.

Revision ID: d9b6e7c32a40
Revises: c8a4f5a21d97
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR

revision: str = "d9b6e7c32a40"
down_revision: Union[str, Sequence[str], None] = "c8a4f5a21d97"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add reviewed document sources and ticket transfer metadata."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column("support_ticket", sa.Column("handoff_reason", sa.String(length=64)))
    op.add_column("support_ticket", sa.Column("context_json", sa.Text(), nullable=False, server_default="[]"))
    op.add_column("support_ticket", sa.Column("assigned_to", sa.Integer(), sa.ForeignKey("user.id")))
    op.create_table(
        "support_document",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.String(length=1000)),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id")),
    )
    op.create_index("ix_support_document_status", "support_document", ["status"])
    op.create_table(
        "support_chunk",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("document_id", sa.String(), sa.ForeignKey("support_document.id", ondelete="CASCADE"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", VECTOR(1024), nullable=False),
    )
    op.create_index("ix_support_chunk_document_id", "support_chunk", ["document_id"])
    op.execute("CREATE INDEX ix_support_chunk_embedding_hnsw ON support_chunk USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    """Remove document vectors and transfer metadata."""
    op.drop_table("support_chunk")
    op.drop_table("support_document")
    op.drop_column("support_ticket", "assigned_to")
    op.drop_column("support_ticket", "context_json")
    op.drop_column("support_ticket", "handoff_reason")
