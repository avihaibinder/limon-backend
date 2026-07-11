from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.tag import Tag
from app.models.user import User

TAGS_URL = "/api/v1/tags"
ME_URL = "/api/v1/users/me"


async def _create_tag(client: AsyncClient, name: str = "sleep") -> dict:
    response = await client.post(TAGS_URL, json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_other_users_tag(
    session_factory: async_sessionmaker[AsyncSession], name: str = "sleep"
) -> Tag:
    """Insert a tag owned by somebody other than the authenticated test user."""
    async with session_factory() as session:
        other = User(provider="google", provider_subject="other-subject")
        session.add(other)
        await session.flush()  # materialize other.id (Python-side default)
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


async def test_create_tag_rejects_duplicate_name_for_same_user(client: AsyncClient) -> None:
    await _create_tag(client)

    response = await client.post(TAGS_URL, json={"name": "sleep"})
    assert response.status_code == 409


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


async def test_delete_tag(client: AsyncClient) -> None:
    created = await _create_tag(client)

    response = await client.delete(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 404


async def test_deleting_account_cascades_to_tags(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    created = await _create_tag(client)

    response = await client.delete(ME_URL)
    assert response.status_code == 204

    async with session_factory() as session:
        assert await session.get(Tag, created["id"]) is None
