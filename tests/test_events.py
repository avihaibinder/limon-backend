from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.recording import Recording
from app.models.user import User
from app.services import storage as storage_service

EVENTS_URL = "/api/v1/events"
ME_URL = "/api/v1/users/me"

# Epoch-ms capture time the client sends; the backend stores it as occurred_at.
CLIENT_CREATED_AT_MS = 1_751_600_000_000

TEXT_EVENT = {
    "type": "text",
    "title": "Feeling a bit overwhelmed today",
    "description": "Logged after work",
    "tagIds": ["mood", "work"],
    "clientCreatedAt": CLIENT_CREATED_AT_MS,
    "clientEventId": "client-text-1",
}


async def _create(client: AsyncClient, **overrides) -> dict:
    """POST /events and return the create envelope ({event, recordId, signedUrl})."""
    response = await client.post(EVENTS_URL, json={**TEXT_EVENT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def fake_presign(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Stub the GCS signer so audio create needs no bucket/credentials. Each call
    returns a distinct URL so a re-minted (fresh) URL is observable; the recorded
    (object_key, content_type) pairs let tests assert the key that was signed."""
    calls: list[tuple[str, str]] = []

    def _presign(object_key: str, content_type: str) -> str:
        calls.append((object_key, content_type))
        return f"https://storage.googleapis.com/signed/{object_key}?sig={len(calls)}"

    monkeypatch.setattr(storage_service, "presign_put", _presign)
    return calls


async def test_create_text_event_returns_envelope_without_recording(client: AsyncClient) -> None:
    body = await _create(client)

    assert body["recordId"] is None
    assert body["signedUrl"] is None

    event = body["event"]
    assert event["type"] == "text"
    assert event["title"] == TEXT_EVENT["title"]
    assert event["description"] == TEXT_EVENT["description"]
    assert event["tagIds"] == TEXT_EVENT["tagIds"]
    assert event["recordId"] is None
    assert event["id"]
    assert event["createdAt"]
    assert event["updatedAt"]


async def test_occurred_at_is_derived_from_client_created_at(client: AsyncClient) -> None:
    event = (await _create(client))["event"]

    expected = datetime.fromtimestamp(CLIENT_CREATED_AT_MS / 1000, tz=UTC)
    got = datetime.fromisoformat(event["occurredAt"])
    # SQLite reads DateTime back naive; Postgres carries the +00:00 offset. Treat
    # a naive readback as UTC so the assertion holds on both.
    if got.tzinfo is None:
        got = got.replace(tzinfo=UTC)
    assert got == expected


async def test_lemon_is_a_text_event_with_null_title_and_body(client: AsyncClient) -> None:
    # The former "press the lemon" quick capture: a text event with no title/body.
    body = await _create(
        client, title=None, description=None, tagIds=[], clientEventId="client-lemon"
    )
    event = body["event"]

    assert event["type"] == "text"
    assert event["title"] is None
    assert event["description"] is None
    assert body["recordId"] is None
    assert body["signedUrl"] is None


async def test_create_audio_event_creates_recording_and_signed_url(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_presign: list[tuple[str, str]],
) -> None:
    me = (await client.get(ME_URL)).json()

    body = await _create(client, type="audio", description=None, clientEventId="client-audio-1")
    event = body["event"]
    record_id = body["recordId"]

    assert event["type"] == "audio"
    assert record_id is not None
    assert event["recordId"] == record_id
    assert event["description"] is None  # transcript arrives later, over Realtime

    expected_key = f"v0/{me['id']}/{record_id}.m4a"
    assert body["signedUrl"] == f"https://storage.googleapis.com/signed/{expected_key}?sig=1"
    assert fake_presign == [(expected_key, "audio/mp4")]

    async with session_factory() as session:
        recording = await session.get(Recording, record_id)
        assert recording is not None
        assert recording.user_id == me["id"]
        assert recording.storage_key == expected_key
        assert recording.content_type == "audio/mp4"
        assert recording.state == "pending"


async def test_repeat_client_event_id_is_idempotent_with_fresh_signed_url(
    client: AsyncClient,
    session_factory: async_sessionmaker[AsyncSession],
    fake_presign: list[tuple[str, str]],
) -> None:
    first = await _create(client, type="audio", description=None, clientEventId="dup-audio")
    second = await _create(client, type="audio", description=None, clientEventId="dup-audio")

    # Same event and recording, never a duplicate.
    assert second["event"]["id"] == first["event"]["id"]
    assert second["recordId"] == first["recordId"]
    # ...but a freshly minted URL (the old one may have expired).
    assert second["signedUrl"] != first["signedUrl"]
    assert len(fake_presign) == 2

    async with session_factory() as session:
        events = await session.scalar(select(func.count()).select_from(Event))
        recordings = await session.scalar(select(func.count()).select_from(Recording))
        assert events == 1
        assert recordings == 1


async def test_get_event_returns_the_event_object(client: AsyncClient) -> None:
    created = (await _create(client))["event"]

    response = await client.get(f"{EVENTS_URL}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_event_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get(f"{EVENTS_URL}/does-not-exist")
    assert response.status_code == 404


async def test_list_events_paginates_newest_first(client: AsyncClient) -> None:
    base = CLIENT_CREATED_AT_MS
    await _create(client, title="older", clientCreatedAt=base, clientEventId="l1")
    await _create(client, title="newer", clientCreatedAt=base + 1000, clientEventId="l2")
    await _create(client, title="newest", clientCreatedAt=base + 2000, clientEventId="l3")

    response = await client.get(EVENTS_URL, params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert [item["title"] for item in body["items"]] == ["newest", "newer"]


async def test_list_events_filters_by_tag(client: AsyncClient) -> None:
    await _create(client, title="tagged", tagIds=["sleep"], clientEventId="t1")
    await _create(client, title="other", tagIds=["mood"], clientEventId="t2")

    response = await client.get(EVENTS_URL, params={"tag": "sleep"})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 1
    assert body["items"][0]["title"] == "tagged"


async def test_update_event_changes_only_provided_fields(client: AsyncClient) -> None:
    created = (await _create(client))["event"]

    response = await client.patch(
        f"{EVENTS_URL}/{created['id']}", json={"title": "Renamed", "tagIds": []}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["title"] == "Renamed"
    assert body["tagIds"] == []
    assert body["description"] == created["description"]
    assert body["occurredAt"] == created["occurredAt"]


async def test_update_event_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.patch(f"{EVENTS_URL}/does-not-exist", json={"title": "x"})
    assert response.status_code == 404


async def test_delete_event(client: AsyncClient) -> None:
    created = (await _create(client))["event"]

    response = await client.delete(f"{EVENTS_URL}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{EVENTS_URL}/{created['id']}")
    assert response.status_code == 404


# --- Ownership scoping (domain 07) --------------------------------------------


async def _seed_other_users_event(session_factory: async_sessionmaker[AsyncSession]) -> str:
    """Insert a second user and a text event they own; return the event id. The
    default `client` acts as TEST_IDENTITY, so this row belongs to someone else and
    must be invisible to every read/write the caller makes."""
    async with session_factory() as session:
        session.add(User(id="other-user", provider="google"))
        await session.flush()  # parent row before the FK child (no ORM relationship orders them)
        event = Event(
            id="other-users-event",
            user_id="other-user",
            type="text",
            title="not yours",
            tag_ids=[],
            occurred_at=datetime.fromtimestamp(CLIENT_CREATED_AT_MS / 1000, tz=UTC),
        )
        session.add(event)
        await session.commit()
        return event.id


async def test_create_stamps_the_caller_as_owner(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    me = (await client.get(ME_URL)).json()
    event_id = (await _create(client))["event"]["id"]

    async with session_factory() as session:
        event = await session.get(Event, event_id)
        assert event is not None
        assert event.user_id == me["id"]


async def test_get_another_users_event_returns_404(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    other_id = await _seed_other_users_event(session_factory)

    response = await client.get(f"{EVENTS_URL}/{other_id}")
    assert response.status_code == 404  # 404, not 403: never reveal it exists


async def test_update_another_users_event_returns_404(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    other_id = await _seed_other_users_event(session_factory)

    response = await client.patch(f"{EVENTS_URL}/{other_id}", json={"title": "hijacked"})
    assert response.status_code == 404


async def test_delete_another_users_event_returns_404(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    other_id = await _seed_other_users_event(session_factory)

    response = await client.delete(f"{EVENTS_URL}/{other_id}")
    assert response.status_code == 404

    # And the row is untouched: still readable directly.
    async with session_factory() as session:
        assert await session.get(Event, other_id) is not None


async def test_list_only_returns_the_callers_events(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_other_users_event(session_factory)
    mine = (await _create(client, title="mine"))["event"]

    body = (await client.get(EVENTS_URL)).json()

    assert body["total"] == 1
    assert [item["id"] for item in body["items"]] == [mine["id"]]
