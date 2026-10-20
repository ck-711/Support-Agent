"""Celery tasks for document parsing, OCR, embeddings, and Milvus indexing."""

import asyncio
from typing import Any

from celery import Celery
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger
from app.services import support_catalog
from app.services.support_documents_v2 import index_document, reindex_document
from app.services.support_store import engine as support_engine
from app.services.support_upload_progress import get_upload, update_upload
from app.services.support_upload_storage import get_upload as get_staged_upload
from app.services.support_upload_storage import remove_upload

celery_app = Celery("support", broker=settings.SUPPORT_CELERY_BROKER_URL)
celery_app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
)


async def _index_upload(upload_id: str, staff_user_id: int, object_key: str, options: dict[str, Any]) -> None:
    """Run one idempotent job using persisted progress and a private staged file."""
    state = await get_upload(upload_id, staff_user_id)
    if state is None or state.status == "completed":
        return
    raw = await get_staged_upload(object_key)

    async def progress(phase: str, percent: int) -> None:
        await update_upload(upload_id, staff_user_id, phase, percent)

    document = await index_document(
        title=str(options["title"]),
        filename=str(options["filename"]),
        mime_type=str(options["mime_type"]),
        raw=raw,
        source_url=options.get("source_url"),
        document_id=options.get("document_id"),
        source_type=str(options.get("source_type", "unknown")),
        game_version=options.get("game_version"),
        language=str(options.get("language", "en")),
        keywords=list(options.get("keywords", [])),
        progress=progress,
    )
    await update_upload(upload_id, staff_user_id, "completed", 100, document_id=document.id)


@celery_app.task(name="support.index_document")
def index_document_task(upload_id: str, staff_user_id: int, object_key: str, options: dict[str, Any]) -> None:
    """Run async knowledge indexing in a dedicated worker process."""

    async def run() -> None:
        try:
            await _index_upload(upload_id, staff_user_id, object_key, options)
        except Exception:
            logger.exception("support_document_job_failed", upload_id=upload_id, staff_user_id=staff_user_id)
            await update_upload(upload_id, staff_user_id, "failed", 0)
            raise
        finally:
            try:
                await remove_upload(object_key)
            except Exception:
                logger.exception("support_document_staged_file_cleanup_failed", upload_id=upload_id)
            await support_engine.dispose()
            await support_catalog.close()

    asyncio.run(run())


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _publish(upload_id: str, staff_user_id: int, object_key: str, options: dict[str, Any]) -> None:
    celery_app.send_task(
        "support.index_document", args=[upload_id, staff_user_id, object_key, options], task_id=upload_id
    )


async def enqueue_document(upload_id: str, staff_user_id: int, object_key: str, options: dict[str, Any]) -> None:
    """Publish a small JSON job; document bytes remain in MinIO."""
    await asyncio.to_thread(_publish, upload_id, staff_user_id, object_key, options)


@celery_app.task(name="support.reindex_document")
def reindex_document_task(upload_id: str, staff_user_id: int, document_id: str) -> None:
    """Rebuild existing passages from the Milvus original text in a worker."""

    async def run() -> None:
        try:
            await update_upload(upload_id, staff_user_id, "embedding", 50)
            document = await reindex_document(document_id)
            if document is None:
                raise LookupError("Document not found")
            await update_upload(upload_id, staff_user_id, "completed", 100, document_id=document.id)
        except Exception:
            logger.exception("support_document_reindex_job_failed", upload_id=upload_id, document_id=document_id)
            await update_upload(upload_id, staff_user_id, "failed", 0)
            raise
        finally:
            await support_engine.dispose()
            await support_catalog.close()

    asyncio.run(run())


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _publish_reindex(upload_id: str, staff_user_id: int, document_id: str) -> None:
    celery_app.send_task("support.reindex_document", args=[upload_id, staff_user_id, document_id], task_id=upload_id)


async def enqueue_reindex(upload_id: str, staff_user_id: int, document_id: str) -> None:
    """Queue an existing source for re-embedding."""
    await asyncio.to_thread(_publish_reindex, upload_id, staff_user_id, document_id)
