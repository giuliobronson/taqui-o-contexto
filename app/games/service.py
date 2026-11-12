"""Game lifecycle: scheduled → locked → open → finished (PRD RF-7)."""

import uuid
from datetime import datetime

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.games.models import ACTIVE_STATUSES, Game, GameMember, GameStatus, MemberRole

# Arbitrary constant used with pg_advisory_xact_lock so that concurrent requests
# never run the lazy transitions at the same time.
_TRANSITION_LOCK_KEY = 7_041_001


class GameStateError(Exception):
    """The requested transition is not valid for the game's current state."""


async def current_game(session: AsyncSession, now: datetime) -> Game | None:
    """Return the active (locked or open) game, applying any pending transitions.

    Transitions are lazy: they happen when someone asks for the current game.
    - an active game whose ends_at has passed is finished without a winner;
    - with no active game, the earliest scheduled game whose starts_at has
      arrived becomes locked (games whose whole period already passed while
      nobody was looking are finished straight away).
    """
    if not await _needs_transition(session, now):
        return await _active_game(session)

    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _TRANSITION_LOCK_KEY})

    game = await _active_game(session)
    if game is not None and now >= game.ends_at:
        _expire(game)
        game = None

    while game is None:
        candidate = await session.scalar(
            select(Game)
            .where(Game.status == GameStatus.SCHEDULED, Game.starts_at <= now)
            .order_by(Game.starts_at)
            .limit(1)
            .with_for_update()
        )
        if candidate is None:
            break
        if now >= candidate.ends_at:
            _expire(candidate)
            continue
        candidate.status = GameStatus.LOCKED
        game = candidate

    await session.commit()
    return game


async def unlock_game(
    session: AsyncSession, game_id: int, admin_id: uuid.UUID, now: datetime
) -> None:
    """Open a locked game for guesses and hints. Cannot be undone."""
    result = await session.execute(
        update(Game)
        .where(Game.id == game_id, Game.status == GameStatus.LOCKED)
        .values(status=GameStatus.OPEN, unlocked_at=now, unlocked_by=admin_id)
        .returning(Game.id)
    )
    if result.scalar_one_or_none() is None:
        raise GameStateError("Only a locked game can be unlocked.")
    await session.commit()


async def finish_game(
    session: AsyncSession,
    game_id: int,
    now: datetime,
    *,
    winner_id: uuid.UUID | None = None,
) -> bool:
    """Finish a game atomically. Returns False if it was no longer finishable.

    With a winner the game must be open: the conditional UPDATE guarantees that
    when two correct guesses race, only the first one becomes the winner.
    Without a winner (admin action) a locked or open game can be finished.
    """
    allowed = (GameStatus.OPEN,) if winner_id is not None else ACTIVE_STATUSES
    result = await session.execute(
        update(Game)
        .where(Game.id == game_id, Game.status.in_(allowed))
        .values(status=GameStatus.FINISHED, winner_id=winner_id, finished_at=now)
        .returning(Game.id)
        .execution_options(synchronize_session=False)
    )
    finished = result.scalar_one_or_none() is not None
    await session.commit()
    return finished


async def join_active_game(
    session: AsyncSession, profile: Profile, now: datetime
) -> GameMember | None:
    """Associate a user with the active game as a player, if not associated yet.

    Admins are never associated (they know the secret word). Returns the
    membership, or None when there is no active game or the user is an admin.
    """
    if profile.is_admin:
        return None
    game = await current_game(session, now)
    if game is None:
        return None
    await session.execute(
        insert(GameMember)
        .values(game_id=game.id, user_id=profile.id, role=MemberRole.PLAYER)
        .on_conflict_do_nothing(index_elements=["game_id", "user_id"])
    )
    await session.commit()
    return await session.get(GameMember, (game.id, profile.id))


async def last_finished_game(session: AsyncSession) -> Game | None:
    return await session.scalar(
        select(Game)
        .where(Game.status == GameStatus.FINISHED)
        .order_by(Game.finished_at.desc())
        .limit(1)
    )


async def next_scheduled_game(session: AsyncSession) -> Game | None:
    return await session.scalar(
        select(Game).where(Game.status == GameStatus.SCHEDULED).order_by(Game.starts_at).limit(1)
    )


async def _active_game(session: AsyncSession) -> Game | None:
    return await session.scalar(select(Game).where(Game.status.in_(ACTIVE_STATUSES)))


async def _needs_transition(session: AsyncSession, now: datetime) -> bool:
    expired_active = select(Game.id).where(Game.status.in_(ACTIVE_STATUSES), Game.ends_at <= now)
    due_scheduled = select(Game.id).where(
        Game.status == GameStatus.SCHEDULED, Game.starts_at <= now
    )
    active_exists = select(Game.id).where(Game.status.in_(ACTIVE_STATUSES))
    return bool(
        await session.scalar(
            select(expired_active.exists() | (due_scheduled.exists() & ~active_exists.exists()))
        )
    )


def _expire(game: Game) -> None:
    """Finish a game whose period ended without a winner."""
    game.status = GameStatus.FINISHED
    game.finished_at = game.ends_at
