"""Service layer for users."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.event import Event
from app.models.recording import Recording
from app.models.user import User
from app.schemas.user import UserUpdate
from app.services import storage, supabase_admin, task_queue


class AccountDeletionError(RuntimeError):
    """All controlled account data could not be removed."""


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
    """Delete all controlled data owned by the authenticated account.

    Queue and audio cleanup must succeed before the irreversible Supabase Auth
    deletion. The final users-row delete cascades events, recordings, and tags.
    Any failed stage returns no success, though an earlier external stage may
    already have removed queued work or audio and is therefore idempotent on retry.
    """
    # Lock the authenticated account row while collecting ownership. On
    # PostgreSQL this serializes FK inserts against the final user deletion.
    owned_user = await session.scalar(select(User).where(User.id == user.id).with_for_update())
    if owned_user is None:
        return

    event_ids = set(await session.scalars(select(Event.id).where(Event.user_id == owned_user.id)))
    recordings = list(
        await session.scalars(select(Recording).where(Recording.user_id == owned_user.id))
    )
    recording_ids = {recording.id for recording in recordings}
    storage_keys = {recording.storage_key for recording in recordings}

    try:
        await task_queue.cancel_account_tasks(event_ids=event_ids, recording_ids=recording_ids)
        await storage.delete_user_audio(owned_user.id, storage_keys)
    except (task_queue.TaskQueueError, storage.AudioCleanupError) as exc:
        await session.rollback()
        raise AccountDeletionError(str(exc)) from exc

    # Auth goes only after controlled external data is gone. A failure here
    # leaves the database account available for an authenticated retry.
    await supabase_admin.delete_auth_user(owned_user.id)
    await session.delete(owned_user)
    await session.commit()
