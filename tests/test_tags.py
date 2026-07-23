from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.tag import Tag
from app.models.user import User

TAGS_URL = "/api/v1/tags"
EVENTS_URL = "/api/v1/events"
ME_URL = "/api/v1/users/me"


async def _create_tag(client: AsyncClient, name: str = "sleep", **extra) -> dict:
    response = await client.post(TAGS_URL, json={"name": name, **extra})
    assert response.status_code == 201, response.text
    return response.json()


async def _create_event(client: AsyncClient, tag_ids: list[str]) -> dict:
    """Create a text event carrying ``tag_ids``; returns the event body."""
    response = await client.post(
        EVENTS_URL,
        json={"type": "text", "tagIds": tag_ids, "clientCreatedAt": 1_751_600_000_000},
    )
    assert response.status_code == 201, response.text
    return response.json()["event"]


async def _seed_other_users_tag(
    session_factory: async_sessionmaker[AsyncSession], name: str = "sleep"
) -> Tag:
    """Insert a tag owned by somebody other than the authenticated test user."""
    async with session_factory() as session:
        other = User(id="other-subject", provider="google")
        session.add(other)
        await session.flush()
        tag = Tag(user_id=other.id, name=name)
        session.add(tag)
        await session.commit()
        await session.refresh(tag)
    return tag


async def test_create_tag_belongs_to_authenticated_user(client: AsyncClient) -> None:
    me = (await client.get(ME_URL)).json()
    body = await _create_tag(client)

    assert body["name"] == "sleep"
    assert body["user_id"] == me["id"]
    assert body["id"]
    assert body["color"] is None


async def test_create_tag_stores_color(client: AsyncClient) -> None:
    body = await _create_tag(client, color="#f9d9a0")
    assert body["color"] == "#f9d9a0"


async def test_create_tag_trims_name(client: AsyncClient) -> None:
    body = await _create_tag(client, name="  sleep  ")
    assert body["name"] == "sleep"


async def test_create_tag_rejects_whitespace_only_name(client: AsyncClient) -> None:
    response = await client.post(TAGS_URL, json={"name": "   "})
    assert response.status_code == 422


async def test_create_duplicate_name_returns_existing_tag_with_200(client: AsyncClient) -> None:
    """Upsert-by-name: the existing tag comes back (200) and keeps its color."""
    created = await _create_tag(client, color="#f9d9a0")

    response = await client.post(TAGS_URL, json={"name": " sleep ", "color": "#000000"})
    assert response.status_code == 200
    body = response.json()

    assert body["id"] == created["id"]
    assert body["color"] == "#f9d9a0"  # not overwritten by the retry's color


async def test_create_tag_allows_name_already_used_by_other_user(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_other_users_tag(session_factory, name="sleep")
    await _create_tag(client, name="sleep")


async def test_get_tag(client: AsyncClient) -> None:
    created = await _create_tag(client)

    response = await client.get(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_tag_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get(f"{TAGS_URL}/does-not-exist")
    assert response.status_code == 404


async def test_other_users_tag_is_invisible(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    foreign = await _seed_other_users_tag(session_factory)

    assert (await client.get(f"{TAGS_URL}/{foreign.id}")).status_code == 404
    assert (await client.patch(f"{TAGS_URL}/{foreign.id}", json={"name": "x"})).status_code == 404
    assert (await client.delete(f"{TAGS_URL}/{foreign.id}")).status_code == 404


async def test_list_tags_returns_only_own_tags_sorted_by_name(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await _seed_other_users_tag(session_factory, name="aardvark")
    await _create_tag(client, name="mood")
    await _create_tag(client, name="anxiety")

    response = await client.get(TAGS_URL)
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["anxiety", "mood"]


async def test_rename_tag(client: AsyncClient) -> None:
    created = await _create_tag(client)

    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"name": "rest"})
    assert response.status_code == 200
    body = response.json()

    assert body["name"] == "rest"
    assert body["user_id"] == created["user_id"]


async def test_rename_tag_rejects_existing_name(client: AsyncClient) -> None:
    await _create_tag(client, name="mood")
    created = await _create_tag(client, name="sleep")

    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"name": "mood"})
    assert response.status_code == 409


async def test_patch_color_sets_and_clears(client: AsyncClient) -> None:
    created = await _create_tag(client)

    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"color": "#a0d9f9"})
    assert response.status_code == 200
    assert response.json()["color"] == "#a0d9f9"

    # An omitted color leaves it untouched; an explicit null clears it.
    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"name": "rest"})
    assert response.json()["color"] == "#a0d9f9"

    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"color": None})
    assert response.status_code == 200
    assert response.json()["color"] is None


async def test_delete_tag(client: AsyncClient) -> None:
    created = await _create_tag(client)

    response = await client.delete(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 404


async def test_delete_tag_detaches_it_from_events(client: AsyncClient) -> None:
    tag = await _create_tag(client, name="mood")
    keep = await _create_tag(client, name="work")
    tagged = await _create_event(client, tag_ids=[tag["id"], keep["id"]])
    untagged = await _create_event(client, tag_ids=[keep["id"]])

    response = await client.delete(f"{TAGS_URL}/{tag['id']}")
    assert response.status_code == 204

    refreshed = (await client.get(f"{EVENTS_URL}/{tagged['id']}")).json()
    assert refreshed["tagIds"] == [keep["id"]]
    # The detach is a real UPDATE (Realtime echoes it in production).
    assert refreshed["updatedAt"] != tagged["updatedAt"]

    # An event that never carried the tag is left alone.
    assert (await client.get(f"{EVENTS_URL}/{untagged['id']}")).json()["updatedAt"] == untagged[
        "updatedAt"
    ]


async def test_delete_tag_leaves_other_users_events_alone(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    tag = await _create_tag(client)
    async with session_factory() as session:
        other = User(id="other-subject", provider="google")
        session.add(other)
        await session.flush()
        foreign_event = Event(
            user_id=other.id,
            type="text",
            occurred_at=datetime.now(UTC),
            tag_ids=[tag["id"]],
        )
        session.add(foreign_event)
        await session.commit()
        foreign_event_id = foreign_event.id

    assert (await client.delete(f"{TAGS_URL}/{tag['id']}")).status_code == 204

    async with session_factory() as session:
        foreign = await session.get(Event, foreign_event_id)
        assert foreign is not None
        assert foreign.tag_ids == [tag["id"]]


async def test_deleting_account_cascades_to_tags(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    created = await _create_tag(client)

    response = await client.delete(ME_URL)
    assert response.status_code == 204

    async with session_factory() as session:
        assert await session.get(Tag, created["id"]) is None
