"""Guards from core.security and games.dependencies, on a small app built for the test."""

from datetime import timedelta

import httpx
import pytest
from fastapi import FastAPI

from app.auth.session import ACCESS_COOKIE
from app.core.security import AdminUser, CurrentUser
from app.games.dependencies import PlayerMembership, WatcherMembership
from app.games.models import GameMember, GameStatus, MemberRole
from tests.conftest import mint_token


def _build_app() -> FastAPI:
    app = FastAPI()

    @app.get("/user")
    async def user_route(user: CurrentUser) -> dict:
        return {"ok": True}

    @app.get("/admin")
    async def admin_route(user: AdminUser) -> dict:
        return {"ok": True}

    @app.get("/player")
    async def player_route(membership: PlayerMembership) -> dict:
        return {"game": membership.game.id}

    @app.get("/watcher")
    async def watcher_route(membership: WatcherMembership) -> dict:
        return {"game": membership.game.id}

    return app


@pytest.fixture
async def guarded(configure_app):
    app = configure_app(_build_app())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture
def login_as(guarded, make_profile):
    async def _login(*, is_admin: bool = False):
        profile = await make_profile(is_admin=is_admin)
        guarded.cookies.set(ACCESS_COOKIE, mint_token(profile.id, profile.email))
        return profile

    return _login


@pytest.fixture
async def active_game(make_game, clock):
    return await make_game(status=GameStatus.OPEN, starts_at=clock.now - timedelta(hours=1))


async def _set_role(session, game, profile, role: MemberRole) -> None:
    session.add(GameMember(game_id=game.id, user_id=profile.id, role=role))
    await session.flush()


@pytest.mark.parametrize("path", ["/user", "/admin", "/player", "/watcher"])
async def test_visitor_gets_401(guarded, path) -> None:
    assert (await guarded.get(path)).status_code == 401


async def test_admin_route(guarded, login_as) -> None:
    await login_as()
    assert (await guarded.get("/admin")).status_code == 403
    await login_as(is_admin=True)
    assert (await guarded.get("/admin")).status_code == 200


async def test_player_route_without_active_game(guarded, login_as) -> None:
    await login_as()
    assert (await guarded.get("/player")).status_code == 409


async def test_new_user_is_player_in_active_game(guarded, login_as, active_game) -> None:
    await login_as()
    assert (await guarded.get("/player")).status_code == 200
    assert (await guarded.get("/watcher")).status_code == 403


async def test_watcher_cannot_use_player_route(guarded, login_as, active_game, session) -> None:
    profile = await login_as()
    await _set_role(session, active_game, profile, MemberRole.WATCHER)
    assert (await guarded.get("/player")).status_code == 403
    assert (await guarded.get("/watcher")).status_code == 200


async def test_admin_is_not_a_game_member(guarded, login_as, active_game) -> None:
    await login_as(is_admin=True)
    assert (await guarded.get("/player")).status_code == 403
    assert (await guarded.get("/watcher")).status_code == 403
