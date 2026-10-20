"""Private knowledge images with metadata and review state in Milvus."""

import asyncio
import re
from uuid import uuid4

from app.core.logging import logger
from app.models.support import SupportDocumentImage, now_utc
from app.services import support_catalog as catalog
from app.services.support_documents import RetrievedPassage, embed_texts
from app.services.support_documents_v2 import document_page_text
from app.services.support_images import (
    MAX_IMAGE_BYTES as MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_DOCUMENT,
    _put_object,
    _remove_object,
    generate_image_description as generate_image_description,
    normalize_image,
    read_image as read_image,
)


async def list_document_images(document_id: str) -> list[SupportDocumentImage]:
    """List image metadata for the active document revision."""
    return await catalog.list_images(document_id)


async def get_image(image_id: str, *, published_only: bool = False) -> SupportDocumentImage | None:
    """Read a current image, enforcing publication when requested."""
    image = await catalog.get_image(image_id)
    if image is None:
        return None
    if published_only:
        document = await catalog.get_document(image.document_id, with_body=False)
        if document is None or document.status != "published" or not image.description:
            return None
    return image


async def create_image(
    document_id: str,
    filename: str,
    raw: bytes,
    *,
    page_number: int | None = None,
) -> SupportDocumentImage:
    """Store an image in MinIO and its review metadata in Milvus."""
    normalized = await asyncio.to_thread(normalize_image, raw)
    document = await catalog.get_document(document_id)
    if document is None:
        raise LookupError("Document not found")
    if page_number is not None and document_page_text(document, page_number) is None:
        raise ValueError("Document page not found")
    images = await catalog.list_images(document_id)
    if len(images) >= MAX_IMAGES_PER_DOCUMENT:
        raise ValueError("A document supports at most 20 images")
    image = SupportDocumentImage(
        document_id=document_id,
        object_key=f"documents/{document_id}/{uuid4()}.webp",
        filename=filename[:255],
        mime_type="image/webp",
        page_number=page_number,
    )
    await asyncio.to_thread(_put_object, image.object_key, normalized)
    try:
        await catalog.save_image(image)
    except Exception:
        await asyncio.to_thread(_remove_object, image.object_key)
        raise
    logger.info("support_image_uploaded", document_id=document_id, image_id=image.id)
    return image


async def update_image(image_id: str, description: str, page_number: int | None) -> SupportDocumentImage | None:
    """Update a reviewed caption and regenerate its vector."""
    image = await catalog.get_image(image_id)
    if image is None:
        return None
    document = await catalog.get_document(image.document_id)
    if document is None:
        return None
    if page_number is not None and document_page_text(document, page_number) is None:
        raise ValueError("Document page not found")
    vector = (await embed_texts([description]))[0]
    image.description = description
    image.embedding = vector
    image.page_number = page_number
    image.updated_at = now_utc()
    await catalog.save_image(image)
    return image


async def delete_image(image_id: str) -> bool:
    """Delete the image object and its Milvus metadata."""
    image = await catalog.get_image(image_id)
    if image is None:
        return False
    await catalog.delete_image(image)
    await asyncio.to_thread(_remove_object, image.object_key)
    return True


async def delete_document_images(document_id: str) -> None:
    """Remove all image objects before deleting a document."""
    images = await catalog.list_images(document_id)
    for image in images:
        await asyncio.to_thread(_remove_object, image.object_key)


async def search_images(query: str, limit: int = 3) -> list[RetrievedPassage]:
    """Find published caption text through the Milvus catalog."""
    terms = re.findall(r"[a-záéíóúüñ][a-záéíóúüñ0-9_]{2,}", query.casefold())
    terms.extend(
        phrase[start : start + 2]
        for phrase in re.findall(r"[\u3400-\u9fff]{2,}", query)
        for start in range(max(0, len(phrase) - 1))
    )
    if not terms:
        return []
    rows = await catalog.published_passages()
    ranked: list[tuple[int, RetrievedPassage]] = []
    for row in rows:
        if row["kind"] != "image":
            continue
        record = await catalog.get_document_record(str(row["source_id"]))
        if record is None or record["status"] != "published" or record["revision"] != row["revision"]:
            continue
        score = sum(term in str(row["content"]).casefold() for term in terms)
        if not score:
            continue
        document = await catalog.get_document(str(row["source_id"]), with_body=False)
        if document is None:
            continue
        page = int(row["page_number"])
        ranked.append(
            (
                score,
                RetrievedPassage(
                    id=str(row["id"]),
                    title=f"{document.title} · image",
                    content=str(row["content"]),
                    url=f"/api/v1/support/documents/{document.id}" + (f"?page={page}" if page >= 0 else ""),
                    distance=0.0,
                    page_number=page if page >= 0 else None,
                    image_id=str(row["id"]).removeprefix("image:"),
                ),
            )
        )
    return [passage for _, passage in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]]
