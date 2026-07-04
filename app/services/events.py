"""Service layer for events. Routers stay thin; persistence and business logic live here."""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.schemas.event import EventCreate, EventUpdate


async def create_event(session: AsyncSession, payload: EventCreate) -> Event:
    event = Event(**payload.model_dump())
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


async def get_event(session: AsyncSession, event_id: str) -> Event | None:
    return await session.get(Event, event_id)


async def list_events(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    tag: str | None = None,
) -> tuple[list[Event], int]:
    """Return a page of events (newest first) and the total matching count."""
    query = select(Event)
    if tag is not None:
        # Tags are stored as a JSON array; unpack it with SQLite's json_each.
        query = query.where(
            text(":tag IN (SELECT value FROM json_each(events.tags))").bindparams(tag=tag)
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
