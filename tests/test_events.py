from httpx import AsyncClient

EVENTS_URL = "/api/v1/events"

SAMPLE_EVENT = {
    "title": "Feeling a bit overwhelmed today",
    "description": "Logged after work",
    "occurred_at": "2026-07-04T10:30:00Z",
    "tags": ["mood", "work"],
}


async def _create_event(client: AsyncClient, **overrides) -> dict:
    response = await client.post(EVENTS_URL, json={**SAMPLE_EVENT, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def test_create_event(client: AsyncClient) -> None:
    body = await _create_event(client)

    assert body["title"] == SAMPLE_EVENT["title"]
    assert body["description"] == SAMPLE_EVENT["description"]
    assert body["tags"] == SAMPLE_EVENT["tags"]
    assert body["id"]
    assert body["created_at"]
    assert body["updated_at"]


async def test_create_event_rejects_missing_title(client: AsyncClient) -> None:
    payload = {k: v for k, v in SAMPLE_EVENT.items() if k != "title"}
    response = await client.post(EVENTS_URL, json=payload)
    assert response.status_code == 422


async def test_get_event(client: AsyncClient) -> None:
    created = await _create_event(client)

    response = await client.get(f"{EVENTS_URL}/{created['id']}")
    assert response.status_code == 200
    assert response.json() == created


async def test_get_event_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.get(f"{EVENTS_URL}/does-not-exist")
    assert response.status_code == 404


async def test_list_events_paginates_newest_first(client: AsyncClient) -> None:
    await _create_event(client, title="older", occurred_at="2026-07-01T00:00:00Z")
    await _create_event(client, title="newer", occurred_at="2026-07-02T00:00:00Z")
    await _create_event(client, title="newest", occurred_at="2026-07-03T00:00:00Z")

    response = await client.get(EVENTS_URL, params={"limit": 2, "offset": 0})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 3
    assert body["limit"] == 2
    assert body["offset"] == 0
    assert [item["title"] for item in body["items"]] == ["newest", "newer"]


async def test_list_events_filters_by_tag(client: AsyncClient) -> None:
    await _create_event(client, title="tagged", tags=["sleep"])
    await _create_event(client, title="other", tags=["mood"])

    response = await client.get(EVENTS_URL, params={"tag": "sleep"})
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 1
    assert body["items"][0]["title"] == "tagged"


async def test_update_event_changes_only_provided_fields(client: AsyncClient) -> None:
    created = await _create_event(client)

    response = await client.patch(
        f"{EVENTS_URL}/{created['id']}", json={"title": "Renamed", "tags": []}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["title"] == "Renamed"
    assert body["tags"] == []
    assert body["description"] == created["description"]
    assert body["occurred_at"] == created["occurred_at"]


async def test_update_event_returns_404_for_unknown_id(client: AsyncClient) -> None:
    response = await client.patch(f"{EVENTS_URL}/does-not-exist", json={"title": "x"})
    assert response.status_code == 404


async def test_delete_event(client: AsyncClient) -> None:
    created = await _create_event(client)

    response = await client.delete(f"{EVENTS_URL}/{created['id']}")
    assert response.status_code == 204

    response = await client.get(f"{EVENTS_URL}/{created['id']}")
    assert response.status_code == 404
