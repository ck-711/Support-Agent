"""Exercise the Celery indexing handler directly against a development stack.

This validates MinIO staging, progress persistence, parsing, Milvus indexing, and
cleanup without requiring a broker. It creates and removes a temporary source.
"""

import asyncio
from uuid import uuid4

from rich.console import Console
from sqlalchemy import delete
from sqlmodel import select

from app.core.config import settings
from app.models.support import SupportDocumentUpload
from app.models.user import User
from app.services import support_documents_v2
from app.services.support_documents_v2 import delete_document, get_document
from app.services.support_store import engine, session_factory
from app.services.support_tasks import index_document_task
from app.services.support_upload_progress import create_upload, get_upload
from app.services.support_upload_storage import put_upload

console = Console()


async def _embedding_stub(texts: list[str], *, progress=None) -> list[list[float]]:
    if progress is not None:
        await progress(len(texts), len(texts))
    return [[1.0] + [0.0] * (settings.SUPPORT_EMBEDDING_DIMENSIONS - 1) for _ in texts]


async def _stage(upload_id: str, object_key: str) -> int:
    async with session_factory() as db:
        staff_id = (await db.exec(select(User.id).limit(1))).first()
    if staff_id is None:
        raise RuntimeError("Create a development user before running the worker smoke check")
    await create_upload(upload_id, staff_id)
    await put_upload(object_key, b"Omega worker smoke test content.", "text/plain")
    await engine.dispose()
    return staff_id


async def _verify_and_clean(upload_id: str, staff_id: int) -> None:
    state = await get_upload(upload_id, staff_id)
    if state is None or state.status != "completed" or state.document_id is None:
        raise RuntimeError("Worker did not complete the upload")
    document = await get_document(state.document_id)
    if document is None or document.content != "Omega worker smoke test content.":
        raise RuntimeError("Worker did not index the uploaded text")
    await delete_document(document.id)
    async with session_factory() as db:
        await db.exec(delete(SupportDocumentUpload).where(SupportDocumentUpload.id == upload_id))
        await db.commit()
    await engine.dispose()


def run() -> None:
    """Invoke a registered Celery task as a function after staging a test file."""
    support_documents_v2.embed_texts = _embedding_stub
    upload_id = str(uuid4())
    object_key = f"uploads/{upload_id}"
    staff_id = asyncio.run(_stage(upload_id, object_key))
    index_document_task(
        upload_id,
        staff_id,
        object_key,
        {
            "title": "Worker smoke test",
            "filename": "worker-smoke.txt",
            "mime_type": "text/plain",
            "source_type": "internal",
            "language": "en",
            "keywords": [],
        },
    )
    asyncio.run(_verify_and_clean(upload_id, staff_id))
    console.print("Celery indexing handler passed")


if __name__ == "__main__":
    run()
