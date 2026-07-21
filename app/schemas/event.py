from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EventBase(BaseModel):
    """Fields shared by create and read representations."""

    title: str = Field(min_length=1, max_length=200, examples=["Feeling a bit overwhelmed today"])
    description: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime = Field(description="When the event happened.")
    tag_ids: list[str] = Field(
        default_factory=list,
        description="tags.id strings this event carries.",
        examples=[["3f2504e0-4f89-41d3-9a0c-0305e82c3301"]],
    )


class EventCreate(EventBase):
    """Payload for creating an event."""


class EventUpdate(BaseModel):
    """Partial update payload; only provided fields are changed."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None
    tag_ids: list[str] | None = None


class EventRead(EventBase):
    """Event as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime


class EventList(BaseModel):
    """Paginated collection of events."""

    items: list[EventRead]
    total: int
    limit: int
    offset: int
