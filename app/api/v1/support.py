"""Player and staff routes for DOTS Survivors support."""

import asyncio
import json
from pathlib import Path
from typing import Annotated, Any, AsyncIterator
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import HttpUrl, TypeAdapter
from starlette.responses import Response, StreamingResponse

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.models.support import (
    SupportConversation,
    SupportDocument,
    SupportDocumentImage,
    SupportMessage,
    SupportTicket,
    now_utc,
)
from app.models.user import User
from app.schemas.support import (
    ChatResult,
    ConversationClaim,
    ConversationCreated,
    ConversationStart,
    ChunkKeywordsWrite,
    DocumentChunkRead,
    DocumentImageRead,
    DocumentImageWrite,
    DocumentRead,
    DocumentUploadProgress,
    DocumentReview,
    DocumentSource,
    FeedbackRead,
    FeedbackWrite,
    MessageCreate,
    MessageRead,
    SourceReference,
    SupportOverview,
    SupportQualityOverview,
    TicketCreate,
    TicketContext,
    TicketRead,
    TicketUpdate,
    ToolCallRequest,
    ToolCallResult,
    TextDocumentWrite,
    VisionDescription,
)
from app.services.game_support_agent import answer_player, classify_handoff
from app.services.support_documents_v2 import (
    MAX_UPLOAD_BYTES,
    delete_document,
    document_page_text,
    extract_keyword_candidates,
    get_document,
    list_document_chunks,
    list_documents,
    normalize_keywords,
    publish_document,
    save_chunk_keywords,
    stored_keywords,
)
from app.services.support_images_v2 import (
    MAX_IMAGE_BYTES,
    create_image,
    delete_document_images,
    delete_image,
    generate_image_description,
    get_image,
    list_document_images,
    read_image,
    update_image,
)
from app.services.support_upload_progress import create_upload, get_upload, update_upload
from app.services.support_upload_storage import put_upload, remove_upload
from app.services.support_tasks import enqueue_document, enqueue_reindex
from app.services.support_store import (
    add_message,
    claim_ticket,
    claim_legacy_conversation,
    create_conversation,
    create_ticket,
    create_handoff_ticket,
    delete_conversation,
    find_conversation,
    get_active_handoff,
    get_ticket,
    list_feedback,
    latest_support_activity,
    list_messages,
    list_tickets,
    overview,
    save_feedback,
    quality_overview,
    release_ticket,
    update_ticket,
)
from app.services.support_tools import execute_support_tool

router = APIRouter()


async def current_player(user: User = Depends(get_current_user)) -> SupportConversation:
    """Resolve the conversation owned by the authenticated account."""
    conversation = await find_conversation(user.id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="No active support conversation")
    return conversation


async def current_admin(user: User = Depends(get_current_user)) -> User:
    """Require an explicitly configured staff user ID."""
    ids = {item.strip() for item in settings.SUPPORT_ADMIN_USER_IDS.split(",") if item.strip()}
    if str(user.id) not in ids:
        raise HTTPException(status_code=403, detail="Support staff access required")
    return user


def message_read(message: SupportMessage) -> MessageRead:
    """Map a stored message to its public response."""
    sources = [SourceReference.model_validate(item) for item in json.loads(message.sources_json)]
    return MessageRead(
        id=message.id,
        role=message.role,
        content=message.content,
        sources=sources,
        created_at=message.created_at,
    )


def ticket_read(ticket: SupportTicket) -> TicketRead:
    """Map a ticket to a response."""
    return TicketRead.model_validate(ticket, from_attributes=True)


def document_read(document: SupportDocument) -> DocumentRead:
    """Map indexed document metadata to staff output."""
    parsed = DocumentRead.model_validate(document, from_attributes=True)
    return parsed.model_copy(update={"keywords": stored_keywords(document.keywords_json)})


async def queue_document_upload(
    *,
    upload_id: str,
    staff_user_id: int,
    raw: bytes,
    mime_type: str,
    options: dict[str, Any],
) -> DocumentUploadProgress:
    """Stage a bounded file outside Redis and queue its Milvus indexing task."""
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the 50 MiB upload limit")
    object_key = f"uploads/{upload_id}"
    await create_upload(upload_id, staff_user_id)
    try:
        await put_upload(object_key, raw, mime_type)
        await enqueue_document(upload_id, staff_user_id, object_key, options)
    except Exception as exc:
        await update_upload(upload_id, staff_user_id, "failed", 30)
        try:
            await remove_upload(object_key)
        except Exception:
            logger.exception("support_document_staged_file_cleanup_failed", upload_id=upload_id)
        logger.exception("support_document_queue_failed", upload_id=upload_id, staff_user_id=staff_user_id)
        raise HTTPException(status_code=503, detail="Document indexing queue unavailable") from exc
    return DocumentUploadProgress(id=upload_id, status="processing", phase="queued", percent=30)


