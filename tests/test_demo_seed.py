"""Tests for POST /users/me/demo-data: the on-request demo history backfill."""

import re
from datetime import UTC, datetime

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

# The seed is larger than one default page; ask for all of it.
ALL_EVENTS_URL = f"{EVENTS_URL}?limit=100"


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

    events = (await client.get(ALL_EVENTS_URL)).json()
    assert events["total"] == len(demo_seed._SEED_EVENTS) == 46
    # Every seeded event is text and carries no recording, including the rows
    # that were recordings in the source.
    assert all(e["type"] == "text" and e["recordId"] is None for e in events["items"])

    tags = (await client.get(TAGS_URL)).json()
    assert {(t["name"], t["color"]) for t in tags["items"]} == set(demo_seed._TAGS.items())
    assert tags["total"] == 16


async def test_timestamps_are_absolute_israel_time(
    client: AsyncClient, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Source times are kept as written (IDT = UTC+3), not rebased onto "now"."""
    await client.post(DEMO_URL)

    async with session_factory() as session:
        events = list(
            await session.scalars(
                select(Event)
                .where(Event.user_id == TEST_IDENTITY["sub"])
                .order_by(Event.occurred_at.desc())
            )
        )

    # Newest row: 25/07/2026 19:42 Israel time == 16:42 UTC.
    assert _as_utc(events[0].occurred_at) == datetime(2026, 7, 25, 16, 42, tzinfo=UTC)
    # Oldest row: 09/07/2026 08:10 Israel time == 05:10 UTC.
    assert _as_utc(events[-1].occurred_at) == datetime(2026, 7, 9, 5, 10, tzinfo=UTC)

    # Nothing was shifted relative to "now": these are fixed points in time.
    expected = sorted(
        (demo_seed._occurred_at(date, time) for date, time, *_ in demo_seed._SEED_EVENTS),
        reverse=True,
    )
    assert [_as_utc(e.occurred_at) for e in events] == expected


async def test_recording_rows_become_text_events_with_duration(client: AsyncClient) -> None:
    await client.post(DEMO_URL)
    items = (await client.get(ALL_EVENTS_URL)).json()["items"]

    recordings = [e for e in items if e["title"] and e["title"].startswith("הקלטה")]
    assert len(recordings) == 9
    # Title kept verbatim, transcript in the description, duration parsed out of
    # the title, and no recording behind any of them.
    assert all(
        e["type"] == "text" and e["description"] and e["recordId"] is None for e in recordings
    )
    assert {e["title"]: e["durationSec"] for e in recordings} == {
        "הקלטה (0:09)": 9,
        "הקלטה (0:11)": 11,
        "הקלטה (0:08)": 8,
        "הקלטה (0:14)": 14,
        "הקלטה (0:10)": 10,
        "הקלטה (0:06)": 6,
        "הקלטה (0:13)": 13,
        "הקלטה (0:12)": 12,
        "הקלטה (0:07)": 7,
    }


def test_seed_durations_agree_with_their_titles() -> None:
    """`duration_sec` is stored alongside the `הקלטה (M:SS)` title; keep them in sync."""
    for _, _, title, _, _, duration_sec in demo_seed._SEED_EVENTS:
        if title is not None and title.startswith("הקלטה"):
            minutes, seconds = re.fullmatch(r"הקלטה \((\d+):(\d{2})\)", title).groups()
            assert duration_sec == int(minutes) * 60 + int(seconds), title
        else:
            # Only the recording rows carry a duration.
            assert duration_sec is None


async def test_lemon_rows_are_untitled_and_tagged(client: AsyncClient) -> None:
    """Rows with neither title nor description: an instant lemon press, tags only."""
    await client.post(DEMO_URL)
    items = (await client.get(ALL_EVENTS_URL)).json()["items"]

    untitled = [e for e in items if e["title"] is None]
    assert len(untitled) == 9
    assert all(e["type"] == "text" and e["description"] is None for e in untitled)
    # Unlike the previous seed, these do carry tags.
    assert all(e["tagIds"] for e in untitled)


async def test_rows_without_tags_are_seeded_untagged(client: AsyncClient) -> None:
    await client.post(DEMO_URL)
    items = (await client.get(ALL_EVENTS_URL)).json()["items"]

    untagged = [e for e in items if not e["tagIds"]]
    assert {e["title"] for e in untagged} == {"קניות בסופר", "הקלטה (0:07)"}


async def test_events_reference_the_created_tag_ids(client: AsyncClient) -> None:
    await client.post(DEMO_URL)
    items = (await client.get(ALL_EVENTS_URL)).json()["items"]
    tag_id_by_name = {t["name"]: t["id"] for t in (await client.get(TAGS_URL)).json()["items"]}

    motorcycle = next(e for e in items if e["title"] == "רעש של אופנוע מהרחוב")
    assert motorcycle["tagIds"] == [tag_id_by_name["רעש"], tag_id_by_name["תחושה רעה"]]

    doctor = next(e for e in items if e["title"] == "ביקור אצל הרופא")
    assert doctor["tagIds"] == [tag_id_by_name["המתנה"], tag_id_by_name["גופני"]]


async def test_existing_tag_with_seed_name_is_reused(client: AsyncClient) -> None:
    created = await client.post(TAGS_URL, json={"name": "רעש"})
    assert created.status_code == 201
    existing_tag_id = created.json()["id"]

    assert (await client.post(DEMO_URL)).status_code == 201

    tags = (await client.get(TAGS_URL)).json()
    assert tags["total"] == 16  # reused, not duplicated
    reused = next(t for t in tags["items"] if t["id"] == existing_tag_id)
    assert reused["color"] is None  # the user's own tag keeps its color, no legend overwrite

    items = (await client.get(ALL_EVENTS_URL)).json()["items"]
    motorcycle = next(e for e in items if e["title"] == "רעש של אופנוע מהרחוב")
    assert motorcycle["tagIds"][0] == existing_tag_id


async def test_existing_tags_alone_do_not_block_seeding(client: AsyncClient) -> None:
    """Tags are not a blocker -- only events are."""
    assert (await client.post(TAGS_URL, json={"name": "משהו משלי"})).status_code == 201

    assert (await client.post(DEMO_URL)).status_code == 201
    assert (await client.get(ALL_EVENTS_URL)).json()["total"] == 46


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
    assert (await client.get(ALL_EVENTS_URL)).json()["total"] == 1
    assert (await client.get(ME_URL)).json()["demo_seeded_at"] is None


async def test_second_call_conflicts_while_events_exist(client: AsyncClient) -> None:
    assert (await client.post(DEMO_URL)).status_code == 201

    response = await client.post(DEMO_URL)
    assert response.status_code == 409
    assert "empty account" in response.json()["detail"]

    # Nothing was added by the rejected call.
    assert (await client.get(ALL_EVENTS_URL)).json()["total"] == 46


async def test_reseeding_after_deleting_every_event_is_allowed(client: AsyncClient) -> None:
    """The stamp is not a gate: empty the account and the button works again."""
    assert (await client.post(DEMO_URL)).status_code == 201
    first_stamp = (await client.get(ME_URL)).json()["demo_seeded_at"]

    for event in (await client.get(ALL_EVENTS_URL)).json()["items"]:
        assert (await client.delete(f"{EVENTS_URL}/{event['id']}")).status_code == 204
    assert (await client.get(ALL_EVENTS_URL)).json()["total"] == 0

    # Still stamped from the first run, and that must not block a second seed.
    assert first_stamp is not None
    assert (await client.post(DEMO_URL)).status_code == 201

    assert (await client.get(ALL_EVENTS_URL)).json()["total"] == 46
    # Tags were reused by name rather than duplicated.
    assert (await client.get(TAGS_URL)).json()["total"] == 16
