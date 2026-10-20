"""Opt-in PostgreSQL and HTTP smoke test for game support."""

import asyncio
import os
import selectors
from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage
from sqlalchemy import delete
from sqlmodel import col

from app.api.v1.auth import get_current_user
from app.core.config import settings
from app.main import app
from app.models.support import SupportConversation, SupportKnowledge, now_utc
from app.models.user import User
from app.services.game_support_agent import SupportAnswer
from app.services import game_support_agent
from app.services.llm import llm_service
from app.services.support_store import purge_expired, session_factory


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("SUPPORT_INTEGRATION") != "1", reason="requires migrated local PostgreSQL")
def test_player_report_staff_reply_and_erasure(monkeypatch) -> None:
    """Check the actual HTTP, graph, database, staff reply, and deletion path."""
    async def fake_call(*args, **kwargs) -> SupportAnswer:
        return SupportAnswer(
            answer="Steam lists Achievements as a feature. The team can investigate your issue.",
            cited_ids=["00000000-0000-4000-8000-000000332683"],
            needs_ticket=True,
        )

    class FakeToolModel:
        def bind_tools(self, schemas):
            return self

        async def ainvoke(self, messages):
            return AIMessage(content="", tool_calls=[])

    staff_id: int | None = None

    async def staff_user() -> User:
        assert staff_id is not None
        return User(id=staff_id, email="staff@example.com", hashed_password="unused")

    monkeypatch.setattr(llm_service, "call", fake_call)
    monkeypatch.setattr(game_support_agent.LLMRegistry, "get", classmethod(lambda cls, name: FakeToolModel()))
    app.dependency_overrides[get_current_user] = staff_user

    async def exercise() -> None:
        nonlocal staff_id
        async with session_factory() as db:
            staff = User(email=f"support-test-{uuid4()}@example.com", hashed_password="unused")
            db.add(staff)
            await db.commit()
            await db.refresh(staff)
            staff_id = staff.id
        monkeypatch.setattr(settings, "SUPPORT_ADMIN_USER_IDS", str(staff_id))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            started = await client.post("/api/v1/support/conversations", json={"language": "en"})
            assert started.status_code == 201, started.text
            headers = {"Authorization": "Bearer test-staff-token"}
            try:
                knowledge = await client.get("/api/v1/support/knowledge")
                assert knowledge.status_code == 200
                assert any(article["title"] == "Steam features" for article in knowledge.json())

                chat = await client.post(
                    "/api/v1/support/messages", headers=headers,
                    json={"content": "My Steam achievement did not unlock", "language": "en"},
                )
                assert chat.status_code == 200, chat.text
                assert chat.json()["ticket_id"]
                assert chat.json()["message"]["sources"][0]["title"] == "Steam features"

                ticket_id = chat.json()["ticket_id"]
                claim = await client.post(f"/api/v1/support/admin/tickets/{ticket_id}/claim")
                assert claim.status_code == 200, claim.text
                reply = await client.patch(
                    f"/api/v1/support/admin/tickets/{ticket_id}",
                    json={"status": "investigating", "reply": "Please share your game version if available."},
                )
                assert reply.status_code == 200, reply.text

                messages = await client.get("/api/v1/support/messages", headers=headers)
                assert any(item["role"] == "staff" for item in messages.json())
            finally:
                removed = await client.delete("/api/v1/support/conversations/current", headers=headers)
                assert removed.status_code == 204, removed.text
            expired = await client.get("/api/v1/support/messages", headers=headers)
            assert expired.status_code == 404

            draft_id: str | None = None
            try:
                draft = await client.post("/api/v1/support/admin/knowledge", json={
                    "title": "Test article",
                    "content": "This test entry should not appear before staff approval.",
                    "source_url": "https://store.steampowered.com/app/3326810/DOTS_Survivors/",
                })
                assert draft.status_code == 201, draft.text
                draft_id = draft.json()["id"]
                public = await client.get("/api/v1/support/knowledge")
                assert all(item["id"] != draft_id for item in public.json())

                published = await client.post(f"/api/v1/support/admin/knowledge/{draft_id}/publish")
                assert published.status_code == 200
                public = await client.get("/api/v1/support/knowledge")
                assert any(item["id"] == draft_id for item in public.json())

                revised = await client.put(f"/api/v1/support/admin/knowledge/{draft_id}", json={
                    "title": "Test article",
                    "content": "A revised test entry is hidden until it is reviewed again.",
                    "source_url": "https://store.steampowered.com/app/3326810/DOTS_Survivors/",
                })
                assert revised.status_code == 200
                public = await client.get("/api/v1/support/knowledge")
                assert all(item["id"] != draft_id for item in public.json())
            finally:
                if draft_id is not None:
                    async with session_factory() as db:
                        await db.exec(delete(SupportKnowledge).where(col(SupportKnowledge.id) == draft_id))
                        await db.commit()

            old = await client.post("/api/v1/support/conversations", json={"language": "en"})
            old_id = old.json()["conversation_id"]
            async with session_factory() as db:
                old_record = await db.get(SupportConversation, old_id)
                assert old_record is not None
                old_record.expires_at = now_utc() - timedelta(seconds=1)
                db.add(old_record)
                await db.commit()
            assert await purge_expired() >= 1
            gone = await client.get("/api/v1/support/messages", headers=headers)
            assert gone.status_code == 404
        async with session_factory() as db:
            staff = await db.get(User, staff_id)
            if staff is not None:
                await db.delete(staff)
                await db.commit()

    try:
        asyncio.run(exercise(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    finally:
        app.dependency_overrides.clear()
