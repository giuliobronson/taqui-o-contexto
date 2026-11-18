from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.auth.models import Profile
from app.auth.session import ACCESS_COOKIE
from app.games.models import GameMember, GameStatus, MemberRole, Vocabulary, WordForm
from app.games.ranking import build_ranking
from app.hints.models import Hint
from app.hints.presentation import time_ago
from app.hints.service import HintOutcome, fold, list_hints, post_hint, reveals
from tests.conftest import mint_token

NOON = datetime(2030, 3, 1, 12, tzinfo=UTC)


@pytest.fixture
async def open_game(session, make_game):
    session.add_all(
        [
            Vocabulary(model_version="test", word="spy family", embedding=[1.0, 0.0, 0.0]),
            Vocabulary(model_version="test", word="espião", embedding=[0.8, 0.2, 0.0]),
            Vocabulary(model_version="test", word="pedra", embedding=[0.0, 0.0, 1.0]),
        ]
    )
    await session.flush()
    session.add(WordForm(model_version="test", form="spy x family", word="spy family"))
    game = await make_game(
        status=GameStatus.OPEN,
        secret_word="spy family",
        starts_at=NOON - timedelta(days=1),
        ends_at=NOON + timedelta(days=9),
    )
    await build_ranking(session, game)
    return game


async def _member(session, make_profile, game, role: MemberRole, name: str) -> Profile:
    profile = await make_profile(name=name)
    session.add(GameMember(game_id=game.id, user_id=profile.id, role=role))
    await session.flush()
    return profile


@pytest.fixture
async def watcher(session, make_profile, open_game):
    return await _member(session, make_profile, open_game, MemberRole.WATCHER, "Bia")


@pytest.fixture
async def player(session, make_profile, open_game):
    return await _member(session, make_profile, open_game, MemberRole.PLAYER, "Ana")


# ---------- secret detection ----------


def test_fold_drops_accents_case_and_punctuation() -> None:
    assert fold("  Espião,  CAFÉ-com-Leite! ") == "espiao cafe com leite"


@pytest.mark.parametrize(
    "body",
    [
        "É SPY FAMILY!",
        "spy-family",
        "Spyfamily",
        "Spy×Family",
        "o nome é spy x family",
    ],
)
def test_reveals_secret_in_any_spelling(body) -> None:
    assert reveals(body, {"spy family", "spy x family"})


def test_reveals_matches_whole_words_only() -> None:
    assert not reveals("pense no amarelo", {"mar"})
    assert reveals("pense no MAR", {"mar"})
    assert reveals("Mar!", {"mar"})


def test_reveals_ignores_accents() -> None:
    assert reveals("tem cafe aqui", {"café"})


# ---------- post_hint / list_hints ----------


async def test_watcher_posts_hint(session, open_game, watcher) -> None:
    result = await post_hint(session, open_game, watcher.id, "  Um agente secreto  ")
    assert result.outcome == HintOutcome.POSTED
    [hint] = await list_hints(session, open_game)
    assert (hint.body, hint.author_name) == ("Um agente secreto", "Bia")


@pytest.mark.parametrize(
    ("body", "outcome"),
    [
        ("   ", HintOutcome.EMPTY),
        ("x" * 281, HintOutcome.TOO_LONG),
        ("é o Spy X Family", HintOutcome.REVEALS_SECRET),
    ],
)
async def test_rejected_hints_are_not_saved(session, open_game, watcher, body, outcome) -> None:
    result = await post_hint(session, open_game, watcher.id, body)
    assert result.outcome == outcome
    assert await list_hints(session, open_game) == []


async def test_280_characters_is_allowed(session, open_game, watcher) -> None:
    result = await post_hint(session, open_game, watcher.id, "x" * 280)
    assert result.outcome == HintOutcome.POSTED


async def test_no_hints_unless_game_is_open(session, open_game, watcher) -> None:
    open_game.status = GameStatus.LOCKED
    open_game.unlocked_at = None
    await session.flush()
    result = await post_hint(session, open_game, watcher.id, "dica")
    assert result.outcome == HintOutcome.GAME_NOT_OPEN


async def test_list_is_newest_first_and_skips_deleted(session, open_game, watcher) -> None:
    session.add_all(
        [
            Hint(game_id=open_game.id, author_id=watcher.id, body="velha", created_at=NOON),
            Hint(
                game_id=open_game.id,
                author_id=watcher.id,
                body="nova",
                created_at=NOON + timedelta(hours=1),
            ),
            Hint(
                game_id=open_game.id,
                author_id=watcher.id,
                body="apagada",
                created_at=NOON + timedelta(hours=2),
                deleted_at=NOON + timedelta(hours=3),
            ),
        ]
    )
    await session.flush()
    assert [h.body for h in await list_hints(session, open_game)] == ["nova", "velha"]


def test_time_ago() -> None:
    assert time_ago(NOON, NOON + timedelta(seconds=30)) == "agora"
    assert time_ago(NOON, NOON + timedelta(minutes=5)) == "há 5 min"
    assert time_ago(NOON, NOON + timedelta(hours=3)) == "há 3 h"
    assert time_ago(NOON, NOON + timedelta(days=1)) == "há 1 dia"
    assert time_ago(NOON, NOON + timedelta(days=8)) == "01/03 às 09:00"


