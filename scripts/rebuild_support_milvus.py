"""Backfill approved support articles and documents into the shared Milvus index."""

import asyncio
import sys

from rich.console import Console
from rich.progress import track
from sqlmodel import select

from app.models.support import SupportDocument, SupportKnowledge
from app.services.support_documents import article_vectors, sync_published_document
from app.services.support_milvus import close, ensure_collection, replace_source
from app.services.support_store import session_factory


async def rebuild() -> None:
    """Reconcile currently published sources without changing their review state."""
    console = Console()
    await ensure_collection()
    async with session_factory() as db:
        article_ids = list((await db.exec(
            select(SupportKnowledge.id).where(SupportKnowledge.status == "published")
        )).all())
        document_ids = list((await db.exec(
            select(SupportDocument.id).where(SupportDocument.status == "published")
        )).all())
    try:
        for article_id in track(article_ids, description="Indexing articles", console=console):
            async with session_factory() as db:
                article = await db.get(SupportKnowledge, article_id)
            if article is not None and article.status == "published":
                await replace_source(article.id, await article_vectors(article))
        for document_id in track(document_ids, description="Indexing documents", console=console):
            await sync_published_document(document_id)
        console.print(f"[green]Milvus ready:[/green] {len(article_ids)} articles, {len(document_ids)} documents")
    finally:
        await close()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(rebuild())
