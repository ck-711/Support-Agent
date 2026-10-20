"""Opt-in HTTP and pgvector checks for document RAG and human handoff."""

import asyncio
import os
import selectors
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.v1.auth import get_current_user
from app.api.v1 import support as support_api
from app.core.config import settings
from app.main import app
from app.models.user import User
from app.services import support_documents
from app.services.support_store import add_message, session_factory


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("SUPPORT_INTEGRATION") != "1", reason="requires migrated local PostgreSQL")
def test_document_lifecycle_and_handoff(monkeypatch) -> None:
    """Draft isolation, vector retrieval, scoped tool, and full staff transcript work together."""
    async def fake_embed(texts: list[str]) -> list[list[float]]:
        return [[1.0] + [0.0] * 1023 for _ in texts]

    monkeypatch.setattr(support_documents, "embed_texts", fake_embed)
    monkeypatch.setattr(settings, "SUPPORT_EMBEDDING_API_KEY", "test-key")
    staff_id: int | None = None

    async def staff_user() -> User:
        assert staff_id is not None
        return User(id=staff_id, email="staff@example.com", hashed_password="unused")

    app.dependency_overrides[get_current_user] = staff_user

    async def exercise() -> None:
        nonlocal staff_id
        async with session_factory() as db:
            staff = User(email=f"support-rag-{uuid4()}@example.com", hashed_password="unused")
            db.add(staff)
            await db.commit()
            await db.refresh(staff)
            staff_id = staff.id
        monkeypatch.setattr(settings, "SUPPORT_ADMIN_USER_IDS", str(staff_id))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            started = await client.post("/api/v1/support/conversations", json={"language": "zh"})
            conversation_id = started.json()["conversation_id"]
            player = {"Authorization": "Bearer test-staff-token"}
            document_id: str | None = None
            try:
                upload = await client.post(
                    "/api/v1/support/admin/documents",
                    data={"title": "QA facts"},
                    files={"file": ("qa.txt", b"The support team can investigate achievement failures.", "text/plain")},
                )
                assert upload.status_code == 201, upload.text
                document_id = upload.json()["id"]
                assert upload.json()["status"] == "draft"
                assert upload.json()["chunk_count"] == 1
                hidden = await client.get(f"/api/v1/support/documents/{document_id}")
                assert hidden.status_code == 404
                assert await support_documents.search_documents("achievement") == []
                approved = await client.post(f"/api/v1/support/admin/documents/{document_id}/publish")
                assert approved.status_code == 200, approved.text
                assert (await client.get(f"/api/v1/support/documents/{document_id}")).status_code == 200
                matches = await support_documents.search_documents("achievement")
                assert matches and matches[0].title == "QA facts"
                monkeypatch.setattr(settings, "SUPPORT_EMBEDDING_API_KEY", "")
                lexical_matches = await support_documents.search_documents("achievement")
                assert lexical_matches and lexical_matches[0].title == "QA facts"
                monkeypatch.setattr(settings, "SUPPORT_EMBEDDING_API_KEY", "test-key")
                replaced = await client.put(
                    f"/api/v1/support/admin/documents/{document_id}",
                    data={"title": "Updated QA facts"},
                    files={"file": ("updated.txt", b"Staff can inspect updated support material.", "text/plain")},
                )
                assert replaced.status_code == 200, replaced.text
                assert replaced.json()["status"] == "draft"
                assert await support_documents.search_documents("achievement") == []
                assert (await client.post(f"/api/v1/support/admin/documents/{document_id}/publish")).status_code == 200

                await add_message(conversation_id, "player", "My achievement did not unlock")
                tool = await client.post(
                    "/api/v1/support/tools/request_human_handoff", headers=player,
                    json={"arguments": {"summary": "Achievement did not unlock", "reason": "complex"}},
                )
                assert tool.status_code == 200, tool.text
                ticket_id = tool.json()["result"]["ticket_id"]
                context = await client.get(f"/api/v1/support/admin/tickets/{ticket_id}/context")
                assert context.status_code == 200, context.text
                assert context.json()["ticket"]["handoff_reason"] == "complex"
                assert any(message["content"] == "My achievement did not unlock" for message in context.json()["messages"])
                claim = await client.post(f"/api/v1/support/admin/tickets/{ticket_id}/claim")
                assert claim.status_code == 200, claim.text
                reply = await client.patch(
                    f"/api/v1/support/admin/tickets/{ticket_id}",
                    json={"status": "investigating", "reply": "We are checking this."},
                )
                assert reply.status_code == 200, reply.text
                transcript = await client.get(f"/api/v1/support/admin/tickets/{ticket_id}/context")
                assert any(message["role"] == "staff" for message in transcript.json()["messages"])
                paused = await client.post("/api/v1/support/messages", headers=player,
                                           json={"content": "还有一条线索", "language": "zh"})
                assert paused.status_code == 200, paused.text
                assert paused.json()["message"]["role"] == "handoff"
                assert paused.json()["handoff_active"] is True

                assistant_message = await add_message(conversation_id, "assistant", "已核实的资料")
                rated = await client.put(f"/api/v1/support/messages/{assistant_message.id}/feedback", headers=player,
                                         json={"rating": "positive"})
                assert rated.status_code == 200, rated.text
                assert rated.json()["rating"] == "positive"
                feedback = await client.get("/api/v1/support/feedback", headers=player)
                assert any(row["message_id"] == assistant_message.id for row in feedback.json())

                resolved = await client.patch(f"/api/v1/support/admin/tickets/{ticket_id}",
                                              json={"status": "resolved", "reply": "Investigation complete."})
                assert resolved.status_code == 200, resolved.text
                async def fake_answer(question: str, language: str, history, conversation_id: str):
                    return {"answer": "可以继续提问。", "sources": [], "needs_ticket": False}
                monkeypatch.setattr(support_api, "answer_player", fake_answer)
                resumed = await client.post("/api/v1/support/messages", headers=player,
                                            json={"content": "你好", "language": "zh"})
                assert resumed.status_code == 200, resumed.text
                assert resumed.json()["message"]["role"] == "assistant"
                assert resumed.json()["handoff_active"] is False
                rebuilt = await client.post(f"/api/v1/support/admin/documents/{document_id}/reindex")
                assert rebuilt.status_code == 200, rebuilt.text
                assert rebuilt.json()["status"] == "draft"
                assert await support_documents.search_documents("achievement") == []
            finally:
                if document_id is not None:
                    removed = await client.delete(f"/api/v1/support/admin/documents/{document_id}")
                    assert removed.status_code == 204, removed.text
                erased = await client.delete("/api/v1/support/conversations/current", headers=player)
                assert erased.status_code == 204, erased.text
        async with session_factory() as db:
            staff = await db.get(User, staff_id)
            if staff is not None:
                await db.delete(staff)
                await db.commit()

    try:
        asyncio.run(exercise(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    finally:
        app.dependency_overrides.clear()
