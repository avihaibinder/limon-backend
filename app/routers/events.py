from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import CurrentUserDep, get_current_user
from app.dependencies import SessionDep
from app.models.event import Event
from app.schemas.event import (
    EventCreate,
    EventCreateResponse,
    EventList,
    EventRead,
    EventUpdate,
)
from app.services import events as events_service
from app.services.storage import StorageNotConfiguredError

# Authentication is a router-level gate; create stamps the owner from the token.
# Every query is scoped to the caller (domain 07): a row that is not theirs 404s
# rather than 403s, so the API never reveals another user's event exists.
router = APIRouter(prefix="/events", tags=["events"], dependencies=[Depends(get_current_user)])


async def _get_event_or_404(session: SessionDep, event_id: str, user_id: str) -> Event:
    event = await events_service.get_event(session, event_id, user_id=user_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Event {event_id!r} not found",
        )
    return event


@router.post("", response_model=EventCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_event(
    session: SessionDep, current_user: CurrentUserDep, payload: EventCreate
) -> EventCreateResponse:
    """Create an event owned by the authenticated user.

    Audio events also get a pending recording row and a direct-to-GCS
    ``signedUrl``; text events get neither. ``clientEventId`` makes this
    idempotent (a retry returns the same event with a fresh ``signedUrl``).
    """
    try:
        event, signed_url = await events_service.create_event(
            session, payload, user_id=current_user.id
        )
    except StorageNotConfiguredError as exc:
        # Audio upload has no bucket to sign against; the caller did nothing wrong.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Audio uploads are not configured (set LIMON_GCS_BUCKET).",
        ) from exc
    return EventCreateResponse(
        event=EventRead.model_validate(event),
        record_id=event.recording_id,
        signed_url=signed_url,
    )


@router.get("", response_model=EventList)
async def list_events(
    session: SessionDep,
    current_user: CurrentUserDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    tag: str | None = Query(default=None, description="Only return events carrying this tag."),
) -> EventList:
    """List the caller's events, newest first, with pagination and optional tag filtering."""
    items, total = await events_service.list_events(
        session, user_id=current_user.id, limit=limit, offset=offset, tag=tag
    )
    return EventList(
        items=[EventRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{event_id}", response_model=EventRead)
async def get_event(session: SessionDep, current_user: CurrentUserDep, event_id: str) -> Event:
    """Fetch one of the caller's events by id."""
    return await _get_event_or_404(session, event_id, current_user.id)


@router.patch("/{event_id}", response_model=EventRead)
async def update_event(
    session: SessionDep, current_user: CurrentUserDep, event_id: str, payload: EventUpdate
) -> Event:
    """Partially update one of the caller's events; only the provided fields change."""
    event = await _get_event_or_404(session, event_id, current_user.id)
    return await events_service.update_event(session, event, payload)


@router.delete("/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(session: SessionDep, current_user: CurrentUserDep, event_id: str) -> None:
    """Delete one of the caller's events."""
    event = await _get_event_or_404(session, event_id, current_user.id)
    await events_service.delete_event(session, event)
