"""Track staff document indexing progress.

Revision ID: j9e4f6c03a52
Revises: i8d3e5b92f41
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j9e4f6c03a52"
down_revision: Union[str, Sequence[str], None] = "i8d3e5b92f41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create short-lived upload status records."""
    op.create_table(
        "support_document_upload",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("staff_user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.String(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_support_document_upload_staff_user_id", "support_document_upload", ["staff_user_id"])
    op.create_index("ix_support_document_upload_updated_at", "support_document_upload", ["updated_at"])


def downgrade() -> None:
    """Remove upload status records."""
    op.drop_index("ix_support_document_upload_updated_at", table_name="support_document_upload")
    op.drop_index("ix_support_document_upload_staff_user_id", table_name="support_document_upload")
    op.drop_table("support_document_upload")
