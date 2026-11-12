"""Imports every feature's models so SQLAlchemy and Alembic see the full schema."""

from app.auth.models import Profile
from app.games.models import Game, GameMember, Vocabulary, WordRanking
from app.guesses.models import Guess
from app.hints.models import Hint
from app.prizes.models import Prize

__all__ = ["Game", "GameMember", "Guess", "Hint", "Prize", "Profile", "Vocabulary", "WordRanking"]
