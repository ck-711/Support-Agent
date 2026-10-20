"""Copy legacy PostgreSQL support knowledge into the unified Milvus catalog.

Run ``python -m scripts.migrate_support_knowledge`` once after deploying the new code.
Run again with ``--purge-legacy`` only after verifying the staff dashboard.
"""

import argparse
import asyncio
import json
import selectors
import sys

from rich.console import Console
from sqlalchemy import delete
from sqlmodel import col, select

from app.core.config import settings
from app.models.support import SupportChunk, SupportDocument, SupportDocumentImage, SupportKnowledge
from app.services import support_catalog as catalog
from app.services.support_documents import (
    embed_texts,
    extract_keyword_candidates,
    normalize_keywords,
    split_document_pages,
)
from app.services.support_store import engine, session_factory

console = Console()


async def _legacy_rows() -> tuple[list[SupportKnowledge], list[SupportDocument]]:
    async with session_factory() as db:
        articles = list((await db.exec(select(SupportKnowledge))).all())
        documents = list((await db.exec(select(SupportDocument))).all())
    return articles, documents


async def _article_chunks(article: SupportKnowledge) -> list[SupportChunk]:
    passages = split_document_pages([(None, article.content)])

    def old_vectors() -> dict[str, list[float]]:
        client = catalog._collection()
        if not client.has_collection(settings.SUPPORT_MILVUS_COLLECTION):
            return {}
        rows = client.query(
            collection_name=settings.SUPPORT_MILVUS_COLLECTION,
            filter=f'source_id == {json.dumps(article.id)} and source_type == "article"',
            output_fields=["content", "embedding"],
            limit=800,
            consistency_level="Strong",
        )
        return {str(row["content"]): list(row["embedding"]) for row in rows}

    existing = await asyncio.to_thread(old_vectors)
    vectors = (
        [existing[text] for _, text in passages]
        if all(text in existing for _, text in passages)
        else await embed_texts([f"{article.title}\n{text}" for _, text in passages])
    )
    return [
        SupportChunk(
            document_id=article.id,
            position=position,
            page_number=page,
            content=text,
            keywords_json=json.dumps(extract_keyword_candidates(text), ensure_ascii=False),
            embedding=vector,
        )
        for position, ((page, text), vector) in enumerate(zip(passages, vectors, strict=True))
    ]


async def _copy_article(article: SupportKnowledge) -> None:
    if await catalog.get_document(article.id, with_body=False) is not None:
        return
    chunks = await _article_chunks(article)
    document = SupportDocument(
        id=article.id,
        title=article.title,
        filename=f"article-{article.id}.txt",
        mime_type="text/plain",
        source_url=article.source_url,
        source_type="official",
        language="en",
        keywords_json=json.dumps(normalize_keywords(extract_keyword_candidates(article.content)), ensure_ascii=False),
        content=article.content,
        status=article.status,
        chunk_count=len(chunks),
        embedding_model=settings.SUPPORT_EMBEDDING_MODEL,
        created_at=article.updated_at,
        updated_at=article.updated_at,
        reviewed_by=article.reviewed_by,
        review_due_at=article.review_due_at,
    )
    await catalog.save_document(document, [(None, article.content)], chunks)


async def _copy_document(document: SupportDocument) -> None:
    if await catalog.get_document(document.id, with_body=False) is not None:
        return
    async with session_factory() as db:
        chunks = list(
            (
                await db.exec(
                    select(SupportChunk)
                    .where(SupportChunk.document_id == document.id)
                    .order_by(col(SupportChunk.position))
                )
            ).all()
        )
        images = list(
            (await db.exec(select(SupportDocumentImage).where(SupportDocumentImage.document_id == document.id))).all()
        )
    if document.embedding_model != settings.SUPPORT_EMBEDDING_MODEL:
        vectors = await embed_texts([chunk.content for chunk in chunks])
        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk.embedding = vector
        described = [image for image in images if image.description.strip()]
        image_vectors = await embed_texts([image.description for image in described]) if described else []
        for image, vector in zip(described, image_vectors, strict=True):
            image.embedding = vector
    pages = [(item.get("page"), item["content"]) for item in json.loads(document.pages_json or "[]")]
    if not pages:
        pages = [(None, document.content)]
    document.chunk_count = len(chunks)
    document.embedding_model = settings.SUPPORT_EMBEDDING_MODEL
    await catalog.save_document(document, pages, chunks, images)


async def _verify(articles: list[SupportKnowledge], documents: list[SupportDocument]) -> None:
    for source in [*articles, *documents]:
        current = await catalog.get_document(source.id)
        if (
            current is None
            or current.content != source.content
            or current.status != source.status
            or current.title != source.title
            or current.source_url != source.source_url
            or current.reviewed_by != source.reviewed_by
            or current.review_due_at != source.review_due_at
        ):
            raise RuntimeError(f"Milvus source verification failed: {source.id}")
        expected_chunks = (
            source.chunk_count
            if isinstance(source, SupportDocument)
            else len(split_document_pages([(None, source.content)]))
        )
        copied_chunks = await catalog.list_chunks(source.id)
        if len(copied_chunks) != expected_chunks:
            raise RuntimeError(f"Milvus chunk verification failed: {source.id}")
        if isinstance(source, SupportDocument):
            async with session_factory() as db:
                images = list(
                    (
                        await db.exec(
                            select(SupportDocumentImage).where(SupportDocumentImage.document_id == source.id)
                        )
                    ).all()
                )
                chunks = list(
                    (
                        await db.exec(
                            select(SupportChunk)
                            .where(SupportChunk.document_id == source.id)
                            .order_by(col(SupportChunk.position))
                        )
                    ).all()
                )
            if current.keywords_json != source.keywords_json or current.source_type != source.source_type:
                raise RuntimeError(f"Milvus metadata verification failed: {source.id}")
            if [(item.content, item.keywords_json) for item in copied_chunks] != [
                (item.content, item.keywords_json) for item in chunks
            ]:
                raise RuntimeError(f"Milvus passage verification failed: {source.id}")
            copied_images = await catalog.list_images(source.id)
            if {(item.id, item.object_key, item.description) for item in copied_images} != {
                (item.id, item.object_key, item.description) for item in images
            }:
                raise RuntimeError(f"Milvus image verification failed: {source.id}")


async def _purge() -> None:
    async with session_factory() as db:
        await db.exec(delete(SupportDocumentImage))
        await db.exec(delete(SupportChunk))
        await db.exec(delete(SupportDocument))
        await db.exec(delete(SupportKnowledge))
        await db.commit()


async def migrate(purge_legacy: bool = False) -> None:
    """Copy and verify every legacy source, optionally clearing old SQL rows."""
    articles, documents = await _legacy_rows()
    try:
        await catalog.ensure_catalog()
        for article in articles:
            await _copy_article(article)
        for document in documents:
            await _copy_document(document)
        await _verify(articles, documents)
        console.print(f"Verified {len(articles)} articles and {len(documents)} documents in Milvus")
        if purge_legacy:
            await _purge()
            console.print("Cleared legacy PostgreSQL knowledge rows")
    finally:
        await catalog.close()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--purge-legacy", action="store_true", help="Clear PostgreSQL knowledge after verification")
    arguments = parser.parse_args()
    loop_factory = (lambda: asyncio.SelectorEventLoop(selectors.SelectSelector())) if sys.platform == "win32" else None
    asyncio.run(migrate(arguments.purge_legacy), loop_factory=loop_factory)
