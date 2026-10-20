"""Knowledge image validation and publication boundary checks."""

import asyncio
import os
import selectors
from io import BytesIO
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.main import app
from app.models.user import User
from app.services import support_documents, support_images
from app.services import game_support_agent
from app.services.support_documents import RetrievedPassage
from app.services.support_store import session_factory


def sample_image() -> bytes:
    """Create a small deterministic raster image."""
    output = BytesIO()
    Image.new("RGB", (200, 100), "#268ca2").save(output, format="PNG")
    return output.getvalue()


def test_image_normalization_rejects_invalid_file() -> None:
    """Only decoded, normalized raster pixels may reach object storage."""
    with pytest.raises(ValueError):
        support_images.normalize_image(b"<svg><script>alert(1)</script></svg>")
    with Image.open(BytesIO(support_images.normalize_image(sample_image()))) as image:
        assert image.format == "WEBP"
        assert image.size == (200, 100)


def test_cited_image_is_returned_to_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """An image citation carries a same-origin image URL into stored chat sources."""
    async def fake_call(*args, **kwargs):
        return game_support_agent.SupportAnswer(answer="The toggle is shown in Settings.", cited_ids=["image-1"])

    monkeypatch.setattr(game_support_agent.llm_service, "call", fake_call)
    passage = RetrievedPassage(
        id="image-1", title="Cloud save guide · image", content="Cloud save toggle in Settings",
        url="/api/v1/support/documents/document-1", distance=0.0, image_id="image-1",
    )
    result = asyncio.run(game_support_agent._answer({"question": "Where is the toggle?", "language": "en", "passages": [passage]}))
    assert result["sources"][0].image_url == "/api/v1/support/images/image-1"
    assert result["sources"][0].image_alt == passage.content


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("SUPPORT_INTEGRATION") != "1", reason="requires migrated local PostgreSQL")
def test_staff_image_stays_private_until_caption_and_document_approval(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uploads, retrieval and image bytes honor the same publication boundary."""
    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _ in texts]

    objects: dict[str, bytes] = {}
    monkeypatch.setattr(support_documents, "embed_texts", fake_embed)
    monkeypatch.setattr(support_images, "embed_texts", fake_embed)
    monkeypatch.setattr(support_images, "_put_object", lambda key, raw: objects.__setitem__(key, raw))
    monkeypatch.setattr(support_images, "_get_object", lambda key: objects[key])
    monkeypatch.setattr(support_images, "_remove_object", lambda key: objects.pop(key, None))
    monkeypatch.setattr(settings, "SUPPORT_EMBEDDING_API_KEY", "")
    staff_id: int | None = None

    async def staff_user() -> User:
        assert staff_id is not None
        return User(id=staff_id, email="staff@example.com", hashed_password="unused")

    app.dependency_overrides[get_current_user] = staff_user

    async def exercise() -> None:
        nonlocal staff_id
        async with session_factory() as db:
            staff = User(email=f"support-image-{uuid4()}@example.com", hashed_password="unused")
            db.add(staff)
            await db.commit()
            await db.refresh(staff)
            staff_id = staff.id
        monkeypatch.setattr(settings, "SUPPORT_ADMIN_USER_IDS", str(staff_id))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            document_id: str | None = None
            try:
                uploaded = await client.post(
                    "/api/v1/support/admin/documents",
                    data={"title": "Cloud save guide"},
                    files={"file": ("guide.txt", b"The cloud save menu is in Settings.", "text/plain")},
                )
                assert uploaded.status_code == 201, uploaded.text
                document_id = uploaded.json()["id"]
                image_upload = await client.post(
                    f"/api/v1/support/admin/documents/{document_id}/images",
                    files={"file": ("settings.png", sample_image(), "image/png")},
                )
                assert image_upload.status_code == 201, image_upload.text
                image_id = image_upload.json()["id"]
                review = await client.get(f"/api/v1/support/admin/documents/{document_id}")
                assert review.status_code == 200, review.text
                assert review.json()["images"][0]["id"] == image_id
                assert (await client.get(f"/api/v1/support/images/{image_id}")).status_code == 404
                assert (await client.post(f"/api/v1/support/admin/documents/{document_id}/publish")).status_code == 422
                saved = await client.put(
                    f"/api/v1/support/admin/images/{image_id}",
                    json={"description": "Cloud save toggle in the Settings panel", "page_number": None},
                )
                assert saved.status_code == 200, saved.text
                assert (await client.get(f"/api/v1/support/images/{image_id}")).status_code == 404
                approved = await client.post(f"/api/v1/support/admin/documents/{document_id}/publish")
                assert approved.status_code == 200, approved.text
                image_response = await client.get(f"/api/v1/support/images/{image_id}")
                assert image_response.status_code == 200
                assert image_response.headers["content-type"] == "image/webp"
                assert image_response.content.startswith(b"RIFF")
                results = await support_images.search_images("Cloud save")
                assert results and results[0].image_id == image_id
                monkeypatch.setattr(settings, "SUPPORT_EMBEDDING_API_KEY", "test-key")
                semantic_results = await support_images.search_images("unrelated wording")
                assert semantic_results and semantic_results[0].image_id == image_id
            finally:
                if document_id is not None:
                    assert (await client.delete(f"/api/v1/support/admin/documents/{document_id}")).status_code == 204
        async with session_factory() as db:
            staff = await db.get(User, staff_id)
            if staff is not None:
                await db.delete(staff)
                await db.commit()

    try:
        asyncio.run(exercise(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    finally:
        app.dependency_overrides.clear()
