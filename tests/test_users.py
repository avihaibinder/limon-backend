from httpx import AsyncClient

from tests.conftest import TEST_IDENTITY

ME_URL = "/api/v1/users/me"


async def test_get_me_provisions_and_returns_profile(client: AsyncClient) -> None:
    response = await client.get(ME_URL)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["provider"] == TEST_IDENTITY["provider"]
    assert body["provider_subject"] == TEST_IDENTITY["provider_subject"]
    assert body["email"] == TEST_IDENTITY["email"]
    assert body["display_name"] == TEST_IDENTITY["display_name"]
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_me_is_stable_across_requests(client: AsyncClient) -> None:
    first = (await client.get(ME_URL)).json()
    second = (await client.get(ME_URL)).json()

    assert first["id"] == second["id"]


async def test_update_me_profile_fields(client: AsyncClient) -> None:
    response = await client.patch(ME_URL, json={"display_name": "Renamed"})
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

    response = await client.patch(f"{USERS_URL}/{created['id']}", json={"display_name": "Renamed"})
    assert response.status_code == 200
    body = response.json()

    assert body["display_name"] == "Renamed"
    assert body["email"] == TEST_IDENTITY["email"]

    # The edit sticks — JIT provisioning must not overwrite it on the next request.
    assert (await client.get(ME_URL)).json()["display_name"] == "Renamed"


async def test_update_me_rejects_invalid_email(client: AsyncClient) -> None:
    response = await client.patch(ME_URL, json={"email": "not-an-email"})
    assert response.status_code == 422


async def test_delete_me_then_next_request_provisions_fresh_account(client: AsyncClient) -> None:
    original = (await client.get(ME_URL)).json()

    response = await client.delete(ME_URL)
    assert response.status_code == 204

    # Same OAuth identity signing in again gets a brand-new account.
    recreated = (await client.get(ME_URL)).json()
    assert recreated["id"] != original["id"]
    assert recreated["provider_subject"] == original["provider_subject"]
