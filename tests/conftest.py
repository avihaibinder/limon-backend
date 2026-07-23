from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.auth import get_current_user
from app.db.base import Base
from app.db.session import create_engine, get_db_session
from app.dependencies import SessionDep
from app.main import app
from app.models.user import User
from app.services import users as users_service

# The Supabase identity every authenticated test request acts as. `sub` is the
# user id (decision 15), so it is also the users.id row provisioned for tests.
TEST_IDENTITY = {
    "sub": "test-subject",
    "provider": "google",
    "email": "lemon@example.com",
    "display_name": "Lemon",
}


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Isolated in-memory database, shared by the app and by tests that need
    to seed or inspect rows directly (e.g. another user's data)."""
    engine = create_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # share the single in-memory DB across connections
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield async_sessionmaker(engine, expire_on_commit=False)

    await engine.dispose()


@pytest.fixture
async def client(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncClient]:
    """HTTP client with every request authenticated as TEST_IDENTITY.

    JWT verification is bypassed (it gets real coverage in test_auth.py), but
    resolution still runs through the production JIT-provisioning path in the
    request's own session.
    """

    async def override_get_db_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    async def override_get_current_user(session: SessionDep) -> User:
        return await users_service.get_or_create_user(session, **TEST_IDENTITY)

    app.dependency_overrides[get_db_session] = override_get_db_session
    app.dependency_overrides[get_current_user] = override_get_current_user
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client

    app.dependency_overrides.clear()


@pytest.fixture
async def anon_client(client: AsyncClient) -> AsyncClient:
    """Same app and database as `client`, but requests carry no identity —
    tokens (or their absence) hit the real verification code."""
    del app.dependency_overrides[get_current_user]
    return client