HANDOFF_ACK = {
    "en": "Your message has reached the support team. A staff member is handling this conversation; AI replies are paused.",
    "es": "El equipo de soporte recibió tu mensaje. Una persona atiende esta conversación; la IA está en pausa.",
    "zh": "消息已转交人工客服。此对话正由团队处理，AI 回复已暂停。",
}


@router.post("/conversations", response_model=ConversationCreated, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
async def start_conversation(
    request: Request, data: ConversationStart, user: User = Depends(get_current_user)
) -> ConversationCreated:
    """Resume or start support for the signed-in player."""
    conversation = await create_conversation(data.language, user.id)
    logger.info("support_conversation_opened", conversation_id=conversation.id, user_id=user.id)
    return ConversationCreated(conversation_id=conversation.id, expires_at=conversation.expires_at)


@router.post("/conversations/claim", response_model=ConversationCreated)
@limiter.limit("10/hour")
async def claim_conversation(
    request: Request, data: ConversationClaim, user: User = Depends(get_current_user)
) -> ConversationCreated:
    """Import this browser's old anonymous history into the signed-in account."""
    try:
        conversation = await claim_legacy_conversation(user.id, data.token)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if conversation is None:
        raise HTTPException(status_code=404, detail="Legacy support conversation not found")
    logger.info("support_conversation_claimed", conversation_id=conversation.id, user_id=user.id)
    return ConversationCreated(conversation_id=conversation.id, expires_at=conversation.expires_at)


@router.get("/messages", response_model=list[MessageRead])
@limiter.limit("50/minute")
async def get_messages(
    request: Request,
    before: UUID | None = None,
    after: UUID | None = None,
    player: SupportConversation = Depends(current_player),
) -> list[MessageRead]:
    """Show the current player's conversation."""
    if before is not None and after is not None:
        raise HTTPException(status_code=400, detail="Choose either before or after")
    try:
        records = await list_messages(
            player.id, before=str(before) if before else None, after=str(after) if after else None
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Message cursor not found") from None
    return [message_read(item) for item in records]


@router.post("/messages", response_model=ChatResult)
@limiter.limit("20/minute")
async def send_message(
    request: Request, data: MessageCreate, player: SupportConversation = Depends(current_player)
) -> ChatResult:
    """Answer a player's question and create a ticket for a reported malfunction."""
    previous = await list_messages(player.id, limit=20)
    await add_message(player.id, "player", data.content)
    active = await get_active_handoff(player.id)
    if active is not None:
        ack = await add_message(
            player.id, "handoff", HANDOFF_ACK.get(data.language or player.language, HANDOFF_ACK["en"])
        )
        return ChatResult(
            message=message_read(ack), ticket_id=active.id, handoff_reason=active.handoff_reason, handoff_active=True
        )
    try:
        result = await answer_player(
            data.content, data.language or player.language, [(m.role, m.content) for m in previous], player.id
        )
    except Exception:
        logger.exception("support_chat_failed", conversation_id=player.id)
        raise HTTPException(status_code=503, detail="Support assistant temporarily unavailable")

    active = await get_active_handoff(player.id)
    if active is not None:
        ack = await add_message(
            player.id, "handoff", HANDOFF_ACK.get(data.language or player.language, HANDOFF_ACK["en"])
        )
        return ChatResult(
            message=message_read(ack), ticket_id=active.id, handoff_reason=active.handoff_reason, handoff_active=True
        )
    message = await add_message(player.id, "assistant", result.get("answer", ""), result.get("sources", []))
    ticket_id = result.get("tool_ticket_id")
    handoff_reason = classify_handoff(data.content, result)
    if handoff_reason and not ticket_id:
        ticket = await create_handoff_ticket(player.id, data.content, handoff_reason)
        ticket_id = ticket.id
        logger.info(
            "support_ticket_auto_created", ticket_id=ticket_id, conversation_id=player.id, reason=handoff_reason
        )
    return ChatResult(
        message=message_read(message),
        ticket_id=ticket_id,
        handoff_reason=handoff_reason,
        handoff_active=bool(ticket_id),
    )


@router.get("/events")
@limiter.limit("10/minute")
async def player_events(request: Request, player: SupportConversation = Depends(current_player)) -> StreamingResponse:
    """Push message and ticket changes to the current player's page."""

    async def events() -> AsyncIterator[str]:
        fingerprint = ""
        ticks = 0
        while not await request.is_disconnected() and player.expires_at > now_utc():
            messages = await list_messages(player.id, limit=1)
            tickets = await list_tickets(player.id)
            current = json.dumps(
                [
                    messages[-1].id if messages else "",
                    [(item.id, item.status, item.updated_at.isoformat(), item.assigned_to) for item in tickets],
                ]
            )
            if current != fingerprint:
                fingerprint = current
                yield "event: refresh\ndata: {}\n\n"
            elif ticks % 8 == 0:
                yield ": keepalive\n\n"
            ticks += 1
            await asyncio.sleep(2)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/feedback", response_model=list[FeedbackRead])
@limiter.limit("40/minute")
async def player_feedback(
    request: Request, player: SupportConversation = Depends(current_player)
) -> list[FeedbackRead]:
    """Read ratings previously submitted in this conversation."""
    return [FeedbackRead.model_validate(item, from_attributes=True) for item in await list_feedback(player.id)]


@router.put("/messages/{message_id}/feedback", response_model=FeedbackRead)
@limiter.limit("20/minute")
async def rate_message(
    request: Request, message_id: str, data: FeedbackWrite, player: SupportConversation = Depends(current_player)
) -> FeedbackRead:
    """Save a player's rating of their own AI response."""
    record = await save_feedback(player.id, message_id, data)
    if record is None:
        raise HTTPException(status_code=404, detail="Assistant answer not found")
    logger.info("support_answer_rated", message_id=message_id, rating=data.rating)
    return FeedbackRead.model_validate(record, from_attributes=True)


@router.post("/tickets", response_model=TicketRead, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
async def submit_ticket(
    request: Request, data: TicketCreate, player: SupportConversation = Depends(current_player)
) -> TicketRead:
    """Submit an explicit issue report."""
    ticket = await create_ticket(player.id, data)
    logger.info("support_ticket_created", ticket_id=ticket.id, conversation_id=player.id)
    return ticket_read(ticket)


@router.get("/tickets", response_model=list[TicketRead])
@limiter.limit("40/minute")
async def player_tickets(request: Request, player: SupportConversation = Depends(current_player)) -> list[TicketRead]:
    """Show a player's issue status."""
    return [ticket_read(ticket) for ticket in await list_tickets(player.id)]


@router.delete("/conversations/current", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/hour")
async def erase_conversation(request: Request, player: SupportConversation = Depends(current_player)) -> None:
    """Erase the player's conversation and related tickets."""
    await delete_conversation(player.id)
    logger.info("support_conversation_deleted", conversation_id=player.id)


@router.get("/admin/overview", response_model=SupportOverview)
@limiter.limit("60/minute")
async def admin_overview(request: Request, admin: User = Depends(current_admin)) -> SupportOverview:
    """Display staff workload and knowledge counts."""
    open_count, tickets, conversations = await overview()
    published = sum(item.status == "published" for item in await list_documents())
    return SupportOverview(
        open_tickets=open_count,
        total_tickets=tickets,
        published_documents=published,
        conversations_30d=conversations,
    )


@router.get("/admin/tickets", response_model=list[TicketRead])
@limiter.limit("60/minute")
async def admin_tickets(request: Request, admin: User = Depends(current_admin)) -> list[TicketRead]:
    """Display staff ticket queue."""
    return [ticket_read(ticket) for ticket in await list_tickets()]


@router.patch("/admin/tickets/{ticket_id}", response_model=TicketRead)
@limiter.limit("30/minute")
async def admin_update_ticket(
    request: Request, ticket_id: str, data: TicketUpdate, admin: User = Depends(current_admin)
) -> TicketRead:
    """Set ticket status and optionally reply to the player."""
    try:
        ticket = await update_ticket(ticket_id, data, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    logger.info("support_ticket_updated", ticket_id=ticket_id, staff_user_id=admin.id, status=data.status)
    return ticket_read(ticket)


@router.post("/admin/tickets/{ticket_id}/claim", response_model=TicketRead)
@limiter.limit("30/minute")
async def admin_claim_ticket(request: Request, ticket_id: str, admin: User = Depends(current_admin)) -> TicketRead:
    """Claim an active ticket for exclusive staff handling."""
    try:
        ticket = await claim_ticket(ticket_id, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    logger.info("support_ticket_claimed", ticket_id=ticket_id, staff_user_id=admin.id)
    return ticket_read(ticket)


@router.post("/admin/tickets/{ticket_id}/release", response_model=TicketRead)
@limiter.limit("30/minute")
async def admin_release_ticket(request: Request, ticket_id: str, admin: User = Depends(current_admin)) -> TicketRead:
    """Release a ticket to the staff queue."""
    try:
        ticket = await release_ticket(ticket_id, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    logger.info("support_ticket_released", ticket_id=ticket_id, staff_user_id=admin.id)
    return ticket_read(ticket)


@router.get("/admin/events")
@limiter.limit("10/minute")
async def admin_events(request: Request, admin: User = Depends(current_admin)) -> StreamingResponse:
    """Push staff queue changes without exposing player tokens in URLs."""

    async def events() -> AsyncIterator[str]:
        fingerprint = ""
        ticks = 0
        while not await request.is_disconnected():
            tickets = await list_tickets()
            activity = await latest_support_activity()
            current = json.dumps(
                {
                    "tickets": [
                        (item.id, item.status, item.updated_at.isoformat(), item.assigned_to) for item in tickets
                    ],
                    "activity": activity,
                }
            )
            if current != fingerprint:
                fingerprint = current
                yield "event: refresh\ndata: {}\n\n"
            elif ticks % 8 == 0:
                yield ": keepalive\n\n"
            ticks += 1
            await asyncio.sleep(2)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@router.get("/admin/quality", response_model=SupportQualityOverview)
@limiter.limit("60/minute")
async def admin_quality(request: Request, admin: User = Depends(current_admin)) -> SupportQualityOverview:
    """Show feedback, overdue source, and queue metrics."""
    positive, negative, unassigned = await quality_overview()
    stale_documents = sum(
        item.status == "published" and item.review_due_at is not None and item.review_due_at <= now_utc()
        for item in await list_documents()
    )
    return SupportQualityOverview(
        positive_feedback=positive,
        negative_feedback=negative,
        stale_documents=stale_documents,
        unassigned_tickets=unassigned,
    )


@router.get("/admin/tickets/{ticket_id}/context", response_model=TicketContext)
@limiter.limit("60/minute")
async def admin_ticket_context(
    request: Request, ticket_id: str, admin: User = Depends(current_admin)
) -> TicketContext:
    """Show the entire conversation associated with a handoff."""
    ticket = await get_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    messages = await list_messages(ticket.conversation_id, limit=None)
    return TicketContext(ticket=ticket_read(ticket), messages=[message_read(item) for item in messages])


@router.post("/tools/{tool_name}", response_model=ToolCallResult)
@limiter.limit("20/minute")
async def call_support_tool(
    request: Request,
    tool_name: str,
    data: ToolCallRequest,
    player: SupportConversation = Depends(current_player),
) -> ToolCallResult:
    """Execute a named player-scoped business tool through the API."""
    try:
        result = await execute_support_tool(tool_name, data.arguments, player.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    logger.info("support_tool_executed", tool_name=tool_name, conversation_id=player.id)
    return ToolCallResult(name=tool_name, result=result)


@router.get("/documents/{document_id}", response_model=DocumentSource)
@limiter.limit("40/minute")
async def public_document(
    request: Request,
    document_id: str,
    page: Annotated[int | None, Query(ge=1, le=200)] = None,
) -> DocumentSource:
    """Expose citations for published documents only."""
    document = await get_document(document_id, published_only=True)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    content = document_page_text(document, page)
    if content is None:
        raise HTTPException(status_code=404, detail="Document page not found")
    return DocumentSource(title=document.title, content=content, source_url=document.source_url, page_number=page)


@router.get("/images/{image_id}")
@limiter.limit("60/minute")
async def public_image(request: Request, image_id: str) -> Response:
    """Serve only images belonging to published knowledge documents."""
    image = await get_image(image_id, published_only=True)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        raw = await read_image(image)
    except Exception as exc:
        logger.exception("support_public_image_read_failed", image_id=image_id)
        raise HTTPException(status_code=503, detail="Image storage unavailable") from exc
    return Response(
        raw,
        media_type=image.mime_type,
        headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"},
    )


def document_image_read(image: SupportDocumentImage) -> DocumentImageRead:
    """Return image metadata without exposing its private object key."""
    return DocumentImageRead(
        id=image.id,
        document_id=image.document_id,
        filename=image.filename,
        description=image.description,
        page_number=image.page_number,
        created_at=image.created_at,
        url=f"/api/v1/support/images/{image.id}",
    )


@router.get("/admin/documents", response_model=list[DocumentRead])
@limiter.limit("60/minute")
async def admin_documents(request: Request, admin: User = Depends(current_admin)) -> list[DocumentRead]:
    """List uploaded sources and index state."""
    return [document_read(item) for item in await list_documents()]


@router.get("/admin/documents/{document_id}", response_model=DocumentReview)
@limiter.limit("60/minute")
async def admin_document_source(
    request: Request, document_id: str, admin: User = Depends(current_admin)
) -> DocumentReview:
    """Let staff inspect source text, passage boundaries, and keyword candidates."""
    document = await get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    chunks = await list_document_chunks(document_id)
    images = await list_document_images(document_id)
    return DocumentReview(
        id=document.id,
        title=document.title,
        filename=document.filename,
        source_url=document.source_url,
        source_type=document.source_type,
        game_version=document.game_version,
        language=document.language,
        keywords=stored_keywords(document.keywords_json),
        content=document.content,
        chunks=[
            DocumentChunkRead(
                id=chunk.id,
                position=chunk.position,
                page_number=chunk.page_number,
                content=chunk.content,
                keywords=stored_keywords(chunk.keywords_json) or extract_keyword_candidates(chunk.content),
            )
            for chunk in chunks
        ],
        images=[document_image_read(image) for image in images],
    )


@router.post(
    "/admin/documents/{document_id}/images", response_model=DocumentImageRead, status_code=status.HTTP_201_CREATED
)
@limiter.limit("20/hour")
async def admin_upload_image(
    request: Request,
    document_id: str,
    file: Annotated[UploadFile, File()],
    page_number: Annotated[int | None, Form(ge=1, le=200)] = None,
    admin: User = Depends(current_admin),
) -> DocumentImageRead:
    """Attach an image to a knowledge document for caption review."""
    raw = await file.read(MAX_IMAGE_BYTES + 1)
    await file.close()
    try:
        image = await create_image(document_id, Path(file.filename or "image").name, raw, page_number=page_number)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("support_image_upload_failed", document_id=document_id, staff_user_id=admin.id)
        raise HTTPException(status_code=503, detail="Image storage unavailable") from exc
    return document_image_read(image)


@router.get("/admin/images/{image_id}/content")
@limiter.limit("60/minute")
async def admin_image_content(request: Request, image_id: str, admin: User = Depends(current_admin)) -> Response:
    """Allow staff to preview draft images using authenticated fetch."""
    image = await get_image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    try:
        raw = await read_image(image)
    except Exception as exc:
        logger.exception("support_admin_image_read_failed", image_id=image_id)
        raise HTTPException(status_code=503, detail="Image storage unavailable") from exc
    return Response(
        raw, media_type=image.mime_type, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    )


@router.post("/admin/images/{image_id}/describe", response_model=VisionDescription)
@limiter.limit("20/hour")
async def admin_describe_image(
    request: Request, image_id: str, admin: User = Depends(current_admin)
) -> VisionDescription:
    """Suggest a visual description that staff must review and save."""
    image = await get_image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    document = await get_document(image.document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    try:
        description = await generate_image_description(image, document.title)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("support_image_description_failed", image_id=image_id, staff_user_id=admin.id)
        raise HTTPException(status_code=503, detail="Vision model unavailable") from exc
    return VisionDescription(description=description)


@router.put("/admin/images/{image_id}", response_model=DocumentImageRead)
@limiter.limit("30/minute")
async def admin_update_image(
    request: Request, image_id: str, data: DocumentImageWrite, admin: User = Depends(current_admin)
) -> DocumentImageRead:
    """Approve a searchable image description as a draft change."""
    try:
        image = await update_image(image_id, data.description, data.page_number)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("support_image_caption_index_failed", image_id=image_id, staff_user_id=admin.id)
        raise HTTPException(status_code=503, detail="Image indexing unavailable") from exc
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    logger.info("support_image_caption_saved", image_id=image_id, staff_user_id=admin.id)
    return document_image_read(image)


@router.delete("/admin/images/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("20/hour")
async def admin_delete_image(request: Request, image_id: str, admin: User = Depends(current_admin)) -> None:
    """Remove a knowledge image and return its document to draft."""
    try:
        removed = await delete_image(image_id)
    except Exception as exc:
        logger.exception("support_image_delete_failed", image_id=image_id, staff_user_id=admin.id)
        raise HTTPException(status_code=503, detail="Image storage unavailable") from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Image not found")
    logger.info("support_image_deleted", image_id=image_id, staff_user_id=admin.id)


@router.put("/admin/documents/{document_id}/chunks/{chunk_id}/keywords", response_model=DocumentChunkRead)
@limiter.limit("30/minute")
async def admin_update_chunk_keywords(
    request: Request,
    document_id: str,
    chunk_id: str,
    data: ChunkKeywordsWrite,
    admin: User = Depends(current_admin),
) -> DocumentChunkRead:
    """Save staff-reviewed search terms for one document passage."""
    document = await get_document(document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    chunk = await save_chunk_keywords(document_id, chunk_id, normalize_keywords(data.keywords))
    if chunk is None:
        raise HTTPException(status_code=404, detail="Document passage not found")
    logger.info("support_chunk_keywords_updated", document_id=document_id, chunk_id=chunk_id, staff_user_id=admin.id)
    return DocumentChunkRead(
        id=chunk.id,
        position=chunk.position,
        page_number=chunk.page_number,
        content=chunk.content,
        keywords=stored_keywords(chunk.keywords_json),
    )


@router.post("/admin/documents", response_model=DocumentUploadProgress, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/hour")
async def admin_upload_document(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=3, max_length=200)],
    source_url: Annotated[str | None, Form()] = None,
    source_type: Annotated[str, Form()] = "unknown",
    game_version: Annotated[str | None, Form(max_length=64)] = None,
    language: Annotated[str, Form()] = "en",
    keywords: Annotated[str, Form(max_length=1000)] = "",
    upload_id: Annotated[UUID | None, Form()] = None,
    admin: User = Depends(current_admin),
) -> DocumentUploadProgress:
    """Queue a staff upload for parsing and Milvus indexing."""
    url = None
    if source_url:
        try:
            url = str(TypeAdapter(HttpUrl).validate_python(source_url))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid source URL") from exc
    filename = Path(file.filename or "").name
    if source_type not in {"unknown", "official", "community", "internal"}:
        raise HTTPException(status_code=422, detail="Invalid source type")
    if language not in {"en", "es", "zh"}:
        raise HTTPException(status_code=422, detail="Invalid language")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    return await queue_document_upload(
        upload_id=str(upload_id or uuid4()),
        staff_user_id=admin.id,
        raw=raw,
        mime_type=file.content_type or "application/octet-stream",
        options={
            "title": title,
            "filename": filename,
            "mime_type": file.content_type or "application/octet-stream",
            "source_url": url,
            "source_type": source_type,
            "game_version": game_version or None,
            "language": language,
            "keywords": keywords.replace("，", ",").replace("\n", ",").split(","),
        },
    )


@router.post("/admin/documents/text", response_model=DocumentUploadProgress, status_code=status.HTTP_202_ACCEPTED)
@limiter.limit("10/hour")
async def admin_create_text_document(
    request: Request,
    data: TextDocumentWrite,
    admin: User = Depends(current_admin),
) -> DocumentUploadProgress:
    """Queue staff-authored text through the same indexing and review pipeline."""
    raw = data.content.encode("utf-8")
    return await queue_document_upload(
        upload_id=str(uuid4()),
        staff_user_id=admin.id,
        raw=raw,
        mime_type="text/plain; charset=utf-8",
        options={
            "title": data.title,
            "filename": f"{uuid4().hex}.txt",
            "mime_type": "text/plain",
            "source_url": str(data.source_url) if data.source_url else None,
            "source_type": data.source_type,
            "game_version": data.game_version,
            "language": data.language,
            "keywords": data.keywords,
        },
    )


@router.put(
    "/admin/documents/{document_id}", response_model=DocumentUploadProgress, status_code=status.HTTP_202_ACCEPTED
)
@limiter.limit("10/hour")
async def admin_replace_document(
    request: Request,
    document_id: str,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=3, max_length=200)],
    source_url: Annotated[str | None, Form()] = None,
    source_type: Annotated[str, Form()] = "unknown",
    game_version: Annotated[str | None, Form(max_length=64)] = None,
    language: Annotated[str, Form()] = "en",
    keywords: Annotated[str, Form(max_length=1000)] = "",
    upload_id: Annotated[UUID | None, Form()] = None,
    admin: User = Depends(current_admin),
) -> DocumentUploadProgress:
    """Queue replacement and return its progress identifier."""
    if await get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    url = None
    if source_url:
        try:
            url = str(TypeAdapter(HttpUrl).validate_python(source_url))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid source URL") from exc
    filename = Path(file.filename or "").name
    if source_type not in {"unknown", "official", "community", "internal"}:
        raise HTTPException(status_code=422, detail="Invalid source type")
    if language not in {"en", "es", "zh"}:
        raise HTTPException(status_code=422, detail="Invalid language")
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    return await queue_document_upload(
        upload_id=str(upload_id or uuid4()),
        staff_user_id=admin.id,
        raw=raw,
        mime_type=file.content_type or "application/octet-stream",
        options={
            "title": title,
            "filename": filename,
            "mime_type": file.content_type or "application/octet-stream",
            "source_url": url,
            "document_id": document_id,
            "source_type": source_type,
            "game_version": game_version or None,
            "language": language,
            "keywords": keywords.replace("，", ",").replace("\n", ",").split(","),
        },
    )


@router.get("/admin/document-uploads/{upload_id}", response_model=DocumentUploadProgress)
@limiter.limit("120/minute")
async def admin_document_upload_progress(
    request: Request, upload_id: UUID, admin: User = Depends(current_admin)
) -> DocumentUploadProgress:
    """Return live indexing progress to the staff member who uploaded the file."""
    upload = await get_upload(str(upload_id), admin.id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return DocumentUploadProgress(
        id=upload.id,
        status=upload.status,
        phase=upload.phase,
        percent=upload.percent,
        document_id=upload.document_id,
    )


@router.post(
    "/admin/documents/{document_id}/reindex",
    response_model=DocumentUploadProgress,
    status_code=status.HTTP_202_ACCEPTED,
)
@limiter.limit("10/hour")
async def admin_reindex_document(
    request: Request, document_id: str, admin: User = Depends(current_admin)
) -> DocumentUploadProgress:
    """Queue a vector rebuild and return a progress identifier."""
    if await get_document(document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    upload_id = str(uuid4())
    await create_upload(upload_id, admin.id)
    try:
        await enqueue_reindex(upload_id, admin.id, document_id)
    except Exception as exc:
        await update_upload(upload_id, admin.id, "failed", 0)
        logger.exception("support_document_reindex_failed", document_id=document_id)
        raise HTTPException(status_code=503, detail="Document indexing queue unavailable") from exc
    return DocumentUploadProgress(id=upload_id, status="processing", phase="queued", percent=30)


@router.post("/admin/documents/{document_id}/publish", response_model=DocumentRead)
@limiter.limit("30/minute")
async def admin_publish_document(
    request: Request, document_id: str, admin: User = Depends(current_admin)
) -> DocumentRead:
    """Publish indexed passages after staff review."""
    try:
        document = await publish_document(document_id, admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("support_document_publish_index_failed", document_id=document_id)
        raise HTTPException(status_code=503, detail="Knowledge indexing unavailable") from exc
    if document is None:
        raise HTTPException(status_code=404, detail="Indexed document not found")
    logger.info("support_document_published", document_id=document_id, staff_user_id=admin.id)
    return document_read(document)


@router.delete("/admin/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("10/hour")
async def admin_delete_document(request: Request, document_id: str, admin: User = Depends(current_admin)) -> None:
    """Delete a source and its vector index."""
    try:
        await delete_document_images(document_id)
        removed = await delete_document(document_id)
    except Exception as exc:
        logger.exception("support_document_delete_failed", document_id=document_id, staff_user_id=admin.id)
        raise HTTPException(status_code=503, detail="Image storage unavailable") from exc
    if not removed:
        raise HTTPException(status_code=404, detail="Document not found")
    logger.info("support_document_deleted", document_id=document_id, staff_user_id=admin.id)
