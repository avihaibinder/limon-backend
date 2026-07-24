from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# The wire contract is camelCase (CONTRACT.v2 "Fields, timestamps, and casing").
# populate_by_name lets the API also accept the snake_case field names, and lets
# EventRead read snake_case attributes straight off the ORM row.
_camel = ConfigDict(alias_generator=to_camel, populate_by_name=True)

EventType = Literal["text", "audio"]


class EventCreate(BaseModel):
    """Payload for ``POST /events``.

    ``type`` is ``text | audio`` (the former ``lemon`` is a ``text`` event with a
    null title/body). ``title`` and ``description`` are optional for every type;
    audio events leave ``description`` null and get the transcript later. Time in
    is ``clientCreatedAt`` (epoch-ms), stored as the event's ``occurred_at``.
    """

    model_config = _camel

    type: EventType = "text"
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    tag_ids: list[str] = Field(
        default_factory=list,
        description="tags.id strings this event carries.",
        examples=[["3f2504e0-4f89-41d3-9a0c-0305e82c3301"]],
    )
    client_created_at: int = Field(
        description="When the event happened, epoch milliseconds. Stored as occurred_at.",
        examples=[1737000000000],
    )
    client_event_id: str | None = Field(
        default=None,
        max_length=36,
        description="Client-generated idempotency key; a retry with the same value "
        "returns the same event (and a fresh signedUrl) instead of a duplicate.",
    )
    duration_sec: int | None = Field(
        default=None,
        ge=0,
        description="Recording length in whole seconds (audio only). Optional; absence "
        "or null means unknown length. A negative or non-integer value is rejected (422).",
        examples=[15],
    )


class EventUpdate(BaseModel):
    """Partial update payload; only provided fields are changed."""

    model_config = _camel

    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None
    tag_ids: list[str] | None = None


class EventRead(BaseModel):
    """The event object as the FE reads it (create response, GET, list)."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)

    id: str
    type: EventType
    title: str | None
    description: str | None
    tag_ids: list[str]
    # Serialized as recordId (not the generator's recordingId); read off the ORM
    # attribute recording_id. Present only for audio events.
    record_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("recording_id", "recordId"),
        serialization_alias="recordId",
    )
    # Recording length in whole seconds; serialized as durationSec (to_camel). Null
    # for text events and for audio without a stored length. Read off events.duration_sec.
    duration_sec: int | None = None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime


class EventCreateResponse(BaseModel):
    """``POST /events`` envelope. recordId/signedUrl are set for audio only."""

    model_config = _camel

    event: EventRead
    record_id: str | None = None
    signed_url: str | None = None


class EventList(BaseModel):
    """Paginated collection of events."""

    items: list[EventRead]
    total: int
    limit: int
    offset: int
