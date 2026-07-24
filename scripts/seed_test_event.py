"""Throwaway seeding script for manually testing POST /internal/tag.

Not wired into the app or pytest, and touches no app logic -- it only inserts
rows using the same models and the same session factory
(app.db.session.async_session_factory) the app itself uses, so it writes to
exactly the DB your local dev server is pointed at (LIMON_DATABASE_URL, the
local SQLite file by default). Run it from the repo root, same as the dev
server, so relative SQLite paths resolve to the same file.

Creates one fake user, five Hebrew tags spanning a reasonable spread, and two
text events with empty tag_ids (so /internal/tag will actually attempt to tag
them instead of no-op'ing):

  Event A -- long, detailed, an explicit place name, clear emotional content.
  Event B -- short, vague, no location mentioned: tests how the tagger
             handles ambiguity when there isn't much to go on.

Prints the user id, tag ids/names, and both event ids so you can call
POST /internal/tag on each by hand and compare results.

Usage: uv run python scripts/seed_test_event.py
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.db.base import Base
from app.db.session import async_session_factory, engine
from app.models.event import Event
from app.models.tag import Tag
from app.models.user import User

_TAG_NAMES = ["עבודה", "משפחה", "ספורט", "בריאות נפשית", "בישול"]

_EVENT_A_TEXT = (
    "היום התעוררתי מוקדם והלכתי לרוץ בפארק הירקון בתל אביב. "
    "אחר כך פגשתי את אמא שלי לארוחת בוקר, ודיברנו הרבה על העבודה החדשה שלי. "
    "הרגשתי די לחוץ לקראת הפגישה של אחר הצהריים, אבל בסופו של דבר היא הלכה "
    "טוב ויצאתי ממנה במצב רוח הרבה יותר טוב."
)

_EVENT_B_TEXT = "יום רגיל, כלום מיוחד."


async def main() -> None:
    # Idempotent -- safe whether or not the dev server has created the tables
    # yet (mirrors app.main's lifespan, so this works stand-alone too).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as session:
        user = User(id=str(uuid.uuid4()), provider="manual-test")
        session.add(user)
        await session.flush()  # assign user.id's FK target before tags/events reference it

        tags = [Tag(user_id=user.id, name=name) for name in _TAG_NAMES]
        session.add_all(tags)
        await session.flush()  # assign generated tag ids for the printout below

        now = datetime.now(UTC)
        event_a = Event(
            user_id=user.id,
            type="text",
            description=_EVENT_A_TEXT,
            tag_ids=[],
            occurred_at=now,
        )
        event_b = Event(
            user_id=user.id,
            type="text",
            description=_EVENT_B_TEXT,
            tag_ids=[],
            occurred_at=now,
        )
        session.add_all([event_a, event_b])
        await session.commit()
        await session.refresh(event_a)
        await session.refresh(event_b)

    print("Seeded manual test data:\n")
    print(f"user_id: {user.id}\n")
    print("tags:")
    for tag in tags:
        print(f"  {tag.id}  {tag.name}")
    print()
    print(f"event A id (long, detailed, explicit location): {event_a.id}")
    print(f"event B id (short, vague, no location):          {event_b.id}")
    print()
    print("Call POST /internal/tag with a body of, e.g.:")
    print(f'  {{"eventId": "{event_a.id}"}}')
    print(f'  {{"eventId": "{event_b.id}"}}')


if __name__ == "__main__":
    asyncio.run(main())
