import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.admin.service import editable_fields
from app.auth.models import Profile
from app.auth.session import ACCESS_COOKIE
from app.core.config import get_settings
from app.games.models import (
    Game,
    GameMember,
    GameStatus,
    MemberRole,
    Vocabulary,
    WordForm,
    WordRanking,
)
from app.games.ranking import build_ranking
from app.guesses.service import submit_guess
from app.prizes.service import prizes_of
from tests.conftest import mint_token

# 12:00 UTC = 09:00 in São Paulo.
NOON = datetime(2030, 3, 1, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
async def vocabulary(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "vocabulary_version", "test")
    session.add_all(
        [
            Vocabulary(model_version="test", word="spy family", embedding=[1.0, 0.0, 0.0]),
            Vocabulary(model_version="test", word="café", embedding=[0.0, 1.0, 0.0]),
            Vocabulary(model_version="test", word="chá", embedding=[0.1, 0.9, 0.0]),
        ]
    )
    await session.flush()
    session.add(WordForm(model_version="test", form="spy x family", word="spy family"))
    await session.flush()


@pytest.fixture(autouse=True)
def at_noon(clock):
    clock.now = NOON
    return clock


@pytest.fixture
async def admin(make_profile, client) -> Profile:
    profile = await make_profile(is_admin=True, name="Admin")
    client.cookies.set(ACCESS_COOKIE, mint_token(profile.id, profile.email))
    return profile


def _form(**overrides) -> dict[str, str]:
    data = {
        "secret_word": "Spy x Family",
        "starts_at": "2030-03-02T18:00",
        "ends_at": "2030-03-09T18:00",
        "prize_code": "SPY-10",
        "prize_description": "10% de desconto",
    }
    return {**data, **overrides}


async def _game(session, status: GameStatus, **kwargs) -> Game:
    starts = kwargs.pop("starts_at", NOON - timedelta(days=1))
    game = Game(
        secret_word="café",
        model_version="test",
        status=status,
        starts_at=starts,
        ends_at=kwargs.pop("ends_at", starts + timedelta(days=7)),
        unlocked_at=NOON if status in (GameStatus.OPEN, GameStatus.FINISHED) else None,
        finished_at=NOON if status == GameStatus.FINISHED else None,
        **kwargs,
    )
    session.add(game)
    await session.flush()
    await build_ranking(session, game)
    return game


# ---------- access ----------


async def test_admin_routes_need_an_admin(client, make_profile) -> None:
    assert (await client.get("/admin")).status_code == 401
    player = await make_profile()
    client.cookies.set(ACCESS_COOKIE, mint_token(player.id, player.email))
    assert (await client.get("/admin")).status_code == 403
    assert (await client.post("/admin/games/new", data=_form())).status_code == 403


# ---------- creating ----------


async def test_create_game(client, session, admin) -> None:
    response = await client.post("/admin/games/new", data=_form())
    assert response.status_code == 303
    game = (await session.scalars(select(Game))).one()
    assert response.headers["location"] == f"/admin/games/{game.id}?notice=created"
    assert game.secret_word == "spy family"  # the variant was resolved
    assert game.status == GameStatus.SCHEDULED
    assert game.starts_at == datetime(2030, 3, 2, 21, tzinfo=UTC)  # 18:00 in São Paulo
    assert (game.prize_code, game.prize_description) == ("SPY-10", "10% de desconto")
    assert game.created_by == admin.id
    assert game.vocabulary_size == 3
    ranks = await session.scalar(
        select(func.count()).select_from(WordRanking).where(WordRanking.game_id == game.id)
    )
    assert ranks == 3

    page = await client.get(response.headers["location"])
    assert "Jogo criado" in page.text and "spy family" in page.text


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"secret_word": "abacaxi"}, "não está no vocabulário"),
        ({"secret_word": " "}, "Informe a palavra secreta"),
        ({"starts_at": ""}, "Informe a data e a hora de início"),
        ({"ends_at": "2030-03-01T10:00"}, "O fim precisa ser depois do início"),
        (
            {"starts_at": "2030-02-01T10:00", "ends_at": "2030-03-01T08:00"},
            "O fim precisa estar no futuro",
        ),
        ({"prize_code": "x" * 101}, "O código pode ter até 100 caracteres"),
    ],
)
async def test_create_game_validation(client, session, admin, override, message) -> None:
    response = await client.post("/admin/games/new", data=_form(**override))
    assert response.status_code == 422
    assert message in response.text
    assert (await session.scalars(select(Game))).all() == []


async def test_create_rejects_overlapping_period(client, session, admin) -> None:
    other = await _game(session, GameStatus.SCHEDULED, starts_at=NOON + timedelta(days=3))
    response = await client.post("/admin/games/new", data=_form())
    assert response.status_code == 422
    assert f"coincide com o do jogo #{other.id}" in response.text


