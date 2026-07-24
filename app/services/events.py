"""Service layer for events. Routers stay thin; persistence and business logic live here."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import step
from app.models.event import Event
from app.models.recording import Recording
from app.schemas.event import EventCreate, EventUpdate
from app.services import storage as storage_service

# Content type the audio upload URL is signed for (m4a); the client must PUT with
# a matching Content-Type or GCS rejects the signature.
_AUDIO_CONTENT_TYPE = "audio/mp4"


def _occurred_at(client_created_at_ms: int) -> datetime:
    """Map the client's epoch-ms capture time to a tz-aware UTC datetime."""
    return datetime.fromtimestamp(client_created_at_ms / 1000, tz=UTC)


async def create_event(
    session: AsyncSession, payload: EventCreate, *, user_id: str
) -> tuple[Event, str | None]:
    """Create an event owned by ``user_id``; return ``(event, signed_url)``.

    For ``type == "audio"`` this also creates the pending ``recordings`` row and
    mints a fresh direct-to-GCS upload URL (``signed_url``). Text events get no
    recording and no URL. ``client_event_id`` makes create idempotent: a repeat
    returns the existing event with a freshly minted ``signed_url``.
    """
    if payload.client_event_id is not None:
        existing = await session.scalar(
            select(Event).where(
                Event.user_id == user_id,
                Event.client_event_id == payload.client_event_id,
            )
        )
        if existing is not None:
            return existing, await _mint_signed_url(session, existing)

    occurred_at = _occurred_at(payload.client_created_at)

    if payload.type == "audio":
        record_id = str(uuid.uuid4())
        storage_key = storage_service.audio_object_key(user_id, record_id)
        # Sign before persisting: if signing is misconfigured / fails we raise
        # without committing, so there is no event left dangling without a URL.
        signed_url = storage_service.presign_put(storage_key, _AUDIO_CONTENT_TYPE)
        session.add(
            Recording(
                id=record_id,
                user_id=user_id,
                storage_key=storage_key,
                content_type=_AUDIO_CONTENT_TYPE,
                duration_sec=payload.duration_sec,
            )
        )
        # Flush so the recording row exists before the event's FK references it;
        # there is no ORM relationship to order the two inserts automatically.
        await session.flush()
        event = Event(
            user_id=user_id,
            type="audio",
            title=payload.title,
            description=payload.description,
            tag_ids=payload.tag_ids,
            occurred_at=occurred_at,
            client_event_id=payload.client_event_id,
            recording_id=record_id,
            # Mirrored onto the event so the FE's raw Supabase snapshot/Realtime read
            # surfaces it (it never reads the recordings table). Set once here; the
            # idempotent-retry path above returns early and never rewrites it.
            duration_sec=payload.duration_sec,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        # Stepping stone 1: the audio event + pending recording exist, URL signed.
        step("event_created", recordId=record_id, eventId=event.id, type="audio")
        return event, signed_url

    event = Event(
        user_id=user_id,
        type="text",
        title=payload.title,
        description=payload.description,
        tag_ids=payload.tag_ids,
        occurred_at=occurred_at,
        client_event_id=payload.client_event_id,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event, None


async def _mint_signed_url(session: AsyncSession, event: Event) -> str | None:
    """A fresh upload URL for an audio event's recording, else None (text event)."""
    if event.recording_id is None:
        return None
    recording = await session.get(Recording, event.recording_id)
    if recording is None:
        return None
    return storage_service.presign_put(recording.storage_key, _AUDIO_CONTENT_TYPE)


async def has_events(session: AsyncSession, *, user_id: str) -> bool:
    """True if the user owns at least one event (guards the demo-data backfill)."""
    first = await session.scalar(select(Event.id).where(Event.user_id == user_id).limit(1))
    return first is not None


async def get_event(session: AsyncSession, event_id: str, *, user_id: str) -> Event | None:
    """Fetch an event by id, scoped to its owner. A row that is not ``user_id``'s
    returns ``None`` (the caller turns that into a 404, never revealing existence)."""
    return await session.scalar(select(Event).where(Event.id == event_id, Event.user_id == user_id))


async def list_events(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int,
    offset: int,
    tag: str | None = None,
) -> tuple[list[Event], int]:
    """Return a page of the caller's events (newest first) and the total count."""
    query = select(Event).where(Event.user_id == user_id)
    if tag is not None:
        # tag_ids is a JSON array of tag id strings; unpack it with SQLite's json_each.
        query = query.where(
            text(":tag IN (SELECT value FROM json_each(events.tag_ids))").bindparams(tag=tag)
        )

    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await session.scalars(
        query.order_by(Event.occurred_at.desc()).limit(limit).offset(offset)
    )
    return list(result.all()), total


async def update_event(session: AsyncSession, event: Event, payload: EventUpdate) -> Event:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(event, field, value)
    await session.commit()
    await session.refresh(event)
    return event


async def delete_event(session: AsyncSession, event: Event) -> None:
    await session.delete(event)
    await session.commit()
