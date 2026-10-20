"""Persistent, staff-scoped progress for document indexing."""

from datetime import timedelta

from sqlalchemy import delete
from sqlmodel import col

from app.models.support import SupportDocumentUpload, now_utc
from app.services.support_store import session_factory


async def create_upload(upload_id: str, staff_user_id: int) -> None:
    """Start tracking after the request body has reached the API."""
    async with session_factory() as db:
        await db.exec(
            delete(SupportDocumentUpload).where(
                col(SupportDocumentUpload.updated_at) < now_utc() - timedelta(days=1)
            )
        )
        db.add(SupportDocumentUpload(id=upload_id, staff_user_id=staff_user_id))
        await db.commit()


async def update_upload(
    upload_id: str,
    staff_user_id: int,
    phase: str,
    percent: int,
    *,
    document_id: str | None = None,
) -> None:
    """Publish the latest server-side phase for a single staff upload."""
    async with session_factory() as db:
        upload = await db.get(SupportDocumentUpload, upload_id)
        if upload is None or upload.staff_user_id != staff_user_id:
            return
        upload.phase = phase
        upload.status = "failed" if phase == "failed" else "completed" if phase == "completed" else "processing"
        upload.percent = max(upload.percent, min(percent, 100))
        upload.document_id = document_id
        upload.updated_at = now_utc()
        db.add(upload)
        await db.commit()


async def get_upload(upload_id: str, staff_user_id: int) -> SupportDocumentUpload | None:
    """Expose progress only to the staff member who started it."""
    async with session_factory() as db:
        upload = await db.get(SupportDocumentUpload, upload_id)
        return upload if upload is not None and upload.staff_user_id == staff_user_id else None
