from pydantic import BaseModel, ConfigDict, Field


class TranscribeTask(BaseModel):
    """Body of the Cloud Tasks call to POST /internal/transcribe."""

    model_config = ConfigDict(populate_by_name=True)

    record_id: str = Field(alias="recordId")
