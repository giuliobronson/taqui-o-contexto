import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import Profile
from app.auth.session import ACCESS_COOKIE
from app.games.models import Game, GameMember, GameStatus, MemberRole, Vocabulary
from app.games.ranking import build_ranking
from app.guesses.presentation import rank_to_color
from app.guesses.service import Outcome, ranking, submit_guess, time_until_next_day
from tests.conftest import mint_token

WORDS = {
    "café": [1.0, 0.0, 0.0],
    "chá": [0.9, 0.1, 0.0],
    "leite": [0.6, 0.6, 0.0],
    "pão": [0.3, 0.7, 0.1],
    "pedra": [0.0, 0.0, 1.0],
}

# 2030-03-01 12:00 UTC is 09:00 in São Paulo.
NOON = datetime(2030, 3, 1, 12, tzinfo=UTC)


async def _add_vocabulary(session: AsyncSession) -> None:
    session.add_all(
        Vocabulary(model_version="test", word=word, embedding=vector)
        for word, vector in WORDS.items()
    )
    await session.flush()


@pytest.fixture
async def open_game(session, make_game):
    await _add_vocabulary(session)
    game = await make_game(
        status=GameStatus.OPEN, starts_at=NOON - timedelta(days=1), ends_at=NOON + timedelta(days=9)
    )
    await build_ranking(session, game)
    return game


@pytest.fixture
async def player(session, make_profile, open_game):
    profile = await make_profile(name="Ana")
    session.add(GameMember(game_id=open_game.id, user_id=profile.id, role=MemberRole.PLAYER))
    await session.flush()
    return profile


# ---------- submit_guess ----------


async def test_accepted_guess_records_rank(session, open_game, player) -> None:
    result = await submit_guess(session, open_game, player.id, "  Chá ", NOON)
    assert result.outcome == Outcome.ACCEPTED
    assert (result.word, result.rank) == ("chá", 2)


async def test_one_guess_per_day(session, open_game, player) -> None:
    await submit_guess(session, open_game, player.id, "chá", NOON)
    result = await submit_guess(session, open_game, player.id, "leite", NOON + timedelta(hours=1))
    assert result.outcome == Outcome.ALREADY_GUESSED_TODAY


async def test_day_turns_at_midnight_in_sao_paulo(session, open_game, player) -> None:
    await submit_guess(session, open_game, player.id, "chá", NOON)
    # 02:59 UTC on Mar 2 is still Mar 1 in São Paulo (UTC-3).
    late = datetime(2030, 3, 2, 2, 59, tzinfo=UTC)
    assert (await submit_guess(session, open_game, player.id, "leite", late)).outcome == (
        Outcome.ALREADY_GUESSED_TODAY
    )
    next_day = datetime(2030, 3, 2, 3, 0, tzinfo=UTC)
    assert (await submit_guess(session, open_game, player.id, "leite", next_day)).outcome == (
        Outcome.ACCEPTED
    )


@pytest.mark.parametrize(
    ("word", "outcome"),
    [("abacaxi", Outcome.UNKNOWN_WORD), ("   ", Outcome.EMPTY)],
)
async def test_invalid_words_do_not_use_the_daily_guess(
    session, open_game, player, word, outcome
) -> None:
    assert (await submit_guess(session, open_game, player.id, word, NOON)).outcome == outcome
    assert (await submit_guess(session, open_game, player.id, "chá", NOON)).outcome == (
        Outcome.ACCEPTED
    )


async def test_repeated_word_does_not_use_the_daily_guess(session, open_game, player) -> None:
    await submit_guess(session, open_game, player.id, "chá", NOON)
    tomorrow = NOON + timedelta(days=1)
    assert (await submit_guess(session, open_game, player.id, "chá", tomorrow)).outcome == (
        Outcome.REPEATED_WORD
    )
    assert (await submit_guess(session, open_game, player.id, "pão", tomorrow)).outcome == (
        Outcome.ACCEPTED
    )


async def test_correct_guess_wins_and_finishes_game(session, open_game, player) -> None:
    result = await submit_guess(session, open_game, player.id, "café", NOON)

    assert result.outcome == Outcome.WON
    await session.refresh(open_game)
    assert open_game.status == GameStatus.FINISHED
    assert open_game.winner_id == player.id


