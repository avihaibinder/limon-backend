"""Tests for POST /users/me/demo-data: the on-request demo history backfill."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.services import demo_seed
from tests.conftest import TEST_IDENTITY

DEMO_URL = "/api/v1/users/me/demo-data"
ME_URL = "/api/v1/users/me"
EVENTS_URL = "/api/v1/events"
TAGS_URL = "/api/v1/tags"

# The original mock timestamps (newest first), used to assert relative spacing.
_ORIGINAL_TS_DESC = sorted((ts for ts, _, _ in demo_seed._SEED_EVENTS), reverse=True)


def _as_utc(value: datetime) -> datetime:
    """SQLite reads DateTime back naive; treat naive as UTC (same as Postgres)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


async def test_create_demo_data_marks_the_user(client: AsyncClient) -> None:
    assert (await client.get(ME_URL)).json()["demo_seeded_at"] is None

    response = await client.post(DEMO_URL)
    assert response.status_code == 201, response.text
    assert response.json()["demo_seeded_at"] is not None

    # The mark persists on the profile.
    assert (await client.get(ME_URL)).json()["demo_seeded_at"] is not None


async def test_demo_data_creates_all_events_and_tags(client: AsyncClient) -> None:
    await client.post(DEMO_URL)

    events = (await client.get(EVENTS_URL)).json()
    assert events["total"] == len(demo_seed._SEED_EVENTS) == 10
    assert all(e["type"] == "text" and e["recordId"] is None for e in events["items"])

    tags = (await client.get(TAGS_URL)).json()
    assert {t["name"] for t in tags["items"]} == set(demo_seed._TAG_NAMES.values())
    assert tags["total"] == 6


async def test_timestamps_are_relative_to_now(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    before = datetime.now(UTC)
    await client.post(DEMO_URL)
    after = datetime.now(UTC)

    async with session_factory() as session:
        events = list(
            await session.scalars(
                select(Event)
                .where(Event.user_id == TEST_IDENTITY["sub"])
                .order_by(Event.occurred_at.desc())
            )
        )

    # The newest row lands at "now" (bracketed by the call), the rest keep their
    # original distance from it exactly.
    newest = _as_utc(events[0].occurred_at)
    assert before <= newest <= after
    for event, original_ts in zip(events, _ORIGINAL_TS_DESC, strict=True):
        offset = newest - _as_utc(event.occurred_at)
        assert offset.total_seconds() * 1000 == pytest.approx(_ORIGINAL_TS_DESC[0] - original_ts)


async def test_lemon_rows_become_untitled_text_events(client: AsyncClient) -> None:
    await client.post(DEMO_URL)
    items = (await client.get(EVENTS_URL)).json()["items"]

    untitled = [e for e in items if e["title"] is None]
    assert len(untitled) == 2
    assert all(
        e["type"] == "text" and e["description"] is None and e["tagIds"] == [] for e in untitled
    )


async def test_events_reference_the_created_tag_ids(client: AsyncClient) -> None:
    await client.post(DEMO_URL)
    items = (await client.get(EVENTS_URL)).json()["items"]
    tag_id_by_name = {t["name"]: t["id"] for t in (await client.get(TAGS_URL)).json()["items"]}

    nightmare = next(e for e in items if e["title"] == "סיוט")
    assert nightmare["tagIds"] == [tag_id_by_name["חלום רע"]]

    motorcycle = next(e for e in items if e["title"] == "רעש של אופנוע מהרחוב")
    assert motorcycle["tagIds"] == [tag_id_by_name["רעש"], tag_id_by_name["תחושה רעה"]]


async def test_existing_tag_with_seed_name_is_reused(client: AsyncClient) -> None:
    created = await client.post(TAGS_URL, json={"name": "רעש"})
    assert created.status_code == 201
    existing_tag_id = created.json()["id"]

    assert (await client.post(DEMO_URL)).status_code == 201

    tags = (await client.get(TAGS_URL)).json()
    assert tags["total"] == 6  # reused, not duplicated

    items = (await client.get(EVENTS_URL)).json()["items"]
    motorcycle = next(e for e in items if e["title"] == "רעש של אופנוע מהרחוב")
    assert motorcycle["tagIds"][0] == existing_tag_id


async def test_second_call_conflicts(client: AsyncClient) -> None:
    assert (await client.post(DEMO_URL)).status_code == 201

    response = await client.post(DEMO_URL)
    assert response.status_code == 409
    assert "already created" in response.json()["detail"]

    # Nothing was added by the rejected call.
    assert (await client.get(EVENTS_URL)).json()["total"] == 10


async def test_account_with_events_conflicts(client: AsyncClient) -> None:
    created = await client.post(
        EVENTS_URL,
        json={"type": "text", "title": "my own note", "clientCreatedAt": 1_751_600_000_000},
    )
    assert created.status_code == 201

    response = await client.post(DEMO_URL)
    assert response.status_code == 409
    assert "empty account" in response.json()["detail"]

    # The account is untouched: the one real event, no seeded rows, no mark.
    assert (await client.get(EVENTS_URL)).json()["total"] == 1
    assert (await client.get(ME_URL)).json()["demo_seeded_at"] is None
