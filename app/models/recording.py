import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


class Recording(Base):
    """One recorded audio file plus its transcription state machine.

    Holds audio metadata and the internal ``state`` the worker uses for its
    idempotent claim (``pending -> transcribing -> done / failed``). The FE never
    reads this table; it watches the linked event's ``description`` over Realtime.
    Linked from ``events.recording_id``. The row ``id`` is the contract's
    ``recordId`` and the stem of the GCS object key.
    """

    __tablename__ = "recordings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # GCS object key, e.g. v0/{userId}/{recordId}.m4a (set at create time).
    storage_key: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False, default="audio/mp4")
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # pending | transcribing | done | failed. Indexed so the backlog re-enqueue
    # (up script) can find pending rows cheaply.
    state: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    # Short failure reason on `failed`; never the transcript or endpoint internals.
    error: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
