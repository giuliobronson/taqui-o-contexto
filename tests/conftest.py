import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession, create_async_engine

from app.auth.models import Profile
from app.core.config import get_settings
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
