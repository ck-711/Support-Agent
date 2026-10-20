"""Document import, review, and retrieval backed entirely by Milvus."""

import asyncio
import json
import re
from typing import Awaitable, Callable, Sequence

from app.core.config import settings
from app.core.logging import logger
from app.models.support import SupportChunk, SupportDocument, now_utc
from app.services import support_catalog as catalog
from app.services.support_documents import (
    MAX_UPLOAD_BYTES,
    RetrievedPassage,
    embed_texts,
    extract_keyword_candidates,
    normalize_keywords,
    parse_document_pages,
    split_document_pages,
    stored_keywords,
)


async def index_document(
    title: str,
    filename: str,
    mime_type: str,
    raw: bytes,
    source_url: str | None = None,
    document_id: str | None = None,
    *,
    source_type: str = "unknown",
    game_version: str | None = None,
    language: str = "en",
    keywords: Sequence[str] = (),
    progress: Callable[[str, int], Awaitable[None]] | None = None,
) -> SupportDocument:
    """Build draft passages and replace the active Milvus source revision."""
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Document exceeds the 50 MiB upload limit")
    if progress:
        await progress("parsing", 35)
    pages = await asyncio.to_thread(parse_document_pages, filename, raw)
    if progress:
        await progress("splitting", 45)
    passages = split_document_pages(pages)
    aliases = normalize_keywords(keywords)

    async def embed_progress(done: int, total: int) -> None:
        if progress:
            await progress("embedding", 50 + 40 * done // total)

    if progress:
        await progress("embedding", 50)
    vectors = await embed_texts([text for _, text in passages], progress=embed_progress if progress else None)
    existing = await catalog.get_document(document_id, with_body=False) if document_id else None
    if document_id and existing is None:
        raise LookupError("Document not found")
    document = existing or SupportDocument(
        title=title,
        filename=filename,
        mime_type=mime_type,
        content="",
        embedding_model=settings.SUPPORT_EMBEDDING_MODEL,
    )
    document.title, document.filename, document.mime_type = title, filename, mime_type
    document.source_url, document.source_type = source_url, source_type
    document.game_version, document.language = game_version, language
    document.keywords_json = json.dumps(aliases, ensure_ascii=False)
    document.content = "\n\n".join(text for _, text in pages)
    document.status = "draft"
    document.chunk_count = len(passages)
    document.embedding_model = settings.SUPPORT_EMBEDDING_MODEL
    document.updated_at = now_utc()
    document.reviewed_by = None
    document.review_due_at = None
    chunks = [
        SupportChunk(
            document_id=document.id,
            position=position,
            page_number=page,
            content=text,
            keywords_json=json.dumps(
                normalize_keywords([*aliases, *extract_keyword_candidates(text)]), ensure_ascii=False
            ),
            embedding=vector,
        )
        for position, ((page, text), vector) in enumerate(zip(passages, vectors, strict=True))
    ]
    images = await catalog.list_images(document.id) if existing else []
    if images:
        descriptions = [image.description for image in images if image.description.strip()]
        image_vectors = await embed_texts(descriptions) if descriptions else []
        for image, vector in zip((image for image in images if image.description.strip()), image_vectors, strict=True):
            image.embedding = vector
    if progress:
        await progress("saving", 95)
    await catalog.save_document(document, pages, chunks, images)
    logger.info("support_document_indexed", document_id=document.id, chunk_count=len(chunks))
    return document


async def reindex_document(document_id: str) -> SupportDocument | None:
    """Rebuild vectors from Milvus source text while preserving reviewed aliases."""
    document = await catalog.get_document(document_id)
    if document is None:
        return None
    old_chunks = await catalog.list_chunks(document_id)
    reviewed = {(item.page_number, item.content): stored_keywords(item.keywords_json) for item in old_chunks}
    pages = [(item["page"], item["content"]) for item in json.loads(document.pages_json or "[]")]
    passages = split_document_pages(pages)
    vectors = await embed_texts([text for _, text in passages])
    aliases = stored_keywords(document.keywords_json)
    chunks = [
        SupportChunk(
            document_id=document_id,
            position=position,
            page_number=page,
            content=text,
            keywords_json=json.dumps(
                normalize_keywords([*aliases, *(reviewed.get((page, text)) or extract_keyword_candidates(text))]),
                ensure_ascii=False,
            ),
            embedding=vector,
        )
        for position, ((page, text), vector) in enumerate(zip(passages, vectors, strict=True))
    ]
    images = await catalog.list_images(document_id)
    descriptions = [image.description for image in images if image.description.strip()]
    image_vectors = await embed_texts(descriptions) if descriptions else []
    for image, vector in zip((image for image in images if image.description.strip()), image_vectors, strict=True):
        image.embedding = vector
    document.status = "draft"
    document.reviewed_by = None
    document.review_due_at = None
    document.updated_at = now_utc()
    document.chunk_count = len(chunks)
    document.embedding_model = settings.SUPPORT_EMBEDDING_MODEL
    await catalog.save_document(document, pages, chunks, images)
    return document


async def list_documents() -> list[SupportDocument]:
    """List all indexed knowledge sources from Milvus."""
    return await catalog.list_documents()


async def get_document(document_id: str, published_only: bool = False) -> SupportDocument | None:
    """Read an active knowledge source, optionally requiring publication."""
    document = await catalog.get_document(document_id)
    return document if document and (not published_only or document.status == "published") else None


async def list_document_chunks(document_id: str) -> list[SupportChunk]:
    """Read the active passage revision for staff review."""
    return await catalog.list_chunks(document_id)


async def save_chunk_keywords(document_id: str, chunk_id: str, keywords: Sequence[str]) -> SupportChunk | None:
    """Replace staff-reviewed search terms on one passage."""
    return await catalog.save_chunk_keywords(document_id, chunk_id, normalize_keywords(keywords))


def document_page_text(document: SupportDocument, page_number: int | None) -> str | None:
    """Extract one source page or the whole original text."""
    if page_number is None:
        return document.content
    for page in json.loads(document.pages_json or "[]"):
        if page["page"] == page_number:
            return str(page["content"])
    return None


async def publish_document(document_id: str, staff_id: int) -> SupportDocument | None:
    """Expose a reviewed document and its passages to search."""
    document = await catalog.get_document(document_id, with_body=False)
    if document is None or document.chunk_count == 0:
        return None
    images = await catalog.list_images(document_id)
    if any(not image.description.strip() for image in images):
        raise ValueError("Every image needs a reviewed description before publication")
    return await catalog.set_document_status(document_id, "published", staff_id)


async def delete_document(document_id: str) -> bool:
    """Remove an indexed knowledge source from Milvus."""
    return await catalog.delete_document(document_id)


async def _passages_from_hits(hits: list[dict[str, object]], limit: int) -> list[RetrievedPassage]:
    seen: dict[str, tuple[str, str] | None] = {}
    passages: list[RetrievedPassage] = []
    for hit in hits:
        entity = hit["entity"]
        if not isinstance(entity, dict):
            continue
        source_id = str(entity["source_id"])
        if source_id not in seen:
            record = await catalog.get_document_record(source_id)
            seen[source_id] = (str(record["revision"]), str(record["status"])) if record else None
        if seen[source_id] != (str(entity["revision"]), "published"):
            continue
        document = await catalog.get_document(source_id, with_body=False)
        if document is None:
            continue
        score = float(str(hit["distance"]))
        if score < 0.28:
            continue
        page = int(entity["page_number"])
        image_id = str(hit["id"]).removeprefix("image:") if entity["kind"] == "image" else None
        url = (
            f"/api/v1/support/documents/{source_id}?page={page}"
            if page >= 0
            else document.source_url or f"/api/v1/support/documents/{source_id}"
        )
        passages.append(
            RetrievedPassage(
                id=str(hit["id"]),
                title=document.title,
                content=str(entity["content"]),
                url=url,
                distance=1 - score,
                page_number=page if page >= 0 else None,
                image_id=image_id,
            )
        )
        if len(passages) >= limit:
            break
    return passages


async def search_support_vectors(query: str, limit: int = 8) -> list[RetrievedPassage]:
    """Search the unified Milvus collection for published passages."""
    if not query.strip() or not settings.SUPPORT_EMBEDDING_API_KEY:
        return []
    try:
        vector = (await embed_texts([query]))[0]
        hits = await catalog.search(vector, max(limit * 3, 18))
        return await _passages_from_hits(hits, limit)
    except Exception:
        logger.exception("support_milvus_search_failed")
        return []


async def search_documents(query: str, limit: int = 5) -> list[RetrievedPassage]:
    """Use Milvus scalar data for exact term fallback when vector retrieval misses."""
    if not query.strip():
        return []
    terms = re.findall(r"[a-záéíóúüñ][a-záéíóúüñ0-9_]{2,}", query.casefold())
    terms.extend(
        phrase[start : start + 2]
        for phrase in re.findall(r"[\u3400-\u9fff]{2,}", query)
        for start in range(max(0, len(phrase) - 1))
    )
    terms = list(dict.fromkeys(terms))[:12]
    if not terms:
        return []
    try:
        rows = await catalog.published_passages()
    except Exception:
        logger.exception("support_lexical_search_failed")
        return []
    ranked: list[tuple[int, RetrievedPassage]] = []
    for row in rows:
        record = await catalog.get_document_record(str(row["source_id"]))
        if record is None or record["status"] != "published" or record["revision"] != row["revision"]:
            continue
        text = str(row["content"])
        metadata = row["metadata"]
        haystack = (text + " " + " ".join(metadata.get("keywords", []))).casefold()
        score = sum(1 for term in terms if term in haystack)
        if not score:
            continue
        document = await catalog.get_document(str(row["source_id"]), with_body=False)
        if document is None:
            continue
        page = int(row["page_number"])
        source_id = document.id
        ranked.append(
            (
                score,
                RetrievedPassage(
                    id=str(row["id"]),
                    title=document.title,
                    content=text,
                    url=(
                        f"/api/v1/support/documents/{source_id}?page={page}"
                        if page >= 0
                        else document.source_url or f"/api/v1/support/documents/{source_id}"
                    ),
                    distance=0.0,
                    page_number=page if page >= 0 else None,
                    image_id=str(row["id"]).removeprefix("image:") if row["kind"] == "image" else None,
                ),
            )
        )
    return [passage for _, passage in sorted(ranked, key=lambda item: item[0], reverse=True)[:limit]]
