import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class Event(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    # Owner = the Supabase uid (see decision 15). FK to users.id; cascades on
    # account deletion so no event outlives its user.
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # text | audio. Only `audio` carries a recording and gets a transcript.
    type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    # Nullable: audio events have no title at upload; the user fills it in later.
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Body text: the user's note for text events, the transcript for audio events.
    description: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # JSON array of tags.id strings (decision 9), not opaque names. No DB-level
    # FK (a JSON array cannot carry one); readers tolerate ids of deleted tags.
    tag_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    # Link to the audio row (audio events only); one recording per event.
    recording_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("recordings.id"), nullable=True, unique=True
    )
    # Recording length in whole seconds (audio only); mirrors recordings.duration_sec.
    # Denormalized onto the event so the FE's direct-Supabase snapshot/Realtime read
    # (which sees only public.events columns) can surface it. Null for text events.
    duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Auto-tagging output (app/services/tagger.py), set once by POST /internal/tag
    # when the event had no user-selected tags. Not exposed to the client yet.
    suggested_location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    tag_reasoning: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Client-generated idempotency key for POST /events; UUID4 is globally unique.
    client_event_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
