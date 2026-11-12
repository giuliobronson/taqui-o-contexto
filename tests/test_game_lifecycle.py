from datetime import UTC, datetime, timedelta

import pytest

from app.games import service
from app.games.models import GameStatus

T0 = datetime(2030, 3, 1, 12, tzinfo=UTC)
DAY = timedelta(days=1)


async def test_no_games_means_no_current_game(session) -> None:
    assert await service.current_game(session, T0) is None


async def test_scheduled_game_waits_for_starts_at(session, make_game) -> None:
    game = await make_game(starts_at=T0 + DAY, ends_at=T0 + 5 * DAY)
    assert await service.current_game(session, T0) is None
    assert game.status == GameStatus.SCHEDULED


async def test_scheduled_game_becomes_locked_at_starts_at(session, make_game) -> None:
    game = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    current = await service.current_game(session, T0)
    assert current is not None and current.id == game.id
    assert current.status == GameStatus.LOCKED


async def test_locked_game_is_never_opened_automatically(session, make_game) -> None:
    await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    await service.current_game(session, T0)
    current = await service.current_game(session, T0 + 3 * DAY)
    assert current is not None and current.status == GameStatus.LOCKED


async def test_admin_unlocks_game(session, make_game, make_profile) -> None:
    admin = await make_profile(is_admin=True)
    game = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    await service.current_game(session, T0)

    await service.unlock_game(session, game.id, admin.id, T0 + DAY)
    await session.refresh(game)

    assert game.status == GameStatus.OPEN
    assert game.unlocked_at == T0 + DAY
    assert game.unlocked_by == admin.id


async def test_only_locked_game_can_be_unlocked(session, make_game, make_profile) -> None:
    admin = await make_profile(is_admin=True)
    game = await make_game(starts_at=T0 + DAY, ends_at=T0 + 5 * DAY)  # still scheduled
    with pytest.raises(service.GameStateError):
        await service.unlock_game(session, game.id, admin.id, T0)


@pytest.mark.parametrize("unlock", [False, True], ids=["while-locked", "while-open"])
async def test_active_game_expires_at_ends_at(session, make_game, make_profile, unlock) -> None:
    admin = await make_profile(is_admin=True)
    game = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    await service.current_game(session, T0)
    if unlock:
        await service.unlock_game(session, game.id, admin.id, T0)

    assert await service.current_game(session, T0 + 5 * DAY) is None
    await session.refresh(game)
    assert game.status == GameStatus.FINISHED
    assert game.winner_id is None
    assert game.finished_at == game.ends_at


async def test_next_game_starts_after_previous_expires(session, make_game) -> None:
    first = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    second = await make_game(starts_at=T0 + 5 * DAY, ends_at=T0 + 9 * DAY)
    await service.current_game(session, T0)

    current = await service.current_game(session, T0 + 6 * DAY)

    await session.refresh(first)
    assert first.status == GameStatus.FINISHED
    assert current is not None and current.id == second.id
    assert current.status == GameStatus.LOCKED


async def test_game_missed_entirely_is_finished(session, make_game) -> None:
    missed = await make_game(starts_at=T0, ends_at=T0 + DAY)
    later = await make_game(starts_at=T0 + 2 * DAY, ends_at=T0 + 4 * DAY)

    current = await service.current_game(session, T0 + 3 * DAY)

    await session.refresh(missed)
    assert missed.status == GameStatus.FINISHED
    assert current is not None and current.id == later.id


async def test_winner_finishes_open_game_once(session, make_game, make_profile) -> None:
    admin = await make_profile(is_admin=True)
    first, second = await make_profile(), await make_profile()
    game = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    await service.current_game(session, T0)
    await service.unlock_game(session, game.id, admin.id, T0)

    assert await service.finish_game(session, game.id, T0 + DAY, winner_id=first.id)
    assert not await service.finish_game(session, game.id, T0 + DAY, winner_id=second.id)

    await session.refresh(game)
    assert game.status == GameStatus.FINISHED
    assert game.winner_id == first.id


async def test_winner_cannot_finish_locked_game(session, make_game, make_profile) -> None:
    player = await make_profile()
    game = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    await service.current_game(session, T0)
    assert not await service.finish_game(session, game.id, T0, winner_id=player.id)


async def test_admin_can_finish_locked_game(session, make_game) -> None:
    game = await make_game(starts_at=T0, ends_at=T0 + 5 * DAY)
    await service.current_game(session, T0)
    assert await service.finish_game(session, game.id, T0 + DAY)
    await session.refresh(game)
    assert game.status == GameStatus.FINISHED and game.winner_id is None
