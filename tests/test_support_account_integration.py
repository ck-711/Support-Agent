"""Opt-in account isolation and conversation paging checks."""

import asyncio
import os
import selectors
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.models.support import SupportConversation, SupportMessage
from app.models.user import User
from app.services.support_store import delete_conversation, hash_token, session_factory
from app.utils.auth import create_access_token


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("SUPPORT_INTEGRATION") != "1", reason="requires migrated local PostgreSQL")
def test_player_accounts_are_isolated_and_history_is_pageable() -> None:
    """Only the owning JWT can read a conversation, including older pages."""

    async def exercise() -> None:
        user_ids: list[int] = []
        conversation_ids: list[str] = []
        async with session_factory() as db:
            for _ in range(3):
                user = User(email=f"support-owner-{uuid4()}@example.com", hashed_password="unused")
                db.add(user)
                await db.flush()
                user_ids.append(user.id)
            await db.commit()
        try:
            headers = [
                {"Authorization": f"Bearer {create_access_token(str(user_id)).access_token}"}
                for user_id in user_ids
            ]
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                for owner in headers[:2]:
                    created = await client.post(
                        "/api/v1/support/conversations", headers=owner, json={"language": "zh"}
                    )
                    assert created.status_code == 201, created.text
                    conversation_ids.append(created.json()["conversation_id"])
                resumed = await client.post(
                    "/api/v1/support/conversations", headers=headers[0], json={"language": "en"}
                )
                assert resumed.json()["conversation_id"] == conversation_ids[0]
                assert conversation_ids[0] != conversation_ids[1]

                timestamp = datetime.now(UTC)
                async with session_factory() as db:
                    db.add_all([
                        SupportMessage(
                            conversation_id=conversation_ids[0], role="player", content=f"Private message {n}",
                            created_at=timestamp,
                        )
                        for n in range(103)
                    ])
                    await db.commit()

                first = await client.get("/api/v1/support/messages", headers=headers[0])
                assert first.status_code == 200, first.text
                assert len(first.json()) == 100
                older = await client.get(
                    "/api/v1/support/messages", headers=headers[0],
                    params={"before": first.json()[0]["id"]},
                )
                assert older.status_code == 200, older.text
                assert len(older.json()) == 3
                assert {item["id"] for item in first.json()}.isdisjoint(
                    {item["id"] for item in older.json()}
                )
                newer = await client.get(
                    "/api/v1/support/messages", headers=headers[0],
                    params={"after": older.json()[-1]["id"]},
                )
                assert newer.status_code == 200 and len(newer.json()) == 100
                assert newer.json() == first.json()
                invalid_direction = await client.get(
                    "/api/v1/support/messages", headers=headers[0],
                    params={"before": first.json()[0]["id"], "after": first.json()[-1]["id"]},
                )
                assert invalid_direction.status_code == 400

                other = await client.get("/api/v1/support/messages", headers=headers[1])
                assert other.status_code == 200 and other.json() == []
                foreign_cursor = await client.get(
                    "/api/v1/support/messages", headers=headers[1],
                    params={"before": first.json()[0]["id"]},
                )
                assert foreign_cursor.status_code == 404
                no_login = await client.get("/api/v1/support/messages")
                assert no_login.status_code in (401, 403)
                old_bearer_only = await client.get(
                    "/api/v1/support/messages", headers={"X-Support-Token": "legacy-token"}
                )
                assert old_bearer_only.status_code in (401, 403)

                legacy_token = "legacy-" + "x" * 48
                legacy = SupportConversation(token_hash=hash_token(legacy_token), language="zh")
                async with session_factory() as db:
                    db.add(legacy)
                    await db.commit()
                conversation_ids.append(legacy.id)
                claimed = await client.post(
                    "/api/v1/support/conversations/claim", headers=headers[2], json={"token": legacy_token}
                )
                assert claimed.status_code == 200, claimed.text
                assert claimed.json()["conversation_id"] == legacy.id
                replay = await client.post(
                    "/api/v1/support/conversations/claim", headers=headers[0], json={"token": legacy_token}
                )
                assert replay.status_code in (404, 409)
                owned = await client.post(
                    "/api/v1/support/conversations", headers=headers[2], json={"language": "en"}
                )
                assert owned.json()["conversation_id"] == legacy.id
        finally:
            for conversation_id in conversation_ids:
                await delete_conversation(conversation_id)
            async with session_factory() as db:
                for user_id in user_ids:
                    user = await db.get(User, user_id)
                    if user is not None:
                        await db.delete(user)
                await db.commit()

    asyncio.run(exercise(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


@pytest.mark.slow
@pytest.mark.skipif(os.getenv("SUPPORT_INTEGRATION") != "1", reason="requires migrated local PostgreSQL")
def test_player_registration_and_login_resume_same_history() -> None:
    """The page's existing auth endpoints issue JWTs for the same support owner."""

    async def exercise() -> None:
        user_id: int | None = None
        conversation_id: str | None = None
        email = f"support-login-{uuid4()}@example.com"
        password = "TestOnly!Password123"
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                registered = await client.post(
                    "/api/v1/auth/register", json={"email": email, "password": password, "username": "Test player"}
                )
                assert registered.status_code == 200, registered.text
                user_id = registered.json()["id"]
                first_token = registered.json()["token"]["access_token"]
                first = await client.post(
                    "/api/v1/support/conversations", headers={"Authorization": f"Bearer {first_token}"},
                    json={"language": "zh"},
                )
                assert first.status_code == 201, first.text
                conversation_id = first.json()["conversation_id"]
                logged_in = await client.post(
                    "/api/v1/auth/login", data={"email": email, "password": password, "grant_type": "password"}
                )
                assert logged_in.status_code == 200, logged_in.text
                second_token = logged_in.json()["access_token"]
                resumed = await client.post(
                    "/api/v1/support/conversations", headers={"Authorization": f"Bearer {second_token}"},
                    json={"language": "en"},
                )
                assert resumed.status_code == 201, resumed.text
                assert resumed.json()["conversation_id"] == conversation_id
        finally:
            if conversation_id is not None:
                await delete_conversation(conversation_id)
            if user_id is not None:
                async with session_factory() as db:
                    user = await db.get(User, user_id)
                    if user is not None:
                        await db.delete(user)
                        await db.commit()

    asyncio.run(exercise(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
