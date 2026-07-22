"""Service layer for users."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserUpdate
from app.services import supabase_admin


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_or_create_user(
    session: AsyncSession,
    *,
    sub: str,
    provider: str = "supabase",
    email: str | None = None,
    display_name: str | None = None,
) -> User:
    """JIT provisioning: return the user for this Supabase identity, creating it
    on first sight. ``sub`` (the Supabase user id) *is* our primary key, so a
    lookup is a plain PK fetch and there is exactly one account per identity.
    Profile fields are only seeded at creation — later token metadata never
    overwrites edits the user made through PATCH /users/me.
    """
    user = await session.get(User, sub)
    if user is not None:
        return user
    user = User(id=sub, provider=provider, email=email, display_name=display_name)
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def update_user(session: AsyncSession, user: User, payload: UserUpdate) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_account(session: AsyncSession, user: User) -> None:
    """Delete the account across both stores. The Supabase ``auth.users`` identity
    goes first: if that call fails we raise before touching local data, so a failed
    delete leaves everything intact and retryable rather than half-removed. Deleting
    our ``users`` row then cascades away the user's events, recordings, and tags.
    """
    await supabase_admin.delete_auth_user(user.id)
    await session.delete(user)
    await session.commit()
