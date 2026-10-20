"""Add feedback, source review dates, and PDF page provenance.

Revision ID: e4c7a9d51b62
Revises: d9b6e7c32a40
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4c7a9d51b62"
down_revision: Union[str, Sequence[str], None] = "d9b6e7c32a40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Extend support quality and source metadata without deleting records."""
    op.add_column("support_knowledge", sa.Column("review_due_at", sa.DateTime(timezone=True)))
    op.add_column("support_document", sa.Column("review_due_at", sa.DateTime(timezone=True)))
    op.add_column("support_document", sa.Column("pages_json", sa.Text()))
    op.add_column("support_chunk", sa.Column("page_number", sa.Integer()))
    op.create_index("ix_support_knowledge_review_due_at", "support_knowledge", ["review_due_at"])
    op.create_index("ix_support_document_review_due_at", "support_document", ["review_due_at"])
    op.execute("CREATE INDEX ix_support_chunk_textsearch ON support_chunk USING gin (to_tsvector('simple', content))")
    op.execute("UPDATE support_knowledge SET review_due_at = updated_at + interval '90 days' WHERE status = 'published'")
    op.execute("UPDATE support_document SET review_due_at = updated_at + interval '90 days' WHERE status = 'published'")
    op.create_table(
        "support_feedback",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("support_conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(), sa.ForeignKey("support_message.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_feedback_conversation_id", "support_feedback", ["conversation_id"])


def downgrade() -> None:
    """Remove iteration metadata."""
    op.drop_table("support_feedback")
    op.execute("DROP INDEX ix_support_chunk_textsearch")
    op.drop_index("ix_support_document_review_due_at", table_name="support_document")
    op.drop_index("ix_support_knowledge_review_due_at", table_name="support_knowledge")
    op.drop_column("support_chunk", "page_number")
    op.drop_column("support_document", "review_due_at")
    op.drop_column("support_document", "pages_json")
    op.drop_column("support_knowledge", "review_due_at")
