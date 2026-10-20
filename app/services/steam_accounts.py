"""Persist only SteamIDs proven for the signed-in local account."""

from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from app.models.support import SupportConversation, SupportSteamAccount, now_utc
from app.services.support_store import session_factory


async def link_steam_account(user_id: int, steam_id: str) -> SupportSteamAccount:
    """Bind a Steam identity to one local user, refusing cross-account reuse."""
    async with session_factory() as db:
        existing = await db.exec(select(SupportSteamAccount).where(SupportSteamAccount.steam_id == steam_id))
        owner = existing.first()
        if owner is not None and owner.user_id != user_id:
            raise ValueError("Steam account is already linked to another player")
        current = await db.get(SupportSteamAccount, user_id)
        if current is None:
            current = SupportSteamAccount(user_id=user_id, steam_id=steam_id)
        else:
            current.steam_id = steam_id
            current.verified_at = now_utc()
        db.add(current)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ValueError("Steam account is already linked to another player") from exc
        await db.refresh(current)
        return current


async def get_linked_steam_id(conversation_id: str) -> str | None:
    """Resolve Steam identity through the server-owned conversation."""
    async with session_factory() as db:
        result = await db.exec(
            select(SupportSteamAccount.steam_id)
            .join(SupportConversation, col(SupportConversation.user_id) == col(SupportSteamAccount.user_id))
            .where(SupportConversation.id == conversation_id)
        )
        return result.first()


async def unlink_steam_account(user_id: int) -> None:
    """Remove the local Steam identity binding."""
    async with session_factory() as db:
        account = await db.get(SupportSteamAccount, user_id)
        if account is not None:
            await db.delete(account)
            await db.commit()
