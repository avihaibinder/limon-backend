"""Service layer for tags."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import Tag
from app.schemas.tag import TagUpdate


async def create_tag(session: AsyncSession, *, user_id: str, name: str) -> Tag:
    tag = Tag(user_id=user_id, name=name)
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    return tag


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
    await session.delete(tag)
    await session.commit()
