from fastapi import APIRouter, status

from app.core.auth import CurrentUserDep
from app.dependencies import SessionDep
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate
from app.services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])

# Self-service only: identity comes from the verified Supabase token, so the
# API never takes a user id from the client. Accounts are created implicitly
# on the first authenticated request (see app.core.auth).


@router.get("/me", response_model=UserRead)
async def get_me(current_user: CurrentUserDep) -> User:
    """Fetch the authenticated user's profile."""
    return current_user


@router.patch("/me", response_model=UserRead)
async def update_me(session: SessionDep, current_user: CurrentUserDep, payload: UserUpdate) -> User:
    """Partially update the authenticated user's profile; provider identity is immutable."""
    return await users_service.update_user(session, current_user, payload)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(session: SessionDep, current_user: CurrentUserDep) -> None:
    """Delete the authenticated user's account (tags cascade with it)."""
    await users_service.delete_user(session, current_user)
