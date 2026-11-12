"""Builds what the game page shows for a given viewer (PRD RF-2, RF-3, RF-7)."""

import enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.core.clock import local_date
from app.games.models import Game, GameMember, GameStatus, MemberRole
from app.games.service import (
    current_game,
    join_active_game,
    last_finished_game,
    next_scheduled_game,
)
from app.guesses.models import Guess
from app.guesses.service import RankingRow, ranking, time_until_next_day, todays_guess


class FormState(enum.StrEnum):
    """What the guess field shows (PRD RF-2, T-23)."""

    VISITOR = "visitor"  # not logged in: "Entrar para jogar"
    ADMIN = "admin"  # admins only watch
    WATCHER = "watcher"  # watchers write hints instead
    LOCKED = "locked"  # game not unlocked yet
    FINISHED = "finished"  # game over
    WAITING = "waiting"  # already guessed today
    READY = "ready"  # can guess now


@dataclass
class GamePage:
    user: Profile | None
    game: Game | None
    is_current: bool = False
    member: GameMember | None = None
    form_state: FormState = FormState.VISITOR
    todays_guess: Guess | None = None
    wait: timedelta | None = None
    rows: list[RankingRow] = field(default_factory=list)
    winner: Profile | None = None
    next_game: Game | None = None

    @property
    def can_see_secret(self) -> bool:
        if self.game is None:
            return False
        if self.game.status == GameStatus.FINISHED:
            return True
        if self.user is not None and self.user.is_admin:
            return True
        return self.member is not None and self.member.role == MemberRole.WATCHER

    @property
    def total(self) -> int:
        return self.game.vocabulary_size if self.game else 0

    def as_context(self) -> dict:
        return {"page": self, "user": self.user, "game": self.game}


def _form_state(
    user: Profile | None, game: Game, member: GameMember | None, guessed_today: bool
) -> FormState:
    if game.status == GameStatus.FINISHED:
        return FormState.FINISHED
    if user is None:
        return FormState.VISITOR
    if user.is_admin:
        return FormState.ADMIN
    if member is not None and member.role == MemberRole.WATCHER:
        return FormState.WATCHER
    if game.status == GameStatus.LOCKED:
        return FormState.LOCKED
    if guessed_today:
        return FormState.WAITING
    return FormState.READY


async def build_game_page(
    session: AsyncSession,
    user: Profile | None,
    now: datetime,
    game: Game | None = None,
) -> GamePage:
    """Page for `game`, or for the current game (falling back to the last finished one)."""
    is_current = False
    if game is None:
        game = await current_game(session, now)
        is_current = game is not None
        if game is None:
            game = await last_finished_game(session)

    page = GamePage(user=user, game=game, is_current=is_current)
    if game is None or game.status == GameStatus.FINISHED:
        page.next_game = await next_scheduled_game(session)
    if game is None:
        return page

    if user is not None and is_current:
        page.member = await join_active_game(session, user, now)
    if user is not None and page.member is not None:
        page.todays_guess = await todays_guess(session, game.id, user.id, local_date(now))

    page.form_state = _form_state(user, game, page.member, page.todays_guess is not None)
    if page.form_state == FormState.WAITING:
        page.wait = time_until_next_day(now)
    page.rows = await ranking(session, game, user.id if user else None)
    if game.winner_id is not None:
        page.winner = await session.get(Profile, game.winner_id)
    return page
