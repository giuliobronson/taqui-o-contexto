"""Admin panel: listing, creating and editing games (PRD RF-6)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.core.config import get_settings
from app.games.models import ACTIVE_STATUSES, Game, GameMember, GameStatus, MemberRole
from app.games.ranking import build_ranking, resolve_word, word_in_vocabulary
from app.games.service import GameStateError, finish_game, unlock_game
from app.guesses.models import Guess
from app.prizes.models import Prize
from app.prizes.service import award_prize

MAX_PRIZE_CODE = 100
MAX_PRIZE_DESCRIPTION = 200

ALL_FIELDS = frozenset({"secret_word", "starts_at", "ends_at", "prize_code", "prize_description"})
PRIZE_FIELDS = frozenset({"prize_code", "prize_description"})


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GameSummary:
    game: Game
    players: int
    watchers: int
    guesses: int
    winner: str | None
    has_prize: bool

    @property
    def members(self) -> int:
        return self.players + self.watchers


def _count_members(role: MemberRole):
    return (
        select(func.count())
        .where(GameMember.game_id == Game.id, GameMember.role == role)
        .scalar_subquery()
    )


async def game_summaries(session: AsyncSession, game_id: int | None = None) -> list[GameSummary]:
    """Every game (or just one) with its counts, newest period first."""
    guesses = select(func.count()).where(Guess.game_id == Game.id).scalar_subquery()
    prize = select(Prize.id).where(Prize.game_id == Game.id).exists()
    query = (
        select(
            Game,
            _count_members(MemberRole.PLAYER).label("players"),
            _count_members(MemberRole.WATCHER).label("watchers"),
            guesses.label("guesses"),
            Profile.display_name.label("winner"),
            prize.label("has_prize"),
        )
        .outerjoin(Profile, Profile.id == Game.winner_id)
        .order_by(Game.starts_at.desc())
    )
    if game_id is not None:
        query = query.where(Game.id == game_id)
    rows = await session.execute(query)
    return [
        GameSummary(r.Game, r.players, r.watchers, r.guesses, r.winner, r.has_prize) for r in rows
    ]


async def game_summary(session: AsyncSession, game_id: int) -> GameSummary | None:
    found = await game_summaries(session, game_id)
    return found[0] if found else None


# ---------------------------------------------------------------------------
# The game form
# ---------------------------------------------------------------------------


def editable_fields(game: Game | None, has_prize: bool = False) -> frozenset[str]:
    """What the admin may change in each state (PRD RF-6).

    - new or scheduled: everything (changing the word recomputes the ranking);
    - active: only ends_at; the word is fixed because watchers already saw it;
    - finished: read-only, except registering the coupon of a winner who has none.
    The coupon can be changed until it has been handed over.
    """
    if game is None or game.status == GameStatus.SCHEDULED:
        return ALL_FIELDS
    if game.status in ACTIVE_STATUSES:
        return frozenset({"ends_at"}) | PRIZE_FIELDS
    if game.winner_id is not None and not has_prize:
        return PRIZE_FIELDS
    return frozenset()


@dataclass
class GameForm:
    """Raw values as typed in the form, so they can be shown back with the errors."""

    secret_word: str = ""
    starts_at: str = ""  # "YYYY-MM-DDTHH:MM" in the game's time zone (datetime-local)
    ends_at: str = ""
    prize_code: str = ""
    prize_description: str = ""
    errors: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_game(cls, game: Game) -> "GameForm":
        return cls(
            secret_word=game.secret_word,
            starts_at=to_local_input(game.starts_at),
            ends_at=to_local_input(game.ends_at),
            prize_code=game.prize_code or "",
            prize_description=game.prize_description or "",
        )


def to_local_input(moment: datetime) -> str:
    return moment.astimezone(get_settings().tz).strftime("%Y-%m-%dT%H:%M")


def parse_local_input(raw: str) -> datetime | None:
    try:
        naive = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    if naive.tzinfo is not None:
        return naive
    return naive.replace(tzinfo=get_settings().tz)


@dataclass(frozen=True)
class GameValues:
    secret_word: str
    starts_at: datetime
    ends_at: datetime
    prize_code: str | None
    prize_description: str | None


async def validate(
    session: AsyncSession,
    form: GameForm,
    now: datetime,
    model_version: str,
    game: Game | None = None,
    has_prize: bool = False,
) -> GameValues | None:
    """Check the editable fields; fill form.errors and return None when invalid.
    Fields the admin may not change keep the game's current values."""
    allowed = editable_fields(game, has_prize)
    errors = form.errors

    secret = game.secret_word if game else ""
    if "secret_word" in allowed:
        secret = await resolve_word(session, model_version, form.secret_word)
        if not secret:
            errors["secret_word"] = "Informe a palavra secreta."
        elif not await word_in_vocabulary(session, model_version, secret):
            errors["secret_word"] = f"“{secret}” não está no vocabulário."

    starts_at = game.starts_at if game else None
    if "starts_at" in allowed:
        starts_at = parse_local_input(form.starts_at)
        if starts_at is None:
            errors["starts_at"] = "Informe a data e a hora de início."

    ends_at = game.ends_at if game else None
    if "ends_at" in allowed:
        ends_at = parse_local_input(form.ends_at)
        if ends_at is None:
            errors["ends_at"] = "Informe a data e a hora de fim."
        elif starts_at is not None and ends_at <= starts_at:
            errors["ends_at"] = "O fim precisa ser depois do início."
        elif ends_at <= now:
            errors["ends_at"] = "O fim precisa estar no futuro."

    code = game.prize_code if game else None
    description = game.prize_description if game else None
    if "prize_code" in allowed:
        code = form.prize_code.strip() or None
        description = form.prize_description.strip() or None
        if code and len(code) > MAX_PRIZE_CODE:
            errors["prize_code"] = f"O código pode ter até {MAX_PRIZE_CODE} caracteres."
        if description and len(description) > MAX_PRIZE_DESCRIPTION:
            errors["prize_description"] = (
                f"A descrição pode ter até {MAX_PRIZE_DESCRIPTION} caracteres."
            )

    dates_changed = bool(allowed & {"starts_at", "ends_at"})
    if dates_changed and starts_at and ends_at and not errors.keys() & {"starts_at", "ends_at"}:
        clash = await overlapping_game(session, starts_at, ends_at, game.id if game else None)
        if clash is not None:
            errors["ends_at"] = f"O período coincide com o do jogo #{clash}."

    if errors:
        return None
    assert starts_at is not None and ends_at is not None
    return GameValues(secret, starts_at, ends_at, code, description)


