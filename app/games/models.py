import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class GameStatus(enum.StrEnum):
    SCHEDULED = "scheduled"
    LOCKED = "locked"
    OPEN = "open"
    FINISHED = "finished"


ACTIVE_STATUSES = (GameStatus.LOCKED, GameStatus.OPEN)


class MemberRole(enum.StrEnum):
    PLAYER = "player"
    WATCHER = "watcher"


def _enum(enum_cls: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda members: [member.value for member in members],
        create_type=False,
    )


class Vocabulary(Base):
    """Word embeddings loaded offline by pipeline/build_vocabulary.py."""

    __tablename__ = "vocabulary"

    model_version: Mapped[str] = mapped_column(String, primary_key=True)
    word: Mapped[str] = mapped_column(String, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(), nullable=False)


class WordForm(Base):
    """Maps a form a player may type to a vocabulary word ("casas" → "casa")."""

    __tablename__ = "word_forms"

    model_version: Mapped[str] = mapped_column(String, primary_key=True)
    form: Mapped[str] = mapped_column(String, primary_key=True)
    word: Mapped[str] = mapped_column(String, nullable=False)


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    secret_word: Mapped[str] = mapped_column(String, nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False)
    vocabulary_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[GameStatus] = mapped_column(
        _enum(GameStatus, "game_status"), nullable=False, server_default=GameStatus.SCHEDULED
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    unlocked_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    winner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class GameMember(Base):
    __tablename__ = "game_members"

    game_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[MemberRole] = mapped_column(
        _enum(MemberRole, "member_role"), nullable=False, server_default=MemberRole.PLAYER
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WordRanking(Base):
    """Precomputed rank of every vocabulary word for a game's secret word."""

    __tablename__ = "word_rankings"

    game_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("games.id", ondelete="CASCADE"), primary_key=True
    )
    word: Mapped[str] = mapped_column(String, primary_key=True)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
