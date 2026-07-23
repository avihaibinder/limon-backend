"""Model-level checks for the new recordings table and the additive events columns.

Uses the in-memory `session_factory` from conftest to write and read rows directly.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.event import Event
from app.models.recording import Recording
from app.models.user import User


async def _seed_user(session: AsyncSession) -> User:
    user = User(id="subj-1", provider="google", email="a@example.com")
    session.add(user)
    await session.flush()
    return user


async def test_recording_and_audio_event_roundtrip(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await _seed_user(session)

        recording = Recording(
            user_id=user.id,
            storage_key=f"v0/{user.id}/rec.m4a",
            content_type="audio/mp4",
        )
        session.add(recording)
        await session.flush()

        event = Event(
            user_id=user.id,
            type="audio",
            title=None,
            occurred_at=datetime.now(UTC),
            recording_id=recording.id,
            client_event_id="client-1",
        )
        session.add(event)
        await session.commit()
        event_id, recording_id = event.id, recording.id

    async with session_factory() as session:
        fetched = await session.get(Event, event_id)
        assert fetched is not None
        assert fetched.type == "audio"
        assert fetched.title is None
        assert fetched.recording_id == recording_id
        assert fetched.client_event_id == "client-1"

        rec = await session.get(Recording, recording_id)
        assert rec is not None
        assert rec.state == "pending"  # default
        assert rec.content_type == "audio/mp4"
        assert rec.byte_size is None
        assert rec.error is None


async def test_event_defaults_to_text_type_with_no_recording(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await _seed_user(session)
        event = Event(user_id=user.id, title="A typed note", occurred_at=datetime.now(UTC))
        session.add(event)
        await session.commit()
        event_id = event.id

    async with session_factory() as session:
        fetched = await session.get(Event, event_id)
        assert fetched is not None
        assert fetched.type == "text"  # default
        assert fetched.recording_id is None
        assert fetched.client_event_id is None
