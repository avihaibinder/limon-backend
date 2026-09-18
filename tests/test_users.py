from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.recording import Recording
from app.models.tag import Tag
from app.models.user import User
from app.services import storage, supabase_admin, task_queue
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


async def test_delete_me_then_next_request_reprovisions_same_id(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _delete_auth(_sub: str) -> None:
        return None

    monkeypatch.setattr(supabase_admin, "delete_auth_user", _delete_auth)
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
    cancelled: list[tuple[set[str], set[str]]] = []
    deleted_audio: list[tuple[str, set[str]]] = []

    async def _record(sub: str) -> None:
        deleted.append(sub)

    async def _cancel(*, event_ids: set[str], recording_ids: set[str]) -> None:
        cancelled.append((event_ids, recording_ids))

    async def _delete_audio(user_id: str, keys: set[str]) -> None:
        deleted_audio.append((user_id, keys))

    monkeypatch.setattr(supabase_admin, "delete_auth_user", _record)
    monkeypatch.setattr(task_queue, "cancel_account_tasks", _cancel)
    monkeypatch.setattr(storage, "delete_user_audio", _delete_audio)

    me = (await client.get(ME_URL)).json()
    async with session_factory() as session:
        recording = Recording(
            user_id=me["id"],
            storage_key=f"v0/{me['id']}/owned.m4a",
            content_type="audio/mp4",
        )
        session.add(recording)
        await session.flush()
        owned_event = Event(
            user_id=me["id"],
            type="audio",
            occurred_at=datetime.now(UTC),
            recording_id=recording.id,
        )
        session.add(owned_event)

        other = User(id="other-user", provider="google", email="other@example.com")
        session.add(other)
        await session.flush()
        other_recording = Recording(
            user_id=other.id,
            storage_key=f"v0/{other.id}/other.m4a",
            content_type="audio/mp4",
        )
        other_tag = Tag(user_id=other.id, name="private")
        session.add_all([other_recording, other_tag])
        await session.flush()
        other_event = Event(
            user_id=other.id,
            type="audio",
            occurred_at=datetime.now(UTC),
            recording_id=other_recording.id,
        )
        session.add(other_event)
        await session.commit()
        owned_ids = (owned_event.id, recording.id)
        other_ids = (other_event.id, other_recording.id, other_tag.id)

    response = await client.delete(ME_URL)
    assert response.status_code == 204

    # Both halves: the Supabase auth identity was deleted, and our row cascaded
    # away the user's events.
    assert deleted == [me["id"]]
    assert cancelled == [({owned_ids[0]}, {owned_ids[1]})]
    assert deleted_audio == [(me["id"], {f"v0/{me['id']}/owned.m4a"})]
    async with session_factory() as session:
        remaining = await session.scalar(
            select(func.count()).select_from(Event).where(Event.user_id == me["id"])
        )
        assert remaining == 0
        assert await session.get(Recording, owned_ids[1]) is None
        assert await session.get(User, "other-user") is not None
        assert await session.get(Event, other_ids[0]) is not None
        assert await session.get(Recording, other_ids[1]) is not None
        assert await session.get(Tag, other_ids[2]) is not None


async def test_delete_me_external_cleanup_failure_keeps_auth_and_database(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth_calls: list[str] = []

    async def _auth(sub: str) -> None:
        auth_calls.append(sub)

    async def _storage_failure(_user_id: str, _keys: set[str]) -> None:
        raise storage.AudioCleanupError("storage unavailable")

    monkeypatch.setattr(supabase_admin, "delete_auth_user", _auth)
    monkeypatch.setattr(storage, "delete_user_audio", _storage_failure)

    me = (await client.get(ME_URL)).json()
    response = await client.delete(ME_URL)

    assert response.status_code == 502
    assert auth_calls == []
    async with session_factory() as session:
        assert await session.get(User, me["id"]) is not None


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