async def test_no_guesses_after_game_finished(session, open_game, player, make_profile) -> None:
    await submit_guess(session, open_game, player.id, "café", NOON)
    other = await make_profile()
    result = await submit_guess(session, open_game, other.id, "chá", NOON)
    assert result.outcome == Outcome.GAME_FINISHED


async def test_no_guesses_while_locked(session, make_game, make_profile) -> None:
    game = await make_game(status=GameStatus.LOCKED)
    profile = await make_profile()
    result = await submit_guess(session, game, profile.id, "chá", NOON)
    assert result.outcome == Outcome.GAME_LOCKED


async def test_time_until_next_day() -> None:
    # 09:00 in São Paulo → 15 hours until midnight.
    assert time_until_next_day(NOON) == timedelta(hours=15)


# ---------- ranking ----------


async def test_ranking_groups_and_orders_guesses(session, open_game, player, make_profile) -> None:
    other = await make_profile()
    await submit_guess(session, open_game, player.id, "pedra", NOON)
    await submit_guess(session, open_game, other.id, "pedra", NOON)
    await submit_guess(session, open_game, player.id, "chá", NOON + timedelta(days=1))

    rows = await ranking(session, open_game, player.id)

    assert [(r.word, r.rank, r.players, r.mine) for r in rows] == [
        ("chá", 2, 1, True),
        ("pedra", 5, 2, True),
    ]
    other_view = await ranking(session, open_game, other.id)
    assert [r.mine for r in other_view] == [False, True]


async def test_secret_word_appears_once_game_is_finished(session, open_game, player) -> None:
    await submit_guess(session, open_game, player.id, "café", NOON)
    rows = await ranking(session, open_game, None)
    assert rows[0].word == "café" and rows[0].rank == 1


def test_rank_colors_go_from_green_to_red() -> None:
    assert rank_to_color(1, 50_000) == "hsl(120, 55%, 82%)"
    assert rank_to_color(50_000, 50_000) == "hsl(0, 55%, 82%)"
    # Log scale: rank 224 (≈ √50 000) is halfway.
    assert rank_to_color(224, 50_000) == "hsl(60, 55%, 82%)"


# ---------- concurrency ----------


async def test_two_simultaneous_correct_guesses_have_one_winner(engine) -> None:
    """Real concurrent transactions (no test rollback), cleaned up at the end."""
    ids = [uuid.uuid4(), uuid.uuid4()]
    model = f"race-{uuid.uuid4().hex[:8]}"
    far = datetime(2099, 1, 1, tzinfo=UTC)
    async with AsyncSession(engine, expire_on_commit=False) as setup:
        for user_id in ids:
            await setup.execute(
                text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
                {"id": user_id, "email": f"{user_id.hex[:8]}@race.test"},
            )
            setup.add(Profile(id=user_id, email=f"{user_id.hex[:8]}@race.test", display_name="R"))
        setup.add_all(
            Vocabulary(model_version=model, word=w, embedding=v) for w, v in WORDS.items()
        )
        game = Game(
            secret_word="café",
            model_version=model,
            status=GameStatus.OPEN,
            starts_at=far,
            ends_at=far + timedelta(days=7),
            unlocked_at=far,
        )
        setup.add(game)
        await setup.flush()
        await build_ranking(setup, game)
        await setup.commit()

    async def attempt(user_id: uuid.UUID) -> Outcome:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            race_game = await session.get(Game, game.id)
            return (await submit_guess(session, race_game, user_id, "café", far)).outcome

    try:
        outcomes = await asyncio.gather(*(attempt(user_id) for user_id in ids))
        assert sorted(outcomes) == sorted([Outcome.WON, Outcome.GAME_FINISHED])
    finally:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM games WHERE id = :id"), {"id": game.id})
            await conn.execute(
                text("DELETE FROM vocabulary WHERE model_version = :m"), {"m": model}
            )
            await conn.execute(text("DELETE FROM auth.users WHERE id = ANY(:ids)"), {"ids": ids})


# ---------- HTTP ----------


