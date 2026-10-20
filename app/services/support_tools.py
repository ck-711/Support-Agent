"""Allowlisted, player-scoped business tools for support agents and API clients."""

from typing import Any

from app.core.config import settings
from app.services.steam_accounts import get_linked_steam_id
from app.services.steam_api import check_ownership, get_app_news, get_player_achievements
from app.services.support_documents_v2 import search_documents, search_support_vectors
from app.services.support_store import create_handoff_ticket, list_tickets

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function", "function": {
            "name": "get_steam_news", "description": "Read current official Steam announcements for DOTS Survivors.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function", "function": {
            "name": "check_my_steam_ownership", "description": "Check whether the linked Steam account currently owns DOTS Survivors. Requires verified linking.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function", "function": {
            "name": "get_my_steam_achievements", "description": "Read this linked player's DOTS Survivors Steam achievements. Requires verified linking.",
            "parameters": {"type": "object", "properties": {
                "achievement_name": {"type": "string", "description": "Optional achievement name or API name to filter."},
            }},
        },
    },
    {
        "type": "function", "function": {
            "name": "search_knowledge", "description": "Search approved DOTS Survivors support knowledge.",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
        },
    },
    {
        "type": "function", "function": {
            "name": "list_my_tickets", "description": "Read this player's recent support ticket statuses.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function", "function": {
            "name": "request_human_handoff", "description": "Transfer an unresolved, complex, or high-risk issue to staff.",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}, "reason": {"type": "string", "enum": ["unresolved", "complex", "high_risk"]}},
                "required": ["summary", "reason"],
            },
        },
    },
]


async def execute_support_tool(
    name: str, arguments: dict[str, Any], conversation_id: str,
) -> dict[str, Any]:
    """Execute one tool with conversation scope injected by the server."""
    if name == "get_steam_news":
        return {"appid": settings.STEAM_APP_ID, "news": await get_app_news()}
    if name in {"check_my_steam_ownership", "get_my_steam_achievements"}:
        steam_id = await get_linked_steam_id(conversation_id)
        if steam_id is None:
            raise ValueError("Link a Steam account with a game authentication ticket first")
        if name == "check_my_steam_ownership":
            return {"owns_game": await check_ownership(steam_id)}
        result = await get_player_achievements(steam_id)
        query = str(arguments.get("achievement_name", "")).strip().casefold()[:120]
        achievements = result["achievements"]
        if query:
            achievements = [item for item in achievements if query in item["name"].casefold() or query in item["title"].casefold()]
        return {"game": result["game"], "total": len(achievements),
                "unlocked_total": sum(1 for item in achievements if item["unlocked"]),
                "achievements": achievements[:30]}
    if name == "search_knowledge":
        query = str(arguments.get("query", "")).strip()[:500]
        if not query:
            raise ValueError("query is required")
        passages = await search_support_vectors(query, limit=5)
        if not passages:
            passages = await search_documents(query, limit=5)
        return {
            "passages": [{"title": p.title, "content": p.content, "url": p.url} for p in passages],
            "articles": [],
        }
    if name == "list_my_tickets":
        tickets = await list_tickets(conversation_id)
        return {"tickets": [
            {"id": ticket.id, "status": ticket.status, "summary": ticket.summary[:200]}
            for ticket in tickets[:10]
        ]}
    if name == "request_human_handoff":
        reason = str(arguments.get("reason", "unresolved"))
        if reason not in {"unresolved", "complex", "high_risk"}:
            raise ValueError("Invalid handoff reason")
        summary = str(arguments.get("summary", "")).strip()[:4000]
        if not summary:
            raise ValueError("summary is required")
        ticket = await create_handoff_ticket(conversation_id, summary, reason)
        return {"ticket_id": ticket.id, "status": ticket.status, "handoff_reason": reason}
    raise ValueError("Unknown support tool")
