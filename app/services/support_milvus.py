"""Shared Milvus index for approved support articles, passages, and image captions."""

import asyncio
import json
from dataclasses import dataclass
from threading import Lock
from typing import Any

from pymilvus import DataType, MilvusClient
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings


@dataclass(frozen=True)
class SupportVector:
    """A searchable passage tied to one reviewed PostgreSQL source."""

    source_id: str
    item_id: str
    source_type: str
    revision: str
    title: str
    content: str
    url: str
    embedding: list[float]
    page_number: int | None = None
    image_id: str | None = None


_client: MilvusClient | None = None
_ready = False
_lock = Lock()


def _milvus_client() -> MilvusClient:
    global _client
    with _lock:
        if _client is None:
            options: dict[str, Any] = {"uri": settings.SUPPORT_MILVUS_URI, "timeout": 15}
            if settings.SUPPORT_MILVUS_TOKEN:
                options["token"] = settings.SUPPORT_MILVUS_TOKEN
            _client = MilvusClient(**options)
        return _client


def _ensure_collection() -> MilvusClient:
    global _ready
    client = _milvus_client()
    with _lock:
        if _ready:
            return client
        name = settings.SUPPORT_MILVUS_COLLECTION
        if not client.has_collection(name):
            schema = MilvusClient.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field("id", DataType.VARCHAR, max_length=128, is_primary=True)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=settings.SUPPORT_EMBEDDING_DIMENSIONS)
            schema.add_field("source_id", DataType.VARCHAR, max_length=64)
            schema.add_field("source_type", DataType.VARCHAR, max_length=24)
            schema.add_field("revision", DataType.VARCHAR, max_length=64)
            schema.add_field("embedding_model", DataType.VARCHAR, max_length=120)
            schema.add_field("title", DataType.VARCHAR, max_length=1024)
            schema.add_field("content", DataType.VARCHAR, max_length=8192)
            schema.add_field("url", DataType.VARCHAR, max_length=2048)
            schema.add_field("page_number", DataType.INT64)
            schema.add_field("image_id", DataType.VARCHAR, max_length=64)
            indexes = client.prepare_index_params()
            indexes.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
            client.create_collection(collection_name=name, schema=schema, index_params=indexes,
                                     consistency_level="Strong")
        client.load_collection(name)
        _ready = True
    return client


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _replace_source(source_id: str, records: list[SupportVector]) -> None:
    client = _ensure_collection()
    name = settings.SUPPORT_MILVUS_COLLECTION
    if not records:
        client.delete(collection_name=name, filter=f"source_id == {json.dumps(source_id)}")
        return
    revision = records[0].revision
    if any(record.source_id != source_id or record.revision != revision for record in records):
        raise ValueError("Milvus source batch must have one source and revision")
    rows = []
    for record in records:
        if len(record.embedding) != settings.SUPPORT_EMBEDDING_DIMENSIONS:
            raise ValueError("Support embedding dimensions do not match Milvus collection")
        rows.append({
            "id": f"{record.source_type}:{record.item_id}",
            "embedding": record.embedding,
            "source_id": source_id,
            "source_type": record.source_type,
            "revision": revision,
            "embedding_model": settings.SUPPORT_EMBEDDING_MODEL,
            "title": record.title,
            "content": record.content,
            "url": record.url,
            "page_number": record.page_number if record.page_number is not None else -1,
            "image_id": record.image_id or "",
        })
    client.upsert(collection_name=name, data=rows)
    client.delete(collection_name=name, filter=(
        f"source_id == {json.dumps(source_id)} and revision != {json.dumps(revision)}"
    ))


async def replace_source(source_id: str, records: list[SupportVector]) -> None:
    """Replace one source without blocking the event loop."""
    await asyncio.to_thread(_replace_source, source_id, records)


async def ensure_collection() -> None:
    """Create the collection on the first connection."""
    await asyncio.to_thread(_ensure_collection)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _search(vector: list[float], limit: int) -> list[dict[str, Any]]:
    client = _ensure_collection()
    model = json.dumps(settings.SUPPORT_EMBEDDING_MODEL)
    result = client.search(
        collection_name=settings.SUPPORT_MILVUS_COLLECTION,
        data=[vector],
        anns_field="embedding",
        filter=f"embedding_model == {model}",
        limit=limit,
        output_fields=["source_id", "source_type", "revision", "title", "content", "url", "page_number", "image_id"],
        search_params={"metric_type": "COSINE", "params": {}},
        consistency_level="Strong",
    )
    return result[0] if result else []


async def search(vector: list[float], limit: int = 12) -> list[dict[str, Any]]:
    """Search the same collection across all approved source kinds."""
    return await asyncio.to_thread(_search, vector, limit)


async def close() -> None:
    """Release the shared client on application shutdown."""
    global _client, _ready
    with _lock:
        client = _client
        _client = None
        _ready = False
    if client is not None:
        await asyncio.to_thread(client.close)
