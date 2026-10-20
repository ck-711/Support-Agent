"""Async persistence for player support."""

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

from sqlalchemy import delete, func, tuple_
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.support import (
    SupportConversation, SupportFeedback, SupportKnowledge, SupportMessage, SupportTicket, now_utc,
)
from app.models.user import User
from app.schemas.support import FeedbackWrite, KnowledgeWrite, SourceReference, TicketCreate, TicketUpdate


def _database_url() -> str:
    """Build the async psycopg connection URL."""
    return (
        "postgresql+psycopg://"
        f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
    )


engine = create_async_engine(_database_url(), pool_pre_ping=True, pool_size=settings.POSTGRES_POOL_SIZE)
session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def hash_token(token: str) -> str:
    """Hash a bearer token before storage or lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def create_conversation(language: str, user_id: int) -> SupportConversation:
    """Return this user's active conversation, creating one under a user row lock."""
    async with session_factory() as db:
        user = await db.exec(select(User).where(User.id == user_id).with_for_update())
        if user.first() is None:
            raise ValueError("User not found")
        existing = await db.exec(
            select(SupportConversation).where(
                SupportConversation.user_id == user_id,
                col(SupportConversation.expires_at) > now_utc(),
            ).order_by(col(SupportConversation.created_at).desc()).limit(1)
        )
        conversation = existing.first()
        if conversation is not None:
            return conversation
        conversation = SupportConversation(
            token_hash=hash_token(secrets.token_urlsafe(48)), user_id=user_id, language=language
        )
        db.add(conversation)
        await db.commit()
    return conversation


async def find_conversation(user_id: int) -> SupportConversation | None:
    """Resolve only the authenticated user's live conversation."""
    async with session_factory() as db:
        result = await db.exec(
            select(SupportConversation).where(
                SupportConversation.user_id == user_id,
                col(SupportConversation.expires_at) > now_utc(),
            ).order_by(col(SupportConversation.created_at).desc()).limit(1)
        )
        return result.first()


async def claim_legacy_conversation(user_id: int, token: str) -> SupportConversation | None:
    """Move a bearer-only conversation to an account and invalidate its old secret."""
    async with session_factory() as db:
        user = await db.exec(select(User).where(User.id == user_id).with_for_update())
        if user.first() is None:
            return None
        current = await db.exec(
            select(SupportConversation.id).where(
                SupportConversation.user_id == user_id,
                col(SupportConversation.expires_at) > now_utc(),
            ).limit(1)
        )
        if current.first() is not None:
            raise ValueError("Account already has an active support conversation")
        result = await db.exec(
            select(SupportConversation).where(
                SupportConversation.token_hash == hash_token(token),
                col(SupportConversation.user_id).is_(None),
                col(SupportConversation.expires_at) > now_utc(),
            ).with_for_update()
        )
        conversation = result.first()
        if conversation is None:
            return None
        conversation.user_id = user_id
        conversation.token_hash = hash_token(secrets.token_urlsafe(48))
        db.add(conversation)
        await db.commit()
        return conversation


async def add_message(
    conversation_id: str, role: str, content: str, sources: list[SourceReference] | None = None
) -> SupportMessage:
    """Persist one message."""
    record = SupportMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources_json=json.dumps([source.model_dump() for source in sources or []]),
    )
    async with session_factory() as db:
        db.add(record)
        await db.commit()
    return record


