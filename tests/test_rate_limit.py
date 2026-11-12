from datetime import UTC, datetime, timedelta

import pytest

from app.auth.session import ACCESS_COOKIE
from app.core.rate_limit import GUESSES_PER_USER, HINTS_PER_USER, RateLimiter
from app.games.models import GameMember, GameStatus, MemberRole, Vocabulary
from app.games.ranking import build_ranking
from tests.conftest import mint_token

NOON = datetime(2030, 3, 1, 12, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_sliding_window() -> None:
    clock = FakeTime()
    limiter = RateLimiter(limit=2, window_seconds=60, clock=clock)
    assert limiter.hit("a") and limiter.hit("a")
    assert not limiter.hit("a")
    assert limiter.hit("b")  # keys are independent
    clock.now = 59.9
    assert not limiter.hit("a")
    clock.now = 60.1  # the first two hits left the window
    assert limiter.hit("a")


def test_stale_keys_are_pruned(monkeypatch) -> None:
    monkeypatch.setattr("app.core.rate_limit._MAX_KEYS", 3)
    clock = FakeTime()
    limiter = RateLimiter(limit=1, window_seconds=10, clock=clock)
    for key in "abc":
        limiter.hit(key)
    clock.now = 20
    limiter.hit("d")
    assert set(limiter._hits) == {"d"}


@pytest.fixture
async def game(session, make_game, clock):
    clock.now = NOON
    session.add_all(
        [
            Vocabulary(model_version="test", word="café", embedding=[1.0, 0.0, 0.0]),
            Vocabulary(model_version="test", word="chá", embedding=[0.9, 0.1, 0.0]),
        ]
    )
    await session.flush()
    game = await make_game(
        status=GameStatus.OPEN, starts_at=NOON - timedelta(days=1), ends_at=NOON + timedelta(days=9)
    )
    await build_ranking(session, game)
    return game


async def _login_member(client, session, make_profile, game, role):
    profile = await make_profile()
    session.add(GameMember(game_id=game.id, user_id=profile.id, role=role))
    await session.flush()
    client.cookies.set(ACCESS_COOKIE, mint_token(profile.id, profile.email))
    return profile


async def test_guess_spam_gets_429(client, session, make_profile, game, monkeypatch) -> None:
    monkeypatch.setattr(GUESSES_PER_USER, "limit", 3)
    await _login_member(client, session, make_profile, game, MemberRole.PLAYER)
    for _ in range(3):  # unknown words do not use up the daily guess
        response = await client.post("/guesses", data={"word": "abacaxi"})
        assert response.status_code == 200
    blocked = await client.post("/guesses", data={"word": "chá"})
    assert blocked.status_code == 429
    assert "Muitas tentativas seguidas" in blocked.text
    assert "está na posição" not in blocked.text


async def test_hint_spam_gets_429_and_keeps_text(
    client, session, make_profile, game, monkeypatch
) -> None:
    monkeypatch.setattr(HINTS_PER_USER, "limit", 1)
    await _login_member(client, session, make_profile, game, MemberRole.WATCHER)
    assert (await client.post("/hints", data={"body": "primeira"})).status_code == 200
    blocked = await client.post("/hints", data={"body": "segunda dica"})
    assert blocked.status_code == 429
    assert "Muitas dicas seguidas" in blocked.text
    assert "segunda dica</textarea>" in blocked.text
