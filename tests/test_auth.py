from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.auth.models import Profile
from app.auth.session import ACCESS_COOKIE, PKCE_COOKIE, REFRESH_COOKIE
from app.core.config import get_settings
from app.games.models import GameMember, GameStatus, MemberRole
from tests.conftest import mint_token


async def _login(client, auth_client, user) -> None:
    """Run the full OAuth round trip for `user` = (user_id, email)."""
    auth_client.codes["good-code"] = user
    await client.get("/auth/login")
    response = await client.get("/auth/callback", params={"code": "good-code"})
    assert response.status_code == 302 and response.headers["location"] == "/"


async def test_visitor_sees_login_button(client) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "Entrar com Google" in response.text


async def test_login_redirects_to_google_with_pkce(client) -> None:
    response = await client.get("/auth/login")

    assert response.status_code == 302
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["provider"] == ["google"]
    assert query["code_challenge"][0]
    assert PKCE_COOKIE in response.cookies


async def test_callback_without_pkce_cookie_fails(client) -> None:
    response = await client.get("/auth/callback", params={"code": "anything"})
    assert response.headers["location"] == "/?login=erro"


async def test_callback_with_bad_code_fails(client) -> None:
    await client.get("/auth/login")
    response = await client.get("/auth/callback", params={"code": "bad-code"})
    assert response.headers["location"] == "/?login=erro"
    assert ACCESS_COOKIE not in response.cookies


async def test_first_login_creates_profile_and_session(
    client, auth_client, auth_user, session
) -> None:
    user_id, email = await auth_user()

    await _login(client, auth_client, (user_id, email))

    profile = await session.get(Profile, user_id)
    assert profile is not None
    assert profile.email == email
    assert profile.display_name == "Tester"
    assert profile.avatar_url == "https://example.com/a.png"
    assert not profile.is_admin
    assert client.cookies.get(ACCESS_COOKIE)
    assert client.cookies.get(REFRESH_COOKIE)

    home = await client.get("/")
    assert "Tester" in home.text and "Sair" in home.text


async def test_login_joins_active_game_as_player(
    client, auth_client, auth_user, make_game, session, clock
) -> None:
    game = await make_game(status=GameStatus.LOCKED, starts_at=clock.now - timedelta(hours=1))
    user = await auth_user()

    await _login(client, auth_client, user)

    member = await session.get(GameMember, (game.id, user[0]))
    assert member is not None and member.role == MemberRole.PLAYER


async def test_login_without_active_game_creates_no_membership(
    client, auth_client, auth_user, session
) -> None:
    user = await auth_user()
    await _login(client, auth_client, user)
    profile = await session.get(Profile, user[0])
    assert profile is not None


async def test_admin_email_becomes_admin_and_does_not_join(
    client, auth_client, auth_user, make_game, session, clock, monkeypatch
) -> None:
    game = await make_game(status=GameStatus.LOCKED, starts_at=clock.now - timedelta(hours=1))
    user_id, email = await auth_user("boss@example.com")
    monkeypatch.setattr(get_settings(), "admin_emails", ["boss@example.com"])

    await _login(client, auth_client, (user_id, email))

    profile = await session.get(Profile, user_id)
    assert profile is not None and profile.is_admin
    assert await session.get(GameMember, (game.id, user_id)) is None


async def test_home_joins_game_started_after_login(
    client, auth_client, auth_user, make_game, session, clock
) -> None:
    user = await auth_user()
    await _login(client, auth_client, user)
    game = await make_game(starts_at=clock.now + timedelta(days=1))

    clock.now += timedelta(days=2)
    await client.get("/")

    member = await session.get(GameMember, (game.id, user[0]))
    assert member is not None and member.role == MemberRole.PLAYER


async def test_existing_watcher_is_not_downgraded_on_login(
    client, auth_client, auth_user, make_game, session, clock
) -> None:
    game = await make_game(status=GameStatus.LOCKED, starts_at=clock.now - timedelta(hours=1))
    user = await auth_user()
    await _login(client, auth_client, user)
    member = await session.get(GameMember, (game.id, user[0]))
    member.role = MemberRole.WATCHER
    await session.flush()

    await _login(client, auth_client, user)

    await session.refresh(member)
    assert member.role == MemberRole.WATCHER


@pytest.mark.parametrize(
    "token",
    ["not-a-jwt", "wrong-key", "expired"],
)
async def test_invalid_token_is_treated_as_visitor(client, auth_user, token) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    user_id, email = await auth_user()
    if token == "wrong-key":
        token = mint_token(user_id, email, key=ec.generate_private_key(ec.SECP256R1()))
    elif token == "expired":
        token = mint_token(user_id, email, expires_in=-10)

    client.cookies.set(ACCESS_COOKIE, token)
    response = await client.get("/")
    assert "Entrar com Google" in response.text


async def test_expired_access_token_is_refreshed(client, auth_client, auth_user) -> None:
    user = await auth_user()
    await _login(client, auth_client, user)
    client.cookies.set(ACCESS_COOKIE, mint_token(*user, expires_in=-10))
    old_refresh = client.cookies.get(REFRESH_COOKIE)

    response = await client.get("/")

    assert "Sair" in response.text
    assert client.cookies.get(REFRESH_COOKIE) != old_refresh


async def test_bad_refresh_token_logs_out(client, auth_user) -> None:
    user = await auth_user()
    client.cookies.set(ACCESS_COOKIE, mint_token(*user, expires_in=-10))
    client.cookies.set(REFRESH_COOKIE, "revoked")

    response = await client.get("/")

    assert "Entrar com Google" in response.text
    assert not response.cookies.get(REFRESH_COOKIE)


async def test_logout_clears_session(client, auth_client, auth_user) -> None:
    user = await auth_user()
    await _login(client, auth_client, user)

    response = await client.post("/auth/logout")

    assert response.status_code == 303
    assert auth_client.signed_out
    assert not client.cookies.get(ACCESS_COOKIE)
    home = await client.get("/")
    assert "Entrar com Google" in home.text
