from pydantic import BaseModel, ConfigDict, Field


class TagTask(BaseModel):
    """Body of the Cloud Tasks call to POST /internal/tag."""

    model_config = ConfigDict(populate_by_name=True)

    event_id: str = Field(alias="eventId")
