"""Exercise draft, keyword review, publication, retrieval, and deletion in Milvus.

Run against a disposable development stack with ``python -m scripts.smoke_support_catalog``.
The embedding call is stubbed so the check does not contact a model provider.
"""

import asyncio
import json

from rich.console import Console

from app.core.config import settings
from app.services import support_catalog, support_documents_v2

console = Console()


async def _embedding_stub(texts: list[str], *, progress=None) -> list[list[float]]:
    if progress is not None:
        await progress(len(texts), len(texts))
    return [[1.0] + [0.0] * (settings.SUPPORT_EMBEDDING_DIMENSIONS - 1) for _ in texts]


async def run() -> None:
    """Create one temporary source and verify its complete review lifecycle."""
    support_documents_v2.embed_texts = _embedding_stub
    source = await support_documents_v2.index_document(
        "Catalog smoke test", "catalog-smoke.txt", "text/plain", b"Omega support catalog smoke test content."
    )
    try:
        loaded = await support_documents_v2.get_document(source.id)
        assert loaded is not None and loaded.content == "Omega support catalog smoke test content."
        assert loaded.status == "draft"
        chunks = await support_documents_v2.list_document_chunks(source.id)
        assert len(chunks) == 1
        updated = await support_documents_v2.save_chunk_keywords(source.id, chunks[0].id, ["omega-check"])
        assert updated is not None and json.loads(updated.keywords_json) == ["omega-check"]
        published = await support_documents_v2.publish_document(source.id, 3)
        assert published is not None and published.status == "published"
        vectors = await support_catalog.search(
            [1.0] + [0.0] * (settings.SUPPORT_EMBEDDING_DIMENSIONS - 1), limit=8
        )
        assert any(hit.get("entity", {}).get("source_id") == source.id for hit in vectors)
        hits = await support_documents_v2.search_documents("omega-check")
        assert any(hit.title == source.title for hit in hits)
        console.print("Milvus knowledge lifecycle passed")
    finally:
        await support_documents_v2.delete_document(source.id)
        await support_catalog.close()


if __name__ == "__main__":
    asyncio.run(run())
