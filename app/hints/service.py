"""Watchers' hint board (PRD RF-4)."""

import enum
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.games.models import Game, GameStatus, WordForm
from app.hints.models import MAX_HINT_LENGTH, Hint


class HintOutcome(enum.StrEnum):
    POSTED = "posted"
    EMPTY = "empty"
    TOO_LONG = "too_long"
    REVEALS_SECRET = "reveals_secret"
    GAME_NOT_OPEN = "game_not_open"


@dataclass(frozen=True)
class HintResult:
    outcome: HintOutcome
    body: str = ""  # kept so the watcher can fix a rejected hint


@dataclass(frozen=True)
class HintView:
    id: int
    body: str
    created_at: datetime
    author_id: uuid.UUID
    author_name: str
    author_avatar: str | None


def fold(text: str) -> str:
    """Lower case, no accents, anything that is not a letter or digit becomes one space."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", bare).split())


def reveals(body: str, forms: set[str]) -> bool:
    """True when `body` contains any of `forms` as whole words, ignoring accents,
    case and punctuation. Multi-word forms also match written together
    ("spy family" in "spyfamily", "spy-family", "Spy×Family")."""
    text = fold(body)
    padded, compact = f" {text} ", text.replace(" ", "")
    for form in filter(None, map(fold, forms)):
        if f" {form} " in padded:
            return True
        if " " in form and form.replace(" ", "") in compact:
            return True
    return False


async def secret_forms(session: AsyncSession, game: Game) -> set[str]:
    """The secret word plus everything that maps to it ("cafés", "spy x family")."""
    variants = await session.scalars(
        select(WordForm.form).where(
            WordForm.model_version == game.model_version, WordForm.word == game.secret_word
        )
    )
    return {game.secret_word, *variants}


async def post_hint(
    session: AsyncSession, game: Game, author_id: uuid.UUID, raw_body: str
) -> HintResult:
    await session.refresh(game)
    if game.status != GameStatus.OPEN:
        return HintResult(HintOutcome.GAME_NOT_OPEN)

    body = raw_body.strip()
    if not body:
        return HintResult(HintOutcome.EMPTY)
    if len(body) > MAX_HINT_LENGTH:
        return HintResult(HintOutcome.TOO_LONG, body)
    if reveals(body, await secret_forms(session, game)):
        return HintResult(HintOutcome.REVEALS_SECRET, body)

    session.add(Hint(game_id=game.id, author_id=author_id, body=body))
    await session.commit()
    return HintResult(HintOutcome.POSTED)


async def list_hints(session: AsyncSession, game: Game) -> list[HintView]:
    """Visible hints of the game, newest first."""
    rows = await session.execute(
        select(Hint, Profile)
        .join(Profile, Profile.id == Hint.author_id)
        .where(Hint.game_id == game.id, Hint.deleted_at.is_(None))
        .order_by(Hint.created_at.desc(), Hint.id.desc())
    )
    return [
        HintView(
            id=hint.id,
            body=hint.body,
            created_at=hint.created_at,
            author_id=author.id,
            author_name=author.display_name,
            author_avatar=author.avatar_url,
        )
        for hint, author in rows
    ]
