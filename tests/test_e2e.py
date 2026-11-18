"""End to end, through HTTP only (T-36): admin creates a game → it locks at
starts_at → people join → admin picks a watcher and unlocks → guesses over
several days → the winning guess → coupon → finished game stays visible.

The clock is injected, so "the next day" is one assignment away.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.auth.models import Profile
from app.auth.session import ACCESS_COOKIE
from app.core.config import get_settings
from app.games.models import Game, Vocabulary, WordForm
from tests.conftest import mint_token

# Game from Mar 2, 09:00 to Mar 9, 09:00 in São Paulo (12:00 UTC).
START = datetime(2030, 3, 2, 12, tzinfo=UTC)
CODE = "SPY-FAMILY-20"


@pytest.fixture(autouse=True)
async def vocabulary(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "vocabulary_version", "test")
    words = {
        "spy family": [1.0, 0.0, 0.0],
        "espião": [0.9, 0.1, 0.0],
        "família": [0.7, 0.3, 0.0],
        "anime": [0.6, 0.2, 0.2],
        "mesa": [0.0, 0.0, 1.0],
    }
    session.add_all(Vocabulary(model_version="test", word=w, embedding=v) for w, v in words.items())
    await session.flush()
    session.add(WordForm(model_version="test", form="spy x family", word="spy family"))
    await session.flush()


class Browser:
    """One person using the site: switches the session cookie before each request."""

    def __init__(self, client, profile: Profile) -> None:
        self.client = client
        self.token = mint_token(profile.id, profile.email, name=profile.display_name)

    def _use(self) -> None:
        self.client.cookies.set(ACCESS_COOKIE, self.token)

    async def get(self, url: str, **kwargs):
        self._use()
        return await self.client.get(url, **kwargs)

    async def post(self, url: str, data: dict | None = None, htmx: bool = True):
        self._use()
        headers = {"HX-Request": "true"} if htmx else {}
        return await self.client.post(url, data=data or {}, headers=headers)


async def test_full_game(client, session, make_profile, clock) -> None:
    admin = Browser(client, await make_profile(is_admin=True, name="Admin"))
    ana_profile = await make_profile(name="Ana")
    ana = Browser(client, ana_profile)
    bia = Browser(client, await make_profile(name="Bia"))
    caio_profile = await make_profile(name="Caio")
    caio = Browser(client, caio_profile)

    # 1. The admin creates the game the day before.
    clock.now = START - timedelta(days=1)
    created = await admin.post(
        "/admin/games/new",
        {
            "secret_word": "Spy x Family",
            "starts_at": "2030-03-02T09:00",
            "ends_at": "2030-03-09T09:00",
            "prize_code": CODE,
            "prize_description": "20% de desconto",
        },
        htmx=False,
    )
    assert created.status_code == 303
    game = (await session.scalars(select(Game))).one()
    assert game.secret_word == "spy family"

    home = await ana.get("/")
    assert "Nenhum jogo no momento" in home.text
    assert "02/03 às 09:00" in home.text  # next game announced

    # 2. At starts_at the game locks; whoever visits joins as a player.
    clock.now = START + timedelta(minutes=5)
    for person in (ana, bia, caio):
        page = await person.get("/")
        assert "Aguardando liberação" in page.text
        assert "O jogo ainda não foi liberado" in page.text
    locked = await ana.post("/guesses", {"word": "mesa"})
    assert "está na posição" not in locked.text

    # 3. The admin turns Caio into a watcher and unlocks the game.
    members = await admin.get(f"/admin/games/{game.id}/members")
    assert all(name in members.text for name in ("Ana", "Bia", "Caio"))
    promoted = await admin.post(f"/admin/games/{game.id}/members/{caio_profile.id}/role")
    assert promoted.status_code == 200
    detail = await admin.get(f"/admin/games/{game.id}")
    assert "Liberar o jogo para 2 players e 1 watchers?" in detail.text
    await admin.post(f"/admin/games/{game.id}/unlock")

    caio_home = await caio.get("/")
    assert "Palavra secreta" in caio_home.text and 'hx-post="/hints"' in caio_home.text
    assert 'hx-post="/guesses"' not in caio_home.text
    assert "spy family" not in (await bia.get("/")).text

    # 4. Day 1: one guess each; Caio writes hints (one of them gives the word away).
    first = await ana.post("/guesses", {"word": "Mesa"})
    assert "“mesa” está na posição 5" in first.text
    again = await ana.post("/guesses", {"word": "anime"})
    assert "está na posição" not in again.text and "Volte amanhã" in again.text

    unknown = await bia.post("/guesses", {"word": "abacaxi"})
    assert "Não conhecemos" in unknown.text  # does not use up the day
    second = await bia.post("/guesses", {"word": "família"})
    assert "está na posição 3" in second.text

    revealing = await caio.post("/hints", {"body": "É o Spy×Family!"})
    assert "não pode conter a palavra secreta" in revealing.text
    hint = await caio.post("/hints", {"body": "Um pai que esconde a profissão"})
    assert "Dica publicada" in hint.text

    ranking = await bia.get("/ranking", params={"status": "open"})
    assert ranking.text.index("família") < ranking.text.index("mesa")
    assert "Um pai que esconde a profissão" in (await ana.get("/hints")).text

    # 5. Day 2 (midnight in São Paulo is 03:00 UTC): Ana guesses the word.
    clock.now = datetime(2030, 3, 3, 3, 1, tzinfo=UTC)
    win = await ana.post("/guesses", {"word": "spy x family"})
    assert "Você acertou!" in win.text
    assert win.headers["HX-Redirect"] == "/?celebrate=1"

    celebration = await ana.get("/", params={"celebrate": "1"})
    assert "Parabéns, você acertou!" in celebration.text and CODE in celebration.text
    assert CODE in (await ana.get("/prizes/me")).text

    # 6. Everyone else sees the result, never the code; nobody can play anymore.
    bia_home = await bia.get("/")
    assert "A palavra era" in bia_home.text and "Ana" in bia_home.text
    assert CODE not in bia_home.text and "prize-modal" not in bia_home.text
    late = await bia.post("/guesses", {"word": "espião"})
    assert "está na posição" not in late.text
    assert (await caio.post("/hints", {"body": "tarde"})).status_code == 409  # no active game

    # 7. The finished game stays public, with its ranking and hints.
    client.cookies.clear()
    public = await client.get(f"/games/{game.id}")
    assert public.status_code == 200
    assert "spy family" in public.text and "Um pai que esconde a profissão" in public.text
    assert CODE not in public.text

    admin_list = await admin.get("/admin")
    assert "Encerrado" in admin_list.text and "Ana" in admin_list.text
    assert "cupom entregue" in (await admin.get(f"/admin/games/{game.id}")).text
