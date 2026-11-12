"""Database-level guarantees from the PRD (section 6)."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.models import GameMember, GameStatus, MemberRole
from app.guesses.models import Guess


async def _flush_fails(session: AsyncSession) -> None:
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.flush()


async def test_only_one_active_game(session, make_game) -> None:
    base = datetime(2030, 1, 1, tzinfo=UTC)
    await make_game(status=GameStatus.OPEN, starts_at=base, ends_at=base + timedelta(days=5))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await make_game(
                status=GameStatus.LOCKED,
                starts_at=base + timedelta(days=10),
                ends_at=base + timedelta(days=12),
            )


async def test_game_periods_cannot_overlap(session, make_game) -> None:
    base = datetime(2030, 1, 1, tzinfo=UTC)
    await make_game(starts_at=base, ends_at=base + timedelta(days=5))
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await make_game(starts_at=base + timedelta(days=4), ends_at=base + timedelta(days=8))


async def test_finished_game_frees_rest_of_period(session, make_game) -> None:
    base = datetime.now(UTC) - timedelta(days=2)
    game = await make_game(status=GameStatus.FINISHED, starts_at=base)  # ends_at = base + 7d
    assert game.finished_at is not None
    # Starts after the early finish but before the original ends_at: allowed.
    await make_game(starts_at=game.finished_at + timedelta(hours=1))


async def test_one_role_per_game(session, make_game, make_profile) -> None:
    game = await make_game()
    user = await make_profile()
    session.add(GameMember(game_id=game.id, user_id=user.id, role=MemberRole.PLAYER))
    await session.flush()
    session.add(GameMember(game_id=game.id, user_id=user.id, role=MemberRole.WATCHER))
    await _flush_fails(session)


async def test_one_guess_per_day(session, make_game, make_profile) -> None:
    game = await make_game(status=GameStatus.OPEN)
    user = await make_profile()
    today = date(2030, 1, 1)
    session.add(Guess(user_id=user.id, game_id=game.id, word="chá", rank=3, guess_date=today))
    await session.flush()
    session.add(Guess(user_id=user.id, game_id=game.id, word="leite", rank=9, guess_date=today))
    await _flush_fails(session)


async def test_no_repeated_word(session, make_game, make_profile) -> None:
    game = await make_game(status=GameStatus.OPEN)
    user = await make_profile()
    session.add(
        Guess(user_id=user.id, game_id=game.id, word="chá", rank=3, guess_date=date(2030, 1, 1))
    )
    await session.flush()
    session.add(
        Guess(user_id=user.id, game_id=game.id, word="chá", rank=3, guess_date=date(2030, 1, 2))
    )
    await _flush_fails(session)
