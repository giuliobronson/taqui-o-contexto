from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.auth.models import Profile
from app.auth.session import ACCESS_COOKIE
from app.games.models import GameMember, GameStatus, MemberRole, Vocabulary
from app.games.ranking import build_ranking
from app.guesses.service import Outcome, submit_guess
from app.prizes.models import Prize
from app.prizes.service import award_prize, prizes_of
from tests.conftest import mint_token

NOON = datetime(2030, 3, 1, 12, tzinfo=UTC)
CODE = "SPY-2030-XYZ"


@pytest.fixture
async def make_open_game(session, make_game):
    session.add_all(
        [
            Vocabulary(model_version="test", word="café", embedding=[1.0, 0.0, 0.0]),
            Vocabulary(model_version="test", word="chá", embedding=[0.9, 0.1, 0.0]),
        ]
    )
    await session.flush()

    async def _make(*, code: str | None = CODE, starts_at: datetime = NOON - timedelta(days=1)):
        game = await make_game(
            status=GameStatus.OPEN, starts_at=starts_at, ends_at=starts_at + timedelta(days=10)
        )
        game.prize_code = code
        game.prize_description = "20% de desconto" if code else None
        await build_ranking(session, game)
        return game

    return _make


@pytest.fixture
async def player(session, make_profile):
    return await make_profile(name="Ana")


async def _join(session, game, profile) -> None:
    session.add(GameMember(game_id=game.id, user_id=profile.id, role=MemberRole.PLAYER))
    await session.flush()


async def test_winning_hands_over_the_coupon(session, make_open_game, player) -> None:
    game = await make_open_game()
    await _join(session, game, player)
    assert (await submit_guess(session, game, player.id, "café", NOON)).outcome == Outcome.WON

    [prize] = await prizes_of(session, player.id)
    assert (prize.code, prize.description, prize.game_id) == (CODE, "20% de desconto", game.id)


async def test_wrong_guess_gives_nothing(session, make_open_game, player) -> None:
    game = await make_open_game()
    await _join(session, game, player)
    await submit_guess(session, game, player.id, "chá", NOON)
    assert await prizes_of(session, player.id) == []


async def test_code_registered_after_the_win(session, make_open_game, player) -> None:
    game = await make_open_game(code=None)
    await _join(session, game, player)
    await submit_guess(session, game, player.id, "café", NOON)
    assert await prizes_of(session, player.id) == []

    game.prize_code = "LATE-1"
    await session.flush()
    prize = await award_prize(session, game.id)
    assert prize is not None and prize.user_id == player.id


async def test_award_is_idempotent(session, make_open_game, player) -> None:
    game = await make_open_game()
    await _join(session, game, player)
    await submit_guess(session, game, player.id, "café", NOON)
    first = await award_prize(session, game.id)
    game.prize_code = "CHANGED"
    await session.flush()
    second = await award_prize(session, game.id)
    assert first is not None and second is not None and second.id == first.id
    assert second.code == CODE
    assert await session.scalar(select(func.count()).select_from(Prize)) == 1


async def test_no_prize_without_a_winner(session, make_open_game) -> None:
    game = await make_open_game()
    assert await award_prize(session, game.id) is None


async def test_same_code_in_two_games(session, make_open_game, player) -> None:
    first = await make_open_game(starts_at=NOON - timedelta(days=30))
    await _join(session, first, player)
    await submit_guess(session, first, player.id, "café", NOON - timedelta(days=29))
    second = await make_open_game()
    await _join(session, second, player)
    assert (await submit_guess(session, second, player.id, "café", NOON)).outcome == Outcome.WON
    assert [p.code for p in await prizes_of(session, player.id)] == [CODE, CODE]


# ---------- HTTP ----------


def _login(client, profile: Profile) -> None:
    client.cookies.set(ACCESS_COOKIE, mint_token(profile.id, profile.email))


@pytest.fixture
def at_noon(clock):
    clock.now = NOON
    return clock


async def test_only_the_winner_sees_the_code(
    client, session, make_open_game, player, make_profile, at_noon
) -> None:
    game = await make_open_game()
    other = await make_profile(name="Bia")
    await _join(session, game, player)
    await _join(session, game, other)

    _login(client, player)
    await client.post("/guesses", data={"word": "café"})
    assert CODE in (await client.get("/")).text
    assert CODE in (await client.get("/prizes/me")).text

    _login(client, other)
    assert CODE not in (await client.get("/")).text
    mine = await client.get("/prizes/me")
    assert CODE not in mine.text and "ainda não tem cupons" in mine.text

    client.cookies.clear()
    assert CODE not in (await client.get(f"/games/{game.id}")).text
    assert (await client.get("/prizes/me")).status_code == 401


async def test_code_never_shown_while_playing(
    client, session, make_open_game, player, at_noon
) -> None:
    game = await make_open_game()
    await _join(session, game, player)
    _login(client, player)
    assert CODE not in (await client.get("/")).text


async def test_winner_without_code_is_told_to_wait(
    client, session, make_open_game, player, at_noon
) -> None:
    game = await make_open_game(code=None)
    await _join(session, game, player)
    _login(client, player)
    await client.post("/guesses", data={"word": "café"})
    assert "ainda vai ser cadastrado" in (await client.get("/")).text
