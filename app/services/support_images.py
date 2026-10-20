"""Private knowledge image storage, review, and caption generation."""

import asyncio
import base64
import logging
import re
from io import BytesIO
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from minio import Minio
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import SecretStr
from sqlalchemy import delete, or_
from sqlmodel import col, select
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.models.support import SupportDocument, SupportDocumentImage, now_utc
from app.services.support_documents import RetrievedPassage, document_page_text, embed_texts
from app.services.support_store import session_factory

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_IMAGES_PER_DOCUMENT = 20
IMAGE_MIME_TYPE = "image/webp"


def _client() -> Minio:
    """Build a private object-store client only when configured."""
    if not settings.SUPPORT_MINIO_ACCESS_KEY or not settings.SUPPORT_MINIO_SECRET_KEY:
        raise RuntimeError("Support image storage is not configured")
    return Minio(
        settings.SUPPORT_MINIO_ENDPOINT,
        access_key=settings.SUPPORT_MINIO_ACCESS_KEY,
        secret_key=settings.SUPPORT_MINIO_SECRET_KEY,
        secure=settings.SUPPORT_MINIO_SECURE,
    )


def normalize_image(raw: bytes) -> bytes:
    """Validate and re-encode pixels so uploaded metadata and active formats are discarded."""
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the 10 MiB upload limit")
    try:
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ValueError("Only PNG, JPEG and WebP images are supported")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image exceeds the 20-megapixel limit")
            converted = ImageOps.exif_transpose(image).convert("RGB")
            converted.thumbnail((2400, 2400))
            output = BytesIO()
            converted.save(output, format="WEBP", quality=88, method=4)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Image could not be decoded") from exc
    return output.getvalue()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _put_object(object_key: str, raw: bytes) -> None:
    client = _client()
    bucket = settings.SUPPORT_MINIO_BUCKET
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, object_key, BytesIO(raw), len(raw), content_type=IMAGE_MIME_TYPE)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _get_object(object_key: str) -> bytes:
    response = _client().get_object(settings.SUPPORT_MINIO_BUCKET, object_key)
    try:
        return bytes(response.read())
    finally:
        response.close()
        response.release_conn()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _remove_object(object_key: str) -> None:
    _client().remove_object(settings.SUPPORT_MINIO_BUCKET, object_key)


async def list_document_images(document_id: str) -> list[SupportDocumentImage]:
    """Return image metadata in upload order."""
    async with session_factory() as db:
        result = await db.exec(
            select(SupportDocumentImage)
            .where(col(SupportDocumentImage.document_id) == document_id)
            .order_by(col(SupportDocumentImage.created_at))
        )
        return list(result.all())


async def get_image(image_id: str, *, published_only: bool = False) -> SupportDocumentImage | None:
    """Look up an image and enforce its parent document's publication when public."""
    async with session_factory() as db:
        image = await db.get(SupportDocumentImage, image_id)
        if image is None:
            return None
        if published_only:
            document = await db.get(SupportDocument, image.document_id)
            if document is None or document.status != "published" or not image.description:
                return None
        return image


async def read_image(image: SupportDocumentImage) -> bytes:
    """Fetch validated image bytes without blocking the event loop."""
    return await asyncio.to_thread(_get_object, image.object_key)


async def create_image(
    document_id: str, filename: str, raw: bytes, *, page_number: int | None = None
) -> SupportDocumentImage:
    """Upload a staff image and return its unapproved metadata."""
    normalized = await asyncio.to_thread(normalize_image, raw)
    async with session_factory() as db:
        document = await db.get(SupportDocument, document_id)
        if document is None:
            raise LookupError("Document not found")
        if page_number is not None and document_page_text(document, page_number) is None:
            raise ValueError("Document page not found")
        existing = await db.exec(select(SupportDocumentImage.id).where(col(SupportDocumentImage.document_id) == document_id))
        if len(existing.all()) >= MAX_IMAGES_PER_DOCUMENT:
            raise ValueError("A document supports at most 20 images")
        image = SupportDocumentImage(
            document_id=document_id,
            object_key=f"documents/{document_id}/{uuid4()}.webp",
            filename=filename[:255],
            mime_type=IMAGE_MIME_TYPE,
            page_number=page_number,
        )
        await asyncio.to_thread(_put_object, image.object_key, normalized)
        try:
            db.add(image)
            document.status = "draft"
            document.reviewed_by = None
            document.review_due_at = None
            document.updated_at = now_utc()
            db.add(document)
            await db.commit()
        except Exception:
            await asyncio.to_thread(_remove_object, image.object_key)
            raise
    logger.info("support_image_uploaded", document_id=document_id, image_id=image.id)
    return image


async def update_image(image_id: str, description: str, page_number: int | None) -> SupportDocumentImage | None:
    """Save an employee-reviewed caption and vector, returning the document to draft."""
    description = description.strip()
    async with session_factory() as db:
        image = await db.get(SupportDocumentImage, image_id)
        if image is None:
            return None
        document = await db.get(SupportDocument, image.document_id)
        if document is None:
            return None
        if page_number is not None and document_page_text(document, page_number) is None:
            raise ValueError("Document page not found")
    vector = (await embed_texts([description]))[0]
    async with session_factory() as db:
        image = await db.get(SupportDocumentImage, image_id)
        if image is None:
            return None
        document = await db.get(SupportDocument, image.document_id)
        if document is None:
            return None
        image.description = description
        image.embedding = vector
        image.page_number = page_number
        image.updated_at = now_utc()
        document.status = "draft"
        document.reviewed_by = None
        document.review_due_at = None
        document.updated_at = now_utc()
        db.add(image)
        db.add(document)
        await db.commit()
        return image


