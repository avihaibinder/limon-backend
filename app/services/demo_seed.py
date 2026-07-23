"""Demo history backfilled into an empty account, on request.

Triggered by ``POST /users/me/demo-data`` (an FE button), never automatically.
The router guards the call: 409 if the account is already marked
(``users.demo_seeded_at``) or already has events; this module only seeds.

Source: spec-local/mock_data/DEMO_SEED.mock-data.md (the FE's pre-migration
placeholder dataset). The original epoch-ms timestamps are kept verbatim below;
at seed time the newest row is treated as "now" and every other row keeps its
original distance from it, so a fresh account always shows recent-looking
history (about a day and a half of entries).

Mapping from the old FE shape (the open points called out in the mock file):
- Every seeded event is ``text``; mocks can only be text.
- ``lemon`` rows became text events with a null title/body, the same rule the
  API applies (see ``EventCreate``). Lemon intensity is not kept.
- The ``audio`` row never had a real recording (it was a placeholder), so it
  is seeded as a plain text event, keeping its title.
- Legacy local tag ids (t1..t6) become real per-user ``tags`` rows; the
  legend's colors are dropped (tags carry no color on the BE).
- ``exported_record_ids`` is dropped (no export flag on the BE).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.tag import Tag
from app.models.user import User
from app.services import tags as tags_service

# Local tag id to tag name, in the FE's defaultTags order. All six are created
# even though the seed events reference only five (t2 is part of the default set).
_TAG_NAMES: dict[str, str] = {
    "t1": "משפחה",
    "t2": "עבודה",
    "t3": "חלום רע",
    "t4": "ריב",
    "t5": "רעש",
    "t6": "תחושה רעה",
}

# One row per seed event: (original epoch-ms timestamp, title, local tag ids).
# All rows are text events; mocks can only be text.
_SEED_EVENTS: list[tuple[int, str | None, list[str]]] = [
    # seed-10
    (1_781_622_060_000, "הרגשה רעה", ["t6"]),
    # seed-9
    (1_781_619_240_000, "ריח של עשן", ["t6"]),
    # seed-8: was type "audio" in the mock (a placeholder, no recording ever existed)
    (1_781_610_480_000, "שיחת טלפון עם אבא", ["t1"]),
    # seed-7: was type "lemon" (heavy)
    (1_781_601_780_000, None, []),
    # seed-6
    (1_781_588_520_000, "רעש של אופנוע מהרחוב", ["t5", "t6"]),
    # seed-5
    (1_781_535_660_000, "הרגשה רעה", ["t6"]),
    # seed-4: was marked exported
    (1_781_526_360_000, "ריב עם בת הזוג", ["t4"]),
    # seed-3: was type "lemon" (standard)
    (1_781_513_880_000, None, []),
    # seed-2: was marked exported
    (1_781_502_120_000, "רעש של אופנוע מהרחוב", ["t5"]),
    # seed-1
    (1_781_487_360_000, "סיוט", ["t3"]),
]


async def seed_demo_data(session: AsyncSession, *, user: User) -> User:
    """Create the demo tags and events for ``user`` and stamp ``demo_seeded_at``.

    The caller has already verified the account is unmarked and has no events.
    Tags are matched by name: one the user already created is reused rather
    than duplicated, so the (user_id, name) unique constraint holds. Commits
    once, so the events and the mark land atomically.
    """
    tags: dict[str, Tag] = {}
    for local_id, name in _TAG_NAMES.items():
        tag = await tags_service.get_tag_by_name(session, user.id, name)
        if tag is None:
            tag = Tag(user_id=user.id, name=name)
            session.add(tag)
        tags[local_id] = tag
    # Flush so newly created tags get their generated ids before events reference them.
    await session.flush()

    now = datetime.now(UTC)
    newest_ms = max(ts for ts, _, _ in _SEED_EVENTS)
    for ts, title, local_tag_ids in _SEED_EVENTS:
        session.add(
            Event(
                user_id=user.id,
                type="text",
                title=title,
                occurred_at=now - timedelta(milliseconds=newest_ms - ts),
                tag_ids=[tags[t].id for t in local_tag_ids],
            )
        )
    user.demo_seeded_at = now
    await session.commit()
    await session.refresh(user)
    return user