async def test_game_without_coupon(client, session, admin) -> None:
    response = await client.post(
        "/admin/games/new", data=_form(prize_code="", prize_description="")
    )
    assert response.status_code == 303
    game = (await session.scalars(select(Game))).one()
    assert game.prize_code is None and game.prize_description is None


# ---------- listing ----------


async def test_list_shows_counts_and_winner(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN, prize_code="C1")
    ana = await make_profile(name="Ana")
    bia = await make_profile(name="Bia")
    session.add_all(
        [
            GameMember(game_id=game.id, user_id=ana.id, role=MemberRole.PLAYER),
            GameMember(game_id=game.id, user_id=bia.id, role=MemberRole.WATCHER),
        ]
    )
    await session.flush()
    await submit_guess(session, game, ana.id, "café", NOON)

    page = await client.get("/admin")
    assert f'href="/admin/games/{game.id}"' in page.text
    assert "1 players · 1 watchers" in page.text
    assert "Ana" in page.text and "Encerrado" in page.text


# ---------- editing rules ----------


def test_editable_fields_by_state() -> None:
    everything = {"secret_word", "starts_at", "ends_at", "prize_code", "prize_description"}
    assert editable_fields(None) == everything
    game = Game(status=GameStatus.SCHEDULED)
    assert editable_fields(game) == everything
    for status in (GameStatus.LOCKED, GameStatus.OPEN):
        game.status = status
        assert editable_fields(game) == {"ends_at", "prize_code", "prize_description"}
    game.status = GameStatus.FINISHED
    assert editable_fields(game) == set()  # no winner
    game.winner_id = uuid.uuid4()
    assert editable_fields(game, has_prize=False) == {"prize_code", "prize_description"}
    assert editable_fields(game, has_prize=True) == set()


async def test_edit_scheduled_word_recomputes_ranking(client, session, admin) -> None:
    game = await _game(session, GameStatus.SCHEDULED, starts_at=NOON + timedelta(days=1))
    response = await client.post(f"/admin/games/{game.id}", data=_form(secret_word="chá"))
    assert response.status_code == 303
    await session.refresh(game)
    assert game.secret_word == "chá"
    first = await session.scalar(
        select(WordRanking.word).where(WordRanking.game_id == game.id, WordRanking.rank == 1)
    )
    assert first == "chá"


async def test_active_game_only_changes_end_and_coupon(client, session, admin) -> None:
    game = await _game(session, GameStatus.OPEN)
    original_start = game.starts_at
    response = await client.post(
        f"/admin/games/{game.id}",
        data=_form(secret_word="chá", starts_at="2030-01-01T00:00", ends_at="2030-03-20T18:00"),
    )
    assert response.status_code == 303
    await session.refresh(game)
    assert game.secret_word == "café"  # ignored
    assert game.starts_at == original_start  # ignored
    assert game.ends_at == datetime(2030, 3, 20, 21, tzinfo=UTC)
    assert game.prize_code == "SPY-10"


async def test_extending_into_next_game_is_rejected(client, session, admin) -> None:
    game = await _game(session, GameStatus.OPEN)
    nxt = await _game(session, GameStatus.SCHEDULED, starts_at=NOON + timedelta(days=10))
    response = await client.post(f"/admin/games/{game.id}", data=_form(ends_at="2030-03-15T12:00"))
    assert response.status_code == 422
    assert f"jogo #{nxt.id}" in response.text


async def test_finished_game_is_read_only(client, session, admin) -> None:
    game = await _game(session, GameStatus.FINISHED, starts_at=NOON - timedelta(days=5))
    page = await client.get(f"/admin/games/{game.id}")
    assert "somente leitura" in page.text
    assert (await client.post(f"/admin/games/{game.id}", data=_form())).status_code == 409


async def test_coupon_registered_after_win_is_awarded(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN)
    ana = await make_profile(name="Ana")
    session.add(GameMember(game_id=game.id, user_id=ana.id, role=MemberRole.PLAYER))
    await session.flush()
    await submit_guess(session, game, ana.id, "café", NOON)
    assert await prizes_of(session, ana.id) == []
    assert "sem cupom" in (await client.get(f"/admin/games/{game.id}")).text

    response = await client.post(f"/admin/games/{game.id}", data=_form(prize_code="LATE-7"))
    assert response.status_code == 303
    [prize] = await prizes_of(session, ana.id)
    assert prize.code == "LATE-7"
    # Once handed over, the coupon cannot change.
    assert (await client.post(f"/admin/games/{game.id}", data=_form())).status_code == 409


# ---------- actions ----------


async def test_unlock_confirmation_shows_counts(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.LOCKED)
    ana = await make_profile(name="Ana")
    session.add(GameMember(game_id=game.id, user_id=ana.id, role=MemberRole.PLAYER))
    await session.flush()
    page = await client.get(f"/admin/games/{game.id}")
    assert "Liberar o jogo para 1 players e 0 watchers?" in page.text

    response = await client.post(f"/admin/games/{game.id}/unlock", headers={"HX-Request": "true"})
    assert response.headers["HX-Redirect"] == f"/admin/games/{game.id}?notice=unlocked"
    await session.refresh(game)
    assert (game.status, game.unlocked_by) == (GameStatus.OPEN, admin.id)
    assert (await client.post(f"/admin/games/{game.id}/unlock")).status_code == 409


