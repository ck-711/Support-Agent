"""Player support API contracts."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl


class ConversationStart(BaseModel):
    """Preferred language for a new conversation."""

    language: Literal["en", "es", "zh"] = "en"


class ConversationClaim(BaseModel):
    """One-time legacy anonymous conversation token."""

    token: str = Field(min_length=40, max_length=256)


class ConversationCreated(BaseModel):
    """The authenticated player's current support conversation."""

    conversation_id: str
    expires_at: datetime


class MessageCreate(BaseModel):
    """A player message."""

    content: str = Field(min_length=1, max_length=4000)
    language: Literal["en", "es", "zh"] | None = None


class SourceReference(BaseModel):
    """Provenance for an assistant answer."""

    title: str
    url: str
    image_url: str | None = None
    image_alt: str | None = None


class MessageRead(BaseModel):
    """One stored message."""

    id: str
    role: str
    content: str
    sources: list[SourceReference] = Field(default_factory=list)
    created_at: datetime


class ChatResult(BaseModel):
    """Answer and optional issue created from a bug report."""

    message: MessageRead
    ticket_id: str | None = None
    handoff_reason: str | None = None
    handoff_active: bool = False


class TicketCreate(BaseModel):
    """Explicit player issue submission."""

    category: Literal["bug", "achievement", "save", "performance", "other"] = "bug"
    summary: str = Field(min_length=10, max_length=4000)
    game_version: str | None = Field(default=None, max_length=64)
    device_info: str | None = Field(default=None, max_length=500)


class TicketRead(BaseModel):
    """Ticket visible to player or staff."""

    id: str
    conversation_id: str
    category: str
    status: str
    summary: str
    game_version: str | None
    device_info: str | None
    handoff_reason: str | None = None
    assigned_to: int | None = None
    created_at: datetime
    updated_at: datetime


class TicketUpdate(BaseModel):
    """Staff resolution and optional reply."""

    status: Literal["open", "investigating", "resolved", "closed"]
    reply: str | None = Field(default=None, min_length=1, max_length=4000)


class KnowledgeWrite(BaseModel):
    """Staff-authored knowledge entry."""

    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=10, max_length=8000)
    source_url: HttpUrl


class KnowledgeRead(BaseModel):
    """Knowledge entry and publication state."""

    id: str
    title: str
    content: str
    source_url: str
    status: str
    version: int
    updated_at: datetime
    review_due_at: datetime | None = None


class SupportOverview(BaseModel):
    """Small operational dashboard for staff."""

    open_tickets: int
    total_tickets: int
    published_documents: int
    conversations_30d: int


class DocumentRead(BaseModel):
    """Uploaded document status and index information."""

    id: str
    title: str
    filename: str
    mime_type: str
    source_url: str | None
    source_type: str = "unknown"
    game_version: str | None = None
    language: str = "en"
    keywords: list[str] = Field(default_factory=list)
    status: str
    chunk_count: int
    embedding_model: str
    created_at: datetime
    updated_at: datetime
    review_due_at: datetime | None = None


class DocumentUploadProgress(BaseModel):
    """Staff-visible progress for one upload and indexing request."""

    id: str
    status: str
    phase: str
    percent: int
    document_id: str | None = None


class TextDocumentWrite(BaseModel):
    """Manually authored knowledge entering the same document pipeline."""

    title: str = Field(min_length=3, max_length=200)
    content: str = Field(min_length=10, max_length=100000)
    source_url: HttpUrl | None = None
    source_type: Literal["unknown", "official", "community", "internal"] = "unknown"
    game_version: str | None = Field(default=None, max_length=64)
    language: Literal["en", "es", "zh"] = "en"
    keywords: list[str] = Field(default_factory=list, max_length=50)


class DocumentSource(BaseModel):
    """Public source text for a published document."""

    title: str
    content: str
    source_url: str | None
    page_number: int | None = None


class DocumentChunkRead(BaseModel):
    """One passage and its reviewable search keywords."""

    id: str
    position: int
    page_number: int | None
    content: str
    keywords: list[str] = Field(default_factory=list)


class DocumentReview(BaseModel):
    """Source metadata and extracted passages for staff review."""

    id: str
    title: str
    filename: str
    source_url: str | None
    source_type: str
    game_version: str | None
    language: str
    keywords: list[str] = Field(default_factory=list)
    content: str
    chunks: list[DocumentChunkRead] = Field(default_factory=list)
    images: list["DocumentImageRead"] = Field(default_factory=list)


class DocumentImageRead(BaseModel):
    """Reviewable image metadata; binary content stays in private object storage."""

    id: str
    document_id: str
    filename: str
    description: str
    page_number: int | None
    created_at: datetime
    url: str


class DocumentImageWrite(BaseModel):
    """Staff-approved description and optional page association."""

    description: str = Field(min_length=10, max_length=2000)
    page_number: int | None = Field(default=None, ge=1, le=200)


class VisionDescription(BaseModel):
    """Unapproved caption suggestion returned to a staff editor."""

    description: str


class ChunkKeywordsWrite(BaseModel):
    """Staff-curated search aliases for a passage."""

    keywords: list[str] = Field(max_length=30)


class TicketContext(BaseModel):
    """Complete handoff transcript for staff."""

    ticket: TicketRead
    messages: list[MessageRead]


class ToolCallRequest(BaseModel):
    """Allowlisted player business operation."""

    arguments: dict[str, str] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """Result of a scoped support operation."""

    name: str
    result: dict[str, Any]


class FeedbackWrite(BaseModel):
    """One player evaluation of an AI answer."""

    rating: Literal["positive", "negative"]
    reason: str | None = Field(default=None, max_length=500)


class FeedbackRead(BaseModel):
    """Persisted rating visible to the same player."""

    message_id: str
    rating: str
    reason: str | None
    created_at: datetime


class SupportQualityOverview(BaseModel):
    """Operational quality indicators for staff."""

    positive_feedback: int
    negative_feedback: int
    stale_documents: int
    unassigned_tickets: int
