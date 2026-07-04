from fastapi import APIRouter, HTTPException, Query, status

from app.dependencies import SessionDep
from app.models.user import User
from app.schemas.user import UserCreate, UserList, UserRead, UserUpdate
from app.services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])


async def _get_user_or_404(session: SessionDep, user_id: str) -> User:
    user = await users_service.get_user(session, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id!r} not found",
        )
    return user


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(session: SessionDep, payload: UserCreate) -> User:
    """Create a user. Will be replaced by automatic provisioning from verified
    OAuth tokens once authentication is wired up.
    """
    existing = await users_service.get_user_by_provider_subject(
        session, payload.provider, payload.provider_subject
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this provider identity already exists",
        )
    return await users_service.create_user(session, payload)


@router.get("", response_model=UserList)
async def list_users(
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> UserList:
    """List users, newest first, with pagination."""
    items, total = await users_service.list_users(session, limit=limit, offset=offset)
    return UserList(
        items=[UserRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{user_id}", response_model=UserRead)
async def get_user(session: SessionDep, user_id: str) -> User:
    """Fetch a single user by id."""
    return await _get_user_or_404(session, user_id)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(session: SessionDep, user_id: str, payload: UserUpdate) -> User:
    """Partially update a user's profile; provider identity is immutable."""
    user = await _get_user_or_404(session, user_id)
    return await users_service.update_user(session, user, payload)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(session: SessionDep, user_id: str) -> None:
    """Delete a user."""
    user = await _get_user_or_404(session, user_id)
    await users_service.delete_user(session, user)
