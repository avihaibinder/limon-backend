from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import SessionDep
from app.models.tag import Tag
from app.schemas.tag import TagCreate, TagList, TagRead, TagUpdate
from app.services import tags as tags_service
from app.services import users as users_service

router = APIRouter(prefix="/tags", tags=["tags"])


async def _get_tag_or_404(session: SessionDep, tag_id: str) -> Tag:
    tag = await tags_service.get_tag(session, tag_id)
    if tag is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tag {tag_id!r} not found",
        )
    return tag


async def _ensure_name_free(session: SessionDep, user_id: str, name: str) -> None:
    existing = await tags_service.get_tag_by_name(session, user_id, name)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tag {name!r} already exists for this user",
        )


@router.post("", response_model=TagRead, status_code=status.HTTP_201_CREATED)
async def create_tag(session: SessionDep, payload: TagCreate) -> Tag:
    """Create a tag for a user."""
    user = await users_service.get_user(session, payload.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {payload.user_id!r} not found",
        )
    await _ensure_name_free(session, payload.user_id, payload.name)
    return await tags_service.create_tag(session, payload)


@router.get("", response_model=TagList)
async def list_tags(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user_id: str | None = Query(default=None, description="Only return this user's tags."),
) -> TagList:
    """List tags alphabetically, with pagination and optional user filtering."""
    items, total = await tags_service.list_tags(
        session, limit=limit, offset=offset, user_id=user_id
    )
    return TagList(
        items=[TagRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{tag_id}", response_model=TagRead)
async def get_tag(session: SessionDep, tag_id: str) -> Tag:
    """Fetch a single tag by id."""
    return await _get_tag_or_404(session, tag_id)


@router.patch("/{tag_id}", response_model=TagRead)
async def update_tag(session: SessionDep, tag_id: str, payload: TagUpdate) -> Tag:
    """Rename a tag; it cannot move between users."""
    tag = await _get_tag_or_404(session, tag_id)
    if payload.name is not None and payload.name != tag.name:
        await _ensure_name_free(session, tag.user_id, payload.name)
    return await tags_service.update_tag(session, tag, payload)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(session: SessionDep, tag_id: str) -> None:
    """Delete a tag."""
    tag = await _get_tag_or_404(session, tag_id)
    await tags_service.delete_tag(session, tag)
