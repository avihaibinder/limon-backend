"""Pydantic models for the Pub/Sub push envelope delivered to POST /internal/uploaded.

A push subscription posts:

    {"message": {"data": "<base64>", "attributes": {...}, "messageId": "..."},
     "subscription": "projects/.../subscriptions/..."}

For a GCS object-finalize notification the object name arrives in
``message.attributes.objectId`` (and again inside the base64 ``data`` payload).
Extra fields are ignored so future Pub/Sub / GCS additions do not 422 us.
"""

from pydantic import BaseModel, ConfigDict, Field


class PubSubMessage(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    data: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    message_id: str | None = Field(default=None, alias="messageId")


class PubSubEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: PubSubMessage
