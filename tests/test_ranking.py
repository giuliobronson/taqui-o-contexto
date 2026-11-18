import pytest

from app.games.models import Vocabulary
from app.games.ranking import SecretWordNotInVocabulary, build_ranking, rank_of

# Tiny 3-dimensional vocabulary: "café" is closest to "chá", then "leite", then "pedra".
WORDS = {
    "café": [1.0, 0.0, 0.0],
    "chá": [0.9, 0.1, 0.0],
    "leite": [0.6, 0.6, 0.0],
    "pedra": [0.0, 0.0, 1.0],
}


@pytest.fixture
async def vocabulary(session):
    session.add_all(
        Vocabulary(model_version="test", word=word, embedding=vector)
        for word, vector in WORDS.items()
    )
    session.add(Vocabulary(model_version="other", word="café", embedding=[0.0, 1.0, 0.0]))
    await session.flush()


async def test_ranking_orders_by_cosine_similarity(session, make_game, vocabulary) -> None:
    game = await make_game(secret_word="café")

    size = await build_ranking(session, game)

    assert size == len(WORDS)
    assert game.vocabulary_size == len(WORDS)
    ranks = {word: await rank_of(session, game.id, word) for word in WORDS}
    assert ranks == {"café": 1, "chá": 2, "leite": 3, "pedra": 4}


async def test_unknown_word_has_no_rank(session, make_game, vocabulary) -> None:
    game = await make_game(secret_word="café")
    await build_ranking(session, game)
    assert await rank_of(session, game.id, "abacaxi") is None


async def test_secret_word_must_be_in_vocabulary(session, make_game, vocabulary) -> None:
    game = await make_game(secret_word="abacaxi")
    with pytest.raises(SecretWordNotInVocabulary):
        await build_ranking(session, game)


async def test_rebuilding_replaces_previous_ranking(session, make_game, vocabulary) -> None:
    game = await make_game(secret_word="café")
    await build_ranking(session, game)

    game.secret_word = "pedra"
    await build_ranking(session, game)

    # "pedra" is orthogonal to the other three, so they tie and are ordered alphabetically.
    ranks = {word: await rank_of(session, game.id, word) for word in WORDS}
    assert ranks == {"pedra": 1, "café": 2, "chá": 3, "leite": 4}


async def test_secret_word_is_normalized(session, make_game, vocabulary) -> None:
    game = await make_game(secret_word="  Café ")
    await build_ranking(session, game)
    assert game.secret_word == "café"
    assert await rank_of(session, game.id, "café") == 1
