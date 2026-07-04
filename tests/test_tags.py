from httpx import AsyncClient

TAGS_URL = "/api/v1/tags"
USERS_URL = "/api/v1/users"


async def _create_user(client: AsyncClient, **overrides) -> dict:
    payload = {
        "provider": "google",
        "provider_subject": "108123456789",
        "email": "lemon@example.com",
        "display_name": "Lemon",
        **overrides,
    }
    response = await client.post(USERS_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _create_tag(client: AsyncClient, user_id: str, name: str = "sleep") -> dict:
    response = await client.post(TAGS_URL, json={"user_id": user_id, "name": name})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_tag(client: AsyncClient) -> None:
    user = await _create_user(client)
    body = await _create_tag(client, user["id"])

    assert body["name"] == "sleep"
    assert body["user_id"] == user["id"]
    assert body["id"]


async def test_create_tag_rejects_unknown_user(client: AsyncClient) -> None:
    response = await client.post(TAGS_URL, json={"user_id": "does-not-exist", "name": "x"})
    assert response.status_code == 404


async def test_create_tag_rejects_duplicate_name_for_same_user(client: AsyncClient) -> None:
    user = await _create_user(client)
    await _create_tag(client, user["id"])

    response = await client.post(TAGS_URL, json={"user_id": user["id"], "name": "sleep"})
    assert response.status_code == 409


async def test_create_tag_allows_same_name_for_other_user(client: AsyncClient) -> None:
    user_a = await _create_user(client)
    user_b = await _create_user(client, provider_subject="other-subject")

    await _create_tag(client, user_a["id"])
    await _create_tag(client, user_b["id"])


async def test_get_tag(client: AsyncClient) -> None:
    user = await _create_user(client)
    created = await _create_tag(client, user["id"])

    response = await client.get(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_tag_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get(f"{TAGS_URL}/does-not-exist")
    assert response.status_code == 404


async def test_list_tags_filters_by_user_and_sorts_by_name(client: AsyncClient) -> None:
    user_a = await _create_user(client)
    user_b = await _create_user(client, provider_subject="other-subject")
    await _create_tag(client, user_a["id"], name="mood")
    await _create_tag(client, user_a["id"], name="anxiety")
    await _create_tag(client, user_b["id"], name="sleep")

    response = await client.get(TAGS_URL, params={"user_id": user_a["id"]})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["anxiety", "mood"]


async def test_rename_tag(client: AsyncClient) -> None:
    user = await _create_user(client)
    created = await _create_tag(client, user["id"])

    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"name": "rest"})
    assert response.status_code == 200
    body = response.json()

    assert body["name"] == "rest"
    assert body["user_id"] == user["id"]


async def test_rename_tag_rejects_existing_name(client: AsyncClient) -> None:
    user = await _create_user(client)
    await _create_tag(client, user["id"], name="mood")
    created = await _create_tag(client, user["id"], name="sleep")

    response = await client.patch(f"{TAGS_URL}/{created['id']}", json={"name": "mood"})
    assert response.status_code == 409


async def test_delete_tag(client: AsyncClient) -> None:
    user = await _create_user(client)
    created = await _create_tag(client, user["id"])

    response = await client.delete(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 404


async def test_deleting_user_cascades_to_their_tags(client: AsyncClient) -> None:
    user = await _create_user(client)
    created = await _create_tag(client, user["id"])

    response = await client.delete(f"{USERS_URL}/{user['id']}")
    assert response.status_code == 204

    response = await client.get(f"{TAGS_URL}/{created['id']}")
    assert response.status_code == 404
