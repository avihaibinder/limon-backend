import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # text | audio | lemon. Only `audio` carries a recording and gets a transcript.
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    # Nullable: audio events have no title at upload; the user fills it in later.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Body text: the user's note for text events, the transcript for audio events.
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Link to the audio row (audio events only); one recording per event.
    recording_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("recordings.id"), nullable=True, unique=True
    )
    # Client-generated idempotency key for POST /events. Globally unique on its
    # own so this stays independent of the (deferred) events.user_id column.
    client_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
