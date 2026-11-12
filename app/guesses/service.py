"""Submitting guesses and building the collective ranking (PRD RF-2, RF-3)."""

import enum
import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_date
from app.core.config import get_settings
from app.games.models import Game, GameStatus
from app.games.ranking import normalize_word, rank_of
from app.games.service import finish_game
from app.guesses.models import Guess


class Outcome(enum.StrEnum):
    ACCEPTED = "accepted"
    WON = "won"
    UNKNOWN_WORD = "unknown_word"
    REPEATED_WORD = "repeated_word"
    ALREADY_GUESSED_TODAY = "already_guessed_today"
    GAME_LOCKED = "game_locked"
    GAME_FINISHED = "game_finished"
    EMPTY = "empty"


# Outcomes that do not use up the player's guess of the day.
FREE_OUTCOMES = {Outcome.UNKNOWN_WORD, Outcome.REPEATED_WORD, Outcome.EMPTY}


@dataclass(frozen=True)
class GuessResult:
    outcome: Outcome
    word: str = ""
    rank: int | None = None


async def todays_guess(
    session: AsyncSession, game_id: int, user_id: uuid.UUID, today: date
) -> Guess | None:
    return await session.scalar(
        select(Guess).where(
            Guess.game_id == game_id, Guess.user_id == user_id, Guess.guess_date == today
        )
    )


async def submit_guess(
    session: AsyncSession, game: Game, user_id: uuid.UUID, raw_word: str, now: datetime
) -> GuessResult:
    await session.refresh(game)
    if game.status == GameStatus.LOCKED:
        return GuessResult(Outcome.GAME_LOCKED)
    if game.status != GameStatus.OPEN:
        return GuessResult(Outcome.GAME_FINISHED)

    word = normalize_word(raw_word)
    if not word:
        return GuessResult(Outcome.EMPTY)

    today = local_date(now)
    if await todays_guess(session, game.id, user_id, today) is not None:
        return GuessResult(Outcome.ALREADY_GUESSED_TODAY, word)

    already_tried = await session.scalar(
        select(Guess.id).where(
            Guess.game_id == game.id, Guess.user_id == user_id, Guess.word == word
        )
    )
    if already_tried is not None:
        return GuessResult(Outcome.REPEATED_WORD, word)

    rank = await rank_of(session, game.id, word)
    if rank is None:
        return GuessResult(Outcome.UNKNOWN_WORD, word)

    # The guess and (when correct) the finish of the game share one transaction.
    session.add(Guess(user_id=user_id, game_id=game.id, word=word, rank=rank, guess_date=today))
    try:
        await session.flush()
    except IntegrityError:
        # A concurrent request from the same user got in first.
        await session.rollback()
        return GuessResult(Outcome.ALREADY_GUESSED_TODAY, word)

    # The conditional UPDATE decides the race: only the first correct guess wins.
    if rank == 1 and not await finish_game(session, game.id, now, winner_id=user_id, commit=False):
        await session.rollback()
        return GuessResult(Outcome.GAME_FINISHED, word)

    await session.commit()

    if rank == 1:
        await session.refresh(game)  # finish_game updated the row directly
        return GuessResult(Outcome.WON, word, rank)
    return GuessResult(Outcome.ACCEPTED, word, rank)


def time_until_next_day(now: datetime) -> timedelta:
    tz = get_settings().tz
    local_now = now.astimezone(tz)
    next_midnight = datetime.combine(local_now.date() + timedelta(days=1), time(), tzinfo=tz)
    return next_midnight - local_now


@dataclass(frozen=True)
class RankingRow:
    word: str
    rank: int
    players: int
    mine: bool

    def proximity(self, total: int) -> float:
        """0..1, where 1 is the secret word. Log scale, like the colours."""
        if self.rank <= 1 or total <= 1:
            return 1.0
        return max(0.0, 1 - math.log(self.rank) / math.log(total))


async def ranking(session: AsyncSession, game: Game, user_id: uuid.UUID | None) -> list[RankingRow]:
    """Distinct guessed words of the game, closest first.

    While the game is not finished the secret word never appears (it could
    only be there if someone had won, which finishes the game anyway).
    """
    mine = func.bool_or(Guess.user_id == user_id) if user_id else func.bool_or(False)
    query = (
        select(Guess.word, Guess.rank, func.count().label("players"), mine.label("mine"))
        .where(Guess.game_id == game.id)
        .group_by(Guess.word, Guess.rank)
        .order_by(Guess.rank)
    )
    if game.status != GameStatus.FINISHED:
        query = query.where(Guess.rank > 1)
    rows = await session.execute(query)
    return [RankingRow(r.word, r.rank, r.players, bool(r.mine)) for r in rows]
