"""Short-lived private staging for Celery document jobs."""

import asyncio
from io import BytesIO

from minio import Minio
from tenacity import retry, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.services.support_documents import MAX_UPLOAD_BYTES


def _client() -> Minio:
    if not settings.SUPPORT_MINIO_ACCESS_KEY or not settings.SUPPORT_MINIO_SECRET_KEY:
        raise RuntimeError("Support upload storage is not configured")
    return Minio(
        settings.SUPPORT_MINIO_ENDPOINT,
        access_key=settings.SUPPORT_MINIO_ACCESS_KEY,
        secret_key=settings.SUPPORT_MINIO_SECRET_KEY,
        secure=settings.SUPPORT_MINIO_SECURE,
    )


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _put(key: str, raw: bytes, mime_type: str) -> None:
    client = _client()
    bucket = settings.SUPPORT_MINIO_BUCKET
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    client.put_object(bucket, key, BytesIO(raw), len(raw), content_type=mime_type)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _get(key: str) -> bytes:
    response = _client().get_object(settings.SUPPORT_MINIO_BUCKET, key)
    try:
        raw = bytes(response.read(MAX_UPLOAD_BYTES + 1))
        if len(raw) > MAX_UPLOAD_BYTES:
            raise ValueError("Document exceeds the 50 MiB upload limit")
        return raw
    finally:
        response.close()
        response.release_conn()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=4), reraise=True)
def _remove(key: str) -> None:
    _client().remove_object(settings.SUPPORT_MINIO_BUCKET, key)


async def put_upload(key: str, raw: bytes, mime_type: str) -> None:
    """Stage bytes for a worker without sending them through Redis."""
    await asyncio.to_thread(_put, key, raw, mime_type)


async def get_upload(key: str) -> bytes:
    """Read a staged upload in the worker."""
    return await asyncio.to_thread(_get, key)


async def remove_upload(key: str) -> None:
    """Delete a staged upload after indexing or failure."""
    await asyncio.to_thread(_remove, key)
