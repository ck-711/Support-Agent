"""Milvus source of truth for reviewable support knowledge."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, Sequence
from uuid import uuid4

from pymilvus import DataType, MilvusClient
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.models.support import SupportChunk, SupportDocument, SupportDocumentImage

ROW_LIMIT = 8000
QUERY_LIMIT = 16384
_client: MilvusClient | None = None
_ready = False
_lock = Lock()


def _client_or_create() -> MilvusClient:
    global _client
    if _client is None:
        options: dict[str, Any] = {"uri": settings.SUPPORT_MILVUS_URI, "timeout": 30}
        if settings.SUPPORT_MILVUS_TOKEN:
            options["token"] = settings.SUPPORT_MILVUS_TOKEN
        _client = MilvusClient(**options)
    return _client


def _collection() -> MilvusClient:
    global _ready
    with _lock:
        client = _client_or_create()
        if _ready:
            return client
        name = settings.SUPPORT_CATALOG_COLLECTION
        if not client.has_collection(name):
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, max_length=128, is_primary=True)
            schema.add_field("source_id", DataType.VARCHAR, max_length=64)
            schema.add_field("kind", DataType.VARCHAR, max_length=16)
            schema.add_field("revision", DataType.VARCHAR, max_length=64)
            schema.add_field("status", DataType.VARCHAR, max_length=16)
            schema.add_field("position", DataType.INT64)
            schema.add_field("page_number", DataType.INT64)
            schema.add_field("content", DataType.VARCHAR, max_length=8192)
            schema.add_field("metadata", DataType.JSON)
            schema.add_field("embedding_model", DataType.VARCHAR, max_length=120)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=settings.SUPPORT_EMBEDDING_DIMENSIONS)
            indexes = client.prepare_index_params()
            indexes.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
            client.create_collection(
                collection_name=name, schema=schema, index_params=indexes, consistency_level="Strong"
            )
        client.load_collection(name)
        _ready = True
        return client


def _blank_vector() -> list[float]:
    return [1.0] + [0.0] * (settings.SUPPORT_EMBEDDING_DIMENSIONS - 1)


def _row(
    row_id: str,
    source_id: str,
    kind: str,
    revision: str,
    status: str,
    position: int = 0,
    page_number: int = -1,
    content: str = "",
    metadata: dict[str, Any] | None = None,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    if len(content.encode("utf-8")) > 8192:
        raise ValueError("Milvus knowledge passage exceeds 8192 bytes")
    vector = embedding if embedding is not None else _blank_vector()
    if len(vector) != settings.SUPPORT_EMBEDDING_DIMENSIONS:
        raise ValueError("Support embedding dimensions do not match Milvus collection")
    return {
        "id": row_id,
        "source_id": source_id,
        "kind": kind,
        "revision": revision,
        "status": status,
        "position": position,
        "page_number": page_number,
        "content": content,
        "metadata": metadata or {},
        "embedding_model": settings.SUPPORT_EMBEDDING_MODEL,
        "embedding": vector,
    }


def _segments(text: str) -> list[str]:
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        width = len(char.encode("utf-8"))
        if current and size + width > ROW_LIMIT:
            pieces.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    if current or not pieces:
        pieces.append("".join(current))
    return pieces


def _query(filter_text: str, *, limit: int = QUERY_LIMIT) -> list[dict[str, Any]]:
    return list(
        _collection().query(
            collection_name=settings.SUPPORT_CATALOG_COLLECTION,
            filter=filter_text,
            output_fields=[
                "id",
                "source_id",
                "kind",
                "revision",
                "status",
                "position",
                "page_number",
                "content",
                "metadata",
                "embedding_model",
                "embedding",
            ],
            limit=limit,
            consistency_level="Strong",
        )
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _upsert(rows: list[dict[str, Any]]) -> None:
    client = _collection()
    for start in range(0, len(rows), 100):
        client.upsert(collection_name=settings.SUPPORT_CATALOG_COLLECTION, data=rows[start : start + 100])


def _delete(filter_text: str) -> None:
    _collection().delete(collection_name=settings.SUPPORT_CATALOG_COLLECTION, filter=filter_text)


def _document_metadata(document: SupportDocument) -> dict[str, Any]:
    return {
        "title": document.title,
        "filename": document.filename,
        "mime_type": document.mime_type,
        "source_url": document.source_url,
        "source_type": document.source_type,
        "game_version": document.game_version,
        "language": document.language,
        "keywords_json": document.keywords_json,
        "chunk_count": document.chunk_count,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
        "reviewed_by": document.reviewed_by,
        "review_due_at": document.review_due_at.isoformat() if document.review_due_at else None,
    }


def _document_from_row(row: dict[str, Any]) -> SupportDocument:
    meta = row["metadata"]
    return SupportDocument(
        id=str(row["source_id"]),
        title=str(meta["title"]),
        filename=str(meta["filename"]),
        mime_type=str(meta["mime_type"]),
        source_url=meta.get("source_url"),
        source_type=str(meta.get("source_type", "unknown")),
        game_version=meta.get("game_version"),
        language=str(meta.get("language", "en")),
        keywords_json=str(meta.get("keywords_json", "[]")),
        content="",
        pages_json=None,
        status=str(row["status"]),
        chunk_count=int(meta.get("chunk_count", 0)),
        embedding_model=str(row["embedding_model"]),
        created_at=datetime.fromisoformat(meta["created_at"]),
        updated_at=datetime.fromisoformat(meta["updated_at"]),
        reviewed_by=meta.get("reviewed_by"),
        review_due_at=datetime.fromisoformat(meta["review_due_at"]) if meta.get("review_due_at") else None,
    )


def _current_row(source_id: str) -> dict[str, Any] | None:
    rows = _query(f"id == {json.dumps('document:' + source_id)}", limit=1)
    return rows[0] if rows else None


async def ensure_catalog() -> None:
    """Create and load the reviewable source collection."""
    await asyncio.to_thread(_collection)


async def get_document_record(source_id: str) -> dict[str, Any] | None:
    """Return the strongly consistent active source pointer for search filtering."""
    return await asyncio.to_thread(_current_row, source_id)


async def published_passages() -> list[dict[str, Any]]:
    """Read published passages for a small-scale lexical fallback."""
    return await asyncio.to_thread(_query, 'kind in ["chunk", "image"] and status == "published"', limit=QUERY_LIMIT)


async def get_document(source_id: str, *, with_body: bool = True) -> SupportDocument | None:
    """Read the active document revision and reconstruct original page text."""
    row = await asyncio.to_thread(_current_row, source_id)
    if row is None:
        return None
    document = _document_from_row(row)
    if with_body:
        revision = str(row["revision"])
        body = await asyncio.to_thread(
            _query, (f'source_id == {json.dumps(source_id)} and revision == {json.dumps(revision)} and kind == "body"')
        )
        pages: dict[int, list[str]] = {}
        for part in sorted(body, key=lambda item: int(item["position"])):
            pages.setdefault(int(part["page_number"]), []).append(str(part["content"]))
        values = [{"page": page if page >= 0 else None, "content": "".join(parts)} for page, parts in pages.items()]
        document.pages_json = json.dumps(values, ensure_ascii=False)
        document.content = "\n\n".join(item["content"] for item in values)
    return document


async def list_documents() -> list[SupportDocument]:
    """List knowledge imports and manually created text documents."""
    rows = await asyncio.to_thread(_query, 'kind == "document"', limit=2000)
    return sorted((_document_from_row(row) for row in rows), key=lambda item: item.updated_at, reverse=True)


async def list_chunks(source_id: str) -> list[SupportChunk]:
    """Read indexed chunks for the active source revision."""
    row = await asyncio.to_thread(_current_row, source_id)
    if row is None:
        return []
    revision = str(row["revision"])
    rows = await asyncio.to_thread(
        _query, (f'source_id == {json.dumps(source_id)} and revision == {json.dumps(revision)} and kind == "chunk"')
    )
    return [
        SupportChunk(
            id=str(item["id"]).removeprefix("chunk:"),
            document_id=source_id,
            position=int(item["position"]),
            page_number=int(item["page_number"]) if int(item["page_number"]) >= 0 else None,
            content=str(item["content"]),
            keywords_json=json.dumps(item["metadata"].get("keywords", []), ensure_ascii=False),
            embedding=list(item["embedding"]),
        )
        for item in sorted(rows, key=lambda value: int(value["position"]))
    ]


async def list_images(source_id: str) -> list[SupportDocumentImage]:
    """Read private image metadata from the active revision."""
    row = await asyncio.to_thread(_current_row, source_id)
    if row is None:
        return []
    revision = str(row["revision"])
    rows = await asyncio.to_thread(
        _query, (f'source_id == {json.dumps(source_id)} and revision == {json.dumps(revision)} and kind == "image"')
    )
    return [_image_from_row(item) for item in rows]


def _image_from_row(row: dict[str, Any]) -> SupportDocumentImage:
    meta = row["metadata"]
    return SupportDocumentImage(
        id=str(row["id"]).removeprefix("image:"),
        document_id=str(row["source_id"]),
        object_key=str(meta["object_key"]),
        filename=str(meta["filename"]),
        mime_type=str(meta["mime_type"]),
        description=str(row["content"]),
        page_number=int(row["page_number"]) if int(row["page_number"]) >= 0 else None,
        embedding=list(row["embedding"]) if row["content"] else None,
        created_at=datetime.fromisoformat(meta["created_at"]),
        updated_at=datetime.fromisoformat(meta["updated_at"]),
    )


async def get_image(image_id: str) -> SupportDocumentImage | None:
    """Read an image only when it belongs to the current source revision."""
    rows = await asyncio.to_thread(_query, f"id == {json.dumps('image:' + image_id)}", limit=1)
    if not rows:
        return None
    row = rows[0]
    document = await asyncio.to_thread(_current_row, str(row["source_id"]))
    if document is None or document["revision"] != row["revision"]:
        return None
    return _image_from_row(row)


async def save_document(
    document: SupportDocument,
    pages: Sequence[tuple[int | None, str]],
    chunks: Sequence[SupportChunk],
    images: Sequence[SupportDocumentImage] = (),
) -> None:
    """Write a complete revision before switching the active document pointer."""
    revision = uuid4().hex
    existing = await asyncio.to_thread(_current_row, document.id)
    rows: list[dict[str, Any]] = []
    offset = 0
    for page, text in pages:
        for segment in _segments(text):
            rows.append(
                _row(
                    f"body:{document.id}:{revision}:{offset}",
                    document.id,
                    "body",
                    revision,
                    document.status,
                    position=offset,
                    page_number=page if page is not None else -1,
                    content=segment,
                )
            )
            offset += 1
    for chunk in chunks:
        rows.append(
            _row(
                f"chunk:{chunk.id}",
                document.id,
                "chunk",
                revision,
                document.status,
                position=chunk.position,
                page_number=chunk.page_number if chunk.page_number is not None else -1,
                content=chunk.content,
                metadata={"keywords": json.loads(chunk.keywords_json)},
                embedding=chunk.embedding,
            )
        )
    for image in images:
        rows.append(_image_row(image, revision, document.status))
    await asyncio.to_thread(_upsert, rows)
    await asyncio.to_thread(
        _upsert,
        [
            _row(
                f"document:{document.id}",
                document.id,
                "document",
                revision,
                document.status,
                metadata=_document_metadata(document),
            )
        ],
    )
    if existing is not None:
        old_revision = str(existing["revision"])
        await asyncio.to_thread(
            _delete,
            (
                f'source_id == {json.dumps(document.id)} and revision == {json.dumps(old_revision)} and kind != "document"'
            ),
        )


def _image_row(image: SupportDocumentImage, revision: str, status: str) -> dict[str, Any]:
    return _row(
        f"image:{image.id}",
        image.document_id,
        "image",
        revision,
        status,
        page_number=image.page_number if image.page_number is not None else -1,
        content=image.description,
        metadata={
            "object_key": image.object_key,
            "filename": image.filename,
            "mime_type": image.mime_type,
            "created_at": image.created_at.isoformat(),
            "updated_at": image.updated_at.isoformat(),
        },
        embedding=image.embedding,
    )


async def save_image(image: SupportDocumentImage) -> None:
    """Save private image metadata and return its source to draft review."""
    row = await asyncio.to_thread(_current_row, image.document_id)
    if row is None:
        raise LookupError("Document not found")
    await asyncio.to_thread(_upsert, [_image_row(image, str(row["revision"]), "draft")])
    await set_document_status(image.document_id, "draft")


async def delete_image(image: SupportDocumentImage) -> None:
    """Remove image metadata and return its source to draft review."""
    await asyncio.to_thread(_delete, f"id == {json.dumps('image:' + image.id)}")
    await set_document_status(image.document_id, "draft")


async def save_chunk_keywords(source_id: str, chunk_id: str, keywords: list[str]) -> SupportChunk | None:
    """Update reviewed search terms within the active passage revision."""
    row = await asyncio.to_thread(_current_row, source_id)
    chunks = await list_chunks(source_id)
    chunk = next((item for item in chunks if item.id == chunk_id), None)
    if row is None or chunk is None:
        return None
    chunk.keywords_json = json.dumps(keywords, ensure_ascii=False)
    await asyncio.to_thread(
        _upsert,
        [
            _row(
                f"chunk:{chunk.id}",
                source_id,
                "chunk",
                str(row["revision"]),
                str(row["status"]),
                position=chunk.position,
                page_number=chunk.page_number if chunk.page_number is not None else -1,
                content=chunk.content,
                metadata={"keywords": keywords},
                embedding=chunk.embedding,
            )
        ],
    )
    return chunk


async def set_document_status(source_id: str, status: str, staff_id: int | None = None) -> SupportDocument | None:
    """Change review state; publish searchable rows before exposing the document."""
    row = await asyncio.to_thread(_current_row, source_id)
    if row is None:
        return None
    document = _document_from_row(row)
    document.status = status
    document.reviewed_by = staff_id if status == "published" else None
    document.updated_at = datetime.now(UTC)
    document.review_due_at = None
    if status == "published":
        document.review_due_at = document.updated_at + timedelta(days=90)
    revision = str(row["revision"])
    if status == "published":
        records = await asyncio.to_thread(
            _query,
            (
                f"source_id == {json.dumps(source_id)} and revision == {json.dumps(revision)} and "
                'kind in ["chunk", "image"]'
            ),
        )
        for record in records:
            record["status"] = status
        if records:
            await asyncio.to_thread(_upsert, records)
    await asyncio.to_thread(
        _upsert,
        [
            _row(
                f"document:{source_id}",
                source_id,
                "document",
                revision,
                status,
                metadata=_document_metadata(document),
            )
        ],
    )
    return document


async def delete_document(source_id: str) -> bool:
    """Remove every row for one source from the unified collection."""
    if await asyncio.to_thread(_current_row, source_id) is None:
        return False
    await asyncio.to_thread(_delete, f"source_id == {json.dumps(source_id)}")
    return True


async def search(vector: list[float], limit: int = 18) -> list[dict[str, Any]]:
    """Return published passages from the active embedding model."""

    def run() -> list[dict[str, Any]]:
        client = _collection()
        hits = client.search(
            collection_name=settings.SUPPORT_CATALOG_COLLECTION,
            data=[vector],
            anns_field="embedding",
            filter=(
                'kind in ["chunk", "image"] and status == "published" and '
                f"embedding_model == {json.dumps(settings.SUPPORT_EMBEDDING_MODEL)}"
            ),
            limit=limit,
            output_fields=["source_id", "kind", "revision", "content", "metadata", "page_number"],
            search_params={"metric_type": "COSINE", "params": {}},
            consistency_level="Strong",
        )
        return hits[0] if hits else []

    return await asyncio.to_thread(run)


async def close() -> None:
    """Release the process-local Milvus client."""
    global _client, _ready
    with _lock:
        client, _client, _ready = _client, None, False
    if client is not None:
        await asyncio.to_thread(client.close)
