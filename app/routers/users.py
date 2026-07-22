from fastapi import APIRouter, HTTPException, status

from app.core.auth import CurrentUserDep
from app.dependencies import SessionDep
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate
from app.services import users as users_service
from app.services.supabase_admin import SupabaseAdminError

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
    """Delete the authenticated user's account: removes the Supabase auth identity
    and our ``users`` row, which cascades away the user's events, recordings, and
    tags. If the Supabase side fails, nothing local is removed and the call 502s so
    the client can retry."""
    try:
        await users_service.delete_account(session, current_user)
    except SupabaseAdminError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not delete the account upstream; please retry.",
        ) from exc
