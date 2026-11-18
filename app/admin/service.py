"""Admin panel: listing, creating and editing games (PRD RF-6)."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.core.config import get_settings
from app.games.models import ACTIVE_STATUSES, Game, GameMember, GameStatus, MemberRole
from app.games.ranking import build_ranking, resolve_word, word_in_vocabulary
from app.games.service import GameStateError, current_game, finish_game, unlock_game
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


# ---------------------------------------------------------------------------
# Members and watchers (PRD "Designação de watchers")
# ---------------------------------------------------------------------------


class RoleChangeError(Exception):
    pass


@dataclass(frozen=True)
class MemberRow:
    user_id: uuid.UUID
    name: str
    email: str
    avatar_url: str | None
    role: MemberRole
    guesses: int
    joined_at: datetime

    @property
    def can_become_watcher(self) -> bool:
        return self.role == MemberRole.PLAYER and self.guesses == 0


async def game_members(session: AsyncSession, game_id: int) -> list[MemberRow]:
    """Members with their role and number of guesses: watchers first, then by name."""
    guesses = (
        select(func.count())
        .where(Guess.game_id == GameMember.game_id, Guess.user_id == GameMember.user_id)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(GameMember, Profile, guesses.label("guesses"))
        .join(Profile, Profile.id == GameMember.user_id)
        .where(GameMember.game_id == game_id)
        .order_by(GameMember.role.desc(), func.lower(Profile.display_name))
    )
    return [
        MemberRow(
            user_id=profile.id,
            name=profile.display_name,
            email=profile.email,
            avatar_url=profile.avatar_url,
            role=member.role,
            guesses=count,
            joined_at=member.joined_at,
        )
        for member, profile, count in rows
    ]


async def make_watcher(session: AsyncSession, game: Game, user_id: uuid.UUID) -> None:
    """Player → watcher, only for an active game and a player with no guesses.

    The membership row is locked, like submit_guess() does, so a guess and the
    role change cannot interleave. There is no way back: a watcher saw the word.
    """
    if game.status not in ACTIVE_STATUSES:
        raise RoleChangeError("O jogo não está ativo.")
    member = await session.scalar(
        select(GameMember)
        .where(GameMember.game_id == game.id, GameMember.user_id == user_id)
        .with_for_update()
    )
    if member is None:
        raise RoleChangeError("Usuário não é membro deste jogo.")
    if member.role != MemberRole.PLAYER:
        raise RoleChangeError("Um watcher não volta a ser player.")
    has_guesses = await session.scalar(
        select(select(Guess.id).where(Guess.game_id == game.id, Guess.user_id == user_id).exists())
    )
    if has_guesses:
        raise RoleChangeError("Este player já deu palpites neste jogo.")
    member.role = MemberRole.WATCHER
    await session.commit()


# ---------------------------------------------------------------------------
# Admins (PRD RF-6 "Admins")
# ---------------------------------------------------------------------------


class AdminChangeError(Exception):
    pass


@dataclass(frozen=True)
class UserRow:
    profile: Profile
    role_in_active_game: MemberRole | None
    from_config: bool  # listed in ADMIN_EMAILS: always promoted again on login


async def list_users(
    session: AsyncSession, now: datetime, query: str = "", limit: int = 200
) -> list[UserRow]:
    """Admins first, then by name. `query` filters by name or e-mail."""
    active = await current_game(session, now)
    role = (
        select(GameMember.role)
        .where(GameMember.game_id == active.id, GameMember.user_id == Profile.id)
        .scalar_subquery()
        if active
        else None
    )
    columns = [Profile] + ([role.label("role")] if role is not None else [])
    stmt = select(*columns).order_by(Profile.is_admin.desc(), func.lower(Profile.display_name))
    if query.strip():
        pattern = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Profile.display_name).like(pattern) | func.lower(Profile.email).like(pattern)
        )
    config = set(get_settings().admin_emails)
    rows = await session.execute(stmt.limit(limit))
    return [
        UserRow(
            profile=row.Profile,
            role_in_active_game=row.role if role is not None else None,
            from_config=row.Profile.email.lower() in config,
        )
        for row in rows
    ]


async def set_admin(
    session: AsyncSession, actor: Profile, target_id: uuid.UUID, make_admin: bool, now: datetime
) -> Profile:
    """Promote or demote a user. An admin cannot demote themself.

    A demoted admin has seen the secret word of the active game, so they come back
    to it as a watcher, never as a player.
    """
    if target_id == actor.id:
        raise AdminChangeError("Você não pode mudar o seu próprio papel de admin.")
    target = await session.get(Profile, target_id, with_for_update=True)
    if target is None:
        raise AdminChangeError("Usuário não encontrado.")
    if target.is_admin == make_admin:
        return target

    if not make_admin:
        if target.email.lower() in get_settings().admin_emails:
            raise AdminChangeError(
                "Este e-mail está em ADMIN_EMAILS: remova-o da configuração primeiro."
            )
        active = await current_game(session, now)
        if active is not None:
            await session.execute(
                insert(GameMember)
                .values(game_id=active.id, user_id=target.id, role=MemberRole.WATCHER)
                .on_conflict_do_update(
                    index_elements=["game_id", "user_id"], set_={"role": MemberRole.WATCHER}
                )
            )
    target.is_admin = make_admin
    await session.commit()
    return target
