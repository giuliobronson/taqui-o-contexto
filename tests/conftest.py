import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.auth.models import Profile
from app.auth.supabase import AuthError, AuthSession
from app.auth.tokens import TokenVerifier
from app.core.clock import utcnow
from app.core.config import get_settings
from app.core.db import get_session
from app.games.models import Game, GameStatus


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(get_settings().database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def connection(engine) -> AsyncIterator[AsyncConnection]:
    """Each test runs inside a transaction that is rolled back at the end."""
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()


@pytest.fixture
async def session(connection: AsyncConnection) -> AsyncIterator[AsyncSession]:
    # Commits inside the code under test become savepoints of the outer transaction.
    session = AsyncSession(
        bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
    )
    yield session
    await session.close()


@pytest.fixture
def make_profile(session: AsyncSession):
    async def _make(*, is_admin: bool = False, name: str = "Tester") -> Profile:
        user_id = uuid.uuid4()
        email = f"{user_id.hex[:8]}@example.com"
        await session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
            {"id": user_id, "email": email},
        )
        profile = Profile(id=user_id, email=email, display_name=name, is_admin=is_admin)
        session.add(profile)
        await session.flush()
        return profile

    return _make


@pytest.fixture
def make_game(session: AsyncSession):
    async def _make(
        *,
        status: GameStatus = GameStatus.SCHEDULED,
        starts_at: datetime | None = None,
        ends_at: datetime | None = None,
        secret_word: str = "café",
        model_version: str = "test",
    ) -> Game:
        now = datetime.now(UTC)
        starts_at = starts_at or now - timedelta(hours=1)
        game = Game(
            secret_word=secret_word,
            model_version=model_version,
            status=status,
            starts_at=starts_at,
            ends_at=ends_at or starts_at + timedelta(days=7),
            unlocked_at=now if status in (GameStatus.OPEN, GameStatus.FINISHED) else None,
            finished_at=now if status == GameStatus.FINISHED else None,
        )
        session.add(game)
        await session.flush()
        return game

    return _make


# ---------------------------------------------------------------------------
# Auth: a fake Supabase that signs tokens with a key generated for the tests.
# ---------------------------------------------------------------------------


ISSUER = "http://supabase.test/auth/v1"
_SIGNING_KEY = ec.generate_private_key(ec.SECP256R1())
_KID = "test-key"


def mint_token(
    user_id: uuid.UUID,
    email: str,
    *,
    name: str = "Tester",
    expires_in: int = 3600,
    key=_SIGNING_KEY,
) -> str:
    now = int(time.time())
    claims = {
        "sub": str(user_id),
        "email": email,
        "aud": "authenticated",
        "iss": ISSUER,
        "iat": now,
        "exp": now + expires_in,
        "role": "authenticated",
        "user_metadata": {"full_name": name, "avatar_url": "https://example.com/a.png"},
    }
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": _KID})


@dataclass
class FakeAuthClient:
    """Stands in for SupabaseAuthClient. `codes` maps OAuth codes to (user_id, email)."""

    codes: dict[str, tuple[uuid.UUID, str]] = field(default_factory=dict)
    refresh_tokens: dict[str, tuple[uuid.UUID, str]] = field(default_factory=dict)
    signed_out: list[str] = field(default_factory=list)

    def authorize_url(self, *, provider: str, redirect_to: str, code_challenge: str) -> str:
        return f"{ISSUER}/authorize?provider={provider}&code_challenge={code_challenge}"

    def _session_for(self, user: tuple[uuid.UUID, str]) -> AuthSession:
        refresh = uuid.uuid4().hex
        self.refresh_tokens[refresh] = user
        return AuthSession(mint_token(*user), refresh, 3600)

    async def exchange_code(self, code: str, code_verifier: str) -> AuthSession:
        if code not in self.codes:
            raise AuthError("bad code")
        return self._session_for(self.codes[code])

    async def refresh(self, refresh_token: str) -> AuthSession:
        user = self.refresh_tokens.pop(refresh_token, None)
        if user is None:
            raise AuthError("bad refresh token")
        return self._session_for(user)

    async def sign_out(self, access_token: str) -> None:
        self.signed_out.append(access_token)

    async def fetch_jwks(self) -> dict[str, Any]:
        public = jwt.algorithms.ECAlgorithm.to_jwk(_SIGNING_KEY.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": _KID, "alg": "ES256", "use": "sig"}]}


@dataclass
class Clock:
    now: datetime = field(default_factory=lambda: datetime(2030, 3, 1, 12, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def auth_client() -> FakeAuthClient:
    return FakeAuthClient()


@pytest.fixture
def configure_app(session, auth_client, clock):
    """Point an app at the test database session, fake Supabase and fake clock."""

    def _configure(app: FastAPI) -> FastAPI:
        async def _session():
            yield session

        app.dependency_overrides[get_session] = _session
        app.dependency_overrides[utcnow] = clock
        app.state.auth_client = auth_client
        app.state.token_verifier = TokenVerifier(auth_client.fetch_jwks, issuer=ISSUER)
        return app

    return _configure


@pytest.fixture
async def client(configure_app):
    from app.main import app

    configure_app(app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def auth_user(session):
    """Create a Supabase auth user (no profile yet) and return (user_id, email)."""

    async def _make(email: str | None = None) -> tuple[uuid.UUID, str]:
        user_id = uuid.uuid4()
        email = email or f"{user_id.hex[:8]}@example.com"
        await session.execute(
            text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
            {"id": user_id, "email": email},
        )
        return user_id, email

    return _make
