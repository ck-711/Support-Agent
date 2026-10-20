"""Persistent player support records."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


def now_utc() -> datetime:
    """Return a UTC timestamp for support records."""
    return datetime.now(UTC)


class SupportConversation(SQLModel, table=True):
    """Anonymous player conversation; access is controlled by a hashed token."""

    __tablename__ = "support_conversation"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    token_hash: str = Field(index=True, unique=True)
    user_id: int | None = Field(default=None, foreign_key="user.id", index=True)
    language: str = Field(default="en", max_length=8)
    created_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime = Field(default_factory=lambda: now_utc() + timedelta(days=30), index=True)


class SupportMessage(SQLModel, table=True):
    """A player, assistant, or staff message."""

    __tablename__ = "support_message"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str = Field(foreign_key="support_conversation.id", index=True)
    role: str = Field(max_length=16)
    content: str = Field(sa_type=Text)
    sources_json: str = Field(default="[]", sa_type=Text)
    created_at: datetime = Field(default_factory=now_utc)


class SupportTicket(SQLModel, table=True):
    """Issue submitted by a player for staff review."""

    __tablename__ = "support_ticket"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str = Field(foreign_key="support_conversation.id", index=True)
    category: str = Field(default="bug", max_length=32)
    status: str = Field(default="open", max_length=24, index=True)
    summary: str = Field(sa_type=Text)
    game_version: str | None = Field(default=None, max_length=64)
    device_info: str | None = Field(default=None, sa_type=Text)
    handoff_reason: str | None = Field(default=None, max_length=64)
    context_json: str = Field(default="[]", sa_type=Text)
    assigned_to: int | None = Field(default=None, foreign_key="user.id")
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class SupportKnowledge(SQLModel, table=True):
    """An approved, versioned knowledge entry with provenance."""

    __tablename__ = "support_knowledge"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str = Field(max_length=200)
    content: str = Field(sa_type=Text)
    source_url: str = Field(max_length=1000)
    status: str = Field(default="draft", max_length=16, index=True)
    version: int = Field(default=1)
    updated_at: datetime = Field(default_factory=now_utc)
    reviewed_by: int | None = Field(default=None, foreign_key="user.id")
    review_due_at: datetime | None = Field(default=None, index=True)


class SupportDocument(SQLModel, table=True):
    """Uploaded source with a reviewable publication lifecycle."""

    __tablename__ = "support_document"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    title: str = Field(max_length=200)
    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=100)
    source_url: str | None = Field(default=None, max_length=1000)
    source_type: str = Field(default="unknown", max_length=24)
    game_version: str | None = Field(default=None, max_length=64)
    language: str = Field(default="en", max_length=8)
    keywords_json: str = Field(default="[]", sa_type=Text)
    content: str = Field(sa_type=Text)
    pages_json: str | None = Field(default=None, sa_type=Text)
    status: str = Field(default="draft", max_length=16, index=True)
    chunk_count: int = Field(default=0)
    embedding_model: str = Field(max_length=120)
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    reviewed_by: int | None = Field(default=None, foreign_key="user.id")
    review_due_at: datetime | None = Field(default=None, index=True)


class SupportDocumentUpload(SQLModel, table=True):
    """Short-lived, staff-scoped status of a document indexing request."""

    __tablename__ = "support_document_upload"  # pyright: ignore[reportAssignmentType]

    id: str = Field(primary_key=True)
    staff_user_id: int = Field(foreign_key="user.id", index=True)
    status: str = Field(default="processing", max_length=16)
    phase: str = Field(default="parsing", max_length=16)
    percent: int = Field(default=30)
    document_id: str | None = Field(default=None)
    updated_at: datetime = Field(default_factory=now_utc, index=True)


class SupportChunk(SQLModel, table=True):
    """One embedded passage; vector storage is indexed by pgvector."""

    __tablename__ = "support_chunk"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="support_document.id", index=True)
    position: int
    page_number: int | None = Field(default=None)
    content: str = Field(sa_type=Text)
    keywords_json: str = Field(default="[]", sa_type=Text)
    embedding: list[float] = Field(sa_column=Column(VECTOR(1024), nullable=False))


class SupportDocumentImage(SQLModel, table=True):
    """Staff-reviewed image attached to a knowledge document."""

    __tablename__ = "support_document_image"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    document_id: str = Field(foreign_key="support_document.id", index=True)
    object_key: str = Field(max_length=255, unique=True)
    filename: str = Field(max_length=255)
    mime_type: str = Field(max_length=100)
    description: str = Field(default="", sa_type=Text)
    page_number: int | None = Field(default=None)
    embedding: list[float] | None = Field(default=None, sa_column=Column(VECTOR(1024), nullable=True))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class SupportFeedback(SQLModel, table=True):
    """Player rating attached to one assistant answer."""

    __tablename__ = "support_feedback"  # pyright: ignore[reportAssignmentType]

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    conversation_id: str = Field(foreign_key="support_conversation.id", index=True)
    message_id: str = Field(foreign_key="support_message.id", unique=True)
    rating: str = Field(max_length=16)
    reason: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=now_utc)
