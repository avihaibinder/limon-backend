from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import SessionDep
from app.models.event import Event
from app.schemas.event import EventCreate, EventList, EventRead, EventUpdate
from app.services import events as events_service

router = APIRouter(prefix="/events", tags=["events"])


async def _get_event_or_404(session: SessionDep, event_id: str) -> Event:
    event = await events_service.get_event(session, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id!r} not found",
        )
    return event


@router.post("", response_model=EventRead, status_code=status.HTTP_201_CREATED)
async def create_event(session: SessionDep, payload: EventCreate) -> Event:
    """Create a new event."""
    return await events_service.create_event(session, payload)


@router.get("", response_model=EventList)
async def list_events(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tag: str | None = Query(default=None, description="Only return events carrying this tag."),
) -> EventList:
    """List events, newest first, with pagination and optional tag filtering."""
    items, total = await events_service.list_events(session, limit=limit, offset=offset, tag=tag)
    return EventList(
        items=[EventRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{event_id}", response_model=EventRead)
async def get_event(session: SessionDep, event_id: str) -> Event:
    """Fetch a single event by id."""
    return await _get_event_or_404(session, event_id)


@router.patch("/{event_id}", response_model=EventRead)
async def update_event(session: SessionDep, event_id: str, payload: EventUpdate) -> Event:
    """Partially update an event; only the provided fields change."""
    event = await _get_event_or_404(session, event_id)
    return await events_service.update_event(session, event, payload)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(session: SessionDep, event_id: str) -> None:
    """Delete an event."""
    event = await _get_event_or_404(session, event_id)
    await events_service.delete_event(session, event)