async def delete_image(image_id: str) -> bool:
    """Remove binary and metadata, invalidating publication."""
    async with session_factory() as db:
        image = await db.get(SupportDocumentImage, image_id)
        if image is None:
            return False
        document = await db.get(SupportDocument, image.document_id)
        await asyncio.to_thread(_remove_object, image.object_key)
        await db.delete(image)
        if document:
            document.status = "draft"
            document.reviewed_by = None
            document.review_due_at = None
            document.updated_at = now_utc()
            db.add(document)
        await db.commit()
        return True


async def delete_document_images(document_id: str) -> None:
    """Remove object bytes before deleting their database rows."""
    images = await list_document_images(document_id)
    for image in images:
        await asyncio.to_thread(_remove_object, image.object_key)
    async with session_factory() as db:
        await db.exec(delete(SupportDocumentImage).where(col(SupportDocumentImage.document_id) == document_id))
        await db.commit()


async def generate_image_description(image: SupportDocumentImage, document_title: str) -> str:
    """Draft a visual description for staff review; never publish it automatically."""
    if not settings.SUPPORT_VISION_MODEL or not settings.SUPPORT_VISION_API_KEY:
        raise RuntimeError("Vision model is not configured")
    # The OpenAI client logs full base64 image payloads at DEBUG level.
    logging.getLogger("openai").setLevel(logging.WARNING)
    raw = await read_image(image)
    model = ChatOpenAI(
        model=settings.SUPPORT_VISION_MODEL,
        api_key=SecretStr(settings.SUPPORT_VISION_API_KEY),
        base_url=settings.SUPPORT_VISION_BASE_URL or None,
        timeout=30,
        max_retries=0,
    )
    messages = [
        SystemMessage(content=(
            "Describe only what is visibly present in this DOTS Survivors knowledge image. "
            "Include readable UI labels, relevant controls, and their positions. "
            "Do not infer game mechanics, fixes, or facts that are not visible. "
            "Write one concise paragraph in the same language as the document title."
        )),
        HumanMessage(content=[
            {"type": "text", "text": f"Document title: {document_title}. Draft a searchable image description."},
            {"type": "image_url", "image_url": {"url": "data:image/webp;base64," + base64.b64encode(raw).decode("ascii")}},
        ]),
    ]
    callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
    response = await asyncio.wait_for(model.ainvoke(messages, config=RunnableConfig(callbacks=callbacks)), timeout=35)
    description = str(response.content).strip()
    if len(description) < 10:
        raise ValueError("Vision model did not return a useful description")
    logger.info("support_image_description_generated", image_id=image.id, model=settings.SUPPORT_VISION_MODEL)
    return description[:2000]


async def search_images(query: str, limit: int = 3) -> list[RetrievedPassage]:
    """Retrieve approved image descriptions using lexical and vector matches."""
    latin = re.findall(r"[a-záéíóúüñ][a-záéíóúüñ0-9_]{2,}", query.casefold())
    chinese = [
        phrase[start : start + 2]
        for phrase in re.findall(r"[\u3400-\u9fff]{2,}", query)
        for start in range(len(phrase) - 1)
    ]
    terms = list(dict.fromkeys([*latin, *chinese]))[:12]
    if not terms:
        return []
    async with session_factory() as db:
        conditions = [col(SupportDocumentImage.description).ilike(f"%{term}%") for term in terms]
        lexical = await db.exec(
            select(SupportDocumentImage, SupportDocument)
            .join(SupportDocument, col(SupportDocumentImage.document_id) == col(SupportDocument.id))
            .where(SupportDocument.status == "published", col(SupportDocumentImage.description) != "", or_(*conditions))
            .limit(12)
        )
        rows = sorted(
            lexical.all(),
            key=lambda row: sum(term in f"{row[1].title} {row[0].description}".casefold() for term in terms),
            reverse=True,
        )
    if settings.SUPPORT_EMBEDDING_API_KEY:
        try:
            vector = (await embed_texts([query]))[0]
            distance = SupportDocumentImage.embedding.cosine_distance(vector)  # pyright: ignore[reportOptionalMemberAccess,reportAttributeAccessIssue]
            async with session_factory() as db:
                vector_result = await db.exec(
                    select(SupportDocumentImage, SupportDocument, distance.label("distance"))
                    .join(SupportDocument, col(SupportDocumentImage.document_id) == col(SupportDocument.id))
                    .where(SupportDocument.status == "published", col(SupportDocumentImage.embedding).is_not(None))
                    .order_by(distance)
                    .limit(8)
                )
                for image, document, score in vector_result.all():
                    if score is not None and float(score) <= 0.72 and all(item.id != image.id for item, _ in rows):
                        rows.append((image, document))
        except Exception:
            logger.exception("support_image_vector_search_failed")
    return [
        RetrievedPassage(
            id=image.id,
            title=f"{document.title} · image: {image.filename}",
            content=image.description,
            url=f"/api/v1/support/documents/{document.id}" + (f"?page={image.page_number}" if image.page_number else ""),
            distance=0.0,
            page_number=image.page_number,
            image_id=image.id,
        )
        for image, document in rows[:limit]
    ]
