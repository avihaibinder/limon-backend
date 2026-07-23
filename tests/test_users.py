import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.services import supabase_admin
from app.services.supabase_admin import SupabaseAdminError
from tests.conftest import TEST_IDENTITY

ME_URL = "/api/v1/users/me"
EVENTS_URL = "/api/v1/events"


async def test_get_me_provisions_and_returns_profile(client: AsyncClient) -> None:
    response = await client.get(ME_URL)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["id"] == TEST_IDENTITY["sub"]  # id is the Supabase sub
    assert body["provider"] == TEST_IDENTITY["provider"]
    assert body["email"] == TEST_IDENTITY["email"]
    assert body["display_name"] == TEST_IDENTITY["display_name"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_me_is_stable_across_requests(client: AsyncClient) -> None:
    first = (await client.get(ME_URL)).json()
    second = (await client.get(ME_URL)).json()

    assert first["id"] == second["id"]


async def test_update_me_profile_fields(client: AsyncClient) -> None:
    response = await client.patch(ME_URL, json={"display_name": "Renamed"})
    assert response.status_code == 200
    body = response.json()

    assert body["display_name"] == "Renamed"
    assert body["email"] == TEST_IDENTITY["email"]

    # The edit sticks — JIT provisioning must not overwrite it on the next request.
    assert (await client.get(ME_URL)).json()["display_name"] == "Renamed"


async def test_update_me_rejects_invalid_email(client: AsyncClient) -> None:
    response = await client.patch(ME_URL, json={"email": "not-an-email"})
    assert response.status_code == 422


async def test_delete_me_then_next_request_reprovisions_same_id(client: AsyncClient) -> None:
    original = (await client.get(ME_URL)).json()

    response = await client.delete(ME_URL)
    assert response.status_code == 204

    # The id is the Supabase sub, so signing in again re-provisions the same id
    # (a fresh row, but keyed by the same identity).
    recreated = (await client.get(ME_URL)).json()
    assert recreated["id"] == original["id"] == TEST_IDENTITY["sub"]


async def test_delete_me_removes_the_supabase_auth_identity_and_cascades(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Supabase isn't configured in tests, so record the admin call instead of
    # letting it hit the network (its own wiring is covered in test_supabase_admin).
    deleted: list[str] = []

    async def _record(sub: str) -> None:
        deleted.append(sub)

    monkeypatch.setattr(supabase_admin, "delete_auth_user", _record)

    me = (await client.get(ME_URL)).json()
    await client.post(
        EVENTS_URL,
        json={"type": "text", "title": "keep me?", "clientCreatedAt": 1_751_600_000_000},
    )

    response = await client.delete(ME_URL)
    assert response.status_code == 204

    # Both halves: the Supabase auth identity was deleted, and our row cascaded
    # away the user's events.
    assert deleted == [me["id"]]
    async with session_factory() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(Event).where(Event.user_id == me["id"])
        )
        assert remaining == 0


async def test_delete_me_502s_and_keeps_data_when_supabase_delete_fails(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(sub: str) -> None:
        raise SupabaseAdminError("upstream down")

    monkeypatch.setattr(supabase_admin, "delete_auth_user", _boom)

    me = (await client.get(ME_URL)).json()
    await client.post(
        EVENTS_URL,
        json={"type": "text", "title": "survivor", "clientCreatedAt": 1_751_600_000_000},
    )

    response = await client.delete(ME_URL)
    assert response.status_code == 502

    # Nothing local was removed: a failed delete is fully retryable.
    async with session_factory() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(Event).where(Event.user_id == me["id"])
        )
        assert remaining == 1
