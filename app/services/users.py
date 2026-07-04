"""Service layer for users."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


async def create_user(session: AsyncSession, payload: UserCreate) -> User:
    user = User(**payload.model_dump())
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_provider_subject(
    session: AsyncSession, provider: str, provider_subject: str
) -> User | None:
    """Look up a user by OAuth identity — the basis for JIT provisioning later."""
    return await session.scalar(
        select(User).where(
            User.provider == provider,
            User.provider_subject == provider_subject,
        )
    )


async def list_users(
    session: AsyncSession,
    *,
    limit: int,
    offset: int,
) -> tuple[list[User], int]:
    """Return a page of users (newest first) and the total count."""
    total = await session.scalar(select(func.count()).select_from(User)) or 0
    result = await session.scalars(
        select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.all()), total


async def update_user(session: AsyncSession, user: User, payload: UserUpdate) -> User:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return user


async def delete_user(session: AsyncSession, user: User) -> None:
    await session.delete(user)
    await session.commit()