async def list_messages(
    conversation_id: str, limit: int | None = 100, before: str | None = None, after: str | None = None
) -> list[SupportMessage]:
    """Load a stable page of messages in chronological order."""
    if before is not None and after is not None:
        raise ValueError("Choose either before or after")
    async with session_factory() as db:
        statement = select(SupportMessage).where(SupportMessage.conversation_id == conversation_id)
        cursor = before or after
        if cursor is not None:
            anchor = await db.get(SupportMessage, cursor)
            if anchor is None or anchor.conversation_id != conversation_id:
                raise ValueError("Message cursor not found in this conversation")
            ordering = tuple_(col(SupportMessage.created_at), col(SupportMessage.id))
            statement = statement.where(
                ordering < (anchor.created_at, anchor.id)
                if before is not None else ordering > (anchor.created_at, anchor.id)
            )
        if limit is None:
            result = await db.exec(statement.order_by(col(SupportMessage.created_at), col(SupportMessage.id)))
            return list(result.all())
        if after is not None:
            result = await db.exec(
                statement.order_by(col(SupportMessage.created_at), col(SupportMessage.id)).limit(limit)
            )
            return list(result.all())
        result = await db.exec(
            statement.order_by(col(SupportMessage.created_at).desc(), col(SupportMessage.id).desc()).limit(limit)
        )
        return list(reversed(result.all()))


async def create_ticket(conversation_id: str, issue: TicketCreate) -> SupportTicket:
    """Create an actionable support ticket."""
    messages = await list_messages(conversation_id, limit=None)
    context = json.dumps(
        [{"id": item.id, "role": item.role, "content": item.content, "sources_json": item.sources_json,
          "created_at": item.created_at.isoformat()} for item in messages], ensure_ascii=False
    )
    ticket = SupportTicket(
        conversation_id=conversation_id, context_json=context, handoff_reason="player_request", **issue.model_dump()
    )
    async with session_factory() as db:
        db.add(ticket)
        await db.commit()
    return ticket


async def create_handoff_ticket(conversation_id: str, summary: str, reason: str) -> SupportTicket:
    """Create one open transfer per conversation, preserving its full transcript."""
    async with session_factory() as db:
        lock = await db.exec(
            select(SupportConversation).where(SupportConversation.id == conversation_id).with_for_update()
        )
        if lock.first() is None:
            raise ValueError("Conversation not found")
        result = await db.exec(
            select(SupportTicket).where(
                SupportTicket.conversation_id == conversation_id,
                col(SupportTicket.status).in_(["open", "investigating"]),
            ).order_by(col(SupportTicket.created_at).desc()).limit(1)
        )
        existing = result.first()
        if existing is not None:
            return existing
        messages = await db.exec(
            select(SupportMessage).where(SupportMessage.conversation_id == conversation_id)
            .order_by(col(SupportMessage.created_at))
        )
        context = json.dumps(
            [{"id": item.id, "role": item.role, "content": item.content,
              "sources_json": item.sources_json, "created_at": item.created_at.isoformat()}
             for item in messages.all()], ensure_ascii=False
        )
        ticket = SupportTicket(
            conversation_id=conversation_id, category="other", summary=summary[:4000],
            handoff_reason=reason, context_json=context,
        )
        db.add(ticket)
        await db.commit()
        return ticket


async def get_ticket(ticket_id: str) -> SupportTicket | None:
    """Find one staff ticket."""
    async with session_factory() as db:
        return await db.get(SupportTicket, ticket_id)


async def get_active_handoff(conversation_id: str) -> SupportTicket | None:
    """Return an unresolved ticket that currently owns the conversation."""
    async with session_factory() as db:
        result = await db.exec(
            select(SupportTicket).where(
                SupportTicket.conversation_id == conversation_id,
                col(SupportTicket.status).in_(["open", "investigating"]),
            ).order_by(col(SupportTicket.created_at).desc()).limit(1)
        )
        return result.first()


async def claim_ticket(ticket_id: str, staff_id: int) -> SupportTicket | None:
    """Atomically assign an active ticket to one staff member."""
    async with session_factory() as db:
        result = await db.exec(select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update())
        ticket = result.first()
        if ticket is None:
            return None
        if ticket.status not in {"open", "investigating"}:
            raise ValueError("Only active tickets can be claimed")
        if ticket.assigned_to is not None and ticket.assigned_to != staff_id:
            raise ValueError("Ticket is assigned to another staff member")
        ticket.assigned_to = staff_id
        ticket.status = "investigating"
        ticket.updated_at = now_utc()
        db.add(ticket)
        await db.commit()
        return ticket


