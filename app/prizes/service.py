"""The winner's coupon (PRD RF-5). Codes are registered by the admin per game."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.models import Game, GameStatus
from app.prizes.models import Prize


@dataclass(frozen=True)
class PrizeView:
    game_id: int
    secret_word: str
    code: str
    description: str | None
    claimed_at: datetime


async def award_prize(session: AsyncSession, game_id: int) -> Prize | None:
    """Hand the game's coupon to its winner. Idempotent: one prize per game.

    Call it only after finish_game() confirmed the win (same transaction is fine).
    Returns None while the game has no winner or the admin has not registered a
    code yet; registering the code later calls this again.
    """
    game = await session.scalar(
        select(Game).where(Game.id == game_id).execution_options(populate_existing=True)
    )
    if game is None or game.status != GameStatus.FINISHED or game.winner_id is None:
        return None
    if not game.prize_code:
        return None
    await session.execute(
        insert(Prize)
        .values(game_id=game.id, user_id=game.winner_id, code=game.prize_code)
        .on_conflict_do_nothing(index_elements=[Prize.game_id])
    )
    await session.flush()
    return await session.scalar(select(Prize).where(Prize.game_id == game.id))


async def prizes_of(session: AsyncSession, user_id: uuid.UUID) -> list[PrizeView]:
    """The user's coupons, newest first."""
    rows = await session.execute(
        select(Prize, Game)
        .join(Game, Game.id == Prize.game_id)
        .where(Prize.user_id == user_id)
        .order_by(Prize.claimed_at.desc(), Prize.id.desc())
    )
    return [
        PrizeView(
            game_id=game.id,
            secret_word=game.secret_word,
            code=prize.code,
            description=game.prize_description,
            claimed_at=prize.claimed_at,
        )
        for prize, game in rows
    ]


async def prize_for_game(
    session: AsyncSession, game_id: int, user_id: uuid.UUID
) -> PrizeView | None:
    return next((p for p in await prizes_of(session, user_id) if p.game_id == game_id), None)
