from fastapi import APIRouter, HTTPException, status

from app.core.auth import CurrentUserDep
from app.dependencies import SessionDep
from app.models.user import User
from app.schemas.user import UserRead, UserUpdate
from app.services import demo_seed as demo_seed_service
from app.services import events as events_service
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


@router.post("/me/demo-data", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_my_demo_data(session: SessionDep, current_user: CurrentUserDep) -> User:
    """Backfill the authenticated (empty) account with demo history: 6 tags and
    10 text events whose timestamps are shifted so the newest lands at "now"
    (see ``app.services.demo_seed``). One-shot per account: the success stamps
    ``demo_seeded_at`` and any later call 409s, as does an account that already
    has events. Returns the updated profile."""
    if current_user.demo_seeded_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Demo data was already created for this account",
        )
    if await events_service.has_events(session, user_id=current_user.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Account already has events; demo data can only be created for an empty account",
        )
    return await demo_seed_service.seed_demo_data(session, user=current_user)


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
