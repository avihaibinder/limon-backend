from httpx import AsyncClient

USERS_URL = "/api/v1/users"

SAMPLE_USER = {
    "provider": "google",
    "provider_subject": "108123456789",
    "email": "lemon@example.com",
    "display_name": "Lemon",
}


async def _create_user(client: AsyncClient, **overrides) -> dict:
    response = await client.post(USERS_URL, json={**SAMPLE_USER, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_user(client: AsyncClient) -> None:
    body = await _create_user(client)

    assert body["provider"] == SAMPLE_USER["provider"]
    assert body["provider_subject"] == SAMPLE_USER["provider_subject"]
    assert body["email"] == SAMPLE_USER["email"]
    assert body["display_name"] == SAMPLE_USER["display_name"]
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_create_user_rejects_duplicate_provider_identity(client: AsyncClient) -> None:
    await _create_user(client)

    response = await client.post(USERS_URL, json=SAMPLE_USER)
    assert response.status_code == 409


async def test_create_user_allows_same_subject_on_other_provider(client: AsyncClient) -> None:
    await _create_user(client)
    await _create_user(client, provider="apple")


async def test_create_user_rejects_invalid_email(client: AsyncClient) -> None:
    response = await client.post(USERS_URL, json={**SAMPLE_USER, "email": "not-an-email"})
    assert response.status_code == 422


async def test_get_user(client: AsyncClient) -> None:
    created = await _create_user(client)

    response = await client.get(f"{USERS_URL}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_user_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get(f"{USERS_URL}/does-not-exist")
    assert response.status_code == 404


async def test_list_users(client: AsyncClient) -> None:
    await _create_user(client)
    await _create_user(client, provider_subject="other-subject")

    response = await client.get(USERS_URL)
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert len(body["items"]) == 2


async def test_update_user_profile_fields(client: AsyncClient) -> None:
    created = await _create_user(client)

    response = await client.patch(
        f"{USERS_URL}/{created['id']}", json={"display_name": "Renamed"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["display_name"] == "Renamed"
    assert body["email"] == created["email"]
    assert body["provider_subject"] == created["provider_subject"]


async def test_delete_user(client: AsyncClient) -> None:
    created = await _create_user(client)

    response = await client.delete(f"{USERS_URL}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{USERS_URL}/{created['id']}")
    assert response.status_code == 404