async def release_ticket(ticket_id: str, staff_id: int) -> SupportTicket | None:
    """Put the current staff member's ticket back in the queue."""
    async with session_factory() as db:
        result = await db.exec(select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update())
        ticket = result.first()
        if ticket is None:
            return None
        if ticket.assigned_to != staff_id or ticket.status not in {"open", "investigating"}:
            raise ValueError("Only the assigned staff member can release this ticket")
        ticket.assigned_to = None
        ticket.status = "open"
        ticket.updated_at = now_utc()
        db.add(ticket)
        await db.commit()
        return ticket


async def list_tickets(conversation_id: str | None = None) -> list[SupportTicket]:
    """Load tickets for one player or all staff."""
    statement = select(SupportTicket).order_by(col(SupportTicket.created_at).desc()).limit(200)
    if conversation_id is not None:
        statement = statement.where(SupportTicket.conversation_id == conversation_id)
    async with session_factory() as db:
        result = await db.exec(statement)
        return list(result.all())


async def update_ticket(ticket_id: str, change: TicketUpdate, staff_id: int | None = None) -> SupportTicket | None:
    """Update status and append a staff reply in one transaction."""
    async with session_factory() as db:
        result = await db.exec(select(SupportTicket).where(SupportTicket.id == ticket_id).with_for_update())
        ticket = result.first()
        if ticket is None:
            return None
        if staff_id is not None and ticket.assigned_to != staff_id:
            raise ValueError("Claim this ticket before updating it")
        ticket.status = change.status
        if staff_id is not None:
            ticket.assigned_to = staff_id
        ticket.updated_at = now_utc()
        if change.reply:
            db.add(SupportMessage(conversation_id=ticket.conversation_id, role="staff", content=change.reply))
        db.add(ticket)
        await db.commit()
        return ticket


async def list_knowledge(published_only: bool = False) -> list[SupportKnowledge]:
    """Load approved public entries or the full staff catalog."""
    statement = select(SupportKnowledge).order_by(col(SupportKnowledge.updated_at).desc()).limit(200)
    if published_only:
        statement = statement.where(SupportKnowledge.status == "published")
    async with session_factory() as db:
        result = await db.exec(statement)
        return list(result.all())


async def get_knowledge(entry_id: str) -> SupportKnowledge | None:
    """Read one staff article before indexing or publication."""
    async with session_factory() as db:
        return await db.get(SupportKnowledge, entry_id)


async def save_knowledge(
    entry: KnowledgeWrite, entry_id: str | None = None
) -> SupportKnowledge | None:
    """Create a draft or revise an entry, returning it to review."""
    async with session_factory() as db:
        record = (
            await db.get(SupportKnowledge, entry_id)
            if entry_id
            else SupportKnowledge(title="", content="", source_url="")
        )
        if record is None:
            return None
        record.title = entry.title
        record.content = entry.content
        record.source_url = str(entry.source_url)
        record.status = "draft"
        record.version = record.version + 1 if entry_id else 1
        record.reviewed_by = None
        record.review_due_at = None
        record.updated_at = now_utc()
        db.add(record)
        await db.commit()
        return record


async def publish_knowledge(entry_id: str, user_id: int) -> SupportKnowledge | None:
    """Publish a reviewed knowledge entry."""
    async with session_factory() as db:
        record = await db.get(SupportKnowledge, entry_id)
        if record is None:
            return None
        record.status = "published"
        record.reviewed_by = user_id
        record.updated_at = now_utc()
        record.review_due_at = record.updated_at + timedelta(days=90)
        db.add(record)
        await db.commit()
        return record


