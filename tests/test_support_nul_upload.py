"""A PDF text layer with NUL characters remains indexable in PostgreSQL."""

import asyncio
import os
import selectors
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.main import app
from app.models.support import SupportDocumentUpload
from app.models.user import User
from app.services import support_documents
from app.services.support_store import session_factory


@pytest.mark.skipif(os.getenv("SUPPORT_INTEGRATION") != "1", reason="requires migrated local PostgreSQL")
def test_pdf_with_nul_uploads_and_reports_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the HTTP upload, PDF parser, embeddings, and database write."""
    page = SimpleNamespace(extract_text=lambda: "Weapon\x00 upgrade details", images=[])
    monkeypatch.setattr(support_documents, "PdfReader", lambda _raw: SimpleNamespace(pages=[page]))

    async def fake_embed(texts: list[str], progress=None) -> list[list[float]]:
        if progress is not None:
            await progress(len(texts), len(texts))
        return [[0.0] * 1024 for _ in texts]

    monkeypatch.setattr(support_documents, "embed_texts", fake_embed)
    upload_id = str(uuid4())
    staff_id: int | None = None

    async def staff_user() -> User:
        assert staff_id is not None
        return User(id=staff_id, email="staff@example.com", hashed_password="unused")

    app.dependency_overrides[get_current_user] = staff_user

    async def exercise() -> None:
        nonlocal staff_id
        async with session_factory() as db:
            staff = User(email=f"support-nul-{uuid4()}@example.com", hashed_password="unused")
            db.add(staff)
            await db.commit()
            await db.refresh(staff)
            staff_id = staff.id
        monkeypatch.setattr(settings, "SUPPORT_ADMIN_USER_IDS", str(staff_id))
        document_id: str | None = None
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/support/admin/documents",
                    data={"title": "NUL PDF", "upload_id": upload_id},
                    files={"file": ("nul.pdf", b"pdf", "application/pdf")},
                )
                assert response.status_code == 201, response.json().get("detail")
                document_id = response.json()["id"]
                progress = await client.get(f"/api/v1/support/admin/document-uploads/{upload_id}")
                assert progress.status_code == 200
                assert progress.json()["status"] == "completed"
                review = await client.get(f"/api/v1/support/admin/documents/{document_id}")
                assert review.status_code == 200
                assert "Weapon upgrade details" in review.json()["content"]
                assert "\x00" not in review.json()["content"]
                removed = await client.delete(f"/api/v1/support/admin/documents/{document_id}")
                assert removed.status_code == 204
                document_id = None
        finally:
            async with session_factory() as db:
                if document_id is not None:
                    document = await db.get(support_documents.SupportDocument, document_id)
                    if document is not None:
                        await db.delete(document)
                await db.exec(delete(SupportDocumentUpload).where(SupportDocumentUpload.id == upload_id))
                if staff_id is not None:
                    staff = await db.get(User, staff_id)
                    if staff is not None:
                        await db.delete(staff)
                await db.commit()

    try:
        asyncio.run(exercise(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    finally:
        app.dependency_overrides.clear()
