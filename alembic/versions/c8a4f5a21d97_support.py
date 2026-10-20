"""Add player support tables.

Revision ID: c8a4f5a21d97
Revises: b25d38b0cd7c
"""

from typing import Sequence, Union
from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision: str = "c8a4f5a21d97"
down_revision: Union[str, Sequence[str], None] = "b25d38b0cd7c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create support storage."""
    op.create_table(
        "support_conversation",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_conversation_expires_at", "support_conversation", ["expires_at"])
    op.create_table(
        "support_message",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("support_conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_message_conversation_id", "support_message", ["conversation_id"])
    op.create_table(
        "support_ticket",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("conversation_id", sa.String(), sa.ForeignKey("support_conversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("game_version", sa.String(length=64)),
        sa.Column("device_info", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_support_ticket_conversation_id", "support_ticket", ["conversation_id"])
    op.create_index("ix_support_ticket_status", "support_ticket", ["status"])
    op.create_table(
        "support_knowledge",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("user.id")),
    )
    op.create_index("ix_support_knowledge_status", "support_knowledge", ["status"])

    source = "https://store.steampowered.com/app/3326810/DOTS_Survivors/"
    updated = datetime.now(UTC)
    articles = [
        (
            "00000000-0000-4000-8000-000000332681",
            "About DOTS Survivors",
            "DOTS Survivors is a free-to-play single-player casual action game. Players control an astronaut, explore the cosmos, fight hostile aliens, and select and level up futuristic weapons during each run. The Steam store describes it as an example of Unity's Data-Oriented Technology Stack (DOTS).",
        ),
        (
            "00000000-0000-4000-8000-000000332682",
            "Weapons and powerups",
            "The Steam store says players can make builds from two dozen weapons and powerups. Listed weapons include Oxygen Hose Whip, Rogue Jetpack, Radioactive Waste Drops, Drone Swarm, Mega Laser Strike, Screwdriver Shot, Sawblade Scatter, Satellite Dish Toss, CO2 Vent, Plasma Blaster, Black Hole, and O.M.G. Wrench. The page does not document unlock rules, upgrade values, or weapon evolutions.",
        ),
        (
            "00000000-0000-4000-8000-000000332683",
            "Steam features",
            "The Steam store lists single-player, Steam Achievements, Steam Cloud, Stats, and Family Sharing. The page does not specify achievement unlock conditions, save restoration procedures, or cloud sync troubleshooting for this game.",
        ),
        (
            "00000000-0000-4000-8000-000000332684",
            "Minimum system requirements",
            "For Windows, the Steam store lists Windows 10, a 64-bit processor and operating system, 2.5 GHz 4-core CPU, 8 GB RAM, GTX 1070 graphics, DirectX 11, and 1 GB of available storage. The store also lists SteamOS + Linux with 2.5 GHz 4-core CPU, 8 GB RAM, GTX 1070 graphics, and 1 GB storage. These are minimum requirements, not a performance guarantee.",
        ),
        (
            "00000000-0000-4000-8000-000000332685",
            "Languages and release",
            "The Steam store lists interface support for English, Spanish (Spain), and Spanish (Latin America). It lists the release date as March 12, 2025 and names Turbo Makes Games as developer and publisher. The page does not list full audio or subtitles for those languages.",
        ),
    ]
    table = sa.table(
        "support_knowledge",
        sa.column("id", sa.String()),
        sa.column("title", sa.String()),
        sa.column("content", sa.Text()),
        sa.column("source_url", sa.String()),
        sa.column("status", sa.String()),
        sa.column("version", sa.Integer()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
        sa.column("reviewed_by", sa.Integer()),
    )
    op.bulk_insert(
        table,
        [
            {
                "id": article_id,
                "title": title,
                "content": content,
                "source_url": source,
                "status": "published",
                "version": 1,
                "updated_at": updated,
                "reviewed_by": None,
            }
            for article_id, title, content in articles
        ],
    )


def downgrade() -> None:
    """Remove support storage."""
    op.drop_table("support_knowledge")
    op.drop_table("support_ticket")
    op.drop_table("support_message")
    op.drop_table("support_conversation")