async def delete_conversation(conversation_id: str) -> None:
    """Erase a player's messages and tickets immediately."""
    async with session_factory() as db:
        await db.exec(delete(SupportFeedback).where(col(SupportFeedback.conversation_id) == conversation_id))
        await db.exec(delete(SupportTicket).where(col(SupportTicket.conversation_id) == conversation_id))
        await db.exec(delete(SupportMessage).where(col(SupportMessage.conversation_id) == conversation_id))
        await db.exec(delete(SupportConversation).where(col(SupportConversation.id) == conversation_id))
        await db.commit()


async def purge_expired() -> int:
    """Remove records past the 30-day retention period."""
    async with session_factory() as db:
        result = await db.exec(
            select(SupportConversation.id).where(col(SupportConversation.expires_at) <= now_utc())
        )
        expired = list(result.all())
        if not expired:
            return 0
        await db.exec(delete(SupportTicket).where(col(SupportTicket.conversation_id).in_(expired)))
        await db.exec(delete(SupportFeedback).where(col(SupportFeedback.conversation_id).in_(expired)))
        await db.exec(delete(SupportMessage).where(col(SupportMessage.conversation_id).in_(expired)))
        await db.exec(delete(SupportConversation).where(col(SupportConversation.id).in_(expired)))
        await db.commit()
        return len(expired)


async def overview() -> tuple[int, int, int]:
    """Return counts for the staff dashboard."""
    since = datetime.now(UTC) - timedelta(days=30)
    async with session_factory() as db:
        open_count = await db.scalar(
            select(func.count()).select_from(SupportTicket).where(SupportTicket.status != "closed")
        )
        ticket_count = await db.scalar(select(func.count()).select_from(SupportTicket))
        conversation_count = await db.scalar(
            select(func.count()).select_from(SupportConversation).where(col(SupportConversation.created_at) >= since)
        )
        return int(open_count or 0), int(ticket_count or 0), int(conversation_count or 0)


async def save_feedback(conversation_id: str, message_id: str, feedback: FeedbackWrite) -> SupportFeedback | None:
    """Upsert feedback only for an assistant answer in the player's own conversation."""
    async with session_factory() as db:
        message = await db.get(SupportMessage, message_id)
        if message is None or message.conversation_id != conversation_id or message.role != "assistant":
            return None
        result = await db.exec(select(SupportFeedback).where(SupportFeedback.message_id == message_id))
        record = result.first() or SupportFeedback(conversation_id=conversation_id, message_id=message_id,
                                                   rating=feedback.rating)
        record.rating = feedback.rating
        record.reason = feedback.reason
        record.created_at = now_utc()
        db.add(record)
        await db.commit()
        return record


async def list_feedback(conversation_id: str) -> list[SupportFeedback]:
    """Read ratings for the current player's assistant messages."""
    async with session_factory() as db:
        result = await db.exec(select(SupportFeedback).where(SupportFeedback.conversation_id == conversation_id))
        return list(result.all())


async def latest_support_activity() -> tuple[str, str]:
    """Return timestamps used to notify staff of new messages and ratings."""
    async with session_factory() as db:
        message_at = await db.scalar(select(func.max(SupportMessage.created_at)))
        feedback_at = await db.scalar(select(func.max(SupportFeedback.created_at)))
        return (message_at.isoformat() if message_at else "", feedback_at.isoformat() if feedback_at else "")


async def quality_overview() -> tuple[int, int, int]:
    """Count player ratings and unassigned active tickets."""
    async with session_factory() as db:
        positive = await db.scalar(select(func.count()).select_from(SupportFeedback).where(SupportFeedback.rating == "positive"))
        negative = await db.scalar(select(func.count()).select_from(SupportFeedback).where(SupportFeedback.rating == "negative"))
        unassigned = await db.scalar(select(func.count()).select_from(SupportTicket).where(
            col(SupportTicket.status).in_(["open", "investigating"]), col(SupportTicket.assigned_to).is_(None)
        ))
        return int(positive or 0), int(negative or 0), int(unassigned or 0)
