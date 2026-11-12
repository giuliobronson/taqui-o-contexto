"""Precomputes a game's word ranking inside Postgres with pgvector (PRD RF-8)."""

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.games.models import Game, Vocabulary, WordRanking


class SecretWordNotInVocabulary(Exception):
    def __init__(self, word: str, model_version: str) -> None:
        super().__init__(f"'{word}' is not in vocabulary '{model_version}'.")
        self.word = word
        self.model_version = model_version


def normalize_word(raw: str) -> str:
    return raw.strip().lower()


async def word_in_vocabulary(session: AsyncSession, model_version: str, word: str) -> bool:
    return bool(
        await session.scalar(
            select(
                select(Vocabulary.word)
                .where(Vocabulary.model_version == model_version, Vocabulary.word == word)
                .exists()
            )
        )
    )


# The secret word is always rank 1; ties in distance are broken alphabetically
# so the ranking is deterministic.
_BUILD_RANKING_SQL = text(
    """
    INSERT INTO word_rankings (game_id, word, rank)
    SELECT :game_id,
           v.word,
           ROW_NUMBER() OVER (
               ORDER BY (v.word = :secret) DESC, v.embedding <=> s.embedding, v.word
           )
      FROM vocabulary v
      CROSS JOIN (
            SELECT embedding FROM vocabulary
             WHERE model_version = :model_version AND word = :secret
           ) s
     WHERE v.model_version = :model_version
    """
)


async def build_ranking(session: AsyncSession, game: Game) -> int:
    """(Re)compute every word's rank for the game's secret word. Returns the vocabulary size."""
    secret = normalize_word(game.secret_word)
    if not await word_in_vocabulary(session, game.model_version, secret):
        raise SecretWordNotInVocabulary(secret, game.model_version)

    await session.execute(delete(WordRanking).where(WordRanking.game_id == game.id))
    await session.execute(
        _BUILD_RANKING_SQL,
        {"game_id": game.id, "secret": secret, "model_version": game.model_version},
    )
    size = await session.scalar(
        select(func.count()).select_from(WordRanking).where(WordRanking.game_id == game.id)
    )
    game.secret_word = secret
    game.vocabulary_size = size or 0
    await session.flush()
    return game.vocabulary_size


async def rank_of(session: AsyncSession, game_id: int, word: str) -> int | None:
    """Rank of `word` in the game, or None when it is not in the vocabulary."""
    return await session.scalar(
        select(WordRanking.rank).where(WordRanking.game_id == game_id, WordRanking.word == word)
    )
