"""Small, server-side Steamworks Web API client for this game's support flow."""

import logging
from typing import Any
from urllib.parse import urlparse

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.logging import logger

PUBLIC_API = "https://api.steampowered.com"
PUBLISHER_API = "https://partner.steam-api.com"

# Steam requires auth tickets in a GET query string; HTTPX's INFO request log
# would otherwise write the full ticket URL to the application log.
logging.getLogger("httpx").setLevel(logging.WARNING)


class SteamUnavailable(Exception):
    """Steam could not return usable data at this time."""


def _publisher_key() -> str:
    key = settings.STEAM_PUBLISHER_KEY.strip()
    if not key:
        raise SteamUnavailable("Steamworks publisher key is not configured")
    return key


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)
async def _get(path: str, params: dict[str, str | int], *, publisher: bool = False) -> dict[str, Any]:
    """Call one fixed Steam path over HTTPS, keeping keys out of URLs and logs."""
    headers = {"x-webapi-key": _publisher_key()} if publisher else {}
    base = PUBLISHER_API if publisher else PUBLIC_API
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.get(f"{base}{path}", params=params, headers=headers)
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPStatusError, ValueError) as exc:
        logger.exception("steam_api_invalid_response", endpoint=path)
        raise SteamUnavailable("Steam returned an invalid response") from exc
    if not isinstance(payload, dict):
        raise SteamUnavailable("Steam returned an invalid response")
    return payload


async def get_app_news() -> list[dict[str, str | int]]:
    """Read recent public announcements for the configured game."""
    data = await _get("/ISteamNews/GetNewsForApp/v2/", {"appid": settings.STEAM_APP_ID, "count": 5, "maxlength": 500})
    appnews = data.get("appnews", {})
    items = appnews.get("newsitems", []) if isinstance(appnews, dict) else []
    if not isinstance(items, list):
        raise SteamUnavailable("Steam returned an invalid news list")
    return [
        {"title": str(item.get("title", ""))[:200], "url": str(item.get("url", ""))[:500],
         "published_at": int(item.get("date", 0)), "summary": str(item.get("contents", ""))[:500]}
        for item in items[:5] if isinstance(item, dict)
        and urlparse(str(item.get("url", ""))).scheme == "https"
        and urlparse(str(item.get("url", ""))).hostname in {
            "store.steampowered.com", "steamcommunity.com", "steamdeck.com",
        }
    ]


async def verify_auth_ticket(ticket: str) -> str:
    """Exchange a game-client Web API ticket for the proven SteamID64."""
    if not ticket or len(ticket) > 4096 or len(ticket) % 2 or any(c not in "0123456789abcdefABCDEF" for c in ticket):
        raise ValueError("Invalid Steam authentication ticket")
    data = await _get(
        "/ISteamUserAuth/AuthenticateUserTicket/v1/",
        {"appid": settings.STEAM_APP_ID, "ticket": ticket, "identity": settings.STEAM_TICKET_IDENTITY},
        publisher=True,
    )
    envelope = data.get("response", {})
    params = envelope.get("params", {}) if isinstance(envelope, dict) else {}
    if not isinstance(params, dict) or params.get("result") != "OK":
        raise ValueError("Steam authentication ticket was rejected")
    steam_id = str(params.get("steamid", ""))
    if not steam_id.isdecimal() or not 17 <= len(steam_id) <= 20:
        raise SteamUnavailable("Steam returned an invalid identity")
    return steam_id


async def check_ownership(steam_id: str) -> bool:
    """Check the linked player's current valid game licence."""
    data = await _get(
        "/ISteamUser/CheckAppOwnership/v4/",
        {"steamid": steam_id, "appid": settings.STEAM_APP_ID}, publisher=True,
    )
    result = data.get("appownership", {})
    if not isinstance(result, dict) or not isinstance(result.get("ownsapp"), bool):
        raise SteamUnavailable("Steam returned an invalid ownership result")
    return result["ownsapp"]


async def get_player_achievements(steam_id: str) -> dict[str, Any]:
    """Read achievements for the linked player and this game only."""
    data = await _get(
        "/ISteamUserStats/GetPlayerAchievements/v1/",
        {"steamid": steam_id, "appid": settings.STEAM_APP_ID, "l": "english"}, publisher=True,
    )
    stats = data.get("playerstats", {})
    if not isinstance(stats, dict) or stats.get("success") is not True:
        raise SteamUnavailable("Steam achievements are unavailable for this player")
    items = stats.get("achievements", [])
    if not isinstance(items, list):
        raise SteamUnavailable("Steam returned an invalid achievement list")
    return {
        "game": str(stats.get("gameName", ""))[:120],
        "achievements": [
            {"name": str(item.get("apiname", ""))[:120], "title": str(item.get("name", ""))[:200],
             "unlocked": item.get("achieved") == 1, "unlock_time": int(item.get("unlocktime", 0))}
            for item in items[:200] if isinstance(item, dict)
        ],
    }
