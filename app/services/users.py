"""Service layer for users."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserUpdate


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_provider_subject(
    session: AsyncSession, provider: str, provider_subject: str
) -> User | None:
    """Look up a user by OAuth identity."""
    return await session.scalar(
        select(User).where(
            User.provider == provider,
            User.provider_subject == provider_subject,
        )
    )


async def get_or_create_user(
    session: AsyncSession,
    *,
    provider: str,
    provider_subject: str,
    email: str | None = None,
    display_name: str | None = None,
) -> User:
    """JIT provisioning: return the user for this OAuth identity, creating it
    on first sight. Profile fields are only seeded at creation — later token
    metadata never overwrites edits the user made through PATCH /users/me.
    """
    user = await get_user_by_provider_subject(session, provider, provider_subject)
    if user is not None:
        return user
    user = User(
        provider=provider,
        provider_subject=provider_subject,
        email=email,
        display_name=display_name,
    )
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


async def delete_user(session: AsyncSession, user: User) -> None:
    await session.delete(user)
    await session.commit()
