"""Steam account linking via a ticket issued by the DOTS Survivors game client."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1.auth import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.services.steam_accounts import link_steam_account, unlink_steam_account
from app.services.steam_api import SteamUnavailable, verify_auth_ticket

router = APIRouter()


class SteamLinkRequest(BaseModel):
    """Hex ticket from SteamUser.GetAuthTicketForWebApi."""

    ticket: str = Field(min_length=2, max_length=4096)


class SteamLinkResult(BaseModel):
    """Verified local to Steam account binding."""

    steam_id: str


@router.post("/link", response_model=SteamLinkResult)
@limiter.limit("5/minute")
async def link_steam(request: Request, data: SteamLinkRequest, user: User = Depends(get_current_user)) -> SteamLinkResult:
    """Verify an app-specific Steamworks ticket and link its SteamID to this user."""
    try:
        steam_id = await verify_auth_ticket(data.ticket)
        account = await link_steam_account(user.id, steam_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except (SteamUnavailable, httpx.TransportError) as exc:
        raise HTTPException(status_code=503, detail="Steam verification is temporarily unavailable") from exc
    return SteamLinkResult(steam_id=account.steam_id)


@router.delete("/link", status_code=204)
@limiter.limit("10/minute")
async def unlink_steam(request: Request, user: User = Depends(get_current_user)) -> None:
    """Let a player revoke the stored SteamID association."""
    await unlink_steam_account(user.id)