async def overlapping_game(
    session: AsyncSession, starts_at: datetime, ends_at: datetime, ignore_id: int | None
) -> int | None:
    """Id of another game whose period overlaps. Mirrors the games_no_overlap constraint:
    a game that finished early frees the rest of its period."""
    return await session.scalar(
        text(
            """
            SELECT id FROM games
             WHERE id IS DISTINCT FROM :ignore
               AND tstzrange(starts_at, LEAST(ends_at, COALESCE(finished_at, ends_at)))
                   && tstzrange(:starts, :ends)
             ORDER BY starts_at
             LIMIT 1
            """
        ),
        {"ignore": ignore_id, "starts": starts_at, "ends": ends_at},
    )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


async def create_game(
    session: AsyncSession, values: GameValues, model_version: str, admin_id: uuid.UUID
) -> Game:
    game = Game(
        secret_word=values.secret_word,
        model_version=model_version,
        starts_at=values.starts_at,
        ends_at=values.ends_at,
        prize_code=values.prize_code,
        prize_description=values.prize_description,
        created_by=admin_id,
    )
    session.add(game)
    await session.flush()
    await build_ranking(session, game)
    await session.commit()
    return game


async def update_game(session: AsyncSession, game: Game, values: GameValues) -> None:
    """Apply validated values. Only fields allowed by editable_fields() can differ."""
    word_changed = values.secret_word != game.secret_word
    game.secret_word = values.secret_word
    game.starts_at = values.starts_at
    game.ends_at = values.ends_at
    game.prize_code = values.prize_code
    game.prize_description = values.prize_description
    await session.flush()
    if word_changed:
        await build_ranking(session, game)
    if game.status == GameStatus.FINISHED:
        await award_prize(session, game.id)  # coupon registered after the win
    await session.commit()


async def unlock(session: AsyncSession, game: Game, admin_id: uuid.UUID, now: datetime) -> None:
    await unlock_game(session, game.id, admin_id, now)
    await session.refresh(game)


async def finish(session: AsyncSession, game: Game, now: datetime) -> None:
    if not await finish_game(session, game.id, now):
        raise GameStateError("Only an active game can be finished.")
    await session.refresh(game)


async def delete_game(session: AsyncSession, game: Game) -> None:
    if game.status != GameStatus.SCHEDULED:
        raise GameStateError("Only a scheduled game can be deleted.")
    await session.execute(delete(Game).where(Game.id == game.id))
    await session.commit()
