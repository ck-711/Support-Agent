"""Milvus publication boundary and shared source indexing checks."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from app.core.config import settings
from app.models.support import SupportDocument, SupportKnowledge, now_utc
from app.services import support_documents, support_milvus


def test_replace_source_indexes_articles_and_document_passages(monkeypatch) -> None:
    """One collection accepts both kinds and removes older source revisions."""
    calls: list[tuple[str, dict[str, Any]]] = []

    class Client:
        def upsert(self, **kwargs):
            calls.append(("upsert", kwargs))

        def delete(self, **kwargs):
            calls.append(("delete", kwargs))

    monkeypatch.setattr(support_milvus, "_ensure_collection", lambda: Client())
    vector = [1.0] + [0.0] * 1023
    article = support_milvus.SupportVector("article-1", "article-1:0", "article", "2", "FAQ", "Answer", "https://example.com", vector)
    document = support_milvus.SupportVector("doc-1", "chunk-1", "document", "2026-01-01", "Guide", "Passage", "/api/v1/support/documents/doc-1", vector)
    asyncio.run(support_milvus.replace_source("article-1", [article]))
    asyncio.run(support_milvus.replace_source("doc-1", [document]))
    rows = [entry for action, payload in calls if action == "upsert" for entry in payload["data"]]
    assert {row["source_type"] for row in rows} == {"article", "document"}
    assert all(row["embedding_model"] == settings.SUPPORT_EMBEDDING_MODEL for row in rows)
    assert any("revision !=" in payload["filter"] for action, payload in calls if action == "delete")


def test_search_discards_drafts_and_old_revisions(monkeypatch) -> None:
    """Milvus content is shown only if PostgreSQL still approves that exact revision."""
    published = SupportKnowledge(id="article-live", title="FAQ", content="Current answer", source_url="https://example.com",
                                 status="published", version=2)
    draft = SupportKnowledge(id="article-draft", title="Draft", content="Private answer", source_url="https://example.com",
                             status="draft", version=1)
    document = SupportDocument(id="doc-live", title="Guide", filename="guide.txt", mime_type="text/plain",
                               content="Current passage", embedding_model="test", status="published", updated_at=now_utc())

    class Result:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def exec(self, statement):
            return Result([published, draft] if "support_knowledge" in str(statement) else [document])

    def hit(source_id: str, source_type: str, revision: str, content: str) -> dict:
        return {"id": f"{source_type}:{source_id}", "distance": 0.9, "entity": {
            "source_id": source_id, "source_type": source_type, "revision": revision,
            "title": "Source", "content": content, "url": "https://example.com", "page_number": -1, "image_id": "",
        }}

    hits = [
        hit("article-live", "article", "2", "Current answer"),
        hit("article-draft", "article", "1", "Private answer"),
        hit("doc-live", "document", "outdated", "Old passage"),
        hit("doc-live", "document", document.updated_at.isoformat(), "Current passage"),
    ]
    monkeypatch.setattr(settings, "SUPPORT_EMBEDDING_API_KEY", "test-key")
    monkeypatch.setattr(support_documents, "embed_texts", AsyncMock(return_value=[[1.0] + [0.0] * 1023]))
    monkeypatch.setattr(support_documents, "search_milvus", AsyncMock(return_value=hits))
    monkeypatch.setattr(support_documents, "session_factory", Session)
    matches = asyncio.run(support_documents.search_support_vectors("question"))
    assert [item.content for item in matches] == ["Current answer", "Current passage"]