async def test_finish_active_game(client, session, admin) -> None:
    game = await _game(session, GameStatus.OPEN)
    response = await client.post(f"/admin/games/{game.id}/finish")
    assert response.status_code == 303
    await session.refresh(game)
    assert game.status == GameStatus.FINISHED and game.winner_id is None
    assert (await client.post(f"/admin/games/{game.id}/finish")).status_code == 409


async def test_delete_only_scheduled(client, session, admin) -> None:
    active = await _game(session, GameStatus.OPEN)
    assert (await client.delete(f"/admin/games/{active.id}")).status_code == 409

    scheduled = await _game(session, GameStatus.SCHEDULED, starts_at=NOON + timedelta(days=10))
    response = await client.delete(f"/admin/games/{scheduled.id}", headers={"HX-Request": "true"})
    assert response.headers["HX-Redirect"] == "/admin?notice=deleted"
    assert await session.get(Game, scheduled.id) is None


async def test_unknown_game_is_404(client, admin) -> None:
    assert (await client.get("/admin/games/999999")).status_code == 404


# ---------- members and watchers (T-30) ----------


async def _member(session, make_profile, game, name, role=MemberRole.PLAYER) -> Profile:
    profile = await make_profile(name=name)
    session.add(GameMember(game_id=game.id, user_id=profile.id, role=role))
    await session.flush()
    return profile


async def test_members_page_lists_roles_and_guesses(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN)
    ana = await _member(session, make_profile, game, "Ana")
    bia = await _member(session, make_profile, game, "Bia")
    await _member(session, make_profile, game, "Caio", MemberRole.WATCHER)
    await submit_guess(session, game, ana.id, "chá", NOON)

    page = await client.get(f"/admin/games/{game.id}/members")
    text = page.text
    assert text.index("Caio") < text.index("Ana")  # watchers first
    assert "Já palpitou" in text
    assert f"/members/{bia.id}/role" in text
    assert f"/members/{ana.id}/role" not in text


async def test_player_becomes_watcher(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.LOCKED)
    bia = await _member(session, make_profile, game, "Bia")
    response = await client.post(
        f"/admin/games/{game.id}/members/{bia.id}/role",
        data={"role": "watcher"},
        headers={"HX-Request": "true"},
    )
    assert response.headers["HX-Redirect"] == f"/admin/games/{game.id}/members?notice=watcher"
    member = await session.get(GameMember, (game.id, bia.id))
    await session.refresh(member)
    assert member.role == MemberRole.WATCHER

    # The new watcher sees the secret and the hint form, not the guess form.
    client.cookies.set(ACCESS_COOKIE, mint_token(bia.id, bia.email))
    home = await client.get("/")
    assert "Palavra secreta" in home.text and 'hx-post="/guesses"' not in home.text


async def test_player_with_guesses_stays_player(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN)
    ana = await _member(session, make_profile, game, "Ana")
    await submit_guess(session, game, ana.id, "chá", NOON)
    response = await client.post(f"/admin/games/{game.id}/members/{ana.id}/role")
    assert response.status_code == 409
    assert "já deu palpites" in response.text
    member = await session.get(GameMember, (game.id, ana.id))
    await session.refresh(member)
    assert member.role == MemberRole.PLAYER


async def test_watcher_never_goes_back(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN)
    caio = await _member(session, make_profile, game, "Caio", MemberRole.WATCHER)
    url = f"/admin/games/{game.id}/members/{caio.id}/role"
    assert (await client.post(url, data={"role": "player"})).status_code == 409
    assert (await client.post(url, data={"role": "watcher"})).status_code == 409


async def test_no_role_changes_in_finished_games(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.FINISHED, starts_at=NOON - timedelta(days=5))
    bia = await _member(session, make_profile, game, "Bia")
    response = await client.post(f"/admin/games/{game.id}/members/{bia.id}/role")
    assert response.status_code == 409


async def test_non_member_cannot_be_promoted(client, session, admin, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN)
    stranger = await make_profile(name="Zé")
    response = await client.post(f"/admin/games/{game.id}/members/{stranger.id}/role")
    assert response.status_code == 409


async def test_members_page_needs_admin(client, session, make_profile) -> None:
    game = await _game(session, GameStatus.OPEN)
    player = await _member(session, make_profile, game, "Ana")
    client.cookies.set(ACCESS_COOKIE, mint_token(player.id, player.email))
    assert (await client.get(f"/admin/games/{game.id}/members")).status_code == 403
    url = f"/admin/games/{game.id}/members/{player.id}/role"
    assert (await client.post(url)).status_code == 403
