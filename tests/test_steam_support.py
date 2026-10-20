"""Steam support tools must use verified, server-scoped identity."""

import asyncio

import httpx
import pytest

from app.services import game_support_agent, steam_api, support_tools


def test_steam_news_uses_fixed_app_and_no_key(monkeypatch) -> None:
    """Public calls never put publisher credentials in the request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"appnews": {"newsitems": [
            {"title": "Update", "url": "https://store.steampowered.com/news/app/3326810", "date": 42,
             "contents": "Patch notes"},
        ]}})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(steam_api.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    result = asyncio.run(steam_api.get_app_news())
    assert result[0]["title"] == "Update"
    assert seen[0].url.params["appid"] == str(steam_api.settings.STEAM_APP_ID)
    assert "key" not in seen[0].url.params
    assert "x-webapi-key" not in seen[0].headers


def test_ticket_verification_rejects_invalid_and_uses_header(monkeypatch) -> None:
    """Only Steam's OK response can bind a Steam identity."""
    monkeypatch.setattr(steam_api.settings, "STEAM_PUBLISHER_KEY", "test-secret")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"response": {"params": {
            "result": "OK", "steamid": "76561198000000001",
        }}})

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(steam_api.httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs))
    assert asyncio.run(steam_api.verify_auth_ticket("aabb")) == "76561198000000001"
    assert seen[0].headers["x-webapi-key"] == "test-secret"
    assert "test-secret" not in str(seen[0].url)
    assert seen[0].url.params["identity"] == steam_api.settings.STEAM_TICKET_IDENTITY
    with pytest.raises(ValueError):
        asyncio.run(steam_api.verify_auth_ticket("not-hex"))
    with pytest.raises(ValueError, match="rejected"):
        original_get = steam_api._get

        async def rejected(path: str, params: dict[str, str | int], *, publisher: bool = False) -> dict:
            return {"response": {"params": {"result": "InvalidTicket", "steamid": "76561198000000001"}}}

        monkeypatch.setattr(steam_api, "_get", rejected)
        try:
            asyncio.run(steam_api.verify_auth_ticket("aabb"))
        finally:
            monkeypatch.setattr(steam_api, "_get", original_get)


def test_player_steam_tools_ignore_supplied_steam_id(monkeypatch) -> None:
    """Tool arguments cannot choose an arbitrary player's Steam account."""
    seen: list[str] = []

    async def linked(conversation_id: str) -> str:
        assert conversation_id == "trusted-conversation"
        return "76561198000000001"

    async def ownership(steam_id: str) -> bool:
        seen.append(steam_id)
        return True

    monkeypatch.setattr(support_tools, "get_linked_steam_id", linked)
    monkeypatch.setattr(support_tools, "check_ownership", ownership)
    result = asyncio.run(support_tools.execute_support_tool(
        "check_my_steam_ownership", {"steam_id": "76561198000000099"}, "trusted-conversation",
    ))
    assert result == {"owns_game": True}
    assert seen == ["76561198000000001"]


def test_unlinked_player_cannot_query_private_steam_data(monkeypatch) -> None:
    """A local account without a verified Steam link has no private Steam access."""
    async def missing(conversation_id: str) -> None:
        return None

    monkeypatch.setattr(support_tools, "get_linked_steam_id", missing)
    with pytest.raises(ValueError, match="Link a Steam account"):
        asyncio.run(support_tools.execute_support_tool("get_my_steam_achievements", {}, "player-1"))


def test_steam_answer_is_visible_without_rag_passages() -> None:
    """Verified API facts must not be replaced by the knowledge fallback."""
    result = asyncio.run(game_support_agent._answer({
        "language": "zh", "tool_steam": {"name": "check_my_steam_ownership", "result": {"owns_game": True}},
    }))
    assert "拥有 DOTS Survivors" in result.get("answer", "")
    assert result.get("tool_answered") is True


def test_achievement_summary_uses_full_total() -> None:
    """The summary must not count only the first displayed achievements."""
    result = game_support_agent._steam_answer({
        "name": "get_my_steam_achievements",
        "result": {"total": 40, "unlocked_total": 35, "achievements": [
            {"name": "one", "title": "One", "unlocked": True},
        ]},
    }, "zh")
    assert "35/40" in result.get("answer", "")