# ---------- HTTP ----------


def _login(client, profile: Profile) -> None:
    client.cookies.set(ACCESS_COOKIE, mint_token(profile.id, profile.email))


@pytest.fixture
def at_noon(clock):
    clock.now = NOON
    return clock


async def test_watcher_posts_through_htmx(client, open_game, watcher, at_noon) -> None:
    _login(client, watcher)
    page = await client.get("/")
    assert 'hx-post="/hints"' in page.text

    response = await client.post("/hints", data={"body": "Um agente <b>secreto</b>"})
    assert response.status_code == 200
    assert "Dica publicada" in response.text
    assert 'id="hints" hx-swap-oob="innerHTML"' in response.text
    assert "Um agente &lt;b&gt;secreto&lt;/b&gt;" in response.text  # escaped


async def test_rejected_hint_keeps_text(client, open_game, watcher, at_noon) -> None:
    _login(client, watcher)
    response = await client.post("/hints", data={"body": "rima com spy family"})
    assert "não pode conter a palavra secreta" in response.text
    assert "rima com spy family</textarea>" in response.text


async def test_everyone_sees_the_board(client, session, open_game, watcher, at_noon) -> None:
    await post_hint(session, open_game, watcher.id, "Pense em espiões")
    page = await client.get("/")
    assert "Pense em espiões" in page.text
    assert 'hx-post="/hints"' not in page.text  # visitors cannot write

    board = await client.get("/hints")
    assert "Pense em espiões" in board.text and "Bia" in board.text


async def test_player_cannot_post(client, session, open_game, player, at_noon) -> None:
    _login(client, player)
    page = await client.get("/")
    assert 'hx-post="/hints"' not in page.text
    assert (await client.post("/hints", data={"body": "dica"})).status_code == 403
    assert (await session.scalars(select(Hint))).all() == []


async def test_admin_cannot_post(client, open_game, make_profile, at_noon) -> None:
    _login(client, await make_profile(is_admin=True))
    assert (await client.post("/hints", data={"body": "dica"})).status_code == 403


async def test_post_hint_requires_login(client, open_game, at_noon) -> None:
    assert (await client.post("/hints", data={"body": "dica"})).status_code == 401


# ---------- deleting (T-25) ----------


async def _posted(session, game, author) -> Hint:
    await post_hint(session, game, author.id, "Pense em espiões")
    return (await session.scalars(select(Hint))).one()


async def test_author_deletes_own_hint(client, session, open_game, watcher, at_noon) -> None:
    hint = await _posted(session, open_game, watcher)
    _login(client, watcher)
    page = await client.get("/")
    assert f'hx-delete="/hints/{hint.id}"' in page.text

    response = await client.delete(f"/hints/{hint.id}")
    assert response.status_code == 200
    assert "Pense em espiões" not in response.text
    await session.refresh(hint)
    assert (hint.deleted_at, hint.deleted_by) == (NOON, watcher.id)
    assert await list_hints(session, open_game) == []


async def test_admin_deletes_any_hint(
    client, session, open_game, watcher, make_profile, at_noon
) -> None:
    hint = await _posted(session, open_game, watcher)
    admin = await make_profile(is_admin=True)
    _login(client, admin)
    assert f'hx-delete="/hints/{hint.id}"' in (await client.get("/")).text
    assert (await client.delete(f"/hints/{hint.id}")).status_code == 200
    await session.refresh(hint)
    assert hint.deleted_by == admin.id


async def test_others_cannot_delete(
    client, session, open_game, watcher, player, make_profile, at_noon
) -> None:
    hint = await _posted(session, open_game, watcher)
    other_watcher = await _member(session, make_profile, open_game, MemberRole.WATCHER, "Caio")
    for profile in (player, other_watcher):
        _login(client, profile)
        assert "hx-delete" not in (await client.get("/")).text
        assert (await client.delete(f"/hints/{hint.id}")).status_code == 403
    client.cookies.clear()
    assert (await client.delete(f"/hints/{hint.id}")).status_code == 401
    await session.refresh(hint)
    assert hint.deleted_at is None


async def test_deleting_twice_or_unknown_is_404(
    client, session, open_game, watcher, at_noon
) -> None:
    hint = await _posted(session, open_game, watcher)
    _login(client, watcher)
    assert (await client.delete(f"/hints/{hint.id}")).status_code == 200
    assert (await client.delete(f"/hints/{hint.id}")).status_code == 404
    assert (await client.delete("/hints/999999")).status_code == 404


async def test_game_page_has_mobile_tabs(client, session, open_game, watcher, at_noon) -> None:
    await post_hint(session, open_game, watcher.id, "Pense em espiões")
    page = await client.get("/")
    assert 'role="tablist"' in page.text
    assert 'aria-controls="panel-ranking"' in page.text and 'id="panel-hints"' in page.text
    assert '<span class="tab-count">1</span>' in page.text
    assert "js/tabs.js" in page.text
