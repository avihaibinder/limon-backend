"""Service layer for tags."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.tag import Tag
from app.schemas.tag import TagUpdate


async def upsert_tag_by_name(
    session: AsyncSession, *, user_id: str, name: str, color: str | None
) -> tuple[Tag, bool]:
    """Create a tag, or return the user's existing tag of the same name.

    Returns ``(tag, created)``. On the existing path the stored ``color`` is
    never overwritten (contract: create is the FE's retry-safe dedupe, not an
    edit). A lost race against a concurrent create of the same name resolves to
    the winner's row instead of surfacing the unique-constraint error.
    """
    existing = await get_tag_by_name(session, user_id, name)
    if existing is not None:
        return existing, False

    tag = Tag(user_id=user_id, name=name, color=color)
    session.add(tag)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await get_tag_by_name(session, user_id, name)
        if existing is not None:
            return existing, False
        raise
    await session.refresh(tag)
    return tag, True


async def get_tag(session: AsyncSession, tag_id: str) -> Tag | None:
    return await session.get(Tag, tag_id)


async def get_tag_by_name(session: AsyncSession, user_id: str, name: str) -> Tag | None:
    return await session.scalar(select(Tag).where(Tag.user_id == user_id, Tag.name == name))


async def list_tags(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
    user_id: str,
) -> tuple[list[Tag], int]:
    """Return a page of one user's tags (alphabetical) and the total matching count."""
    query = select(Tag).where(Tag.user_id == user_id)

    total = await session.scalar(select(func.count()).select_from(query.subquery())) or 0
    result = await session.scalars(query.order_by(Tag.name).limit(limit).offset(offset))
    return list(result.all()), total


async def update_tag(session: AsyncSession, tag: Tag, payload: TagUpdate) -> Tag:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(tag, field, value)
    await session.commit()
    await session.refresh(tag)
    return tag


async def delete_tag(session: AsyncSession, tag: Tag) -> None:
    """Delete a tag and detach its id from all of the owner's events, atomically.

    ``events.tag_ids`` is a JSON array with no FK, so without the detach a
    deleted id would dangle in events forever (and sync to every future
    device). Reassigning the list marks the row dirty, bumping ``updated_at``
    and, in production, echoing each touched event over Realtime as a normal
    UPDATE.
    """
    events = await session.scalars(select(Event).where(Event.user_id == tag.user_id))
    for event in events:
        if tag.id in event.tag_ids:
            event.tag_ids = [tag_id for tag_id in event.tag_ids if tag_id != tag.id]
    await session.delete(tag)
    await session.commit()