def _login(client, profile: Profile) -> None:
    client.cookies.set(ACCESS_COOKIE, mint_token(profile.id, profile.email))


@pytest.fixture
def at_noon(clock):
    clock.now = NOON
    return clock


async def test_visitor_sees_ranking_and_login_button(client, open_game, at_noon) -> None:
    response = await client.get("/")
    assert "Entrar para jogar" in response.text
    assert "Nenhum palpite ainda" in response.text
    assert "café" not in response.text


async def test_player_guesses_through_htmx(client, open_game, player, at_noon) -> None:
    _login(client, player)

    page = await client.get("/")
    assert 'hx-post="/guesses"' in page.text

    response = await client.post("/guesses", data={"word": "leite"})
    assert response.status_code == 200
    assert "está na posição 3" in response.text
    assert 'hx-swap-oob="innerHTML"' in response.text
    assert "Próximo palpite em 15h 00min" in response.text

    again = await client.post("/guesses", data={"word": "chá"})
    assert "está na posição" not in again.text
    assert "Seu palpite de hoje" in again.text


async def test_unknown_word_message_keeps_form(client, open_game, player, at_noon) -> None:
    _login(client, player)
    response = await client.post("/guesses", data={"word": "abacaxi"})
    assert "Não conhecemos" in response.text
    assert 'hx-post="/guesses"' in response.text


async def test_winning_through_http(client, open_game, player, at_noon) -> None:
    _login(client, player)
    response = await client.post("/guesses", data={"word": "café"})
    assert "Você acertou!" in response.text

    page = await client.get("/")
    assert "A palavra era" in page.text and "Ana" in page.text


async def test_watcher_sees_secret_and_cannot_guess(
    client, session, open_game, make_profile, at_noon
) -> None:
    watcher = await make_profile()
    session.add(GameMember(game_id=open_game.id, user_id=watcher.id, role=MemberRole.WATCHER))
    await session.flush()
    _login(client, watcher)

    page = await client.get("/")
    assert "Palavra secreta" in page.text and "café" in page.text
    assert 'hx-post="/guesses"' not in page.text

    response = await client.post("/guesses", data={"word": "café"})
    assert "Você acertou" not in response.text
    await session.refresh(open_game)
    assert open_game.status == GameStatus.OPEN


async def test_admin_sees_secret_and_cannot_guess(client, open_game, make_profile, at_noon) -> None:
    admin = await make_profile(is_admin=True)
    _login(client, admin)
    page = await client.get("/")
    assert "Palavra secreta" in page.text
    assert "Administradores acompanham o jogo" in page.text


async def test_locked_game_blocks_everyone(client, make_game, make_profile, at_noon) -> None:
    await make_game(status=GameStatus.LOCKED, starts_at=NOON - timedelta(hours=1))
    _login(client, await make_profile())
    page = await client.get("/")
    assert "O jogo ainda não foi liberado" in page.text
    assert 'hx-post="/guesses"' not in page.text


async def test_post_guess_requires_login(client, open_game, at_noon) -> None:
    assert (await client.post("/guesses", data={"word": "chá"})).status_code == 401


async def test_ranking_poll_asks_reload_when_state_changes(
    client, open_game, player, at_noon
) -> None:
    unchanged = await client.get("/ranking", params={"status": "open"})
    assert "HX-Refresh" not in unchanged.headers

    _login(client, player)
    await client.post("/guesses", data={"word": "café"})
    changed = await client.get("/ranking", params={"status": "open"})
    assert changed.headers.get("HX-Refresh") == "true"


async def test_no_game_page(client, at_noon) -> None:
    response = await client.get("/")
    assert "Nenhum jogo no momento" in response.text


async def test_finished_game_page_is_public(client, open_game, player, at_noon) -> None:
    _login(client, player)
    await client.post("/guesses", data={"word": "café"})
    client.cookies.clear()

    response = await client.get(f"/games/{open_game.id}")
    assert response.status_code == 200
    assert "A palavra era" in response.text and "café" in response.text


async def test_unfinished_game_page_is_hidden(client, open_game, at_noon) -> None:
    assert (await client.get(f"/games/{open_game.id}")).status_code == 404
    assert (await client.get("/games/999999")).status_code == 404
